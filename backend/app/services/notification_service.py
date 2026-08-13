"""通知系统：多渠道抽象 + 站内信（REST 持久化）+ WebSocket 实时推送。

渠道：
- in_app   站内信（真实实现：持久化到 notifications 表，前端站内信读取）
- email    邮件（预留接口，不真实发送）
- dingtalk 钉钉机器人（预留接口，不真实发送）
"""
from typing import List

from sqlalchemy.orm import Session

from ..models import Notification, NOTIF_INFO


# ---- 渠道抽象 ----
class NotificationChannel:
    name = "base"

    def send(self, db: Session, notification: Notification) -> bool:
        raise NotImplementedError


class InAppChannel(NotificationChannel):
    name = "in_app"

    def send(self, db: Session, notification: Notification) -> bool:
        # 站内信本身即持久化在 notifications 表，实时推送由 push_notification 负责
        return True


class EmailChannel(NotificationChannel):
    """邮件渠道——预留接口，Phase 3 接入 SMTP 时实现真实发送。"""
    name = "email"

    def send(self, db: Session, notification: Notification) -> bool:
        # TODO(预留): 接入 SMTP 发送邮件
        return True


class DingTalkChannel(NotificationChannel):
    """钉钉机器人渠道——预留接口，Phase 3 接入 Webhook 时实现真实发送。"""
    name = "dingtalk"

    def send(self, db: Session, notification: Notification) -> bool:
        # TODO(预留): 调用钉钉群机器人 Webhook
        return True


CHANNELS: List[NotificationChannel] = [InAppChannel(), EmailChannel(), DingTalkChannel()]


def dispatch(
    db: Session,
    recipient_ids: list[str],
    title: str,
    content: str,
    category: str = NOTIF_INFO,
) -> list[Notification]:
    """向多个接收人分发通知：持久化站内信 + 依次调用各渠道。"""
    recipient_ids = list(dict.fromkeys(r for r in recipient_ids if r))
    created: list[Notification] = []
    for uid in recipient_ids:
        n = Notification(recipient_id=uid, title=title, content=content, category=category)
        db.add(n)
        created.append(n)
    db.commit()
    for n in created:
        db.refresh(n)
        for channel in CHANNELS:
            channel.send(db, n)
    return created


# ---- WebSocket 连接管理器（实时推送） ----
class ConnectionManager:
    def __init__(self):
        self._connections: dict[str, set] = {}

    async def connect(self, user_id: str, websocket) -> None:
        await websocket.accept()
        self._connections.setdefault(user_id, set()).add(websocket)

    def disconnect(self, user_id: str, websocket) -> None:
        conns = self._connections.get(user_id)
        if conns:
            conns.discard(websocket)

    async def push_to_users(self, recipient_ids: list[str], payload: dict) -> None:
        for uid in recipient_ids:
            for ws in list(self._connections.get(uid, set())):
                try:
                    await ws.send_json(payload)
                except Exception:
                    self.disconnect(uid, ws)


manager = ConnectionManager()


async def push_notification(recipient_ids: list[str], payload: dict) -> None:
    """向在线用户实时推送（WebSocket）。"""
    await manager.push_to_users(recipient_ids, payload)
