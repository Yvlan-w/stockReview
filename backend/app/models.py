"""数据模型（SQLAlchemy ORM）。

角色体系（5 类）：
- guest      游客（只读公开内容）
- user       用户（子角色：client 客户 / non_client 非客户）
- advisor    投资顾问
- service    客服
- admin      管理员

关系映射：
- Client.advisor_id  -> User.id（一个客户固定 1 名顾问；顾问服务多个客户，一对多）
- ServiceAssignment  -> 客户与客服的多对多（一个客户 1~2 名客服；客服服务多个客户）
- 顾问与客服之间无直接关系。
"""
import datetime as _dt

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text,
    UniqueConstraint, PrimaryKeyConstraint, SmallInteger,
)
from sqlalchemy.orm import relationship

from .database import Base

# ---- 角色与子角色常量 ----
ROLE_GUEST = "guest"
ROLE_USER = "user"
ROLE_ADVISOR = "advisor"
ROLE_SERVICE = "service"
ROLE_ADMIN = "admin"

SUBROLE_CLIENT = "client"
SUBROLE_NON_CLIENT = "non_client"

# 账户生命周期状态（取代粗暴删人：expired 只是"到期了不能登录"，可续费恢复）
USER_STATUS_ACTIVE = "active"
USER_STATUS_EXPIRED = "expired"
USER_STATUS_DELETED = "deleted"
USER_STATUSES = (USER_STATUS_ACTIVE, USER_STATUS_EXPIRED, USER_STATUS_DELETED)

ROLES = (ROLE_GUEST, ROLE_USER, ROLE_ADVISOR, ROLE_SERVICE, ROLE_ADMIN)
ROLE_LEVEL = {ROLE_GUEST: 0, ROLE_USER: 1, ROLE_ADVISOR: 2, ROLE_SERVICE: 3, ROLE_ADMIN: 4}
# 非 admin 角色才受生命周期管理：admin 默认永不过期
NON_ADMIN_ROLES = (ROLE_USER, ROLE_ADVISOR, ROLE_SERVICE)
# 新建非 admin 账户默认赠送天数（可由创建账户时传 license_days 覆盖）
DEFAULT_LICENSE_DAYS = 30

# 风险预警状态
ALERT_OPEN = "open"
ALERT_ACK = "acknowledged"
ALERT_RESOLVED = "resolved"

# 站内信类别
NOTIF_RISK = "risk_alert"
NOTIF_INFO = "info"
NOTIF_SYSTEM = "system"
NOTIF_ACCOUNT_CREDENTIALS = "account_credentials"   # 客服开户创建的 user-client 账密 → 站内信给管理员


