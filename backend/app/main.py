"""FastAPI 应用入口：组装路由、启动建表与种子数据、同源托管前端。"""
import asyncio
import logging
import os
import random

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse

from .api.routes import router
from .api.ws import ws_router
from .api.ocr import ocr_router
from .config import (
    FRONTEND_DIR, MARKET_REFRESH_INTERVAL_REALTIME, MARKET_REFRESH_INTERVAL_REALTIME_OFF,
    MARKET_REFRESH_INTERVAL_KLINE, MARKET_REFRESH_INTERVAL_SECTOR,
    MARKET_REFRESH_INTERVAL_SECTOR_OFF,
    STOCK_REFRESH_INTERVAL, STOCK_REFRESH_INTERVAL_OFF,
    NEWS_INGEST_INTERVAL_SEC, NEWS_INGEST_INTERVAL_OFF, NEWS_INGEST_JITTER,
)
from .database import Base, SessionLocal, engine
from .services import market_service
from .services.seed import seed_all

logger = logging.getLogger(__name__)
_bg_tasks: list[asyncio.Task] = []


async def _realtime_refresh_loop():
    """实时行情：交易期每 20s 刷一次，非交易期每 120s。"""
    await asyncio.sleep(3)
    try:
        await market_service.refresh_realtime()
    except Exception as e:
        logger.warning("启动时实时行情初始刷新失败: %s", e)

    while True:
        try:
            interval = (MARKET_REFRESH_INTERVAL_REALTIME
                        if market_service.is_trading_hours()
                        else MARKET_REFRESH_INTERVAL_REALTIME_OFF)
            await asyncio.sleep(interval)
            await market_service.refresh_realtime()
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.warning("实时行情刷新异常（继续）: %s", e)


async def _kline_refresh_loop():
    """K 线增量刷新：
    - 空库 → 全量初始化 40 天
    - 有 computed 数据 → 触发精确源升级
    - 正常 → 增量刷新最近 5 天
    循环周期 MARKET_REFRESH_INTERVAL_KLINE 秒。
    """
    await asyncio.sleep(5)
    try:
        await market_service.refresh_kline()
    except Exception as e:
        logger.warning("启动时 K 线初始刷新失败: %s", e)

    while True:
        try:
            await asyncio.sleep(MARKET_REFRESH_INTERVAL_KLINE)
            await market_service.refresh_kline()
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.warning("K 线刷新异常（继续）: %s", e)


async def _sector_refresh_loop():
    """行业板块：交易期每 30s 刷一次，非交易期每 300s。"""
    await asyncio.sleep(4)
    try:
        await market_service.refresh_sectors()
    except Exception as e:
        logger.warning("启动时板块初始刷新失败: %s", e)

    while True:
        try:
            interval = (MARKET_REFRESH_INTERVAL_SECTOR
                        if market_service.is_trading_hours()
                        else MARKET_REFRESH_INTERVAL_SECTOR_OFF)
            await asyncio.sleep(interval)
            await market_service.refresh_sectors()
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.warning("板块刷新异常（继续）: %s", e)


