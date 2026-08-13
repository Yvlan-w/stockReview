"""风险引擎与风险预警接口测试。

覆盖：规则命中（浮亏/行业集中/单票集中/个股深度亏损）、
边界（无持仓、盈亏平衡）、异常（越权、非法状态、资源不存在）。
"""
from types import SimpleNamespace

from app.services.risk_engine import evaluate_client


# ---- 纯规则单元测试（不落库） ----
def _pos(name="股", sector="行业", qty=100, cost=10.0, price=10.0):
    return SimpleNamespace(name=name, sector=sector, quantity=qty,
                           cost_price=cost, price=price)


def _client(positions):
    return SimpleNamespace(positions=positions)


def _types(alerts):
    return [(a["type"], a["level"]) for a in alerts]


def test_evaluate_loss_high():
    # 成本 2000，市值 1700，亏损 -15%
    c = _client([_pos(cost=10.0, price=8.5)])
    alerts = evaluate_client(c)
    assert ("loss", "high") in _types(alerts)


def test_evaluate_loss_mid():
    # 亏损 -5%
    c = _client([_pos(cost=10.0, price=9.5)])
    alerts = evaluate_client(c)
    assert ("loss", "mid") in _types(alerts)


def test_evaluate_sector_concentration_high():
    # 单一行业占比 > 50%
    c = _client([
        _pos(sector="新能源", qty=100, cost=10, price=10),
        _pos(sector="银行", qty=10, cost=10, price=10),
    ])
    alerts = evaluate_client(c)
    assert ("sector", "high") in _types(alerts)


def test_evaluate_single_stock_high():
    # 单票占比 > 40%
    c = _client([
        _pos(name="A", qty=100, cost=10, price=10),
        _pos(name="B", qty=10, cost=10, price=10),
        _pos(name="C", qty=10, cost=10, price=10),
    ])
    alerts = evaluate_client(c)
    assert ("stock", "high") in _types(alerts)


def test_evaluate_individual_stock_loss_mid():
    # 单只亏损 ≤ -20%
    c = _client([
        _pos(name="A", qty=100, cost=10, price=7.5),
        _pos(name="B", qty=100, cost=10, price=10),
    ])
    alerts = evaluate_client(c)
    assert ("stock", "mid") in _types(alerts)


def test_evaluate_no_alerts_on_profit():
    # 盈利 + 分散：不触发任何预警
    c = _client([
        _pos(sector="银行", qty=100, cost=10, price=11),
        _pos(sector="消费", qty=100, cost=10, price=11),
        _pos(sector="科技", qty=100, cost=10, price=11),
    ])
    assert evaluate_client(c) == []


def test_evaluate_empty_positions():
    assert evaluate_client(_client([])) == []


def test_evaluate_breakeven_no_loss_alert():
    # 盈亏平衡（0%）不应触发浮亏
    c = _client([_pos(cost=10.0, price=10.0)])
    assert ("loss", "high") not in _types(evaluate_client(c))
    assert ("loss", "mid") not in _types(evaluate_client(c))


# ---- 风险预警接口测试（依赖种子数据） ----
def test_evaluate_admin_triggers_alerts(client, login):
    # C005 触发：loss high + sector high + stock high + stock mid = 4 条
    r = client.post("/api/risk/evaluate/C005", headers=login("admin"))
    assert r.status_code == 200
    alerts = r.json()
    types = {a["type"] for a in alerts}
    assert {"loss", "sector", "stock"} <= types
    assert all(a["status"] == "open" for a in alerts)


def test_evaluate_advisor_own_client(client, login):
    r = client.post("/api/risk/evaluate/C001", headers=login("adv_001"))
    assert r.status_code == 200
    assert len(r.json()) >= 1


def test_evaluate_advisor_other_client_forbidden(client, login):
    r = client.post("/api/risk/evaluate/C001", headers=login("adv_002"))
    assert r.status_code == 403


def test_evaluate_guest_forbidden(client, login):
    r = client.post("/api/risk/evaluate/C001", headers=login("guest"))
    assert r.status_code == 403


def test_evaluate_not_found(client, login):
    r = client.post("/api/risk/evaluate/C999", headers=login("admin"))
    assert r.status_code == 404


def test_list_alerts_visible(client, login):
    client.post("/api/risk/evaluate/C001", headers=login("admin"))
    r = client.get("/api/clients/C001/alerts", headers=login("adv_001"))
    assert r.status_code == 200
    assert len(r.json()) >= 1


def test_list_alerts_forbidden(client, login):
    r = client.get("/api/clients/C001/alerts", headers=login("adv_002"))
    assert r.status_code == 403


def test_update_alert_status_valid(client, login):
    client.post("/api/risk/evaluate/C001", headers=login("admin"))
    alert_id = client.get("/api/clients/C001/alerts", headers=login("admin")).json()[0]["id"]
    r = client.patch(f"/api/alerts/{alert_id}", headers=login("admin"), json={"status": "acknowledged"})
    assert r.status_code == 200
    assert r.json()["status"] == "acknowledged"


def test_update_alert_status_invalid(client, login):
    client.post("/api/risk/evaluate/C001", headers=login("admin"))
    alert_id = client.get("/api/clients/C001/alerts", headers=login("admin")).json()[0]["id"]
    r = client.patch(f"/api/alerts/{alert_id}", headers=login("admin"), json={"status": "bogus"})
    assert r.status_code == 422


def test_update_alert_status_guest_forbidden(client, login):
    client.post("/api/risk/evaluate/C001", headers=login("admin"))
    alert_id = client.get("/api/clients/C001/alerts", headers=login("admin")).json()[0]["id"]
    r = client.patch(f"/api/alerts/{alert_id}", headers=login("guest"), json={"status": "resolved"})
    assert r.status_code == 403


def test_update_alert_status_not_found(client, login):
    r = client.patch("/api/alerts/99999", headers=login("admin"), json={"status": "resolved"})
    assert r.status_code == 404
