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
import logging
from typing import Optional

from sqlalchemy.orm import Session

from ..models import Client, CostBasisLot, Position, Transaction, User
from .audit_service import build_transaction_audit_log
from .cost_basis_service import (
    CostMethod,
    apply_match_updates,
    calculate_sell_realized_pnl,
    create_buy_lot,
    infer_market_from_code,
    match_sell_fifo_lifo,
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
                   executed_at: Optional[dt.datetime] = None,
                   actor: Optional[User] = None,
                   from_cash: bool = True,
                   cost_method: CostMethod = "average",
                   skip_if_duplicate: bool = False) -> dict:
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
        executed_at: 人为指定的完整交易时间戳；None 表示使用服务器当前时间。
            用于补录历史交易。该值会同时写入 Transaction.executed_at，影响
            FIFO/LIFO 批次匹配的顺序（按 executed_at + id 稳定排序）。
        actor: 触发本次调仓的操作用户（admin/service）。用于写入审计日志，
            回填 transaction.audit_log_id；系统级操作/种子数据时传 None。
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
            fee_value=fee_value, trade_date=trade_date,
            executed_at=executed_at, actor=actor,
            from_cash=from_cash, cost_method=cost_method,
            skip_if_duplicate=skip_if_duplicate,
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
                         trade_date: Optional[str],
                         executed_at: Optional[dt.datetime],
                         actor: Optional[User],
                         from_cash: bool,
                         cost_method: CostMethod,
                         skip_if_duplicate: bool = False) -> dict:
    # 交易时间戳：优先使用调用方人为指定值；否则使用服务器当前 UTC 时间
    executed_at = executed_at if executed_at is not None else dt.datetime.utcnow()
    date_str = trade_date or executed_at.date().isoformat()
    amount = quantity * price
    market = infer_market_from_code(code)

    # ------------------------------------------------------------------
    # 0. 导入去重守卫（仅导入路径开启 skip_if_duplicate）
    #    判定：同一 client 下，code(无 code 时退化为 name) + action + quantity + price + executed_at
    #    五个字段完全一致 → 视为同一笔已存在交易，跳过写入（不创建流水/持仓/现金变动）。
    #    人工调仓不开启此开关，故不影响手工录入语义；未显式指定时间（executed_at 为服务器
    #    当前时间）时不会命中（每次 now 不同），天然避免误删。
    # ------------------------------------------------------------------
    if skip_if_duplicate and (code or name) and executed_at is not None:
        stock_filter = Transaction.code == code if code else Transaction.name == name
        existing = db.query(Transaction).filter(
            Transaction.client_id == client.id,
            stock_filter,
            Transaction.action == action,
            Transaction.quantity == quantity,
            Transaction.price == price,
            Transaction.executed_at == executed_at,
        ).first()
        if existing is not None:
            db.refresh(existing)
            return {
                "transaction": existing,
                "position": db.query(Position).filter(
                    Position.client_id == client.id, Position.code == code,
                ).first(),
                "available_cash": client.available_cash,
                "duplicate": True,
                "skipped": True,
                "cost_basis_matches": [],
                "cost_method": cost_method,
            }

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

        # 记录卖出批次明细（matched_lots 落库）：
        #   - FIFO/LIFO：calculate_sell_realized_pnl 内部已消费批次并返回 matches，直接采用；
        #   - average：历史口径不消费批次，这里额外按 FIFO 轻量记账（仅减 remaining_quantity，
        #     不改变 average 的含费移动加权盈亏口径），使 matched_lots 真实可用，
        #     支撑"精确批次撤销"与"跨批次禁止撤销"。历史数据若完全无可用批次则跳过（matched=[]）。
        if cost_method == "average":
            try:
                avg_book = match_sell_fifo_lifo(
                    db, client_id=client.id, code=code,
                    sell_quantity=quantity, method="fifo",
                )
                if avg_book:
                    apply_match_updates(db, avg_book)
                    cost_matches = avg_book
            except ValueError:
                # 老库未建档 / 批次不足：不记账，撤销时按"无批次"分支处理
                cost_matches = []
    else:
        raise AdjustError(f"非法操作类型：{action}")

    # ------------------------------------------------------------------
    # 提前计算：交易前后的成本价（用于 Transaction.cost_price / prev_cost_price）
    #   - tx_cost_price ：交易完成后的持仓成本价（用户看到的"成本价"）
    #   - prev_cost_price：交易发生前的持仓成本价（括号中的变化差值 = tx - prev）
    # 提前计算的原因：Transaction 写入在步骤②，但持仓更新在步骤④⑤，且
    #   新建持仓时原先仅在步骤④算 new_cost，导致 Transaction.cost_price 取到 None（历史 bug）。
    # ------------------------------------------------------------------
    prev_cost_price: Optional[float] = None
    tx_cost_price: Optional[float] = None

    if action == "buy":
        if position is None:
            # 新仓：无前成本，交易后成本 = 含费买入成本
            prev_cost_price = None
            tx_cost_price = (amount + fee_amount) / quantity
        else:
            # 加仓：移动加权
            prev_cost_price = position.cost_price
            old_value = position.quantity * position.cost_price
            new_qty = position.quantity + quantity
            tx_cost_price = (old_value + amount + fee_amount) / new_qty
    else:  # sell
        if position is not None:
            prev_cost_price = position.cost_price
            if quantity >= position.quantity:
                # 清仓：持仓被删，交易后成本价不存在
                tx_cost_price = None
            else:
                # 部分卖出：成本价保持不变（卖出不减摊成本）
                tx_cost_price = position.cost_price

    # ------------------------------------------------------------------
    # ② 先写 Transaction（CostBasisLot.buy_transaction_id 需要其 id）
    # ------------------------------------------------------------------
    transaction = Transaction(
        client_id=client.id, code=code,
        name=name or (position.name if position else None),
        market=market,
        action=action, quantity=quantity, price=price,
        cost_price=(round(tx_cost_price, 4) if tx_cost_price is not None else None),
        prev_cost_price=(round(prev_cost_price, 4) if prev_cost_price is not None else None),
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

    # 卖出批次匹配明细落库（用于精确撤销 + 跨批次检测）；买入/调整为空
    transaction.matched_lots = [
        {
            "lot_id": m.lot_id,
            "buy_transaction_id": m.buy_transaction_id,
            "matched_quantity": m.matched_quantity,
        }
        for m in (cost_matches or [])
    ] or None

    # ------------------------------------------------------------------
    # ②-1：写 Transaction 级审计尾链（与 Transaction 同事务 flush，不单独 commit）
    #       好处：若后续业务报错 rollback，AuditLog 同 rollback，不会留"幽灵记录"
    #       actor=None 场景（种子/系统/公司行动）允许 audit_log_id 为空（nullable FK）
    # ------------------------------------------------------------------
    audit_log = build_transaction_audit_log(
        db, actor=actor, client=client, transaction=transaction, from_cash=from_cash,
    )
    transaction.audit_log_id = audit_log.id
    db.flush()

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
            # 新建持仓：含费成本价已提前算好（= tx_cost_price）
            position = Position(
                client_id=client.id, code=code, name=name, sector=sector,
                quantity=quantity, cost_price=round(tx_cost_price, 4),
            )
            db.add(position)
        else:
            # 加仓：移动加权结果已提前算好（= tx_cost_price）
            position.quantity += quantity
            position.cost_price = round(tx_cost_price, 4)
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

    # 清仓（持仓行删除）后，主动清理该客户下失效的 client_news 关联（替代仅靠 TTL 自然清理）
    if action == "sell" and position is None:
        try:
            from . import news_service
            pruned = news_service.prune_stale_client_news(client.id, db)
            if pruned:
                logging.getLogger(__name__).info(
                    "清仓后清理失效资讯关联(client=%s, code=%s): 删除 %d 条", client.id, code, pruned)
        except Exception as e:  # noqa: BLE001
            logging.getLogger(__name__).warning(
                "清仓后资讯关联清理失败(client=%s, code=%s): %s", client.id, code, e)

    # 方案二：买入（新建/加仓）后 best-effort 填充该股票板块映射，点亮 Tier2（失败不影响主流程）
    if action == "buy":
        try:
            from . import news_service
            news_service.refresh_boards_for_codes([code], db)
        except Exception as e:  # noqa: BLE001
            logging.getLogger(__name__).warning(
                "调仓买入后板块映射刷新失败(client=%s, code=%s): %s", client.id, code, e)

    # ------------------------------------------------------------------
    # ⑦ 当日快照增量更新（非交易日自动跳过；失败不影响已提交的调仓）
    # ------------------------------------------------------------------
    try:
        portfolio = compute_portfolio(db, client)
        write_daily_snapshot(db, client, portfolio)
    except Exception as e:  # noqa: BLE001
        # 快照由后台循环每日重建兜底，此处失败仅记录
        logging.getLogger(__name__).warning(
            "调仓后快照更新失败（client=%s, %s %s）: %s",
            client.id, action, code, e,
        )

    # ------------------------------------------------------------------
    # ⑧ 买入新增/加仓后回扫存量新闻，补齐 client_news 关联（确保已入库新闻即时联动）。
    #    卖出/复盘(adjust)不改变持仓，无需处理；匹配失败不影响已提交的调仓。
    # ------------------------------------------------------------------
    if action == "buy":
        try:
            from . import news_service
            added = news_service.match_news_for_client(client.id, db)
            if added:
                logging.getLogger(__name__).info(
                    "调仓买入后联动资讯(client=%s, code=%s): 新增 %d 条关联",
                    client.id, code, added)
        except Exception as e:  # noqa: BLE001
            logging.getLogger(__name__).warning(
                "调仓买入后资讯关联匹配失败(client=%s, code=%s): %s",
                client.id, code, e)

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


class RevokeError(ValueError):
    """撤销业务校验失败（跨批次 / 无记录 / 未知类型等）。携带 status_code 供路由层映射 HTTP。"""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def reverse_last_transaction(db: Session, client: Client, code: str) -> dict:
    """撤销某持仓最近一笔操作（按 executed_at DESC, id DESC 取最新一条），精确批次反转。

    行为按被撤销交易的操作类型区分：
      - 'adjust'：仅删除该策略复盘记录（不动持仓 / 现金 / 批次）；
      - 'sell'  ：
            * 若 matched_lots 跨越多个买入批次 → 抛 RevokeError(409) 禁止撤销，
              并清晰说明原因（避免误删历史批次记录）；
            * 否则恢复持仓数量、回扣卖出时增加的现金、按 matched_lots 回补对应成本批次
              的 remaining_quantity，并删除该卖出流水（其本身即一条复盘记录）。
              仅删除"目标批次"的记录，更早批次的复盘记录完整保留。
      - 'buy'  ：退回买入占用的现金（含手续费）、删除对应成本批次、减少（或归零删除）
                 持仓行，并删除该买入流水。

    返回 dict：{'action', 'transaction_id', 'restored_quantity?'}。
    """
    tx = db.query(Transaction).filter(
        Transaction.client_id == client.id, Transaction.code == code,
    ).order_by(Transaction.executed_at.desc().nullslast(), Transaction.id.desc()).first()
    if tx is None:
        raise RevokeError(f"客户 {client.id} 的 {code} 没有可撤销的操作记录", status_code=404)

    if tx.action == "adjust":
        db.delete(tx)
        db.commit()
        return {"action": "adjust", "transaction_id": tx.id}

    if tx.action == "sell":
        matched = tx.matched_lots or []
        distinct_lots = {m.get("lot_id") for m in matched if m.get("lot_id") is not None}
        # 跨批次保护：一笔卖出若命中等多个买入批次，禁止整体撤销，避免破坏历史批次记录
        if len(distinct_lots) > 1:
            raise RevokeError(
                f"该笔卖出跨越 {len(distinct_lots)} 个买入批次"
                f"（批次 id：{sorted(distinct_lots)}），系统无法精确撤销单一批次的"
                f"持仓与成本记录。请改用手工调仓（加仓 / 减仓）完成调整后再试。",
                status_code=409,
            )

        amount = (tx.quantity or 0) * (tx.price or 0.0)
        sell_fee = tx.fee_amount or 0.0
        # 卖出时现金增加 (amount - 手续费)，撤销则回扣这部分现金
        client.available_cash = round((client.available_cash or 0.0) - (amount - sell_fee), 2)

        position = db.query(Position).filter(
            Position.client_id == client.id, Position.code == code,
        ).first()
        if position is None:
            # 卖出后已清仓：撤销卖出 = 恢复该持仓（成本价取交易时的 cost_price，缺失时回退到成交价）
            position = Position(
                client_id=client.id, code=code,
                name=tx.name, sector=None,
                quantity=tx.quantity,
                cost_price=tx.cost_price if tx.cost_price is not None else (tx.price or 0.0),
            )
            db.add(position)
        else:
            position.quantity += tx.quantity

        # 回补成本批次：有 matched_lots 明细时按记录恢复剩余量；
        # 老数据（average 无批次记账）缺明细则跳过批次回补
        if matched:
            qty_by_lot: dict[int, int] = {}
            lot_ids: list[int] = []
            for m in matched:
                lid = m.get("lot_id")
                if lid is not None:
                    lot_ids.append(lid)
                    qty_by_lot[lid] = qty_by_lot.get(lid, 0) + m.get("matched_quantity", 0)
            lots = db.query(CostBasisLot).filter(CostBasisLot.id.in_(lot_ids)).all() if lot_ids else []
            for lot in lots:
                lot.remaining_quantity = (lot.remaining_quantity or 0) + qty_by_lot.get(lot.id, 0)

        db.delete(tx)
        db.commit()
        # 方案二：撤销卖出会重建/恢复持仓，best-effort 填充该股票板块映射，点亮 Tier2
        try:
            from . import news_service
            news_service.refresh_boards_for_codes([code], db)
        except Exception as e:  # noqa: BLE001
            logging.getLogger(__name__).warning(
                "撤销卖出后板块映射刷新失败(client=%s, code=%s): %s", client.id, code, e)
        return {"action": "sell", "transaction_id": tx.id, "restored_quantity": tx.quantity}

    if tx.action == "buy":
        amount = (tx.quantity or 0) * (tx.price or 0.0)
        buy_fee = tx.fee_amount or 0.0
        # 买入占用现金（含手续费），撤销则退回
        client.available_cash = round((client.available_cash or 0.0) + amount + buy_fee, 2)
        # 删除对应的成本批次（该买入建档的物理批次）
        db.query(CostBasisLot).filter(
            CostBasisLot.buy_transaction_id == tx.id,
        ).delete(synchronize_session=False)
        # 减少 / 删除持仓行
        position = db.query(Position).filter(
            Position.client_id == client.id, Position.code == code,
        ).first()
        position_removed = False
        if position is not None:
            if position.quantity <= (tx.quantity or 0):
                position_removed = True
                db.delete(position)
            else:
                position.quantity -= tx.quantity
        db.delete(tx)
        db.commit()
        # 撤销买入导致持仓清零（持仓行删除）时，主动清理失效的 client_news 关联
        if position_removed:
            try:
                from . import news_service
                pruned = news_service.prune_stale_client_news(client.id, db)
                if pruned:
                    logging.getLogger(__name__).info(
                        "撤销买入清仓后清理失效资讯关联(client=%s, code=%s): 删除 %d 条",
                        client.id, code, pruned)
            except Exception as e:  # noqa: BLE001
                logging.getLogger(__name__).warning(
                    "撤销买入清仓后资讯关联清理失败(client=%s, code=%s): %s",
                    client.id, code, e)
        # 方案二：撤销买入后持仓若仍在（未清仓）best-effort 刷新板块映射；已清仓则不补（避免无用映射对）
        if not position_removed:
            try:
                from . import news_service
                news_service.refresh_boards_for_codes([code], db)
            except Exception as e:  # noqa: BLE001
                logging.getLogger(__name__).warning(
                    "撤销买入后板块映射刷新失败(client=%s, code=%s): %s", client.id, code, e)
        return {"action": "buy", "transaction_id": tx.id, "restored_quantity": tx.quantity}

    raise RevokeError(f"未知操作类型：{tx.action}", status_code=400)
