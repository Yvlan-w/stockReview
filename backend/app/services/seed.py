"""演示数据种子：默认用户 + 演示客户（含持仓与关系）。幂等。"""
from sqlalchemy.orm import Session

from ..models import Client
from ..schemas import ClientCreate, PositionIn
from . import auth_service
from .client_service import create_client


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
    auth_service.seed_default_users(db)
    if db.query(Client).count() == 0:
        for data in _DEMO_CLIENTS:
            create_client(db, data)
