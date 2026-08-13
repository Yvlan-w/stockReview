"""客户管理服务：CRUD、关系映射校验、角色数据范围。"""
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..config import MAX_SERVICES_PER_CLIENT
from ..models import (
    Client, User, ServiceAssignment, Position,
    ROLE_ADMIN, ROLE_ADVISOR, ROLE_SERVICE, ROLE_USER,
)
from ..schemas import ClientCreate, ClientUpdate, RelationsUpdate


def _get_role_user(db: Session, user_id: str, role: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    if user.role != role:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"用户 {user_id} 不是 {role} 角色",
        )
    return user


def validate_relations(db: Session, advisor_id: str, service_ids: list[str]):
    """校验客户-顾问-客服关系：顾问须为 advisor 角色，客服须为 service 角色，且 1~2 名。"""
    advisor = _get_role_user(db, advisor_id, ROLE_ADVISOR)
    if not service_ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "每个客户至少需分配 1 名客服")
    if len(service_ids) > MAX_SERVICES_PER_CLIENT:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"每个客户最多分配 {MAX_SERVICES_PER_CLIENT} 名客服",
        )
    unique = list(dict.fromkeys(service_ids))
    if len(unique) != len(service_ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "客服分配存在重复")
    for sid in unique:
        _get_role_user(db, sid, ROLE_SERVICE)
    return advisor, unique


def _next_client_id(db: Session) -> str:
    count = db.query(Client).count()
    while True:
        cid = "C" + str(count + 1).zfill(3)
        if db.get(Client, cid) is None:
            return cid
        count += 1


def create_client(db: Session, data: ClientCreate) -> Client:
    advisor, service_ids = validate_relations(db, data.advisor_id, data.service_ids)
    client_id = data.id or _next_client_id(db)
    if db.get(Client, client_id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "客户编号已存在")

    client = Client(
        id=client_id, name=data.name, age=data.age, risk_level=data.risk_level,
        tags=data.tags or [], note=data.note or "", available_cash=data.available_cash,
        advisor_id=advisor.id, owner_user_id=data.owner_user_id,
    )
    db.add(client)
    db.flush()
    for sid in service_ids:
        db.add(ServiceAssignment(client_id=client.id, service_id=sid))
    for p in data.positions:
        db.add(Position(
            client_id=client.id, name=p.name, code=p.code, sector=p.sector,
            quantity=p.quantity, cost_price=p.cost_price, price=p.price,
        ))
    db.commit()
    db.refresh(client)
    return client


def update_client_fields(db: Session, client: Client, data: ClientUpdate) -> Client:
    for field in ("name", "age", "risk_level", "tags", "note", "available_cash"):
        val = getattr(data, field)
        if val is not None:
            setattr(client, field, val)
    db.commit()
    db.refresh(client)
    return client


def update_client_relations(db: Session, client: Client, data: RelationsUpdate) -> Client:
    advisor, service_ids = validate_relations(db, data.advisor_id, data.service_ids)
    client.advisor_id = advisor.id
    client.service_assignments.clear()
    for sid in service_ids:
        db.add(ServiceAssignment(client_id=client.id, service_id=sid))
    db.commit()
    db.refresh(client)
    return client


# ---- 角色数据范围（权限矩阵的数据维） ----
def can_view_client(user: User, client: Client) -> bool:
    if user.role == ROLE_ADMIN:
        return True
    if user.role == ROLE_ADVISOR:
        return client.advisor_id == user.id
    if user.role == ROLE_SERVICE:
        return user.id in client.service_ids
    if user.role == ROLE_USER:
        return client.owner_user_id == user.id
    return False


def list_visible_clients(db: Session, user: User) -> list[Client]:
    if user.role == ROLE_ADMIN:
        return db.query(Client).order_by(Client.id).all()
    if user.role == ROLE_ADVISOR:
        return db.query(Client).filter(Client.advisor_id == user.id).order_by(Client.id).all()
    if user.role == ROLE_SERVICE:
        return (
            db.query(Client)
            .join(ServiceAssignment, ServiceAssignment.client_id == Client.id)
            .filter(ServiceAssignment.service_id == user.id)
            .order_by(Client.id)
            .all()
        )
    if user.role == ROLE_USER:
        return db.query(Client).filter(Client.owner_user_id == user.id).order_by(Client.id).all()
    return []  # guest


def get_visible_client(db: Session, user: User, client_id: str) -> Client:
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "客户不存在")
    if not can_view_client(user, client):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权限访问该客户")
    return client


def serialize_client(client: Client) -> dict:
    return {
        "id": client.id,
        "name": client.name,
        "age": client.age,
        "risk_level": client.risk_level,
        "tags": client.tags or [],
        "note": client.note or "",
        "available_cash": client.available_cash or 0.0,
        "advisor_id": client.advisor_id,
        "advisor_name": client.advisor.name if client.advisor else None,
        "service_ids": client.service_ids,
        "positions": [
            {
                "id": p.id, "name": p.name, "code": p.code, "sector": p.sector,
                "quantity": p.quantity, "cost_price": p.cost_price, "price": p.price,
            }
            for p in client.positions
        ],
    }
