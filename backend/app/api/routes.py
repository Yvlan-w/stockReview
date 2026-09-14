"""REST API 路由：认证 / 用户 / 客户 / 关系 / 风险预警 / 站内信。"""
import csv
import datetime as dt
import io
from typing import List, Optional
from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import or_, func
from sqlalchemy.orm import Session

from ..core.deps import get_current_user, require_roles
from ..core.security import create_access_token
from ..database import get_db
from ..models import (
    User, Client, Position, RiskAlert, Notification, Transaction, AuditLog,
    NewsItem, ClientNews, StockBoards,
    ROLE_ADMIN, ROLE_SERVICE, ROLE_ADVISOR, ROLE_USER,
    ALERT_OPEN, ALERT_ACK, ALERT_RESOLVED,
    USER_STATUS_DELETED, USER_STATUS_ACTIVE, USER_STATUS_EXPIRED,
    SUBROLE_CLIENT, SUBROLE_NON_CLIENT,
)
from ..schemas import (
    LoginRequest, TokenOut, UserOut, UserCreate, UserCreateOut, UserOptionsOut, UserUpdate,
    ClientCreate, ClientUpdate, RelationsUpdate, PositionsUpdate, ClientOut, ClientCreateOut,
    RiskAlertOut, AlertStatusUpdate, NotificationOut, UnreadCountOut,
    RelationImportRow, RelationExportRow, RelationImportResult,
    TransactionCreate, TransactionOut, PasswordChange, AdjustRequest,
    UserRenew, UserResetPassword, UserStatusPatch, AuditLogPage,
    PortfolioHealthRequest, PortfolioHealthResponse,
    RelatedNewsOut, NewsIngestResultOut, NewsAffectedClientsOut, NewsItemOut,
)
from ..services import auth_service, client_service, risk_engine, notification_service
from ..services import market_service
from ..services import market_analysis_service
from ..services.adjust_service import execute_adjust, reverse_last_transaction, AdjustError, RevokeError
from ..services import cost_basis_service
from ..services.audit_service import log_user_activity
from ..services import module_permission_service as mp_service
from ..services.report_service import build_report, public_stocks
from ..services.report_data_service import resolve_adapter
from ..services.llm_narrative import generate_narrative, resolve_node
from ..services.report_render import render_html_report

router = APIRouter(prefix="/api")


def _serialize_user_out(user: User, db: Session | None = None) -> dict:
    """在返回前补 remaining_days + client_id + 老库 status 兜底。

    - client_id：当 role=user 且 sub_role=client 时，通过 owner_user_id 反查 Client 表
      得到客户档案编号。调用 list_users 等场景下 db 可用时直接查询；调用方没传 db 时
      为 None（避免把 DB 查询逻辑塞进 ORM 模型序列化里）。
    - status：老库可能 status IS NULL（没迁移 NOT NULL 或迁移前遗留），
      在交给 Pydantic 之前先规范化为 USER_STATUS_ACTIVE，避免 UserOut 校验
      （status 字段要求是 str）报错。
    """
    # 先对 ORM 对象的 status 做一次非破坏性兜底（不写 DB，只序列化时替换）
    if getattr(user, "status", None) is None:
        # 不用 user.status = xxx，避免触发 ORM session flush 警告；
        # 转而在 UserOut.model_validate 之前先 by-alias 转 dict 再补默认值
        import copy as _copy
        user_proxy = _copy.copy(user)
        try:
            user_proxy.status = USER_STATUS_ACTIVE
        except Exception:
            user_proxy = user  # fallback：User 类不支持浅拷贝属性赋值时用下面的 payload 兜底
    else:
        user_proxy = user
    payload = UserOut.model_validate(user_proxy).model_dump()
    # Pydantic 默认值与 ORM 赋值都不工作时（比如 User 实例无法浅拷贝 status），
    # 最后在 payload dict 层再兜底一次
    if not payload.get("status"):
        payload["status"] = USER_STATUS_ACTIVE
    payload["remaining_days"] = auth_service.compute_remaining_days(user)
    if (db is not None
            and payload.get("role") == "user"
            and payload.get("sub_role") == "client"
            and payload.get("client_id") in (None, "")):
        linked = (db.query(Client.id)
                  .filter(Client.owner_user_id == user.id)
                  .order_by(Client.created_at.desc())
                  .first())
        if linked:
            payload["client_id"] = linked[0]
    return payload


# ==================== 认证 ====================
@router.post("/auth/login", response_model=TokenOut)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else None
    ua = request.headers.get("User-Agent")
    try:
        user = auth_service.authenticate(db, body.username, body.password)
    except HTTPException as he:
        # 登录失败：记一条审计日志（actor=None，target_user_id=尝试的用户名，便于溯源）
        log_user_activity(
            db, actor=None, action="user.login_failed",
            target_user_id=body.username,
            note=f"用户名={body.username} 原因={he.detail}",
            ip_address=ip, user_agent=ua,
        )
        raise
    # 登录成功
    log_user_activity(
        db, actor=user, action="user.login_success",
        target_user=user,
        note=f"用户 {user.username} 登录成功",
        ip_address=ip, user_agent=ua,
    )
    token = create_access_token(user.id, user.role, user.sub_role)
    return {"access_token": token, "token_type": "bearer", "user": _serialize_user_out(user, db=db)}


