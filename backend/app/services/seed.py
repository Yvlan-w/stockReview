"""数据库种子。

- seed_all(db)：生产启动种子——仅幂等创建唯一管理员账号，不含任何模拟数据。
- seed_demo_data(db)：演示数据（演示用户 + 演示客户），仅供 pytest 使用，生产不调用。
"""
from sqlalchemy.orm import Session

from ..models import (
    User, ROLE_ADMIN, ROLE_ADVISOR, ROLE_SERVICE, ROLE_USER, ROLE_GUEST,
    SUBROLE_CLIENT, SUBROLE_NON_CLIENT, Client,
)
from ..schemas import ClientCreate, PositionIn
from ..core.security import hash_password
from . import auth_service
from .client_service import create_client


# 演示账号密码（仅测试环境；生产环境由管理员在界面创建真实账号）
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
            _p("万华化学", "600309", "化工", 1000, 78, 60),
            _p("长江电力", "600900", "公用", 2000, 24, 25),
        ],
    ),
]


def seed_all(db: Session) -> None:
    """生产启动种子：仅创建管理员账号（幂等），无任何模拟数据。"""
    auth_service.seed_default_users(db)


def _ensure_demo_user(db: Session, uid: str, username: str, name: str, role: str, sub_role: str | None = None) -> None:
    if db.get(User, uid) is None:
        db.add(User(
            id=uid, username=username, name=name, role=role, sub_role=sub_role,
            password_hash=hash_password(DEMO_PASSWORD),
        ))


def seed_demo_data(db: Session) -> None:
    """演示数据（仅供 pytest）：演示用户 + 演示客户。幂等。"""
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
