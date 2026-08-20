# -*- coding: utf-8 -*-
"""端到端验证脚本：手续费双模式 / price移除 / 快照日期完整性。

运行后自动清理测试交易数据。用法: python _e2e_verify.py
"""
import sys
import json

import httpx

BASE = "http://127.0.0.1:8000/api"

passed, failed = [], []


def check(name: str, ok: bool, detail: str = ""):
    (passed if ok else failed).append(name)
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" | {detail}" if detail else ""))


def main():
    client = httpx.Client(base_url=BASE, timeout=30)

    # ---- 1. 登录 ----
    r = client.post("/auth/login", json={"username": "admin", "password": "123456"})
    check("登录 admin", r.status_code == 200, f"status={r.status_code}")
    token = r.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}

    # ---- 2. 手续费配置接口 ----
    r = client.get("/settings/trading-fees", headers=h)
    ok = r.status_code == 200 and "commission_rate" in r.json()
    check("GET /settings/trading-fees", ok, f"status={r.status_code}")
    fees = r.json() if r.status_code == 200 else {}
    print(f"    当前配置: commission={fees.get('commission_rate')}, "
          f"min={fees.get('min_commission')}, stamp={fees.get('stamp_tax_rate')}, "
          f"transfer={fees.get('transfer_fee_rate')}")

    # ---- 3. 持仓组合（实时价格，不依赖positions.price） ----
    r = client.get("/clients/C001/portfolio", headers=h)
    ok = r.status_code == 200
    body = r.json() if ok else {}
    positions = body.get("positions", [])
    has_cur = all("currentPrice" in p for p in positions) if positions else False
    check("GET /clients/C001/portfolio", ok and has_cur,
          f"status={r.status_code}, positions={len(positions)}, currentPrice字段存在={has_cur}")
    for p in positions:
        print(f"    {p['name']}({p['code']}): qty={p['quantity']}, cost={p['costPrice']}, "
              f"current={p.get('currentPrice')}, 市值={p.get('marketValue')}, "
              f"盈亏={p.get('unrealizedPnl')}")

    # ---- 4. 收益曲线日期完整性（8.17/8.18应存在，8.15/8.16周末应缺失） ----
    r = client.get("/clients/C001/pnl-history?range=30", headers=h)
    ok = r.status_code == 200
    hist = r.json() if ok else {}
    dates = hist.get("dates", [])
    check("GET /pnl-history?range=30", ok, f"status={r.status_code}, 共{len(dates)}个交易日")
    print(f"    日期序列: {dates}")
    check("快照含 2026-08-17", "2026-08-17" in dates)
    check("快照含 2026-08-18", "2026-08-18" in dates)
    check("快照不含周末 2026-08-15", "2026-08-15" not in dates)
    check("快照不含周末 2026-08-16", "2026-08-16" not in dates)
    # 检查关键字段非空
    if dates:
        check("最新快照累计盈亏非空", hist.get("pnl") and hist["pnl"][-1] is not None,
              f"pnl[-1]={hist.get('pnl', [None])[-1] if hist.get('pnl') else None}, "
              f"dailyPnl[-1]={hist.get('dailyPnl', [None])[-1] if hist.get('dailyPnl') else None}")

    # ---- 5. 交易记录列表 ----
    r = client.get("/clients/C001/transactions", headers=h)
    check("GET /clients/C001/transactions", r.status_code == 200,
          f"status={r.status_code}, 共{len(r.json()) if r.status_code == 200 else 0}条")

    # ---- 6. 快照完整性校验 ----
    r = client.get("/admin/snapshots/validate", headers=h)
    ok = r.status_code == 200
    v = r.json() if ok else {}
    check("GET /admin/snapshots/validate", ok, f"status={r.status_code}")
    print(f"    完整性: {json.dumps(v, ensure_ascii=False, default=str)[:400]}")

    # ---- 7. 创建交易（fixed模式），验证手续费字段并清理 ----
    test_ids = []
    r = client.post("/clients/C001/transactions", headers=h, json={
        "code": "600519", "name": "贵州茅台", "action": "buy",
        "quantity": 100, "price": 1400.0, "cost_price": 1680.0,
        "fee_mode": "fixed", "fee_value": 30.0,
    })
    ok = r.status_code == 201
    tx = r.json() if ok else {}
    if ok:
        test_ids.append(tx["id"])
    check("POST 交易(fixed手续费)", ok and tx.get("fee_amount") == 30.0,
          f"status={r.status_code}, fee_amount={tx.get('fee_amount')}, fee_mode={tx.get('fee_mode')}")

    # ---- 8. rate 模式：金额×费率 ----
    r = client.post("/clients/C001/transactions", headers=h, json={
        "code": "600036", "name": "招商银行", "action": "buy",
        "quantity": 1000, "price": 34.0, "cost_price": 40.0,
        "fee_mode": "rate", "fee_value": 0.00025,
    })
    ok = r.status_code == 201
    tx = r.json() if ok else {}
    if ok:
        test_ids.append(tx["id"])
    expect_fee = round(34.0 * 1000 * 0.00025, 2)  # 8.5
    check("POST 交易(rate手续费)", ok and abs((tx.get("fee_amount") or 0) - expect_fee) < 0.01,
          f"status={r.status_code}, fee_amount={tx.get('fee_amount')}(期望{expect_fee})")

    # ---- 9. 非法手续费 → 422（不落库） ----
    r = client.post("/clients/C001/transactions", headers=h, json={
        "code": "600519", "action": "buy", "quantity": 100, "price": 1400.0,
        "fee_mode": "rate", "fee_value": 0.05,  # 超过1%
    })
    check("非法费率(5%)返回422", r.status_code == 422, f"status={r.status_code}")

    r = client.post("/clients/C001/transactions", headers=h, json={
        "code": "600519", "action": "buy", "quantity": 100, "price": 1400.0,
        "fee_mode": "rate",  # 缺少fee_value
    })
    check("缺fee_value返回422", r.status_code == 422, f"status={r.status_code}")

    r = client.post("/clients/C001/transactions", headers=h, json={
        "code": "600519", "action": "buy", "quantity": 100, "price": 1400.0,
        "fee_mode": "fixed", "fee_value": 200000.0,  # 超过10万上限
    })
    check("固定金额超限返回422", r.status_code == 422, f"status={r.status_code}")

    # ---- 10. 清理测试交易 ----
    if test_ids:
        from app.database import SessionLocal
        from app.models import Transaction
        db = SessionLocal()
        try:
            n = db.query(Transaction).filter(Transaction.id.in_(test_ids)).delete(
                synchronize_session=False)
            db.commit()
            print(f"    已清理 {n} 条测试交易")
        finally:
            db.close()

    # ---- 11. 数据库表结构校验 ----
    from app.database import engine
    from sqlalchemy import text
    with engine.connect() as conn:
        p_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(positions)"))]
        t_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(transactions)"))]
    check("positions 表无 price 列", "price" not in p_cols, f"columns={p_cols}")
    check("transactions 表含手续费三列",
          all(c in t_cols for c in ("fee_mode", "fee_value", "fee_amount")),
          f"fee相关={[c for c in t_cols if c.startswith('fee')]}")

    # ---- 12. 手续费计算函数单元验证 ----
    from app.services.pnl_service import calc_buy_fee, calc_sell_fee, calc_realized_pnl_with_fee
    b = calc_buy_fee(100000.0)
    check("calc_buy_fee 默认(佣金+过户费)",
          b["total_fee"] == round(max(100000 * fees.get("commission_rate", 0.00025), 5)
                                  + 100000 * fees.get("transfer_fee_rate", 0.00001), 2),
          f"total_fee={b['total_fee']}")
    b2 = calc_buy_fee(100000.0, "fixed", 20.0)
    check("calc_buy_fee fixed=20", b2["total_fee"] == 20.0, f"total_fee={b2['total_fee']}")
    b3 = calc_buy_fee(100000.0, "rate", 0.0003)
    check("calc_buy_fee rate=0.0003", abs(b3["total_fee"] - 30.0) < 0.01,
          f"total_fee={b3['total_fee']}")
    s = calc_sell_fee(100000.0, "fixed", 15.0)
    check("calc_sell_fee fixed=15", s["total_fee"] == 15.0, f"total_fee={s['total_fee']}")
    p = calc_realized_pnl_with_fee(10.0, 11.0, 1000, "rate", 0.001)
    # 卖出金额11000, 费用=11, 买入金额10000 → net = 11000-11-10000 = 989
    check("calc_realized_pnl_with_fee rate模式", abs(p["net_pnl"] - 989.0) < 0.01,
          f"net_pnl={p['net_pnl']}, fee={p['total_fee']}")

    # ---- 汇总 ----
    print(f"\n===== 结果: {len(passed)} 通过, {len(failed)} 失败 =====")
    if failed:
        print("失败项:", failed)
        sys.exit(1)


if __name__ == "__main__":
    main()
