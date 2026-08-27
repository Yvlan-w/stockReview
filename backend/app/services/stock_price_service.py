"""个股行情服务：批量拉取个股实时行情 → 解析 → 落库 → 缓存。

数据源：东方财富 ulist.np/get 接口（支持批量最多 50 只股票）。
市场判断：6xx/9xx 开头 → 上证（1），0xx/3xx 开头 → 深证（0）。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import (
    STOCK_FIELDS, STOCK_CACHE_TTL,
    EASTMONEY_UT, MARKET_HTTP_TIMEOUT,
)
from ..database import SessionLocal
from ..models import StockPrice, StockDailyPrice, Position

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 双 host 轮询（与指数行情共用域名）
# ---------------------------------------------------------------------------
STOCK_HOSTS = [
    "https://push2.eastmoney.com/api/qt/ulist.np/get",
    "https://push2delay.eastmoney.com/api/qt/ulist.np/get",
]

# ---------------------------------------------------------------------------
# 内存缓存（进程内 TTL 缓存，避免热点数据频繁查库）
# ---------------------------------------------------------------------------
_price_cache: dict[str, tuple[dict, float]] = {}  # code → (data, timestamp)


def _invalidate_cache(code: str | None = None) -> None:
    """清除缓存；code 为 None 时清全部。"""
    if code is None:
        _price_cache.clear()
    else:
        _price_cache.pop(code, None)


def _get_cached(code: str) -> Optional[dict]:
    """从内存缓存读取；过期则返回 None。"""
    entry = _price_cache.get(code)
    if entry is None:
        return None
    data, ts = entry
    if time.time() - ts > STOCK_CACHE_TTL:
        _price_cache.pop(code, None)
        return None
    return data


def _set_cache(code: str, data: dict) -> None:
    _price_cache[code] = (data, time.time())


# ---------------------------------------------------------------------------
# 代码格式转换
# ---------------------------------------------------------------------------

def code_to_secid(code: str) -> str:
    """纯数字代码 → 东财 secid（如 600519 → 1.600519, 000001 → 0.000001）。"""
    code = code.strip()
    if not code:
        return code
    # 6/9 开头为上证主板/科创板，0/3 开头为深证主板/创业板
    if code[0] in ('6', '9'):
        return f"1.{code}"
    elif code[0] in ('0', '3'):
        return f"0.{code}"
    else:
        # 其他情况默认深证
        return f"0.{code}"


def secid_to_code(secid: str) -> str:
    """东财 secid → 纯数字代码（去掉市场前缀）。"""
    if '.' in secid:
        return secid.split('.')[1]
    return secid


# ---------------------------------------------------------------------------
# HTTP 层
# ---------------------------------------------------------------------------

def _build_stock_url(host: str, secids: list[str]) -> str:
    return (
        f"{host}?fltt=2&invt=2&ut={EASTMONEY_UT}"
        f"&fields={STOCK_FIELDS}&secids={','.join(secids)}"
    )


async def _fetch_stock_batch(secids: list[str]) -> Optional[list[dict]]:
    """批量拉取个股行情（双 host 轮询 + 重试）。返回解析后的列表，失败返回 None。"""
    last_err = None
    max_retries = 2  # 每个 host 最多重试 2 次
    
    for host in STOCK_HOSTS:
        for attempt in range(max_retries + 1):
            try:
                url = _build_stock_url(host, secids)
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(MARKET_HTTP_TIMEOUT, connect=10),
                    follow_redirects=True
                ) as client:
                    resp = await client.get(url, headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                        ),
                        "Referer": "https://quote.eastmoney.com/",
                    })
                    resp.raise_for_status()
                    raw = resp.json()

                if raw.get("rc") != 0 or not raw.get("data"):
                    raise ValueError(f"东财个股返回异常 rc={raw.get('rc')}")

                items = raw.get("data", {}).get("diff", [])
                if not items:
                    raise ValueError("返回无数据")

                logger.info("个股行情 fetch 成功 (host=%s, attempt=%d): %d 只", host, attempt + 1, len(items))
                return items
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                last_err = e
                logger.warning("个股行情 host=%s 连接失败 (attempt=%d): %s", host, attempt + 1, e)
                if attempt < max_retries:
                    await asyncio.sleep(1)  # 连接错误稍等重试
                    continue
                break  # 连接错误不再尝试同 host
            except Exception as e:
                last_err = e
                logger.warning("个股行情 host=%s 失败 (attempt=%d): %s", host, attempt + 1, e)
                if attempt < max_retries:
                    continue
                break

    logger.error("个股行情全部 host 失败: %s", last_err)
    return None


# ---------------------------------------------------------------------------
# 解析层
# ---------------------------------------------------------------------------

def _parse_stock_item(item: dict) -> Optional[dict]:
    """解析单条个股行情。返回标准化字典，无效数据返回 None。"""
    try:
        code = str(item.get("f12", "") or "").strip()
        if not code:
            return None

        price = float(item.get("f2", 0) or 0)
        if price <= 0:
            return None

        return {
            "code": code,
            "name": item.get("f14", "") or "",
            "current_price": price,
            "prev_close": float(item.get("f18", 0) or 0),
            "change_pct": float(item.get("f3", 0) or 0),
            "change_amount": float(item.get("f4", 0) or 0),
            "volume": float(item.get("f5", 0) or 0),
            "turnover": float(item.get("f6", 0) or 0),
            "high": float(item.get("f15", 0) or 0),
            "low": float(item.get("f16", 0) or 0),
            "open_price": float(item.get("f17", 0) or 0),
        }
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# 持久化层
# ---------------------------------------------------------------------------

def _upsert_stock_prices(db: Session, items: list[dict]) -> None:
    """批量 upsert 个股实时行情。"""
    now = dt.datetime.utcnow()

    # 查询已存在的记录
    codes = [item["code"] for item in items]
    existing_map: dict[str, StockPrice] = {}
    if codes:
        existing_rows = db.execute(
            select(StockPrice).where(StockPrice.code.in_(codes))
        ).scalars().all()
        for r in existing_rows:
            existing_map[r.code] = r

    for item in items:
        existing = existing_map.get(item["code"])
        if existing:
            existing.name = item["name"]
            existing.current_price = item["current_price"]
            existing.prev_close = item["prev_close"]
            existing.change_pct = item["change_pct"]
            existing.change_amount = item["change_amount"]
            existing.volume = item["volume"]
            existing.turnover = item["turnover"]
            existing.high = item["high"]
            existing.low = item["low"]
            existing.open_price = item["open_price"]
            existing.updated_at = now
        else:
            db.add(StockPrice(
                code=item["code"],
                name=item["name"],
                current_price=item["current_price"],
                prev_close=item["prev_close"],
                change_pct=item["change_pct"],
                change_amount=item["change_amount"],
                volume=item["volume"],
                turnover=item["turnover"],
                high=item["high"],
                low=item["low"],
                open_price=item["open_price"],
                updated_at=now,
            ))

    db.commit()
    # 失效缓存
    for item in items:
        _price_cache.pop(item["code"], None)


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------

def get_stock_prices(db: Session, codes: list[str]) -> dict[str, Optional[dict]]:
    """批量查询个股行情（优先缓存 → 数据库）。未找到的返回 None。"""
    result: dict[str, Optional[dict]] = {}
    cache_miss_codes: list[str] = []

    # 第一遍：命中缓存的直接返回
    for code in codes:
        cached = _get_cached(code)
        if cached:
            result[code] = cached
        else:
            cache_miss_codes.append(code)

    # 第二遍：缓存未命中的查库
    if cache_miss_codes:
        rows = db.execute(
            select(StockPrice).where(StockPrice.code.in_(cache_miss_codes))
        ).scalars().all()
        found_codes = set()
        for row in rows:
            data = {
                "code": row.code,
                "name": row.name,
                "current_price": row.current_price,
                "prev_close": row.prev_close,
                "change_pct": row.change_pct,
                "change_amount": row.change_amount,
                "volume": row.volume,
                "turnover": row.turnover,
                "high": row.high,
                "low": row.low,
                "open_price": row.open_price,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            result[row.code] = data
            _set_cache(row.code, data)
            found_codes.add(row.code)

        # 未找到的标记为 None
        for code in cache_miss_codes:
            if code not in found_codes:
                result[code] = None

    return result


def get_all_holding_codes(db: Session) -> list[str]:
    """获取所有持仓中不重复的股票代码。"""
    rows = db.execute(
        select(Position.code).distinct()
    ).scalars().all()
    return list(rows)


async def refresh_stock_prices(db: Optional[Session] = None) -> bool:
    """刷新所有活跃持仓的个股行情。

    调用方可选传入 db session；不传则自行创建并在结束时关闭。
    """
    owns_db = db is None
    if owns_db:
        db = SessionLocal()

    try:
        # 1. 获取所有持仓股票代码
        codes = get_all_holding_codes(db)
        if not codes:
            logger.info("个股行情刷新：无持仓股票，跳过")
            return True

        # 2. 转换为 secid 并分批（每批最多 50 只）
        secids = [code_to_secid(c) for c in codes]
        batch_size = 50
        all_items: list[dict] = []

        for i in range(0, len(secids), batch_size):
            batch = secids[i:i + batch_size]
            try:
                raw_items = await _fetch_stock_batch(batch)
                if raw_items:
                    for raw in raw_items:
                        parsed = _parse_stock_item(raw)
                        if parsed:
                            all_items.append(parsed)
            except Exception as e:
                logger.warning("个股行情批次 %d 刷新失败: %s", i // batch_size, e)

        if not all_items:
            logger.warning("个股行情全部刷新失败，无有效数据")
            return False

        # 3. 落库
        _upsert_stock_prices(db, all_items)
        logger.info("个股行情刷新完成: %d 只股票", len(all_items))
        return True

    except Exception as e:
        logger.error("个股行情刷新异常: %s", e)
        return False
    finally:
        if owns_db:
            db.close()


# ---------------------------------------------------------------------------
# 个股日 K 线（历史数据，用于计算昨日收盘价）
# ---------------------------------------------------------------------------

# K线数据源（多级降级）
KLINE_HOSTS = [
    "https://push2his.eastmoney.com",
    "https://push2hisdelay.eastmoney.com",
]


def _code_to_tencent(code: str) -> str:
    """纯数字代码 → 腾讯格式 (600519 → sh600519)"""
    code = code.strip()
    if code[0] in ('6', '9'):
        return f"sh{code}"
    else:
        return f"sz{code}"


async def _fetch_from_eastmoney(code: str, limit: int) -> Optional[list[dict]]:
    """从东方财富拉取K线（双host轮询+重试）。"""
    secid = code_to_secid(code)
    path = (
        f"/api/qt/stock/kline/get"
        f"?secid={secid}&ut={EASTMONEY_UT}"
        f"&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
        f"&klt=101&fqt=0&end=20500101&lmt={limit}"
    )

    max_retries = 2

    for host in KLINE_HOSTS:
        for attempt in range(max_retries + 1):
            try:
                url = f"{host}{path}"
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(MARKET_HTTP_TIMEOUT, connect=10),
                    follow_redirects=True,
                ) as client:
                    resp = await client.get(url, headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                        ),
                        "Referer": "https://quote.eastmoney.com/",
                        "Accept": "*/*",
                        "Accept-Language": "zh-CN,zh;q=0.9",
                    })
                    resp.raise_for_status()
                    raw = resp.json()

                if raw.get("rc") != 0 or not raw.get("data"):
                    raise ValueError(f"rc={raw.get('rc')}")

                klines = raw.get("data", {}).get("klines", [])
                if not klines:
                    return None

                result = []
                for line in klines:
                    parts = line.split(",")
                    if len(parts) < 7:
                        continue
                    result.append({
                        "code": code,
                        "trade_date": parts[0],
                        "open": float(parts[1]),
                        "close": float(parts[2]),
                        "high": float(parts[3]),
                        "low": float(parts[4]),
                        "volume": float(parts[5]),
                        "turnover": float(parts[6]) if len(parts) > 6 else 0.0,
                    })
                logger.debug("%s: 东财获取 %d 条", code, len(result))
                return result if result else None

            except (httpx.ConnectError, httpx.ConnectTimeout):
                if attempt < max_retries:
                    await asyncio.sleep(1)
                    continue
                break
            except Exception:
                if attempt < max_retries:
                    continue
                break

    return None


async def _fetch_from_tencent(code: str, limit: int) -> Optional[list[dict]]:
    """从腾讯财经拉取K线（备选数据源）。"""
    symbol = _code_to_tencent(code)
    url = (
        f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={symbol},day,,,{limit},qfq"
    )

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(MARKET_HTTP_TIMEOUT, connect=10),
            follow_redirects=True,
        ) as client:
            resp = await client.get(url, headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": "https://finance.qq.com/",
            })
            resp.raise_for_status()
            raw = resp.json()

        # 腾讯返回格式: data[symbol]['day'] 或 data[symbol]['qfqday']
        symbol_data = raw.get("data", {}).get(symbol, {})
        klines = symbol_data.get("day") or symbol_data.get("qfqday") or []

        if not klines:
            return None

        result = []
        for line in klines:
            # 腾讯格式: [date, open, close, high, low, volume]
            # 注意顺序不同于东财
            if len(line) < 6:
                continue
            result.append({
                "code": code,
                "trade_date": line[0],
                "open": float(line[1]),
                "close": float(line[2]),
                "high": float(line[3]),
                "low": float(line[4]),
                "volume": float(line[5]) if len(line) > 5 else 0.0,
                "turnover": float(line[6]) if len(line) > 6 else 0.0,
            })
        logger.debug("%s: 腾讯获取 %d 条", code, len(result))
        return result if result else None

    except Exception as e:
        logger.debug("%s: 腾讯获取失败: %s", code, e)
        return None


async def _fetch_from_sina(code: str, limit: int) -> Optional[list[dict]]:
    """从新浪财经拉取K线（备选数据源）。"""
    symbol = _code_to_tencent(code)  # 新浪格式与腾讯相同
    url = (
        f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
        f"?symbol={symbol}&scale=240&ma=no&datalen={limit}"
    )

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(MARKET_HTTP_TIMEOUT, connect=10),
            follow_redirects=True,
        ) as client:
            resp = await client.get(url, headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": "https://finance.sina.com.cn/",
            })
            resp.raise_for_status()
            text = resp.text

        # 新浪返回JS数组格式
        import json
        klines = json.loads(text) if text else []

        if not klines:
            return None

        result = []
        for k in klines:
            result.append({
                "code": code,
                "trade_date": k.get("day", ""),
                "open": float(k.get("open", 0)),
                "close": float(k.get("close", 0)),
                "high": float(k.get("high", 0)),
                "low": float(k.get("low", 0)),
                "volume": float(k.get("volume", 0)),
                "turnover": float(k.get("turnover", 0)) if k.get("turnover") else 0.0,
            })
        logger.debug("%s: 新浪获取 %d 条", code, len(result))
        return result if result else None

    except Exception as e:
        logger.debug("%s: 新浪获取失败: %s", code, e)
        return None


async def fetch_stock_daily_kline(code: str, limit: int = 90) -> Optional[list[dict]]:
    """拉取单只股票的日 K 线（多级降级：东财 → 腾讯 → 新浪）。"""
    # 1. 东方财富（首选）
    result = await _fetch_from_eastmoney(code, limit)
    if result:
        return result

    # 2. 腾讯财经（备选）
    result = await _fetch_from_tencent(code, limit)
    if result:
        return result

    # 3. 新浪财经（备选）
    result = await _fetch_from_sina(code, limit)
    if result:
        return result

    logger.warning("%s: 所有K线数据源均失败", code)
    return None


def upsert_stock_daily_prices(db: Session, data: list[dict]) -> None:
    """批量 upsert 个股日 K 线。"""
    if not data:
        return

    # 查询已存在的 (code, trade_date)
    keys = [(d["code"], d["trade_date"]) for d in data]
    existing_codes: set[tuple[str, str]] = set()
    for code in set(k[0] for k in keys):
        dates = [k[1] for k in keys if k[0] == code]
        existing = db.execute(
            select(StockDailyPrice).where(
                StockDailyPrice.code == code,
                StockDailyPrice.trade_date.in_(dates),
            )
        ).scalars().all()
        for r in existing:
            existing_codes.add((r.code, r.trade_date))

    for row in data:
        if (row["code"], row["trade_date"]) in existing_codes:
            existing = db.execute(
                select(StockDailyPrice).where(
                    StockDailyPrice.code == row["code"],
                    StockDailyPrice.trade_date == row["trade_date"],
                )
            ).scalar_one_or_none()
            if existing:
                existing.open = row["open"]
                existing.close = row["close"]
                existing.high = row["high"]
                existing.low = row["low"]
                existing.volume = row["volume"]
                existing.turnover = row["turnover"]
        else:
            db.add(StockDailyPrice(
                code=row["code"],
                trade_date=row["trade_date"],
                open=row["open"],
                close=row["close"],
                high=row["high"],
                low=row["low"],
                volume=row["volume"],
                turnover=row["turnover"],
            ))

    db.commit()


def get_prev_close(db: Session, code: str) -> Optional[float]:
    """获取某只股票的最近收盘价（用于计算每日盈亏）。"""
    row = db.execute(
        select(StockDailyPrice)
        .where(StockDailyPrice.code == code)
        .order_by(StockDailyPrice.trade_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    return row.close if row else None


# ---------------------------------------------------------------------------
# 股票搜索（添加持仓自动填充：代码 / 名称 / 拼音 → 现价 + 行业）
# ---------------------------------------------------------------------------

# 东财搜索建议接口（公开 token，支持代码/名称/拼音前缀模糊搜索）
SUGGEST_HOST = "https://searchapi.eastmoney.com/api/suggest/get"
SUGGEST_TOKEN = "D43BF722C8E33BDC906FB84D85E326E8"

# 东财行业名 → 前端板块枚举（SECTORS）映射
# 覆盖东财 f100 常见一级行业；匹配失败兜底返回"其他"（前端板块必填）
INDUSTRY_SECTOR_MAP: dict[str, list[str]] = {
    "消费": [
        "酿酒", "食品", "饮料", "白酒", "乳品", "调味", "农牧", "农业", "养殖", "种植", "饲料",
        "家电", "商业", "零售", "百货", "超市", "旅游", "酒店", "餐饮", "服装", "纺织", "贸易",
        "商贸", "化妆", "休闲", "珠宝", "文具", "玩具", "物流", "包装", "印刷", "出版", "燃气灶具",
    ],
    "金融": ["证券", "银行", "保险", "多元金融", "信托", "期货", "创投", "金融"],
    "科技": [
        "软件", "互联网", "计算机", "通信", "电子信息", "消费电子", "光学", "游戏",
        "传媒", "文化", "教育", "安防", "仪表", "智能", "数据", "云计算", "无人机",
    ],
    "医疗": ["医疗", "医药", "生物", "中药", "疫苗", "器械", "医美", "制药", "临床"],
    "新能源": ["电池", "新能源", "汽车", "能源金属", "电机", "充电", "锂电", "整车", "零部件"],
    "光伏": ["光伏", "风电", "电网", "电源设备", "输配", "储能"],
    "资源": [
        "有色", "煤炭", "钢铁", "石油", "黄金", "采掘", "贵金属", "小金属", "能源",
        "油气", "矿山", "稀土", "盐湖", "非金属矿",
    ],
    "军工": ["航天", "军工", "国防", "船舶", "兵器", "航空装备"],
    "半导体": ["半导体", "电子化学品", "元件", "芯片", "集成电路", "封测", "光刻"],
    "地产": ["房地产", "地产", "建材", "装修", "工程建设", "水泥", "园林", "物业", "租售"],
    "化工": ["化工", "化学", "化纤", "塑料", "橡胶", "化肥", "纤维", "树脂", "纯碱", "氟"],
}

# 搜索结果内存缓存（keyword → (results, timestamp)），短 TTL 防止连续输入重复请求
_search_cache: dict[str, tuple[list[dict], float]] = {}
_SEARCH_CACHE_TTL = 60  # 秒


def industry_to_sector(industry: Optional[str]) -> str:
    """东财行业名（如"酿酒行业"）→ 前端板块枚举（如"消费"）。

    匹配失败兜底返回"其他"，保证自动填充时板块字段总有值。
    """
    if industry:
        for sector, keywords in INDUSTRY_SECTOR_MAP.items():
            if any(kw in industry for kw in keywords):
                return sector
    return "其他"


def _extract_code(raw_code: str) -> str:
    """从 suggest 返回的 Code（如 SH600519 / SZ000001）提取纯数字代码。"""
    import re
    m = re.search(r"(\d{5,8})$", (raw_code or "").strip())
    return m.group(1) if m else ""


def _parse_suggest(raw: dict) -> list[dict]:
    """解析东财 suggest 响应：{"QuotationCodeTable": {"Data": [{"Code", "Name"}, ...]}}。"""
    items = (raw.get("QuotationCodeTable") or {}).get("Data") or []
    result = []
    for it in items:
        code = _extract_code(it.get("Code") or "")
        name = (it.get("Name") or "").strip()
        if code and name:
            result.append({"code": code, "name": name})
    return result


async def _fetch_suggest(keyword: str, count: int = 8) -> list[dict]:
    """东财搜索建议：返回 [{code, name}] 候选列表，失败返回 []。"""
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(MARKET_HTTP_TIMEOUT, connect=10),
            follow_redirects=True,
        ) as client:
            resp = await client.get(SUGGEST_HOST, params={
                "input": keyword,
                "type": "14",  # 沪深A股
                "token": SUGGEST_TOKEN,
                "count": count,
            }, headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": "https://so.eastmoney.com/",
            })
            resp.raise_for_status()
            raw = resp.json()

        return _parse_suggest(raw)
    except Exception as e:
        logger.warning("股票搜索 suggest 失败 (%s): %s", keyword, e)
        return []


async def _fetch_search_quotes(secids: list[str]) -> dict[str, dict]:
    """批量获取候选股现价与所属行业（ulist 接口，双 host 轮询）。

    返回 {code: {"price": float, "change_pct": float, "industry": str|None}}。
    """
    if not secids:
        return {}

    fields = "f2,f3,f12,f14,f100"  # 现价/涨跌幅/代码/名称/所属行业
    for host in STOCK_HOSTS:
        try:
            url = (
                f"{host}?fltt=2&invt=2&ut={EASTMONEY_UT}"
                f"&fields={fields}&secids={','.join(secids)}"
            )
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(MARKET_HTTP_TIMEOUT, connect=10),
                follow_redirects=True,
            ) as client:
                resp = await client.get(url, headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Referer": "https://quote.eastmoney.com/",
                })
                resp.raise_for_status()
                raw = resp.json()

            if raw.get("rc") != 0:
                raise ValueError(f"rc={raw.get('rc')}")

            result: dict[str, dict] = {}
            for item in (raw.get("data") or {}).get("diff") or []:
                code = str(item.get("f12", "") or "").strip()
                if not code:
                    continue
                try:
                    price = float(item.get("f2", 0) or 0)
                except (ValueError, TypeError):
                    price = 0.0
                try:
                    change_pct = float(item.get("f3", 0) or 0)
                except (ValueError, TypeError):
                    change_pct = 0.0
                industry = item.get("f100")
                if not isinstance(industry, str) or not industry.strip():
                    industry = None
                result[code] = {
                    "price": price,
                    "change_pct": change_pct,
                    "industry": industry,
                }
            if result:
                return result
        except Exception as e:
            logger.warning("搜索行情获取失败 (%s): %s", host, e)

    return {}


def _search_local(db: Session, keyword: str, limit: int) -> list[dict]:
    """本地 stock_price 表搜索（降级数据源，仅覆盖已入库股票）。"""
    from sqlalchemy import or_
    try:
        rows = db.execute(
            select(StockPrice)
            .where(or_(
                StockPrice.code.like(f"{keyword}%"),
                StockPrice.name.like(f"%{keyword}%"),
            ))
            .order_by(StockPrice.code)
            .limit(limit)
        ).scalars().all()
    except Exception as e:
        logger.warning("本地股票搜索失败: %s", e)
        return []

    return [
        {
            "code": r.code,
            "name": r.name or r.code,
            "price": r.current_price,
            "change_pct": r.change_pct,
            "industry": None,
            "sector": "其他",  # 本地表无行业信息，兜底"其他"
        }
        for r in rows
    ]


async def search_stocks(db: Session, keyword: str, limit: int = 8) -> list[dict]:
    """按代码 / 名称 / 拼音搜索股票，用于添加持仓时自动填充。

    数据链路：东财 suggest（候选）→ ulist（现价 + 行业）→ 行业映射前端板块枚举；
    线上失败时降级本地 stock_price 表模糊匹配。

    Returns:
        [{code, name, price, change_pct, industry, sector}]，price/行业可能为 None
    """
    keyword = (keyword or "").strip()
    if not keyword:
        return []
    limit = max(1, min(int(limit), 10))

    # 命中缓存直接返回
    cached = _search_cache.get(keyword)
    if cached and time.time() - cached[1] < _SEARCH_CACHE_TTL:
        return cached[0][:limit]

    results: list[dict] = []

    # 1) 线上搜索：suggest 候选 + 批量行情（含行业）
    candidates = await _fetch_suggest(keyword, count=limit)
    if candidates:
        secids = [code_to_secid(c["code"]) for c in candidates]
        quotes = await _fetch_search_quotes(secids)
        for c in candidates:
            q = quotes.get(c["code"]) or {}
            results.append({
                "code": c["code"],
                "name": c["name"],
                "price": q.get("price") or None,
                "change_pct": q.get("change_pct"),
                "industry": q.get("industry"),
                "sector": industry_to_sector(q.get("industry")),
            })

    # 2) 降级：本地 stock_price 表模糊匹配
    if not results:
        results = _search_local(db, keyword, limit)

    # 写入缓存
    _search_cache[keyword] = (results, time.time())
    if len(_search_cache) > 200:  # 防止无限增长
        _search_cache.pop(next(iter(_search_cache)))

    return results[:limit]


# ---------------------------------------------------------------------------
# 批量日 K 线刷新（导入真实市场数据）
# ---------------------------------------------------------------------------

async def refresh_all_daily_klines(db: Optional[Session] = None, limit: int = 90) -> dict:
    """批量刷新所有持仓股票的日 K 线数据到 stock_daily_price 表。

    调用东方财富 push2his 接口，拉取每只股票最近 N 天的 K 线数据，
    增量更新到数据库中，确保数据的准确性、完整性和时效性。

    Args:
        db: 数据库会话（可选，不传则自行创建）
        limit: 拉取的历史天数，默认 90 天

    Returns:
        dict: 包含成功数量、失败数量、详细结果的字典
    """
    owns_db = db is None
    if owns_db:
        db = SessionLocal()

    try:
        # 1. 获取所有持仓股票代码
        codes = get_all_holding_codes(db)
        if not codes:
            logger.info("日K线刷新：无持仓股票，跳过")
            return {"success": 0, "failed": 0, "results": []}

        logger.info("开始刷新 %d 只股票的日K线数据（最近 %d 天）", len(codes), limit)

        results = []
        success_count = 0
        failed_count = 0

        # 2. 逐只拉取K线数据（含并发控制）
        sem = asyncio.Semaphore(5)  # 最多5个并发请求

        async def _fetch_and_upsert(code: str) -> dict:
            async with sem:
                try:
                    klines = await fetch_stock_daily_kline(code, limit=limit)
                    if klines:
                        upsert_stock_daily_prices(db, klines)
                        logger.info("✓ %s: 获取 %d 条K线数据", code, len(klines))
                        return {"code": code, "status": "success", "count": len(klines)}
                    else:
                        logger.warning("✗ %s: 无K线数据返回", code)
                        return {"code": code, "status": "empty", "count": 0}
                except Exception as e:
                    logger.warning("✗ %s: 拉取失败 - %s", code, e)
                    return {"code": code, "status": "failed", "error": str(e)}

        # 并发执行所有拉取任务
        tasks = [_fetch_and_upsert(code) for code in codes]
        task_results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in task_results:
            if isinstance(result, Exception):
                failed_count += 1
                results.append({"status": "exception", "error": str(result)})
            elif result.get("status") == "success":
                success_count += 1
                results.append(result)
            else:
                failed_count += 1
                results.append(result)

        logger.info("日K线刷新完成：成功 %d 只，失败 %d 只", success_count, failed_count)

        return {
            "success": success_count,
            "failed": failed_count,
            "total": len(codes),
            "results": results,
        }

    except Exception as e:
        logger.error("日K线刷新异常: %s", e)
        return {"success": 0, "failed": len(codes), "error": str(e), "results": []}
    finally:
        if owns_db:
            db.close()


async def import_stock_daily_data(code: str, trade_date: str, close: float,
                                  open_price: float = 0, high: float = 0,
                                  low: float = 0, volume: float = 0,
                                  turnover: float = 0) -> bool:
    """手动导入单条日线数据到 stock_daily_price 表。

    Args:
        code: 股票代码（如 600519）
        trade_date: 交易日期（如 2026-08-15）
        close: 收盘价
        open_price: 开盘价
        high: 最高价
        low: 最低价
        volume: 成交量
        turnover: 成交额

    Returns:
        bool: 是否成功
    """
    try:
        db = SessionLocal()
        data = [{
            "code": code,
            "trade_date": trade_date,
            "open": open_price,
            "close": close,
            "high": high or close,
            "low": low or close,
            "volume": volume,
            "turnover": turnover,
        }]
        upsert_stock_daily_prices(db, data)
        logger.info("已导入 %s @ %s: 收盘价 %.2f", code, trade_date, close)
        return True
    except Exception as e:
        logger.error("导入日线数据失败 %s: %s", code, e)
        return False
    finally:
        if 'db' in locals():
            db.close()
