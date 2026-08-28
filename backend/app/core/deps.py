"""FastAPI 依赖：认证与角色鉴权。"""
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User, USER_STATUS_EXPIRED, USER_STATUS_DELETED
from .security import decode_access_token
from ..services import auth_service


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未提供认证令牌")
    token = authorization.split(" ", 1)[1]
    try:
        payload = decode_access_token(token)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="令牌无效或已过期")

    user_id = payload.get("sub")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已禁用")
    # 任何请求入口：根据 expires_at 自动刷新 expired/active（即便 token 仍然在有效期，也能限制到期用户）
    auth_service.sync_expired_status(db, user)
    if not user.is_active or user.status == USER_STATUS_DELETED or user.status == USER_STATUS_EXPIRED:
        if user.status == USER_STATUS_EXPIRED:
            detail = auth_service.EXPIRED_ERROR
        elif user.status == USER_STATUS_DELETED:
            detail = "账号已删除"
        else:
            detail = "用户不存在或已禁用"
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    return user


def require_roles(*roles: str):
    """返回一个要求当前用户角色属于给定集合的依赖。"""
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权限执行此操作")
        return user
    return dependency