def utcnow():
    return _dt.datetime.now(_dt.timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(String(64), primary_key=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    role = Column(String(16), nullable=False)
    sub_role = Column(String(16), nullable=True)  # client / non_client（仅 user 角色）
    name = Column(String(64), nullable=False)
    email = Column(String(128), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    # 生命周期（非 admin 才启用，admin 两字段都为 None → 永久）
    status = Column(String(16), nullable=False, default=USER_STATUS_ACTIVE, index=True)
    expires_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    # 顾问：所服务的客户（通过 Client.advisor_id 反向）
    advised_clients = relationship("Client", back_populates="advisor", foreign_keys="Client.advisor_id")
    # 客服：所服务的客户（通过 ServiceAssignment）
    service_assignments = relationship("ServiceAssignment", back_populates="service")
    notifications = relationship("Notification", back_populates="recipient")

    def __repr__(self):
        return f"<User {self.id} {self.role}>"


class Client(Base):
    __tablename__ = "clients"

    id = Column(String(64), primary_key=True)
    name = Column(String(64), nullable=False)
    age = Column(Integer, nullable=True)
    risk_level = Column(String(16), nullable=True)  # 保守型/稳健型/平衡型/积极型/激进型
    tags = Column(JSON, default=list)
    note = Column(Text, default="")
    available_cash = Column(Float, default=0.0)

    advisor_id = Column(String(64), ForeignKey("users.id"), nullable=True, index=True)
    # 归属用户账号（user 角色「客户/非客户」子角色管理自有持仓时关联）
    owner_user_id = Column(String(64), ForeignKey("users.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    advisor = relationship("User", back_populates="advised_clients", foreign_keys=[advisor_id])
    service_assignments = relationship(
        "ServiceAssignment", back_populates="client", cascade="all, delete-orphan"
    )
    positions = relationship("Position", back_populates="client", cascade="all, delete-orphan")
    alerts = relationship("RiskAlert", back_populates="client", cascade="all, delete-orphan")

    @property
    def service_ids(self):
        return [sa.service_id for sa in self.service_assignments]


class ServiceAssignment(Base):
    """客户与客服的多对多关联表。"""
    __tablename__ = "service_assignments"
    __table_args__ = (PrimaryKeyConstraint("client_id", "service_id"),)

    client_id = Column(String(64), ForeignKey("clients.id"), nullable=False)
    service_id = Column(String(64), ForeignKey("users.id"), nullable=False)

    client = relationship("Client", back_populates="service_assignments")
    service = relationship("User", back_populates="service_assignments")


class Position(Base):
    """持仓记录。

    注意：不存储现价（price）字段。现价一律通过实时行情获取：
    实时行情表(stock_price) → 日K线最近收盘(stock_daily_price) → 成本价兜底。
    """
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(String(64), ForeignKey("clients.id"), nullable=False, index=True)
    name = Column(String(64), nullable=False)
    code = Column(String(16), nullable=False)
    sector = Column(String(16), nullable=False)
    quantity = Column(Integer, nullable=False, default=0)
    cost_price = Column(Float, nullable=False, default=0.0)

    client = relationship("Client", back_populates="positions")


class RiskAlert(Base):
    __tablename__ = "risk_alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(String(64), ForeignKey("clients.id"), nullable=False, index=True)
    type = Column(String(16), nullable=False)   # loss / gain / sector / stock
    level = Column(String(16), nullable=False)  # high / mid / positive
    dimension = Column(String(64), nullable=True)  # 去重维度：portfolio / <股票代码> / <行业名>
    title = Column(String(128), nullable=False)
    description = Column(Text, default="")
    status = Column(String(16), nullable=False, default=ALERT_OPEN)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    client = relationship("Client", back_populates="alerts")


class Notification(Base):
    """站内消息（站内信）。"""
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    recipient_id = Column(String(64), ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(128), nullable=False)
    content = Column(Text, default="")
    category = Column(String(16), nullable=False, default=NOTIF_INFO)
    is_read = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    recipient = relationship("User", back_populates="notifications")


# ---------------------------------------------------------------------------
# 行情数据（后台定时刷新并落库；前端只读，不直接调第三方）
# ---------------------------------------------------------------------------

class MarketSnapshot(Base):
    """实时行情快照（覆盖式，永远只有 id=1 一行）。"""
    __tablename__ = "market_snapshot"

    id = Column(Integer, primary_key=True)
    snapshot_type = Column(String(16), nullable=False, default="realtime")
    data = Column(JSON, nullable=False)
    source = Column(String(32), nullable=False, default="eastmoney")
    fetch_status = Column(String(8), nullable=False, default="ok")  # ok / fail / pending
    fetch_error = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class MarketKline(Base):
    """指数日 K 线（最多 40 行/指数，insert-or-update）。

    支持多指数：上证指数(1.000001)、深证成指(0.399001)、创业板指(0.399006)、科创50(1.000688)。
    index_code + trade_date 联合唯一。
    """
    __tablename__ = "market_kline"

    id = Column(Integer, primary_key=True, autoincrement=True)
    index_code = Column(String(16), nullable=False, default="1.000001", index=True)
    trade_date = Column(String(10), nullable=False, index=True)
    open = Column(Float, nullable=True)
    close = Column(Float, nullable=True)
    high = Column(Float, nullable=True)
    low = Column(Float, nullable=True)
    volume = Column(Float, nullable=True)
    turnover = Column(Float, nullable=True)
    change_pct = Column(Float, nullable=True)
    data_source = Column(String(32), nullable=False, default="eastmoney")
    raw = Column(JSON, nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("index_code", "trade_date", name="uq_index_trade_date"),
    )


class MarketSector(Base):
    """行业板块行情（东财行业板块实时行情，每次覆盖全部 496 个行业）。"""
    __tablename__ = "market_sector"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sector_code = Column(String(16), unique=True, nullable=False, index=True)
    sector_name = Column(String(64), nullable=False)
    sector_type = Column(String(16), nullable=False, default="industry")
    change_pct = Column(Float, nullable=True)
    turnover = Column(Float, nullable=True)
    up_count = Column(Integer, nullable=True)
    down_count = Column(Integer, nullable=True)
    raw = Column(JSON, nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# 个股行情与盈亏数据
# ---------------------------------------------------------------------------

class StockPrice(Base):
    """个股实时行情（覆盖式更新，每只股票一行）。"""
    __tablename__ = "stock_price"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(16), unique=True, nullable=False, index=True)
    name = Column(String(64), nullable=True)
    current_price = Column(Float, nullable=True)
    prev_close = Column(Float, nullable=True)
    change_pct = Column(Float, nullable=True)
    change_amount = Column(Float, nullable=True)
    volume = Column(Float, nullable=True)
    turnover = Column(Float, nullable=True)
    high = Column(Float, nullable=True)
    low = Column(Float, nullable=True)
    open_price = Column(Float, nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class StockDailyPrice(Base):
    """个股日 K 线（历史数据，用于计算昨日收盘价、收益曲线基准）。"""
    __tablename__ = "stock_daily_price"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(16), nullable=False, index=True)
    trade_date = Column(String(10), nullable=False, index=True)
    open = Column(Float, nullable=True)
    close = Column(Float, nullable=True)
    high = Column(Float, nullable=True)
    low = Column(Float, nullable=True)
    volume = Column(Float, nullable=True)
    turnover = Column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint("code", "trade_date", name="uq_stock_date"),
    )


class PnLDailySnapshot(Base):
    """每日盈亏快照（客户级别的每日总资产/盈亏记录，用于绘制收益曲线）。"""
    __tablename__ = "pnl_daily_snapshot"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(String(64), ForeignKey("clients.id"), nullable=False, index=True)
    snapshot_date = Column(String(10), nullable=False, index=True)
    total_market_value = Column(Float, nullable=False, default=0.0)
    total_cost = Column(Float, nullable=False, default=0.0)
    available_cash = Column(Float, nullable=False, default=0.0)
    total_assets = Column(Float, nullable=False, default=0.0)
    daily_pnl = Column(Float, nullable=False, default=0.0)
    realized_pnl = Column(Float, nullable=False, default=0.0)
    floating_pnl = Column(Float, nullable=False, default=0.0)
    cumulative_pnl = Column(Float, nullable=False, default=0.0)
    cumulative_return_pct = Column(Float, nullable=True)
    benchmark_value = Column(Float, nullable=True)
    benchmark_return_pct = Column(Float, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("client_id", "snapshot_date", name="uq_client_snapshot_date"),
    )


class Transaction(Base):
    """交易记录（每笔买入/卖出交易的流水记录，含本笔手续费全量明细）。

    手续费支持两种模式（fee_mode）：
    - 'rate'  按费率：手续费 = 交易金额 × fee_value（如 0.00025 = 万2.5）
    - 'fixed' 固定金额：手续费 = fee_value（元/笔）
    - None    未指定时按全局配置（config.TRADING_FEE_*）计算

    fee_commission / fee_stamp_tax / fee_transfer_fee 三列分别记录 A 股
    三大类手续费明细：佣金、印花税、过户费；fee_amount 为三者合计。
    无论 fee_mode 是 rate/fixed/None，均会在写入时填齐 3 个明细列
    （rate/fixed 自定义时，仅写 fee_commission，其它两项为 0），
    确保可独立审计、对账与策略复盘费用归因。
    """
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(String(64), ForeignKey("clients.id"), nullable=False, index=True)
    code = Column(String(16), nullable=False, index=True)
    name = Column(String(64), nullable=True)
    market = Column(String(8), nullable=True)  # 市场：SH/SZ/BJ/HK/US 等
    action = Column(String(8), nullable=False)  # 'buy' / 'sell'
    quantity = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    cost_price = Column(Float, nullable=True)  # 交易完成后的最新持仓成本价（移动加权口径）
    prev_cost_price = Column(Float, nullable=True)  # 交易发生前的持仓成本价；仅加仓时与 cost_price 不同（新仓/清仓=None，减仓与 cost_price 相等）
    fee_mode = Column(String(8), nullable=True)     # 'rate' / 'fixed' / None(全局默认)
    fee_value = Column(Float, nullable=True)        # 费率值或固定金额
    fee_amount = Column(Float, nullable=False, default=0.0)  # 本笔实际手续费合计（元）
    fee_commission = Column(Float, nullable=False, default=0.0)   # 佣金
    fee_stamp_tax = Column(Float, nullable=False, default=0.0)    # 印花税
    fee_transfer_fee = Column(Float, nullable=False, default=0.0)  # 过户费
    realized_pnl = Column(Float, nullable=False, default=0.0)  # 已实现盈亏（卖出时，已扣手续费）
    trade_date = Column(String(10), nullable=False, index=True)
    executed_at = Column(DateTime, nullable=True, index=True)  # 完整执行时间戳（同秒内按 id 保证稳定排序）
    audit_log_id = Column(Integer,
                          ForeignKey("audit_logs.id", ondelete="SET NULL"),
                          nullable=True, index=True,
                          comment="关联的操作审计日志（人工调仓必填，种子/系统/公司行动可为空）")
    # 卖出批次匹配记账（FIFO 物理批次口径）：[{lot_id, buy_transaction_id, matched_quantity}]
    # 用于「撤销」时精确识别该卖出跨越的批次；买入/调整为空
    matched_lots = Column(JSON, nullable=True, comment="卖出批次匹配明细（FIFO 记账用），买入/调整为空")
    # 交易来源与操作链路溯源（v0.1.6）：区分手工调仓 / 交易截图导入 / 持仓截图导入，
    # 配合 audit_log_id（操作人/时间/动作）构成可审计的操作链路。
    source = Column(String(16), nullable=True, default="manual",
                   comment="交易来源：manual=手工调仓/调整；ocr_import=交易截图导入；holding_import=持仓截图导入")
    created_at = Column(DateTime, default=utcnow, nullable=False)


class CostBasisLot(Base):
    """成本批次表：支持 FIFO / LIFO 两种批次成本法的匹配回溯。

    每一笔买入都会产生一条批次记录，记录可卖出的剩余数量（remaining_quantity）
    和该批次含费单位成本。卖出时根据指定方法（FIFO 最早 / LIFO 最新）匹配批次，
    并按数量比例分摊买入侧费用，计算出精确的已实现盈亏。

    字段说明：
    - remaining_quantity: 剩余可匹配数量。被卖出匹配后扣减；0 表示该批次已完全平仓。
    - unit_cost_with_fee: 包含买入侧佣金、过户费的单位成本（= (price*qty + buy_fee)/qty）。
    - buy_transaction_id: 关联的买入交易流水 id，便于审计溯源。
    """
    __tablename__ = "cost_basis_lots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(String(64), ForeignKey("clients.id"), nullable=False, index=True)
    code = Column(String(16), nullable=False, index=True)
    buy_transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=False, index=True)
    original_quantity = Column(Integer, nullable=False)
    remaining_quantity = Column(Integer, nullable=False, index=True)
    unit_cost_with_fee = Column(Float, nullable=False)  # 含买入侧费用的单位成本
    buy_fee_total = Column(Float, nullable=False, default=0.0)  # 本批次买入总手续费
    executed_at = Column(DateTime, nullable=False, index=True)  # 批次时间（用于 FIFO/LIFO 排序）
    created_at = Column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_lot_client_code_remaining", "client_id", "code", "remaining_quantity"),
    )


class AuditLog(Base):
    """操作审计日志：记录所有
        - 客户信息/关系/持仓修改（target_type='client'）
        - 调仓交易尾链（target_type='transaction'，transaction.audit_log_id FK）
        - 用户账号活动（创建/删除/修改角色/登录/改密，target_type='user'）
    包括操作人、操作时间、修改前后的字段级对比，以及 IP/UA（登录事件溯源用）。"""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 操作人（users.id），可能为空（系统级操作）
    actor_user_id = Column(String(64), ForeignKey("users.id"), nullable=True, index=True)
    actor_name = Column(String(64), nullable=True)  # 冗余快照，防止用户被删除后丢失
    # 操作类别：
    #   client: client.update / client.relations / client.positions / client.delete
    #   transaction: transaction.create（人工调仓）
    #   user: user.create / user.update / user.delete / user.login_success / user.login_failed / user.password_change
    action = Column(String(32), nullable=False, index=True)
    # 目标对象：client / transaction / user 等
    target_type = Column(String(16), nullable=False, index=True)
    target_id = Column(String(64), nullable=False, index=True)
    # 修改前快照 / 修改后快照（JSON），按 action 决定包含的字段
    before_value = Column(JSON, nullable=True)
    after_value = Column(JSON, nullable=True)
    # 字段级 diff：{ field: { before, after } }，只记录发生变化的字段
    diff = Column(JSON, nullable=True)
    # 账号级活动的溯源信息（用于登录事件定位异常 IP）；非登录事件通常为 None
    ip_address = Column(String(45), nullable=True, index=True,
                        comment="操作来源 IPv4/IPv6（最长 45 = IPv4-mapped-IPv6 长度）")
    user_agent = Column(Text, nullable=True, comment="登录事件的浏览器 UA 快照；非登录通常为空")
    note = Column(String(256), nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False, index=True)


class ModuleVisibility(Base):
    """模块可见性覆盖表（配置驱动，无需改业务代码）。

    - scope_type: 'role'（角色级） | 'account'（账户级/用户ID）
    - scope_id:   角色名(role) 或 账户ID(user.id)
    - module_key: 模块标识（见 module_permission_service.MODULE_REGISTRY）
    - visible:    True=显示 / False=隐藏（覆盖默认规则）

    解析优先级：账户级(account) > 角色级(role) > 代码默认(DEFAULT_HIDDEN_FOR_ROLE)。
    未来要调整某角色/某账户的模块展示，仅通过配置或接口调用即可，无需改动业务代码。
    """
    __tablename__ = "module_visibility"

    id = Column(Integer, primary_key=True, autoincrement=True)
    scope_type = Column(String(16), nullable=False, comment="role | account")
    scope_id = Column(String(64), nullable=False, comment="角色名 或 账户ID")
    module_key = Column(String(48), nullable=False, comment="模块标识")
    visible = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "scope_type", "scope_id", "module_key",
            name="uq_module_visibility_scope",
        ),
    )