@router.get("/auth/me", response_model=UserOut)
def me(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # 每次拉 /me 时同步刷新一次 expired 状态（避免过了零点仍显示"还有 1 天"）
    auth_service.sync_expired_status(db, user)
    return _serialize_user_out(user, db=db)


# ==================== 自助：修改个人密码（所有登录用户均可） ====================
@router.post("/users/me/password")
def change_my_password(body: PasswordChange, request: Request,
                       db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    result = auth_service.change_password(db, user, body.old_password, body.new_password)
    # 修改密码成功：写审计（password 字段 masked；before/after 都是 ***）
    ip = request.client.host if request.client else None
    ua = request.headers.get("User-Agent")
    log_user_activity(
        db, actor=user, action="user.password_change",
        target_user=user,
        note="用户自行修改密码成功",
        ip_address=ip, user_agent=ua,
    )
    return {
        "ok": True,
        "message": "密码修改成功",
        "strength_score": result["strength_score"],
        "strength_label": result["strength_label"],
    }


# ==================== 用户管理（管理员） ====================
@router.get("/users", response_model=List[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_roles(ROLE_ADMIN))):
    # 关键点：使用 .isnot(DELETED) 而不是 != DELETED，
    #   - SQLite 中 `NULL != 'deleted'` → NULL（WHERE 视为 False，会被过滤掉）
    #   - `status IS NOT 'deleted'` → NULL 仍然保留（老数据 status 为空的账户也能显示出来）
    rows = (db.query(User)
            .filter(User.status.isnot(USER_STATUS_DELETED))
            .order_by(User.id).all())
    for u in rows:
        auth_service.sync_expired_status(db, u)
    return [_serialize_user_out(u, db=db) for u in rows]


@router.get("/users/options", response_model=UserOptionsOut)
def user_options(db: Session = Depends(get_db), _: User = Depends(require_roles(ROLE_ADMIN, ROLE_SERVICE))):
    """开户/关系映射表单的顾问、客服下拉选项。"""
    advisors = (db.query(User)
                .filter(User.role == ROLE_ADVISOR, User.is_active.is_(True),
                        User.status.isnot(USER_STATUS_DELETED))
                .order_by(User.id).all())
    services = (db.query(User)
                .filter(User.role == ROLE_SERVICE, User.is_active.is_(True),
                        User.status.isnot(USER_STATUS_DELETED))
                .order_by(User.id).all())
    return {"advisors": advisors, "services": services}


@router.post("/users", response_model=UserCreateOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, request: Request,
                db: Session = Depends(get_db),
                admin_actor: User = Depends(require_roles(ROLE_ADMIN))):
    user, initial_password = auth_service.create_user(db, body)
    ip = request.client.host if request.client else None
    ua = request.headers.get("User-Agent")

    # 当 role=user 且 sub_role=client 时：同步创建 Client 档案并挂 owner_user_id
    # 否则该 user 无法操作持仓（权限矩阵要求 owner_user_id 对应的 Client 存在）
    created_client_id: str | None = None
    if body.role == ROLE_USER and body.sub_role == "client":
        # 管理员账户 Tab 可留空投顾与客服：不自动兜底，此时 client 只挂 owner_user_id，
        # 由账号本人登录后管理自己的持仓（owner 角色数据范围仅依赖 owner_user_id 命中）。
        advisor_id = body.client_advisor_id or None
        service_ids = list(body.client_service_ids or [])

        client_create = ClientCreate(
            name=user.name,
            advisor_id=advisor_id,
            service_ids=service_ids,
            owner_user_id=user.id,
            risk_level=body.client_risk_level,
            available_cash=body.client_available_cash,
            create_login=False,  # 登录账号已经创建，别再绕回来
        )
        # 复用 create_client 的事务 + 审计逻辑（client.create + user.create 开户同步那条；
        # user.create 我们已经在下面单独写过一次，create_client 内部那条用 target_user=None 避免重复）
        created_client, _ = client_service.create_client(
            db, client_create, creator=admin_actor,
            ip_address=ip, request_user_agent=ua,
        )
        created_client_id = created_client.id

    # 创建新用户：写审计（before=None，after=新用户快照）
    note = f"Admin {admin_actor.username} 创建新用户：role={user.role} username={user.username}"
    if created_client_id:
        note += f" （同步生成客户档案 {created_client_id}）"
    log_user_activity(
        db, actor=admin_actor, action="user.create",
        target_user=user,
        note=note,
        ip_address=ip, user_agent=ua,
    )
    out = UserCreateOut.model_validate(user)
    out.initial_password = initial_password
    out.client_id = created_client_id
    payload = out.model_dump()
    # 与其他 UserOut 系列响应保持一致：补 remaining_days + client_id 兜底查询
    payload["remaining_days"] = auth_service.compute_remaining_days(user)
    if (db is not None
            and payload.get("role") == "user"
            and payload.get("sub_role") == "client"
            and payload.get("client_id") in (None, "")):
        linked = (db.query(Client.id)
                  .filter(Client.owner_user_id == user.id)
                  .order_by(Client.created_at.desc())
                  .first())
        if linked:
            payload["client_id"] = linked[0]
    return payload


@router.put("/users/{user_id}", response_model=UserOut)
def update_user_route(user_id: str, body: UserUpdate, request: Request,
                      db: Session = Depends(get_db),
                      admin_actor: User = Depends(require_roles(ROLE_ADMIN))):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    # before 快照必须取在修改前，字段口径与 _snapshot_user 完全对齐避免假 diff
    before = {
        "id": target.id, "username": target.username, "name": target.name,
        "email": getattr(target, "email", None) or "",
        "role": target.role, "is_active": bool(target.is_active),
    }
    # sub_role 仅 User 角色有值时才放进去，避免每次非 user 角色都显示 sub_role: None→None 的假 diff
    sub_role = getattr(target, "sub_role", None)
    if sub_role is not None:
        before["sub_role"] = sub_role
    updated = auth_service.update_user(db, target, body)
    ip = request.client.host if request.client else None
    log_user_activity(
        db, actor=admin_actor, action="user.update", target_user=updated,
        before=before, fields=("name", "email", "role", "sub_role", "is_active"),
        note=f"Admin {admin_actor.username} 更新用户 {updated.username} 资料",
        ip_address=ip,
    )
    return updated


# —— 注意：FastAPI 路由匹配按注册顺序，`/users/me` 必须放在 `/users/{user_id}` 之前，否则会被路径参数吞掉 404 ——
@router.delete("/users/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_self_route(request: Request,
                      user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    """当前登录的普通用户删除自己的账户（软删除）。

    仅 ROLE_USER（用户·普通 / 用户·客户）允许调用；
    管理员 / 客服 / 投顾角色禁止（此入口是给用户自助注销用的）。
    """
    if user.role != ROLE_USER:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "当前角色不允许自助删除账户，请联系管理员处理",
        )
    before = {
        "id": user.id, "username": user.username, "name": user.name,
        "email": getattr(user, "email", None) or "",
        "role": user.role, "is_active": bool(user.is_active),
        "status": getattr(user, "status", "active"),
        "expires_at": getattr(user, "expires_at", None).isoformat() if getattr(user, "expires_at", None) else None,
    }
    sub_role = getattr(user, "sub_role", None)
    if sub_role is not None:
        before["sub_role"] = sub_role
    ip = request.client.host if request.client else None
    auth_service.delete_user(db, user)
    log_user_activity(
        db, actor=user, action="user.delete_self", target_user=user,
        before=before,
        note=f"User {before['username']} 自助注销账户（软删除）",
        ip_address=ip,
    )
    return None


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user_route(user_id: str, request: Request,
                      db: Session = Depends(get_db),
                      admin_actor: User = Depends(require_roles(ROLE_ADMIN))):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    # 先取快照再软删（删完 is_active=false，仍在 DB）
    before = {
        "id": target.id, "username": target.username, "name": target.name,
        "email": getattr(target, "email", None) or "",
        "role": target.role, "is_active": bool(target.is_active),
        "status": getattr(target, "status", "active"),
        "expires_at": getattr(target, "expires_at", None).isoformat() if getattr(target, "expires_at", None) else None,
    }
    sub_role = getattr(target, "sub_role", None)
    if sub_role is not None:
        before["sub_role"] = sub_role
    ip = request.client.host if request.client else None
    auth_service.delete_user(db, target)
    log_user_activity(
        db, actor=admin_actor, action="user.delete", target_user=target,
        before=before,
        note=f"Admin {admin_actor.username} 软删除用户 {before['username']}",
        ip_address=ip,
    )
    return None


@router.delete("/users/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_self_route(request: Request,
                      user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    """当前登录的普通用户删除自己的账户（软删除）。

    仅 ROLE_USER（用户·普通 / 用户·客户）允许调用；
    管理员 / 客服 / 投顾角色禁止（此入口是给用户自助注销用的）。
    """
    if user.role != ROLE_USER:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "当前角色不允许自助删除账户，请联系管理员处理",
        )
    before = {
        "id": user.id, "username": user.username, "name": user.name,
        "email": getattr(user, "email", None) or "",
        "role": user.role, "is_active": bool(user.is_active),
        "status": getattr(user, "status", "active"),
        "expires_at": getattr(user, "expires_at", None).isoformat() if getattr(user, "expires_at", None) else None,
    }
    sub_role = getattr(user, "sub_role", None)
    if sub_role is not None:
        before["sub_role"] = sub_role
    ip = request.client.host if request.client else None
    auth_service.delete_user(db, user)
    log_user_activity(
        db, actor=user, action="user.delete_self", target_user=user,
        before=before,
        note=f"User {before['username']} 自助注销账户（软删除）",
        ip_address=ip,
    )
    return None


# ---- 账户生命周期管理（充值续费、重置密码、生命周期 patch）----
@router.post("/users/{user_id}/renew", response_model=UserOut)
def renew_user_route(user_id: str, body: UserRenew, request: Request,
                     db: Session = Depends(get_db),
                     admin_actor: User = Depends(require_roles(ROLE_ADMIN))):
    """充值续费：预留账户续费接口。"""
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    before = {
        "status": getattr(target, "status", "active"),
        "expires_at": target.expires_at.isoformat() if target.expires_at else None,
        "remaining_days_before": auth_service.compute_remaining_days(target),
    }
    updated = auth_service.renew_user(db, target, body.extend_days)
    ip = request.client.host if request.client else None
    log_user_activity(
        db, actor=admin_actor, action="user.renew", target_user=updated,
        before=before,
        fields=("status", "expires_at"),
        note=f"Admin {admin_actor.username} 续费账户 {updated.username} {body.extend_days:+d} 天",
        ip_address=ip, user_agent=request.headers.get("User-Agent"),
    )
    return _serialize_user_out(updated, db=db)


@router.post("/users/{user_id}/reset-password")
def reset_password_route(user_id: str, body: UserResetPassword, request: Request,
                         db: Session = Depends(get_db),
                         admin_actor: User = Depends(require_roles(ROLE_ADMIN))):
    """管理员一键重置密码；返回新密码明文一次。"""
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    new_pwd, generated = auth_service.reset_user_password(db, target, body.password)
    ip = request.client.host if request.client else None
    log_user_activity(
        db, actor=admin_actor, action="user.password_reset", target_user=target,
        note=f"Admin {admin_actor.username} 重置 {target.username} 密码（{'自动生成' if generated else '指定'}）",
        ip_address=ip,
    )
    return {
        "ok": True,
        "new_password": new_pwd,
        "generated": generated,
    }


@router.patch("/users/{user_id}/lifecycle", response_model=UserOut)
def lifecycle_patch(user_id: str, body: UserStatusPatch, request: Request,
                    db: Session = Depends(get_db),
                    admin_actor: User = Depends(require_roles(ROLE_ADMIN))):
    """管理账户生命周期：改 status / 过期日 / 或直接设置新的 license_days。"""
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    before = {
        "status": getattr(target, "status", "active"),
        "expires_at": target.expires_at.isoformat() if target.expires_at else None,
        "remaining_days": auth_service.compute_remaining_days(target),
    }
    updated = auth_service.patch_user_lifecycle(
        db, target, new_status=body.status, expires_at=body.expires_at, license_days=body.license_days,
    )
    ip = request.client.host if request.client else None
    log_user_activity(
        db, actor=admin_actor, action="user.lifecycle", target_user=updated,
        before=before, fields=("status", "expires_at"),
        note=(f"Admin {admin_actor.username} 修改 {updated.username} 生命周期："
              f"status={body.status} license_days={body.license_days} expires_at={body.expires_at}"),
        ip_address=ip,
    )
    return _serialize_user_out(updated, db=db)


@router.post("/users/{user_id}/ensure-client-profile")
def ensure_client_profile_route(user_id: str, request: Request,
                                db: Session = Depends(get_db),
                                admin_actor: User = Depends(require_roles(ROLE_ADMIN))):
    """补齐/迁移用户到「客户」子角色，并保证拥有一个绑定的客户档案。

    行为分两类：
      1) role=user + sub_role=client ：已在客户角色，但缺少 Client 档案 → 建档（原接口语义，保持不变）
      2) role=user + sub_role=non_client ：是「非客户普通用户」，管理员点「分配关系」后
         需要先转为客户 → sub_role 改成 client → 再创建档案并绑定 owner_user_id。

    对 advisor/service/admin 等非 user 角色仍直接拒绝。

    返回:
      - client_id            已有的 / 新创建的客户编号
      - created              True 表示本次新建, False 表示已存在直接复用
      - role_changed         True 当本次把 sub_role 从 non_client 改为 client（前端据此提示）
    """
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    if target.status == USER_STATUS_DELETED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "该账户已删除，无法补齐客户档案")
    if target.role != ROLE_USER:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"仅 user 角色可以建立客户档案（当前 role={target.role}）",
        )

    # 情况 B：sub_role=non_client 的普通用户 → 管理员分配关系时自动升级为 client
    role_changed = False
    if target.sub_role is None or target.sub_role == SUBROLE_NON_CLIENT:
        target.sub_role = SUBROLE_CLIENT
        db.flush()
        role_changed = True

    # 先看有没有已经通过 owner_user_id 关联的客户档案
    existing = (db.query(Client)
                .filter(Client.owner_user_id == target.id)
                .order_by(Client.created_at.desc())
                .first())
    if existing is not None:
        db.commit()
        return {"client_id": existing.id, "created": False, "role_changed": role_changed}

    # 没有 → 自动创建一个；以用户姓名命名，不强制分配顾问/客服（后续在「分配关系」里再设置）
    ip = request.client.host if request.client else None
    ua = request.headers.get("User-Agent")
    client_create = ClientCreate(
        name=target.name,
        owner_user_id=target.id,
        advisor_id=None,
        service_ids=[],
        create_login=False,
    )
    created_client, _ = client_service.create_client(
        db, client_create, creator=admin_actor,
        ip_address=ip, request_user_agent=ua,
    )
    # 写审计（与开户同步建立档案保持一致）
    log_user_activity(
        db, actor=admin_actor, action="user.create_client_profile",
        target_user=target,
        note=(
            f"Admin {admin_actor.username} 为 {target.username} 补齐客户档案 {created_client.id}"
            + ("（本次同步将 sub_role 从 non_client → client）" if role_changed else "")
        ),
        ip_address=ip, user_agent=ua,
    )
    return {"client_id": created_client.id, "created": True, "role_changed": role_changed}


