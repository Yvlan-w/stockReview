"""模块可见性（页面/板块模块显隐）权限体系。

设计目标
--------
- 把"哪些角色 / 哪些账户能看到哪些页面模块"抽象为一套**可配置**的模块权限体系；
- 默认规则集中在 DEFAULT_HIDDEN_FOR_ROLE（代码基线），但可被数据库中的覆盖规则改写；
- 提供统一接口 get_visible_modules / is_module_visible / set_visibility / reset_visibility，
  未来无需改动业务代码，仅凭「配置」或「接口调用」即可管理各角色、各账户的模块展示状态。

覆盖解析优先级（从高到低）
------------------------
账户级(account) > 角色级(role) > 代码默认。
"""
from typing import Optional

from sqlalchemy.orm import Session

from ..models import ModuleVisibility

# ============================================================
# 1. 模块注册表（单一事实来源；新增可配置模块只需在此登记）
# ============================================================
MODULE_REGISTRY = [
    {"key": "market_overview",    "name": "今日盯大盘", "category": "market",  "description": "四大指数 / 两市成交额 / 涨跌家数"},
    {"key": "index_turnover",     "name": "指数成交额", "category": "market",  "description": "近一月日成交额走势"},
    {"key": "sector_performance", "name": "板块表现",   "category": "market",  "description": "板块成交额 / 涨跌幅热力图"},
    {"key": "market_analysis",    "name": "市场分析",   "category": "market",  "description": "领涨方向 / 驱动因素"},
    {"key": "market_ticker",      "name": "顶部行情条", "category": "market",  "description": "顶部滚动行情"},
    {"key": "live_news",          "name": "实时资讯",   "category": "market",  "description": "市场资讯流"},
    {"key": "client_workbench",   "name": "客户工作台", "category": "client",  "description": "客户列表与详情"},
    {"key": "position_detail",    "name": "持仓明细",   "category": "client",  "description": "持仓表格与图表"},
    {"key": "strategy_review",    "name": "策略复盘",   "category": "client",  "description": "买卖复盘时间线"},
    {"key": "risk_warning",       "name": "风险预警",   "category": "client",  "description": "风险预警与处理"},
    {"key": "notifications",      "name": "站内信",     "category": "system",  "description": "通知与站内信"},
    {"key": "admin_panel",        "name": "管理后台",   "category": "system",  "description": "账户 / 关系 / 审计管理"},
]

ALL_MODULE_KEYS = [m["key"] for m in MODULE_REGISTRY]

# ============================================================
# 2. 代码基线：默认隐藏某模块的角色集合
# ============================================================
# 当前需求：客服(service) 隐藏「今日盯大盘 / 指数成交额 / 板块表现」三个模块；
# 其他角色（guest / user / advisor / admin）全部显示。
DEFAULT_HIDDEN_FOR_ROLE = {
    "market_overview": {"service"},
    "index_turnover": {"service"},
    "sector_performance": {"service"},
}

SCOPE_ROLE = "role"
SCOPE_ACCOUNT = "account"


# ============================================================
# 3. 解析逻辑
# ============================================================
def _default_visible(module_key: str, role: str) -> bool:
    """无数据库覆盖时的默认可见性。"""
    hidden_roles = DEFAULT_HIDDEN_FOR_ROLE.get(module_key)
    if not hidden_roles:
        return True
    return role not in hidden_roles


def get_db_override(db: Session, scope_type: str, scope_id: str, module_key: str) -> Optional[ModuleVisibility]:
    return db.query(ModuleVisibility).filter_by(
        scope_type=scope_type, scope_id=str(scope_id), module_key=module_key
    ).first()


def get_effective_visibility(db: Session, role: str, account_id: Optional[str] = None) -> dict:
    """返回 {module_key: bool} 全量有效可见性（账户级 > 角色级 > 默认）。"""
    result: dict = {}
    for m in MODULE_REGISTRY:
        key = m["key"]
        # 默认 / 角色级覆盖
        role_row = get_db_override(db, SCOPE_ROLE, role, key)
        effective = role_row.visible if role_row is not None else _default_visible(key, role)
        # 账户级覆盖（最高优先级）
        if account_id:
            acct_row = get_db_override(db, SCOPE_ACCOUNT, account_id, key)
            if acct_row is not None:
                effective = acct_row.visible
        result[key] = effective
    return result


def is_module_visible(db: Session, module_key: str, role: str, account_id: Optional[str] = None) -> bool:
    return get_effective_visibility(db, role, account_id).get(module_key, True)


def get_visible_modules(db: Session, role: str, account_id: Optional[str] = None) -> list:
    vis = get_effective_visibility(db, role, account_id)
    return [k for k, v in vis.items() if v]


def get_hidden_modules(db: Session, role: str, account_id: Optional[str] = None) -> list:
    vis = get_effective_visibility(db, role, account_id)
    return [k for k, v in vis.items() if not v]


# ============================================================
# 4. 写接口（覆盖规则 upsert / 重置）—— 供管理接口 / 配置脚本调用
# ============================================================
def set_visibility(db: Session, scope_type: str, scope_id: str, module_key: str, visible: bool) -> dict:
    """写入 / 更新一条覆盖规则（upsert）。返回本次变更信息（不含完整矩阵）。"""
    if module_key not in ALL_MODULE_KEYS:
        raise ValueError(f"未知模块: {module_key}")
    if scope_type not in (SCOPE_ROLE, SCOPE_ACCOUNT):
        raise ValueError(f"非法 scope_type: {scope_type}")
    scope_id = str(scope_id)

    row = get_db_override(db, scope_type, scope_id, module_key)
    if row is None:
        row = ModuleVisibility(
            scope_type=scope_type, scope_id=scope_id, module_key=module_key, visible=visible
        )
        db.add(row)
    else:
        row.visible = visible
    db.commit()
    db.refresh(row)

    return {
        "scope_type": scope_type,
        "scope_id": scope_id,
        "module_key": module_key,
        "visible": visible,
    }


def reset_visibility(db: Session, scope_type: str, scope_id: str, module_key: str) -> None:
    """删除覆盖规则，使该模块恢复默认（或回退到更低优先级的规则）。"""
    row = get_db_override(db, scope_type, scope_id, module_key)
    if row is not None:
        db.delete(row)
        db.commit()


# ============================================================
# 5. 管理视图辅助
# ============================================================
def get_role_matrix(db: Session) -> dict:
    """管理员视图：每个角色的「角色级」有效可见性（不含账户级覆盖）。"""
    from ..models import ROLES
    matrix = {}
    for role in ROLES:
        matrix[role] = get_effective_visibility(db, role, account_id=None)
    return matrix


def list_overrides(db: Session) -> list:
    rows = db.query(ModuleVisibility).order_by(ModuleVisibility.id).all()
    return [
        {
            "id": r.id,
            "scope_type": r.scope_type,
            "scope_id": r.scope_id,
            "module_key": r.module_key,
            "visible": r.visible,
        }
        for r in rows
    ]
