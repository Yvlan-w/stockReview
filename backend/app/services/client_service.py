"""客户管理服务：CRUD、关系映射校验、角色数据范围。"""
import datetime as dt
import logging
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..config import MAX_SERVICES_PER_CLIENT
from ..models import (
    Client, User, ServiceAssignment, Position,
    ROLE_ADMIN, ROLE_ADVISOR, ROLE_SERVICE, ROLE_USER, SUBROLE_CLIENT,
)
from ..schemas import ClientCreate, ClientUpdate, RelationsUpdate, RelationImportRow, UserCreate
from . import auth_service
from . import audit_service as _audit


FIELDS_BASIC = ("name", "age", "risk_level", "tags", "note", "available_cash")
FIELDS_RELATIONS = ("advisor_id", "service_ids")


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


def validate_relations(db: Session, advisor_id: str | None, service_ids: list[str]):
    """校验客户-顾问-客服关系；未分配时允许传空（由自己的 owner 账号管理持仓）。"""
    advisor = _get_role_user(db, advisor_id, ROLE_ADVISOR) if advisor_id else None
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


def create_client(db: Session, data: ClientCreate, creator: User | None = None, *,
                  ip_address: str | None = None,
                  request_user_agent: str | None = None,
                  ) -> tuple[Client, dict | None]:
    """创建客户；客服创建时固定将本人加入客服名单。返回 (客户, 登录账号信息)。

    create_login=True 时同时创建 user-client 登录账号并回填 owner_user_id，
    登录账号信息（含一次性初始密码）通过第二个返回值回传给调用方。

    会写入两条审计日志（若开启 best-effort 降级则不抛异常）：
      - client.create：客户档案本身
      - user.create：同时创建登录账号时（=create_login=True）
    """
    from .audit_service import log_user_activity

    service_ids = list(data.service_ids or [])
    if creator is not None and creator.role == ROLE_SERVICE and creator.id not in service_ids:
        service_ids.insert(0, creator.id)

    advisor, service_ids = validate_relations(db, data.advisor_id, service_ids)
    client_id = data.id or _next_client_id(db)
    if db.get(Client, client_id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "客户编号已存在")

    login = None
    owner_user_id = data.owner_user_id
    created_user_obj = None
    if data.create_login:
        created_user_obj, initial_password = auth_service.create_user(
            db, UserCreate(role=ROLE_USER, sub_role=SUBROLE_CLIENT, name=data.name)
        )
        owner_user_id = created_user_obj.id
        login = {
            "id": created_user_obj.id,
            "username": created_user_obj.username,
            "role": created_user_obj.role,
            "sub_role": created_user_obj.sub_role,
            "name": created_user_obj.name,
            "email": created_user_obj.email,
            "is_active": created_user_obj.is_active,
            "initial_password": initial_password,
        }

    client = Client(
        id=client_id, name=data.name, age=data.age, risk_level=data.risk_level,
        tags=data.tags or [], note=data.note or "", available_cash=data.available_cash,
        advisor_id=advisor.id if advisor is not None else None,
        owner_user_id=owner_user_id,
    )
    db.add(client)
    db.flush()
    for sid in service_ids:
        db.add(ServiceAssignment(client_id=client.id, service_id=sid))
    for p in data.positions:
        db.add(Position(
            client_id=client.id, name=p.name, code=p.code, sector=p.sector,
            quantity=p.quantity, cost_price=p.cost_price,
            opened_date=dt.date.today().isoformat(),
        ))
    db.commit()
    db.refresh(client)

    # ----- 审计：client.create -----
    after_client = {
        **_audit._snapshot_client_basic(client),
        "advisor_id": client.advisor_id,
        "service_ids": list(client.service_ids),
        "owner_user_id": client.owner_user_id,
        "position_count": len(client.positions),
    }
    creator_label = (f"{creator.name}(role={creator.role})") if creator else "系统"
    _audit.log_client_change(
        db, actor=creator, action="client.create", client=client,
        before=None, after=after_client,
        note=f"{creator_label} 开户：name={client.name} advisor_id={client.advisor_id} "
             f"service_ids={client.service_ids} create_login={'是' if data.create_login else '否'}",
        ip_address=ip_address,
    )

    # ----- 审计：同时创建登录账号时再写一条 user.create -----
    if created_user_obj is not None:
        log_user_activity(
            db, actor=creator, action="user.create",
            target_user=created_user_obj,
            note=(f"{creator_label} 开户同步创建 user-client 登录账号："
                  f"username={created_user_obj.username} owner_of_client_id={client.id}"),
            ip_address=ip_address, user_agent=request_user_agent,
        )

    # ----- 账号密码安全分发：客服角色开户时不把账密回传给本人，改以站内信发给管理员 -----
    if created_user_obj is not None and creator is not None and creator.role == ROLE_SERVICE:
        from ..models import (
            User as _U, ROLE_ADMIN, USER_STATUS_DELETED, NOTIF_ACCOUNT_CREDENTIALS,
        )
        from .notification_service import dispatch as _notif_dispatch

        admin_ids = [u.id for u in (
            db.query(_U.id)
            .filter(_U.role == ROLE_ADMIN,
                    _U.is_active.is_(True),
                    _U.status.isnot(USER_STATUS_DELETED))
            .all()
        )]
        if admin_ids and login is not None:
            uname = login.get("username") or created_user_obj.username
            pwd = login.get("initial_password") or ""
            title = "新建客户账户凭证（客服开户）"
            content_lines = [
                f"客服「{creator.name}(@{creator.username})」为客户「{client.name}({client.id})」完成开户，并创建了 user-client 登录账户。",
                "",
                f"• 归属客户：{client.name}（{client.id}）",
                f"• 用户ID：{created_user_obj.id}",
                f"• 用户名：{uname}",
                f"• 初始密码：{pwd}",
                f"• 有效期至：{getattr(created_user_obj, 'expires_at', None) or '—'}",
                "",
                "💡 请及时将以上账号与初始密码安全地转交客户本人，并提醒客户登录后第一时间修改密码。"
                "（出于安全考虑，本次创建的账号密码没有直接展示给客服，仅通过管理员站内信分发一次。）",
            ]
            _notif_dispatch(db, admin_ids, title, "\n".join(content_lines),
                            category=NOTIF_ACCOUNT_CREDENTIALS)
            # 清理返回给客服的敏感字段：用户名留给客服确认，初始密码置空
            login = {**login, "initial_password": None,
                     "redelivered_via_admin_inbox": True}

    return client, login



