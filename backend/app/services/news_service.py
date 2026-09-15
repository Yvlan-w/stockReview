"""持仓相关资讯采集与匹配服务（后端常驻采集层）。

两表模型：
  news_item   全局资讯主表（去重，一份正文）
  client_news 客户-资讯关联表（扇出，每客户一份匹配上下文）

数据流（run_once，一个调度周期）：
  fetch(东财/新浪 7×24) -> parse_stock_refs -> normalize_code
    -> upsert_news_items（去重，first_seen 仅首插）
    -> load_client_holdings + build_code_index
    -> match_and_persist（tier1 个股 / tier2 板块，IGNORE 重复，仅增量匹配新增）
    -> prune_ttl（24h）

代码归一化（normalize_code）：
  - 个股：剥离交易所前缀取末 6 位数字
    （东财 `1.600519` / 新浪 `sh603448` / 持仓 `SH600519` / `600519` 均 -> `600519`）
  - 板块/概念：保留 `BKxxxx`（不以 6 位数字处理）

Tier2 板块相关依赖 stock_boards 的 code→BK 映射。映射源（按优先级）：
  1) 方案二：持仓写入时 best-effort 调东财 datacenter 核心题材板块接口取该股票所属 BK 码；
  2) 方案一：run_once 内周期刷新「全部持仓并集」的板块映射（节流 6h，覆盖板块漂移）；
  3) 离线兜底：derive_boards_from_news 从新闻「个股+BK 共现」推断（零网络）。
push2 板块成分股接口(fetch_board_members)在本环境被墙，已降级为备用。
"""
import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Set, Tuple

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import config
from ..database import SessionLocal
from ..models import Client, ClientNews, NewsItem, Position, StockBoards, utcnow

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- 配置
SOURCES: List[str] = config.NEWS_SOURCES
TTL_HOURS: int = config.NEWS_TTL_HOURS
PAGE_SIZE: int = config.NEWS_PAGE_SIZE
HTTP_TIMEOUT: int = config.NEWS_HTTP_TIMEOUT
UA: str = config.NEWS_USER_AGENT

EASTMONEY_URL = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
SINA_URL = "https://zhibo.sina.com.cn/api/zhibo/feed"

# ---------------------------------------------------------------- 代码归一化
_BK_RE = re.compile(r"BK\d+", re.IGNORECASE)
_DIGITS_RE = re.compile(r"\d+")


def normalize_code(raw: str) -> Optional[str]:
    """把任意来源的股票/板块代码归一化为可比形式。

    - 板块码（BKxxxx）保留大写 BK 前缀；
    - 个股：剥离交易所前缀，取末 6 位数字（兼容 1.600519 / sh603448 / SH600519 / 600519）。
    - 无法解析返回 None。
    """
    if not raw:
        return None
    s = str(raw).strip().upper()
    m = _BK_RE.search(s)
    if m:
        return m.group(0).upper()
    digits = _DIGITS_RE.findall(s)
    if not digits:
        return None
    last = digits[-1]
    if len(last) >= 6:
        return last[-6:]
    # 港股/其它短码：原样返回（与 positions.code 约定在实测对齐）
    return last


# ---------------------------------------------------------------- 展示过滤（入库前 + 序列化双保险）
# 可像股票一样买卖 / 与市场直接相关的代码前缀白名单：A 股个股 / ETF / LOF / 可转债 /
# 北交所 / 港股(5 位 0 开头) / 主要指数。不在白名单内的非 BK 代码视为「仅申赎的基金代码」
# 或无法匹配的脏码，交由过滤 2 判断。
_TRADABLE_PREFIXES = frozenset({
    "60", "68",                       # 沪市主板 / 科创板
    "00", "01", "02", "03", "30",     # 深市主板 / 创业板
    "8", "43", "92",                  # 北交所 / 新三板
    "50", "51", "52", "55", "56", "58",  # 上交所 ETF / LOF
    "15", "16",                       # 深交所 ETF / LOF
    "11", "12",                       # 可转债 / 可交换债
    "39",                             # 深交所指数（399xxx）
    "93", "95", "96", "99",           # 中证 / 其它指数（931xxx 等）
})


