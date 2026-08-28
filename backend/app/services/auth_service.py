"""认证与用户管理服务。"""
import datetime as _dt

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..core.security import hash_password, verify_password
from ..models import (
    User, ROLE_ADMIN, ROLE_ADVISOR, ROLE_SERVICE, ROLE_USER, ROLE_GUEST,
    SUBROLE_CLIENT, SUBROLE_NON_CLIENT, ROLES, Client, ServiceAssignment,
    USER_STATUS_ACTIVE, USER_STATUS_EXPIRED, USER_STATUS_DELETED, USER_STATUSES,
    NON_ADMIN_ROLES, DEFAULT_LICENSE_DAYS,
)
from ..schemas import UserCreate, UserUpdate
from ..config import ADMIN_PASSWORD_DEFAULT
from . import account

MIN_PASSWORD_LENGTH = 6
# 密码强度：建议至少 8 位、包含大小写字母 + 数字；服务端强制最低 6 位即可通过，
# 但会在响应里返回强度等级提示前端展示。
PASSWORD_STRENGTH_STRONG_LEN = 8

EXPIRED_ERROR = "账户到期，请联系管理员续费"


def _now():
    return _dt.datetime.now(tz=_dt.UTC).replace(tzinfo=None)


def compute_remaining_days(user: User) -> int | None:
    """前端卡片直用：永久 admin=None，到期后负数，其余正数。"""
    if user.expires_at is None:
        return None
    delta = user.expires_at - _now()
    return delta.days + (1 if delta.total_seconds() > 0 else 0)


def sync_expired_status(db: Session, user: User) -> User:
    """非 admin 账户按 expires_at 自动在 active/expired 间翻转（不影响 deleted）。"""
    if user.role == ROLE_ADMIN:
        return user
    if user.status == USER_STATUS_DELETED:
        return user
    if user.expires_at is None:
        return user
    now = _now()
    flipped = False
    if now >= user.expires_at and user.status == USER_STATUS_ACTIVE:
        user.status = USER_STATUS_EXPIRED
        user.is_active = False   # 配合原有 require_roles 里的 is_active 拦截，形成双重保险
        flipped = True
    elif now < user.expires_at and user.status == USER_STATUS_EXPIRED:
        user.status = USER_STATUS_ACTIVE
        user.is_active = True
        flipped = True
    if flipped:
        db.commit()
        db.refresh(user)
    return user


def _password_strength(pwd: str) -> tuple[int, str]:
    """返回 (score 0-4, level_label)。"""
    if not pwd:
        return 0, "极弱"
    score = 0
    has_lower = any(c.islower() for c in pwd)
    has_upper = any(c.isupper() for c in pwd)
    has_digit = any(c.isdigit() for c in pwd)
    has_special = any(not c.isalnum() for c in pwd)
    if len(pwd) >= PASSWORD_STRENGTH_STRONG_LEN:
        score += 1
    if has_lower or has_upper:
        score += 1
    if (has_lower and has_upper) or has_digit:
        score += 1
    if has_special and (has_lower or has_upper) and has_digit:
        score += 1
    levels = ["极弱", "较弱", "一般", "较强", "极强"]
    return score, levels[min(score, 4)]


def change_password(db: Session, user: User, old_password: str, new_password: str) -> dict:
    """修改当前用户自己的密码。返回 {strength_score, strength_label}。"""
    if not verify_password(old_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="原密码错误",
        )
    if new_password == old_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="新密码不能与原密码相同",
        )
    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"新密码长度至少 {MIN_PASSWORD_LENGTH} 位",
        )
    score, label = _password_strength(new_password)
    user.password_hash = hash_password(new_password)
    db.commit()
    return {"strength_score": score, "strength_label": label}


def authenticate(db: Session, username: str, password: str) -> User:
    user = db.query(User).filter(User.username == username).first()
    if user is None or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    # 登录入口：先根据 expires_at 自刷新一次 expired/active
    sync_expired_status(db, user)
    if not user.is_active:
        # 把"到期"与"禁用"两种情况区分开，前端登录界面可显示对应提示
        if user.status == USER_STATUS_EXPIRED:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=EXPIRED_ERROR)
        if user.status == USER_STATUS_DELETED:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已删除")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已禁用")
    if user.status == USER_STATUS_DELETED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已删除")
    if user.status == USER_STATUS_EXPIRED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=EXPIRED_ERROR)
    return user


def _next_user_id(db: Session) -> str:
    """生成不重复的用户 ID（u_xxx）。"""
    count = db.query(User).count()
    while True:
        uid = "u_" + str(count + 1).zfill(4)
        if db.get(User, uid) is None:
            return uid
        count += 1


