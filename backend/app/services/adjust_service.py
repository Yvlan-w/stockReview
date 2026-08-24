"""调仓执行服务：流水 + 持仓 + 现金 + 快照 的单事务原子更新。

背景：此前调仓是"写流水"与"整体替换持仓"两个独立接口，中途失败会产生
流水与持仓不一致（如 C001 000001 流水有 1000 股、持仓无记录的问题）。
本服务将调仓收敛为一个数据库事务，任一步骤失败全部回滚。

事务内步骤：
    ① 参数与业务校验（持仓存在性、数量足够、现金充足、手续费合法）
    ② 写入 transaction 流水（含手续费、卖出已实现盈亏）
    ③ 更新 positions（买入新建/加权加仓；卖出减仓/清仓删行）
    ④ 勾稽 clients.available_cash（买入扣减、卖出回补）
    ⑤ 触发当日快照增量更新（非交易日自动跳过）
"""
import datetime as dt
from typing import Optional

from sqlalchemy.orm import Session

from ..models import Client, Position, Transaction
from .pnl_service import (
    calc_buy_fee,
    calc_realized_pnl_with_fee,
    compute_portfolio,
    write_daily_snapshot,
)


class AdjustError(ValueError):
    """调仓业务校验失败（统一转 HTTP 422）。"""


def execute_adjust(db: Session, client: Client, *, code: str, action: str,
                   quantity: int, price: float,
                   name: Optional[str] = None, sector: Optional[str] = None,
                   fee_mode: Optional[str] = None,
                   fee_value: Optional[float] = None,
                   trade_date: Optional[str] = None,
                   from_cash: bool = True) -> dict:
    """执行一笔调仓（买入/卖出），单事务原子更新所有相关表。

    Args:
        client: 目标客户（权限已由路由层校验）
        code: 证券代码
        action: 'buy'（新建/加仓）或 'sell'（减仓/清仓）
        quantity: 数量（>0）
        price: 成交价（>0）
        name / sector: 买入新建持仓时必填；持仓已存在时可选（用于纠错名称）
        fee_mode / fee_value: 手续费模式与数值（None = 全局默认费率）
        trade_date: 交易日期（默认今天）
        from_cash: 买入资金来源。True=来自账户可用资金（扣减现金，总资产不变，
            现金→持仓内部划转）；False=外部转入持仓（不扣现金、不校验现金充足，
            总资产随之增加）。仅对买入生效，卖出始终回补现金。

    Returns:
        {transaction, position(清仓为 None), available_cash, portfolio}

    Raises:
        AdjustError: 校验失败（路由层转 422）。任何异常时事务整体回滚。
    """
    try:
        return _execute_adjust_impl(
            db, client, code=code, action=action, quantity=quantity,
            price=price, name=name, sector=sector, fee_mode=fee_mode,
            fee_value=fee_value, trade_date=trade_date, from_cash=from_cash,
        )
    except Exception:
        db.rollback()
        raise


