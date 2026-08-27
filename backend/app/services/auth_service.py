"""认证与用户管理服务。"""
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..core.security import hash_password, verify_password
from ..models import (
    User, ROLE_ADMIN, ROLE_ADVISOR, ROLE_SERVICE, ROLE_USER, ROLE_GUEST,
    SUBROLE_CLIENT, SUBROLE_NON_CLIENT, ROLES,
)
from ..schemas import UserCreate
from ..config import ADMIN_PASSWORD_DEFAULT
from . import account

MIN_PASSWORD_LENGTH = 6
# 密码强度：建议至少 8 位、包含大小写字母 + 数字；服务端强制最低 6 位即可通过，
# 但会在响应里返回强度等级提示前端展示。
PASSWORD_STRENGTH_STRONG_LEN = 8


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


def ensure_admin_user(db: Session) -> None:
    """在当前 Session 内保证至少存在唯一管理员（幂等）。"""
    if db.get(User, "u_admin") is None:
        db.add(User(
            id="u_admin", username="admin", name="管理员", role=ROLE_ADMIN,
            password_hash=hash_password(ADMIN_PASSWORD_DEFAULT),
        ))
        db.commit()