# ==================== 客户 ====================
@router.get("/clients", response_model=List[ClientOut])
def list_clients(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    clients = client_service.list_visible_clients(db, user)
    return [client_service.serialize_client(c) for c in clients]


# 客户盈亏汇总缓存：{user_id: (过期时间戳, 响应体)}。
# 客户列表筛选会反复渲染，15s 内存缓存避免每次全量重算组合估值。
_client_summary_cache: dict[str, tuple[float, dict]] = {}
_CLIENT_SUMMARY_CACHE_TTL = 15.0  # 秒


@router.get("/clients/summaries")
def list_client_summaries(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """批量返回当前用户可见客户的实时盈亏摘要（客户列表用）。

    每客户一次 compute_portfolio（实时价格降级链），结果按用户缓存 15 秒。
    注意：必须注册在 /clients/{client_id} 之前，否则 summaries 会被当作路径参数。
    """
    import time

    now = time.monotonic()
    cached = _client_summary_cache.get(user.id)
    if cached and cached[0] > now:
        data = dict(cached[1])
        data["cached"] = True
        return data

    clients = client_service.list_visible_clients(db, user)
    summaries = {}
    for c in clients:
        try:
            from ..services.pnl_service import compute_portfolio
            p = compute_portfolio(db, c)
            summaries[c.id] = {
                "totalMarketValue": p["totalMarketValue"],
                "totalPnl": p["totalPnl"],
                "totalPnlPct": p["totalPnlPct"],
                # 持仓盈亏（仅当前持仓浮动盈亏，不含已卖出历史收益）
                "totalFloatingPnl": p["totalFloatingPnl"],
                "floatingPnlPct": p["floatingPnlPct"],
                "totalAssets": p["totalAssets"],
            }
        except Exception as e:  # 单客户失败不影响其他客户
            summaries[c.id] = None
            print(f"[client-summaries] 客户 {c.id} 盈亏计算失败: {e}")

    result = {
        "summaries": summaries,
        "updated_at": dt.datetime.now().isoformat(),
        "cached": False,
    }
    _client_summary_cache[user.id] = (now + _CLIENT_SUMMARY_CACHE_TTL, result)
    return result


@router.post("/clients", response_model=ClientCreateOut, status_code=status.HTTP_201_CREATED)
def create_client(body: ClientCreate, request: Request,
                  db: Session = Depends(get_db),
                  user: User = Depends(require_roles(ROLE_ADMIN, ROLE_SERVICE))):
    ip = request.client.host if request.client else None
    ua = request.headers.get("User-Agent")
    client, login = client_service.create_client(db, body, creator=user,
                                                 ip_address=ip, request_user_agent=ua)
    result = client_service.serialize_client(client)
    result["login"] = login
    return result


@router.delete("/clients/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_client(client_id: str, request: Request, db: Session = Depends(get_db),
                  actor: User = Depends(require_roles(ROLE_ADMIN))):
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "客户不存在")
    before = {
        **client_service._audit._snapshot_client_basic(client),
        "advisor_id": client.advisor_id,
        "service_ids": list(client.service_ids),
        "owner_user_id": client.owner_user_id,
        "position_count": len(client.positions),
    }
    db.delete(client)
    db.commit()
    # 审计：client.delete
    client_service._audit.log_client_change(
        db, actor=actor, action="client.delete",
        # delete 后对象 detached，构造最小 fake client 仅提供 id 字段给 log_client_change 使用
        client=type("__DeletedClient", (), {"id": client_id})(),
        before=before, after=None,
        note=f"Admin {actor.name} 删除客户 {before['name']}(id={client_id})，"
             f"关联持仓 {before['position_count']} 条、客服 {before['service_ids']}",
        ip_address=(request.client.host if request.client else None),
    )
    return None


@router.get("/clients/{client_id}", response_model=ClientOut)
def get_client(client_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    client = client_service.get_visible_client(db, user, client_id)
    return client_service.serialize_client(client)


@router.put("/clients/{client_id}", response_model=ClientOut)
def update_client(
    client_id: str,
    body: ClientUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    client = client_service.get_visible_client(db, user, client_id)

    # ---- 字段级权限校验（避免 advisor / 用户自身修改 name/age/risk_level 等管理字段）----
    # 允许的字段集合按角色拆分：
    # - ADMIN / SERVICE：FIELDS_BASIC 全量（name/age/risk_level/tags/note/available_cash）
    # - ADVISOR / 客户本人（ROLE_USER 且 client.owner_user_id == user.id）：仅 tags / note
    body_dict = body.model_dump(exclude_unset=True)
    ALLOWED_ADV_OR_SELF = {"tags", "note"}
    if user.role not in (ROLE_ADMIN, ROLE_SERVICE):
        for key in body_dict.keys():
            if key not in ALLOWED_ADV_OR_SELF:
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    f"无权限修改字段「{key}」，当前角色仅可修改：{', '.join(sorted(ALLOWED_ADV_OR_SELF))}",
                )
        if user.role == ROLE_ADVISOR:
            # advisor 只能改自己负责的客户（get_visible_client 已确保 advisor_id==user.id，但仍兜底校验）
            if client.advisor_id != user.id:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "仅可修改自己所负责客户的标签与备注")
        if user.role == ROLE_USER:
            # 客户本人：仅能改自己档案（owner_user_id == user.id）
            if client.owner_user_id != user.id:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "仅可修改本人客户档案的标签与备注")

    client = client_service.update_client_fields(db, client, body, actor=user)
    return client_service.serialize_client(client)


