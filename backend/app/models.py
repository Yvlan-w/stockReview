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
    Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text,
    UniqueConstraint, PrimaryKeyConstraint,
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

ROLES = (ROLE_GUEST, ROLE_USER, ROLE_ADVISOR, ROLE_SERVICE, ROLE_ADMIN)
ROLE_LEVEL = {ROLE_GUEST: 0, ROLE_USER: 1, ROLE_ADVISOR: 2, ROLE_SERVICE: 3, ROLE_ADMIN: 4}

# 风险预警状态
ALERT_OPEN = "open"
ALERT_ACK = "acknowledged"
ALERT_RESOLVED = "resolved"

# 站内信类别
NOTIF_RISK = "risk_alert"
NOTIF_INFO = "info"
NOTIF_SYSTEM = "system"


def utcnow():
    return _dt.datetime.now(_dt.timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(String(64), primary_key=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    role = Column(String(16), nullable=False, default=ROLE_GUEST)
    sub_role = Column(String(16), nullable=True)  # client / non_client（仅 user 角色）
    name = Column(String(64), nullable=False)
    email = Column(String(128), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
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

    advisor_id = Column(String(64), ForeignKey("users.id"), nullable=False, index=True)
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
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(String(64), ForeignKey("clients.id"), nullable=False, index=True)
    name = Column(String(64), nullable=False)
    code = Column(String(16), nullable=False)
    sector = Column(String(16), nullable=False)
    quantity = Column(Integer, nullable=False, default=0)
    cost_price = Column(Float, nullable=False, default=0.0)
    price = Column(Float, nullable=False, default=0.0)

    client = relationship("Client", back_populates="positions")


class RiskAlert(Base):
    __tablename__ = "risk_alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(String(64), ForeignKey("clients.id"), nullable=False, index=True)
    type = Column(String(16), nullable=False)   # loss / sector / stock
    level = Column(String(16), nullable=False)  # high / mid
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
