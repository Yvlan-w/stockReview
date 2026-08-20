"""盈亏计算引擎：实时持仓估值、每日盈亏计算、收益曲线数据生成。

核心计算公式：
- 浮动盈亏 = Σ (current_price - cost_price) × quantity（当前持仓的浮盈浮亏）
- 今日浮动盈亏 = Σ (current_price - prev_day_close) × quantity（持仓的日内波动）
- 累计盈亏 = 浮动盈亏 + 所有历史已实现盈亏（含手续费调整）
- 今日总盈亏 = 今日浮动盈亏 + 今日已实现盈亏
- 总资产 = 持仓市值 + 可用资金
- 累计收益率 = 累计盈亏 / 持仓成本 × 100%

实时价格获取降级链（positions 表不再存储现价）：
1. stock_price 实时行情表（后台 20s 刷新）
2. stock_daily_price 日K线最近收盘价
3. cost_price 成本价兜底（标记为降级数据）

手续费计算规则（A股）：
- 全局默认：买入 = 佣金(最低5元) + 过户费；卖出 = 佣金(最低5元) + 印花税 + 过户费
- 按笔覆盖（fee_mode）：
  - 'rate'  按费率：手续费 = 交易金额 × fee_value
  - 'fixed' 固定金额：手续费 = fee_value（元/笔）
- 已实现盈亏 = 卖出净收入 - 买入净成本

快照完整性：
- 快照仅写入交易日（以 market_kline 指数日历为准）
- recalculate_snapshots() 用历史K线收盘价回算任意日期区间的快照
- validate_snapshot_integrity() 校验缺失交易日 / 非交易日污染 / 连续同值
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..config import (
    TRADING_FEE_COMMISSION,
    TRADING_FEE_MIN_COMMISSION,
    TRADING_FEE_STAMP_TAX,
    TRADING_FEE_TRANSFER_FEE,
)
from ..models import (
    Client, Position, StockPrice, StockDailyPrice, PnLDailySnapshot,
    MarketKline, Transaction,
)
from .stock_price_service import get_stock_prices, get_prev_close

logger = logging.getLogger(__name__)

# 完整性告警：连续 N 天以上数值完全相同视为疑似脏数据
FLAT_SNAPSHOT_ALERT_DAYS = 3


# ---------------------------------------------------------------------------
# 手续费计算工具
# ---------------------------------------------------------------------------

def _calc_fee_by_mode(amount: float, fee_mode: Optional[str],
                      fee_value: Optional[float]) -> Optional[float]:
    """按指定模式计算手续费。返回 None 表示未指定（使用全局默认）。"""
    if fee_mode is None or fee_value is None:
        return None
    if fee_mode == "rate":
        # 费率模式：0 ~ 1%
        if not (0 <= fee_value <= 0.01):
            raise ValueError(f"费率超出范围 [0, 0.01]：{fee_value}")
        return amount * fee_value
    if fee_mode == "fixed":
        # 固定金额模式：0 ~ 10万元/笔
        if not (0 <= fee_value <= 100000):
            raise ValueError(f"固定手续费金额超出范围 [0, 100000]：{fee_value}")
        return fee_value
    raise ValueError(f"非法手续费模式：{fee_mode}（仅支持 rate / fixed）")


def calc_buy_fee(amount: float, fee_mode: Optional[str] = None,
                 fee_value: Optional[float] = None) -> dict:
    """计算买入手续费。

    Args:
        amount: 买入金额（元）
        fee_mode: 手续费模式（'rate' 按费率 / 'fixed' 固定金额 / None 全局默认）
        fee_value: 费率值或固定金额

    Returns:
        dict: 包含各项费用和净成本
    """
    override = _calc_fee_by_mode(amount, fee_mode, fee_value)
    if override is not None:
        total_fee = round(override, 2)
        return {
            "commission": total_fee, "stamp_tax": 0.0, "transfer_fee": 0.0,
            "total_fee": total_fee,
            "net_cost": round(amount + total_fee, 2),
            "fee_mode": fee_mode, "fee_value": fee_value,
        }

    commission = max(amount * TRADING_FEE_COMMISSION, TRADING_FEE_MIN_COMMISSION)
    transfer_fee = amount * TRADING_FEE_TRANSFER_FEE
    total_fee = commission + transfer_fee
    return {
        "commission": round(commission, 2),
        "stamp_tax": 0.0,  # 买入无印花税
        "transfer_fee": round(transfer_fee, 2),
        "total_fee": round(total_fee, 2),
        "net_cost": round(amount + total_fee, 2),  # 买入总成本 = 金额 + 费用
        "fee_mode": None, "fee_value": None,
    }


def calc_sell_fee(amount: float, fee_mode: Optional[str] = None,
                  fee_value: Optional[float] = None) -> dict:
    """计算卖出手续费。

    Args:
        amount: 卖出金额（元）
        fee_mode: 手续费模式（'rate' 按费率 / 'fixed' 固定金额 / None 全局默认）
        fee_value: 费率值或固定金额

    Returns:
        dict: 包含各项费用和净收入
    """
    override = _calc_fee_by_mode(amount, fee_mode, fee_value)
    if override is not None:
        total_fee = round(override, 2)
        return {
            "commission": total_fee, "stamp_tax": 0.0, "transfer_fee": 0.0,
            "total_fee": total_fee,
            "net_proceeds": round(amount - total_fee, 2),
            "fee_mode": fee_mode, "fee_value": fee_value,
        }

    commission = max(amount * TRADING_FEE_COMMISSION, TRADING_FEE_MIN_COMMISSION)
    stamp_tax = amount * TRADING_FEE_STAMP_TAX
    transfer_fee = amount * TRADING_FEE_TRANSFER_FEE
    total_fee = commission + stamp_tax + transfer_fee
    return {
        "commission": round(commission, 2),
        "stamp_tax": round(stamp_tax, 2),
        "transfer_fee": round(transfer_fee, 2),
        "total_fee": round(total_fee, 2),
        "net_proceeds": round(amount - total_fee, 2),  # 卖出净收入 = 金额 - 费用
        "fee_mode": None, "fee_value": None,
    }


def calc_realized_pnl_with_fee(buy_price: float, sell_price: float,
                               quantity: int,
                               fee_mode: Optional[str] = None,
                               fee_value: Optional[float] = None) -> dict:
    """计算含手续费的已实现盈亏。

    fee_mode/fee_value 指定时按"单边统一费率/固定金额"覆盖买卖两侧费用
    （同一笔卖出的买入成本侧不再叠加费用，费用仅从卖出侧扣除一次）。

    Args:
        buy_price: 买入价格（持仓成本价）
        sell_price: 卖出价格
        quantity: 交易数量
        fee_mode: 手续费模式（'rate' / 'fixed' / None 全局默认）
        fee_value: 费率值或固定金额

    Returns:
        dict: 包含毛盈亏、手续费、净盈亏
    """
    buy_amount = buy_price * quantity
    sell_amount = sell_price * quantity

    if fee_mode is not None and fee_value is not None:
        # 按笔覆盖：费用只从卖出侧扣一次
        sell_fee = calc_sell_fee(sell_amount, fee_mode, fee_value)
        fee_amount = sell_fee["total_fee"]
        gross_pnl = sell_amount - buy_amount
        net_pnl = sell_amount - fee_amount - buy_amount
        return {
            "gross_pnl": round(gross_pnl, 2),
            "total_fee": round(fee_amount, 2),
            "net_pnl": round(net_pnl, 2),
            "fee_amount": round(fee_amount, 2),
            "fee_mode": fee_mode,
            "fee_value": fee_value,
            "buy_fee": None,
            "sell_fee": sell_fee,
        }

    buy_fee = calc_buy_fee(buy_amount)
    sell_fee = calc_sell_fee(sell_amount)

    gross_pnl = sell_amount - buy_amount
    net_pnl = sell_fee["net_proceeds"] - buy_fee["net_cost"]
    total_fee = buy_fee["total_fee"] + sell_fee["total_fee"]

    return {
        "gross_pnl": round(gross_pnl, 2),
        "total_fee": round(total_fee, 2),
        "net_pnl": round(net_pnl, 2),
        "fee_amount": round(total_fee, 2),
        "fee_mode": None,
        "fee_value": None,
        "buy_fee": buy_fee,
        "sell_fee": sell_fee,
    }


# ---------------------------------------------------------------------------
# 实时持仓估值
# ---------------------------------------------------------------------------

def compute_portfolio(db: Session, client: Client) -> dict:
    """计算单客户的实时持仓估值与盈亏（包含已实现盈亏）。

    核心计算公式：
    - 浮动盈亏 = Σ (current_price - cost_price) × quantity
    - 今日浮动盈亏 = Σ (current_price - prev_close) × quantity
    - 累计盈亏 = 浮动盈亏 + 所有历史已实现盈亏
    - 今日总盈亏 = 今日浮动盈亏 + 今日已实现盈亏
    - 累计收益率 = 累计盈亏 / 持仓成本 × 100%

    返回结构：
    {
        "totalMarketValue": float,    # 持仓市值
        "totalCost": float,           # 持仓成本
        "totalPnl": float,            # 累计盈亏（浮动 + 所有历史已实现）
        "totalPnlPct": float,         # 累计收益率（%）
        "todayPnl": float,            # 今日总盈亏（今日浮动 + 今日已实现）
        "todayPnlPct": float,         # 今日盈亏百分比
        "todayRealizedPnl": float,    # 今日已实现盈亏（今日卖出交易）
        "todayFloatingPnl": float,    # 今日浮动盈亏（持仓日内波动）
        "totalRealizedPnl": float,    # 历史累计已实现盈亏（所有卖出交易）
        "totalAssets": float,         # 总资产（市值 + 现金）
        "availableCash": float,       # 可用资金
        "positions": [                # 每只持仓的详细数据
            {code, name, quantity, costPrice, currentPrice, prevClose,
             marketValue, pnl, pnlPct, todayPnl}
        ]
    }
    """
    positions = client.positions or []
    if not positions:
        cash = client.available_cash or 0.0
        # 即使无持仓，也计算历史已实现盈亏
        all_sell_realized = db.execute(
            select(Transaction.realized_pnl)
            .where(
                Transaction.client_id == client.id,
                Transaction.action == "sell",
            )
        ).scalars().all()
        total_realized_pnl = sum(all_sell_realized) if all_sell_realized else 0.0
        return {
            "totalMarketValue": 0.0,
            "totalCost": 0.0,
            "totalPnl": round(total_realized_pnl, 2),
            "totalPnlPct": 0.0,
            "todayPnl": 0.0,
            "todayPnlPct": 0.0,
            "todayRealizedPnl": 0.0,
            "todayFloatingPnl": 0.0,
            "totalRealizedPnl": round(total_realized_pnl, 2),
            "totalAssets": cash,
            "availableCash": cash,
            "positions": [],
        }

    # 1. 查询所有历史卖出交易的已实现盈亏（累计）
    all_sell_realized = db.execute(
        select(Transaction.realized_pnl)
        .where(
            Transaction.client_id == client.id,
            Transaction.action == "sell",
        )
    ).scalars().all()
    total_realized_pnl = sum(all_sell_realized) if all_sell_realized else 0.0

    # 2. 查询今日卖出交易的已实现盈亏
    today_str = dt.date.today().isoformat()
    today_sell_realized = db.execute(
        select(Transaction.realized_pnl)
        .where(
            Transaction.client_id == client.id,
            Transaction.action == "sell",
            Transaction.trade_date == today_str,
        )
    ).scalars().all()
    today_realized_pnl = sum(today_sell_realized) if today_sell_realized else 0.0

    # 批量获取实时价格（优先实时行情，降级到日K线）
    codes = [p.code for p in positions]
    price_map = get_stock_prices(db, codes)
    
    # 预加载日K线收盘价作为降级数据
    daily_price_map = {}
    for code in codes:
        daily_price_map[code] = get_prev_close(db, code)

    total_market_value = 0.0
    total_cost = 0.0
    total_floating_pnl = 0.0  # 浮动盈亏（当前持仓）
    today_floating_pnl = 0.0  # 今日浮动盈亏
    position_details = []

    for pos in positions:
        # 实时价格降级链：实时行情 → 日K线最近收盘 → 成本价兜底
        current_price = pos.cost_price  # 最终兜底
        prev_close = None

        # 1. 实时行情表
        sp = price_map.get(pos.code)
        if sp and sp.get("current_price"):
            current_price = sp["current_price"]
            prev_close = sp.get("prev_close")

        # 2. 日K线最近收盘价（降级）
        if (not sp or not sp.get("current_price")) and daily_price_map.get(pos.code):
            current_price = daily_price_map[pos.code] or current_price
            logger.debug("使用日K线降级价格: %s = %.2f", pos.code, current_price)

        # 如果 prev_close 仍未获取，从日 K 线查
        if prev_close is None:
            prev_close = daily_price_map.get(pos.code)

        market_value = current_price * pos.quantity
        cost_value = pos.cost_price * pos.quantity
        floating_pnl = market_value - cost_value  # 浮动盈亏
        pnl_pct = (floating_pnl / cost_value * 100) if cost_value > 0 else 0.0

        # 今日浮动盈亏（持仓日内波动）
        if prev_close and prev_close > 0:
            today_floating = (current_price - prev_close) * pos.quantity
        else:
            today_floating = 0.0

        total_market_value += market_value
        total_cost += cost_value
        total_floating_pnl += floating_pnl
        today_floating_pnl += today_floating

        position_details.append({
            "code": pos.code,
            "name": pos.name,
            "quantity": pos.quantity,
            "costPrice": pos.cost_price,
            "currentPrice": current_price,
            "prevClose": prev_close,
            "marketValue": round(market_value, 2),
            "pnl": round(floating_pnl, 2),
            "pnlPct": round(pnl_pct, 2),
            "todayPnl": round(today_floating, 2),
        })

    cash = client.available_cash or 0.0
    total_assets = total_market_value + cash
    # 累计盈亏 = 浮动盈亏（当前持仓）+ 所有历史已实现盈亏
    total_pnl = total_floating_pnl + total_realized_pnl
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0.0
    # 今日总盈亏 = 今日浮动盈亏 + 今日已实现盈亏
    today_pnl = today_floating_pnl + today_realized_pnl
    today_pnl_pct = (today_pnl / (total_assets - today_pnl) * 100) if (total_assets - today_pnl) > 0 else 0.0

    return {
        "totalMarketValue": round(total_market_value, 2),
        "totalCost": round(total_cost, 2),
        "totalPnl": round(total_pnl, 2),
        "totalPnlPct": round(total_pnl_pct, 2),
        "todayPnl": round(today_pnl, 2),
        "todayPnlPct": round(today_pnl_pct, 2),
        "todayRealizedPnl": round(today_realized_pnl, 2),
        "todayFloatingPnl": round(today_floating_pnl, 2),
        "totalRealizedPnl": round(total_realized_pnl, 2),
        "totalAssets": round(total_assets, 2),
        "availableCash": round(cash, 2),
        "positions": position_details,
    }


# ---------------------------------------------------------------------------
# 交易日历 / 每日盈亏快照
# ---------------------------------------------------------------------------

def get_trading_days(db: Session, start_date: Optional[str] = None,
                     end_date: Optional[str] = None) -> list[str]:
    """获取交易日历（以上证指数 market_kline 为准，升序返回）。

    这是快照写入与完整性校验的基准日历：非交易日不写快照。
    """
    stmt = select(MarketKline.trade_date).where(
        MarketKline.index_code == "1.000001"
    )
    if start_date:
        stmt = stmt.where(MarketKline.trade_date >= start_date)
    if end_date:
        stmt = stmt.where(MarketKline.trade_date <= end_date)
    rows = db.execute(stmt.order_by(MarketKline.trade_date.asc())).scalars().all()
    return sorted(set(rows))


def is_trading_day(db: Session, date_str: str) -> bool:
    """判断指定日期是否为交易日（以 market_kline 上证指数日历为准）。

    兜底规则（防止当日快照缺失）：若查询的是"今天"、日历中尚无今天、
    但日历最新交易日 < 今天 且今天是周一~周五，则视为交易日。
    场景：当日指数 K 线尚未拉取（刷新间隔未到/拉取失败）时，
    当日快照仍应正常写入（K 线到位后快照会被幂等更新；
    若当天实为法定节假日，recalculate_snapshots 会以日历为准清除）。
    """
    row = db.execute(
        select(MarketKline.id).where(
            MarketKline.index_code == "1.000001",
            MarketKline.trade_date == date_str,
        )
    ).scalar_one_or_none()
    if row is not None:
        return True

    # 日历中无该日期：仅对"今天"应用工作日兜底
    if date_str != dt.date.today().isoformat():
        return False
    if dt.date.fromisoformat(date_str).weekday() >= 5:  # 周六/周日
        return False
    # 日历最新交易日是否早于今天（早于才说明是 K 线延迟而非休市）
    latest = db.execute(
        select(MarketKline.trade_date)
        .where(MarketKline.index_code == "1.000001")
        .order_by(MarketKline.trade_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    return latest is not None and latest < date_str


def write_daily_snapshot(db: Session, client: Client, portfolio: dict) -> bool:
    """为客户写入当日盈亏快照（幂等：重复写入同日不产生重复记录）。

    非交易日（周末/休市，以 market_kline 日历为准）直接跳过，不产生快照，
    保证收益曲线的日期序列与真实交易日完全一致。

    盈亏计算规则：
    - 累计盈亏 = 当前持仓市值 - 持仓成本 + 所有历史已实现盈亏 = totalPnl
    - 累计收益率 = 累计盈亏 / 持仓成本 × 100%
    - 每日盈亏 = 今日市值变化 + 今日已实现盈亏 = todayPnl
    - 已实现盈亏字段记录当日的已实现盈亏，累计已实现盈亏可从交易记录表查询
    """
    today = dt.date.today().isoformat()

    # 非交易日跳过（防止周末/休市污染日期序列）
    if not is_trading_day(db, today):
        logger.info("%s 非交易日，跳过客户 %s 快照写入", today, client.id)
        return False

    # 检查是否已存在当日快照
    existing = db.execute(
        select(PnLDailySnapshot).where(
            PnLDailySnapshot.client_id == client.id,
            PnLDailySnapshot.snapshot_date == today,
        )
    ).scalar_one_or_none()

    # 获取基准指数（上证指数 1.000001）
    benchmark_row = db.execute(
        select(MarketKline)
        .where(MarketKline.index_code == "1.000001")
        .order_by(MarketKline.trade_date.desc())
        .limit(1)
    ).scalars().first()
    benchmark_value = benchmark_row.close if benchmark_row else None

    # 使用 portfolio 中的精确值（基于实时价格计算）
    total_cost = portfolio["totalCost"]
    total_pnl = portfolio["totalPnl"]  # 累计盈亏 = 浮动盈亏 + 所有历史已实现盈亏
    today_pnl = portfolio["todayPnl"]  # 今日盈亏 = 今日浮动 + 今日已实现
    today_realized = portfolio["todayRealizedPnl"]  # 今日已实现盈亏
    today_floating = portfolio["todayFloatingPnl"]  # 今日浮动盈亏

    # 累计收益率 = 累计盈亏 / 持仓成本 × 100%
    cumulative_return = (total_pnl / total_cost * 100) if total_cost > 0 else 0.0

    snapshot_data = {
        "client_id": client.id,
        "snapshot_date": today,
        "total_market_value": portfolio["totalMarketValue"],
        "total_cost": total_cost,
        "available_cash": portfolio["availableCash"],
        "total_assets": portfolio["totalAssets"],
        "daily_pnl": round(today_pnl, 2),
        "realized_pnl": today_realized,  # 当日已实现盈亏
        "floating_pnl": today_floating,  # 当日浮动盈亏
        "cumulative_pnl": round(total_pnl, 2),  # 累计盈亏（浮动+历史已实现）
        "cumulative_return_pct": round(cumulative_return, 2),
        "benchmark_value": benchmark_value,
    }

    if existing:
        # 更新已有快照
        existing.total_market_value = snapshot_data["total_market_value"]
        existing.total_cost = snapshot_data["total_cost"]
        existing.available_cash = snapshot_data["available_cash"]
        existing.total_assets = snapshot_data["total_assets"]
        existing.daily_pnl = snapshot_data["daily_pnl"]
        existing.realized_pnl = snapshot_data["realized_pnl"]
        existing.floating_pnl = snapshot_data["floating_pnl"]
        existing.cumulative_pnl = snapshot_data["cumulative_pnl"]
        existing.cumulative_return_pct = snapshot_data["cumulative_return_pct"]
        existing.benchmark_value = benchmark_value
    else:
        db.add(PnLDailySnapshot(**snapshot_data))

    db.commit()
    return True


def write_all_daily_snapshots(db: Session) -> int:
    """为所有活跃客户写入当日快照。返回处理的客户数量。"""
    clients = db.execute(
        select(Client)
    ).scalars().all()

    count = 0
    for client in clients:
        try:
            portfolio = compute_portfolio(db, client)
            if write_daily_snapshot(db, client, portfolio):
                count += 1
        except Exception as e:
            logger.warning("客户 %s 快照写入失败: %s", client.id, e)

    return count


# ---------------------------------------------------------------------------
# 快照重算（修复日期缺失 / 脏数据）与完整性校验
# ---------------------------------------------------------------------------

def _get_close_price_map(db: Session, codes: list[str]) -> dict[str, dict[str, float]]:
    """批量加载个股全部日K线收盘价。返回 {code: {trade_date: close}}。"""
    result: dict[str, dict[str, float]] = {code: {} for code in codes}
    if not codes:
        return result
    rows = db.execute(
        select(StockDailyPrice.code, StockDailyPrice.trade_date, StockDailyPrice.close)
        .where(StockDailyPrice.code.in_(codes))
    ).all()
    for code, trade_date, close in rows:
        if close is not None:
            result[code][trade_date] = close
    return result


def _get_benchmark_map(db: Session) -> dict[str, float]:
    """加载上证指数日K线收盘价。返回 {trade_date: close}。"""
    rows = db.execute(
        select(MarketKline.trade_date, MarketKline.close).where(
            MarketKline.index_code == "1.000001"
        )
    ).all()
    return {d: c for d, c in rows if c is not None}


def recalculate_snapshots(db: Session, client_id: Optional[str] = None,
                          start_date: Optional[str] = None,
                          end_date: Optional[str] = None) -> dict:
    """用历史K线收盘价重算盈亏快照（数据修复机制）。

    背景：快照若在数据缺失时被"补写"（用同一时刻价格填充多天），会出现
    日期缺失或连续多天数值完全相同的脏数据。本函数以交易日历为准，
    用 stock_daily_price 的真实收盘价逐日重算，保证：
    - 日期序列 = 完整交易日序列（缺失日补齐，非交易日清除）
    - 每日数值 = 当日真实收盘价计算结果

    计算规则（对交易日 D）：
    - market_value(D) = Σ close(D, code) × 当前持仓数量
    - floating_pnl(D) = market_value(D) - 持仓成本
    - realized_until(D) = Σ realized_pnl（trade_date <= D 的所有卖出交易）
    - realized_on(D) = Σ realized_pnl（trade_date == D 的卖出交易）
    - cumulative_pnl(D) = floating_pnl(D) + realized_until(D)
    - daily_pnl(D) = (market_value(D) - market_value(前一交易日)) + realized_on(D)
    - cumulative_return_pct(D) = cumulative_pnl(D) / 持仓成本 × 100
    - 当日（今天）有实时行情的，保留实时计算结果不覆盖

    Args:
        client_id: 指定客户；None 时重算全部客户
        start_date: 起始日期（含）；None 时取K线最早日期
        end_date: 截止日期（含）；None 时取K线最新日期

    Returns:
        {"clients": int, "days_recalculated": int, "days_deleted": int, "details": [...]}
    """
    today = dt.date.today().isoformat()

    # 交易日历
    all_trading_days = get_trading_days(db)
    if not all_trading_days:
        return {"clients": 0, "days_recalculated": 0, "days_deleted": 0,
                "details": [], "error": "交易日历为空（market_kline 无数据）"}

    lo = start_date or all_trading_days[0]
    hi = end_date or all_trading_days[-1]
    trading_days = [d for d in all_trading_days if lo <= d <= hi]
    if not trading_days:
        return {"clients": 0, "days_recalculated": 0, "days_deleted": 0,
                "details": [], "error": f"区间 [{lo}, {hi}] 内无交易日"}

    # 基准指数收盘价
    benchmark_map = _get_benchmark_map(db)

    stmt = select(Client)
    if client_id:
        stmt = stmt.where(Client.id == client_id)
    clients = db.execute(stmt).scalars().all()

    total_recalc = 0
    total_deleted = 0
    details = []

    for client in clients:
        positions = client.positions or []
        codes = list({p.code for p in positions})
        close_map = _get_close_price_map(db, codes)
        total_cost = sum(p.cost_price * p.quantity for p in positions)
        cash = client.available_cash or 0.0

        # 交易记录：按日期聚合已实现盈亏
        tx_rows = db.execute(
            select(Transaction.trade_date, Transaction.realized_pnl).where(
                Transaction.client_id == client.id,
                Transaction.action == "sell",
            )
        ).all()
        realized_by_date: dict[str, float] = {}
        for d, pnl in tx_rows:
            realized_by_date[d] = realized_by_date.get(d, 0.0) + (pnl or 0.0)

        # 前一交易日（区间首日的前一个交易日，用于计算首日 daily_pnl）
        lo_idx = all_trading_days.index(trading_days[0])
        prev_days = ([all_trading_days[lo_idx - 1]] if lo_idx > 0 else [])
        calc_days = prev_days + trading_days

        # 逐日市值
        mv_by_date: dict[str, Optional[float]] = {}
        for d in calc_days:
            mv = 0.0
            ok = True
            for p in positions:
                close = close_map.get(p.code, {}).get(d)
                if close is None:
                    ok = False
                    break
                mv += close * p.quantity
            mv_by_date[d] = mv if ok else None

        # 删除该客户区间内的旧快照（含非交易日脏数据），今日实时快照除外
        old_rows = db.execute(
            select(PnLDailySnapshot).where(
                PnLDailySnapshot.client_id == client.id,
                PnLDailySnapshot.snapshot_date >= lo,
                PnLDailySnapshot.snapshot_date <= hi,
            )
        ).scalars().all()
        for row in old_rows:
            if row.snapshot_date == today:
                continue  # 今日快照由实时行情写入，永不覆盖
            db.delete(row)
            total_deleted += 1
        # 强制先落库删除，避免同事务中 INSERT 先于 DELETE 触发唯一约束冲突
        db.flush()

        # 重建快照
        days_done = 0
        prev_d = None
        for d in trading_days:
            if d == today:
                continue  # 今日快照由实时行情写入，永不覆盖
            mv = mv_by_date.get(d)
            if mv is None:
                logger.warning("重算跳过 %s %s：部分持仓缺少当日K线", client.id, d)
                continue

            floating_pnl = mv - total_cost
            realized_until = sum(v for dd, v in realized_by_date.items() if dd <= d)
            realized_on = realized_by_date.get(d, 0.0)
            cumulative_pnl = floating_pnl + realized_until

            prev_mv = mv_by_date.get(prev_d) if prev_d else None
            mv_change = (mv - prev_mv) if prev_mv is not None else 0.0
            daily_pnl = mv_change + realized_on

            cumulative_return = (cumulative_pnl / total_cost * 100) if total_cost > 0 else 0.0

            db.add(PnLDailySnapshot(
                client_id=client.id,
                snapshot_date=d,
                total_market_value=round(mv, 2),
                total_cost=total_cost,
                available_cash=cash,
                total_assets=round(mv + cash, 2),
                daily_pnl=round(daily_pnl, 2),
                realized_pnl=round(realized_on, 2),
                floating_pnl=round(floating_pnl, 2),
                cumulative_pnl=round(cumulative_pnl, 2),
                cumulative_return_pct=round(cumulative_return, 2),
                benchmark_value=benchmark_map.get(d),
            ))
            days_done += 1
            prev_d = d

        total_recalc += days_done
        details.append({"client_id": client.id, "days": days_done})

    db.commit()
    logger.info("快照重算完成：%d 客户，重算 %d 天，删除 %d 条旧快照",
                len(clients), total_recalc, total_deleted)
    return {
        "clients": len(clients),
        "days_recalculated": total_recalc,
        "days_deleted": total_deleted,
        "range": {"start": lo, "end": hi, "trading_days": len(trading_days)},
        "details": details,
    }


def validate_snapshot_integrity(db: Session, client_id: Optional[str] = None) -> dict:
    """校验快照数据完整性，输出告警（不修改数据）。

    检查项：
    1. missing_days      快照区间内缺失的交易日
    2. non_trading_days  非交易日存在快照（周末/休市污染）
    3. flat_segments     连续 N 天 cumulative_pnl/daily_pnl 完全相同（疑似补写脏数据）
    4. null_values       关键数值为 NULL 的快照

    Returns:
        {"ok": bool, "clients": [{client_id, issues: [...]}], "summary": str}
    """
    stmt = select(Client.id)
    if client_id:
        stmt = stmt.where(Client.id == client_id)
    client_ids = db.execute(stmt).scalars().all()

    all_trading_days = get_trading_days(db)
    trading_set = set(all_trading_days)
    results = []
    has_issue = False

    for cid in client_ids:
        snaps = db.execute(
            select(PnLDailySnapshot).where(PnLDailySnapshot.client_id == cid)
            .order_by(PnLDailySnapshot.snapshot_date.asc())
        ).scalars().all()
        issues = []

        if not snaps:
            issues.append({"type": "no_snapshots", "detail": "无任何快照记录"})
        else:
            dates = [s.snapshot_date for s in snaps]

            # 1. 缺失交易日（快照首尾区间内）
            lo, hi = dates[0], dates[-1]
            expected = [d for d in all_trading_days if lo <= d <= hi]
            missing = [d for d in expected if d not in set(dates)]
            if missing:
                issues.append({
                    "type": "missing_days",
                    "detail": f"缺失 {len(missing)} 个交易日快照",
                    "dates": missing[:20],
                })

            # 2. 非交易日快照（今日例外：当日 K 线可能尚未入库）
            today_str = dt.date.today().isoformat()
            non_trading = [d for d in dates
                           if d not in trading_set and d != today_str]
            if non_trading:
                issues.append({
                    "type": "non_trading_days",
                    "detail": f"存在 {len(non_trading)} 个非交易日快照（周末/休市污染）",
                    "dates": non_trading[:20],
                })

            # 3. 连续同值（疑似同一时刻价格补写的脏数据）
            #    空仓客户（全 0）不视为脏数据
            flat_runs = []
            all_zero = all((s.cumulative_pnl or 0) == 0 for s in snaps)
            i = 0
            while i < len(snaps):
                j = i
                while (j + 1 < len(snaps)
                       and snaps[j + 1].cumulative_pnl == snaps[i].cumulative_pnl
                       and snaps[j + 1].daily_pnl == snaps[i].daily_pnl):
                    j += 1
                if j - i + 1 >= FLAT_SNAPSHOT_ALERT_DAYS and not all_zero:
                    flat_runs.append({
                        "start": snaps[i].snapshot_date,
                        "end": snaps[j].snapshot_date,
                        "days": j - i + 1,
                    })
                i = j + 1
            if flat_runs:
                issues.append({
                    "type": "flat_segments",
                    "detail": f"存在 {len(flat_runs)} 段连续相同数值（疑似脏数据，建议重算）",
                    "segments": flat_runs[:10],
                })

            # 4. NULL 数值
            null_dates = [s.snapshot_date for s in snaps if s.cumulative_pnl is None]
            if null_dates:
                issues.append({"type": "null_values", "dates": null_dates[:20],
                               "detail": f"{len(null_dates)} 条快照累计盈亏为 NULL"})

        if issues:
            has_issue = True
            for issue in issues:
                logger.warning("[快照完整性告警] 客户 %s：%s（%s）",
                               cid, issue["type"], issue["detail"])
        results.append({"client_id": cid, "issues": issues})

    return {
        "ok": not has_issue,
        "clients": results,
        "summary": ("快照数据完整" if not has_issue else "发现快照完整性问题，建议调用重算接口修复"),
        "checked_at": dt.datetime.now().isoformat(),
    }


# ---------------------------------------------------------------------------
# 收益曲线数据
# ---------------------------------------------------------------------------

def get_pnl_history(db: Session, client_id: str, range_days: int = 30) -> dict:
    """获取客户最近 N 天的盈亏历史（用于收益曲线图）。

    返回：
    {
        "dates": ["2026-08-01", "2026-08-02", ...],
        "pnl": [1.23, 2.34, ...],            # 累计盈亏（万元）
        "dailyPnl": [0.50, -0.30, ...],      # 每日盈亏（万元）
        "benchmark": [3200, 3210, ...],       # 基准指数点位
        "benchmarkReturn": [0.5, 0.8, ...],   # 基准累计收益率（%）
    }
    """
    snapshots = db.execute(
        select(PnLDailySnapshot)
        .where(PnLDailySnapshot.client_id == client_id)
        .order_by(PnLDailySnapshot.snapshot_date.desc())
        .limit(range_days)
    ).scalars().all()

    if not snapshots:
        return {
            "dates": [],
            "pnl": [],
            "cumulativeReturnPct": [],
            "dailyPnl": [],
            "benchmark": [],
            "benchmarkReturn": [],
        }

    # 按日期升序排列；防御性过滤非交易日快照（历史遗留脏数据），
    # 保证图表横坐标为连续交易日序列。
    # 今日快照例外：当日 K 线可能尚未入库（刷新延迟），不应被过滤。
    today_str = dt.date.today().isoformat()
    trading_set = set(get_trading_days(db))
    snapshots = sorted(
        (s for s in snapshots
         if s.snapshot_date == today_str
         or not trading_set
         or s.snapshot_date in trading_set),
        key=lambda s: s.snapshot_date,
    )

    dates = [s.snapshot_date for s in snapshots]
    pnl = [round(s.cumulative_pnl / 10000, 2) for s in snapshots]
    daily_pnl = [round(s.daily_pnl / 10000, 2) for s in snapshots]
    benchmark = [s.benchmark_value for s in snapshots]
    cumulative_return = [s.cumulative_return_pct for s in snapshots]

    # 基准收益率（相对第一天）
    benchmark_return = []
    first_benchmark = snapshots[0].benchmark_value
    for s in snapshots:
        if first_benchmark and first_benchmark > 0 and s.benchmark_value:
            br = round((s.benchmark_value - first_benchmark) / first_benchmark * 100, 2)
        else:
            br = None
        benchmark_return.append(br)

    return {
        "dates": dates,
        "pnl": pnl,
        "cumulativeReturnPct": cumulative_return,
        "dailyPnl": daily_pnl,
        "benchmark": benchmark,
        "benchmarkReturn": benchmark_return,
    }
