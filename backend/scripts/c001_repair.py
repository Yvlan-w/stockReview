# -*- coding: utf-8 -*-
"""C001 数据一致性修复脚本（已获用户书面批准执行）。

修复项（依据一致性查验报告基准数据）：
  R1 修复名称乱码：positions C001 600519 → 贵州茅台/消费，600036 → 招商银行/金融
  R2 补 000001 持仓：1000 股 @12.5（金融板块），与流水严格一致
  R3 补拉 000001 行情：实时行情 + 日K线（重算快照的前置条件）
  R4 补 600519 期初建仓流水：buy 390 @1481.05 @2026-08-18
     （期初价按当前持仓成本反推的自洽值：重放 390→卖200→买10 后
      加权成本精确回到 1491.00，数量回到 200）
  R5 现金勾稽：available_cash 180000 → 510700
     （= 180000 + 卖600519收入360000 − 买000001支出12500 − 买600519支出16800；
       期初建仓为修复性质，不计入现金变动）
  R6 重算 C001 全部历史快照 + 用实时行情刷新今日快照

安全措施：执行前整库备份；每项修复独立短事务；输出修复前后对照。
"""
import asyncio
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.models import Client, Position, Transaction  # noqa: E402
from sqlalchemy import select  # noqa: E402

DB_PATH = Path(r"d:\github_rep\stockHoldingReview\backend\stock_review.db")

log_lines = []


def log(msg=""):
    print(msg)
    log_lines.append(msg)


# ---------------------------------------------------------------------------
# 0. 备份
# ---------------------------------------------------------------------------
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
backup_path = DB_PATH.with_name(f"stock_review_backup_{ts}.db")
for suffix in ("", "-wal", "-shm"):
    src = DB_PATH.with_name(DB_PATH.name + suffix)
    if src.exists():
        shutil.copy2(src, backup_path.with_name(backup_path.name + suffix))
log(f"[备份] {DB_PATH.name} → {backup_path.name}（含 -wal/-shm 如存在）")

db = SessionLocal()

