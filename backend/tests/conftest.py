"""pytest 测试配置：使用独立测试数据库，每个用例前重置数据。"""
import os
import tempfile

# 必须在导入 app 之前设置，使 app 使用独立测试库
_TEST_DB = os.path.join(tempfile.mkdtemp(prefix="stock_review_test_"), "test.db")
os.environ["STOCK_REVIEW_DB"] = "sqlite:///" + _TEST_DB

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services.seed import seed_all  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_db():
    """每个用例前重置数据库并写入种子数据，保证用例相互独立。"""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_all(db)
    finally:
        db.close()
    yield


@pytest.fixture
def login(client):
    """返回 login(username) -> 携带 Bearer token 的请求头字典。"""
    def _login(username: str, password: str = "123456"):
        r = client.post("/api/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}
    return _login
