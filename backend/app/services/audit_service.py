"""操作审计日志服务：统一记录日志入口，生成字段级 diff。"""
from __future__ import annotations

from typing import Any, Iterable
from sqlalchemy.orm import Session

from ..models import AuditLog, User


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


FIELDS_BASIC = ("name", "age", "risk_level", "tags", "note", "available_cash")
FIELDS_RELATIONS = ("advisor_id", "service_ids")


def log_client_change(
    db: Session,
    *,
    actor: User | None,
    action: str,         # client.update / client.relations
    client,               # 修改后的 Client 对象（已 refresh）
    before: dict,         # 修改前快照
    after: dict,          # 修改后快照
    fields: Iterable[str],
    note: str | None = None,
) -> AuditLog | None:
    """记录一条客户信息变更审计日志；若没有变化则返回 None 不写库。"""
    diff = _compute_diff(before, after, fields)
    if not diff:
        return None  # 无实际变化不记录
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
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


__all__ = [
    "log_client_change",
    "_snapshot_client_basic",
    "_snapshot_client_relations",
    "FIELDS_BASIC",
    "FIELDS_RELATIONS",
    "_compute_diff",
]