def update_client_fields(db: Session, client: Client, data: ClientUpdate, *, actor: User | None = None) -> Client:
    before = _audit._snapshot_client_basic(client)
    for field in FIELDS_BASIC:
        val = getattr(data, field, None)
        if val is not None:
            setattr(client, field, val)
    db.commit()
    db.refresh(client)
    after = _audit._snapshot_client_basic(client)
    _audit.log_client_change(
        db, actor=actor, action="client.update", client=client,
        before=before, after=after, fields=FIELDS_BASIC,
    )
    return client


def update_client_relations(db: Session, client: Client, data: RelationsUpdate, *, actor: User | None = None) -> Client:
    advisor, service_ids = validate_relations(db, data.advisor_id, data.service_ids)
    before = _audit._snapshot_client_relations(client)
    client.advisor_id = advisor.id if advisor is not None else None
    client.service_assignments.clear()
    for sid in service_ids:
        db.add(ServiceAssignment(client_id=client.id, service_id=sid))
    db.commit()
    db.refresh(client)
    after = _audit._snapshot_client_relations(client)
    _audit.log_client_change(
        db, actor=actor, action="client.relations", client=client,
        before=before, after=after, fields=FIELDS_RELATIONS,
    )
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
                client = Client(
                    id=client_id, name=row.name,
                    advisor_id=advisor.id if advisor is not None else None,
                )
                db.add(client)
                db.flush()
                for sid in service_ids:
                    db.add(ServiceAssignment(client_id=client.id, service_id=sid))
                created += 1
            else:
                client.name = row.name
                client.advisor_id = advisor.id if advisor is not None else None
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
    """整体替换客户持仓（前端按可见范围编辑后回写）。

    持仓变更后回扫 TTL 内存量新闻，补齐 client_news 关联——确保已入库新闻也能即时联动到
    该客户资讯栏（而非只能等下一条新闻入库）。匹配失败不影响持仓写入结果。
    """
    # 整体替换持仓：先快照旧 code→opened_date，重建时沿用（避免把老持仓误标为今日新建），
    # 新出现的 code 视为今日新建。
    existing_opened = {p.code: p.opened_date for p in client.positions}
    today_str = dt.date.today().isoformat()
    client.positions.clear()
    for p in positions:
        client.positions.append(Position(
            name=p.name, code=p.code, sector=p.sector,
            quantity=p.quantity, cost_price=p.cost_price,
            opened_date=existing_opened.get(p.code) or today_str,
        ))
    db.commit()
    db.refresh(client)
    # 方案二：先 best-effort 填充持仓股票的板块映射（独立 try，绝不被下游异常吞掉），
    # 使下方 Tier2 匹配立即可用（失败不影响主流程）。
    try:
        from . import news_service
        held = {news_service.normalize_code(p.code) for p in positions if p.code}
        if held:
            news_service.refresh_boards_for_codes(held, db)
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            "持仓更新后板块映射刷新失败(client=%s): %s", client.id, e)
    # 持仓变更后回扫 TTL 内存量新闻，补齐 client_news 关联 + 清理失效关联（独立 try）
    try:
        from . import news_service
        added = news_service.match_news_for_client(client.id, db)
        if added:
            logging.getLogger(__name__).info(
                "持仓更新后联动资讯(client=%s): 新增 %d 条关联", client.id, added)
        # 持仓整体替换后，清理因移除持仓而失效的 client_news 行（替代仅靠 TTL 自然清理）
        pruned = news_service.prune_stale_client_news(client.id, db)
        if pruned:
            logging.getLogger(__name__).info(
                "持仓更新后清理失效资讯关联(client=%s): 删除 %d 条", client.id, pruned)
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            "持仓更新后资讯关联失败(client=%s): %s", client.id, e)
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
    service_names = [
        sa.service.name for sa in client.service_assignments
        if sa.service is not None
    ]
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
        "service_names": service_names,
        "positions": [
            {
                "id": p.id, "name": p.name, "code": p.code, "sector": p.sector,
                "quantity": p.quantity, "cost_price": p.cost_price,
            }
            for p in client.positions
        ],
    }
