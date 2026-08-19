"""REST API 路由：认证 / 用户 / 客户 / 关系 / 风险预警 / 站内信。"""
import csv
import io
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
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
    LoginRequest, TokenOut, UserOut, UserCreate, UserCreateOut, UserOptionsOut,
    ClientCreate, ClientUpdate, RelationsUpdate, PositionsUpdate, ClientOut, ClientCreateOut,
    RiskAlertOut, AlertStatusUpdate, NotificationOut, UnreadCountOut,
    RelationImportRow, RelationExportRow, RelationImportResult,
)
from ..services import auth_service, client_service, risk_engine, notification_service
from ..services import market_service
from ..services import market_analysis_service

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


@router.get("/users/options", response_model=UserOptionsOut)
def user_options(db: Session = Depends(get_db), _: User = Depends(require_roles(ROLE_ADMIN, ROLE_SERVICE))):
    """开户/关系映射表单的顾问、客服下拉选项。"""
    advisors = db.query(User).filter(User.role == ROLE_ADVISOR, User.is_active.is_(True)).order_by(User.id).all()
    services = db.query(User).filter(User.role == ROLE_SERVICE, User.is_active.is_(True)).order_by(User.id).all()
    return {"advisors": advisors, "services": services}


@router.post("/users", response_model=UserCreateOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, db: Session = Depends(get_db), _: User = Depends(require_roles(ROLE_ADMIN))):
    user, initial_password = auth_service.create_user(db, body)
    out = UserCreateOut.model_validate(user)
    out.initial_password = initial_password
    return out


# ==================== 客户 ====================
@router.get("/clients", response_model=List[ClientOut])
def list_clients(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    clients = client_service.list_visible_clients(db, user)
    return [client_service.serialize_client(c) for c in clients]


@router.post("/clients", response_model=ClientCreateOut, status_code=status.HTTP_201_CREATED)
def create_client(body: ClientCreate, db: Session = Depends(get_db), user: User = Depends(require_roles(ROLE_ADMIN, ROLE_SERVICE))):
    client, login = client_service.create_client(db, body, creator=user)
    result = client_service.serialize_client(client)
    result["login"] = login
    return result


@router.get("/clients/{client_id}", response_model=ClientOut)
def get_client(client_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    client = client_service.get_visible_client(db, user, client_id)
    return client_service.serialize_client(client)


@router.put("/clients/{client_id}", response_model=ClientOut)
def update_client(client_id: str, body: ClientUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    client = client_service.get_visible_client(db, user, client_id)
    client = client_service.update_client_fields(db, client, body)
    return client_service.serialize_client(client)


@router.put("/clients/{client_id}/positions", response_model=ClientOut)
def update_client_positions(client_id: str, body: PositionsUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    client = client_service.get_visible_client(db, user, client_id)
    client = client_service.update_client_positions(db, client, body.positions)
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


@router.get("/relations/export", response_model=List[RelationExportRow])
def export_relations(db: Session = Depends(get_db), _: User = Depends(require_roles(ROLE_ADMIN))):
    """导出关系映射（JSON 数组）。"""
    return client_service.export_relations(db)


@router.get("/relations/export/csv")
def export_relations_csv(db: Session = Depends(get_db), _: User = Depends(require_roles(ROLE_ADMIN))):
    """导出关系映射（CSV，service_ids 以 | 分隔）。"""
    rows = client_service.export_relations(db)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["client_id", "name", "advisor_id", "advisor_name", "service_ids"])
    for r in rows:
        writer.writerow([r["client_id"], r["name"], r["advisor_id"], r["advisor_name"], "|".join(r["service_ids"])])
    return Response(content=buf.getvalue(), media_type="text/csv; charset=utf-8")


@router.post("/relations/import", response_model=RelationImportResult)
def import_relations(body: List[RelationImportRow], db: Session = Depends(get_db), _: User = Depends(require_roles(ROLE_ADMIN))):
    """批量导入关系映射（JSON 数组）。"""
    return client_service.import_relations(db, body)


@router.post("/relations/import/csv", response_model=RelationImportResult)
async def import_relations_csv(request: Request, db: Session = Depends(get_db), _: User = Depends(require_roles(ROLE_ADMIN))):
    """批量导入关系映射（CSV，列：client_id,name,advisor_id,service_ids）。"""
    raw = (await request.body()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(raw))
    rows: List[RelationImportRow] = []
    errors = []
    for i, line in enumerate(reader):
        line_no = i + 2  # 表头占第 1 行
        try:
            client_id = (line.get("client_id") or "").strip() or None
            name = (line.get("name") or "").strip()
            advisor_id = (line.get("advisor_id") or "").strip()
            raw_services = (line.get("service_ids") or "").replace("；", "|").replace(";", "|")
            service_ids = [s.strip() for s in raw_services.split("|") if s.strip()]
            rows.append(RelationImportRow(
                client_id=client_id, name=name, advisor_id=advisor_id, service_ids=service_ids,
            ))
        except Exception:
            errors.append({"row": line_no, "detail": "字段格式错误"})
    result = client_service.import_relations(db, rows)
    result["errors"] = errors + result["errors"]
    return result


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


# ==================== 行情（公开，无需登录） ====================
@router.get("/market/realtime")
def get_market_realtime(db: Session = Depends(get_db)):
    """返回实时行情快照（直接读库，<1ms）。空库返回 data=null + pending 状态。"""
    snap = market_service.get_snapshot(db)
    if snap is None:
        return {"data": None, "fetch_status": "pending", "updated_at": None}
    return {
        "data": snap.data,
        "fetch_status": snap.fetch_status,
        "fetch_error": snap.fetch_error,
        "updated_at": snap.updated_at.isoformat() if snap.updated_at else None,
    }


@router.get("/market/kline")
def get_market_kline(db: Session = Depends(get_db), index: str = "1.000001"):
    """返回指定指数的日 K 线（最多 40 条，直接读库）。
    支持指数代码: 1.000001(上证), 0.399001(深证), 0.399006(创业板), 1.000688(科创50)。
    """
    rows = market_service.get_kline(db, index_code=index)
    result = []
    for r in rows:
        obj = {
            "fullDate": r.trade_date,
            "date": r.trade_date[5:],
            "open": r.open, "close": r.close, "high": r.high, "low": r.low,
            "volume": r.volume, "turnover": r.turnover,
            "changePct": r.change_pct,
            "indexCode": r.index_code,
        }
        if r.raw:
            raw = r.raw
            if len(raw) > 6: obj["amplitude"] = float(raw[7]) if raw[7] else 0.0
            if len(raw) > 9: obj["change"] = float(raw[9]) if raw[9] else 0.0
            if len(raw) > 10: obj["turnoverRate"] = float(raw[10]) if raw[10] else 0.0
        result.append(obj)
    return {"data": result, "index": index}


@router.get("/market/sectors")
def get_market_sectors(db: Session = Depends(get_db), limit: int = 80):
    """返回行业板块行情（按涨跌幅降序，直接读库）。空库返回 []。"""
    rows = market_service.get_sectors(db, limit=limit)
    result = []
    for r in rows:
        result.append({
            "code": r.sector_code,
            "name": r.sector_name,
            "changePct": r.change_pct,
            "turnover": r.turnover,
            "upCount": r.up_count,
            "downCount": r.down_count,
        })
    return {"data": result}


@router.get("/market/analysis")
def get_market_analysis(db: Session = Depends(get_db)):
    """返回市场深度分析：高低切 / 领涨方向 / 核心驱动因素。"""
    result = market_analysis_service.get_market_analysis(db)
    return {"data": result}
