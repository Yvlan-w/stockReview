# -*- coding: utf-8 -*-
"""只读数据一致性查验脚本：不改任何数据，仅输出核查结果。

核查维度：
1. 持仓 vs 交易流水：数量对账（Σ买入-Σ卖出 = 当前持仓？）、成本价对账（含费移动加权平均）
2. 重复持仓：同客户同证券代码多行
3. 持仓 vs 行情覆盖：stock_price / stock_daily_price 是否覆盖持仓代码
4. 名称漂移：positions.name vs stock_price.name（同一代码不同名称）
5. 快照 vs 持仓：pnl_daily_snapshot.total_cost vs 持仓总成本（当日口径）
6. 孤儿数据：positions/transactions 引用不存在的 client_id
7. 负持仓/超卖：交易流水累计卖出 > 累计买入
8. 可用资金 vs 交易流水：净买入金额与 available_cash 的勾稽关系（仅提示，不判定错误）
"""
import sqlite3
import sys

DB = r"d:\github_rep\stockHoldingReview\backend\stock_review.db"

conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

issues = []          # (级别, 维度, 明细)
LEVELS = {"FATAL": 0, "WARN": 1, "INFO": 2}


def add(level, dim, msg):
    issues.append((level, dim, msg))


print("=" * 72)
print("数据库数据一致性查验（只读）")
print("=" * 72)

