"""重算「当日」盈亏快照（修复前已落库的当日快照修正）。

背景：
  修复「今日盈亏」口径（当日新建/加仓持仓改用成本价基准）后，已经按旧口径写入的
  「当日」快照 daily_pnl 仍是高估值。recalculate_snapshots() 刻意跳过今日
  （今日快照由实时行情写入、永不覆盖），因此这里直接调用 write_daily_snapshot
  对今日做 upsert，用修复后的 compute_portfolio 实时重算。

用法（backend/ 目录下）：
  python scripts/recalc_today_snapshot.py
"""
import os
import sys

# 让脚本能 import app 包（backend/ 作为根）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, text  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import Client, PnLDailySnapshot  # noqa: E402
from app.services.pnl_service import compute_portfolio, write_daily_snapshot  # noqa: E402


def _ensure_opened_date_column(engine):
    """幂等确保 positions.opened_date 列存在（与 main._migrate_sqlite_columns 等价）。"""
    with engine.connect() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(positions)"))]
        if "opened_date" not in cols:
            conn.execute(text("ALTER TABLE positions ADD COLUMN opened_date VARCHAR(10)"))
            conn.commit()
            print("  · 已补充 positions.opened_date 列")


def main():
    db = SessionLocal()
    try:
        _ensure_opened_date_column(db.get_bind())
        clients = db.execute(select(Client)).scalars().all()
        print(f"共 {len(clients)} 个客户，开始重算当日快照...")
        done = 0
        for client in clients:
            portfolio = compute_portfolio(db, client)
            ok = write_daily_snapshot(db, client, portfolio)
            if ok:
                done += 1
                print(f"  ✓ {client.id}  今日盈亏={portfolio.get('todayPnl')}  "
                      f"浮动={portfolio.get('totalFloatingPnl')} 总资产={portfolio.get('totalAssets')}")
            else:
                print(f"  · {client.id}  跳过（非交易日，不写快照）")
        print(f"完成：重算 {done} 个客户的当日快照")
    finally:
        db.close()


if __name__ == "__main__":
    main()
