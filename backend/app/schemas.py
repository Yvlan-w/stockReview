"""Pydantic 请求/响应模型（API 字段级契约）。"""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class PasswordChange(BaseModel):
    old_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=1, max_length=128)


# ---- 持仓 ----
class PositionIn(BaseModel):
    """持仓录入/更新。现价不再持久化（通过实时行情获取），price 字段仅为向后兼容保留、将被忽略。"""
    name: str
    code: str
    sector: str
    quantity: int = Field(..., gt=0)
    cost_price: float = Field(..., gt=0)
    price: Optional[float] = Field(None, gt=0, deprecated=True)  # 已废弃：现价由实时行情提供


class PositionOut(BaseModel):
    """持仓输出：不含 price（现价由 /api/clients/{id}/portfolio 实时提供）。"""
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    code: str
    sector: str
    quantity: int
    cost_price: float


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

    @model_validator(mode="after")
    def _validate_cash_and_age(self):
        if self.available_cash is not None and self.available_cash < 0:
            raise ValueError("available_cash 必须为非负数")
        if self.age is not None and (self.age < 0 or self.age > 150):
            raise ValueError("age 必须在 0-150 之间")
        return self


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
    service_names: List[str] = []
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
    dimension: Optional[str] = None
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


# ---- 个股行情 ----
class StockPriceOut(BaseModel):
    code: str
    name: Optional[str] = None
    current_price: Optional[float] = None
    prev_close: Optional[float] = None
    change_pct: Optional[float] = None
    change_amount: Optional[float] = None
    volume: Optional[float] = None
    turnover: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    open_price: Optional[float] = None
    updated_at: Optional[datetime] = None


class StockPriceBatchOut(BaseModel):
    prices: dict[str, Optional[StockPriceOut]] = {}
    updated_at: Optional[datetime] = None


# ---- 组合估值 / 盈亏 ----
class PositionDetail(BaseModel):
    code: str
    name: str
    quantity: int
    costPrice: float
    currentPrice: float
    prevClose: Optional[float] = None
    marketValue: float
    pnl: float
    pnlPct: float
    todayPnl: float


class PortfolioOut(BaseModel):
    totalMarketValue: float
    totalCost: float
    totalPnl: float
    totalPnlPct: float
    todayPnl: float
    todayPnlPct: float
    totalAssets: float
    availableCash: float
    positions: List[PositionDetail] = []


class PnLHistoryOut(BaseModel):
    dates: List[str] = []
    pnl: List[Optional[float]] = []
    dailyPnl: List[Optional[float]] = []
    benchmark: List[Optional[float]] = []
    benchmarkReturn: List[Optional[float]] = []


# ---- 交易记录 ----
class TransactionCreate(BaseModel):
    """创建交易记录。

    手续费（可选，不传时按全局配置计算）：
    - fee_mode='rate'：fee_value 为费率（0 < fee_value <= 0.01，即最高1%），手续费 = 金额 × 费率
    - fee_mode='fixed'：fee_value 为固定金额（0 < fee_value <= 100000 元/笔）
    """
    code: str = Field(..., min_length=1, max_length=16)
    name: Optional[str] = None
    action: str = Field(..., pattern="^(buy|sell)$")
    quantity: int = Field(..., gt=0)
    price: float = Field(..., gt=0)
    cost_price: Optional[float] = None  # 交易时的成本价（卖出时用于计算已实现盈亏）
    fee_mode: Optional[str] = Field(None, pattern="^(rate|fixed)$")  # 手续费模式
    fee_value: Optional[float] = None  # 费率值（rate）或固定金额（fixed）
    trade_date: Optional[str] = None  # 交易日期，默认今天

    @model_validator(mode="after")
    def _validate_fee(self):
        """手续费组合校验：模式与数值成对出现，且符合各自范围。"""
        if self.fee_mode is None and self.fee_value is not None:
            raise ValueError("指定 fee_value 时必须同时指定 fee_mode（rate/fixed）")
        if self.fee_mode is not None:
            if self.fee_value is None:
                raise ValueError(f"指定 fee_mode={self.fee_mode} 时必须同时提供 fee_value")
            if self.fee_mode == "rate" and not (0 < self.fee_value <= 0.01):
                raise ValueError("费率需在 (0, 0.01] 之间（如 0.00025 表示万2.5，最高 1%）")
            if self.fee_mode == "fixed" and not (0 < self.fee_value <= 100000):
                raise ValueError("固定手续费金额需在 (0, 100000] 元之间")
        return self


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    client_id: str
    code: str
    name: Optional[str] = None
    market: Optional[str] = None
    action: str
    quantity: int
    price: float
    cost_price: Optional[float] = None
    fee_mode: Optional[str] = None
    fee_value: Optional[float] = None
    fee_amount: float
    fee_commission: float = 0.0
    fee_stamp_tax: float = 0.0
    fee_transfer_fee: float = 0.0
    realized_pnl: float
    trade_date: str
    executed_at: Optional[datetime] = None
    created_at: datetime


# ---- 调仓请求 ----
class AdjustRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=16)
    name: Optional[str] = None
    sector: Optional[str] = None
    action: str = Field(..., pattern="^(buy|sell)$")
    quantity: int = Field(..., gt=0)
    price: float = Field(..., gt=0)
    fee_mode: Optional[str] = Field(None, pattern="^(rate|fixed)$")
    fee_value: Optional[float] = None
    trade_date: Optional[str] = None
    from_cash: bool = True
    cost_method: str = Field("average", pattern="^(average|fifo|lifo)$")

    @model_validator(mode="after")
    def _validate_fee(self):
        if self.fee_mode is None and self.fee_value is not None:
            raise ValueError("指定 fee_value 时必须同时指定 fee_mode")
        if self.fee_mode is not None:
            if self.fee_value is None:
                raise ValueError(f"指定 fee_mode={self.fee_mode} 时必须同时提供 fee_value")
            if self.fee_mode == "rate" and not (0 < self.fee_value <= 0.01):
                raise ValueError("费率需在 (0, 0.01] 之间")
            if self.fee_mode == "fixed" and not (0 < self.fee_value <= 100000):
                raise ValueError("固定手续费需在 (0, 100000] 元之间")
        return self