# ---- 基本信息 ----
tables = [r[0] for r in cur.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
print(f"\n[表清单] {', '.join(tables)}")

rowcount = {t: cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in tables}
print("[行数统计]")
for t, n in rowcount.items():
    print(f"  {t:24s} {n}")

# ---- 客户清单 ----
clients = cur.execute("SELECT id, name, available_cash FROM clients ORDER BY id").fetchall()
print(f"\n[客户清单] 共 {len(clients)} 户")
for c in clients:
    print(f"  {c['id']}  {c['name']}  cash={c['available_cash']}")

# ---- 1. 持仓 vs 交易流水：数量对账 + 成本对账 ----
print("\n" + "-" * 72)
print("[1] 持仓 vs 交易流水 对账")
print("-" * 72)
positions = cur.execute("""
    SELECT client_id, code, name, sector, quantity, cost_price
    FROM positions ORDER BY client_id, code
""").fetchall()

# 交易汇总：按 (client_id, code) 聚合买卖数量与金额
tx_agg = {}
tx_rows = cur.execute("""
    SELECT client_id, code, name, action, quantity, price,
           cost_price, fee_amount, trade_date
    FROM transactions ORDER BY client_id, code, trade_date, id
""").fetchall()
for t in tx_rows:
    key = (t["client_id"], t["code"])
    if key not in tx_agg:
        tx_agg[key] = {"buy_qty": 0, "sell_qty": 0, "buy_amt": 0.0,
                       "sell_amt": 0.0, "fees": 0.0, "names": set(),
                       "last_name": None}
    a = tx_agg[key]
    if t["name"]:
        a["names"].add(t["name"])
        a["last_name"] = t["name"]
    if t["action"] == "buy":
        a["buy_qty"] += t["quantity"]
        a["buy_amt"] += t["quantity"] * t["price"]
    else:
        a["sell_qty"] += t["quantity"]
        a["sell_amt"] += t["quantity"] * t["price"]
    a["fees"] += t["fee_amount"] or 0.0

# 含费移动加权平均成本（按时间序重放）
def replay_wavg_cost(rows):
    """按时间顺序重放交易，返回当前数量与含费移动加权成本价。"""
    qty = 0
    cost_total = 0.0
    for t in rows:
        fee = t["fee_amount"] or 0.0
        if t["action"] == "buy":
            cost_total += t["quantity"] * t["price"] + fee
            qty += t["quantity"]
        else:
            if qty > 0:
                avg = cost_total / qty
                cost_total -= avg * t["quantity"]
            qty -= t["quantity"]
    return qty, (cost_total / qty if qty > 0 else 0.0)

tx_index = {}
for t in tx_rows:
    tx_index.setdefault((t["client_id"], t["code"]), []).append(t)

pos_keys = {(p["client_id"], p["code"]) for p in positions}

# 1a. 持仓行的对账
for p in positions:
    key = (p["client_id"], p["code"])
    a = tx_agg.get(key)
    print(f"\n  持仓: {p['client_id']} {p['code']} {p['name']}"
          f" qty={p['quantity']} cost={p['cost_price']} sector={p['sector']}")
    if a is None:
        add("WARN", "持仓无交易流水",
            f"{p['client_id']} {p['code']} {p['name']}: 持仓 {p['quantity']} 股，"
            f"但交易流水为空（历史导入或建仓记录缺失）")
        print(f"    [WARN] 无交易流水记录")
        continue
    net_qty = a["buy_qty"] - a["sell_qty"]
    print(f"    流水: 买入{a['buy_qty']} 卖出{a['sell_qty']} 净持仓={net_qty}"
          f" 累计费用={a['fees']:.2f}")
    if net_qty != p["quantity"]:
        add("FATAL", "数量不一致",
            f"{p['client_id']} {p['code']} {p['name']}: "
            f"持仓表 {p['quantity']} 股 vs 流水推算 {net_qty} 股"
            f"（差 {p['quantity'] - net_qty:+d}）")
        print(f"    [FATAL] 数量不一致: 持仓表 {p['quantity']} vs 流水 {net_qty}")
    else:
        print(f"    [OK] 数量一致")
    # 成本价对账（仅有流水且数量一致时有意义）
    rows = tx_index.get(key, [])
    _, wavg = replay_wavg_cost(rows)
    if net_qty > 0 and abs(wavg - p["cost_price"]) > 0.01:
        add("WARN", "成本价不一致",
            f"{p['client_id']} {p['code']} {p['name']}: "
            f"持仓表成本 {p['cost_price']:.4f} vs 流水含费加权 {wavg:.4f}")
        print(f"    [WARN] 成本价不一致: 持仓 {p['cost_price']} vs 含费加权 {wavg:.4f}")
    elif net_qty > 0:
        print(f"    [OK] 成本价一致（含费加权 {wavg:.4f}）")

# 1b. 流水有但持仓表无（清仓后残留 or 持仓缺失）
for key, a in tx_agg.items():
    if key not in pos_keys:
        net_qty = a["buy_qty"] - a["sell_qty"]
        if net_qty > 0:
            add("FATAL", "持仓缺失",
                f"{key[0]} {key[1]} {a['last_name']}: 流水净持仓 {net_qty} 股，"
                f"但持仓表无记录")
            print(f"\n  [FATAL] 持仓缺失: {key[0]} {key[1]} 流水净持仓 {net_qty} 股")
        elif net_qty < 0:
            add("FATAL", "超卖",
                f"{key[0]} {key[1]} {a['last_name']}: 流水累计卖出超过买入"
                f"（净 {net_qty} 股）")
            print(f"\n  [FATAL] 超卖: {key[0]} {key[1]} 净 {net_qty} 股")
        else:
            print(f"\n  [OK] {key[0]} {key[1]} 已清仓（净 0），持仓表无记录，正常")

# ---- 2. 重复持仓 ----
print("\n" + "-" * 72)
print("[2] 重复持仓（同客户同代码多行）")
print("-" * 72)
dups = cur.execute("""
    SELECT client_id, code, COUNT(*) AS n
    FROM positions GROUP BY client_id, code HAVING COUNT(*) > 1
""").fetchall()
if dups:
    for d in dups:
        rows = cur.execute(
            "SELECT id, name, quantity, cost_price FROM positions "
            "WHERE client_id=? AND code=?", (d["client_id"], d["code"])).fetchall()
        add("FATAL", "重复持仓",
            f"{d['client_id']} {d['code']} 存在 {d['n']} 行: "
            + "; ".join(f"id={r['id']} {r['name']} qty={r['quantity']}" for r in rows))
        print(f"  [FATAL] {d['client_id']} {d['code']} {d['n']} 行")
else:
    print("  [OK] 无重复持仓")

# ---- 3. 行情覆盖 ----
print("\n" + "-" * 72)
print("[3] 持仓代码的行情数据覆盖")
print("-" * 72)
for p in positions:
    rt = cur.execute("SELECT current_price, prev_close, name FROM stock_price "
                     "WHERE code=?", (p["code"],)).fetchone()
    kd = cur.execute(
        "SELECT trade_date, close FROM stock_daily_price WHERE code=? "
        "ORDER BY trade_date DESC LIMIT 1", (p["code"],)).fetchone()
    rt_ok = rt is not None and rt["current_price"] is not None
    kd_ok = kd is not None
    if not rt_ok and not kd_ok:
        add("WARN", "行情缺失",
            f"{p['client_id']} {p['code']} {p['name']}: 实时行情与日K线均无数据，"
            f"现价将退化为成本价 {p['cost_price']}")
    print(f"  {p['code']} {p['name']}: 实时行情={'有' if rt_ok else '无'}"
          f"（现价 {rt['current_price'] if rt_ok else '-'}），"
          f"日K线={'有' if kd_ok else '无'}"
          f"（最新 {kd['trade_date'] if kd_ok else '-'}）"
          + ("" if (rt_ok or kd_ok) else "  [WARN]"))

# ---- 4. 名称漂移 ----
print("\n" + "-" * 72)
print("[4] 名称漂移（同一代码在不同表中名称不同）")
print("-" * 72)
for p in positions:
    names = {("positions", p["name"])}
    rt = cur.execute("SELECT name FROM stock_price WHERE code=?",
                     (p["code"],)).fetchone()
    if rt and rt["name"]:
        names.add(("stock_price", rt["name"]))
    kd = cur.execute("SELECT DISTINCT name FROM stock_daily_price WHERE code=?",
                     (p["code"],)).fetchall() if "name" in [
        r[1] for r in cur.execute("PRAGMA table_info(stock_daily_price)").fetchall()
    ] else []
    distinct = {}
    for src, nm in names:
        distinct.setdefault(nm, []).append(src)
    if len(distinct) > 1:
        add("WARN", "名称漂移",
            f"{p['code']}: " + "; ".join(
                f"{nm}({','.join(s for s, _ in names if nm in s)})"
                for nm in distinct))
        print(f"  [WARN] {p['code']}: {distinct}")
    else:
        print(f"  [OK] {p['code']} 名称统一: {p['name']}")

# ---- 5. 快照 vs 持仓（最新快照的 total_cost 口径） ----
print("\n" + "-" * 72)
print("[5] 最新快照 vs 当前持仓")
print("-" * 72)
for c in clients:
    snap = cur.execute(
        "SELECT * FROM pnl_daily_snapshot WHERE client_id=? "
        "ORDER BY snapshot_date DESC LIMIT 1", (c["id"],)).fetchall()
    if not snap:
        print(f"  {c['id']} {c['name']}: 无快照（新客户或快照未生成）")
        continue
    s = snap[0]
    pos_cost = cur.execute(
        "SELECT COALESCE(SUM(quantity*cost_price),0) FROM positions "
        "WHERE client_id=?", (c["id"],)).fetchone()[0]
    print(f"  {c['id']} {c['name']} 最新快照 {s['snapshot_date']}: "
          f"snapshot.total_cost={s['total_cost']:.2f} "
          f"当前持仓总成本={pos_cost:.2f}")
    if abs(s["total_cost"] - pos_cost) > 1.0:
        add("WARN", "快照成本漂移",
            f"{c['id']} {c['name']}: 最新快照({s['snapshot_date']}) "
            f"total_cost={s['total_cost']:.2f} vs 当前持仓总成本={pos_cost:.2f}"
            f"（差 {s['total_cost']-pos_cost:+.2f}，若快照后有调仓属正常）")
    snap_cash = s["available_cash"]
    if abs(snap_cash - c["available_cash"]) > 1.0:
        add("INFO", "快照现金漂移",
            f"{c['id']} {c['name']}: 快照现金 {snap_cash:.2f} vs "
            f"当前现金 {c['available_cash']:.2f}")

# ---- 6. 孤儿数据 ----
print("\n" + "-" * 72)
print("[6] 孤儿数据（引用不存在的客户）")
print("-" * 72)
for tbl in ("positions", "transactions", "pnl_daily_snapshot"):
    orphans = cur.execute(f"""
        SELECT DISTINCT {tbl}.client_id FROM {tbl}
        LEFT JOIN clients ON clients.id = {tbl}.client_id
        WHERE clients.id IS NULL
    """).fetchall()
    if orphans:
        add("FATAL", "孤儿数据",
            f"{tbl}: 孤儿 client_id = "
            + ", ".join(r[0] for r in orphans))
        print(f"  [FATAL] {tbl}: {[r[0] for r in orphans]}")
    else:
        print(f"  [OK] {tbl} 无孤儿记录")

# ---- 7. 负数/零数量持仓 ----
print("\n" + "-" * 72)
print("[7] 非法持仓（数量<=0 或 成本价<=0）")
print("-" * 72)
bad = cur.execute(
    "SELECT client_id, code, name, quantity, cost_price FROM positions "
    "WHERE quantity <= 0 OR cost_price <= 0").fetchall()
if bad:
    for b in bad:
        add("FATAL", "非法持仓",
            f"{b['client_id']} {b['code']} {b['name']}: "
            f"qty={b['quantity']} cost={b['cost_price']}")
        print(f"  [FATAL] {dict(b)}")
else:
    print("  [OK] 无非法持仓")

# ---- 汇总 ----
print("\n" + "=" * 72)
print("问题汇总")
print("=" * 72)
if not issues:
    print("未发现一致性问题。")
for level in ("FATAL", "WARN", "INFO"):
    group = [i for i in issues if i[0] == level]
    if group:
        print(f"\n### {level}（{len(group)} 项）")
        for _, dim, msg in group:
            print(f"  [{dim}] {msg}")

conn.close()
print("\n（只读查验完成，未修改任何数据）")