def is_tradable_code(code: Optional[str]) -> bool:
    """判断归一化后的代码是否为可交易 / 市场相关的 6 位代码。

    覆盖：A 股个股 / ETF / LOF / 可转债 / 北交所 / 港股(5 位 0 开头) / 主要指数。
    BK 板块码、空值、以及不在白名单内的纯数字码（仅申赎基金或脏码）返回 False。
    """
    if not code:
        return False
    s = str(code)
    if s.startswith("BK"):
        return False
    # 港股代码：5 位且以 0 开头（如 09988、06110.HK）
    if len(s) == 5 and s.startswith("0"):
        return True
    for pfx in _TRADABLE_PREFIXES:
        if s.startswith(pfx):
            return True
    return False


def is_news_displayable(item) -> bool:
    """判断一条资讯是否值得在「实时资讯 / 持仓相关快讯」展示。

    丢弃两类（入库前与接口序列化时都会调用，双保险）：
      1. url 不完整（非 http(s) 或为空）且 无正文（summary 与 content 皆空）；
      2. 仅含基金代码、无交易代码、也无板块码（BK）—— 板块码资讯保留给 tier2。

    item 可为 dict（入库前原始项）或 NewsItem ORM（序列化前），统一按字段名读取。
    """
    if isinstance(item, dict):
        url = item.get("url") or ""
        summary = item.get("summary") or ""
        content = item.get("content") or ""
        codes = item.get("stock_codes") or []
    else:
        url = getattr(item, "url", "") or ""
        summary = getattr(item, "summary", "") or ""
        content = getattr(item, "content", "") or ""
        codes = getattr(item, "stock_codes", None) or []

    # 过滤 1：url 不完整 且 没有正文 —— 这种「只有标题」的资讯点击无意义，直接丢弃。
    url_ok = bool(url) and str(url).lower().startswith("http")
    has_body = bool(summary) or bool(content)
    if not url_ok and not has_body:
        return False

    # 过滤 2：只有基金代码、没有交易代码、也没有板块码 → 丢弃（板块码留 tier2）。
    bk = [c for c in codes if str(c).startswith("BK")]
    non_bk = [c for c in codes if not str(c).startswith("BK")]
    if non_bk:
        tradable = [c for c in non_bk if is_tradable_code(c)]
        # 非 BK 代码里一笔可交易的都没有 → 全是「仅申赎基金代码」，且无板块码 → 丢弃。
        if not tradable and not bk:
            return False
    # 有 BK 板块码 或 根本没带代码：保留（空代码不算「只有基金代码」）。
    return True


# ---------------------------------------------------------------- 文本/时间工具
_TAG_RE = re.compile(r"<[^>]+>")


def _strip(html: str) -> str:
    if not html:
        return ""
    return _TAG_RE.sub("", str(html)).strip()


