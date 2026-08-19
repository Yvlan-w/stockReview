"""
数据库迁移：重建 market_kline 表，移除 trade_date 唯一约束，
改为 (index_code, trade_date) 联合唯一。
"""
import sys
sys.path.insert(0, '.')
from app.database import engine
from sqlalchemy import text

with engine.connect() as conn:
    # 1. 备份旧数据
    old_rows = conn.execute(text("SELECT * FROM market_kline")).all()
    print(f"旧表数据: {len(old_rows)} 行")

    # 2. 获取旧表列名
    cols = [r[1] for r in conn.execute(text('PRAGMA table_info(market_kline)')).all()]
    print(f"旧表列: {cols}")

    # 3. 创建新表（带联合唯一约束）
    conn.execute(text("""
        CREATE TABLE market_kline_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            index_code VARCHAR(16) NOT NULL DEFAULT '1.000001',
            trade_date VARCHAR(10) NOT NULL,
            open FLOAT,
            close FLOAT,
            high FLOAT,
            low FLOAT,
            volume FLOAT,
            turnover FLOAT,
            change_pct FLOAT,
            data_source VARCHAR(32) NOT NULL DEFAULT 'eastmoney',
            raw JSON,
            updated_at DATETIME,
            UNIQUE(index_code, trade_date)
        )
    """))

    # 4. 复制数据
    if old_rows:
        # 构建列映射
        old_col_map = {c: i for i, c in enumerate(cols)}

        insert_sql = """
            INSERT INTO market_kline_new 
            (index_code, trade_date, open, close, high, low, volume, turnover, 
             change_pct, data_source, raw, updated_at)
            VALUES (:index_code, :trade_date, :open, :close, :high, :low, :volume, 
                    :turnover, :change_pct, :data_source, :raw, :updated_at)
        """
        for row in old_rows:
            vals = {}
            for c in cols:
                vals[c] = row[old_col_map[c]]
            
            # 旧数据默认 index_code
            index_code = '1.000001'
            
            conn.execute(text(insert_sql), {
                'index_code': index_code,
                'trade_date': vals.get('trade_date'),
                'open': vals.get('open'),
                'close': vals.get('close'),
                'high': vals.get('high'),
                'low': vals.get('low'),
                'volume': vals.get('volume'),
                'turnover': vals.get('turnover'),
                'change_pct': vals.get('change_pct'),
                'data_source': vals.get('data_source', 'eastmoney'),
                'raw': vals.get('raw'),
                'updated_at': vals.get('updated_at'),
            })
        print(f"复制数据: {len(old_rows)} 行")

    # 5. 删除旧表
    conn.execute(text("DROP TABLE market_kline"))
    print("删除旧表")

    # 6. 重命名新表
    conn.execute(text("ALTER TABLE market_kline_new RENAME TO market_kline"))
    print("重命名新表")

    # 7. 创建索引
    conn.execute(text("CREATE INDEX ix_market_kline_index_code ON market_kline (index_code)"))
    conn.execute(text("CREATE INDEX ix_market_kline_trade_date ON market_kline (trade_date)"))
    print("创建索引")

    conn.commit()
    print("迁移完成!")