async def _stock_price_refresh_loop():
    """个股行情：交易期每 20s 刷一次，非交易期每 120s。刷新后写入当日盈亏快照。

    每日（收盘后首次刷新时）自动执行一次快照完整性校验：
    缺失交易日 / 非交易日快照 / 连续同值脏数据 → 记录告警日志，
    可通过 POST /api/admin/snapshots/recalculate 修复。
    """
    await asyncio.sleep(6)
    try:
        from .services.stock_price_service import refresh_stock_prices
        await refresh_stock_prices()
    except Exception as e:
        logger.warning("启动时个股行情初始刷新失败: %s", e)

    last_check_date = None
    while True:
        try:
            interval = (STOCK_REFRESH_INTERVAL
                        if market_service.is_trading_hours()
                        else STOCK_REFRESH_INTERVAL_OFF)
            await asyncio.sleep(interval)

            # 刷新个股行情
            from .services.stock_price_service import refresh_stock_prices
            from .services.pnl_service import (
                write_all_daily_snapshots, validate_snapshot_integrity,
            )
            db = SessionLocal()
            try:
                try:
                    await refresh_stock_prices(db)
                except Exception as e:
                    # 行情刷新失败不阻断快照写入（compute_portfolio 有降级价格链）
                    logger.warning("个股行情刷新失败（快照仍按降级价格写入）: %s", e)
                # 写入当日盈亏快照（无论行情刷新是否成功，均有价格降级链兜底）
                write_all_daily_snapshots(db)

                # 每日一次完整性校验（新交易日首次刷新时触发）
                today = _local_date_str()
                if today != last_check_date:
                    last_check_date = today
                    try:
                        result = validate_snapshot_integrity(db)
                        if not result["ok"]:
                            logger.warning(
                                "[每日快照完整性校验] %s；"
                                "可调用 POST /api/admin/snapshots/recalculate 修复",
                                result["summary"])
                    except Exception as e:
                        logger.warning("快照完整性校验异常（继续）: %s", e)
                    # 数据一致性对账（持仓/流水/现金跨表核对）：
                    # FATAL 不一致向管理员发送站内信告警
                    try:
                        from .services.consistency_service import (
                            check_consistency, report_consistency_alert,
                        )
                        consistency = check_consistency(db)
                        if consistency["fatals"]:
                            n = report_consistency_alert(db, consistency["fatals"])
                            logger.warning(
                                "[每日数据一致性对账] 发现 %d 项 FATAL，"
                                "已向管理员发送 %d 条告警站内信",
                                len(consistency["fatals"]), n)
                        else:
                            logger.info(
                                "[每日数据一致性对账] 通过（warn=%d）",
                                len(consistency["warnings"]))
                    except Exception as e:
                        logger.warning("数据一致性对账异常（继续）: %s", e)
            finally:
                db.close()

        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.warning("个股行情刷新异常（继续）: %s", e)


def _local_date_str() -> str:
    """当前本地日期（Asia/Shanghai 语境下的服务器本地时区）。"""
    import datetime as dt
    return dt.date.today().isoformat()


async def _news_ingest_loop():
    """持仓相关资讯常驻采集层：交易期每 60s、非交易期每 300s 抓取一次 7×24 快讯，
    解析结构化个股/板块代码 → 匹配客户持仓 → 写入 client_news 关联表。

    单实例假设（当前单容器）直接循环；多 worker 需加 advisory lock（见计划 R3）。
    任一周期异常被捕获后继续，避免任务退出。
    """
    await asyncio.sleep(10)  # 启动稍后，等建表/种子完成
    while True:
        try:
            base = (NEWS_INGEST_INTERVAL_SEC
                    if market_service.is_trading_hours()
                    else NEWS_INGEST_INTERVAL_OFF)
            from .services.news_service import run_once
            stats = await run_once()
            logger.info(
                "[资讯采集] fetched=%d inserted=%d matched_rows=%d matched_clients=%d pruned=%d/%d",
                stats["fetched"], stats["inserted"], stats["matched_rows"],
                stats["matched_clients"], stats["pruned_cn"], stats["pruned_ni"],
            )
            jitter = random.uniform(0, NEWS_INGEST_JITTER)
            await asyncio.sleep(base + jitter)
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.warning("资讯采集循环异常（继续）: %s", e)
            await asyncio.sleep(NEWS_INGEST_INTERVAL_SEC)