# ---------------------------------------------------------------------------
# 持仓相关资讯（后端常驻采集层 + 两表关联扇出）
#   news_item   全局资讯主表（去重，一份正文）
#   client_news 客户-资讯关联表（扇出，每客户一份匹配上下文）
#   stock_boards 板块/概念缓存表（支撑 Tier2 板块相关；空时自动跳过 Tier2）
# 建表依赖现有 Base.metadata.create_all（lifespan 启动时），无需手写迁移。
# ---------------------------------------------------------------------------

class NewsItem(Base):
    """资讯主表：全局去重，一份正文。

    news_id 用源原生 id（东财 em_<id> / 新浪 sina_<id>）天然去重；
    stock_codes 为归一化后的代码数组，如 ["600519","BK0815"]。
    first_seen 为后端首次入库时间（TTL 基准），全局只写一次。
    """

    __tablename__ = "news_item"

    news_id = Column(String(64), primary_key=True)
    source = Column(String(16), nullable=False, index=True)  # eastmoney_7x24 / sina_7x24
    title = Column(Text, nullable=False)
    summary = Column(Text, default="")
    url = Column(Text, default="")
    content = Column(Text, default="")
    published_at = Column(DateTime, nullable=True, index=True)
    first_seen = Column(DateTime, default=utcnow, nullable=False, index=True)
    stock_codes = Column(JSON, default=list)  # 归一化代码数组
    raw = Column(JSON, nullable=True)         # 原始 payload（便于排查）


