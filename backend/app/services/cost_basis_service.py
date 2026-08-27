"""成本核算服务：支持 加权平均 / FIFO / LIFO 三种会计准则口径，
并提供买入批次建档、卖出批次匹配与费用按比例分摊能力。

设计约束（基于 701343/702350 的失败经验）：
  1) 排序稳定键必须是 (executed_at, id)，避免同秒内批次顺序漂移导致 FIFO 结果不确定。
  2) 不做"静默丢单"：匹配不到足够批次时显式抛错（而非返回 0 盈亏）。
  3) 口径定义不混淆：
     - realized_pnl (FIFO/LIFO) 指 按批次匹配出的已实现净盈亏；
     - position_avg_cost 指当前持仓的加权平均含费成本（与 adjust_service 现有口径一致）；
     - unrealized_pnl 为 浮动盈亏（按现价与所选成本法比较）。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal, Optional

from sqlalchemy.orm import Session

from ..models import Client, CostBasisLot, Position, Transaction
from .pnl_service import calc_sell_fee


CostMethod = Literal["average", "fifo", "lifo"]


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class MatchedLot:
    """卖出匹配结果单条：对应一条被消耗掉（全部或部分）的买入批次。"""
    lot_id: int
    buy_transaction_id: int
    matched_quantity: int
    # 买入侧单位成本（含费用）— 用于与卖出价计算盈亏
    unit_cost_with_fee: float
    # 按匹配数量比例分摊到本次卖出的买入手续费
    allocated_buy_fee: float


@dataclass
class SellMatchResult:
    """卖出匹配汇总结果：批次明细 + 盈亏总计（含双侧费用扣除）。"""
    matched_lots: list[MatchedLot]
    # 毛盈亏 = Σ (sell_price - unit_cost_with_fee) × matched_qty
    gross_realized: float
    # 买入侧按比例分摊手续费合计
    allocated_buy_fee_total: float
    # 卖出侧手续费（佣金+印花税+过户费）
    sell_side_breakdown: dict  # {commission, stamp_tax, transfer_fee, total_fee}
    # 净已实现盈亏 = 毛盈亏 - 买入分摊费 - 卖出手续费
    net_realized_pnl: float
    # 卖出成交额
    sell_amount: float


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def infer_market_from_code(code: str) -> Optional[str]:
    """根据 A 股代码前缀推断市场：60/68/90 → SH；00/30 → SZ；43/83/87/88/92 → BJ。

    未知前缀返回 None，由调用方自行填充。
    """
    code = (code or "").strip()
    if len(code) >= 3:
        if code[:2] in ("60", "68", "90"):
            return "SH"
        if code[:2] in ("00", "30"):
            return "SZ"
        if code[:2] in ("43", "83", "87", "88", "92"):
            return "BJ"
    return None


# ---------------------------------------------------------------------------
# 1. 买入：创建成本批次（CostBasisLot）
# ---------------------------------------------------------------------------

def create_buy_lot(db: Session, *, client: Client, code: str,
                   buy_transaction_id: int, quantity: int,
                   unit_cost_with_fee: float, buy_fee_total: float,
                   executed_at: dt.datetime) -> CostBasisLot:
    """为一笔买入建档一条成本批次（remaining = original = quantity）。

    Args:
        buy_transaction_id: 对应 Transaction.id（已写入数据库）
        unit_cost_with_fee: 单笔买入含费单位成本 = (price × qty + buy_fee) / qty
        buy_fee_total:      该买入交易实际发生的全部手续费
        executed_at:        精确时间戳，用于 FIFO/LIFO 排序；通常等于 transaction.executed_at
    """
    lot = CostBasisLot(
        client_id=client.id, code=code,
        buy_transaction_id=buy_transaction_id,
        original_quantity=quantity,
        remaining_quantity=quantity,
        unit_cost_with_fee=round(unit_cost_with_fee, 6),
        buy_fee_total=round(buy_fee_total, 4),
        executed_at=executed_at,
    )
    db.add(lot)
    db.flush()
    return lot


# ---------------------------------------------------------------------------
# 2. 卖出：FIFO / LIFO 批次匹配 + 买入侧费用按比例分摊
# ---------------------------------------------------------------------------

def _load_active_lots(db: Session, client_id: str, code: str) -> list[CostBasisLot]:
    """取出某股票所有可用批次，排序留给各方法自行处理。"""
    return db.query(CostBasisLot).filter(
        CostBasisLot.client_id == client_id,
        CostBasisLot.code == code,
        CostBasisLot.remaining_quantity > 0,
    ).all()


def match_sell_fifo_lifo(db: Session, *, client_id: str, code: str, sell_quantity: int,
                         method: Literal["fifo", "lifo"]) -> list[MatchedLot]:
    """按 FIFO 或 LIFO 匹配批次，返回所有命中的 MatchedLot 列表。

    FIFO：按 (executed_at ASC,  id ASC) 取最早批次。
    LIFO：按 (executed_at DESC, id DESC) 取最新批次。

    经验教训（701343）：必须使用 (executed_at, id) 双字段稳定排序，
    避免同秒多笔买入批次顺序不确定导致 FIFO 结果漂移。
    """
    lots = _load_active_lots(db, client_id, code)
    if not lots:
        raise ValueError(
            f"成本批次为空：客户 {client_id} 无 {code} 的未平仓买入批次，"
            f"无法匹配卖出 {sell_quantity} 股（若为历史数据迁移请先重建批次）。"
        )

    reverse = method == "lifo"
    lots_sorted = sorted(
        lots,
        key=lambda l: (l.executed_at or dt.datetime.min, l.id or 0),
        reverse=reverse,
    )

    remaining_sell = sell_quantity
    matches: list[MatchedLot] = []
    for lot in lots_sorted:
        if remaining_sell <= 0:
            break
        take_qty = min(lot.remaining_quantity, remaining_sell)
        if take_qty <= 0:
            continue

        # 买入侧费用按匹配数量比例分摊：比例 = 匹配量 / 原始量
        # 这保证"多次部分卖出"的买入手续费合计 = 原始买入手续费
        allocated_fee = (
            lot.buy_fee_total * take_qty / lot.original_quantity
            if lot.original_quantity > 0 else 0.0
        )

        matches.append(MatchedLot(
            lot_id=lot.id,
            buy_transaction_id=lot.buy_transaction_id,
            matched_quantity=take_qty,
            unit_cost_with_fee=lot.unit_cost_with_fee,
            allocated_buy_fee=round(allocated_fee, 6),
        ))
        remaining_sell -= take_qty

    if remaining_sell > 0:
        available = sum(l.remaining_quantity for l in lots)
        raise ValueError(
            f"批次数量不足：客户 {client_id} {code} 剩余成本批次数量={available}，"
            f"卖出请求 {sell_quantity} 股，缺 {remaining_sell} 股。"
        )
    return matches


def apply_match_updates(db: Session, matches: list[MatchedLot]) -> None:
    """把匹配结果回写到 CostBasisLot（扣减 remaining_quantity）。

    注意：调用方必须在事务内，调用后若失败回滚会自动恢复。
    """
    by_lot: dict[int, int] = {}
    for m in matches:
        by_lot[m.lot_id] = by_lot.get(m.lot_id, 0) + m.matched_quantity
    lots = db.query(CostBasisLot).filter(CostBasisLot.id.in_(list(by_lot.keys()))).all()
    for lot in lots:
        lot.remaining_quantity -= by_lot[lot.id]
        if lot.remaining_quantity < 0:  # pragma: no cover
            raise ValueError(f"成本批次(id={lot.id})剩余数量不足，发生并发冲突。")
    db.flush()


# ---------------------------------------------------------------------------
# 3. 卖出已实现盈亏计算（支持 3 种成本法）
# ---------------------------------------------------------------------------

def calculate_sell_realized_pnl(
    db: Session, *, client: Client, code: str,
    quantity: int, sell_price: float,
    method: CostMethod = "average",
    fee_mode: Optional[str] = None,
    fee_value: Optional[float] = None,
) -> tuple[float, dict, list[MatchedLot]]:
    """卖出已实现盈亏（净盈亏，扣买入分摊费 + 卖出手续费）。

    Returns:
        (net_realized_pnl, fee_breakdown_dict, matched_lots)
        - net_realized_pnl:  本次卖出的净已实现盈亏
        - fee_breakdown_dict: {"commission","stamp_tax","transfer_fee","total_fee","buy_alloc_fee"}
        - matched_lots:      FIFO/LIFO 返回批次匹配明细，average 返回空列表
    """
    sell_amount = quantity * sell_price
    # 卖出手续费
    if fee_mode in ("rate", "fixed"):
        # 自定义手续费：仅佣金，其它为 0（与 calc_buy_fee 语义对齐）
        if fee_mode == "rate":
            commission = max(sell_amount * (fee_value or 0.0), 0.0)
            stamp_tax = 0.0
            transfer_fee = 0.0
        else:
            commission = float(fee_value or 0.0)
            stamp_tax = 0.0
            transfer_fee = 0.0
        total_sell_fee = commission + stamp_tax + transfer_fee
    else:
        s = calc_sell_fee(sell_amount, fee_mode=None, fee_value=None)
        commission = s["commission"]
        stamp_tax = s["stamp_tax"]
        transfer_fee = s["transfer_fee"]
        total_sell_fee = s["total_fee"]

    matched: list[MatchedLot] = []
    buy_alloc_fee = 0.0
    cost_of_sold_shares = 0.0

    if method == "average":
        # 加权平均：取 position.cost_price（该值 = adjust_service 维护的含费移动加权平均）
        pos = db.query(Position).filter(
            Position.client_id == client.id, Position.code == code,
        ).first()
        if pos is None:
            raise ValueError(f"客户 {client.id} 无 {code} 持仓")
        cost_of_sold_shares = pos.cost_price * quantity
        # average 口径：买入手续费已经体现在 cost_price 中，所以不再二次扣 buy_alloc_fee
        buy_alloc_fee = 0.0
    else:
        # FIFO / LIFO
        matched = match_sell_fifo_lifo(
            db, client_id=client.id, code=code,
            sell_quantity=quantity, method=method,  # type: ignore[arg-type]
        )
        cost_of_sold_shares = sum(m.unit_cost_with_fee * m.matched_quantity for m in matched)
        buy_alloc_fee = sum(m.allocated_buy_fee for m in matched)
        apply_match_updates(db, matched)

    gross_realized = sell_amount - cost_of_sold_shares
    # 净盈亏 = 毛盈亏 - 卖出手续费
    # 注意：FIFO/LIFO 的 cost_of_sold_shares 使用 unit_cost_with_fee，已含买入侧费用
    # 因此此处不再二次扣除 buy_alloc_fee（避免重复扣减导致现金对账偏差）
    net_realized = gross_realized - total_sell_fee

    breakdown = {
        "commission": round(commission, 4),
        "stamp_tax": round(stamp_tax, 4),
        "transfer_fee": round(transfer_fee, 4),
        "total_fee": round(total_sell_fee, 4),
        "buy_alloc_fee": round(buy_alloc_fee, 4),
    }
    return round(net_realized, 4), breakdown, matched


# ---------------------------------------------------------------------------
# 4. 汇总查询（策略复盘使用）
# ---------------------------------------------------------------------------

def summarize_cost_basis(
    db: Session, client: Client, *, code: Optional[str] = None,
    method: CostMethod = "average",
) -> dict:
    """返回某客户（或指定某股票）的成本基础汇总。

    价格获取：复用 compute_portfolio 的"实时行情→日K→成本兜底"三级降级链，
    通过已计算好的 portfolio.positions[code].currentPrice 取值。
    """
    from .pnl_service import compute_portfolio

    pf = compute_portfolio(db, client)
    price_by_code = {}
    for p in pf.get("positions", []):
        price_by_code[p["code"]] = p.get("currentPrice")

    positions = db.query(Position).filter(Position.client_id == client.id)
    if code:
        positions = positions.filter(Position.code == code)
    positions = positions.all()

    rows = []
    for p in positions:
        current_price = price_by_code.get(p.code, p.cost_price) or p.cost_price
        mv = current_price * p.quantity
        # FIFO/LIFO 成本取批次剩余量 × 含费单位成本
        if method in ("fifo", "lifo"):
            lots = _load_active_lots(db, client.id, p.code)
            cost_value = sum(l.remaining_quantity * l.unit_cost_with_fee for l in lots)
            lots_count = len(lots)
        else:
            cost_value = p.cost_price * p.quantity
            lots_count = None
        unrealized = mv - cost_value
        pct = (unrealized / cost_value * 100.0) if cost_value > 0 else 0.0
        rows.append({
            "code": p.code,
            "name": p.name,
            "quantity": p.quantity,
            "current_price": round(current_price, 4),
            "market_value": round(mv, 2),
            "cost_value": round(cost_value, 2),
            "avg_cost_price": round(cost_value / p.quantity, 4) if p.quantity > 0 else p.cost_price,
            "unrealized_pnl": round(unrealized, 2),
            "unrealized_pct": round(pct, 2),
            "cost_method": method,
            "remaining_lots": lots_count,
        })
    return {
        "client_id": client.id,
        "cost_method": method,
        "positions": rows,
    }


def summarize_transactions(
    db: Session, client: Client, *, code: Optional[str] = None,
) -> dict:
    """策略复盘使用的交易流水汇总（按股票分组的买入/卖出统计）。"""
    q = db.query(Transaction).filter(Transaction.client_id == client.id)
    if code:
        q = q.filter(Transaction.code == code)
    rows = q.order_by(Transaction.executed_at.asc().nullslast(), Transaction.id.asc()).all()

    by_code: dict[str, dict] = {}
    fee_summary = {"commission": 0.0, "stamp_tax": 0.0, "transfer_fee": 0.0, "total": 0.0}
    realized_total = 0.0

    for tx in rows:
        group = by_code.setdefault(tx.code, {
            "name": tx.name,
            "buy_qty": 0, "sell_qty": 0,
            "buy_amount": 0.0, "sell_amount": 0.0,
            "realized_pnl": 0.0,
            "buy_fee": 0.0, "sell_fee": 0.0,
            "trade_count": 0,
        })
        group["trade_count"] += 1
        if tx.action == "buy":
            group["buy_qty"] += tx.quantity
            group["buy_amount"] += tx.quantity * tx.price
            group["buy_fee"] += tx.fee_amount
        elif tx.action == "sell":
            group["sell_qty"] += tx.quantity
            group["sell_amount"] += tx.quantity * tx.price
            group["sell_fee"] += tx.fee_amount
            group["realized_pnl"] += tx.realized_pnl or 0.0
            realized_total += tx.realized_pnl or 0.0
        fee_summary["commission"] += tx.fee_commission or 0.0
        fee_summary["stamp_tax"] += tx.fee_stamp_tax or 0.0
        fee_summary["transfer_fee"] += tx.fee_transfer_fee or 0.0
        fee_summary["total"] += tx.fee_amount or 0.0

    return {
        "client_id": client.id,
        "codes": by_code,
        "fee_summary": {k: round(v, 2) for k, v in fee_summary.items()},
        "total_realized_pnl": round(realized_total, 2),
        "transaction_count": len(rows),
    }
