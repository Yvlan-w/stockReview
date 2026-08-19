"""认证与用户管理服务。"""
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..core.security import hash_password, verify_password
from ..models import (
    User, ROLE_ADMIN, ROLE_ADVISOR, ROLE_SERVICE, ROLE_USER, ROLE_GUEST,
    SUBROLE_CLIENT, SUBROLE_NON_CLIENT, ROLES,
)
from ..schemas import UserCreate
from . import account

MIN_PASSWORD_LENGTH = 6


def authenticate(db: Session, username: str, password: str) -> User:
    user = db.query(User).filter(User.username == username).first()
    if user is None or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已禁用")
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

    user = User(
        id=_next_user_id(db),
        username=username,
        password_hash=hash_password(password),
        role=data.role,
        sub_role=data.sub_role,
        name=data.name,
        email=data.email,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user, initial_password


# 默认演示账号密码（私有单机环境演示用）
DEFAULT_PASSWORD = "123456"

_ADVISORS = [
    ("adv_001", "顾问·张伟"),
    ("adv_002", "顾问·李娜"),
    ("adv_003", "顾问·王强"),
    ("adv_004", "顾问·刘敏"),
    ("adv_005", "顾问·陈静"),
]
_SERVICES = [
    ("svc_001", "客服·赵芳"),
    ("svc_002", "客服·钱磊"),
    ("svc_003", "客服·孙婷"),
    ("svc_004", "客服·周明"),
    ("svc_005", "客服·吴娜"),
    ("svc_006", "客服·郑浩"),
]


def _ensure_user(db: Session, uid: str, username: str, name: str, role: str, sub_role: str | None = None) -> User:
    user = db.get(User, uid)
    if user is None:
        user = User(
            id=uid, username=username, name=name, role=role, sub_role=sub_role,
            password_hash=hash_password(DEFAULT_PASSWORD),
        )
        db.add(user)
    return user


def seed_default_users(db: Session) -> None:
    """幂等写入默认用户（管理员/顾问/客服/演示用户）。"""
    _ensure_user(db, "u_admin", "admin", "管理员", ROLE_ADMIN)
    _ensure_user(db, "u_guest", "guest", "游客", ROLE_GUEST)
    _ensure_user(db, "u_client_demo", "client001", "客户·演示", ROLE_USER, SUBROLE_CLIENT)
    _ensure_user(db, "u_user_demo", "user001", "普通用户·演示", ROLE_USER, SUBROLE_NON_CLIENT)
    for uid, name in _ADVISORS:
        _ensure_user(db, uid, uid, name, ROLE_ADVISOR)
    for uid, name in _SERVICES:
        _ensure_user(db, uid, uid, name, ROLE_SERVICE)
    db.commit()
