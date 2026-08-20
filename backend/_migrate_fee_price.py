"""
数据库迁移（2026-08）：
1. transactions 表新增 fee_mode / fee_value / fee_amount 三列（手续费双模式支持）
2. positions 表移除 price 列（现价改由实时行情获取，SQLite 需重建表）
3. 清除非交易日快照脏数据 + 重算全部盈亏快照（修复 8.17/8.18 日期缺失）

用法：
    cd backend
    python _migrate_fee_price.py
"""
import sys
sys.path.insert(0, '.')

from app.database import engine, SessionLocal
from sqlalchemy import bindparam, text


def migrate_transactions(conn) -> None:
    """transactions 表新增手续费三列。"""
    cols = [r[1] for r in conn.execute(text('PRAGMA table_info(transactions)')).all()]
    print(f"transactions 现有列: {cols}")

    added = []
    if "fee_mode" not in cols:
        conn.execute(text("ALTER TABLE transactions ADD COLUMN fee_mode VARCHAR(8)"))
        added.append("fee_mode")
    if "fee_value" not in cols:
        conn.execute(text("ALTER TABLE transactions ADD COLUMN fee_value FLOAT"))
        added.append("fee_value")
    if "fee_amount" not in cols:
        conn.execute(text(
            "ALTER TABLE transactions ADD COLUMN fee_amount FLOAT NOT NULL DEFAULT 0.0"))
        added.append("fee_amount")

    if added:
        print(f"transactions 新增列: {added}")
    else:
        print("transactions 手续费列已存在，跳过")


def migrate_positions(conn) -> None:
    """positions 表移除 price 列（SQLite 重建表方式）。"""
    cols = [r[1] for r in conn.execute(text('PRAGMA table_info(positions)')).all()]
    print(f"positions 现有列: {cols}")

    if "price" not in cols:
        print("positions 已无 price 列，跳过")
        return

    # 1. 备份旧数据（不含 price）
    old_rows = conn.execute(text(
        "SELECT id, client_id, name, code, sector, quantity, cost_price FROM positions"
    )).all()
    print(f"positions 旧数据: {len(old_rows)} 行")

    # 2. 创建新表（无 price）
    conn.execute(text("""
        CREATE TABLE positions_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id VARCHAR(64) NOT NULL,
            name VARCHAR(64) NOT NULL,
            code VARCHAR(16) NOT NULL,
            sector VARCHAR(16) NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0,
            cost_price FLOAT NOT NULL DEFAULT 0.0,
            FOREIGN KEY(client_id) REFERENCES clients (id)
        )
    """))

    # 3. 复制数据
    if old_rows:
        conn.execute(text("""
            INSERT INTO positions_new
                (id, client_id, name, code, sector, quantity, cost_price)
            VALUES (:id, :client_id, :name, :code, :sector, :quantity, :cost_price)
        """), [
            {"id": r[0], "client_id": r[1], "name": r[2], "code": r[3],
             "sector": r[4], "quantity": r[5], "cost_price": r[6]}
            for r in old_rows
        ])

    # 4. 替换旧表
    conn.execute(text("DROP TABLE positions"))
    conn.execute(text("ALTER TABLE positions_new RENAME TO positions"))
    conn.execute(text("CREATE INDEX ix_positions_client_id ON positions (client_id)"))
    print(f"positions 重建完成（移除 price，迁移 {len(old_rows)} 行）")


def recalc_snapshots() -> dict:
    """清理非交易日快照 + 重算全部客户快照。"""
    from app.services.pnl_service import get_trading_days, recalculate_snapshots

    db = SessionLocal()
    try:
        # 1. 清除非交易日快照
        trading_days = set(get_trading_days(db))
        if trading_days:
            snaps = db.execute(text(
                "SELECT id, snapshot_date FROM pnl_daily_snapshot"
            )).all()
            bad_ids = [r[0] for r in snaps if r[1] not in trading_days]
            if bad_ids:
                db.execute(text(
                    "DELETE FROM pnl_daily_snapshot WHERE id IN :ids"
                ).bindparams(bindparam("ids", expanding=True)), {"ids": bad_ids})
                db.commit()
                print(f"已删除非交易日快照: {len(bad_ids)} 行")
            else:
                print("无非交易日快照脏数据")

        # 2. 重算全部快照
        result = recalculate_snapshots(db)
        print(f"快照重算: 客户 {result.get('clients', 0)} 个, "
              f"重算 {result.get('days_recalculated', 0)} 天, "
              f"删除 {result.get('days_deleted', 0)} 天")
        if result.get("error"):
            print(f"警告: {result['error']}")
        return result
    finally:
        db.close()


if __name__ == "__main__":
    with engine.connect() as conn:
        migrate_transactions(conn)
        migrate_positions(conn)
        conn.commit()
    print("--- 表结构迁移完成，开始重算快照 ---")
    recalc_snapshots()
    print("迁移全部完成!")
