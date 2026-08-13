"""站内信（通知）接口与分发去重测试。"""
from app.database import SessionLocal
from app.models import Notification
from app.services import notification_service


# ---- 分发单元测试（去重 + 持久化） ----
def test_dispatch_dedups_recipients():
    db = SessionLocal()
    try:
        created = notification_service.dispatch(
            db, ["adv_001", "adv_001", "svc_001", None, ""],
            "标题", "内容", "info",
        )
        assert len(created) == 2
        assert {n.recipient_id for n in created} == {"adv_001", "svc_001"}
    finally:
        db.close()


# ---- 站内信接口测试 ----
def test_list_notifications_empty(client, login):
    r = client.get("/api/notifications", headers=login("svc_006"))
    assert r.status_code == 200
    assert r.json() == []


def test_unread_count_zero(client, login):
    r = client.get("/api/notifications/unread-count", headers=login("svc_006"))
    assert r.status_code == 200
    assert r.json() == {"unread": 0}


def test_risk_evaluation_dispatches_to_service(client, login):
    # C001 服务 svc_001、svc_002，顾问 adv_001
    client.post("/api/risk/evaluate/C001", headers=login("admin"))
    r = client.get("/api/notifications", headers=login("svc_001"))
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["category"] == "risk_alert"
    assert "C001" in body[0]["content"]
    assert body[0]["is_read"] is False


def test_risk_evaluation_dispatches_to_advisor(client, login):
    client.post("/api/risk/evaluate/C001", headers=login("admin"))
    r = client.get("/api/notifications", headers=login("adv_001"))
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_unread_count_after_evaluation(client, login):
    client.post("/api/risk/evaluate/C001", headers=login("admin"))
    r = client.get("/api/notifications/unread-count", headers=login("svc_001"))
    assert r.json() == {"unread": 1}


def test_mark_single_read(client, login):
    client.post("/api/risk/evaluate/C001", headers=login("admin"))
    notif_id = client.get("/api/notifications", headers=login("svc_001")).json()[0]["id"]
    r = client.post(f"/api/notifications/{notif_id}/read", headers=login("svc_001"))
    assert r.status_code == 200
    assert r.json()["is_read"] is True
    assert client.get("/api/notifications/unread-count", headers=login("svc_001")).json() == {"unread": 0}


def test_mark_read_others_notification_forbidden(client, login):
    client.post("/api/risk/evaluate/C001", headers=login("admin"))
    notif_id = client.get("/api/notifications", headers=login("svc_001")).json()[0]["id"]
    # svc_002 不是该消息接收人
    r = client.post(f"/api/notifications/{notif_id}/read", headers=login("svc_002"))
    assert r.status_code == 404


def test_mark_read_not_found(client, login):
    r = client.post("/api/notifications/99999/read", headers=login("admin"))
    assert r.status_code == 404


def test_mark_all_read(client, login):
    # 连续两次评估同一客户，会先清理 open 预警再重新写入，因此只保留最新一批通知
    client.post("/api/risk/evaluate/C001", headers=login("admin"))
    r = client.post("/api/notifications/read-all", headers=login("svc_001"))
    assert r.status_code == 200
    assert r.json() == {"unread": 0}
    # 二次确认全部已读
    assert client.get("/api/notifications/unread-count", headers=login("svc_001")).json() == {"unread": 0}


def test_notifications_require_auth(client):
    r = client.get("/api/notifications")
    assert r.status_code == 401