@router.put("/clients/{client_id}/positions", response_model=ClientOut)
def update_client_positions(client_id: str, body: PositionsUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    client = client_service.get_visible_client(db, user, client_id)
    client = client_service.update_client_positions(db, client, body.positions)
    return client_service.serialize_client(client)


# ==================== 关系映射（管理员） ====================
@router.put("/clients/{client_id}/relations", response_model=ClientOut)
def update_relations(client_id: str, body: RelationsUpdate, db: Session = Depends(get_db), user: User = Depends(require_roles(ROLE_ADMIN))):
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "客户不存在")
    client = client_service.update_client_relations(db, client, body, actor=user)
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
    # 前端仅展示未解决的预警（open / acknowledged）；已解决不显示，避免列表干扰
    return db.query(RiskAlert).filter(
        RiskAlert.client_id == client_id,
        RiskAlert.status != ALERT_RESOLVED,
    ).order_by(RiskAlert.id.desc()).all()


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


@router.get("/notifications/{notification_id}", response_model=NotificationOut)
def get_notification(notification_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """获取单个站内信详情（打开详情时自动标记已读）。"""
    n = db.get(Notification, notification_id)
    if n is None or n.recipient_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "消息不存在")
    if not n.is_read:
        n.is_read = True
        db.commit()
        db.refresh(n)
    return n


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


# ==================== 个股行情 ====================
@router.get("/stocks/prices")
def get_stock_prices_api(codes: str = "", db: Session = Depends(get_db)):
    """批量查询个股实时行情（?codes=600519,000001）。

    不传 codes 参数时自动查询所有活跃持仓股票。
    """
    from ..services.stock_price_service import get_stock_prices as _get, get_all_holding_codes
    if codes.strip():
        code_list = [c.strip() for c in codes.split(",") if c.strip()]
    else:
        code_list = get_all_holding_codes(db)

    prices = _get(db, code_list)
    return {
        "prices": {
            code: data for code, data in prices.items() if data is not None
        },
        "updated_at": dt.datetime.utcnow().isoformat(),
    }