def _migrate_sqlite_columns():
    """SQLite 轻量迁移：为已有表补充新增列（create_all 不会改已存在的表）。
    SQLite 不支持 DROP COLUMN / ALTER COLUMN，仅支持 ADD COLUMN。
    """
    from sqlalchemy import text

    migrations = [
        # 表名, 列名, 列类型（同 models.py 定义）
        ("risk_alerts", "dimension", "VARCHAR(64)"),
        # 调仓/成本批次：transactions 表新增 5 列（v0.1.2+ 策略复盘所需）
        ("transactions", "market", "VARCHAR(8)"),
        ("transactions", "executed_at", "DATETIME"),
        ("transactions", "fee_commission", "FLOAT NOT NULL DEFAULT 0.0"),
        ("transactions", "fee_stamp_tax", "FLOAT NOT NULL DEFAULT 0.0"),
        ("transactions", "fee_transfer_fee", "FLOAT NOT NULL DEFAULT 0.0"),
        # 调仓前后成本价（v0.1.3 策略复盘括号内 Δ 值所需）
        ("transactions", "prev_cost_price", "FLOAT"),
        # 调仓审计尾链（nullable FK，允许系统/种子/公司行动无操作人场景为空）
        ("transactions", "audit_log_id", "INTEGER"),
        # 卖出批次匹配记账（FIFO 物理批次口径，用于精确撤销 + 跨批次检测）
        ("transactions", "matched_lots", "JSON"),
        # 账号级活动溯源 IP + UA：复用 audit_logs 扩列，不新建独立 account_activity_logs 表
        ("audit_logs", "ip_address", "VARCHAR(45)"),
        ("audit_logs", "user_agent", "TEXT"),
        # 账户生命周期（v0.1.5）：非 admin 默认 DEFAULT_LICENSE_DAYS，到期自动变 expired 禁止登录
        ("users", "status", "VARCHAR(16) NOT NULL DEFAULT 'active'"),
        ("users", "expires_at", "DATETIME"),
    ]
    with engine.connect() as conn:
        for table, column, col_type in migrations:
            # 检查表是否存在（SQLite：PRAGMA table_info 对不存在表返回空行集）
            cols = [row[1] for row in conn.execute(
                text(f"PRAGMA table_info({table})"))]
            if not cols:
                logger.info("跳过迁移：表 %s 不存在（将由 create_all 创建）", table)
                continue
            if column not in cols:
                conn.execute(text(
                    f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                conn.commit()
                logger.info("已迁移：%s 表新增 %s 列", table, column)

        # -----------------------------------------------------------
        # SQLite 不支持 ALTER COLUMN；clients.advisor_id 早期是 NOT NULL，
        # 现在要放开为"未分配投顾也允许开户（owner_client 自己管持仓）"，
        # 因此对老库重建 clients.advisor_id 的 NOT NULL 约束。
        # -----------------------------------------------------------
        advisor_cols = [dict(zip(("cid","name","type","notnull","dflt","pk"), row))
                        for row in conn.execute(text("PRAGMA table_info(clients)"))]
        advisor_row = next((r for r in advisor_cols if r["name"] == "advisor_id"), None)
        if advisor_row and int(advisor_row.get("notnull") or 0) == 1:
            logger.info("SQLite 重建 clients 表：放松 advisor_id NOT NULL 为可空")
            # 收集所有列名（用于 INSERT 回写时列顺序一致）
            col_names = [r["name"] for r in advisor_cols]
            col_specs = []
            fk_clauses = []
            for r in advisor_cols:
                c = f'"{r["name"]}" {r["type"]}'
                if r["name"] == "advisor_id":
                    # 新定义：去掉 NOT NULL
                    pass
                elif int(r.get("notnull") or 0) == 1:
                    dflt = r.get("dflt")
                    if dflt is not None:
                        c += f" NOT NULL DEFAULT {dflt}"
                    else:
                        c += " NOT NULL"
                else:
                    dflt = r.get("dflt")
                    if dflt is not None:
                        c += f" DEFAULT {dflt}"
                if int(r.get("pk") or 0) == 1:
                    c += " PRIMARY KEY"
                col_specs.append(c)
            # FK：advisor_id / owner_user_id 参考 PRAGMA foreign_key_list
            fks = list(conn.execute(text("PRAGMA foreign_key_list(clients)")))
            for fk in fks:
                fk_clauses.append(f'FOREIGN KEY ("{fk[3]}") REFERENCES "{fk[2]}"("{fk[4]}") ON DELETE NO ACTION')
            # 索引：重建 ix_clients_advisor_id / owner_user_id；
            # PRAGMA index_list 返回列 (seq, name, unique, origin, partial)，
            # 其中 unique 是 int（0/1），之前用 row[2].startswith("sqlite_") 会 AttributeError。
            existing_indexes = [
                row[1] for row in conn.execute(text("PRAGMA index_list(clients)"))
                if isinstance(row[1], str) and not row[1].startswith("sqlite_autoindex_")
            ]
            new_table_ddl = (
                "CREATE TABLE clients_new (\n  "
                + ",\n  ".join(col_specs + fk_clauses)
                + "\n)"
            )
            conn.execute(text(new_table_ddl))
            col_list = ", ".join(f'"{n}"' for n in col_names)
            conn.execute(text(f"INSERT INTO clients_new ({col_list}) SELECT {col_list} FROM clients"))
            conn.execute(text("DROP TABLE clients"))
            conn.execute(text("ALTER TABLE clients_new RENAME TO clients"))
            # 重建原来就存在的非 autoindex 索引（忽略重复，用 CREATE IF NOT EXISTS 包一层）
            for idx_name in existing_indexes:
                # SQLite 没有 CREATE INDEX IF NOT EXISTS LIKE 语法，
                # 我们关心的两个业务索引在下面强制 IF NOT EXISTS 重建即可，
                # 其他遗留同名索引靠"无脑 IF NOT EXISTS"兜底避免冲突。
                pass
            # 直接按已知索引名重建（CREATE INDEX IF NOT EXISTS 幂等）
            for idx_ddl in [
                "CREATE INDEX IF NOT EXISTS ix_clients_advisor_id ON clients(advisor_id)",
                "CREATE INDEX IF NOT EXISTS ix_clients_owner_user_id ON clients(owner_user_id)",
            ]:
                conn.execute(text(idx_ddl))
            conn.commit()
            logger.info("已重建 clients.advisor_id：允许为 NULL（保留原非 autoindex 索引 %s）",
                        existing_indexes)

        # -----------------------------------------------------------
        # 补充索引：ALTER TABLE ADD 列后 SQLite 不会自动建 models.index=True 的索引
        # -----------------------------------------------------------
        index_ddls = [
            "CREATE INDEX IF NOT EXISTS ix_transactions_audit_log_id ON transactions(audit_log_id)",
            "CREATE INDEX IF NOT EXISTS ix_audit_logs_ip_address ON audit_logs(ip_address)",
        ]
        for ddl in index_ddls:
            conn.execute(text(ddl))
            conn.commit()
        logger.info("审计尾链/账号活动索引已确保存在（%d 条）", len(index_ddls))


@asynccontextmanager
async def lifespan(app: FastAPI):
    from .config import RUN_SEED, DATABASE_URL
    from .migrations.changelog import diff_against_expected

    Base.metadata.create_all(bind=engine)
    _migrate_sqlite_columns()

    # 结构变更审计：打印 changelog 期望 vs 实际差异（供部署日志人工巡检）
    for line in diff_against_expected(engine):
        logger.warning("结构变更差异：%s", line)
    logger.info("RUN_SEED=%s · DB=%s", RUN_SEED, DATABASE_URL.replace(":///", ":///***"))

    db = SessionLocal()
    try:
        seed_all(db)
    finally:
        db.close()

    # 四个独立的后台刷新循环
    _bg_tasks.append(asyncio.create_task(_realtime_refresh_loop(), name="market-realtime"))
    _bg_tasks.append(asyncio.create_task(_kline_refresh_loop(), name="market-kline"))
    _bg_tasks.append(asyncio.create_task(_sector_refresh_loop(), name="market-sector"))
    _bg_tasks.append(asyncio.create_task(_stock_price_refresh_loop(), name="stock-price"))
    # 持仓相关资讯常驻采集（两表联动扇出）
    _bg_tasks.append(asyncio.create_task(_news_ingest_loop(), name="news-ingest"))

    yield

    # shutdown：优雅取消
    for task in _bg_tasks:
        if not task.done():
            task.cancel()
    if _bg_tasks:
        await asyncio.gather(*_bg_tasks, return_exceptions=True)


app = FastAPI(title="持仓复盘后台 API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(ws_router)
app.include_router(ocr_router)


@app.get("/api/health")
async def health():
    """容器健康检查（compose healthcheck / 负载均衡探针）。"""
    return {"status": "ok"}


# 同源托管前端静态资源（置于最后，确保 /api、/ws 优先匹配）
# 自定义：本地开发时禁用 JS/CSS 缓存，避免浏览器缓存导致 ES Module 导入报错
class _NoCacheStaticFiles(StaticFiles):
    def file_response(self, *args, **kwargs):
        resp: FileResponse = super().file_response(*args, **kwargs)
        path = str(args[0]) if args else ""
        if path.endswith(".js") or path.endswith(".css") or path.endswith(".html") or path.endswith(".mjs"):
            resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            resp.headers["Pragma"] = "no-cache"
            resp.headers["Expires"] = "0"
        return resp

if os.path.isdir(FRONTEND_DIR):
    app.mount("/", _NoCacheStaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
