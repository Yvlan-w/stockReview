"""Pydantic 请求/响应模型（API 字段级契约）。"""
from datetime import datetime
from typing import Any, Dict, List, Optional

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
    status: str = "active"           # active/expired/deleted（生命周期标记；过期后不能登录）
    expires_at: Optional[datetime] = None   # 非 admin 才会有；到期自动变 expired；admin 永不过期=空
    remaining_days: Optional[int] = None    # 前端直接展示：admin=None/过期后=负数/未过期=整数
    client_id: Optional[str] = None         # 当 role=user 且 sub_role=client 时，通过 owner_user_id 反查的客户档案编号


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
    # 账户生命周期：非 admin 才受管；None 表示走默认 DEFAULT_LICENSE_DAYS；admin 创建时忽略
    license_days: Optional[int] = Field(None, ge=1, le=36500)
    # 仅当创建 role=user & sub_role=client 时使用：同步创建对应 Client 档案所需字段
    # （advisor 是实际分配的投资顾问；service_ids 是分配的 1~2 名客服；都缺省时兜底为 creator 自己。）
    client_advisor_id: Optional[str] = None
    client_service_ids: List[str] = []
    client_risk_level: Optional[str] = None
    client_available_cash: float = 0.0


class UserCreateOut(UserOut):
    initial_password: Optional[str] = None   # 自动生成时返回初始密码（明文，仅一次）
    client_id: Optional[str] = None           # role=user/sub_role=client 时同步创建的客户编号
    # 当创建者角色无权直接看到初始密码（如客服）时，会置 True 告知前端：
    # 初始密码已以站内信形式单独发送给管理员，请不要在当前界面显示明文。
    redelivered_via_admin_inbox: Optional[bool] = None


class PasswordChange(BaseModel):
    old_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=1, max_length=128)


class UserRenew(BaseModel):
    """管理员续费 / 充值天数。extend_days 为正则为续期；为负视为提前缩短。"""
    extend_days: int = Field(..., ge=-36500, le=36500)


class UserResetPassword(BaseModel):
    """管理员重置密码；password 可空则自动生成，响应里返回新密码明文一次。"""
    password: Optional[str] = Field(None, min_length=1, max_length=128)


class UserStatusPatch(BaseModel):
    """分配关系+生命周期管理界面里的动作。"""
    status: Optional[str] = None   # active/expired/deleted（deleted=软删，前端再无列表）
    expires_at: Optional[datetime] = None
    license_days: Optional[int] = Field(None, ge=1, le=36500)  # 相对于今天起的新赠送，到期日直接覆盖


class UserUpdate(BaseModel):
    """管理员更新用户资料（不包含密码；改密见 PasswordChange）。"""
    name: Optional[str] = Field(None, min_length=1, max_length=64)
    email: Optional[str] = None
    role: Optional[str] = None
    sub_role: Optional[str] = None
    is_active: Optional[bool] = None


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
    advisor_id: Optional[str] = None  # 可空：由 owner_user_id 账号本人自管持仓
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
    advisor_id: Optional[str] = None  # 可空：不分配投顾，由 owner 本人自管
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
    advisor_id: Optional[str] = None
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
    advisor_id: Optional[str] = None  # 可空：不分配投顾，由 owner 本人自管
    service_ids: List[str] = []


class RelationExportRow(BaseModel):
    client_id: str
    name: str
    advisor_id: Optional[str] = None
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
    action: str = Field(..., pattern="^(buy|sell|adjust)$")
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
    cost_price: Optional[float] = None  # 交易完成后的最新持仓成本价（移动加权口径）
    prev_cost_price: Optional[float] = None  # 交易发生前的持仓成本价（仅加仓≠cost_price）
    fee_mode: Optional[str] = None
    fee_value: Optional[float] = None
    fee_amount: float
    fee_commission: float = 0.0
    fee_stamp_tax: float = 0.0
    fee_transfer_fee: float = 0.0
    realized_pnl: float
    trade_date: str
    executed_at: Optional[datetime] = None
    audit_log_id: Optional[int] = None  # 关联的审计日志（人工调仓会有，种子/系统操作可能为空）
    matched_lots: Optional[list] = None  # 卖出批次匹配明细（FIFO 记账用），买入/调整为空
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
    executed_at: Optional[datetime] = None  # 人为指定交易时间；不传则用服务器当前时间
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


