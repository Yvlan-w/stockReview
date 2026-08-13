"""REST API 路由：认证 / 用户 / 客户 / 关系 / 风险预警 / 站内信。"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..core.deps import get_current_user, require_roles
from ..core.security import create_access_token
from ..database import get_db
from ..models import (
    User, Client, RiskAlert, Notification,
    ROLE_ADMIN, ROLE_SERVICE, ROLE_ADVISOR, ROLE_USER,
    ALERT_OPEN, ALERT_ACK, ALERT_RESOLVED,
)
from ..schemas import (
    LoginRequest, TokenOut, UserOut, UserCreate,
    ClientCreate, ClientUpdate, RelationsUpdate, ClientOut,
    RiskAlertOut, AlertStatusUpdate, NotificationOut, UnreadCountOut,
)
from ..services import auth_service, client_service, risk_engine, notification_service

router = APIRouter(prefix="/api")


# ==================== 认证 ====================
@router.post("/auth/login", response_model=TokenOut)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = auth_service.authenticate(db, body.username, body.password)
    token = create_access_token(user.id, user.role, user.sub_role)
    return {"access_token": token, "token_type": "bearer", "user": UserOut.model_validate(user)}


@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


# ==================== 用户管理（管理员） ====================
@router.get("/users", response_model=List[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_roles(ROLE_ADMIN))):
    return db.query(User).order_by(User.id).all()


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, db: Session = Depends(get_db), _: User = Depends(require_roles(ROLE_ADMIN))):
    return auth_service.create_user(db, body)


# ==================== 客户 ====================
@router.get("/clients", response_model=List[ClientOut])
def list_clients(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    clients = client_service.list_visible_clients(db, user)
    return [client_service.serialize_client(c) for c in clients]


@router.post("/clients", response_model=ClientOut, status_code=status.HTTP_201_CREATED)
def create_client(body: ClientCreate, db: Session = Depends(get_db), _: User = Depends(require_roles(ROLE_ADMIN))):
    client = client_service.create_client(db, body)
    return client_service.serialize_client(client)


@router.get("/clients/{client_id}", response_model=ClientOut)
def get_client(client_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    client = client_service.get_visible_client(db, user, client_id)
    return client_service.serialize_client(client)


@router.put("/clients/{client_id}", response_model=ClientOut)
def update_client(client_id: str, body: ClientUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    client = client_service.get_visible_client(db, user, client_id)
    client = client_service.update_client_fields(db, client, body)
    return client_service.serialize_client(client)


@router.delete("/clients/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_client(client_id: str, db: Session = Depends(get_db), _: User = Depends(require_roles(ROLE_ADMIN))):
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "客户不存在")
    db.delete(client)
    db.commit()
    return None


# ==================== 关系映射（管理员） ====================
@router.put("/clients/{client_id}/relations", response_model=ClientOut)
def update_relations(client_id: str, body: RelationsUpdate, db: Session = Depends(get_db), _: User = Depends(require_roles(ROLE_ADMIN))):
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "客户不存在")
    client = client_service.update_client_relations(db, client, body)
    return client_service.serialize_client(client)


# ==================== 风险预警 ====================
@router.post("/risk/evaluate/{client_id}", response_model=List[RiskAlertOut])
async def evaluate_risk(client_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    client = client_service.get_visible_client(db, user, client_id)
    alerts, recipients = risk_engine.run_risk_evaluation(db, client)
    if recipients:
        await notification_service.push_notification(recipients, {
            "type": "risk_alert",
            "client_id": client.id,
            "client_name": client.name,
            "count": len(alerts),
        })
    return alerts


@router.get("/clients/{client_id}/alerts", response_model=List[RiskAlertOut])
def list_alerts(client_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    client_service.get_visible_client(db, user, client_id)  # 校验可见性
    return db.query(RiskAlert).filter(RiskAlert.client_id == client_id).order_by(RiskAlert.id.desc()).all()


@router.patch("/alerts/{alert_id}", response_model=RiskAlertOut)
def update_alert_status(alert_id: int, body: AlertStatusUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    alert = db.get(RiskAlert, alert_id)
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "预警不存在")
    if body.status not in (ALERT_OPEN, ALERT_ACK, ALERT_RESOLVED):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "非法预警状态")
    # 仅客服/顾问/管理员可处理；且需在其可见客户范围内
    if user.role not in (ROLE_ADMIN, ROLE_SERVICE, ROLE_ADVISOR):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权限处理预警")
    client_service.get_visible_client(db, user, alert.client_id)
    alert.status = body.status
    db.commit()
    db.refresh(alert)
    return alert


# ==================== 站内信 ====================
@router.get("/notifications", response_model=List[NotificationOut])
def list_notifications(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return (
        db.query(Notification)
        .filter(Notification.recipient_id == user.id)
        .order_by(Notification.id.desc())
        .all()
    )


@router.get("/notifications/unread-count", response_model=UnreadCountOut)
def unread_count(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    count = (
        db.query(Notification)
        .filter(Notification.recipient_id == user.id, Notification.is_read.is_(False))
        .count()
    )
    return {"unread": count}


@router.post("/notifications/{notification_id}/read", response_model=NotificationOut)
def mark_read(notification_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    n = db.get(Notification, notification_id)
    if n is None or n.recipient_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "消息不存在")
    n.is_read = True
    db.commit()
    db.refresh(n)
    return n


@router.post("/notifications/read-all", response_model=UnreadCountOut)
def mark_all_read(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.query(Notification).filter(
        Notification.recipient_id == user.id, Notification.is_read.is_(False)
    ).update({"is_read": True})
    db.commit()
    return {"unread": 0}
