# -*- coding: utf-8 -*-
"""C001 明细深挖：持仓、交易流水、000001 行情、快照（UTF-8 输出到文件避免控制台乱码）。"""
import sqlite3
import io
import sys

DB = r"d:\github_rep\stockHoldingReview\backend\stock_review.db"
OUT = r"d:\github_rep\stockHoldingReview\backend\scripts\c001_detail.txt"

buf = io.StringIO()

conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

def p(s=""):
    buf.write(s + "\n")

p("=" * 76)
p("C001 明细核查")
p("=" * 76)

p("\n## positions 中 C001 全部持仓")
for r in cur.execute("SELECT id, name, code, sector, quantity, cost_price "
                     "FROM positions WHERE client_id='C001' ORDER BY code"):
    p(f"  id={r['id']} code={r['code']} name={r['name']!r} sector={r['sector']!r} "
      f"qty={r['quantity']} cost={r['cost_price']}")

p("\n## transactions 中 C001 全部流水")
for r in cur.execute("SELECT id, code, name, action, quantity, price, cost_price, "
                     "fee_mode, fee_value, fee_amount, realized_pnl, trade_date "
                     "FROM transactions WHERE client_id='C001' ORDER BY trade_date, id"):
    p(f"  id={r['id']} date={r['trade_date']} {r['action']:4s} code={r['code']} "
      f"name={r['name']!r} qty={r['quantity']} price={r['price']} "
      f"cost_price={r['cost_price']} fee={r['fee_amount']} "
      f"realized_pnl={r['realized_pnl']}")

p("\n## 000001 在所有表中的状态")
p("### stock_price（实时行情）")
for r in cur.execute("SELECT code, name, current_price, prev_close FROM stock_price "
                     "WHERE code='000001'"):
    p(f"  code={r['code']} name={r['name']!r} price={r['current_price']} prev={r['prev_close']}")
p("### stock_daily_price（日K线，最新5条）")
for r in cur.execute("SELECT code, trade_date, close FROM stock_daily_price "
                     "WHERE code='000001' ORDER BY trade_date DESC LIMIT 5"):
    p(f"  {r['trade_date']} close={r['close']}")
p("### positions（持仓）")
rows = cur.execute("SELECT * FROM positions WHERE code='000001'").fetchall()
p(f"  {'（无记录）' if not rows else ''}")
for r in rows:
    p(f"  client={r['client_id']} qty={r['quantity']} cost={r['cost_price']} "
      f"name={r['name']!r} sector={r['sector']!r}")
p("### transactions（流水）")
for r in cur.execute("SELECT client_id, trade_date, action, quantity, price, cost_price, "
                     "realized_pnl FROM transactions WHERE code='000001'"):
    p(f"  client={r['client_id']} {r['trade_date']} {r['action']} qty={r['quantity']} "
      f"price={r['price']} cost_price={r['cost_price']} realized={r['realized_pnl']}")

p("\n## C001 全部客户的 600519 持仓与流水对照")
p("### positions")
for r in cur.execute("SELECT client_id, quantity, cost_price, name FROM positions "
                     "WHERE code='600519'"):
    p(f"  {r['client_id']} qty={r['quantity']} cost={r['cost_price']} name={r['name']!r}")
p("### transactions")
for r in cur.execute("SELECT client_id, trade_date, action, quantity, price, "
                     "realized_pnl, fee_amount FROM transactions WHERE code='600519' "
                     "ORDER BY client_id, trade_date"):
    p(f"  {r['client_id']} {r['trade_date']} {r['action']} qty={r['quantity']} "
      f"price={r['price']} realized={r['realized_pnl']} fee={r['fee_amount']}")

p("\n## C001 最近5天快照")
for r in cur.execute("SELECT snapshot_date, total_market_value, total_cost, available_cash, "
                     "total_assets, daily_pnl, floating_pnl, cumulative_pnl "
                     "FROM pnl_daily_snapshot WHERE client_id='C001' "
                     "ORDER BY snapshot_date DESC LIMIT 5"):
    p(f"  {r['snapshot_date']}: mv={r['total_market_value']:.2f} cost={r['total_cost']:.2f} "
      f"cash={r['available_cash']:.2f} assets={r['total_assets']:.2f} "
      f"daily={r['daily_pnl']:.2f} float={r['floating_pnl']:.2f} cum={r['cumulative_pnl']:.2f}")

p("\n## 名称乱码检查：positions 中 C001 的名称十六进制")
for r in cur.execute("SELECT code, name FROM positions WHERE client_id='C001'"):
    p(f"  {r['code']}: {r['name']!r} hex={r['name'].encode('utf-8').hex()}")

conn.close()

with open(OUT, "w", encoding="utf-8") as f:
    f.write(buf.getvalue())
print("written to", OUT)
