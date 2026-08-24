"""认证与用户管理服务。"""
import os

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


# 管理员初始密码（仅首次建号时使用；可用环境变量 STOCK_REVIEW_ADMIN_PASSWORD 覆盖）
ADMIN_PASSWORD_DEFAULT = os.getenv("STOCK_REVIEW_ADMIN_PASSWORD", "jdzt123456")


def seed_default_users(db: Session) -> None:
    """生产种子：仅幂等创建唯一管理员账号，其余用户表初始为空。"""
    user = db.get(User, "u_admin")
    if user is None:
        user = User(
            id="u_admin", username="admin", name="管理员", role=ROLE_ADMIN,
            password_hash=hash_password(ADMIN_PASSWORD_DEFAULT),
        )
        db.add(user)
        db.commit()
