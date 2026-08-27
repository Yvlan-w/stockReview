"""风险引擎：基于持仓计算风险预警并触发通知。

规则（与前端 clientRiskAlerts 逻辑对齐）：
1. 单一持仓占比：30% ~ 50% 黄色(mid)；> 50% 红色(high)
2. 行业集中度：单一行业市值占比 > 60% 红色(high)
3. 组合浮亏（按客户风险等级阈值）：
   - 保守型 黄4%/红5%；稳健型 黄8%/红10%；平衡型 黄12%/红15%；
     积极型 黄16%/红20%；激进型 黄20%/红25%
4. 客户恭喜提醒（绿色 positive）：组合浮盈达到对应风险等级阈值时触发，
   指标与浮亏预警相同但方向为浮盈

预警管理（落库去重规则）：
- 组合浮盈/浮亏预警（dimension='portfolio'）仅保留最新一条
- 单票占比预警按股票维度（dimension=代码）保留最新一条
- 行业集中度预警按行业维度（dimension=行业名）保留最新一条

现价通过实时行情获取（positions 表不存储现价）：
实时行情(stock_price) → 日K线最近收盘(stock_daily_price) → 成本价兜底。
"""
from sqlalchemy.orm import Session

from ..models import Client, RiskAlert, ALERT_OPEN, NOTIF_RISK
from . import notification_service
from .stock_price_service import get_stock_prices, get_prev_close

# 各风险等级的组合浮亏/浮盈预警阈值（黄色, 红色），单位：百分比
RISK_THRESHOLDS: dict[str, tuple[float, float]] = {
    "保守型": (4.0, 5.0),
    "稳健型": (8.0, 10.0),
    "平衡型": (12.0, 15.0),
    "积极型": (16.0, 20.0),
    "激进型": (20.0, 25.0),
}
# 客户未设置风险等级时的默认阈值（稳健型）
DEFAULT_THRESHOLDS: tuple[float, float] = RISK_THRESHOLDS["稳健型"]

# 单票占比预警阈值：30% ~ 50% 黄色，> 50% 红色
STOCK_RATIO_YELLOW = 30.0
STOCK_RATIO_RED = 50.0
# 行业集中度预警阈值：> 60% 红色
SECTOR_RATIO_RED = 60.0


def get_thresholds(client) -> tuple[float, float]:
    """按客户风险等级取（黄色, 红色）阈值；未设置时用稳健型默认。

    兼容鸭子类型：SimpleNamespace 测试对象无 risk_level 时兜底默认。
    """
    risk_level = getattr(client, 'risk_level', None)
    return RISK_THRESHOLDS.get(risk_level, DEFAULT_THRESHOLDS)


def _price_map(db: Session | None, client) -> dict[str, float]:
    """构建 {code: 现价} 映射（实时行情 → 日K线 → 成本价兜底）。

    兼容鸭子类型：
    - 真实持仓对象有 code 字段时，按 code 走实时行情/日K线；
    - 纯单元测试 SimpleNamespace 只有 name/price 时，直接用 p.price（测试注入的模拟价）
      或 p.cost_price 兜底，并用 p.name 作为 key 入字典（后续 _stats 也按 name 取）。
    当 db 为 None 时，不查数据库，直接用持仓对象自带价格。
    """
    result: dict[str, float] = {}
    # 兼容测试持仓 SimpleNamespace：有 price 字段 → 直接用提供的模拟价
    first = next(iter(client.positions), None)
    using_mock_price = db is None and first is not None and hasattr(first, 'price')
    if using_mock_price:
        for p in client.positions:
            # 测试持仓用 name 作 key，真实对象用 code 作 key，双 key 写入，下游兼容命中
            price_val = getattr(p, 'price', p.cost_price)
            if hasattr(p, 'code'):
                result[p.code] = price_val
            # name 兜底 key，避免鸭子类型缺 code 时失效
            result[p.name] = price_val
        return result

    codes = [p.code for p in client.positions if hasattr(p, 'code')]
    realtime = get_stock_prices(db, codes) if db else {}
    for p in client.positions:
        key = p.code if hasattr(p, 'code') else p.name
        sp = realtime.get(p.code) if db else None
        if sp and sp.get("current_price"):
            result[key] = sp["current_price"]
        elif db:
            prev = get_prev_close(db, p.code)
            result[key] = prev if prev else p.cost_price
        else:
            result[key] = p.cost_price
    return result


def _stats(client, prices: dict[str, float]):
    total_market = total_cost = 0.0
    for p in client.positions:
        # 兼容鸭子类型：先按 code 取价，失败按 name 取，兜底成本价
        price = prices.get(p.code if hasattr(p, 'code') else None)
        if price is None:
            price = prices.get(p.name, p.cost_price)
        total_market += price * p.quantity
        total_cost += p.cost_price * p.quantity
    total_pnl = total_market - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0.0
    return total_market, total_cost, total_pnl, total_pnl_pct


