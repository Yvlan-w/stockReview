"""持仓体检报告 · 纯计算层（确定性，无网络依赖）。

职责边界
--------
本模块只做「事实 + 计算」，不产生任何自然语言判断，也不发起任何网络请求：

- 技术指标：RSI(6/12/24)、MACD(DIF/DEA/柱)、MA20/MA60、250 日价格分位、距高点回撤
- 估值分位：当前 PE / PB 在历史序列中的百分位（越低越便宜）
- 四维打分：估值 / 基本面 / 资金 / 技术，各 0~100
- 综合分：WEIGHTS 加权（估值 20% + 基本面 40% + 资金 20% + 技术 20%）
- 组合层：市值权重、行业占比、HHI 集中度、皮尔逊相关性矩阵、年化波动、最大回撤

数据来源通过 `DataAdapter` 注入：
- `DemoDataAdapter`：内置确定性合成数据（离线测试 / 演示用，同一代码任意进程结果一致）
- `PublicApiAdapter`：见 `report_data_service.py`（腾讯行情 + 新浪日K + 东财财务/资金流）

自然语言部分由 `llm_narrative.py` 负责；渲染由 `report_render.py` 负责。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import math
import random
from typing import Any, Dict, List, Optional, Sequence

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 四维权重（与报告模板一致，合计 1.0）
WEIGHTS: Dict[str, float] = {
    "估值": 0.20,
    "基本面": 0.40,
    "资金": 0.20,
    "技术": 0.20,
}

DIMENSIONS = ("估值", "基本面", "资金", "技术")

#: 年化换算用交易日数
TRADING_DAYS = 244

#: 相关性 / 波动计算所用回看窗口（交易日）
CORR_WINDOW = 60
RISK_WINDOW = 250

#: 演示持仓（离线测试与「示例报告」入口使用；生产走客户真实持仓，绝不写死）
DEMO_HOLDINGS: List[Dict[str, Any]] = [
    {"code": "600519", "name": "贵州茅台", "sector": "消费", "quantity": 200, "cost_price": 1520.0},
    {"code": "300750", "name": "宁德时代", "sector": "新能源", "quantity": 1000, "cost_price": 205.0},
    {"code": "002594", "name": "比亚迪", "sector": "新能源", "quantity": 1500, "cost_price": 248.0},
    {"code": "600276", "name": "恒瑞医药", "sector": "医疗", "quantity": 4000, "cost_price": 46.5},
    {"code": "002475", "name": "立讯精密", "sector": "科技", "quantity": 6000, "cost_price": 34.2},
]


# ---------------------------------------------------------------------------
# 通用数学工具（纯 Python，不依赖 numpy，便于云端瘦部署）
# ---------------------------------------------------------------------------

def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _lin(x: float, lo: float, hi: float) -> float:
    """把 x 从 [lo, hi] 线性映射到 [0, 100]，超界截断。"""
    if hi <= lo:
        return 50.0
    return _clamp((x - lo) / (hi - lo) * 100.0)


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs: Sequence[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def percentile_rank(series: Sequence[float], value: float) -> float:
    """value 在 series 中的百分位（0~100）。序列为空时返回 50。"""
    vals = [v for v in series if v is not None and v > 0]
    if not vals:
        return 50.0
    below = sum(1 for v in vals if v < value)
    equal = sum(1 for v in vals if abs(v - value) < 1e-12)
    return _clamp((below + 0.5 * equal) / len(vals) * 100.0)


def ema(series: Sequence[float], span: int) -> List[float]:
    if not series:
        return []
    k = 2.0 / (span + 1.0)
    out = [float(series[0])]
    for v in series[1:]:
        out.append(out[-1] + k * (float(v) - out[-1]))
    return out


def sma(series: Sequence[float], window: int) -> Optional[float]:
    if len(series) < window or window <= 0:
        return None
    return _mean(series[-window:])


def rsi(series: Sequence[float], period: int = 6) -> float:
    """Wilder RSI。数据不足时返回 50（中性）。"""
    if len(series) < period + 1:
        return 50.0
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        d = float(series[i]) - float(series[i - 1])
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_g, avg_l = gains / period, losses / period
    for i in range(period + 1, len(series)):
        d = float(series[i]) - float(series[i - 1])
        avg_g = (avg_g * (period - 1) + max(d, 0.0)) / period
        avg_l = (avg_l * (period - 1) + max(-d, 0.0)) / period
    if avg_l <= 1e-12:
        return 100.0 if avg_g > 0 else 50.0
    rs = avg_g / avg_l
    return _clamp(100.0 - 100.0 / (1.0 + rs))


def macd(series: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """返回 (dif, dea, hist, dir_text)。数据不足时给中性值。"""
    if len(series) < slow + signal:
        return 0.0, 0.0, 0.0, "震荡"
    ef, es = ema(series, fast), ema(series, slow)
    dif_series = [a - b for a, b in zip(ef, es)]
    dea_series = ema(dif_series, signal)
    dif, dea = dif_series[-1], dea_series[-1]
    prev_dif, prev_dea = dif_series[-2], dea_series[-2]
    hist = 2.0 * (dif - dea)
    if prev_dif <= prev_dea and dif > dea:
        text = "金叉"
    elif prev_dif >= prev_dea and dif < dea:
        text = "死叉"
    elif dif > dea:
        text = "多头延续"
    else:
        text = "空头延续"
    return dif, dea, hist, text


def daily_returns(closes: Sequence[float]) -> List[float]:
    out: List[float] = []
    for i in range(1, len(closes)):
        prev = float(closes[i - 1])
        if prev > 0:
            out.append(float(closes[i]) / prev - 1.0)
    return out


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    n = min(len(xs), len(ys))
    if n < 3:
        return 0.0
    xs, ys = list(xs[-n:]), list(ys[-n:])
    mx, my = _mean(xs), _mean(ys)
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    dx = math.sqrt(sum((a - mx) ** 2 for a in xs))
    dy = math.sqrt(sum((b - my) ** 2 for b in ys))
    if dx <= 1e-12 or dy <= 1e-12:
        return 0.0
    return max(-1.0, min(1.0, num / (dx * dy)))


def max_drawdown_pct(equity: Sequence[float]) -> float:
    """最大回撤（返回负数百分比，如 -20.1）。"""
    peak, worst = None, 0.0
    for v in equity:
        v = float(v)
        if peak is None or v > peak:
            peak = v
        if peak and peak > 0:
            dd = (v / peak - 1.0) * 100.0
            worst = min(worst, dd)
    return worst


# ---------------------------------------------------------------------------
# 数据适配器
# ---------------------------------------------------------------------------

class DataAdapter:
    """数据源接口。

    `fetch(codes)` 必须返回 `{code: raw}`，raw 字段约定（缺失项用 None，计算层会兜底）：

    ``name / sector / price / prev_close / change_pct / float_mv(亿元)``
    ``closes[float] / pe / pb / pe_history[] / pb_history[]``
    ``roe / gross_margin / net_margin / debt_ratio / rev_yoy / profit_yoy``
    ``fund_flow {d5, d20}（亿元，主力净流入）``
    ``sources {quote,kline,finance,fund}（各来源真实/合成标记）``
    """

    name = "base"

    def fetch(self, codes: Sequence[str]) -> Dict[str, Dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError


#: 演示/兜底用的行业与基本面画像（真实量级，便于示例报告可读）
_DEMO_PROFILE: Dict[str, Dict[str, Any]] = {
    "600519": dict(name="贵州茅台", sector="消费", price=1438.0, pe=21.4, pb=7.8, roe=31.6,
                   gross_margin=91.8, net_margin=52.3, debt_ratio=16.5, rev_yoy=9.8,
                   profit_yoy=11.2, float_mv=18000.0, d5=6.2, d20=-3.4, drift=0.0002, vol=0.014),
    "300750": dict(name="宁德时代", sector="新能源", price=253.6, pe=19.8, pb=3.6, roe=20.4,
                   gross_margin=26.7, net_margin=13.9, debt_ratio=66.2, rev_yoy=-5.4,
                   profit_yoy=12.6, float_mv=9500.0, d5=-8.5, d20=12.7, drift=0.0004, vol=0.023),
    "002594": dict(name="比亚迪", sector="新能源", price=286.4, pe=23.1, pb=4.4, roe=21.8,
                   gross_margin=20.9, net_margin=5.4, debt_ratio=74.6, rev_yoy=22.4,
                   profit_yoy=18.9, float_mv=6200.0, d5=-4.1, d20=-9.8, drift=0.0001, vol=0.021),
    "600276": dict(name="恒瑞医药", sector="医疗", price=52.8, pe=48.6, pb=6.1, roe=13.2,
                   gross_margin=85.4, net_margin=22.8, debt_ratio=12.8, rev_yoy=21.7,
                   profit_yoy=32.5, float_mv=3300.0, d5=3.8, d20=7.2, drift=0.0005, vol=0.019),
    "002475": dict(name="立讯精密", sector="科技", price=41.9, pe=22.6, pb=4.0, roe=18.6,
                   gross_margin=11.4, net_margin=4.9, debt_ratio=58.4, rev_yoy=15.9,
                   profit_yoy=20.1, float_mv=2900.0, d5=1.6, d20=-2.3, drift=0.0003, vol=0.020),
}


def _seed_of(code: str) -> int:
    """稳定种子：不能用内置 hash()（按进程加盐，跨进程不可复现）。"""
    return int(hashlib.md5(code.encode("utf-8")).hexdigest()[:8], 16)


def synth_raw(code: str, name: Optional[str] = None, sector: Optional[str] = None,
              price: Optional[float] = None, length: int = 520) -> Dict[str, Any]:
    """按代码确定性合成一套完整数据（未知票兜底 / 离线演示）。"""
    prof = dict(_DEMO_PROFILE.get(code, {}))
    rnd = random.Random(_seed_of(code))

    last_price = float(price or prof.get("price") or round(8 + rnd.random() * 70, 2))
    drift = float(prof.get("drift", (rnd.random() - 0.45) * 0.0008))
    vol = float(prof.get("vol", 0.014 + rnd.random() * 0.012))

    # 反向生成价格序列，使末值恰为 last_price
    closes = [last_price]
    for _ in range(length - 1):
        step = math.exp(-(drift + rnd.gauss(0, vol)))
        closes.append(max(0.5, closes[-1] * step))
    closes.reverse()

    pe = float(prof.get("pe", round(12 + rnd.random() * 40, 1)))
    pb = float(prof.get("pb", round(1.2 + rnd.random() * 6, 2)))
    # 用价格序列反推 PE/PB 历史（EPS/BPS 在窗口内视作缓慢变化）
    base = closes[-1] if closes[-1] > 0 else 1.0
    growth = [1.0 + 0.12 * (i / max(1, len(closes) - 1)) for i in range(len(closes))]
    pe_history = [pe * (c / base) / g for c, g in zip(closes, growth)]
    pb_history = [pb * (c / base) / g for c, g in zip(closes, growth)]

    return {
        "code": code,
        "name": name or prof.get("name") or f"股票{code}",
        "sector": sector or prof.get("sector") or "其他",
        "price": round(closes[-1], 2),
        "prev_close": round(closes[-2], 2) if len(closes) > 1 else round(closes[-1], 2),
        "change_pct": round((closes[-1] / closes[-2] - 1) * 100, 2) if len(closes) > 1 else 0.0,
        "float_mv": float(prof.get("float_mv", round(80 + rnd.random() * 900, 1))),
        "closes": [round(c, 3) for c in closes],
        "pe": pe,
        "pb": pb,
        "pe_history": pe_history,
        "pb_history": pb_history,
        "roe": float(prof.get("roe", round(3 + rnd.random() * 22, 1))),
        "gross_margin": float(prof.get("gross_margin", round(12 + rnd.random() * 55, 1))),
        "net_margin": float(prof.get("net_margin", round(1 + rnd.random() * 25, 1))),
        "debt_ratio": float(prof.get("debt_ratio", round(20 + rnd.random() * 55, 1))),
        "rev_yoy": float(prof.get("rev_yoy", round(-15 + rnd.random() * 45, 1))),
        "profit_yoy": float(prof.get("profit_yoy", round(-20 + rnd.random() * 60, 1))),
        "fund_flow": {
            "d5": float(prof.get("d5", round((rnd.random() - 0.5) * 12, 2))),
            "d20": float(prof.get("d20", round((rnd.random() - 0.5) * 26, 2))),
        },
        "sources": {"quote": "synthetic", "kline": "synthetic",
                    "finance": "synthetic", "fund": "synthetic"},
    }


class DemoDataAdapter(DataAdapter):
    """离线确定性适配器：同一股票代码在任何机器 / 任何进程结果完全一致。"""

    name = "demo"

    def fetch(self, codes: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        return {c: synth_raw(c) for c in codes}


# ---------------------------------------------------------------------------
# 四维打分
# ---------------------------------------------------------------------------

def _valuation_score(pe_pct: float, pb_pct: float) -> float:
    """估值分：历史分位越低（越便宜）得分越高。PE 权重 60%，PB 40%。"""
    return _clamp(100.0 - (0.6 * pe_pct + 0.4 * pb_pct))


def _fundamental_score(raw: Dict[str, Any]) -> float:
    roe = float(raw.get("roe") or 8.0)
    gm = float(raw.get("gross_margin") or 25.0)
    nm = float(raw.get("net_margin") or 8.0)
    dr = float(raw.get("debt_ratio") or 50.0)
    rev = float(raw.get("rev_yoy") or 0.0)
    pro = float(raw.get("profit_yoy") or 0.0)
    return _clamp(
        0.28 * _lin(roe, 0, 30)
        + 0.16 * _lin(gm, 10, 70)
        + 0.16 * _lin(nm, 0, 35)
        + 0.12 * (100.0 - _lin(dr, 20, 80))
        + 0.14 * _lin(rev, -20, 40)
        + 0.14 * _lin(pro, -30, 50)
    )


def _fund_score(raw: Dict[str, Any]) -> float:
    """资金分：主力净流入占流通市值比例，5 日权重 60% / 20 日 40%，50 为中性。"""
    flow = raw.get("fund_flow") or {}
    mv = max(float(raw.get("float_mv") or 200.0), 1.0)
    d5 = float(flow.get("d5") or 0.0)
    d20 = float(flow.get("d20") or 0.0)
    s5 = _clamp(50.0 + (d5 / mv * 100.0) * 90.0)
    s20 = _clamp(50.0 + (d20 / mv * 100.0) * 45.0)
    return _clamp(0.6 * s5 + 0.4 * s20)


def _technical_score(m: Dict[str, Any]) -> float:
    """技术分 = 趋势 35% + 动量 30% + 位置 35%。

    位置分以 250 日分位 40 为峰值：过高意味着追高风险，过低意味着趋势尚未修复。
    """
    dif, dea = m["macd_dif"], m["macd_dea"]
    ma20, ma60, price = m.get("ma20"), m.get("ma60"), m["price"]
    s_trend = 50.0
    if dif > dea:
        s_trend += 22.0
    else:
        s_trend -= 18.0
    if ma20 and ma60:
        if ma20 > ma60:
            s_trend += 14.0
        else:
            s_trend -= 12.0
        if price > ma20:
            s_trend += 8.0
        else:
            s_trend -= 8.0
    s_trend = _clamp(s_trend)
    s_rsi = _clamp(100.0 - abs(float(m["rsi6"]) - 55.0) * 1.8)
    s_pos = _clamp(100.0 - abs(float(m["pos250"]) - 40.0) * 1.15)
    return _clamp(0.35 * s_trend + 0.30 * s_rsi + 0.35 * s_pos)


# ---------------------------------------------------------------------------
# 个股指标
# ---------------------------------------------------------------------------

def compute_stock_metrics(holding: Dict[str, Any], raw: Dict[str, Any]) -> Dict[str, Any]:
    """把「持仓 + 原始数据」合成一只股票的完整体检结果。"""
    code = str(holding.get("code") or raw.get("code") or "")
    closes = [float(c) for c in (raw.get("closes") or []) if c]
    price = float(holding.get("current_price") or raw.get("price") or (closes[-1] if closes else 0.0))
    if not closes:
        closes = [price or 1.0]

    window = closes[-RISK_WINDOW:] if len(closes) > RISK_WINDOW else closes
    hi, lo = max(window), min(window)
    pos250 = 50.0 if hi - lo <= 1e-9 else _clamp((price - lo) / (hi - lo) * 100.0)

    dif, dea, hist, macd_dir = macd(closes)
    ma20, ma60 = sma(closes, 20), sma(closes, 60)

    pe = raw.get("pe")
    pb = raw.get("pb")
    pe_pct = percentile_rank(raw.get("pe_history") or [], float(pe)) if pe else 50.0
    pb_pct = percentile_rank(raw.get("pb_history") or [], float(pb)) if pb else 50.0

    metrics = {
        "price": round(price, 2),
        "rsi6": round(rsi(closes, 6), 1),
        "macd_dif": dif,
        "macd_dea": dea,
        "ma20": ma20,
        "ma60": ma60,
        "pos250": round(pos250, 1),
    }

    four = {
        "估值": round(_valuation_score(pe_pct, pb_pct), 1),
        "基本面": round(_fundamental_score(raw), 1),
        "资金": round(_fund_score(raw), 1),
        "技术": round(_technical_score(metrics), 1),
    }
    composite = round(sum(WEIGHTS[d] * four[d] for d in DIMENSIONS), 1)

    quantity = float(holding.get("quantity") or 0)
    cost_price = holding.get("cost_price")
    cost_price = float(cost_price) if cost_price else None
    market_value = round(price * quantity, 2)
    pnl_pct = round((price / cost_price - 1) * 100, 2) if cost_price else None

    flow = raw.get("fund_flow") or {}
    return {
        "code": code,
        "name": holding.get("name") or raw.get("name") or f"股票{code}",
        # 优先用适配器实时算出的行业（public 走 emweb 真实行业）；holding 自带 sector 作为兜底，
        # 避免 DB 里可能过期的 sector 覆盖 public 适配器的新鲜真实行业（影响 HHI / 板块归类）。
        "sector": (raw.get("sector") or holding.get("sector")) or "其他",
        "price": round(price, 2),
        "prev_close": raw.get("prev_close"),
        "change_pct": round(float(raw.get("change_pct") or 0.0), 2),
        "quantity": quantity,
        "cost_price": cost_price,
        "market_value": market_value,
        "pnl_pct": pnl_pct,
        "weight_pct": 0.0,  # 组合层回填
        # 估值
        "pe": round(float(pe), 1) if pe else None,
        "pb": round(float(pb), 2) if pb else None,
        "pe_pct": round(pe_pct, 1),
        "pb_pct": round(pb_pct, 1),
        # 基本面
        "roe": raw.get("roe"),
        "gross_margin": raw.get("gross_margin"),
        "net_margin": raw.get("net_margin"),
        "debt_ratio": raw.get("debt_ratio"),
        "rev_yoy": raw.get("rev_yoy"),
        "profit_yoy": raw.get("profit_yoy"),
        # 资金
        "fund_d5": round(float(flow.get("d5") or 0.0), 2),
        "fund_d20": round(float(flow.get("d20") or 0.0), 2),
        "float_mv": raw.get("float_mv"),
        # 技术
        "rsi6": metrics["rsi6"],
        "rsi12": round(rsi(closes, 12), 1),
        "rsi24": round(rsi(closes, 24), 1),
        "macd_dif": round(dif, 3),
        "macd_dea": round(dea, 3),
        "macd_hist": round(hist, 3),
        "macd_dir": macd_dir,
        "ma20": round(ma20, 2) if ma20 else None,
        "ma60": round(ma60, 2) if ma60 else None,
        "pos250": metrics["pos250"],
        "high250": round(hi, 2),
        "low250": round(lo, 2),
        "dist_high_pct": round((price / hi - 1) * 100, 2) if hi > 0 else 0.0,
        # 打分
        "four_scores": four,
        "composite": composite,
        "sources": raw.get("sources") or {},
        "_returns": daily_returns(closes[-(CORR_WINDOW + 1):]),
        "_closes_tail": closes[-RISK_WINDOW:],
    }


# ---------------------------------------------------------------------------
# 组合层
# ---------------------------------------------------------------------------

def compute_correlation(stocks: List[Dict[str, Any]]):
    labels = [s["name"] for s in stocks]
    series = [s.get("_returns") or [] for s in stocks]
    n = len(stocks)
    matrix: List[List[float]] = []
    for i in range(n):
        row: List[float] = []
        for j in range(n):
            row.append(1.0 if i == j else round(pearson(series[i], series[j]), 2))
        matrix.append(row)
    return labels, matrix


def compute_portfolio(stocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_mv = sum(s["market_value"] for s in stocks)
    if total_mv > 0:
        for s in stocks:
            s["weight_pct"] = round(s["market_value"] / total_mv * 100, 1)
    else:  # 数量缺失时按等权，保证报告可读
        eq = round(100.0 / len(stocks), 1) if stocks else 0.0
        for s in stocks:
            s["weight_pct"] = eq

    # 行业占比
    bucket: Dict[str, float] = {}
    for s in stocks:
        bucket[s["sector"]] = bucket.get(s["sector"], 0.0) + (s["market_value"] or 0.0)
    base = sum(bucket.values())
    if base > 0:
        industry_share = [{"name": k, "pct": round(v / base * 100, 1)}
                          for k, v in sorted(bucket.items(), key=lambda kv: -kv[1])]
    else:
        n = max(1, len(bucket))
        industry_share = [{"name": k, "pct": round(100.0 / n, 1)} for k in bucket]
    # 消化四舍五入误差，保证合计恰为 100
    if industry_share:
        drift = round(100.0 - sum(x["pct"] for x in industry_share), 1)
        industry_share[0]["pct"] = round(industry_share[0]["pct"] + drift, 1)

    hhi = round(sum((x["pct"]) ** 2 for x in industry_share), 1)
    hhi = max(0.0, min(10000.0, hhi))

    corr_labels, corr_matrix = compute_correlation(stocks)

    # 组合日收益（按当前权重近似），用于年化波动 / 最大回撤
    weights = [(s["weight_pct"] or 0.0) / 100.0 for s in stocks]
    series = [s.get("_closes_tail") or [] for s in stocks]
    usable = min((len(x) for x in series), default=0)
    port_rets: List[float] = []
    if usable >= 20:
        rets = [daily_returns(x[-usable:]) for x in series]
        for t in range(len(rets[0])):
            port_rets.append(sum(w * r[t] for w, r in zip(weights, rets)))
    vol_annual_pct = round(_stdev(port_rets) * math.sqrt(TRADING_DAYS) * 100, 1) if port_rets else 0.0
    equity = [1.0]
    for r in port_rets:
        equity.append(equity[-1] * (1 + r))
    mdd = round(max_drawdown_pct(equity), 1)

    off_diag = [corr_matrix[i][j] for i in range(len(stocks)) for j in range(i + 1, len(stocks))]
    ranked = sorted(stocks, key=lambda s: -(s["weight_pct"] or 0))

    return {
        "total_mv": round(total_mv, 2),
        "stock_count": len(stocks),
        "industry_share": industry_share,
        "hhi": hhi,
        "top1_pct": ranked[0]["weight_pct"] if ranked else 0.0,
        "top1_name": ranked[0]["name"] if ranked else "",
        "top3_pct": round(sum(s["weight_pct"] for s in ranked[:3]), 1),
        "corr_labels": corr_labels,
        "corr_matrix": corr_matrix,
        "corr_avg": round(_mean(off_diag), 2) if off_diag else 0.0,
        "corr_max": round(max(off_diag), 2) if off_diag else 0.0,
        "corr_min": round(min(off_diag), 2) if off_diag else 0.0,
        "vol_annual_pct": vol_annual_pct,
        "max_drawdown_pct": mdd,
        "avg_composite": round(_mean([s["composite"] for s in stocks]), 1) if stocks else 0.0,
        "weights": [{"name": s["name"], "code": s["code"], "pct": s["weight_pct"]} for s in ranked],
    }


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def _normalize_holdings(holdings: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: Dict[str, Dict[str, Any]] = {}
    for h in holdings or []:
        code = str(h.get("code") or "").strip()
        if not code:
            continue
        item = {
            "code": code,
            "name": (h.get("name") or "").strip() or None,
            "sector": (h.get("sector") or "").strip() or None,
            "quantity": float(h.get("quantity") or 0),
            "cost_price": h.get("cost_price"),
            "current_price": h.get("current_price"),
        }
        if code in seen:  # 同一代码多笔持仓合并
            prev = seen[code]
            qty = prev["quantity"] + item["quantity"]
            if prev.get("cost_price") and item.get("cost_price") and qty > 0:
                prev["cost_price"] = round(
                    (float(prev["cost_price"]) * prev["quantity"]
                     + float(item["cost_price"]) * item["quantity"]) / qty, 4)
            prev["quantity"] = qty
            continue
        seen[code] = item
        out.append(item)
    return out


def build_report(holdings: Sequence[Dict[str, Any]], adapter: Optional[DataAdapter] = None,
                 title: Optional[str] = None) -> Dict[str, Any]:
    """生成体检「事实层」：meta + stocks + portfolio（不含任何自然语言）。"""
    items = _normalize_holdings(holdings)
    if not items:
        raise ValueError("持仓为空，无法生成报告")
    adapter = adapter or DemoDataAdapter()

    raw_map = adapter.fetch([i["code"] for i in items]) or {}
    stocks: List[Dict[str, Any]] = []
    degraded: List[str] = []
    for it in items:
        raw = raw_map.get(it["code"])
        if not raw:  # 数据源没给，用确定性合成兜底，避免整份报告失败
            raw = synth_raw(it["code"], it.get("name"), it.get("sector"), it.get("current_price"))
            degraded.append(it["code"])
        stocks.append(compute_stock_metrics(it, raw))

    portfolio = compute_portfolio(stocks)

    now = dt.datetime.now()
    src_flags: Dict[str, str] = {}
    for s in stocks:
        for k, v in (s.get("sources") or {}).items():
            if v == "synthetic":
                src_flags[k] = "部分合成"
            else:
                src_flags.setdefault(k, "实时")
    notes = [f"{k}：{v}" for k, v in src_flags.items()]
    if degraded:
        notes.append("数据缺失已按模型兜底：" + "、".join(degraded))

    return {
        "meta": {
            "title": title or "持仓体检报告",
            "generated_at": now.strftime("%Y-%m-%d %H:%M"),
            "generated_date": now.strftime("%Y-%m-%d"),
            "stock_count": len(stocks),
            "adapter": getattr(adapter, "name", "unknown"),
            "weights": dict(WEIGHTS),
            "data_notes": notes,
        },
        "stocks": stocks,
        "portfolio": portfolio,
    }


def public_stocks(stocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """剔除以 `_` 开头的内部字段（序列化给前端时使用）。"""
    return [{k: v for k, v in s.items() if not k.startswith("_")} for s in stocks]