def create_user(db: Session, data: UserCreate) -> tuple[User, str | None]:
    """创建用户；用户名/密码为空时自动生成。返回 (用户, 初始密码)。

    initial_password 仅当密码为自动生成时非空，用于一次性回传给开户方。
    """
    if data.role not in ROLES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="非法角色")
    if data.sub_role is not None and data.sub_role not in (SUBROLE_CLIENT, SUBROLE_NON_CLIENT):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="非法子角色")
    if data.sub_role is not None and data.role != ROLE_USER:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="仅 user 角色可设置子角色")

    username = (data.username or "").strip()
    password = data.password
    initial_password = None

    if not username:
        username = account.generate_username(
            data.name,
            lambda u: db.query(User).filter(User.username == u).first() is not None,
        )
    if not password:
        password = account.generate_password()
        initial_password = password
    elif len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"密码长度至少 {MIN_PASSWORD_LENGTH} 位",
        )

    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在")

    # 生命周期默认值：非 admin 账户给 DEFAULT_LICENSE_DAYS；创建方也可用 license_days 覆盖
    if data.role in NON_ADMIN_ROLES:
        days = int(data.license_days or DEFAULT_LICENSE_DAYS)
        expires_at = _now() + _dt.timedelta(days=days)
        user_status = USER_STATUS_ACTIVE
        is_active = True
    else:
        expires_at = None
        user_status = USER_STATUS_ACTIVE
        is_active = True

    user = User(
        id=_next_user_id(db),
        username=username,
        password_hash=hash_password(password),
        role=data.role,
        sub_role=data.sub_role,
        name=data.name,
        email=data.email,
        expires_at=expires_at,
        status=user_status,
        is_active=is_active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user, initial_password


def ensure_admin_user(db: Session) -> None:
    """在当前 Session 内保证至少存在唯一管理员（幂等）。"""
    if db.get(User, "u_admin") is None:
        db.add(User(
            id="u_admin", username="admin", name="管理员", role=ROLE_ADMIN,
            password_hash=hash_password(ADMIN_PASSWORD_DEFAULT),
        ))
        db.commit()


def _validate_role_and_sub_role(role: str | None, sub_role: str | None) -> None:
    if role is not None and role not in ROLES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="非法角色")
    if sub_role is not None and sub_role not in (SUBROLE_CLIENT, SUBROLE_NON_CLIENT):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="非法子角色")
    if sub_role is not None and (role or "") != ROLE_USER and role is not None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="仅 user 角色可设置子角色")


def update_user(db: Session, user: User, data: UserUpdate) -> User:
    """管理员更新用户资料（不含密码）。返回更新后的 User。

    不做任何字段变更时返回对象不变，调用方可据此决定是否写审计日志。
    """
    _validate_role_and_sub_role(data.role, data.sub_role)

    if data.role == ROLE_USER and data.sub_role is None and user.role != ROLE_USER:
        # 从其他角色切到 user 时，不强制子角色；保留原有逻辑
        pass

    if data.name is not None:
        user.name = data.name
    if data.email is not None or data.email is None and "email" in data.model_fields_set:
        # model_fields_set 能区分传了 None vs 完全没传
        user.email = data.email
    if data.role is not None:
        user.role = data.role
    if data.sub_role is not None or (data.sub_role is None and "sub_role" in data.model_fields_set):
        user.sub_role = data.sub_role
    if data.is_active is not None:
        # 禁止禁用唯一的管理员，避免锁死后台
        if user.role == ROLE_ADMIN and data.is_active is False:
            other_active = (db.query(User).filter(User.role == ROLE_ADMIN,
                                                  User.is_active.is_(True),
                                                  User.id != user.id).count())
            if other_active == 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                    detail="至少需要保留一个启用的管理员账号")
        user.is_active = data.is_active
    db.commit()
    db.refresh(user)
    return user


