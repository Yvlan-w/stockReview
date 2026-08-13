"""认证与角色权限测试。"""


def test_login_success(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "123456"})
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["role"] == "admin"
    assert body["user"]["username"] == "admin"


def test_login_wrong_password(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 401


def test_login_unknown_user(client):
    r = client.post("/api/auth/login", json={"username": "nobody", "password": "123456"})
    assert r.status_code == 401


def test_me_without_token(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_me_with_token(client, login):
    r = client.get("/api/auth/me", headers=login("adv_001"))
    assert r.status_code == 200
    assert r.json()["role"] == "advisor"
    assert r.json()["id"] == "adv_001"


def test_invalid_token(client):
    r = client.get("/api/auth/me", headers={"Authorization": "Bearer bad.token.here"})
    assert r.status_code == 401


def test_list_users_requires_admin(client, login):
    r = client.get("/api/users", headers=login("adv_001"))
    assert r.status_code == 403


def test_list_users_admin(client, login):
    r = client.get("/api/users", headers=login("admin"))
    assert r.status_code == 200
    roles = {u["role"] for u in r.json()}
    assert {"admin", "advisor", "service", "user", "guest"} <= roles


def test_create_user_success(client, login):
    r = client.post("/api/users", headers=login("admin"), json={
        "username": "new_advisor", "password": "secret1", "role": "advisor",
        "name": "新顾问",
    })
    assert r.status_code == 201
    assert r.json()["role"] == "advisor"


def test_create_user_duplicate(client, login):
    payload = {"username": "dup_user", "password": "secret1", "role": "advisor", "name": "重复"}
    assert client.post("/api/users", headers=login("admin"), json=payload).status_code == 201
    r = client.post("/api/users", headers=login("admin"), json=payload)
    assert r.status_code == 409


def test_create_user_invalid_role(client, login):
    r = client.post("/api/users", headers=login("admin"), json={
        "username": "bad_role", "password": "secret1", "role": "superadmin", "name": "x",
    })
    assert r.status_code == 422


def test_create_user_subrole_requires_user_role(client, login):
    r = client.post("/api/users", headers=login("admin"), json={
        "username": "bad_sub", "password": "secret1", "role": "advisor",
        "sub_role": "client", "name": "x",
    })
    assert r.status_code == 422


def test_create_user_short_password(client, login):
    r = client.post("/api/users", headers=login("admin"), json={
        "username": "short_pw", "password": "123", "role": "user", "name": "x",
    })
    assert r.status_code == 422
