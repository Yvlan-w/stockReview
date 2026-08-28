"""数据库结构变更登记（纯审计，不执行任何 DDL）。

每条变更由执行代码在启动时核对，确保部署后真实数据库结构与期望一致。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class MigrationEntry:
    introduced_version: str   # 首次引入此变更的镜像版本
    description: str          # 变更内容描述（给人读）
    table: str                # 目标表
    kind: str                 # "new_table" / "add_column" / "add_index"
    identifier: str           # 唯一标识：列名 / 索引名 / 新表名

    # add_column 专用（可选）
    column_type: str | None = None


# ========== 登记已上线的变更 ==========
CHANGELOG: List[MigrationEntry] = [
    MigrationEntry(
        introduced_version="v0.1.1",
        description="风险预警按维度保留最新一条",
        table="risk_alerts",
        kind="add_column",
        identifier="dimension",
        column_type="VARCHAR(64)",
    ),
    MigrationEntry(
        introduced_version="v0.1.4",
        description="操作审计日志表：记录客户信息、关系、权限等所有修改操作，含前后值与字段级 diff",
        table="audit_logs",
        kind="new_table",
        identifier="audit_logs",
    ),
    MigrationEntry(
        introduced_version="v0.1.4",
        description="客户服务人员展示名：由后端从 users 表关联查询返回，避免前端静态池与真实数据不一致",
        table="clients",
        kind="add_index",   # 逻辑级说明：service_assignments → service 关联已存在，无需新增列
        identifier="service_names_via_users",
    ),
    MigrationEntry(
        introduced_version="v0.1.4",
        description="放开 clients.advisor_id NOT NULL：允许未分配投顾/客服（留空由 owner_user_id 本人登录管理持仓）",
        table="clients",
        kind="add_column",  # 结构变更审计：老库启动时走"建 new→拷→drop→rename"重建
        identifier="advisor_id_nullable",
        column_type="VARCHAR(64)",
    ),
    MigrationEntry(
        introduced_version="v0.1.5",
        description="账户生命周期：users 增加 status/expires_at；到期自动变 expired 禁止登录，支持续费/充值/重置密码/软删除",
        table="users",
        kind="add_column",
        identifier="status",
        column_type="VARCHAR(16)",
    ),
    MigrationEntry(
        introduced_version="v0.1.5",
        description="账户生命周期：expires_at 到期日；admin=null 表示永久，其余按赠送/续费计算",
        table="users",
        kind="add_column",
        identifier="expires_at",
        column_type="DATETIME",
    ),
]


def diff_against_expected(engine) -> List[str]:
    """对比当前 SQLite 实际结构与 changelog 期望，返回差异描述列表（空 = 一致）。

    仅检查 add_column 类变更；新表类由 create_all 自动覆盖。加索引类如后续需要可扩展。
    """
    from sqlalchemy import text

    diffs: List[str] = []
    with engine.connect() as conn:
        for m in CHANGELOG:
            if m.kind != "add_column":
                continue
            cols = [row[1] for row in conn.execute(text(f"PRAGMA table_info({m.table})"))]
            if m.identifier not in cols:
                diffs.append(
                    f"[{m.introduced_version}] {m.table}.{m.identifier}({m.column_type})  "
                    f"{m.description}  ——实际列不存在，依赖启动阶段 _migrate_sqlite_columns 补齐"
                )
    return diffs
