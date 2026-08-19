"""市场深度分析服务：高低切 / 领涨方向 / 核心驱动因素。

利用已落库的板块行情 + 大盘快照数据，计算结构化分析结果，
前端按固定模板渲染为可视化卡片。
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import MarketSector, MarketSnapshot

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 行业主题词典（用于匹配领涨行业的叙事主题）
# ---------------------------------------------------------------------------
INDUSTRY_THEMES: list[dict] = [
    {"keywords": ["半导体", "芯片", "集成电路", "数字芯片", "模拟芯片", "半导体材料"],
     "theme": "科技自主可控", "driver": "国产替代加速预期", "category": "科技成长"},
    {"keywords": ["软件开发", "计算机", "软件", "AI", "人工智能", "信创", "云计算", "大数据"],
     "theme": "数字经济", "driver": "信息化升级需求", "category": "科技成长"},
    {"keywords": ["通信", "5G", "光通信", "光纤", "卫星导航"],
     "theme": "新型基础设施", "driver": "通信基础设施建设", "category": "科技成长"},
    {"keywords": ["军工", "航天", "航空", "国防", "军民融合", "导弹", "无人机"],
     "theme": "国防安全", "driver": "装备交付周期提速", "category": "安全主题"},
    {"keywords": ["银行", "保险", "证券", "非银金融", "国有大型银行"],
     "theme": "金融稳定", "driver": "宽货币环境下的资产配置", "category": "金融价值"},
    {"keywords": ["电力", "新能源", "光伏", "风电", "储能", "电池", "充电桩"],
     "theme": "绿色能源转型", "driver": "双碳目标下的能源革命", "category": "新能源"},
    {"keywords": ["新能源车", "汽车", "整车", "锂电池", "动力电池"],
     "theme": "电动化浪潮", "driver": "新能源车渗透率持续提升", "category": "新能源"},
    {"keywords": ["医药", "医疗", "生物", "创新药", "医疗器械", "中药"],
     "theme": "健康中国", "driver": "人口老龄化与医疗消费升级", "category": "消费医疗"},
    {"keywords": ["白酒", "食品饮料", "食品", "饮料"],
     "theme": "消费复苏", "driver": "消费场景恢复与价格上行", "category": "消费价值"},
    {"keywords": ["家电", "白色家电", "黑色家电"],
     "theme": "地产后周期", "driver": "地产竣工回暖带动需求", "category": "消费价值"},
    {"keywords": ["工程机械", "机械", "通用设备", "专用设备"],
     "theme": "制造业升级", "driver": "设备更新换代政策", "category": "周期制造"},
    {"keywords": ["钢铁", "有色", "煤炭", "石油", "化工", "基础化工"],
     "theme": "周期复苏", "driver": "大宗商品供需再平衡", "category": "周期资源"},
    {"keywords": ["地产", "房地产", "物业服务"],
     "theme": "地产链修复", "driver": "限购松绑与去库存预期", "category": "周期制造"},
    {"keywords": ["建材", "建筑", "基建", "钢铁"],
     "theme": "基建投资", "driver": "稳增长政策发力", "category": "周期制造"},
    {"keywords": ["农业", "种业", "种子", "养殖", "畜牧", "农林牧渔"],
     "theme": "乡村振兴", "driver": "粮食安全与农业现代化", "category": "消费价值"},
    {"keywords": ["交运", "航运", "航空货运", "港口"],
     "theme": "全球贸易复苏", "driver": "海运运费周期上行", "category": "周期制造"},
    {"keywords": ["光伏设备", "风电设备", "电力设备"],
     "theme": "新能源建设", "driver": "风光大基地规划落地", "category": "新能源"},
    {"keywords": ["机器人", "自动化设备", "智能制造"],
     "theme": "工业智能化", "driver": "人口红利消退下的机器换人", "category": "科技成长"},
    {"keywords": ["消费电子", "光学光电子", "面板"],
     "theme": "创新消费电子", "driver": "AI终端设备出货放量", "category": "科技成长"},
    {"keywords": ["化肥", "农药", "化学制品"],
     "theme": "农业支持", "driver": "粮食安全战略下的投入品保障", "category": "周期资源"},
]


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------
def _match_themes(sector_name: str) -> list[dict]:
    """根据行业名匹配主题标签。"""
    matched = []
    for theme_def in INDUSTRY_THEMES:
        for kw in theme_def["keywords"]:
            if kw in sector_name:
                matched.append(theme_def)
                break
    return matched


def _fmt_yi(val: float) -> str:
    """格式化成交额：大数用「亿」(万亿级)，小数用「万」。去除多余小数位。"""
    yi = val / 1e8 if val else 0
    if yi >= 1e4:
        txt = f"{yi / 1e4:.2f}".rstrip("0").rstrip(".")
        return f"{txt}万亿"
    elif yi >= 1:
        txt = f"{yi:.2f}".rstrip("0").rstrip(".")
        return f"{txt}亿"
    else:
        return f"{val / 1e4:.0f}万"


# ---------------------------------------------------------------------------
# 核心分析：高低切 / 领涨方向
# ---------------------------------------------------------------------------
def analyze_sector_rotation(db: Session) -> dict:
    """分析板块高低切与领涨方向。

    Returns:
        {
            "leaders": [{"name", "changePct", "turnover", "themes": [...], "themeSummary": str}],
            "laggards": [...],
            "rotationSignal": "strong" | "moderate" | "weak" | "none",
            "rotationText": str,
            "marketBreadth": {"upRatio", "downRatio", "score"},
        }
    """
    sectors = db.execute(
        select(MarketSector).where(MarketSector.change_pct.isnot(None))
    ).scalars().all()

    if not sectors:
        return {
            "leaders": [], "laggards": [],
            "rotationSignal": "none",
            "rotationText": "暂无板块数据，无法判断领涨方向",
            "marketBreadth": {"upCount": 0, "downCount": 0, "upRatio": 0, "downRatio": 0, "score": 0},
        }

    # 获取快照数据用于准确的市场宽度（避免板块重叠计数）
    snap = db.execute(
        select(MarketSnapshot).order_by(MarketSnapshot.id.desc())
    ).scalars().first()
    snap_data = snap.data if snap and isinstance(snap.data, dict) else {}

    sorted_by_change = sorted(sectors, key=lambda s: s.change_pct or 0, reverse=True)

    leaders = []
    for s in sorted_by_change[:5]:
        themes = _match_themes(s.sector_name)
        leaders.append({
            "name": s.sector_name,
            "sectorCode": s.sector_code,
            "changePct": round(s.change_pct or 0, 2),
            "turnover": s.turnover or 0,
            "turnoverText": _fmt_yi(s.turnover or 0),
            "upCount": s.up_count or 0,
            "downCount": s.down_count or 0,
            "themes": [t["theme"] for t in themes],
            "themeSummary": themes[0]["theme"] if themes else None,
        })

    laggards = []
    for s in sorted_by_change[-3:][::-1]:
        themes = _match_themes(s.sector_name)
        laggards.append({
            "name": s.sector_name,
            "sectorCode": s.sector_code,
            "changePct": round(s.change_pct or 0, 2),
            "turnover": s.turnover or 0,
            "turnoverText": _fmt_yi(s.turnover or 0),
            "upCount": s.up_count or 0,
            "downCount": s.down_count or 0,
            "themes": [t["theme"] for t in themes],
            "themeSummary": themes[0]["theme"] if themes else None,
        })

    # ---- 市场宽度：优先使用快照数据（去重计数） ----
    total_up = snap_data.get("advCount", 0) or 0
    total_down = snap_data.get("decCount", 0) or 0
    total_flat = snap_data.get("flatCount", 0) or 0
    total = total_up + total_down + total_flat
    if total == 0:
        # 回退：使用板块汇总（可能重叠计数）
        total_up = sum(s.up_count or 0 for s in sectors)
        total_down = sum(s.down_count or 0 for s in sectors)
        total = total_up + total_down

    up_ratio = total_up / total if total > 0 else 0
    down_ratio = total_down / total if total > 0 else 0

    # 领涨幅度差距
    leader_pct = leaders[0]["changePct"] if leaders else 0
    laggard_pct = laggards[-1]["changePct"] if laggards else 0
    spread = leader_pct - laggard_pct

    # 轮动信号判断
    if spread > 6 and leader_pct > 3:
        signal = "strong"
        signal_text = f"领涨板块 {leaders[0]['name']}（{leader_pct:+.2f}%）大幅领涨，高低切信号强烈"
    elif spread > 4:
        signal = "moderate"
        signal_text = f"板块分化明显，{leaders[0]['name']}领涨 {leader_pct:+.2f}%，存在结构性机会"
    elif spread > 2:
        signal = "weak"
        signal_text = f"板块轮动温和，{leaders[0]['name']}（{leader_pct:+.2f}%）表现相对强势"
    else:
        signal = "none"
        signal_text = "各板块涨跌均衡，无明显高低切信号"

    # 市场宽度评分
    score = 0
    if up_ratio > 0.7:
        score = 3
    elif up_ratio > 0.55:
        score = 2
    elif up_ratio > 0.4:
        score = 1

    return {
        "leaders": leaders,
        "laggards": laggards,
        "rotationSignal": signal,
        "rotationText": signal_text,
        "spread": round(spread, 2),
        "marketBreadth": {
            "upCount": total_up,
            "downCount": total_down,
            "flatCount": total_flat,
            "upRatio": round(up_ratio, 3),
            "downRatio": round(down_ratio, 3),
            "score": score,
        },
    }


# ---------------------------------------------------------------------------
# 核心驱动因素分析
# ---------------------------------------------------------------------------
def analyze_drivers(db: Session) -> dict:
    """分析核心驱动因素。

    Returns:
        {
            "capitalFlow": {"direction": "inflow"|"outflow"|"neutral", "strength": float, "text": str},
            "sentiment": {"level": "extreme"|"strong"|"moderate"|"weak", "text": str},
            "structure": {"leadingCategories": [...], "text": str},
            "riskFlags": [...],
            "summary": str,
        }
    """
    snap = db.execute(select(MarketSnapshot).order_by(MarketSnapshot.id.desc())).scalars().first()
    sectors = db.execute(
        select(MarketSector).where(MarketSector.change_pct.isnot(None))
    ).scalars().all()

    if not snap or not sectors:
        return {
            "capitalFlow": {"direction": "neutral", "strength": 0, "text": "暂无成交额数据"},
            "sentiment": {"level": "weak", "text": "暂无市场情绪数据"},
            "structure": {"leadingCategories": [], "text": "暂无板块数据"},
            "riskFlags": ["数据不足"],
            "summary": "数据不足，无法生成分析",
        }

    snap_data = snap.data or {}
    total_volume = snap_data.get("totalVolume", 0) or 0  # 已是亿元
    prev_volume = snap_data.get("prevVolume", 0) or 0    # 已是亿元
    adv_count = snap_data.get("advCount", 0) or 0
    dec_count = snap_data.get("decCount", 0) or 0
    flat_count = snap_data.get("flatCount", 0) or 0
    total_stocks = snap_data.get("totalStocks", adv_count + dec_count + flat_count) or 0

    # ---- 1. 资金面分析 ----
    volume_change_pct = 0
    if prev_volume and prev_volume > 0:
        volume_change_pct = (total_volume - prev_volume) / prev_volume * 100

    if volume_change_pct > 15:
        capital_direction = "inflow"
        capital_text = f"两市成交 {total_volume:.0f} 亿元，较前日放量 {volume_change_pct:.0f}%，资金大幅流入"
    elif volume_change_pct > 5:
        capital_direction = "inflow"
        capital_text = f"两市成交 {total_volume:.0f} 亿元，较前日温和放量 {volume_change_pct:.0f}%"
    elif volume_change_pct < -10:
        capital_direction = "outflow"
        capital_text = f"两市成交 {total_volume:.0f} 亿元，较前日缩量 {abs(volume_change_pct):.0f}%，资金流出明显"
    elif volume_change_pct < -3:
        capital_direction = "outflow"
        capital_text = f"两市成交 {total_volume:.0f} 亿元，较前日小幅缩量 {abs(volume_change_pct):.0f}%"
    else:
        capital_direction = "neutral"
        capital_text = f"两市成交 {total_volume:.0f} 亿元，量能基本持平"

    # 计算行业成交额集中度
    sorted_by_turnover = sorted(sectors, key=lambda s: s.turnover or 0, reverse=True)
    total_sector_turnover = sum(s.turnover or 0 for s in sectors)
    top5_turnover = sum(s.turnover or 0 for s in sorted_by_turnover[:5])
    concentration = top5_turnover / total_sector_turnover if total_sector_turnover > 0 else 0

    # ---- 2. 情绪面分析 ----
    up_ratio = adv_count / total_stocks if total_stocks > 0 else 0
    if up_ratio > 0.85:
        sentiment_level = "extreme"
        sentiment_text = f"普涨格局，上涨家数占比 {up_ratio:.0%}，市场情绪极度乐观"
    elif up_ratio > 0.65:
        sentiment_level = "strong"
        sentiment_text = f"上涨为主，上涨家数占比 {up_ratio:.0%}，市场情绪积极"
    elif up_ratio > 0.45:
        sentiment_level = "moderate"
        sentiment_text = f"多空均衡，上涨家数占比 {up_ratio:.0%}，市场情绪中性"
    else:
        sentiment_level = "weak"
        sentiment_text = f"跌多涨少，上涨家数占比 {up_ratio:.0%}，市场情绪偏弱"

    # ---- 3. 结构面分析 ----
    sorted_by_change = sorted(sectors, key=lambda s: s.change_pct or 0, reverse=True)
    top_leaders = sorted_by_change[:5]

    # 归类领涨主题
    category_scores: dict[str, dict] = {}
    for s in top_leaders:
        themes = _match_themes(s.sector_name)
        for t in themes:
            cat = t["category"]
            if cat not in category_scores:
                category_scores[cat] = {"count": 0, "turnover": 0, "maxPct": 0, "themes": set()}
            category_scores[cat]["count"] += 1
            category_scores[cat]["turnover"] += s.turnover or 0
            category_scores[cat]["maxPct"] = max(category_scores[cat]["maxPct"], s.change_pct or 0)
            category_scores[cat]["themes"].add(t["theme"])

    leading_categories = sorted(
        [{"category": k, **v, "themes": list(v["themes"])} for k, v in category_scores.items()],
        key=lambda x: x["count"] * x["maxPct"],
        reverse=True,
    )

    if leading_categories:
        top_cat = leading_categories[0]
        cat_text = (
            f"领涨方向集中在「{top_cat['category']}」板块，"
            f"主题涵盖{', '.join(top_cat['themes'][:3])}"
        )
    else:
        cat_text = "领涨方向分散，无明显主线"

    # ---- 4. 风险标志 ----
    risk_flags = []
    if up_ratio > 0.9:
        risk_flags.append("市场过热警告：上涨家数占比过高，警惕短期回调")
    if volume_change_pct < -15 and up_ratio > 0.5:
        risk_flags.append("量价背离：缩量上涨，持续性存疑")
    if concentration > 0.35 and len(leading_categories) > 0:
        risk_flags.append(f"集中度较高：Top5 行业成交占比 {concentration:.0%}，关注抱团风险")
    if down_ratio := (1 - up_ratio) > 0.75:
        risk_flags.append("系统性下跌信号：需警惕市场进一步调整")

    # ---- 5. 综合摘要 ----
    summary_parts = []
    for cat in leading_categories[:2]:
        theme_names = "、".join(cat["themes"][:2])
        summary_parts.append(
            f"{theme_names}板块表现强势"
        )
    if summary_parts:
        summary = "；".join(summary_parts) + "。"
    else:
        summary = "板块轮动格局，无明确主线。"

    if capital_direction == "inflow" and sentiment_level in ("strong", "extreme"):
        summary += "资金面与情绪面共振向上，"
    elif capital_direction == "outflow" and sentiment_level in ("strong", "extreme"):
        summary += "资金流出但情绪仍热，存在量价背离风险。"
    elif capital_direction == "outflow":
        summary += "资金流出制约市场表现。"

    return {
        "capitalFlow": {
            "direction": capital_direction,
            "strength": round(volume_change_pct, 1),
            "totalVolume": round(total_volume, 0),
            "prevVolume": round(prev_volume, 0),
            "text": capital_text,
        },
        "sentiment": {
            "level": sentiment_level,
            "upCount": adv_count,
            "downCount": dec_count,
            "flatCount": flat_count,
            "upRatio": round(up_ratio, 3),
            "text": sentiment_text,
        },
        "structure": {
            "leadingCategories": [
                {
                    "category": c["category"],
                    "themeCount": c["count"],
                    "maxPct": round(c["maxPct"], 2),
                    "themes": c["themes"],
                }
                for c in leading_categories[:4]
            ],
            "concentration": round(concentration, 3),
            "text": cat_text,
        },
        "riskFlags": risk_flags,
        "summary": summary,
        "updatedAt": snap.updated_at.isoformat() if snap.updated_at else None,
    }


# ---------------------------------------------------------------------------
# 聚合接口
# ---------------------------------------------------------------------------
def get_market_analysis(db: Session) -> dict:
    """获取完整市场分析（高低切 + 驱动因素）。"""
    rotation = analyze_sector_rotation(db)
    drivers = analyze_drivers(db)
    return {
        "rotation": rotation,
        "drivers": drivers,
    }