@router.get("/stocks/search")
async def search_stocks_api(
    keyword: str = "",
    limit: int = 8,
    db: Session = Depends(get_db),
):
    """按代码 / 名称 / 拼音搜索股票（添加持仓自动填充用，公开接口）。

    返回 [{code, name, price, change_pct, industry, sector}]；
    sector 为映射到前端板块枚举的行业分类（匹配不上为 null）。
    """
    from ..services.stock_price_service import search_stocks as _search
    results = await _search(db, keyword, limit=limit)
    return {"results": results}


# ==================== 组合估值 / 盈亏 ====================
@router.get("/clients/{client_id}/portfolio")
def get_client_portfolio(
    client_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """返回客户的实时组合估值（含实时价格、总盈亏、今日盈亏）。"""
    client = client_service.get_visible_client(db, user, client_id)
    from ..services.pnl_service import compute_portfolio
    portfolio = compute_portfolio(db, client)
    return portfolio


@router.get("/clients/{client_id}/pnl-history")
def get_client_pnl_history(
    client_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    range: int = 30,
):
    """返回客户最近 N 天的盈亏历史（收益曲线数据）。"""
    client = client_service.get_visible_client(db, user, client_id)
    from ..services.pnl_service import get_pnl_history
    return get_pnl_history(db, client.id, range_days=range)


@router.post("/admin/snapshots/recalculate")
def recalculate_snapshots_api(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    client_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
):
    """用历史K线收盘价重算盈亏快照（管理员，数据修复机制）。

    Args:
        client_id: 指定客户ID，为空则全部客户
        start_date: 起始日期 YYYY-MM-DD（默认：最早交易记录日期）
        end_date: 截止日期 YYYY-MM-DD（默认：今天）
    """
    from ..core.deps import require_roles
    require_roles(ROLE_ADMIN)(user)
    from ..services.pnl_service import recalculate_snapshots
    return recalculate_snapshots(db, client_id=client_id,
                                 start_date=start_date, end_date=end_date)


@router.get("/admin/snapshots/validate")
def validate_snapshots_api(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    client_id: Optional[str] = None,
):
    """校验快照数据完整性（管理员）：缺失交易日 / 非交易日快照 / 连续同值 / NULL 值。"""
    from ..core.deps import require_roles
    require_roles(ROLE_ADMIN)(user)
    from ..services.pnl_service import validate_snapshot_integrity
    return validate_snapshot_integrity(db, client_id=client_id)


@router.post("/stocks/refresh")
async def trigger_stock_refresh(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """手动触发一次个股行情刷新（管理员）。"""
    from ..core.deps import require_roles
    require_roles(ROLE_ADMIN)(user)
    from ..services.stock_price_service import refresh_stock_prices
    ok = await refresh_stock_prices(db)
    return {"status": "ok" if ok else "fail"}


@router.post("/stocks/refresh-daily-kline")
async def trigger_daily_kline_refresh(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = 90,
):
    """手动触发日K线数据刷新，导入真实市场数据到 stock_daily_price 表（管理员）。

    Args:
        limit: 拉取的历史天数，默认90天
    """
    from ..core.deps import require_roles
    require_roles(ROLE_ADMIN)(user)
    from ..services.stock_price_service import refresh_all_daily_klines
    result = await refresh_all_daily_klines(db, limit=limit)
    return {"status": "ok", "data": result}


# ==================== 系统配置（手续费等） ====================

@router.get("/settings/trading-fees")
def get_trading_fees(user: User = Depends(get_current_user)):
    """获取当前手续费配置（所有登录用户可读）。"""
    from .. import config
    return {
        "commission_rate": config.TRADING_FEE_COMMISSION,
        "min_commission": config.TRADING_FEE_MIN_COMMISSION,
        "stamp_tax_rate": config.TRADING_FEE_STAMP_TAX,
        "transfer_fee_rate": config.TRADING_FEE_TRANSFER_FEE,
        "description": {
            "commission": "佣金费率（双边收取，最低5元）",
            "stamp_tax": "印花税（仅卖出收取）",
            "transfer_fee": "过户费（沪深两市双边收取）",
        }
    }


@router.put("/settings/trading-fees")
def update_trading_fees(
    body: dict,
    user: User = Depends(get_current_user),
):
    """更新手续费配置（管理员）。修改后立即生效（进程内）。"""
    from ..core.deps import require_roles
    require_roles(ROLE_ADMIN)(user)
    from .. import config

    if "commission_rate" in body:
        config.TRADING_FEE_COMMISSION = float(body["commission_rate"])
    if "min_commission" in body:
        config.TRADING_FEE_MIN_COMMISSION = float(body["min_commission"])
    if "stamp_tax_rate" in body:
        config.TRADING_FEE_STAMP_TAX = float(body["stamp_tax_rate"])
    if "transfer_fee_rate" in body:
        config.TRADING_FEE_TRANSFER_FEE = float(body["transfer_fee_rate"])

    return {
        "status": "ok",
        "commission_rate": config.TRADING_FEE_COMMISSION,
        "min_commission": config.TRADING_FEE_MIN_COMMISSION,
        "stamp_tax_rate": config.TRADING_FEE_STAMP_TAX,
        "transfer_fee_rate": config.TRADING_FEE_TRANSFER_FEE,
    }


# ==================== 交易记录 ====================
@router.post("/clients/{client_id}/transactions", response_model=TransactionOut, status_code=status.HTTP_201_CREATED)
def create_transaction(
    client_id: str,
    body: TransactionCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """创建交易记录（买入/卖出）。卖出时会自动计算已实现盈亏（含手续费）。"""
    client = client_service.get_visible_client(db, user, client_id)

    # 确定交易日期
    trade_date = body.trade_date or dt.date.today().isoformat()

    # 手续费计算异常（范围非法等）统一转 422
    try:
        return _create_transaction_impl(db, client_id, body, trade_date)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))


def _create_transaction_impl(db: Session, client_id: str, body, trade_date: str) -> Transaction:

    # 计算已实现盈亏（仅卖出时，含手续费）
    realized_pnl = 0.0
    if body.action == "sell":
        from ..services.pnl_service import calc_realized_pnl_with_fee
        # 从现有持仓获取成本价（如果未提供 cost_price）
        cost_price = body.cost_price
        if cost_price is None:
            position = db.query(Position).filter(
                Position.client_id == client_id,
                Position.code == body.code,
            ).first()
            if position:
                cost_price = position.cost_price
            else:
                cost_price = 0.0

        # 使用含手续费的计算
        pnl_result = calc_realized_pnl_with_fee(
            buy_price=cost_price,
            sell_price=body.price,
            quantity=body.quantity,
            fee_mode=body.fee_mode,
            fee_value=body.fee_value,
        )
        realized_pnl = pnl_result["net_pnl"]  # 净盈亏（扣除所有手续费）

    # 买入：按指定模式（或全局默认配置）计算本笔手续费
    fee_amount = 0.0
    if body.action == "buy":
        from ..services.pnl_service import calc_buy_fee
        buy_fee = calc_buy_fee(body.price * body.quantity, body.fee_mode, body.fee_value)
        fee_amount = buy_fee["total_fee"]
    elif body.action == "sell":
        fee_amount = pnl_result.get("fee_amount", 0.0)

    tx = Transaction(
        client_id=client_id,
        code=body.code,
        name=body.name,
        market=cost_basis_service.infer_market_from_code(body.code),
        action=body.action,
        quantity=body.quantity,
        price=body.price,
        cost_price=body.cost_price,
        fee_mode=body.fee_mode,
        fee_value=body.fee_value,
        fee_amount=fee_amount,
        realized_pnl=realized_pnl,
        trade_date=trade_date,
        executed_at=dt.datetime.utcnow(),
        source=body.source or "manual",
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return tx


@router.get("/clients/{client_id}/transactions", response_model=List[TransactionOut])
def list_transactions(
    client_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = 100,
):
    """获取客户的交易记录列表。"""
    client = client_service.get_visible_client(db, user, client_id)
    transactions = db.query(Transaction).filter(
        Transaction.client_id == client_id
    ).order_by(Transaction.executed_at.desc().nullslast(), Transaction.id.desc()).limit(limit).all()
    return transactions


# ========== 调仓执行（含成本批次与 3 种成本法） ==========
@router.post("/clients/{client_id}/adjust", status_code=201)
def do_adjust(client_id: str, body: AdjustRequest, db: Session = Depends(get_db),
              user: User = Depends(require_roles(ROLE_ADMIN, ROLE_SERVICE, ROLE_ADVISOR, ROLE_USER))):
    """执行一笔调仓（买入/卖出），原子更新：交易流水 + 成本批次 + 持仓 + 现金 + 当日快照。

    角色范围：
      - ROLE_USER(客户) 仅能操作自己 owner_user_id 名下的客户档案
      - 其余三角色按既有数据范围（advisor/service 只看自己的客户）受 get_visible_client 进一步约束
    """
    client = client_service.get_visible_client(db, user, client_id)
    try:
        result = execute_adjust(
            db, client,
            code=body.code, name=body.name, sector=body.sector,
            action=body.action, quantity=body.quantity, price=body.price,
            fee_mode=body.fee_mode, fee_value=body.fee_value,
            trade_date=body.trade_date, executed_at=body.executed_at,
            actor=user,
            from_cash=body.from_cash,
            cost_method=body.cost_method,  # type: ignore[arg-type]
            skip_if_duplicate=body.skip_if_duplicate,
            source=body.source,
        )
    except AdjustError as e:
        raise HTTPException(422, detail=str(e)) from e
    tx = result["transaction"]
    pos_out = None
    if result["position"] is not None:
        p = result["position"]
        pos_out = {
            "code": p.code, "name": p.name, "quantity": p.quantity,
            "cost_price": p.cost_price,
        }
    return {
        "transaction": TransactionOut.model_validate(tx).model_dump(),
        "position": pos_out,
        "available_cash": result["available_cash"],
        "portfolio": result.get("portfolio"),
        "cost_basis_matches": result.get("cost_basis_matches", []),
        "cost_method": result.get("cost_method", body.cost_method),
        "duplicate": bool(result.get("duplicate", False)),
    }


# ========== 撤销某持仓最近一笔操作（精确批次反转） ==========
@router.post("/clients/{client_id}/positions/{code}/revoke", status_code=200)
def revoke_position(
    client_id: str,
    code: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """撤销某持仓最近一笔操作（按时间倒序取最新一条），精确批次反转。

    语义（满足"仅删除目标批次记录，更早批次完整保留" + "跨批次禁止撤销"）：
      - 该笔为 'adjust'（编辑产生的复盘记录）→ 仅删除该记录；
      - 该笔为 'sell' 且 matched_lots 横跨多个买入批次 → 返回 409 并说明原因，禁止撤销；
      - 该笔为 'sell'（单批次）→ 恢复持仓数量、回扣现金、回补对应成本批次，删除该卖出流水；
      - 该笔为 'buy' → 退回现金、删除成本批次、减少/删除持仓行，删除该买入流水。
    """
    client = client_service.get_visible_client(db, user, client_id)
    try:
        result = reverse_last_transaction(db, client, code)
    except RevokeError as e:
        raise HTTPException(getattr(e, "status_code", 400), detail=str(e)) from e

    # 撤销后尽力刷新当日快照（失败不影响已提交的撤销事务）
    try:
        import logging as _logging
        from ..services.pnl_service import compute_portfolio, write_daily_snapshot
        pf = compute_portfolio(db, client)
        write_daily_snapshot(db, client, pf)
    except Exception as e:  # noqa: BLE001
        _logging.getLogger(__name__).warning("撤销后快照更新失败(client=%s, %s): %s", client.id, code, e)
    return result


# ========== 策略复盘：成本基础汇总（3 种方法切换） ==========
@router.get("/clients/{client_id}/cost-basis")
def get_cost_basis(client_id: str, method: str = "average",
                   code: Optional[str] = None,
                   db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """查询客户的成本基础汇总（含未实现盈亏），支持 average / fifo / lifo 三种方法。

    fifo / lifo 基于 CostBasisLot 实际剩余批次 × 含费单位成本，
    average 基于 position.cost_price 移动加权平均口径。
    """
    if method not in ("average", "fifo", "lifo"):
        raise HTTPException(422, "method 必须为 average / fifo / lifo")
    client = client_service.get_visible_client(db, user, client_id)
    return cost_basis_service.summarize_cost_basis(
        db, client, code=code, method=method,  # type: ignore[arg-type]
    )


# ========== 策略复盘：交易流水按股票分组汇总 ==========
@router.get("/clients/{client_id}/transactions/summary")
def get_transactions_summary(client_id: str, code: Optional[str] = None,
                             db: Session = Depends(get_db),
                             user: User = Depends(get_current_user)):
    """按股票分组汇总交易流水统计：买卖量额、累计已实现盈亏、手续费分类总计。"""
    client = client_service.get_visible_client(db, user, client_id)
    return cost_basis_service.summarize_transactions(db, client, code=code)


# ========== 审计日志列表（后台「查看日志」Tab，管理员专用） ==========
ACTION_FILTER_HINT = (
    "支持过滤：action 完整值（如 user.create / client.update / login_success / adjust 等）。"
)


@router.get("/audit-logs", response_model=AuditLogPage)
def list_audit_logs(
    page: int = 1,
    page_size: int = 50,
    keyword: Optional[str] = None,      # 通配：actor_name / note / target_id / target_type
    action: Optional[str] = None,       # 精确 action 过滤
    target_type: Optional[str] = None,  # 精确 target_type 过滤 (user / client / tx)
    actor_id: Optional[str] = None,     # 精确操作人 ID
    start: Optional[dt.datetime] = None,   # 开始时间（>=）
    end: Optional[dt.datetime] = None,     # 结束时间（<=）
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(ROLE_ADMIN)),
):
    """管理员「查看日志」查询：按时间倒序分页返回完整 audit_logs。"""
    page = max(1, page)
    page_size = max(1, min(500, page_size))
    q = db.query(AuditLog)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(or_(
            AuditLog.actor_name.ilike(like),
            AuditLog.note.ilike(like),
            AuditLog.target_id.ilike(like),
            func.coalesce(AuditLog.target_type, "").ilike(like),
            AuditLog.action.ilike(like),
            func.coalesce(AuditLog.ip_address, "").ilike(like),
        ))
    if action:
        q = q.filter(AuditLog.action == action)
    if target_type:
        q = q.filter(AuditLog.target_type == target_type)
    if actor_id:
        q = q.filter(AuditLog.actor_user_id == actor_id)
    if start:
        q = q.filter(AuditLog.created_at >= start)
    if end:
        q = q.filter(AuditLog.created_at <= end)
    total = q.count()
    items = (q.order_by(AuditLog.id.desc())
             .offset((page - 1) * page_size)
             .limit(page_size)
             .all())
    return {"total": total, "items": items}


# ==================== 模块可见性（板块 / 页面模块显隐） ====================
class ModuleVisibilitySet(BaseModel):
    scope_type: str = Field(..., pattern="^(role|account)$", description="'role' 或 'account'")
    scope_id: str = Field(..., description="角色名 或 账户ID")
    module_key: str = Field(..., description="模块标识（见模块注册表）")
    visible: bool = Field(..., description="True=显示 / False=隐藏")


@router.get("/modules/visible")
def get_visible_modules(
    role: Optional[str] = Query(default=None, description="指定角色查询其角色级可见性（不含账户覆盖）"),
    account_id: Optional[str] = Query(default=None, description="指定账户查询其完整有效可见性"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """统一查询接口：按「角色或账户标识」返回可见 / 隐藏模块列表。

    - 提供 account_id：返回该账户的完整有效可见性（账户级 > 角色级 > 默认）；
    - 仅提供 role：返回该角色的角色级可见性；
    - 都不提供：返回当前登录用户的完整有效可见性。
    """
    if account_id:
        # 普通用户只能查自己；其余账户需管理员
        if user.role != ROLE_ADMIN and str(user.id) != str(account_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权限查询该账户的模块可见性")
        target = db.get(User, account_id)
        if not target:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="账户不存在")
        eff_role, eff_account = target.role, account_id
    elif role:
        eff_role, eff_account = role, None
    else:
        eff_role, eff_account = user.role, str(user.id)

    vis = mp_service.get_effective_visibility(db, eff_role, eff_account)
    return {
        "role": eff_role,
        "account_id": eff_account,
        "visible": [k for k, v in vis.items() if v],
        "hidden": [k for k, v in vis.items() if not v],
        "modules": [
            {"key": m["key"], "name": m["name"], "category": m["category"], "visible": vis[m["key"]]}
            for m in mp_service.MODULE_REGISTRY
        ],
    }


@router.get("/modules")
def list_modules(
    user: User = Depends(require_roles(ROLE_ADMIN)),
    db: Session = Depends(get_db),
):
    """管理员视图：模块注册表 + 各角色默认矩阵 + 当前覆盖规则。"""
    return {
        "registry": mp_service.MODULE_REGISTRY,
        "role_matrix": mp_service.get_role_matrix(db),
        "overrides": mp_service.list_overrides(db),
    }


@router.post("/modules/visibility")
def set_module_visibility(
    body: ModuleVisibilitySet,
    user: User = Depends(require_roles(ROLE_ADMIN)),
    db: Session = Depends(get_db),
):
    """设置某角色 / 某账户的模块可见性覆盖（upsert）。返回该 scope 的完整有效矩阵。"""
    try:
        mp_service.set_visibility(db, body.scope_type, body.scope_id, body.module_key, body.visible)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # 解析该 scope 视角下用于展示的 role（账户级用其真实 role），返回完整有效矩阵
    if body.scope_type == mp_service.SCOPE_ACCOUNT:
        acct = db.get(User, body.scope_id)
        matrix_role = acct.role if acct else None
        matrix_account = body.scope_id
    else:
        matrix_role = body.scope_id
        matrix_account = None
    matrix = mp_service.get_effective_visibility(db, matrix_role, matrix_account)
    return {
        "status": "ok",
        "scope_type": body.scope_type,
        "scope_id": body.scope_id,
        "module_key": body.module_key,
        "visible": body.visible,
        "matrix": matrix,
    }


@router.delete("/modules/visibility")
def reset_module_visibility(
    scope_type: str = Query(..., pattern="^(role|account)$"),
    scope_id: str = Query(...),
    module_key: str = Query(...),
    user: User = Depends(require_roles(ROLE_ADMIN)),
    db: Session = Depends(get_db),
):
    """重置某角色 / 某账户的模块可见性覆盖（恢复默认或回退到更低优先级规则）。"""
    mp_service.reset_visibility(db, scope_type, scope_id, module_key)
    return {"status": "ok", "scope_type": scope_type, "scope_id": scope_id, "module_key": module_key}


# ==================== 持仓体检报告（导出客户报告 / 持仓体检） ====================
def _resolve_report_holdings(body: "PortfolioHealthRequest", db: Session, user: User):
    """把请求解析为 holdings 列表（list[dict]）。

    - 传了 ``client_id``：经权限校验后自动取该客户的真实持仓（不写死，绝不兜底示例股）；
    - 否则用 ``body.holdings``。
    返回空列表时由调用方返回 400。
    """
    if body.client_id:
        client = client_service.get_visible_client(db, user, body.client_id)
        return [
            {
                "code": p.code,
                "name": p.name,
                "sector": p.sector,
                "quantity": float(p.quantity),
                "cost_price": float(p.cost_price) if p.cost_price else None,
            }
            for p in client.positions
        ]
    return [h.model_dump() for h in body.holdings]


@router.post("/reports/portfolio-health")
def portfolio_health(
    body: PortfolioHealthRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """生成持仓体检报告（结构化 JSON）。

    用于前端展示与二次加工。支持：
      - 直接传 ``holdings``（分析哪几只完全由调用方决定，不写死）；
      - 或传 ``client_id`` 自动取客户真实持仓；
      - ``adapter``：``demo``（离线确定性，默认）/ ``public``（公开 API，云端可用）；
      - ``use_llm``：开启时尝试外部大模型叙事，未配置自动回落启发式兜底。

    注：响应体为已清洗的 JSON dict（剔除内部字段），不使用严格 Pydantic
    response_model，以避免高版本 Pydantic 对纯 ``Dict[str, Any]`` 响应模型的
    "not fully defined" 校验缺陷，同时保持字段完全由后端契约控制。
    """
    holdings = _resolve_report_holdings(body, db, user)
    if not holdings:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "持仓为空：请传入 holdings，或提供有效的 client_id")

    adapter = resolve_adapter(body.adapter)
    facts = build_report(holdings, adapter=adapter, title=body.title)
    node = resolve_node(body.use_llm)
    narrative = generate_narrative(facts, node=node)
    return {
        "meta": facts["meta"],
        "stocks": public_stocks(facts["stocks"]),
        "portfolio": facts["portfolio"],
        "narrative": narrative,
    }


@router.post("/reports/portfolio-health/html")
def portfolio_health_html(
    body: PortfolioHealthRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """生成持仓体检报告（自包含 HTML，供前端新窗口打印 / 导出 PDF）。"""
    holdings = _resolve_report_holdings(body, db, user)
    if not holdings:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "持仓为空：请传入 holdings，或提供有效的 client_id")

    adapter = resolve_adapter(body.adapter)
    facts = build_report(holdings, adapter=adapter, title=body.title)
    node = resolve_node(body.use_llm)
    narrative = generate_narrative(facts, node=node)
    html = render_html_report({
        "meta": facts["meta"],
        "stocks": facts["stocks"],
        "portfolio": facts["portfolio"],
        "narrative": narrative,
    })
    return Response(content=html, media_type="text/html; charset=utf-8")


# ==================== 持仓相关资讯（两表联动：news_item + client_news） ====================
def _holding_pct(db: Session, client_id: str, matched_codes: list) -> Optional[float]:
    """命中标的占该客户总持仓成本的比例（%），用于反向端点脱敏展示。

    以成本市值（cost_price × quantity）估算，不依赖实时行情，开销低。
    """
    if not matched_codes:
        return None
    positions = db.query(Position).filter(Position.client_id == client_id).all()
    if not positions:
        return None
    matched_set = set(matched_codes)
    from ..services.news_service import normalize_code
    total = sum((p.cost_price or 0) * p.quantity for p in positions)
    matched = sum((p.cost_price or 0) * p.quantity
                  for p in positions if normalize_code(p.code) in matched_set)
    if total <= 0:
        return None
    return round(matched / total * 100, 2)


@router.get("/clients/{client_id}/related-news", response_model=List[RelatedNewsOut])
def get_related_news(
    client_id: str,
    limit: int = 50,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """返回某客户的持仓相关快讯（client_news JOIN news_item），按关联创建时间倒序。

    权限：经 get_visible_client 校验——本人（owner_user_id）或 advisor/service 可见。
    每条含 tier（1=个股相关 / 2=板块相关）、matched_codes、is_read。
    """
    client_service.get_visible_client(db, user, client_id)  # 校验可见性（不可见 -> 404）
    rows = (
        db.query(ClientNews, NewsItem)
        .join(NewsItem, ClientNews.news_id == NewsItem.news_id)
        .filter(ClientNews.client_id == client_id)
        .order_by(ClientNews.first_seen.desc())
        .limit(limit)
        .all()
    )
    from ..services.news_service import is_news_displayable
    return [{
        "news_id": ni.news_id,
        "source": ni.source,
        "title": ni.title,
        "summary": ni.summary,
        "url": ni.url,
        "published_at": ni.published_at.isoformat() if ni.published_at else None,
        "stock_codes": ni.stock_codes or [],
        "tier": cn.tier,
        "matched_codes": cn.matched_codes or [],
        "is_read": cn.is_read,
        "first_seen": cn.first_seen.isoformat() if cn.first_seen else None,
    } for cn, ni in rows if is_news_displayable(ni)]


@router.post("/news/ingest", response_model=NewsIngestResultOut)
async def ingest_news(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """手动触发一次资讯采集周期（便于测试/回填）。权限：admin/service。

    执行 fetch -> upsert -> 匹配 -> 清理，返回各阶段统计。
    """
    from ..core.deps import require_roles
    require_roles(ROLE_ADMIN, ROLE_SERVICE)(user)
    from ..services.news_service import run_once
    stats = await run_once()
    return {"status": "ok", "stats": stats}


@router.get("/news", response_model=List[NewsItemOut])
def list_news(
    limit: int = 50,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """最近 news_item 原始流（全局资讯库视角）。权限：admin/service/advisor。"""
    from ..core.deps import require_roles
    require_roles(ROLE_ADMIN, ROLE_SERVICE, ROLE_ADVISOR)(user)
    from ..services.news_service import is_news_displayable
    rows = (
        db.query(NewsItem)
        .order_by(NewsItem.first_seen.desc())
        .limit(limit)
        .all()
    )
    # 序列化兜底：过滤存量脏数据（如测试占位、无链接无正文的空壳资讯）。
    return [ni for ni in rows if is_news_displayable(ni)]


@router.get("/news/{news_id}/clients", response_model=NewsAffectedClientsOut)
def news_affected_clients(
    news_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """反向端点（方案 E）：给定一条资讯，返回受影响的客户列表（脱敏）。

    复用同一匹配内核，零额外数据源。权限：admin/service/advisor。
    仅返回 client_id / name / matched_codes / 持仓占比，不含敏感财务明细。
    """
    from ..core.deps import require_roles
    require_roles(ROLE_ADMIN, ROLE_SERVICE, ROLE_ADVISOR)(user)
    ni = db.get(NewsItem, news_id)
    if ni is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "资讯不存在")
    rows = db.query(ClientNews).filter(ClientNews.news_id == news_id).all()
    clients = []
    for cn in rows:
        client = db.get(Client, cn.client_id)
        name = client.name if client else cn.client_id
        clients.append({
            "client_id": cn.client_id,
            "client_name": name,
            "matched_codes": cn.matched_codes or [],
            "holding_pct": _holding_pct(db, cn.client_id, cn.matched_codes or []),
        })
    return {"news_id": news_id, "title": ni.title, "clients": clients}


@router.post("/news/refresh-boards", response_model=dict)
async def refresh_boards(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """手动触发 code→BK 映射补全（启用 Tier2 板块相关）。权限：admin/service。

    拉取新闻流中出现过的板块(BK)成分股，写入 stock_boards；force=True 忽略节流。
    返回刷新的板块数与当前 stock_boards 总行数。沙箱若屏蔽 push2 则返回 0（由离线共现推导兜底）。
    """
    from ..core.deps import require_roles
    require_roles(ROLE_ADMIN, ROLE_SERVICE)(user)
    from ..services.news_service import refresh_stock_boards
    refreshed = refresh_stock_boards(db, force=True)
    total = db.query(StockBoards).count()
    return {"status": "ok", "boards_refreshed": refreshed, "stock_boards_total": total}


@router.get("/news/boards", response_model=list)
def list_boards(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """查看当前 code→BK 映射（stock_boards）。权限：admin/service/advisor。"""
    from ..core.deps import require_roles
    require_roles(ROLE_ADMIN, ROLE_SERVICE, ROLE_ADVISOR)(user)
    rows = db.query(StockBoards).order_by(StockBoards.code).all()
    return [{"code": r.code, "board_codes": r.board_codes or [], "updated_at":
             r.updated_at.isoformat() if r.updated_at else None} for r in rows]
