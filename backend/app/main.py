"""FastAPI 应用入口：组装路由、启动建表与种子数据、同源托管前端。"""
import asyncio
import logging
import os

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .api.ws import ws_router
from .config import (
    FRONTEND_DIR, MARKET_REFRESH_INTERVAL_REALTIME, MARKET_REFRESH_INTERVAL_REALTIME_OFF,
    MARKET_REFRESH_INTERVAL_KLINE, MARKET_REFRESH_INTERVAL_SECTOR,
    MARKET_REFRESH_INTERVAL_SECTOR_OFF,
    STOCK_REFRESH_INTERVAL, STOCK_REFRESH_INTERVAL_OFF,
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
    """个股行情：交易期每 20s 刷一次，非交易期每 120s。刷新后写入当日盈亏快照。"""
    await asyncio.sleep(6)
    try:
        from .services.stock_price_service import refresh_stock_prices
        await refresh_stock_prices()
    except Exception as e:
        logger.warning("启动时个股行情初始刷新失败: %s", e)

    while True:
        try:
            interval = (STOCK_REFRESH_INTERVAL
                        if market_service.is_trading_hours()
                        else STOCK_REFRESH_INTERVAL_OFF)
            await asyncio.sleep(interval)

            # 刷新个股行情
            from .services.stock_price_service import refresh_stock_prices
            from .services.pnl_service import write_all_daily_snapshots
            db = SessionLocal()
            try:
                try:
                    await refresh_stock_prices(db)
                except Exception as e:
                    # 行情刷新失败不阻断快照写入（compute_portfolio 有降级价格链）
                    logger.warning("个股行情刷新失败（快照仍按降级价格写入）: %s", e)
                # 写入当日盈亏快照（无论行情刷新是否成功，均有价格降级链兜底）
                write_all_daily_snapshots(db)
            finally:
                db.close()

        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.warning("个股行情刷新异常（继续）: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
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

# 同源托管前端静态资源（置于最后，确保 /api、/ws 优先匹配）
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
