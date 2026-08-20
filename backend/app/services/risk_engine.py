"""风险引擎：基于持仓计算风险预警并触发通知。

规则（与前端 clientRiskAlerts 逻辑对齐）：
1. 组合浮亏：累计亏损 ≤ -8% -> high；0 < 亏损 -> mid
2. 行业集中度：单一行业市值占比 > 50% -> high
3. 单票占比：单一持仓市值占比 > 40% -> high
4. 个股深度亏损：单只亏损 ≤ -20% -> mid

现价通过实时行情获取（positions 表不存储现价）：
实时行情(stock_price) → 日K线最近收盘(stock_daily_price) → 成本价兜底。
"""
from sqlalchemy.orm import Session

from ..models import Client, RiskAlert, ALERT_OPEN, NOTIF_RISK
from . import notification_service
from .stock_price_service import get_stock_prices, get_prev_close


def _price_map(db: Session, client: Client) -> dict[str, float]:
    """构建 {code: 现价} 映射（实时行情 → 日K线 → 成本价兜底）。"""
    codes = list({p.code for p in client.positions})
    realtime = get_stock_prices(db, codes)
    result: dict[str, float] = {}
    for p in client.positions:
        sp = realtime.get(p.code)
        if sp and sp.get("current_price"):
            result[p.code] = sp["current_price"]
        else:
            prev = get_prev_close(db, p.code)
            result[p.code] = prev if prev else p.cost_price
    return result


def _stats(client: Client, prices: dict[str, float]):
    total_market = total_cost = 0.0
    for p in client.positions:
        total_market += prices.get(p.code, p.cost_price) * p.quantity
        total_cost += p.cost_price * p.quantity
    total_pnl = total_market - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0.0
    return total_market, total_cost, total_pnl, total_pnl_pct


def evaluate_client(db: Session, client: Client) -> list[dict]:
    """纯计算：返回预警规则命中结果，不落库。使用实时价格估值。"""
    alerts: list[dict] = []
    prices = _price_map(db, client)
    total_market, _cost, total_pnl, total_pnl_pct = _stats(client, prices)

    if total_pnl < 0 and total_pnl_pct <= -8:
        alerts.append({"type": "loss", "level": "high", "title": "组合亏损超阈值",
                       "description": f"累计亏损 {total_pnl_pct:.1f}%，建议关注止损与调仓"})
    elif total_pnl < 0:
        alerts.append({"type": "loss", "level": "mid", "title": "组合浮亏",
                       "description": f"累计亏损 {total_pnl_pct:.1f}%"})

    sector_map: dict[str, float] = {}
    for p in client.positions:
        mv = prices.get(p.code, p.cost_price) * p.quantity
        sector_map[p.sector] = sector_map.get(p.sector, 0.0) + mv
    if sector_map and total_market > 0:
        top_sector, top_val = max(sector_map.items(), key=lambda kv: kv[1])
        pct = top_val / total_market * 100
        if pct > 50:
            alerts.append({"type": "sector", "level": "high", "title": "行业集中度过高",
                           "description": f"{top_sector} 占比 {pct:.0f}%，单一行业风险较大"})

    for p in client.positions:
        mv = prices.get(p.code, p.cost_price) * p.quantity
        if total_market > 0 and mv / total_market * 100 > 40:
            alerts.append({"type": "stock", "level": "high", "title": "单票占比过高",
                           "description": f"{p.name} 占比 {mv / total_market * 100:.0f}%，集中持仓风险高"})

    for p in client.positions:
        current = prices.get(p.code, p.cost_price)
        pnl_pct = (current - p.cost_price) / p.cost_price * 100
        if pnl_pct <= -20:
            alerts.append({"type": "stock", "level": "mid", "title": "个股深度亏损",
                           "description": f"{p.name} 亏损 {pnl_pct:.1f}%"})

    return alerts


def run_risk_evaluation(db: Session, client: Client) -> tuple[list[RiskAlert], list[str]]:
    """评估并落库预警，向相关客服与顾问分发通知；返回 (alerts, recipients)。"""
    # 清理旧 open 预警，避免重复
    db.query(RiskAlert).filter(
        RiskAlert.client_id == client.id, RiskAlert.status == ALERT_OPEN
    ).delete()
    db.flush()

    evaluated = evaluate_client(db, client)
    alerts: list[RiskAlert] = []
    for a in evaluated:
        alert = RiskAlert(
            client_id=client.id, type=a["type"], level=a["level"],
            title=a["title"], description=a["description"], status=ALERT_OPEN,
        )
        db.add(alert)
        alerts.append(alert)
    db.commit()
    for alert in alerts:
        db.refresh(alert)

    recipients: list[str] = []
    if alerts:
        recipients = list(dict.fromkeys(list(client.service_ids) + [client.advisor_id]))
        high = any(a.level == "high" for a in alerts)
        title = "风险预警" if high else "持仓提示"
        content = f"客户 {client.name}（{client.id}）触发 {len(alerts)} 条风险预警，请及时处理。"
        notification_service.dispatch(db, recipients, title, content, NOTIF_RISK)

    return alerts, recipients