def _parse_time(val) -> Optional[datetime]:
    """尽量把快讯时间解析为 UTC datetime；失败返回 None。"""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        try:
            secs = val / 1000.0 if val > 1e11 else float(val)
            return datetime.fromtimestamp(secs, tz=timezone.utc)
        except Exception:
            return None
    s = str(val).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _hash_id(*parts: str) -> str:
    h = hashlib.md5("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return h[:16]


# ---------------------------------------------------------------- 抓取：东财 7×24
async def fetch_eastmoney_7x24(client: httpx.AsyncClient) -> List[dict]:
    """东财 7×24 快讯。

    响应：data.fastNewsList[]，每条含 title/summary/showTime/ctime/code/stockList。
    stockList 为东财 secid 数组，如 ["1.600519","90.BK0815"]。
    """
    t = int(datetime.now().timestamp() * 1000)
    url = (
        f"{EASTMONEY_URL}?client=web&biz=web_724&fastColumn=102"
        f"&sortEnd=0&pageSize={PAGE_SIZE}&req_trace={t}&_={t - 2}"
    )
    try:
        r = await client.get(url)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning("东财 7x24 抓取失败: %s", e)
        return []

    items = (data or {}).get("data", {}).get("fastNewsList", []) or []
    out: List[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        title = _strip(it.get("title") or it.get("summary") or "")
        stock_list = it.get("stockList") or []
        codes = [normalize_code(c) for c in stock_list]
        codes = [c for c in codes if c]
        news_id = it.get("id") or it.get("code") or _hash_id(title, str(it.get("showTime")))
        if not news_id:
            continue
        out.append({
            "news_id": f"em_{news_id}",
            "source": "eastmoney_7x24",
            "title": title[:2000],
            "summary": _strip(it.get("summary") or ""),
            "url": (f"https://finance.eastmoney.com/a/{it['code']}.html"
                    if it.get("code") else "https://kuaixun.eastmoney.com/"),
            "content": "",
            "published_at": _parse_time(it.get("showTime") or it.get("ctime")),
            "stock_codes": codes,
            "raw": it,
        })
    return out


# ---------------------------------------------------------------- 抓取：新浪 7×24
async def fetch_sina_7x24(client: httpx.AsyncClient) -> List[dict]:
    """新浪 7×24 快讯。

    响应：data.result.data.feed.list[]，每条含 rich_text/create_time/docurl，
    且 ext（JSON 字符串或对象）内嵌 stocks:[{symbol:"sh603448", key:"..."}]。
    """
    url = (
        f"{SINA_URL}?page=1&page_size={PAGE_SIZE}&zhibo_id=152"
        f"&tag_id=0&dire=f&dpc=1&type=0"
    )
    try:
        r = await client.get(url)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning("新浪 7x24 抓取失败: %s", e)
        return []

    feed = ((((data or {}).get("result") or {}).get("data") or {}).get("feed") or {})
    items = feed.get("list", []) or []
    out: List[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        title = _strip(it.get("rich_text") or "")
        # ext 可能是 JSON 字符串或对象
        ext = it.get("ext")
        stocks: List[dict] = []
        if ext:
            if isinstance(ext, str):
                try:
                    ext = json.loads(ext)
                except Exception:
                    ext = None
            if isinstance(ext, dict):
                stocks = ext.get("stocks") or []
        codes = [normalize_code(s.get("symbol")) for s in stocks if isinstance(s, dict)]
        codes = [c for c in codes if c]
        news_id = it.get("id") or _hash_id(title, str(it.get("create_time")))
        if not news_id:
            continue
        out.append({
            "news_id": f"sina_{news_id}",
            "source": "sina_7x24",
            "title": title[:2000],
            "summary": "",
            "url": it.get("docurl") or "https://finance.sina.com.cn/7x24/",
            "content": "",
            "published_at": _parse_time(it.get("create_time")),
            "stock_codes": codes,
            "raw": it,
        })
    return out


# ---------------------------------------------------------------- 入库：news_item 去重
def upsert_news_items(items: List[dict], db: Session) -> Tuple[int, Set[str]]:
    """写入 news_item（按 news_id 去重）。

    返回 (本次新增条数, 新增的 news_id 集合)。仅新增条数写 first_seen，
    已存在的条目保持原 first_seen（TTL 基准稳定）。
    """
    if not items:
        return 0, set()
    all_ids = [it["news_id"] for it in items]
    existing = {e[0] for e in db.query(NewsItem.news_id)
                .filter(NewsItem.news_id.in_(all_ids)).all()}
    new_items = [it for it in items if it["news_id"] not in existing]
    if not new_items:
        return 0, set()

    now = utcnow()
    rows = [{
        "news_id": it["news_id"],
        "source": it["source"],
        "title": it["title"] or "",
        "summary": it.get("summary") or "",
        "url": it.get("url") or "",
        "content": it.get("content") or "",
        "published_at": it.get("published_at"),
        "first_seen": now,
        "stock_codes": it.get("stock_codes") or [],
        "raw": it.get("raw"),
    } for it in new_items]
    db.bulk_insert_mappings(NewsItem, rows)
    db.commit()
    return len(rows), {it["news_id"] for it in new_items}


# ---------------------------------------------------------------- 持仓与板块映射
def load_board_map(db: Session) -> Dict[str, Set[str]]:
    """读取 stock_boards（code -> 板块 BK 码集合）。空表返回 {}（Tier2 自动跳过）。"""
    rows = db.query(StockBoards).all()
    return {r.code: set(r.board_codes or []) for r in rows}


def load_client_holdings(db: Session) -> Dict[str, dict]:
    """返回 {client_id: {codes:Set, board_codes:Set, name:str}}。

    codes 来自 positions.code（归一化）；board_codes 来自 stock_boards 解析。
    """
    board_map = load_board_map(db)
    result: Dict[str, dict] = {}
    clients = db.query(Client).all()
    for c in clients:
        codes: Set[str] = set()
        for p in c.positions:
            nc = normalize_code(p.code)
            if nc:
                codes.add(nc)
        board_codes: Set[str] = set()
        for code in codes:
            board_codes |= board_map.get(code, set())
        result[c.id] = {"codes": codes, "board_codes": board_codes, "name": c.name}
    return result


# ---------------------------------------------------------------- 匹配与扇出
def match_and_persist(new_ids: Set[str], db: Session) -> Tuple[int, int]:
    """把新增资讯扇出到 client_news（增量匹配，IGNORE 重复行）。

    返回 (写入的关联行数, 命中客户数)。
    - Tier1：news 个股码 ∩ 客户持仓码；
    - Tier2：news 板块码(BK) ∩ 客户 board_codes（stock_boards 为空时自动跳过）。
    """
    if not new_ids:
        return 0, 0
    holdings = load_client_holdings(db)
    if not holdings:
        return 0, 0

    news_rows = db.query(NewsItem).filter(NewsItem.news_id.in_(new_ids)).all()
    client_rows: List[dict] = []
    seen_pairs: Set[Tuple[str, str]] = set()
    now = utcnow()

    for news in news_rows:
        codes = news.stock_codes or []
        indiv = {c for c in codes if not c.startswith("BK")}
        bk = {c for c in codes if c.startswith("BK")}
        for cid, h in holdings.items():
            pair = (cid, news.news_id)
            if pair in seen_pairs:
                continue
            hit_ind = indiv & h["codes"]
            hit_bk = bk & h["board_codes"]
            if hit_ind:
                client_rows.append(_cn_row(news, cid, 1, sorted(hit_ind), now))
                seen_pairs.add(pair)
            elif hit_bk:
                client_rows.append(_cn_row(news, cid, 2, sorted(hit_bk), now))
                seen_pairs.add(pair)

    if not client_rows:
        return 0, 0

    # 仅对本次新增的 news_id 做匹配；再用 UNIQUE(client_id, news_id) 去重，
    # 跳过本批已存在的 (client, news) 对，再用标准 ORM add 批量写入
    # （与项目其他表一致，避免 bulk_insert 的 tz-aware 回查比较问题）。
    existing = set(
        (c, n) for c, n in db.query(ClientNews.client_id, ClientNews.news_id)
        .filter(ClientNews.news_id.in_(new_ids)).all()
    )
    fresh = [r for r in client_rows if (r["client_id"], r["news_id"]) not in existing]
    if not fresh:
        return 0, 0
    db.add_all([ClientNews(**r) for r in fresh])
    db.commit()
    clients_hit = {r["client_id"] for r in fresh}
    return len(fresh), len(clients_hit)


def _cn_row(news: NewsItem, client_id: str, tier: int, matched: List[str], now) -> dict:
    return {
        "client_id": client_id,
        "news_id": news.news_id,
        "tier": tier,
        "matched_codes": matched,
        "first_seen": now,
        "is_read": False,
    }


# ---------------------------------------------------------------- 新增持仓后补匹配
def match_news_for_client(client_id: str, db: Session) -> int:
    """「新增/变更持仓」后，把当前已有的 news_item（TTL 内）与该客户持仓做匹配，
    补齐遗漏的 client_news 关联行（增量去重，幂等安全）。

    背景：run_once 的 match_and_persist 只把 *新入库* 的 news 扇出到客户。若客户持仓是在
    某条新闻入库之后才添加的，则这条新闻不会被被动匹配到（后续采集周期也只匹配 new_ids 新新闻）。
    此函数在「添加持仓」动作后主动回扫 TTL 内的存量新闻，使已入库新闻即时联动到该客户资讯栏，
    而非只能等下一条新闻入库时才匹配。

    返回新增的关联行数。无持仓 / 无存量新闻 / 无匹配时返回 0。
    """
    client = db.query(Client).filter(Client.id == client_id).first()
    if client is None:
        return 0
    codes: Set[str] = set()
    for p in client.positions:
        nc = normalize_code(p.code)
        if nc:
            codes.add(nc)
    if not codes:
        return 0

    board_map = load_board_map(db)
    board_codes: Set[str] = set()
    for code in codes:
        board_codes |= board_map.get(code, set())

    # 仅回扫 TTL 内、尚未被 prune 清理的存量新闻（与 prune_ttl 口径一致，避免匹配已过期待清的孤儿新闻）
    cutoff = datetime.utcnow() - timedelta(hours=TTL_HOURS)
    news_rows = db.query(NewsItem).filter(NewsItem.first_seen >= cutoff).all()
    if not news_rows:
        return 0

    now = utcnow()
    client_rows: List[dict] = []
    for news in news_rows:
        codes_n = news.stock_codes or []
        indiv = {c for c in codes_n if not c.startswith("BK")}
        bk = {c for c in codes_n if c.startswith("BK")}
        hit_ind = indiv & codes
        hit_bk = bk & board_codes
        if hit_ind:
            client_rows.append(_cn_row(news, client_id, 1, sorted(hit_ind), now))
        elif hit_bk:
            client_rows.append(_cn_row(news, client_id, 2, sorted(hit_bk), now))

    if not client_rows:
        return 0

    existing = set(
        (c, n) for c, n in db.query(ClientNews.client_id, ClientNews.news_id)
        .filter(ClientNews.client_id == client_id).all()
    )
    fresh = [r for r in client_rows if (r["client_id"], r["news_id"]) not in existing]
    if not fresh:
        return 0
    db.add_all([ClientNews(**r) for r in fresh])
    db.commit()
    return len(fresh)


# ---------------------------------------------------------------- TTL 清理
def prune_ttl(db: Session, ttl_hours: int = TTL_HOURS) -> Tuple[int, int]:
    """清理过期数据。

    1. 删除 first_seen 超过 TTL 的 client_news；
    2. 删除 first_seen 超过 TTL 且已无 client_news 引用的孤儿 news_item。
    返回 (删除的 client_news 数, 删除的 news_item 数)。

    注意：SQLite 的 DateTime 列会把 tz-aware 值存为 naive UTC，故 cutoff 用 naive UTC
    比较（与存储口径一致），避免 "offset-naive vs offset-aware" 比较错误。
    """
    cutoff = datetime.utcnow() - timedelta(hours=ttl_hours)
    deleted_cn = db.query(ClientNews).filter(ClientNews.first_seen < cutoff).delete()
    db.commit()

    referenced = {r[0] for r in db.query(ClientNews.news_id).all()}
    deleted_ni = 0
    if referenced:
        deleted_ni = db.query(NewsItem).filter(
            NewsItem.first_seen < cutoff,
            NewsItem.news_id.notin_(referenced),
        ).delete()
    else:
        deleted_ni = db.query(NewsItem).filter(NewsItem.first_seen < cutoff).delete()
    db.commit()
    return deleted_cn, deleted_ni


# ---------------------------------------------------------------- 代码/板块拆分 & 板块映射源
def _split_codes(codes) -> Tuple[List[str], List[str]]:
    """把归一化代码数组拆成 (个股码, 板块BK码) 两组。"""
    stocks, bks = [], []
    for c in (codes or []):
        s = str(c)
        (bks if s.startswith("BK") else stocks).append(s)
    return stocks, bks


# ---------------------------------------------------------------- 板块映射源（方案一二共用，主源 datacenter）
# 主数据源：东财 datacenter 核心题材板块（股票 → 其所属 BK 板块码列表）。
# 实测沙箱可达（push2 被墙时仍可用）；取 NEW_BOARD_CODE(=BKxxxx)，忽略 DERIVE_BOARD_CODE(BIxxxx)。
_DATA_CENTER_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
_BOARD_CACHE: Dict[str, Tuple[List[str], float]] = {}   # code -> (bk_list, 时间戳)
_BOARD_CACHE_TTL = 24 * 3600   # 板块变化极慢，长缓存降低外部调用


def fetch_boards_for_stock(code: str, timeout: int = 8) -> List[str]:
    """股票代码(6位) → 其所属板块 BK 码列表。best-effort：失败/空返回 []。

    数据源：东财 datacenter RPT_F10_CORETHEME_BOARDTYPE（核心题材板块）。
    仅取 NEW_BOARD_CODE（BKxxxx），忽略 DERIVE_BOARD_CODE（BIxxxx，指数派生非新闻码）。
    进程内长缓存：板块变化极慢，同代码 24h 内直接返回，避免重复外网调用（也缓解方案二热路径开销）。
    """
    code = normalize_code(code) or ""
    if not code or code.startswith("BK"):
        return []
    cached = _BOARD_CACHE.get(code)
    if cached and (time.time() - cached[1]) < _BOARD_CACHE_TTL:
        return cached[0]
    bks: List[str] = []
    try:
        with httpx.Client(
            headers={"User-Agent": UA, "Referer": "https://emweb.eastmoney.com/"},
            timeout=timeout, follow_redirects=True,
        ) as c:
            r = c.get(_DATA_CENTER_URL, params={
                "reportName": "RPT_F10_CORETHEME_BOARDTYPE",
                "columns": "NEW_BOARD_CODE,BOARD_TYPE",
                "filter": f'(SECURITY_CODE="{code}")',
            })
            r.raise_for_status()
            for row in (r.json().get("result", {}).get("data") or []):
                bk = row.get("NEW_BOARD_CODE") or ""
                if isinstance(bk, str) and bk.startswith("BK"):
                    bks.append(bk)
    except Exception as e:  # noqa: BLE001
        logger.warning("板块码抓取失败(%s): %s", code, e)
        return cached[0] if cached else []
    # 去重保序
    seen: Set[str] = set()
    bks = [x for x in bks if not (x in seen or seen.add(x))]
    _BOARD_CACHE[code] = (bks, time.time())
    return bks


def all_held_codes(db: Session) -> Set[str]:
    """全部客户持仓的归一化代码并集（方案一二共用的范围界定，杜绝无关映射对）。"""
    rows = db.query(Position.code).distinct().all()
    out: Set[str] = set()
    for (raw,) in rows:
        nc = normalize_code(raw)
        if nc:
            out.add(nc)
    return out


def refresh_boards_for_codes(codes: Iterable[str], db: Session) -> int:
    """方案二核心：把一批股票代码的板块写入 stock_boards（upsert 并集）。

    在持仓已提交后 best-effort 调用；单笔失败不影响其余，整体异常由调用方 try/except 吞掉，
    绝不回滚持仓/调仓主流程。返回成功写入/更新的代码数。
    """
    refreshed = 0
    for code in codes:
        nc = normalize_code(code)
        if not nc or nc.startswith("BK"):
            continue
        try:
            bks = fetch_boards_for_stock(nc)
        except Exception as e:  # noqa: BLE001
            logger.warning("refresh_boards_for_codes 单笔失败(%s): %s", nc, e)
            continue
        if not bks:
            continue
        rec = db.get(StockBoards, nc)
        if rec is None:
            db.add(StockBoards(code=nc, board_codes=sorted(bks)))
        else:
            merged = sorted(set(rec.board_codes or []) | set(bks))
            rec.board_codes = merged
        refreshed += 1
    if refreshed:
        db.commit()
    return refreshed


def fetch_board_members(bk_code: str, client=None) -> List[str]:
    """抓取某板块(BKxxxx)的成分股（6 位归一化代码）。

    数据源：东财 push2 板块成分股接口（fs=b:{bk}）。best-effort：单板块失败返回 []，
    不抛异常，使采集链路对个别板块失败健壮。沙箱若屏蔽 push2 则返回空（由离线共现推导兜底）。
    """
    bk = str(bk_code).upper()
    if not bk.startswith("BK"):
        return []
    url = (
        "https://push2.eastmoney.com/api/qt/clist/get"
        f"?pn=1&pz=5000&po=0&np=1&fltt=2&invt=2&fid=f3&fs=b:{bk}&fields=f12"
    )
    own = client or httpx.Client(
        headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"},
        timeout=HTTP_TIMEOUT, follow_redirects=True)
    try:
        try:
            r = own.get(url)
            r.raise_for_status()
            d = r.json()
        finally:
            if client is None:
                own.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("板块成分股抓取失败(%s): %s", bk, e)
        return []
    data = (d.get("data") or {})
    items = data.get("diff") or data.get("list") or []
    if isinstance(items, dict):
        items = list(items.values())
    out = []
    for it in items:
        raw = it.get("f12") or it.get("SECURITY_CODE") or it.get("code")
        if not raw:
            continue
        nc = normalize_code(raw)
        if nc:
            out.append(nc)
    return out


def derive_boards_from_news(items: List[dict], db: Session) -> int:
    """从已入库新闻的「个股码 + 板块码」共现推导 stock→BK 映射（离线、零网络，与 push2 抓取互为补充）。

    一条新闻 stock_codes 同时含个股码与板块(BK)码时，即认为这些个股属于该板块。
    合并写入 stock_boards（按 code 去重、并集板块码）。返回新增/更新的映射条数。
    """
    link: Dict[str, Set[str]] = {}
    for it in items:
        stocks, bks = _split_codes(it.get("stock_codes"))
        for s in stocks:
            link.setdefault(s, set()).update(bks)
    if not link:
        return 0
    existing = {
        r.code: set(r.board_codes or [])
        for r in db.query(StockBoards).filter(StockBoards.code.in_(link)).all()
    }
    for code, bks in link.items():
        merged = (existing.get(code) or set()) | bks
        rec = db.get(StockBoards, code)
        if rec is None:
            db.add(StockBoards(code=code, board_codes=sorted(merged)))
        else:
            rec.board_codes = sorted(merged)
    db.commit()
    return len(link)


def refresh_stock_boards(db: Session, force: bool = False, throttle_hours: int = 6) -> int:
    """方案一：周期性（run_once 内）用 datacenter 刷新「全部持仓个股」的板块映射（启用/修复 Tier2）。

    - 范围限定为 all_held_codes（仅持仓并集），不拉无关股票，从根本上消灭「无用映射对」；
    - 节流：距上次刷新 < throttle_hours 且非 force 时跳过；
    - best-effort：单股票失败不影响其余，全失败返回 0；离线共现推导(derive_boards_from_news)作兜底。
    返回成功刷新的股票数。
    """
    if not force:
        newest = db.query(func.max(StockBoards.updated_at)).scalar()
        if newest is not None:
            # utcnow() 返回 offset-aware UTC，需统一两者时区再相减
            if newest.tzinfo is None:
                newest = newest.replace(tzinfo=timezone.utc)
            if (utcnow() - newest).total_seconds() < throttle_hours * 3600:
                return 0
    codes = all_held_codes(db)
    if not codes:
        return 0
    refreshed = 0
    for code in sorted(codes):
        try:
            bks = fetch_boards_for_stock(code)
        except Exception as e:  # noqa: BLE001
            logger.warning("refresh_stock_boards 单笔失败(%s): %s", code, e)
            continue
        if not bks:
            continue
        rec = db.get(StockBoards, code)
        if rec is None:
            db.add(StockBoards(code=code, board_codes=sorted(bks)))
        else:
            merged = sorted(set(rec.board_codes or []) | set(bks))
            rec.board_codes = merged
        refreshed += 1
    if refreshed:
        db.commit()
    return refreshed


def prune_stale_client_news(client_id: str, db: Session) -> int:
    """持仓变更/移除后，主动清理该客户下失效的 client_news 关联行（替代仅靠 TTL 自然清理）。

    失效判定：用当前持仓逐行重新评估匹配——
      - tier1：其命中个股码已不在当前持仓 → 失效；
      - tier2：其命中板块(BK)已不在「当前持仓所属板块集合」 → 失效；
      - 其它 tier：个股或板块任一仍命中则保留。
    返回删除的行数。
    """
    client = db.get(Client, client_id)
    if client is None:
        return 0
    codes = {normalize_code(p.code) for p in client.positions if normalize_code(p.code)}
    board_map = load_board_map(db)
    board_codes = set()
    for c in codes:
        board_codes |= board_map.get(c, set())
    rows = db.query(ClientNews).filter(ClientNews.client_id == client_id).all()
    if not rows:
        return 0
    news_ids = {r.news_id for r in rows}
    ni_map = {
        ni.news_id: ni for ni in
        db.query(NewsItem).filter(NewsItem.news_id.in_(news_ids)).all()
    }
    stale = []
    for cn in rows:
        ni = ni_map.get(cn.news_id)
        if ni is None:
            stale.append(cn)
            continue
        stocks, bks = _split_codes(ni.stock_codes)
        hit_ind = set(stocks) & codes
        hit_bk = set(bks) & board_codes
        if cn.tier == 1 and not hit_ind:
            stale.append(cn)
        elif cn.tier == 2 and not hit_bk:
            stale.append(cn)
        elif cn.tier not in (1, 2) and not (hit_ind or hit_bk):
            stale.append(cn)
    for cn in stale:
        db.delete(cn)
    if stale:
        db.commit()
    return len(stale)


# ---------------------------------------------------------------- 一个调度周期
async def run_once() -> dict:
    """执行一次完整采集周期：抓取 -> 入库 -> 匹配 -> 清理。

    任一数据源失败不阻断另一源（R4 多源降级）；整体异常被调用方（调度循环）捕获。
    """
    stats = {
        "fetched": 0, "inserted": 0, "new_ids": 0,
        "matched_rows": 0, "matched_clients": 0,
        "filtered_pre": 0, "boards_derived": 0, "boards_refreshed": 0,
        "pruned_cn": 0, "pruned_ni": 0, "errors": [],
    }
    items: List[dict] = []
    headers = {
        "User-Agent": UA,
        "Referer": "https://finance.eastmoney.com/",
        "Accept": "application/json, text/plain, */*",
    }
    async with httpx.AsyncClient(headers=headers, timeout=HTTP_TIMEOUT,
                                 follow_redirects=True) as client:
        tasks = []
        if "eastmoney" in SOURCES:
            tasks.append(fetch_eastmoney_7x24(client))
        if "sina" in SOURCES:
            tasks.append(fetch_sina_7x24(client))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                stats["errors"].append(str(r))
            else:
                items.extend(r)

    stats["fetched"] = len(items)
    # 入库前过滤：丢弃「url 不完整且无正文」或「仅基金代码无交易代码」的脏数据，
    # 避免其进入 news_item 并扇出到 client_news（接口序列化时还会再过滤一遍，双保险）。
    before = len(items)
    items = [it for it in items if is_news_displayable(it)]
    stats["filtered_pre"] = before - len(items)
    db = SessionLocal()
    try:
        inserted, new_ids = upsert_news_items(items, db)
        stats["inserted"] = inserted
        stats["new_ids"] = len(new_ids)
        # 离线共现推导 stock→BK（零网络，即时补充映射，启用 Tier2）
        stats["boards_derived"] = derive_boards_from_news(items, db)
        # 方案一：经 datacenter 刷新「全部持仓并集」的板块映射（受节流保护；失败由共现推导兜底）
        stats["boards_refreshed"] = refresh_stock_boards(db)
        rows, clients = match_and_persist(new_ids, db)
        stats["matched_rows"] = rows
        stats["matched_clients"] = clients
        pc, pni = prune_ttl(db)
        stats["pruned_cn"] = pc
        stats["pruned_ni"] = pni
    finally:
        db.close()
    return stats
