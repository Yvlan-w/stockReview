"""客户-顾问-客服关系映射与角色数据范围测试。"""


def _client_payload(**overrides):
    base = {
        "name": "测试客户", "advisor_id": "adv_001", "service_ids": ["svc_001"],
    }
    base.update(overrides)
    return base


def test_admin_sees_all_clients(client, login):
    r = client.get("/api/clients", headers=login("admin"))
    assert r.status_code == 200
    ids = {c["id"] for c in r.json()}
    assert ids == {"C001", "C002", "C003", "C004", "C005"}


def test_advisor_sees_own_clients(client, login):
    r = client.get("/api/clients", headers=login("adv_001"))
    ids = {c["id"] for c in r.json()}
    assert ids == {"C001"}


def test_service_sees_assigned_clients(client, login):
    # svc_001 服务 C001、C002
    r = client.get("/api/clients", headers=login("svc_001"))
    ids = {c["id"] for c in r.json()}
    assert ids == {"C001", "C002"}


def test_client_user_sees_own_record(client, login):
    # u_client_demo 拥有 C001
    r = client.get("/api/clients", headers=login("client001"))
    ids = {c["id"] for c in r.json()}
    assert ids == {"C001"}


def test_guest_sees_none(client, login):
    r = client.get("/api/clients", headers=login("guest"))
    assert r.status_code == 200
    assert r.json() == []


def test_relations_reflected(client, login):
    r = client.get("/api/clients/C001", headers=login("admin"))
    body = r.json()
    assert body["advisor_id"] == "adv_001"
    assert body["advisor_name"] == "顾问·张伟"
    assert set(body["service_ids"]) == {"svc_001", "svc_002"}


def test_get_client_forbidden(client, login):
    r = client.get("/api/clients/C002", headers=login("adv_001"))
    assert r.status_code == 403


def test_get_client_not_found(client, login):
    r = client.get("/api/clients/C999", headers=login("admin"))
    assert r.status_code == 404


def test_create_client_valid(client, login):
    r = client.post("/api/clients", headers=login("admin"), json=_client_payload(
        name="新客户", service_ids=["svc_001", "svc_002"],
    ))
    assert r.status_code == 201
    body = r.json()
    assert body["advisor_id"] == "adv_001"
    assert set(body["service_ids"]) == {"svc_001", "svc_002"}


def test_create_client_requires_admin(client, login):
    r = client.post("/api/clients", headers=login("adv_001"), json=_client_payload())
    assert r.status_code == 403


def test_create_client_no_service(client, login):
    r = client.post("/api/clients", headers=login("admin"), json=_client_payload(service_ids=[]))
    assert r.status_code == 422


def test_create_client_three_services(client, login):
    r = client.post("/api/clients", headers=login("admin"), json=_client_payload(
        service_ids=["svc_001", "svc_002", "svc_003"],
    ))
    assert r.status_code == 422


def test_create_client_duplicate_service(client, login):
    r = client.post("/api/clients", headers=login("admin"), json=_client_payload(
        service_ids=["svc_001", "svc_001"],
    ))
    assert r.status_code == 422


def test_create_client_advisor_wrong_role(client, login):
    r = client.post("/api/clients", headers=login("admin"), json=_client_payload(advisor_id="svc_001"))
    assert r.status_code == 422


def test_create_client_service_wrong_role(client, login):
    r = client.post("/api/clients", headers=login("admin"), json=_client_payload(service_ids=["adv_002"]))
    assert r.status_code == 422


def test_update_relations(client, login):
    r = client.put("/api/clients/C001/relations", headers=login("admin"), json={
        "advisor_id": "adv_002", "service_ids": ["svc_003"],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["advisor_id"] == "adv_002"
    assert body["service_ids"] == ["svc_003"]


def test_update_relations_requires_admin(client, login):
    r = client.put("/api/clients/C001/relations", headers=login("svc_001"), json={
        "advisor_id": "adv_002", "service_ids": ["svc_003"],
    })
    assert r.status_code == 403


def test_update_client_fields(client, login):
    r = client.put("/api/clients/C001", headers=login("adv_001"), json={"note": "顾问更新备注"})
    assert r.status_code == 200
    assert r.json()["note"] == "顾问更新备注"


def test_delete_client_admin_only(client, login):
    r = client.delete("/api/clients/C005", headers=login("adv_005"))
    assert r.status_code == 403
    r = client.delete("/api/clients/C005", headers=login("admin"))
    assert r.status_code == 204
