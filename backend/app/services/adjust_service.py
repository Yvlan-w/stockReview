"""调仓执行服务：流水 + 持仓 + 现金 + 快照 + 成本批次 的单事务原子更新。

背景：此前调仓是"写流水"与"整体替换持仓"两个独立接口，中途失败会产生
流水与持仓不一致（如 C001 000001 流水有 1000 股、持仓无记录的问题）。
本服务将调仓收敛为一个数据库事务，任一步骤失败全部回滚。

事务内步骤：
    ① 参数与业务校验（持仓存在性、数量足够、现金充足、手续费合法）
    ② 计算手续费全量明细（佣金/印花税/过户费/合计），计算 executed_at + market
    ③ 写入 transaction 流水（含手续费明细、时间戳、市场）
    ④ 买入：创建成本批次 CostBasisLot；卖出：根据 cost_method 匹配批次（默认平均法保持历史兼容）
    ⑤ 更新 positions（买入新建/加权加仓；卖出减仓/清仓删行）
    ⑥ 勾稽 clients.available_cash（买入扣减、卖出回补）
    ⑦ 触发当日快照增量更新（非交易日自动跳过）
"""
import datetime as dt
from typing import Optional

from sqlalchemy.orm import Session

from ..models import Client, Position, Transaction
from .cost_basis_service import (
    CostMethod,
    calculate_sell_realized_pnl,
    create_buy_lot,
    infer_market_from_code,
)
from .pnl_service import (
    calc_buy_fee,
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
                   from_cash: bool = True,
                   cost_method: CostMethod = "average") -> dict:
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
        cost_method: 卖出时的成本基础核算方法。average / fifo / lifo。
            average 保持 adjust_service 历史口径（含费移动加权）；fifo / lifo
            会同时消费 CostBasisLot 批次并按比例分摊买入手续费。

    Returns:
        {transaction, position(清仓为 None), available_cash, portfolio, cost_basis_matches}

    Raises:
        AdjustError: 校验失败（路由层转 422）。任何异常时事务整体回滚。
    """
    try:
        return _execute_adjust_impl(
            db, client, code=code, action=action, quantity=quantity,
            price=price, name=name, sector=sector, fee_mode=fee_mode,
            fee_value=fee_value, trade_date=trade_date, from_cash=from_cash,
            cost_method=cost_method,
        )
    except Exception:
        db.rollback()
        raise


def _unpack_fee(fee_result: dict, action: str) -> tuple[float, float, float, float]:
    """从 calc_buy_fee / calc_sell_fee / calculate_sell_realized_pnl 的字典
    返回 (commission, stamp_tax, transfer_fee, total)。兼容 custom 模式。"""
    comm = float(fee_result.get("commission") or 0.0)
    stamp = float(fee_result.get("stamp_tax") or 0.0)
    trans = float(fee_result.get("transfer_fee") or 0.0)
    total = float(fee_result.get("total_fee") or (comm + stamp + trans))
    return comm, stamp, trans, total


def _execute_adjust_impl(db: Session, client: Client, *, code: str, action: str,
                         quantity: int, price: float,
                         name: Optional[str], sector: Optional[str],
                         fee_mode: Optional[str], fee_value: Optional[float],
                         trade_date: Optional[str], from_cash: bool,
                         cost_method: CostMethod) -> dict:
    # 交易时间戳：使用系统时间；trade_date 仅日期
    executed_at = dt.datetime.utcnow()
    date_str = trade_date or executed_at.date().isoformat()
    amount = quantity * price
    market = infer_market_from_code(code)

    position = db.query(Position).filter(
        Position.client_id == client.id, Position.code == code,
    ).first()

    # 成本批次匹配结果（卖出 FIFO/LIFO 时才有）
    cost_matches = []
    sell_fee_breakdown = None

    # ------------------------------------------------------------------
    # ① 业务校验 + 费用计算
    # ------------------------------------------------------------------
    if action == "buy":
        if position is None and (not name or not sector):
            raise AdjustError("新建持仓必须提供股票名称与板块")
        fee_r = calc_buy_fee(amount, fee_mode, fee_value)
        comm, stamp, trans_fee, fee_amount = _unpack_fee(fee_r, "buy")
        # 现金充足性：仅"来自可用资金"的买入需要校验（外部转入不占用账户现金）
        if from_cash and (client.available_cash or 0.0) < amount + fee_amount:
            raise AdjustError(
                f"可用资金不足：需 {amount + fee_amount:.2f} 元"
                f"（含手续费 {fee_amount:.2f}），"
                f"当前仅 {client.available_cash or 0.0:.2f} 元"
            )
        realized_pnl = 0.0
    elif action == "sell":
        if position is None:
            raise AdjustError(f"客户 {client.id} 无 {code} 持仓，无法卖出")
        if position.quantity < quantity:
            raise AdjustError(
                f"卖出数量超限：{code} 当前持仓 {position.quantity} 股，"
                f"本次卖出 {quantity} 股"
            )
        # 调用成本核算服务：返回净盈亏 + 双侧手续费明细（卖出侧 breakdown + 买入分摊费）
        net_rlz, s_breakdown, matches = calculate_sell_realized_pnl(
            db, client=client, code=code, quantity=quantity, sell_price=price,
            method=cost_method, fee_mode=fee_mode, fee_value=fee_value,
        )
        cost_matches = matches
        sell_fee_breakdown = s_breakdown
        comm = s_breakdown["commission"]
        stamp = s_breakdown["stamp_tax"]
        trans_fee = s_breakdown["transfer_fee"]
        fee_amount = s_breakdown["total_fee"]
        realized_pnl = net_rlz
    else:
        raise AdjustError(f"非法操作类型：{action}")

    # ------------------------------------------------------------------
    # ② 先写 Transaction（CostBasisLot.buy_transaction_id 需要其 id）
    # ------------------------------------------------------------------
    transaction = Transaction(
        client_id=client.id, code=code,
        name=name or (position.name if position else None),
        market=market,
        action=action, quantity=quantity, price=price,
        cost_price=(position.cost_price if position else None),
        fee_mode=fee_mode if fee_mode else None,
        fee_value=fee_value if fee_mode else None,
        fee_amount=round(fee_amount, 4),
        fee_commission=round(comm, 4),
        fee_stamp_tax=round(stamp, 4),
        fee_transfer_fee=round(trans_fee, 4),
        realized_pnl=realized_pnl,
        trade_date=date_str,
        executed_at=executed_at,
    )
    db.add(transaction)
    db.flush()  # 取 id

    # ------------------------------------------------------------------
    # ③ 买入：创建成本批次；卖出：批次已在 calculate_sell_realized_pnl 内消费
    # ------------------------------------------------------------------
    if action == "buy":
        # 单笔买入的含费单位成本 = (price × qty + total_fee) / qty
        unit_cost = (amount + fee_amount) / quantity if quantity > 0 else price
        create_buy_lot(
            db, client=client, code=code,
            buy_transaction_id=transaction.id,
            quantity=quantity,
            unit_cost_with_fee=unit_cost,
            buy_fee_total=fee_amount,
            executed_at=executed_at,
        )

    # ------------------------------------------------------------------
    # ④⑤ 持仓 + 现金
    # ------------------------------------------------------------------
    if action == "buy":
        if position is None:
            # 新建持仓：含费成本价 = (金额 + 手续费) / 数量（平均法口径）
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
        # 资金来源勾稽：来自可用资金 → 扣减现金
        if from_cash:
            client.available_cash = round(
                (client.available_cash or 0.0) - amount - fee_amount, 2)
    else:
        # 卖出：调整持仓
        position.quantity -= quantity
        if position.quantity == 0:
            db.delete(position)
            db.flush()
        # 卖出侧费用：用 total_sell_fee（不含买入分摊，因为买入侧在买入时已扣现金）
        sell_side_fee = (sell_fee_breakdown or {}).get("total_fee") or fee_amount
        client.available_cash = round(
            (client.available_cash or 0.0) + amount - sell_side_fee, 2)

    # ------------------------------------------------------------------
    # 提交调仓事务（流水 + 成本批次 + 持仓 + 现金）
    # ------------------------------------------------------------------
    db.commit()

    # ------------------------------------------------------------------
    # ⑦ 当日快照增量更新（非交易日自动跳过；失败不影响已提交的调仓）
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
        "cost_basis_matches": [
            {
                "lot_id": m.lot_id,
                "buy_transaction_id": m.buy_transaction_id,
                "matched_quantity": m.matched_quantity,
                "unit_cost_with_fee": m.unit_cost_with_fee,
                "allocated_buy_fee": m.allocated_buy_fee,
            }
            for m in cost_matches
        ],
        "cost_method": cost_method,
    }
