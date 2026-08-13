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
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=6, max_length=128)
    role: str
    sub_role: Optional[str] = None
    name: str = Field(..., min_length=1, max_length=64)
    email: Optional[str] = None


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
