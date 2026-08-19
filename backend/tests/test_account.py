"""开户、账户自动生成、关系映射导入导出测试。"""


def test_user_options_admin(client, login):
    r = client.get("/api/users/options", headers=login("admin"))
    assert r.status_code == 200
    body = r.json()
    assert {"adv_001", "adv_002"} <= {u["id"] for u in body["advisors"]}
    assert {"svc_001", "svc_002"} <= {u["id"] for u in body["services"]}


def test_user_options_service(client, login):
    r = client.get("/api/users/options", headers=login("svc_001"))
    assert r.status_code == 200


def test_user_options_forbidden(client, login):
    r = client.get("/api/users/options", headers=login("adv_001"))
    assert r.status_code == 403


def test_admin_create_user_auto_credentials(client, login):
    r = client.post("/api/users", headers=login("admin"), json={
        "role": "user", "sub_role": "client", "name": "张伟",
    })
    assert r.status_code == 201
    body = r.json()
    assert body["username"].startswith("zhangwei#")
    assert body["initial_password"]
    assert body["role"] == "user"
    assert body["sub_role"] == "client"


def test_admin_create_user_explicit_password(client, login):
    r = client.post("/api/users", headers=login("admin"), json={
        "username": "explicit_user", "password": "secret1",
        "role": "advisor", "name": "测试顾问",
    })
    assert r.status_code == 201
    body = r.json()
    assert body["initial_password"] is None
    assert body["username"] == "explicit_user"


def test_service_can_create_client_self_assigned(client, login):
    r = client.post("/api/clients", headers=login("svc_001"), json={
        "name": "客服新建客户", "advisor_id": "adv_001", "service_ids": ["svc_002"],
    })
    assert r.status_code == 201
    body = r.json()
    assert set(body["service_ids"]) == {"svc_001", "svc_002"}


def test_service_create_client_self_fixed(client, login):
    r = client.post("/api/clients", headers=login("svc_003"), json={
        "name": "客服固定客户", "advisor_id": "adv_002", "service_ids": [],
    })
    assert r.status_code == 201
    assert r.json()["service_ids"] == ["svc_003"]


def test_create_client_with_login(client, login):
    r = client.post("/api/clients", headers=login("admin"), json={
        "name": "张伟", "advisor_id": "adv_001", "service_ids": ["svc_001"],
        "create_login": True,
    })
    assert r.status_code == 201
    body = r.json()
    assert body["login"] is not None
    assert body["login"]["username"].startswith("zhangwei#")
    assert body["login"]["initial_password"]
    assert body["login"]["role"] == "user"
    assert body["login"]["sub_role"] == "client"

    # 用生成的账号登录，验证 owner_user_id 已回填（仅能看到该客户）
    login_resp = client.post("/api/auth/login", json={
        "username": body["login"]["username"],
        "password": body["login"]["initial_password"],
    })
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]
    r2 = client.get("/api/clients", headers={"Authorization": f"Bearer {token}"})
    assert {c["id"] for c in r2.json()} == {body["id"]}


def test_relations_export_json(client, login):
    r = client.get("/api/relations/export", headers=login("admin"))
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 5
    c001 = next(x for x in body if x["client_id"] == "C001")
    assert c001["advisor_id"] == "adv_001"
    assert set(c001["service_ids"]) == {"svc_001", "svc_002"}


def test_relations_export_csv(client, login):
    r = client.get("/api/relations/export/csv", headers=login("admin"))
    assert r.status_code == 200
    assert "client_id" in r.text
    assert "C001" in r.text


def test_relations_import_json(client, login):
    r = client.post("/api/relations/import", headers=login("admin"), json=[
        {"client_id": "C001", "name": "张伟改", "advisor_id": "adv_002", "service_ids": ["svc_003"]},
        {"name": "导入新客户", "advisor_id": "adv_001", "service_ids": ["svc_001"]},
    ])
    assert r.status_code == 200
    body = r.json()
    assert body["updated"] == 1
    assert body["created"] == 1

    c001 = client.get("/api/clients/C001", headers=login("admin")).json()
    assert c001["name"] == "张伟改"
    assert c001["advisor_id"] == "adv_002"
    assert c001["service_ids"] == ["svc_003"]


def test_relations_import_csv(client, login):
    csv_text = "client_id,name,advisor_id,service_ids\nC002,李娜改,adv_003,svc_004|svc_005\n"
    r = client.post(
        "/api/relations/import/csv",
        content=csv_text.encode("utf-8"),
        headers=login("admin"),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["updated"] == 1

    c002 = client.get("/api/clients/C002", headers=login("admin")).json()
    assert c002["name"] == "李娜改"
    assert c002["advisor_id"] == "adv_003"
    assert set(c002["service_ids"]) == {"svc_004", "svc_005"}


def test_relations_import_requires_admin(client, login):
    r = client.post("/api/relations/import", headers=login("svc_001"), json=[])
    assert r.status_code == 403
