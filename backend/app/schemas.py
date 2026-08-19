"""Pydantic 请求/响应模型（API 字段级契约）。"""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

# ---- 认证 ----
class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    username: str
    role: str
    sub_role: Optional[str] = None
    name: str
    email: Optional[str] = None
    is_active: bool = True


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class UserCreate(BaseModel):
    username: Optional[str] = None   # 为空则按姓名拼音自动生成
    password: Optional[str] = None   # 为空则自动生成随机初始密码
    role: str
    sub_role: Optional[str] = None
    name: str = Field(..., min_length=1, max_length=64)
    email: Optional[str] = None


class UserCreateOut(UserOut):
    initial_password: Optional[str] = None   # 自动生成时返回初始密码（明文，仅一次）


# ---- 持仓 ----
class PositionIn(BaseModel):
    name: str
    code: str
    sector: str
    quantity: int = Field(..., gt=0)
    cost_price: float = Field(..., gt=0)
    price: float = Field(..., gt=0)


class PositionOut(PositionIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


# ---- 客户 ----
class ClientCreate(BaseModel):
    id: Optional[str] = None
    name: str = Field(..., min_length=1, max_length=64)
    age: Optional[int] = None
    risk_level: Optional[str] = None
    tags: List[str] = []
    note: str = ""
    available_cash: float = 0.0
    advisor_id: str
    service_ids: List[str] = []
    owner_user_id: Optional[str] = None
    positions: List[PositionIn] = []
    create_login: bool = False   # 是否同时创建 user-client 登录账号


class ClientUpdate(BaseModel):
    name: Optional[str] = None
    age: Optional[int] = None
    risk_level: Optional[str] = None
    tags: Optional[List[str]] = None
    note: Optional[str] = None
    available_cash: Optional[float] = None


class RelationsUpdate(BaseModel):
    advisor_id: str
    service_ids: List[str] = []


class PositionsUpdate(BaseModel):
    positions: List[PositionIn] = []


class ClientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    age: Optional[int] = None
    risk_level: Optional[str] = None
    tags: List[str] = []
    note: str = ""
    available_cash: float = 0.0
    advisor_id: str
    advisor_name: Optional[str] = None
    service_ids: List[str] = []
    positions: List[PositionOut] = []


class ClientCreateOut(ClientOut):
    login: Optional[UserCreateOut] = None   # create_login=True 时返回生成的登录账号


# ---- 风险预警 ----
class RiskAlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    client_id: str
    type: str
    level: str
    title: str
    description: str
    status: str
    created_at: datetime


class AlertStatusUpdate(BaseModel):
    status: str


# ---- 站内信 ----
class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    content: str
    category: str
    is_read: bool
    created_at: datetime


class UnreadCountOut(BaseModel):
    unread: int


# ---- 用户选项（开户/关系映射下拉） ----
class UserOptionsOut(BaseModel):
    advisors: List[UserOut] = []
    services: List[UserOut] = []


# ---- 关系映射批量导入/导出（CSV + JSON 双格式） ----
class RelationImportRow(BaseModel):
    client_id: Optional[str] = None   # 为空则自动生成客户编号
    name: str = Field(..., min_length=1, max_length=64)
    advisor_id: str
    service_ids: List[str] = []


class RelationExportRow(BaseModel):
    client_id: str
    name: str
    advisor_id: str
    advisor_name: Optional[str] = None
    service_ids: List[str] = []


class RelationImportError(BaseModel):
    row: int
    detail: str


class RelationImportResult(BaseModel):
    created: int = 0
    updated: int = 0
    errors: List[RelationImportError] = []
