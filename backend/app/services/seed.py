"""数据库种子入口。

分层：
- seed_all(db)           → 应用启动时调用，由 RUN_SEED + 空库门闩控制是否创建 admin
- seed_demo_data(db)     → 演示数据（演示用户+演示客户），仅供 pytest 或手动脚本显式调用

关键原则：
- 生产默认 RUN_SEED=first：仅当 users AND clients 双表为空时，才创建 admin 单账号
- 非空库启动时 seed_all 对任何业务表零 DML（绝不能改已有用户/客户/持仓数据）
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..config import RUN_SEED
from ..models import Client, User
from . import auth_service
from . import account  # noqa: F401   ——创建 client id 拼音逻辑依赖初始化


def _seed_admin(db: Session) -> None:
    """仅幂等创建 admin，不 touch 任何其他表。"""
    auth_service.ensure_admin_user(db)


def seed_all(db: Session) -> None:
    """应用启动入口：由 RUN_SEED 配置 + 空库双重门闩控制。"""
    if RUN_SEED == "never":
        return

    if RUN_SEED == "first":
        users_empty = db.query(User).count() == 0
        clients_empty = db.query(Client).count() == 0
        if not (users_empty and clients_empty):
            return

    _seed_admin(db)


# ========= 以下为演示数据（pytest / 手动脚本显式调用），生产永不执行 =========
from ..core.security import hash_password  # noqa: E402
from ..models import (  # noqa: E402
    ROLE_ADVISOR, ROLE_GUEST, ROLE_SERVICE, ROLE_USER,
    SUBROLE_CLIENT, SUBROLE_NON_CLIENT,
)
from ..schemas import ClientCreate, PositionIn  # noqa: E402
from .client_service import create_client  # noqa: E402

DEMO_PASSWORD = "123456"

_DEMO_ADVISORS = [
    ("adv_001", "顾问·张伟"),
    ("adv_002", "顾问·李娜"),
    ("adv_003", "顾问·王强"),
    ("adv_004", "顾问·刘敏"),
    ("adv_005", "顾问·陈静"),
]
_DEMO_SERVICES = [
    ("svc_001", "客服·赵芳"),
    ("svc_002", "客服·钱磊"),
    ("svc_003", "客服·孙婷"),
    ("svc_004", "客服·周明"),
    ("svc_005", "客服·吴娜"),
    ("svc_006", "客服·郑浩"),
]


def _p(name, code, sector, quantity, cost_price, price):
    return PositionIn(name=name, code=code, sector=sector, quantity=quantity,
                      cost_price=cost_price, price=price)


_DEMO_CLIENTS = [
    ClientCreate(
        id="C001", name="张伟", age=42, risk_level="稳健型", tags=["VIP", "高净值"],
        note="偏好低波动，关注高股息分红", available_cash=180000,
        advisor_id="adv_001", service_ids=["svc_001", "svc_002"], owner_user_id="u_client_demo",
        positions=[
            _p("贵州茅台", "600519", "消费", 100, 1680, 1428),
            _p("招商银行", "600036", "金融", 2000, 40, 34),
        ],
    ),
    ClientCreate(
        id="C002", name="李娜", age=35, risk_level="平衡型", tags=[],
        note="关注新能源与科技成长", available_cash=90000,
        advisor_id="adv_002", service_ids=["svc_001"],
        positions=[
            _p("宁德时代", "300750", "新能源", 500, 200, 210),
            _p("比亚迪", "002594", "新能源", 300, 250, 240),
            _p("隆基绿能", "601012", "光伏", 1000, 24, 22),
        ],
    ),
    ClientCreate(
        id="C003", name="王强", age=51, risk_level="积极型", tags=["活跃"],
        note="风险承受能力较强，可谈权益加仓", available_cash=60000,
        advisor_id="adv_003", service_ids=["svc_003", "svc_004"],
        positions=[
            _p("中芯国际", "688981", "科技", 5000, 50, 55),
            _p("贵州茅台", "600519", "消费", 50, 1680, 1700),
        ],
    ),
    ClientCreate(
        id="C004", name="刘敏", age=46, risk_level="稳健型", tags=["稳健偏好"],
        note="希望平衡收益与回撤", available_cash=150000,
        advisor_id="adv_004", service_ids=["svc_005"],
        positions=[
            _p("贵州茅台", "600519", "消费", 50, 1680, 1690),
            _p("招商银行", "600036", "金融", 1500, 35, 36),
            _p("美的集团", "000333", "家电", 800, 58, 59),
            _p("恒瑞医药", "600276", "医疗", 600, 45, 46),
        ],
    ),
    ClientCreate(
        id="C005", name="陈静", age=39, risk_level="激进型", tags=["待跟进"],
        note="近期考虑增加债券配置", available_cash=40000,
        advisor_id="adv_005", service_ids=["svc_006"],
        positions=[
            # 万华化学：成本 200000 / 市值 120000 → 占比 68.6%（stock high），亏 80000
            _p("万华化学", "600309", "化工", 2000, 100, 60),
            # 紫金矿业：成本 88000 / 市值 55000 → 占比 31.4%（stock mid），亏 33000
            _p("紫金矿业", "601899", "化工", 1100, 80, 50),
            # 合计：总成本 288000，总市值 175000 → 浮亏 -39.2%（激进型 ≤-25% 触发 loss high）
            # 化工行业 100% → sector high；共 4 条预警：loss/sector high + stock high/mid
        ],
    ),
]


def _ensure_demo_user(db, uid, username, name, role, sub_role=None):
    if db.get(User, uid) is None:
        db.add(User(
            id=uid, username=username, name=name, role=role, sub_role=sub_role,
            password_hash=hash_password(DEMO_PASSWORD),
        ))


def seed_demo_data(db: Session) -> None:
    """演示数据（演示用户 + 演示客户）。幂等。仅 pytest / 手动脚本显式调用。"""
    _seed_admin(db)
    _ensure_demo_user(db, "u_guest", "guest", "游客", ROLE_GUEST)
    _ensure_demo_user(db, "u_client_demo", "client001", "客户·演示", ROLE_USER, SUBROLE_CLIENT)
    _ensure_demo_user(db, "u_user_demo", "user001", "普通用户·演示", ROLE_USER, SUBROLE_NON_CLIENT)
    for uid, name in _DEMO_ADVISORS:
        _ensure_demo_user(db, uid, uid, name, ROLE_ADVISOR)
    for uid, name in _DEMO_SERVICES:
        _ensure_demo_user(db, uid, uid, name, ROLE_SERVICE)
    db.commit()
    if db.query(Client).count() == 0:
        for data in _DEMO_CLIENTS:
            create_client(db, data)
