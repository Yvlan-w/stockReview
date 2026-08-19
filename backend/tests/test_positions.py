"""持仓写接口（PUT /api/clients/{id}/positions）权限与边界测试。"""


def _payload():
    return {
        "positions": [
            {"name": "测试股", "code": "000001", "sector": "金融",
             "quantity": 100, "cost_price": 10.0, "price": 11.0},
        ]
    }


def test_update_positions_admin(client, login):
    r = client.put("/api/clients/C001/positions", headers=login("admin"), json=_payload())
    assert r.status_code == 200
    body = r.json()
    assert len(body["positions"]) == 1
    assert body["positions"][0]["name"] == "测试股"


def test_update_positions_advisor_own(client, login):
    r = client.put("/api/clients/C001/positions", headers=login("adv_001"), json=_payload())
    assert r.status_code == 200


def test_update_positions_advisor_other_forbidden(client, login):
    r = client.put("/api/clients/C001/positions", headers=login("adv_002"), json=_payload())
    assert r.status_code == 403


def test_update_positions_service_assigned(client, login):
    # svc_001 服务 C001
    r = client.put("/api/clients/C001/positions", headers=login("svc_001"), json=_payload())
    assert r.status_code == 200


def test_update_positions_service_not_assigned_forbidden(client, login):
    # svc_003 不服务 C001
    r = client.put("/api/clients/C001/positions", headers=login("svc_003"), json=_payload())
    assert r.status_code == 403


def test_update_positions_client_own(client, login):
    # client001 拥有 C001
    r = client.put("/api/clients/C001/positions", headers=login("client001"), json=_payload())
    assert r.status_code == 200


def test_update_positions_non_client_forbidden(client, login):
    # user001 为非客户子角色，不拥有任何客户
    r = client.put("/api/clients/C001/positions", headers=login("user001"), json=_payload())
    assert r.status_code == 403


def test_update_positions_guest_forbidden(client, login):
    r = client.put("/api/clients/C001/positions", headers=login("guest"), json=_payload())
    assert r.status_code == 403


def test_update_positions_invalid_quantity(client, login):
    r = client.put("/api/clients/C001/positions", headers=login("admin"), json={
        "positions": [{"name": "股", "code": "000001", "sector": "金融",
                       "quantity": 0, "cost_price": 10.0, "price": 10.0}],
    })
    assert r.status_code == 422


def test_update_positions_replaces_all(client, login):
    r = client.put("/api/clients/C001/positions", headers=login("admin"), json=_payload())
    assert r.status_code == 200
    codes = [p["code"] for p in r.json()["positions"]]
    assert codes == ["000001"]  # 原 2 只持仓被整体替换为 1 只
