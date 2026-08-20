"""客户管理服务：CRUD、关系映射校验、角色数据范围。"""
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..config import MAX_SERVICES_PER_CLIENT
from ..models import (
    Client, User, ServiceAssignment, Position,
    ROLE_ADMIN, ROLE_ADVISOR, ROLE_SERVICE, ROLE_USER, SUBROLE_CLIENT,
)
from ..schemas import ClientCreate, ClientUpdate, RelationsUpdate, RelationImportRow, UserCreate
from . import auth_service


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


def create_client(db: Session, data: ClientCreate, creator: User | None = None) -> tuple[Client, dict | None]:
    """创建客户；客服创建时固定将本人加入客服名单。返回 (客户, 登录账号信息)。

    create_login=True 时同时创建 user-client 登录账号并回填 owner_user_id，
    登录账号信息（含一次性初始密码）通过第二个返回值回传给调用方。
    """
    service_ids = list(data.service_ids or [])
    if creator is not None and creator.role == ROLE_SERVICE and creator.id not in service_ids:
        service_ids.insert(0, creator.id)

    advisor, service_ids = validate_relations(db, data.advisor_id, service_ids)
    client_id = data.id or _next_client_id(db)
    if db.get(Client, client_id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "客户编号已存在")

    login = None
    owner_user_id = data.owner_user_id
    if data.create_login:
        user, initial_password = auth_service.create_user(
            db, UserCreate(role=ROLE_USER, sub_role=SUBROLE_CLIENT, name=data.name)
        )
        owner_user_id = user.id
        login = {
            "id": user.id, "username": user.username, "role": user.role,
            "sub_role": user.sub_role, "name": user.name, "email": user.email,
            "is_active": user.is_active, "initial_password": initial_password,
        }

    client = Client(
        id=client_id, name=data.name, age=data.age, risk_level=data.risk_level,
        tags=data.tags or [], note=data.note or "", available_cash=data.available_cash,
        advisor_id=advisor.id, owner_user_id=owner_user_id,
    )
    db.add(client)
    db.flush()
    for sid in service_ids:
        db.add(ServiceAssignment(client_id=client.id, service_id=sid))
    for p in data.positions:
        db.add(Position(
            client_id=client.id, name=p.name, code=p.code, sector=p.sector,
            quantity=p.quantity, cost_price=p.cost_price,
        ))
    db.commit()
    db.refresh(client)
    return client, login


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


def import_relations(db: Session, rows: list[RelationImportRow]) -> dict:
    """批量导入关系映射：client_id 存在则更新关系，否则新建客户。逐行容错。"""
    created = 0
    updated = 0
    errors = []
    for i, row in enumerate(rows):
        line_no = i + 1
        try:
            advisor, service_ids = validate_relations(db, row.advisor_id, row.service_ids)
            client_id = row.client_id or _next_client_id(db)
            client = db.get(Client, client_id)
            if client is None:
                client = Client(id=client_id, name=row.name, advisor_id=advisor.id)
                db.add(client)
                db.flush()
                for sid in service_ids:
                    db.add(ServiceAssignment(client_id=client.id, service_id=sid))
                created += 1
            else:
                client.name = row.name
                client.advisor_id = advisor.id
                client.service_assignments.clear()
                for sid in service_ids:
                    db.add(ServiceAssignment(client_id=client.id, service_id=sid))
                updated += 1
        except HTTPException as exc:
            errors.append({"row": line_no, "detail": exc.detail})
    db.commit()
    return {"created": created, "updated": updated, "errors": errors}


def export_relations(db: Session) -> list[dict]:
    """导出全部客户-顾问-客服关系映射。"""
    clients = db.query(Client).order_by(Client.id).all()
    return [
        {
            "client_id": c.id,
            "name": c.name,
            "advisor_id": c.advisor_id,
            "advisor_name": c.advisor.name if c.advisor else None,
            "service_ids": c.service_ids,
        }
        for c in clients
    ]


def update_client_positions(db: Session, client: Client, positions) -> Client:
    """整体替换客户持仓（前端按可见范围编辑后回写）。"""
    client.positions.clear()
    for p in positions:
        client.positions.append(Position(
            name=p.name, code=p.code, sector=p.sector,
            quantity=p.quantity, cost_price=p.cost_price,
        ))
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
                "quantity": p.quantity, "cost_price": p.cost_price,
                # 现价不再持久化，由实时行情接口提供（前端通过 /api/market/realtime 获取）
            }
            for p in client.positions
        ],
    }
