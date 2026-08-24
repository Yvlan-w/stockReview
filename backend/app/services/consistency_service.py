"""数据一致性对账服务：持仓/交易流水/现金 跨表核对（防回归）。

由 scripts/consistency_check.py（一次性人工查验）演化而来，供后台任务
每日自动执行：发现 FATAL 级不一致时向管理员发送站内信告警。

FATAL 检查项（会导致盈亏计算错误）：
    1. 持仓数量 vs 流水推算数量不一致
    2. 流水净持仓为正但持仓表无记录（持仓缺失）
    3. 流水累计卖出超过买入（超卖）
    4. 同客户同证券代码重复持仓行
    5. 孤儿数据（引用不存在的客户）
    6. 非法持仓（数量<=0 或成本价<=0）

WARN 检查项（数据完整但不便自动判定）：
    - 持仓无任何交易流水（种子/导入数据，建议补期初建仓流水）
    - 持仓代码无行情数据（现价退化为成本价）
"""
from sqlalchemy import text
from sqlalchemy.orm import Session


def check_consistency(db: Session) -> dict:
    """执行全部一致性检查，返回 {"fatals": [...], "warnings": [...]}。

    每个问题为 "{维度}: {明细}" 字符串。
    """
    fatals: list[str] = []
    warnings: list[str] = []

    positions = db.execute(text(
        "SELECT client_id, code, name, quantity, cost_price FROM positions "
        "ORDER BY client_id, code"
    )).mappings().all()
    tx_rows = db.execute(text(
        "SELECT client_id, code, name, action, quantity, price, fee_amount, "
        "trade_date FROM transactions ORDER BY client_id, code, trade_date, id"
    )).mappings().all()

    # 按流水聚合买卖数量
    tx_agg: dict[tuple, dict] = {}
    for t in tx_rows:
        key = (t["client_id"], t["code"])
        if key not in tx_agg:
            tx_agg[key] = {"buy": 0, "sell": 0, "name": t["name"]}
        if t["action"] == "buy":
            tx_agg[key]["buy"] += t["quantity"]
        else:
            tx_agg[key]["sell"] += t["quantity"]

    pos_keys = {(p["client_id"], p["code"]) for p in positions}

    # 1. 持仓 vs 流水数量对账
    for p in positions:
        key = (p["client_id"], p["code"])
        a = tx_agg.get(key)
        if a is None:
            warnings.append(
                f"持仓无流水: {p['client_id']} {p['code']} {p['name']} "
                f"持仓 {p['quantity']} 股（期初导入未补建仓流水）")
            continue
        net = a["buy"] - a["sell"]
        if net != p["quantity"]:
            fatals.append(
                f"数量不一致: {p['client_id']} {p['code']} {p['name']} "
                f"持仓表 {p['quantity']} 股 vs 流水推算 {net} 股")

    # 2/3. 流水有但持仓缺失 / 超卖
    for key, a in tx_agg.items():
        if key not in pos_keys:
            net = a["buy"] - a["sell"]
            if net > 0:
                fatals.append(
                    f"持仓缺失: {key[0]} {key[1]} {a['name']} "
                    f"流水净持仓 {net} 股但持仓表无记录")
            elif net < 0:
                fatals.append(
                    f"超卖: {key[0]} {key[1]} {a['name']} "
                    f"流水累计卖出超过买入（净 {net} 股）")

    # 4. 重复持仓
    dups = db.execute(text(
        "SELECT client_id, code, COUNT(*) AS n FROM positions "
        "GROUP BY client_id, code HAVING COUNT(*) > 1"
    )).mappings().all()
    for d in dups:
        fatals.append(
            f"重复持仓: {d['client_id']} {d['code']} 存在 {d['n']} 行")

    # 5. 孤儿数据
    for tbl in ("positions", "transactions", "pnl_daily_snapshot"):
        orphans = db.execute(text(
            f"SELECT DISTINCT {tbl}.client_id FROM {tbl} "  # noqa: S608（固定表名）
            f"LEFT JOIN clients ON clients.id = {tbl}.client_id "
            f"WHERE clients.id IS NULL"
        )).scalars().all()
        if orphans:
            fatals.append(
                f"孤儿数据: {tbl} 引用不存在的客户 {', '.join(orphans)}")

    # 6. 非法持仓
    bad = db.execute(text(
        "SELECT client_id, code, name, quantity, cost_price FROM positions "
        "WHERE quantity <= 0 OR cost_price <= 0"
    )).mappings().all()
    for b in bad:
        fatals.append(
            f"非法持仓: {b['client_id']} {b['code']} {b['name']} "
            f"qty={b['quantity']} cost={b['cost_price']}")

    # WARN: 持仓无行情
    holding_codes = {p["code"] for p in positions}
    for code in sorted(holding_codes):
        rt = db.execute(text(
            "SELECT current_price FROM stock_price WHERE code = :c"),
            {"c": code}).scalar_one_or_none()
        kd = db.execute(text(
            "SELECT trade_date FROM stock_daily_price WHERE code = :c "
            "LIMIT 1"), {"c": code}).scalar_one_or_none()
        if rt is None and kd is None:
            warnings.append(f"行情缺失: {code} 无实时行情与日K线")

    return {"fatals": fatals, "warnings": warnings}


def report_consistency_alert(db: Session, fatals: list[str]) -> int:
    """将 FATAL 一致性问题以站内信告警给全部管理员，返回通知数。"""
    if not fatals:
        return 0
    from ..models import NOTIF_SYSTEM
    from .notification_service import dispatch

    admin_ids = db.execute(text(
        "SELECT id FROM users WHERE role = 'admin' AND is_active = 1"
    )).scalars().all()
    if not admin_ids:
        return 0

    detail = "\n".join(f"• {f}" for f in fatals[:20])
    more = f"\n… 共 {len(fatals)} 项" if len(fatals) > 20 else ""
    content = (
        f"检测到 {len(fatals)} 项持仓/流水数据不一致（FATAL），"
        f"盈亏计算可能失真，请及时人工核查。\n\n{detail}{more}\n\n"
        f"排查工具: backend/scripts/consistency_check.py"
    )
    created = dispatch(db, admin_ids, "数据一致性告警", content, category=NOTIF_SYSTEM)
    return len(created)