class ClientNews(Base):
    """客户-资讯关联表：扇出，每客户一份匹配上下文。

    UNIQUE(client_id, news_id) 防止重复行；tier 区分个股相关/板块相关；
    matched_codes 为实际命中的客户持仓代码（用于 UI 高亮）。
    """

    __tablename__ = "client_news"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(String(64), ForeignKey("clients.id"), nullable=False, index=True)
    news_id = Column(String(64), ForeignKey("news_item.news_id"), nullable=False, index=True)
    tier = Column(SmallInteger, nullable=False, default=1)  # 1=个股相关 / 2=板块相关
    matched_codes = Column(JSON, default=list)  # 实际命中的客户持仓代码
    first_seen = Column(DateTime, default=utcnow, nullable=False)  # 该关联首次创建时间（TTL 基准）
    is_read = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("client_id", "news_id", name="uq_client_news"),
        Index("ix_client_news_client_first_seen", "client_id", "first_seen"),
    )


class StockBoards(Base):
    """个股→板块/概念缓存表（支撑 Tier2 板块相关）。

    当前无内置 BK 映射数据源（见实施计划 R2），MVP 阶段表为空、Tier2 自动跳过；
    待补齐 code→BK 映射接口后写入此处即可开启 Tier2。
    """

    __tablename__ = "stock_boards"

    code = Column(String(16), primary_key=True)  # 个股代码（已归一化，对齐 positions.code）
    board_codes = Column(JSON, default=list)      # 该股票所属板块/概念 BK 码数组
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
