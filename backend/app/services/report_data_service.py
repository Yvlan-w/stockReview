"""持仓体检报告 · 公开 API 数据适配器（面向云端部署，不依赖任何私有连接器）。

数据来源（均为公开、免鉴权接口，本机与云端实测可达）
----------------------------------------------------
| 数据 | 来源 | 说明 |
|---|---|---|
| 实时行情、PE(TTM)、PB、流通市值 | 腾讯 `qt.gtimg.cn/q=` | 一次请求可批量取多只 |
| 日 K 线（默认 520 根，用于技术指标 / 分位 / 相关性） | 新浪 `CN_MarketData.getKLineData` | 单只一请求 |
| ROE / 毛利率 / 净利率 / 负债率 / 营收与净利同比 / 所属行业 | 东财 `push2 stock/get` F10 字段 | 单只一请求 |
| 主力资金净流入日序列（取近 5 / 20 日累计） | 东财 `push2his fflow/daykline` | 单只一请求 |

工程约定
--------
- **同步 httpx**：端点为同步 `def`，FastAPI 会在线程池执行，故此处用同步客户端 + 线程并发，
  不引入事件循环嵌套风险。
- **逐项优雅降级**：任一数据缺失只影响对应维度，缺失项由 `report_service.synth_raw()`
  的同代码确定性兜底值填补，并在 `sources` 中标记为 `synthetic`，报告头部会如实披露。
- **进程内 TTL 缓存**：默认 10 分钟，避免同一客户反复导出时重复打网。
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Sequence

import httpx

from .report_service import DataAdapter, synth_raw
from .stock_price_service import code_to_secid, industry_to_sector

logger = logging.getLogger(__name__)

EASTMONEY_UT = "fa5fd1943c7b386f172d6893dbfba10b"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

KLINE_LEN = 520          # 约两年交易日，够算 250 日分位与 60 日相关性
FLOW_SHORT, FLOW_LONG = 5, 20
HTTP_TIMEOUT = 8.0
MAX_WORKERS = 6
CACHE_TTL = 600          # 秒

_cache: Dict[str, tuple[Dict[str, Any], float]] = {}


# ---------------------------------------------------------------------------
# 代码转换
# ---------------------------------------------------------------------------

def _tencent_symbol(code: str) -> str:
    code = (code or "").strip()
    if not code:
        return ""
    return f"sh{code}" if code[0] in ("6", "9") else f"sz{code}"


def _f(v: Any, scale: float = 1.0) -> Optional[float]:
    """安全转 float；东财用 '-' 表示缺失。"""
    try:
        if v in (None, "", "-", "－"):
            return None
        return float(v) / scale
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# HTTP 基础层
# ---------------------------------------------------------------------------
# 注意：东财 push2 / push2his 对「同一 keep-alive 连接上的并发请求」非常敏感，
# 多线程复用同一个 httpx.Client 会稳定触发 "Server disconnected without sending
# a response"。因此这里每次请求都用短连接（Connection: close）并自带重试。

def _http_get(url: str, referer: str, timeout: float = HTTP_TIMEOUT,
              retries: int = 1, label: str = "") -> Optional[httpx.Response]:
    last: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout, connect=5.0),
                              follow_redirects=True) as c:
                r = c.get(url, headers={
                    "User-Agent": UA,
                    "Referer": referer,
                    "Accept": "*/*",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                    "Connection": "close",
                })
                r.raise_for_status()
                return r
        except Exception as e:
            last = e
            if attempt < retries:
                time.sleep(0.2 * (attempt + 1))
    logger.debug("%s 请求失败：%s", label or url[:60], last)
    return None


# ---- 域名健康度：某域名失败后进入冷却，避免每只票都白等一次超时 ----
_host_fail: Dict[str, float] = {}
HOST_COOLDOWN = 300.0


def _pick_hosts(hosts: Sequence[str]) -> List[str]:
    now = time.time()
    fresh = [h for h in hosts if now - _host_fail.get(h, 0.0) > HOST_COOLDOWN]
    return fresh or list(hosts)


def _json_get(hosts: Sequence[str], path: str, referer: str, label: str) -> Optional[dict]:
    """在候选域名间取第一个返回合法 JSON 的结果；失败域名记入冷却。"""
    for host in _pick_hosts(hosts):
        r = _http_get(host + path, referer, label=f"{label}@{host}")
        if r is None:
            _host_fail[host] = time.time()
            continue
        try:
            return r.json()
        except Exception:
            # 返回 HTML 门户页 / JSONP 等，视为该域名不可用
            _host_fail[host] = time.time()
    logger.warning("%s 全部域名不可用", label)
    return None


# ---------------------------------------------------------------------------
# 1) 腾讯行情（批量）
# ---------------------------------------------------------------------------

#: v_shXXXXXX="..." 按 ~ 分隔后的关键下标
_T_NAME, _T_PRICE, _T_PREV = 1, 3, 4
_T_CHG, _T_CHG_PCT = 31, 32
_T_TURNOVER_RATE, _T_PE = 38, 39
_T_FLOAT_MV, _T_TOTAL_MV, _T_PB = 44, 45, 46


def fetch_quotes(codes: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    """批量实时行情。返回 {code: {...}}，失败返回 {}。"""
    symbols = [_tencent_symbol(c) for c in codes if c]
    if not symbols:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    r = _http_get("https://qt.gtimg.cn/q=" + ",".join(symbols),
                  "https://finance.qq.com/", label="腾讯行情")
    if r is None:
        return {}
    text = r.content.decode("gbk", errors="ignore")

    for m in re.finditer(r'v_(?:sh|sz|bj)(\d{6})="([^"]*)"', text):
        code, payload = m.group(1), m.group(2)
        p = payload.split("~")
        if len(p) < 47:
            continue
        price = _f(p[_T_PRICE])
        if not price:
            continue
        out[code] = {
            "name": (p[_T_NAME] or "").strip(),
            "price": price,
            "prev_close": _f(p[_T_PREV]),
            "change_pct": _f(p[_T_CHG_PCT]),
            "turnover_rate": _f(p[_T_TURNOVER_RATE]),
            "pe": _f(p[_T_PE]),
            "pb": _f(p[_T_PB]),
            "float_mv": _f(p[_T_FLOAT_MV]),   # 亿元
            "total_mv": _f(p[_T_TOTAL_MV]),
        }
    if not out:
        logger.warning("腾讯行情返回无法解析（len=%d）", len(text))
    return out


# ---------------------------------------------------------------------------
# 2) 新浪日 K 线
# ---------------------------------------------------------------------------

def fetch_kline(code: str, datalen: int = KLINE_LEN) -> Optional[List[float]]:
    """日收盘价序列（由旧到新）。失败返回 None。"""
    url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           "CN_MarketData.getKLineData"
           f"?symbol={_tencent_symbol(code)}&scale=240&ma=no&datalen={datalen}")
    r = _http_get(url, "https://finance.sina.com.cn/", label=f"{code} 新浪日K")
    if r is None:
        return None
    try:
        rows = json.loads(r.text) if r.text.strip() else []
    except Exception as e:
        logger.warning("%s 新浪日K解析失败：%s", code, e)
        return None
    closes: List[float] = []
    for row in rows or []:
        v = _f(row.get("close"))
        if v and v > 0:
            closes.append(v)
    return closes if len(closes) >= 30 else None


# ---------------------------------------------------------------------------
# 3) 东财 F10 基本面
# ---------------------------------------------------------------------------

_F10_FIELDS = "f57,f58,f43,f100,f116,f117,f162,f167,f173,f183,f184,f105,f185,f186,f187,f188"


#: 东财 push2 主/备域名（主域名偶发被重置时自动切延时行情域名，二者字段一致）
#  注意：不要改用 ulist.np 批量接口——实测其返回的 fXXX 键与值会错位，只能用 stock/get 单只查。
_EM_QUOTE_HOSTS = ["https://push2.eastmoney.com", "https://push2delay.eastmoney.com"]


# ---------------------------------------------------------------------------
# 行业（用于组合行业集中度 / 板块归类）
# ---------------------------------------------------------------------------
# `stock/get` 的 f100（行业）当前已不再返回（恒为 None），故改用 emweb 公司概况的
# EM2016 一级行业（如 "食品饮料-饮料-白酒" → 取首段 "食品饮料"）映射到前端板块枚举。
_EMWEB_HOST = "https://emweb.securities.eastmoney.com"


def _emweb_code(code: str) -> str:
    """纯数字代码 → emweb 代码（SH600519 / SZ000001）。"""
    code = code.strip()
    prefix = "SH" if code[0] in ("6", "9") else "SZ"
    return f"{prefix}{code}"


#: 东财 EM2016 一级行业 → 前端板块枚举（与前端 SECTORS 对齐）
EM_INDUSTRY_SECTOR_MAP: Dict[str, str] = {
    "食品饮料": "消费", "农林牧渔": "消费", "家用电器": "消费", "商贸零售": "消费",
    "商业贸易": "消费", "休闲服务": "消费", "纺织服装": "消费", "轻工制造": "消费",
    "交通运输": "消费", "社会服务": "消费", "美容护理": "消费", "造纸": "消费",
    "医药生物": "医疗", "医药": "医疗",
    "汽车": "新能源", "电气设备": "新能源", "电力设备": "新能源",
    "交运设备": "新能源", "机械设备": "新能源", "公用事业": "新能源",
    "摩托车": "新能源", "自行车": "新能源",
    "电子": "科技", "电子设备": "科技", "计算机": "科技", "通信": "科技",
    "传媒": "科技", "信息设备": "科技", "信息服务": "科技", "通信服务": "科技",
    "半导体": "半导体",
    "化工": "化工", "石油化工": "化工",
    "有色金属": "资源", "钢铁": "资源", "采掘": "资源", "煤炭": "资源",
    "煤炭开采": "资源", "石油": "资源", "稀土": "资源",
    "国防军工": "军工",
    "银行": "金融", "非银金融": "金融",
    "房地产": "地产", "建筑材料": "地产", "建筑装饰": "地产", "建筑": "地产",
    "环保": "光伏", "环保工程": "光伏",
    "综合": "其他",
}


def fetch_industry(code: str) -> Optional[str]:
    """东财 emweb 公司概况：取 EM2016 一级行业（如 '食品饮料'）。失败返回 None。"""
    sym = _emweb_code(code)
    url = f"{_EMWEB_HOST}/PC_HSF10/CompanySurvey/PageAjax?code={sym}"
    r = _http_get(url, "https://emweb.securities.eastmoney.com/", label=f"{code} 东财行业")
    if r is None:
        return None
    try:
        j = r.json()
    except Exception:
        return None
    rows = (j or {}).get("jbzl") or []
    if not rows:
        return None
    em2016 = (rows[0].get("EM2016") or "").strip()
    if em2016 and "-" in em2016:
        return em2016.split("-")[0].strip()
    csrc = (rows[0].get("INDUSTRYCSRC1") or "").strip()
    if csrc and "-" in csrc:
        return csrc.split("-")[0].strip()
    return em2016 or csrc or None


def fetch_fundamentals(code: str) -> Optional[Dict[str, Any]]:
    """ROE / 毛利率 / 净利率 / 负债率 / 营收与净利同比 / PE / PB / 行业。"""
    path = (f"/api/qt/stock/get?secid={code_to_secid(code)}"
            f"&fields={_F10_FIELDS}&ut={EASTMONEY_UT}")
    raw = _json_get(_EM_QUOTE_HOSTS, path, "https://quote.eastmoney.com/", f"{code} 东财F10")
    if raw is None:
        return None
    d = (raw or {}).get("data") or {}
    if not d:
        return None
    # f100（行业）当前恒为 None，改用 emweb 取 EM2016 一级行业；取不到则 industry=None
    industry = fetch_industry(code)
    # 行业 → 板块枚举；映射不到时显式返回 None（不强行兜底为 '其他'），
    # 这样下游 _one 会退回到确定性 demo 行业（已知代码仍正确），避免 HHI 被压成 10000。
    sector = None
    if industry:
        sector = EM_INDUSTRY_SECTOR_MAP.get(industry) or industry_to_sector(industry) or None
    return {
        "name": d.get("f58") or None,
        "industry": industry,
        "sector": sector,
        "pe": _f(d.get("f162"), 100.0),            # 市盈率(动)，原值×100
        "pb": _f(d.get("f167"), 100.0),            # 市净率，原值×100
        "roe": _f(d.get("f173")),
        "gross_margin": _f(d.get("f186")),
        "net_margin": _f(d.get("f187")),
        "debt_ratio": _f(d.get("f188")),
        "rev_yoy": _f(d.get("f184")),
        "profit_yoy": _f(d.get("f185")),
        "revenue": _f(d.get("f183")),
        "net_profit": _f(d.get("f105")),
        "float_mv": (_f(d.get("f117"), 1e8)),      # 元 → 亿元
        "total_mv": (_f(d.get("f116"), 1e8)),
    }


# ---------------------------------------------------------------------------
# 4) 东财主力资金流
# ---------------------------------------------------------------------------

#: 资金流数据源说明
#  东财 push2his `fflow/daykline` 已不可用（主域名重置、延时域名返回门户 HTML），
#  改用新浪「资金流向」日序列：字段 netamount = 当日主力净流入额（元），
#  r0_net = 超大单净额（元）。返回按日期倒序，故取前 N 条即为最近 N 日。
_SINA_FLOW = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
              "MoneyFlow.ssl_qsfx_zjlrqs?page=1&num={num}&sort=opendate&asc=0&daima={sym}")


def fetch_fund_flow(code: str) -> Optional[Dict[str, float]]:
    """主力资金近 5 / 20 日累计净流入（亿元）。"""
    url = _SINA_FLOW.format(num=FLOW_LONG + 5, sym=_tencent_symbol(code))
    r = _http_get(url, "https://finance.sina.com.cn/", label=f"{code} 新浪资金流")
    if r is None:
        return None
    try:
        rows = json.loads(r.text) if r.text.strip() else []
    except Exception:
        return None
    if not isinstance(rows, list) or not rows:
        return None

    main: List[float] = []      # 主力净流入（元），最近在前
    huge: List[float] = []      # 超大单净额（元）
    for row in rows:
        v = _f(row.get("netamount"))
        if v is not None:
            main.append(v)
            huge.append(_f(row.get("r0_net")) or 0.0)
    if not main:
        return None
    return {
        "d5": round(sum(main[:FLOW_SHORT]) / 1e8, 3),
        "d20": round(sum(main[:FLOW_LONG]) / 1e8, 3),
        "huge5": round(sum(huge[:FLOW_SHORT]) / 1e8, 3),
        "days": len(main),
    }


# ---------------------------------------------------------------------------
# PE / PB 历史序列重建（用于历史分位）
# ---------------------------------------------------------------------------

def build_valuation_history(closes: Sequence[float], pe_now: Optional[float],
                            pb_now: Optional[float], profit_yoy: Optional[float],
                            roe: Optional[float]):
    """由价格序列 + 当期 PE/PB 反推历史 PE/PB 序列。

    思路：PE_t = P_t / EPS_t。EPS 无逐日公开序列，故以「当期 EPS + 年化增速」外推——
    往前第 k 个交易日的 EPS 约为 ``EPS_now / (1+g) ** (k/244)``，于是

        PE_t = PE_now × (P_t / P_now) × (1+g) ** (k/244)

    PB 同理，账面价值年增速用 ``ROE × 留存比例(0.6)`` 近似。
    这样得到的分位既反映价格波动，也扣除了业绩增长带来的自然消化，避免高增长股被
    系统性判为「历史低估」。
    """
    n = len(closes)
    if n == 0:
        return [], []
    p_now = float(closes[-1]) or 1.0
    g_e = max(-0.5, min(1.5, (float(profit_yoy) / 100.0) if profit_yoy else 0.0))
    g_b = max(0.0, min(0.6, (float(roe) / 100.0 * 0.6) if roe else 0.05))

    pe_hist: List[float] = []
    pb_hist: List[float] = []
    for i, c in enumerate(closes):
        k = n - 1 - i                      # 距今交易日数
        yrs = k / 244.0
        ratio = float(c) / p_now
        if pe_now:
            pe_hist.append(float(pe_now) * ratio * math.pow(1.0 + g_e, yrs))
        if pb_now:
            pb_hist.append(float(pb_now) * ratio * math.pow(1.0 + g_b, yrs))
    return pe_hist, pb_hist


# ---------------------------------------------------------------------------
# 适配器
# ---------------------------------------------------------------------------

class PublicApiAdapter(DataAdapter):
    """公开 API 适配器：可直接在云端运行，无需任何私有连接器 / 凭据。"""

    name = "public"

    def __init__(self, timeout: float = HTTP_TIMEOUT, use_cache: bool = True,
                 kline_len: int = KLINE_LEN):
        self.timeout = timeout
        self.use_cache = use_cache
        self.kline_len = kline_len

    # ---- 单只组装 ----
    def _one(self, code: str, quote: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        kline = fetch_kline(code, self.kline_len)
        fund = fetch_fundamentals(code)
        flow = fetch_fund_flow(code)

        sources = {
            "quote": "tencent" if quote else "synthetic",
            "kline": "sina" if kline else "synthetic",
            "finance": "eastmoney" if fund else "synthetic",
            "fund": "eastmoney" if flow else "synthetic",
        }

        # 缺失项以同代码确定性合成值兜底，保证维度不缺
        fb = synth_raw(code, length=max(120, min(self.kline_len, 520)))

        quote = quote or {}
        fund = fund or {}
        closes = kline or fb["closes"]

        price = quote.get("price") or (closes[-1] if closes else fb["price"])
        pe = fund.get("pe") or quote.get("pe") or fb["pe"]
        pb = fund.get("pb") or quote.get("pb") or fb["pb"]
        profit_yoy = fund.get("profit_yoy")
        roe = fund.get("roe")

        pe_hist, pb_hist = build_valuation_history(closes, pe, pb, profit_yoy, roe)
        if not pe_hist:
            pe_hist = fb["pe_history"]
        if not pb_hist:
            pb_hist = fb["pb_history"]

        float_mv = fund.get("float_mv") or quote.get("float_mv") or fb["float_mv"]

        return {
            "code": code,
            "name": quote.get("name") or fund.get("name") or fb["name"],
            "sector": fund.get("sector") or fb["sector"],
            "industry": fund.get("industry"),
            "price": round(float(price), 2),
            "prev_close": quote.get("prev_close"),
            "change_pct": quote.get("change_pct") if quote.get("change_pct") is not None else 0.0,
            "float_mv": float_mv,
            "total_mv": fund.get("total_mv") or quote.get("total_mv"),
            "closes": closes,
            "pe": pe,
            "pb": pb,
            "pe_history": pe_hist,
            "pb_history": pb_hist,
            "roe": roe if roe is not None else fb["roe"],
            "gross_margin": fund.get("gross_margin") if fund.get("gross_margin") is not None else fb["gross_margin"],
            "net_margin": fund.get("net_margin") if fund.get("net_margin") is not None else fb["net_margin"],
            "debt_ratio": fund.get("debt_ratio") if fund.get("debt_ratio") is not None else fb["debt_ratio"],
            "rev_yoy": fund.get("rev_yoy") if fund.get("rev_yoy") is not None else fb["rev_yoy"],
            "profit_yoy": profit_yoy if profit_yoy is not None else fb["profit_yoy"],
            "fund_flow": {"d5": (flow or fb["fund_flow"])["d5"],
                          "d20": (flow or fb["fund_flow"])["d20"]},
            "sources": sources,
        }

    # ---- 批量入口 ----
    def fetch(self, codes: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        codes = [str(c).strip() for c in codes if str(c).strip()]
        if not codes:
            return {}

        result: Dict[str, Dict[str, Any]] = {}
        pending: List[str] = []
        now = time.time()
        for c in codes:
            hit = _cache.get(c) if self.use_cache else None
            if hit and now - hit[1] < CACHE_TTL:
                result[c] = hit[0]
            else:
                pending.append(c)
        if not pending:
            return result

        quotes = fetch_quotes(pending)
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(pending))) as pool:
            futures = {pool.submit(self._one, c, quotes.get(c)): c for c in pending}
            for fut, code in futures.items():
                try:
                    raw = fut.result()
                except Exception as e:  # 单只失败不拖垮整份报告
                    logger.warning("%s 公开数据组装失败，使用兜底：%s", code, e)
                    raw = synth_raw(code)
                result[code] = raw
                if self.use_cache:
                    _cache[code] = (raw, time.time())

        if len(_cache) > 500:
            _cache.clear()
        return result


def clear_cache() -> None:
    _cache.clear()


def resolve_adapter(kind: str = "public") -> DataAdapter:
    """按名称解析适配器：``public``（默认，公开 API）/ ``demo``（离线确定性）。"""
    from .report_service import DemoDataAdapter
    if (kind or "").lower() in ("demo", "offline", "mock"):
        return DemoDataAdapter()
    return PublicApiAdapter()
