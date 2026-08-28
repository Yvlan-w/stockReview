"""操作审计日志服务：统一记录日志入口，生成字段级 diff。"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from ..models import AuditLog, User, Transaction

logger = logging.getLogger(__name__)


def _compute_diff(before: dict | None, after: dict | None, fields: Iterable[str]) -> dict[str, dict]:
    """计算 before 和 after 中指定 fields 的差异，返回 {field: {before, after}}，仅包含变化项。"""
    diff: dict[str, dict] = {}
    b = before or {}
    a = after or {}
    for f in fields:
        bv = b.get(f)
        av = a.get(f)
        if bv != av:
            diff[f] = {"before": bv, "after": av}
    return diff


def _snapshot_client_basic(client) -> dict:
    return {
        "name": client.name,
        "age": client.age,
        "risk_level": client.risk_level,
        "tags": list(client.tags) if client.tags else [],
        "note": client.note or "",
        "available_cash": float(client.available_cash or 0.0),
    }


def _snapshot_client_relations(client) -> dict:
    return {
        "advisor_id": client.advisor_id,
        "service_ids": list(client.service_ids) if client.service_ids else [],
    }


def _snapshot_transaction(tx: Transaction) -> dict:
    return {
        "id": tx.id,
        "client_id": tx.client_id,
        "code": tx.code,
        "name": tx.name,
        "market": tx.market,
        "action": tx.action,
        "quantity": tx.quantity,
        "price": float(tx.price or 0.0),
        "cost_price": float(tx.cost_price) if tx.cost_price is not None else None,
        "prev_cost_price": float(tx.prev_cost_price) if tx.prev_cost_price is not None else None,
        "fee_amount": float(tx.fee_amount or 0.0),
        "fee_commission": float(tx.fee_commission or 0.0),
        "fee_stamp_tax": float(tx.fee_stamp_tax or 0.0),
        "fee_transfer_fee": float(tx.fee_transfer_fee or 0.0),
        "realized_pnl": float(tx.realized_pnl or 0.0),
        "trade_date": tx.trade_date,
        "executed_at": tx.executed_at.isoformat() if tx.executed_at else None,
        "from_cash": None,  # 记录在 note 中
    }


FIELDS_BASIC = ("name", "age", "risk_level", "tags", "note", "available_cash")
FIELDS_RELATIONS = ("advisor_id", "service_ids")


def log_client_change(
    db: Session,
    *,
    actor: User | None,
    action: str,         # client.create / client.update / client.relations / client.delete
    client,               # Client 对象（已 refresh；create 场景也要 refresh 过）
    before: dict | None,  # 修改前快照；创建时为 None
    after: dict,          # 修改后快照
    fields: Iterable[str] | None = None,  # 仅 client.create / client.delete 时可 None
    note: str | None = None,
    ip_address: str | None = None,
) -> AuditLog | None:
    """记录一条客户级审计日志；没有实际变化（update/relations diff 为空）返回 None。

    写入失败：logging.warning + db.rollback()，不抛出异常（不阻塞主业务）。
    """
    if action in ("client.create", "client.delete"):
        diff = {"client": {"before": before, "after": after}}
    else:
        if fields is None:
            raise ValueError(f"action={action} 需要显式传 fields 白名单")
        if before is None:
            return None
        diff = _compute_diff(before, after, tuple(fields))
        if not diff:
            return None
    log = AuditLog(
        actor_user_id=actor.id if actor is not None else None,
        actor_name=actor.name if actor is not None else None,
        action=action,
        target_type="client",
        target_id=client.id,
        before_value=before,
        after_value=after,
        diff=diff,
        note=note,
        ip_address=ip_address,
    )
    try:
        db.add(log)
        db.commit()
        db.refresh(log)
        return log
    except SQLAlchemyError as e:
        logger.warning("client 审计日志写入失败（已回滚）：action=%s target=%s err=%s",
                       action, client.id, e)
        db.rollback()
        return None


def build_transaction_audit_log(
    db: Session,
    *,
    actor: User | None,
    client,
    transaction: Transaction,
    from_cash: bool = True,
    note: str | None = None,
) -> AuditLog:
    """构建一条调仓交易的审计日志，不 commit，仅 db.add + db.flush 后返回。

    目的：使 AuditLog 与 Transaction/CostBasisLot/Position/现金/快照 处在同一个
         外层原子事务中，要么同生要么同灭，避免幽灵审计记录。

    返回的 AuditLog 已经具备 id，可回填 transaction.audit_log_id。
    """
    after = _snapshot_transaction(transaction)
    # 调仓属于"创建新流水"，没有 before 快照；diff 用"空 → 新交易"描述
    diff = {"transaction": {"before": None, "after": after}}
    action_text = "交易：买入" if transaction.action == "buy" else "交易：卖出"
    if not from_cash and transaction.action == "buy":
        action_text += "（外部转入，不从可用资金扣减）"

    log = AuditLog(
        actor_user_id=actor.id if actor is not None else None,
        actor_name=actor.name if actor is not None else None,
        action="transaction.create",
        target_type="transaction",
        target_id=str(transaction.id),
        before_value=None,
        after_value=after,
        diff=diff,
        note=note or f"Client:{client.id}  {action_text}  "
                     f"{transaction.code} {transaction.quantity}股 @¥{transaction.price}",
    )
    db.add(log)
    db.flush()  # 拿 id，但不 commit（保证和 transaction 同事务）
    return log


def log_user_activity(
    db: Session,
    *,
    actor: User | None,
    action: str,
    target_user: User | None = None,
    target_user_id: str | None = None,
    before: dict | None = None,
    after: dict | None = None,
    fields: Iterable[str] | None = None,
    note: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> AuditLog | None:
    """记录一条用户账号级活动（创建/删除/修改/登录/改密）。

    Args:
        actor: 触发动作的操作人（user.delete / user.update 场景下是 admin；
               user.login_success 场景下 actor=登录成功的用户自己；
               user.login_failed 场景下 actor 为 None）
        action: 'user.create' / 'user.update' / 'user.delete'
               / 'user.login_success' / 'user.login_failed' / 'user.password_change'
        target_user: 被操作的用户对象。删除/更新/改密码时建议传，用于生成 before 快照。
        target_user_id: 当 target_user 不可得（login_failed 用户名不存在 或 对象已删）时
            直接传 id 字符串，保证 target_id 不为空。
        before: 操作前快照（若不传且有 target_user，会自动生成简化快照）
        after:  操作后快照（若不传且有 target_user，会自动生成简化快照）
        fields: 字段级 diff 的对比字段白名单；None 时对所有快照字段做 diff。
            登录成功/失败场景无字段变化，可传空。
        note: 备注（如"登录失败原因：密码错误"）。
        ip_address: 请求来源 IP（从 FastAPI Request.client.host 取）。
        user_agent: 浏览器 UA 字符串，登录事件建议必传。

    Returns:
        有实际字段级 diff 或 登录类动作 → 返回已 commit 的 AuditLog；
        没有实际变化的 update（diff 空）→ 返回 None，不写库。
    """
    allowed = {
        "user.create", "user.update", "user.delete", "user.delete_self",
        "user.login_success", "user.login_failed", "user.password_change",
        "user.renew", "user.password_reset", "user.lifecycle",
        "user.create_client_profile",   # admin 为已存在的 user-client 账户补齐客户档案
    }
    if action not in allowed:
        raise ValueError(f"未知用户活动 action={action}，允许值: {allowed}")

    def _snapshot_user(u) -> dict:
        if u is None:
            return {}
        snap: dict[str, Any] = {
            "id": u.id,
            "username": u.username,
            "name": u.name,
            "email": getattr(u, "email", None) or "",
            "role": u.role,
            "is_active": bool(getattr(u, "is_active", True)),
            # password_hash 永远不序列化
        }
        # 可选字段：User 模型后续扩展才会出现，用 getattr 兜底避免 AttributeError
        phone = getattr(u, "phone", None)
        if phone is not None:
            snap["phone"] = phone or ""
        sub_role = getattr(u, "sub_role", None)
        if sub_role is not None:
            snap["sub_role"] = sub_role
        return snap

    if before is None and target_user is not None:
        before = _snapshot_user(target_user)
    if after is None and target_user is not None and action != "user.delete":
        after = _snapshot_user(target_user)
    if after is None and action == "user.create" and target_user is not None:
        after = _snapshot_user(target_user)

    # diff：登录成功/失败、创建、删除不强制要求字段 diff（这些是动作本身有意义）
    diff: dict | None = None
    if action == "user.update" or action == "user.password_change":
        # 改密码场景：password 字段不存明文，用 masked 标记
        if action == "user.password_change":
            diff = {"password": {"before": "***", "after": "***"}}
        else:
            compare_fields = tuple(fields) if fields else tuple(_snapshot_user(target_user).keys())
            diff = _compute_diff(before, after, compare_fields)
            if not diff:
                return None  # update 没有实际变化，不写库

    # target_id 优先级：显式传的 target_user_id > target_user.id
    t_id = target_user_id or (target_user.id if target_user else None)
    if not t_id:
        # login_failed 场景下 target_user 可能是 None 也没 target_user_id，退化为用户名（若能从 note 推断则更友好）
        t_id = "unknown"

    log = AuditLog(
        actor_user_id=actor.id if actor is not None else None,
        actor_name=actor.name if actor is not None else None,
        action=action,
        target_type="user",
        target_id=str(t_id),
        before_value=before,
        after_value=after,
        diff=diff,
        note=note,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    try:
        db.add(log)
        db.commit()
        db.refresh(log)
        return log
    except SQLAlchemyError as e:
        # 审计失败不要反过来影响主业务（登录、改密、创建用户都是关键路径）
        logger.warning("user 审计日志写入失败（已回滚）：action=%s target=%s actor=%s err=%s",
                       action, t_id, actor.id if actor else None, e)
        db.rollback()
        return None


__all__ = [
    "log_client_change",
    "_snapshot_client_basic",
    "_snapshot_client_relations",
    "FIELDS_BASIC",
    "FIELDS_RELATIONS",
    "_compute_diff",
    "build_transaction_audit_log",
    "_snapshot_transaction",
    "log_user_activity",
]