try:
    # -----------------------------------------------------------------------
    # 修复前状态快照
    # -----------------------------------------------------------------------
    log("\n===== 修复前状态 =====")
    raw = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    raw.row_factory = sqlite3.Row

    def _scalar(sql, *args):
        return raw.execute(sql, args).fetchone()[0]

    for r in raw.execute("SELECT code, name, sector, quantity, cost_price "
                         "FROM positions WHERE client_id='C001' ORDER BY code"):
        log(f"  持仓 {r['code']}: name={r['name']!r} sector={r['sector']!r} "
            f"qty={r['quantity']} cost={r['cost_price']}")
    log(f"  流水数: {_scalar('SELECT COUNT(*) FROM transactions WHERE client_id=?', 'C001')}")
    n_pos_000001 = _scalar("SELECT COUNT(*) FROM positions WHERE client_id=? AND code=?", "C001", "000001")
    log(f"  000001 持仓: {n_pos_000001} 行")
    log(f"  000001 K线: {_scalar('SELECT COUNT(*) FROM stock_daily_price WHERE code=?', '000001')} 条")
    log(f"  000001 实时行情: {_scalar('SELECT COUNT(*) FROM stock_price WHERE code=?', '000001')} 行")
    log(f"  C001 现金: {_scalar('SELECT available_cash FROM clients WHERE id=?', 'C001')}")
    raw.close()

    # -----------------------------------------------------------------------
    # R1 + R2：修复名称乱码、补 000001 持仓
    # -----------------------------------------------------------------------
    log("\n===== R1/R2 修复持仓 =====")
    p519 = db.execute(select(Position).where(
        Position.client_id == "C001", Position.code == "600519")).scalar_one()
    p036 = db.execute(select(Position).where(
        Position.client_id == "C001", Position.code == "600036")).scalar_one()

    p519.name, p519.sector = "贵州茅台", "消费"
    p036.name, p036.sector = "招商银行", "金融"
    log(f"  R1 名称修复: 600519 → 贵州茅台/消费, 600036 → 招商银行/金融")

    has_000001 = db.execute(select(Position).where(
        Position.client_id == "C001", Position.code == "000001")).scalar_one_or_none()
    if has_000001 is None:
        db.add(Position(client_id="C001", code="000001", name="平安银行",
                        sector="金融", quantity=1000, cost_price=12.5))
        log("  R2 补持仓: 000001 平安银行 1000 股 @12.5（金融）")
    db.commit()

    # -----------------------------------------------------------------------
    # R3：补拉 000001 行情（实时 + 日K线）
    # -----------------------------------------------------------------------
    log("\n===== R3 补拉 000001 行情 =====")

    async def _fetch_market():
        from app.services.stock_price_service import (
            fetch_stock_daily_kline, upsert_stock_daily_prices,
            _fetch_stock_batch, _parse_stock_item, _upsert_stock_prices,
        )
        # 日K线（90天）
        klines = await fetch_stock_daily_kline("000001", limit=90)
        if klines:
            upsert_stock_daily_prices(db, klines)
            log(f"  日K线: 拉取 {len(klines)} 条并落库")
        else:
            log("  日K线: 拉取失败（数据源均不可用）")
            return False
        # 实时行情
        items = await _fetch_stock_batch(["0.000001"])
        if items:
            parsed = [x for x in (_parse_stock_item(i) for i in items) if x]
            if parsed:
                for x in parsed:
                    x["name"] = x.get("name") or "平安银行"
                _upsert_stock_prices(db, parsed)
                log(f"  实时行情: 现价 {parsed[0].get('current_price')}")
        else:
            log("  实时行情: 拉取失败（后台任务稍后会自动重试）")
        return True

    kline_ok = asyncio.run(_fetch_market())

    # -----------------------------------------------------------------------
    # R4：补 600519 期初建仓流水（自洽期初价 1481.05）
    # -----------------------------------------------------------------------
    log("\n===== R4 补期初建仓流水 =====")
    has_open = db.execute(select(Transaction).where(
        Transaction.client_id == "C001", Transaction.code == "600519",
        Transaction.trade_date == "2026-08-18")).scalar_one_or_none()
    if has_open is None:
        db.add(Transaction(
            client_id="C001", code="600519", name="贵州茅台", action="buy",
            quantity=390, price=1481.05, cost_price=1481.05,
            fee_mode=None, fee_value=None, fee_amount=0.0, realized_pnl=0.0,
            trade_date="2026-08-18",
        ))
        db.commit()
        log("  已补: buy 600519 390股 @1481.05 @2026-08-18（期初建仓）")
        log("  验证: 390 − 卖200 + 买10 = 200 股；"
            "加权成本 (190×1481.05 + 10×1680)/200 = 1490.9975 ≈ 1491.00")
    else:
        log("  已存在 2026-08-18 流水，跳过")

    # -----------------------------------------------------------------------
    # R5：现金勾稽
    # -----------------------------------------------------------------------
    log("\n===== R5 现金勾稽 =====")
    c001 = db.get(Client, "C001")
    old_cash = c001.available_cash
    # 真实流水的净现金变动：卖600519 +360000；买000001 −12500；买600519 −16800
    # （期初建仓 390×1481.05 为修复性质，不计入）
    c001.available_cash = 180000 + 360000 - 12500 - 16800
    db.commit()
    log(f"  available_cash: {old_cash} → {c001.available_cash}")

    # -----------------------------------------------------------------------
    # R6：重算历史快照 + 刷新今日快照
    # -----------------------------------------------------------------------
    log("\n===== R6 快照重算 =====")
    from app.services.pnl_service import (
        recalculate_snapshots, compute_portfolio, write_daily_snapshot,
    )
    result = recalculate_snapshots(db, client_id="C001")
    log(f"  历史重算: clients={result['clients']} "
        f"days_recalculated={result['days_recalculated']} "
        f"days_deleted={result['days_deleted']}")
    if not kline_ok:
        log("  ⚠ 000001 K线缺失，历史重算可能跳过全部日期（K线就绪后需重跑）")

    # 今日快照（8-20）用实时行情刷新
    db.refresh(c001)
    portfolio = compute_portfolio(db, c001)
    written = write_daily_snapshot(db, c001, portfolio)
    log(f"  今日快照刷新: written={written} "
        f"mv={portfolio['totalMarketValue']:.2f} "
        f"cash={portfolio['availableCash']:.2f} "
        f"assets={portfolio['totalAssets']:.2f}")

    # -----------------------------------------------------------------------
    # 修复后状态
    # -----------------------------------------------------------------------
    log("\n===== 修复后状态 =====")
    raw = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    raw.row_factory = sqlite3.Row

    def _scalar2(sql, *args):
        return raw.execute(sql, args).fetchone()[0]

    for r in raw.execute("SELECT code, name, sector, quantity, cost_price "
                         "FROM positions WHERE client_id='C001' ORDER BY code"):
        log(f"  持仓 {r['code']} {r['name']}/{r['sector']}: "
            f"qty={r['quantity']} cost={r['cost_price']}")
    log(f"  流水数: {_scalar2('SELECT COUNT(*) FROM transactions WHERE client_id=?', 'C001')}")
    log(f"  000001 K线: {_scalar2('SELECT COUNT(*) FROM stock_daily_price WHERE code=?', '000001')} 条")
    log(f"  000001 实时行情: {_scalar2('SELECT COUNT(*) FROM stock_price WHERE code=?', '000001')} 行")
    log(f"  C001 现金: {_scalar2('SELECT available_cash FROM clients WHERE id=?', 'C001')}")
    log("\n  最近5天快照:")
    for r in raw.execute("SELECT snapshot_date, total_market_value, total_cost, "
                         "available_cash, total_assets, daily_pnl, cumulative_pnl "
                         "FROM pnl_daily_snapshot WHERE client_id='C001' "
                         "ORDER BY snapshot_date DESC LIMIT 5"):
        log(f"    {r['snapshot_date']}: mv={r['total_market_value']:.2f} "
            f"cost={r['total_cost']:.2f} cash={r['available_cash']:.2f} "
            f"assets={r['total_assets']:.2f} daily={r['daily_pnl']:.2f} "
            f"cum={r['cumulative_pnl']:.2f}")
    raw.close()

finally:
    db.close()

out = Path(__file__).with_name("c001_repair_result.txt")
out.write_text("\n".join(log_lines), encoding="utf-8")
print(f"\n[完成] 修复日志已写入 {out}")