def delete_user(db: Session, user: User) -> dict:
    """管理员软删除用户（不物理删除），同步清理关系映射避免数据残留。

    返回一个统计 dict，用于审计日志：
      {"cleared_advisor": N, "cleared_service_assignments": M, "prev_status": str}

    数据一致性策略（参考经验：删除=置位+关联清理）：
      - 用户状态标记为 deleted，不物理删除（保留审计尾链）
      - **同步清理关系映射**（而不是禁止删除）：
          ① clients.advisor_id = user.id → 置为 NULL（仅解除顾问归属，客户档案仍存在）
          ② service_assignments where service_id = user.id → 全部删除
          ③ owner_user_id 指向该用户的 client 保留（owner 是数据归属，不是服务关系）
      - 最后一个管理员仍然禁止删除（避免锁死后台）
    """
    if user.role == ROLE_ADMIN:
        other_admins = (db.query(User).filter(User.role == ROLE_ADMIN,
                                              User.status.isnot(USER_STATUS_DELETED),
                                              User.id != user.id).count())
        if other_admins == 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="至少需要保留一个管理员账号")
    prev_status = getattr(user, "status", USER_STATUS_ACTIVE)

    # ① 解除顾问归属：clients where advisor_id = user.id → set advisor_id = NULL
    cleared_advisor = (db.query(Client)
                       .filter(Client.advisor_id == user.id)
                       .update({Client.advisor_id: None}, synchronize_session=False))

    # ② 解除客服归属：删除 ServiceAssignment
    cleared_service = (db.query(ServiceAssignment)
                       .filter(ServiceAssignment.service_id == user.id)
                       .delete(synchronize_session=False))

    user.status = USER_STATUS_DELETED
    user.is_active = False
    db.commit()
    return {
        "cleared_advisor": int(cleared_advisor or 0),
        "cleared_service_assignments": int(cleared_service or 0),
        "prev_status": prev_status,
    }


# --------------------- 账户级别管理动作（后台 Tab）---------------------
def reset_user_password(db: Session, user: User, new_password: str | None) -> tuple[str, bool]:
    """管理员重置密码。

    返回 (新密码明文一次, 是否自动生成)。若新密码为空则自动生成随机密码。
    """
    generated = False
    if not new_password:
        new_password = account.generate_password()
        generated = True
    elif len(new_password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"密码长度至少 {MIN_PASSWORD_LENGTH} 位")
    user.password_hash = hash_password(new_password)
    # 重置密码后若该账户原 status=expired，也不要强行改回，续费走独立 renew 接口
    db.commit()
    db.refresh(user)
    return new_password, generated


def renew_user(db: Session, user: User, extend_days: int) -> User:
    """管理员续费：在原到期日上加减 extend_days 天；

    - 历史没到期日的非 admin（例如老库未迁移 expires_at 的 admin 调过来时会拒绝）
    - admin 永久账号拒绝续费（没有意义），避免错误理解含义
    """
    if user.role == ROLE_ADMIN:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="管理员账户永久有效，无需续费")
    now = _now()
    if user.expires_at is None:
        # 老数据补一条：视为今天起赠送 DEFAULT_LICENSE_DAYS 再加 extend_days
        base = now + _dt.timedelta(days=DEFAULT_LICENSE_DAYS)
    else:
        # 已过期的从今天开始续，避免"续了 30 天仍然过期"的反直觉
        start_from = max(now, user.expires_at)
        base = start_from
    new_expires = base + _dt.timedelta(days=extend_days)
    if new_expires <= now:
        # 续费后仍 <= 今天：视为提前过期
        user.expires_at = new_expires
        user.status = USER_STATUS_EXPIRED
        user.is_active = False
    else:
        user.expires_at = new_expires
        # deleted 账号不自动激活，避免被误续费后"自动出现"
        if user.status != USER_STATUS_DELETED:
            user.status = USER_STATUS_ACTIVE
            user.is_active = True
    db.commit()
    db.refresh(user)
    return user


def patch_user_lifecycle(db: Session, user: User, *,
                         new_status: str | None,
                         expires_at: _dt.datetime | None,
                         license_days: int | None) -> User:
    """账户生命周期管理：一次 patch 可同时改 status / expires_at / license_days。"""
    if new_status is not None:
        if new_status not in USER_STATUSES:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail=f"非法 status，需为 {USER_STATUSES}")
        if user.role == ROLE_ADMIN and new_status == USER_STATUS_DELETED:
            other = (db.query(User).filter(User.role == ROLE_ADMIN,
                                           User.status != USER_STATUS_DELETED,
                                           User.id != user.id).count())
            if other == 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                    detail="至少需要保留一个管理员账号")
        user.status = new_status
        user.is_active = (new_status == USER_STATUS_ACTIVE)
    if license_days is not None and user.role != ROLE_ADMIN:
        user.expires_at = _now() + _dt.timedelta(days=int(license_days))
        if user.status != USER_STATUS_DELETED:
            user.status = USER_STATUS_ACTIVE
            user.is_active = True
    elif expires_at is not None and user.role != ROLE_ADMIN:
        user.expires_at = expires_at
    db.commit()
    db.refresh(user)
    # 最后基于最新 expires_at 刷新 expired/active
    return sync_expired_status(db, user)