def _execute_adjust_impl(db: Session, client: Client, *, code: str, action: str,
                         quantity: int, price: float,
                         name: Optional[str], sector: Optional[str],
                         fee_mode: Optional[str], fee_value: Optional[float],
                         trade_date: Optional[str], from_cash: bool) -> dict:
    date_str = trade_date or dt.date.today().isoformat()
    amount = quantity * price

    position = db.query(Position).filter(
        Position.client_id == client.id, Position.code == code,
    ).first()

    # ------------------------------------------------------------------
    # ① 业务校验
    # ------------------------------------------------------------------
    if action == "buy":
        if position is None and (not name or not sector):
            raise AdjustError("新建持仓必须提供股票名称与板块")
        fee = calc_buy_fee(amount, fee_mode, fee_value)
        fee_amount = fee["total_fee"]
        # 现金充足性：仅"来自可用资金"的买入需要校验（外部转入不占用账户现金）
        if from_cash and (client.available_cash or 0.0) < amount + fee_amount:
            raise AdjustError(
                f"可用资金不足：需 {amount + fee_amount:.2f} 元"
                f"（含手续费 {fee_amount:.2f}），"
                f"当前仅 {client.available_cash or 0.0:.2f} 元"
            )
    elif action == "sell":
        if position is None:
            raise AdjustError(f"客户 {client.id} 无 {code} 持仓，无法卖出")
        if position.quantity < quantity:
            raise AdjustError(
                f"卖出数量超限：{code} 当前持仓 {position.quantity} 股，"
                f"本次卖出 {quantity} 股"
            )
        pnl_result = calc_realized_pnl_with_fee(
            buy_price=position.cost_price, sell_price=price,
            quantity=quantity, fee_mode=fee_mode, fee_value=fee_value,
        )
        fee_amount = pnl_result["fee_amount"]
    else:
        raise AdjustError(f"非法操作类型：{action}")

    # ------------------------------------------------------------------
    # ②③④ 流水 + 持仓 + 现金（同一事务）
    # ------------------------------------------------------------------
    if action == "buy":
        realized_pnl = 0.0
        if position is None:
            # 新建持仓：含费成本价 = (金额 + 手续费) / 数量
            new_cost = (amount + fee_amount) / quantity
            position = Position(
                client_id=client.id, code=code, name=name, sector=sector,
                quantity=quantity, cost_price=round(new_cost, 4),
            )
            db.add(position)
        else:
            # 加仓：含费移动加权平均
            old_value = position.quantity * position.cost_price
            new_qty = position.quantity + quantity
            new_cost = (old_value + amount + fee_amount) / new_qty
            position.quantity = new_qty
            position.cost_price = round(new_cost, 4)
            if name:
                position.name = name
            if sector:
                position.sector = sector
        # 资金来源勾稽：来自可用资金 → 扣减现金（总资产不变，现金转持仓）；
        # 外部转入 → 不扣现金（总资产随之增加）
        if from_cash:
            client.available_cash = round(
                (client.available_cash or 0.0) - amount - fee_amount, 2)
    else:
        realized_pnl = pnl_result["net_pnl"]
        position.quantity -= quantity
        if position.quantity == 0:
            db.delete(position)
            db.flush()
        # 现金回补只扣卖出侧费用（买入侧费用在买入时已从现金扣除，
        # 双边口径的 fee_amount 仅用于已实现盈亏计算，避免重复扣费）
        sell_side_fee = pnl_result["sell_fee"]["total_fee"]
        client.available_cash = round(
            (client.available_cash or 0.0) + amount - sell_side_fee, 2)

    transaction = Transaction(
        client_id=client.id, code=code, name=name or (position.name if position else None),
        action=action, quantity=quantity, price=price,
        cost_price=(position.cost_price if position else None),
        fee_mode=fee_mode if fee_mode else None,
        fee_value=fee_value if fee_mode else None,
        fee_amount=fee_amount, realized_pnl=realized_pnl,
        trade_date=date_str,
    )
    db.add(transaction)

    # ------------------------------------------------------------------
    # 提交调仓事务（流水 + 持仓 + 现金）
    # ------------------------------------------------------------------
    db.commit()

    # ------------------------------------------------------------------
    # ⑤ 当日快照增量更新（非交易日自动跳过；失败不影响已提交的调仓）
    # ------------------------------------------------------------------
    try:
        portfolio = compute_portfolio(db, client)
        write_daily_snapshot(db, client, portfolio)
    except Exception as e:  # noqa: BLE001
        # 快照由后台循环每日重建兜底，此处失败仅记录
        import logging
        logging.getLogger(__name__).warning(
            "调仓后快照更新失败（client=%s, %s %s）: %s",
            client.id, action, code, e,
        )

    # 提交后重新读取最新状态返回（清仓时 position 已删除）
    db.refresh(client)
    position = db.query(Position).filter(
        Position.client_id == client.id, Position.code == code,
    ).first()
    portfolio = compute_portfolio(db, client)
    db.refresh(transaction)

    return {
        "transaction": transaction,
        "position": position,
        "available_cash": client.available_cash,
        "portfolio": portfolio,
    }