# ---- 审计日志（后台「查看日志」Tab） ----
class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    actor_user_id: Optional[str] = None
    actor_name: Optional[str] = None
    action: str
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    before_value: Any = None
    after_value: Any = None
    diff: Any = None
    note: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None


class AuditLogPage(BaseModel):
    total: int
    items: List[AuditLogOut]


# ---- 持仓体检报告（导出客户报告） ----
class HoldingInput(BaseModel):
    """单只持仓（用于报告生成）。代码必填，其余缺失时由适配器补全。"""
    code: str = Field(..., min_length=1, max_length=16)
    name: Optional[str] = None
    sector: Optional[str] = None
    quantity: float = Field(..., gt=0)          # 持仓数量（>0；A股为整数，但保留 float 以兼容基金/分红拆细）
    cost_price: Optional[float] = Field(None, gt=0)
    current_price: Optional[float] = Field(None, gt=0)


class PortfolioHealthRequest(BaseModel):
    """持仓体检报告请求。

    - ``holdings``：直接传入持仓（不写死，由调用方决定分析哪几只）；
    - ``client_id``：传入则忽略 ``holdings``，自动取该客户的真实持仓；
    - ``adapter``：``demo``（离线确定性，默认）/ ``public``（公开 API，云端可用）；
    - ``use_llm``：是否尝试调用外部大模型叙事（未配置时自动回落启发式）；
    - ``title``：报告标题（缺省为"持仓体检报告"）。
    """
    holdings: List[HoldingInput] = []
    adapter: str = "demo"                       # "demo" | "public"
    use_llm: bool = False
    client_id: Optional[str] = None
    title: Optional[str] = None


class PortfolioHealthResponse(BaseModel):
    meta: Dict[str, Any]
    stocks: List[Dict[str, Any]]
    portfolio: Dict[str, Any]
    narrative: Dict[str, Any]


# ---- 持仓相关资讯（两表联动：news_item + client_news）----
class NewsItemOut(BaseModel):
    """资讯主表响应（注意：均按 UTC 存储，展示层 +8h）。"""
    model_config = ConfigDict(from_attributes=True)
    news_id: str
    source: str
    title: str
    summary: Optional[str] = None
    url: Optional[str] = None
    content: Optional[str] = None
    published_at: Optional[datetime] = None
    first_seen: Optional[datetime] = None
    stock_codes: List[str] = []


class RelatedNewsOut(BaseModel):
    """客户持仓相关快讯：资讯正文 + 匹配上下文（tier / matched_codes / is_read）。"""
    news_id: str
    source: str
    title: str
    summary: Optional[str] = None
    url: Optional[str] = None
    published_at: Optional[datetime] = None
    stock_codes: List[str] = []
    tier: int                                    # 1=个股相关 / 2=板块相关
    matched_codes: List[str] = []                # 实际命中的客户持仓代码（UI 高亮用）
    is_read: bool = False
    first_seen: Optional[datetime] = None         # 该关联首次创建时间（TTL 基准）


class NewsIngestResultOut(BaseModel):
    """手动触发采集（POST /api/news/ingest）的返回。"""
    status: str = "ok"
    stats: Dict[str, Any] = {}


class NewsAffectedClientOut(BaseModel):
    """反向端点：受某条资讯影响的客户（脱敏，仅基础标识 + 持仓占比）。"""
    client_id: str
    client_name: str
    matched_codes: List[str] = []
    holding_pct: Optional[float] = None           # 命中标的占该客户总持仓成本的比例（%）


class NewsAffectedClientsOut(BaseModel):
    news_id: str
    title: str
    clients: List[NewsAffectedClientOut] = []