def evaluate_client(db_or_client: Session | Client | None = None, client: Client | None = None) -> list[dict]:
    """纯计算：返回预警规则命中结果，不落库。使用实时价格估值（浮动口径）。

    每条结果含 type/level/dimension/title/description。

    调用形式兼容两种签名：
      - evaluate_client(db, client)   — 标准（使用数据库查询实时行情）
      - evaluate_client(client)        — 纯单元测试（无需数据库，使用持仓 mock 价或成本价）；
                                         client 可以是 models.Client 或测试用 SimpleNamespace。
    """
    # 签名分派：第一个参数若"看起来是 Session（是 sqlalchemy.orm Session 实例）"，
    # 则视为 (db, client) 两参数形式；否则视为单参数 client 形式。
    # 之所以不用 isinstance(Client)：为了兼容测试的 SimpleNamespace。
    db = None
    if isinstance(db_or_client, Session):
        db = db_or_client
    else:
        # 单参数：把第一个参数当 client
        if client is None:
            client = db_or_client
    if client is None:
        return []

    alerts: list[dict] = []
    prices = _price_map(db, client)
    total_market, _cost, total_pnl, total_pnl_pct = _stats(client, prices)
    yellow, red = get_thresholds(client)
    risk_level = getattr(client, 'risk_level', None) or "稳健型"

    # ---- 组合浮亏 / 浮盈（互斥；仅当前持仓的浮动盈亏口径）----
    if total_pnl < 0:
        if total_pnl_pct <= -red:
            alerts.append({
                "type": "loss", "level": "high", "dimension": "portfolio",
                "title": "组合浮亏超红色预警线",
                "description": (
                    f"组合浮亏 {total_pnl_pct:.1f}%，已超过 {risk_level} 客户"
                    f"红色预警线 -{red:.0f}%，建议立即关注止损与调仓"),
            })
        elif total_pnl_pct <= -yellow:
            alerts.append({
                "type": "loss", "level": "mid", "dimension": "portfolio",
                "title": "组合浮亏超黄色预警线",
                "description": (
                    f"组合浮亏 {total_pnl_pct:.1f}%，已超过 {risk_level} 客户"
                    f"黄色预警线 -{yellow:.0f}%，建议关注组合回撤"),
            })
    elif total_pnl > 0:
        if total_pnl_pct >= red:
            alerts.append({
                "type": "gain", "level": "positive", "dimension": "portfolio",
                "title": "恭喜：组合浮盈表现亮眼",
                "description": (
                    f"组合浮盈 {total_pnl_pct:.1f}%，达到 {risk_level} 客户"
                    f"红色指标 {red:.0f}%，表现优秀，可考虑适当止盈"),
            })
        elif total_pnl_pct >= yellow:
            alerts.append({
                "type": "gain", "level": "positive", "dimension": "portfolio",
                "title": "恭喜：组合浮盈达标",
                "description": (
                    f"组合浮盈 {total_pnl_pct:.1f}%，达到 {risk_level} 客户"
                    f"黄色指标 {yellow:.0f}%，收益稳健"),
            })

    # ---- 单票占比：30%~50% 黄，> 50% 红（按股票维度）----
    for p in client.positions:
        if total_market <= 0:
            break
        price = (prices.get(p.code if hasattr(p, 'code') else None)
                 or prices.get(p.name, p.cost_price))
        pct = price * p.quantity / total_market * 100
        # dimension：鸭子类型优先 code，兜底 name
        dim = getattr(p, 'code', None) or p.name
        if pct > STOCK_RATIO_RED:
            alerts.append({
                "type": "stock", "level": "high", "dimension": dim,
                "title": "单票占比过高（红色）",
                "description": (
                    f"{p.name} 占组合 {pct:.0f}%，超过 50% 红色预警线，"
                    f"集中持仓风险极高，建议分批调降"),
            })
        elif pct >= STOCK_RATIO_YELLOW:
            alerts.append({
                "type": "stock", "level": "mid", "dimension": dim,
                "title": "单票占比偏高（黄色）",
                "description": (
                    f"{p.name} 占组合 {pct:.0f}%，处于 30%~50% 黄色预警区间，"
                    f"建议关注集中度风险"),
            })

    # ---- 行业集中度：> 60% 红（按行业维度）----
    sector_map: dict[str, float] = {}
    for p in client.positions:
        price = (prices.get(p.code if hasattr(p, 'code') else None)
                 or prices.get(p.name, p.cost_price))
        mv = price * p.quantity
        sector_map[p.sector] = sector_map.get(p.sector, 0.0) + mv
    if sector_map and total_market > 0:
        for sector, val in sector_map.items():
            pct = val / total_market * 100
            if pct > SECTOR_RATIO_RED:
                alerts.append({
                    "type": "sector", "level": "high", "dimension": sector,
                    "title": "行业集中度过高",
                    "description": (
                        f"{sector} 行业占组合 {pct:.0f}%，超过 60% 红色预警线，"
                        f"单一行业风险较大，建议均衡配置"),
                })

    return alerts


def run_risk_evaluation(db: Session, client: Client) -> tuple[list[RiskAlert], list[str]]:
    """评估并落库预警，向相关客服与顾问分发通知；返回 (alerts, recipients)。

    预警管理：删除该客户全部 open 预警后重插本次评估结果，
    实现每维度（组合/单票/行业）仅保留最新一条；
    已确认（acknowledged）与已解决（resolved）的历史预警不受影响。
    """
    # 清理旧 open 预警，避免重复（全维度覆盖 = 每维度仅保留最新一条）
    db.query(RiskAlert).filter(
        RiskAlert.client_id == client.id, RiskAlert.status == ALERT_OPEN
    ).delete()
    db.flush()

    evaluated = evaluate_client(db, client)
    alerts: list[RiskAlert] = []
    for a in evaluated:
        alert = RiskAlert(
            client_id=client.id, type=a["type"], level=a["level"],
            dimension=a.get("dimension"), title=a["title"],
            description=a["description"], status=ALERT_OPEN,
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
        positive_only = all(a.level == "positive" for a in alerts)
        if positive_only:
            title = "客户喜报"
        elif high:
            title = "风险预警"
        else:
            title = "持仓提示"
        content = f"客户 {client.name}（{client.id}）触发 {len(alerts)} 条预警，请及时查看。"
        notification_service.dispatch(db, recipients, title, content, NOTIF_RISK)

    return alerts, recipients
