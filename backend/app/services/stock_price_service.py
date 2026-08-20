"""个股行情服务：批量拉取个股实时行情 → 解析 → 落库 → 缓存。

数据源：东方财富 ulist.np/get 接口（支持批量最多 50 只股票）。
市场判断：6xx/9xx 开头 → 上证（1），0xx/3xx 开头 → 深证（0）。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import (
    STOCK_FIELDS, STOCK_CACHE_TTL,
    EASTMONEY_UT, MARKET_HTTP_TIMEOUT,
)
from ..database import SessionLocal
from ..models import StockPrice, StockDailyPrice, Position

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 双 host 轮询（与指数行情共用域名）
# ---------------------------------------------------------------------------
STOCK_HOSTS = [
    "https://push2.eastmoney.com/api/qt/ulist.np/get",
    "https://push2delay.eastmoney.com/api/qt/ulist.np/get",
]

# ---------------------------------------------------------------------------
# 内存缓存（进程内 TTL 缓存，避免热点数据频繁查库）
# ---------------------------------------------------------------------------
_price_cache: dict[str, tuple[dict, float]] = {}  # code → (data, timestamp)


def _invalidate_cache(code: str | None = None) -> None:
    """清除缓存；code 为 None 时清全部。"""
    if code is None:
        _price_cache.clear()
    else:
        _price_cache.pop(code, None)


def _get_cached(code: str) -> Optional[dict]:
    """从内存缓存读取；过期则返回 None。"""
    entry = _price_cache.get(code)
    if entry is None:
        return None
    data, ts = entry
    if time.time() - ts > STOCK_CACHE_TTL:
        _price_cache.pop(code, None)
        return None
    return data


def _set_cache(code: str, data: dict) -> None:
    _price_cache[code] = (data, time.time())


# ---------------------------------------------------------------------------
# 代码格式转换
# ---------------------------------------------------------------------------

def code_to_secid(code: str) -> str:
    """纯数字代码 → 东财 secid（如 600519 → 1.600519, 000001 → 0.000001）。"""
    code = code.strip()
    if not code:
        return code
    # 6/9 开头为上证主板/科创板，0/3 开头为深证主板/创业板
    if code[0] in ('6', '9'):
        return f"1.{code}"
    elif code[0] in ('0', '3'):
        return f"0.{code}"
    else:
        # 其他情况默认深证
        return f"0.{code}"


def secid_to_code(secid: str) -> str:
    """东财 secid → 纯数字代码（去掉市场前缀）。"""
    if '.' in secid:
        return secid.split('.')[1]
    return secid


# ---------------------------------------------------------------------------
# HTTP 层
# ---------------------------------------------------------------------------

def _build_stock_url(host: str, secids: list[str]) -> str:
    return (
        f"{host}?fltt=2&invt=2&ut={EASTMONEY_UT}"
        f"&fields={STOCK_FIELDS}&secids={','.join(secids)}"
    )


async def _fetch_stock_batch(secids: list[str]) -> Optional[list[dict]]:
    """批量拉取个股行情（双 host 轮询 + 重试）。返回解析后的列表，失败返回 None。"""
    last_err = None
    max_retries = 2  # 每个 host 最多重试 2 次
    
    for host in STOCK_HOSTS:
        for attempt in range(max_retries + 1):
            try:
                url = _build_stock_url(host, secids)
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(MARKET_HTTP_TIMEOUT, connect=10),
                    follow_redirects=True
                ) as client:
                    resp = await client.get(url, headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                        ),
                        "Referer": "https://quote.eastmoney.com/",
                    })
                    resp.raise_for_status()
                    raw = resp.json()

                if raw.get("rc") != 0 or not raw.get("data"):
                    raise ValueError(f"东财个股返回异常 rc={raw.get('rc')}")

                items = raw.get("data", {}).get("diff", [])
                if not items:
                    raise ValueError("返回无数据")

                logger.info("个股行情 fetch 成功 (host=%s, attempt=%d): %d 只", host, attempt + 1, len(items))
                return items
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                last_err = e
                logger.warning("个股行情 host=%s 连接失败 (attempt=%d): %s", host, attempt + 1, e)
                if attempt < max_retries:
                    await asyncio.sleep(1)  # 连接错误稍等重试
                    continue
                break  # 连接错误不再尝试同 host
            except Exception as e:
                last_err = e
                logger.warning("个股行情 host=%s 失败 (attempt=%d): %s", host, attempt + 1, e)
                if attempt < max_retries:
                    continue
                break

    logger.error("个股行情全部 host 失败: %s", last_err)
    return None


# ---------------------------------------------------------------------------
# 解析层
# ---------------------------------------------------------------------------

def _parse_stock_item(item: dict) -> Optional[dict]:
    """解析单条个股行情。返回标准化字典，无效数据返回 None。"""
    try:
        code = str(item.get("f12", "") or "").strip()
        if not code:
            return None

        price = float(item.get("f2", 0) or 0)
        if price <= 0:
            return None

        return {
            "code": code,
            "name": item.get("f14", "") or "",
            "current_price": price,
            "prev_close": float(item.get("f18", 0) or 0),
            "change_pct": float(item.get("f3", 0) or 0),
            "change_amount": float(item.get("f4", 0) or 0),
            "volume": float(item.get("f5", 0) or 0),
            "turnover": float(item.get("f6", 0) or 0),
            "high": float(item.get("f15", 0) or 0),
            "low": float(item.get("f16", 0) or 0),
            "open_price": float(item.get("f17", 0) or 0),
        }
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# 持久化层
# ---------------------------------------------------------------------------

def _upsert_stock_prices(db: Session, items: list[dict]) -> None:
    """批量 upsert 个股实时行情。"""
    now = dt.datetime.utcnow()

    # 查询已存在的记录
    codes = [item["code"] for item in items]
    existing_map: dict[str, StockPrice] = {}
    if codes:
        existing_rows = db.execute(
            select(StockPrice).where(StockPrice.code.in_(codes))
        ).scalars().all()
        for r in existing_rows:
            existing_map[r.code] = r

    for item in items:
        existing = existing_map.get(item["code"])
        if existing:
            existing.name = item["name"]
            existing.current_price = item["current_price"]
            existing.prev_close = item["prev_close"]
            existing.change_pct = item["change_pct"]
            existing.change_amount = item["change_amount"]
            existing.volume = item["volume"]
            existing.turnover = item["turnover"]
            existing.high = item["high"]
            existing.low = item["low"]
            existing.open_price = item["open_price"]
            existing.updated_at = now
        else:
            db.add(StockPrice(
                code=item["code"],
                name=item["name"],
                current_price=item["current_price"],
                prev_close=item["prev_close"],
                change_pct=item["change_pct"],
                change_amount=item["change_amount"],
                volume=item["volume"],
                turnover=item["turnover"],
                high=item["high"],
                low=item["low"],
                open_price=item["open_price"],
                updated_at=now,
            ))

    db.commit()
    # 失效缓存
    for item in items:
        _price_cache.pop(item["code"], None)


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------

def get_stock_prices(db: Session, codes: list[str]) -> dict[str, Optional[dict]]:
    """批量查询个股行情（优先缓存 → 数据库）。未找到的返回 None。"""
    result: dict[str, Optional[dict]] = {}
    cache_miss_codes: list[str] = []

    # 第一遍：命中缓存的直接返回
    for code in codes:
        cached = _get_cached(code)
        if cached:
            result[code] = cached
        else:
            cache_miss_codes.append(code)

    # 第二遍：缓存未命中的查库
    if cache_miss_codes:
        rows = db.execute(
            select(StockPrice).where(StockPrice.code.in_(cache_miss_codes))
        ).scalars().all()
        found_codes = set()
        for row in rows:
            data = {
                "code": row.code,
                "name": row.name,
                "current_price": row.current_price,
                "prev_close": row.prev_close,
                "change_pct": row.change_pct,
                "change_amount": row.change_amount,
                "volume": row.volume,
                "turnover": row.turnover,
                "high": row.high,
                "low": row.low,
                "open_price": row.open_price,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            result[row.code] = data
            _set_cache(row.code, data)
            found_codes.add(row.code)

        # 未找到的标记为 None
        for code in cache_miss_codes:
            if code not in found_codes:
                result[code] = None

    return result


def get_all_holding_codes(db: Session) -> list[str]:
    """获取所有持仓中不重复的股票代码。"""
    rows = db.execute(
        select(Position.code).distinct()
    ).scalars().all()
    return list(rows)


async def refresh_stock_prices(db: Optional[Session] = None) -> bool:
    """刷新所有活跃持仓的个股行情。

    调用方可选传入 db session；不传则自行创建并在结束时关闭。
    """
    owns_db = db is None
    if owns_db:
        db = SessionLocal()

    try:
        # 1. 获取所有持仓股票代码
        codes = get_all_holding_codes(db)
        if not codes:
            logger.info("个股行情刷新：无持仓股票，跳过")
            return True

        # 2. 转换为 secid 并分批（每批最多 50 只）
        secids = [code_to_secid(c) for c in codes]
        batch_size = 50
        all_items: list[dict] = []

        for i in range(0, len(secids), batch_size):
            batch = secids[i:i + batch_size]
            try:
                raw_items = await _fetch_stock_batch(batch)
                if raw_items:
                    for raw in raw_items:
                        parsed = _parse_stock_item(raw)
                        if parsed:
                            all_items.append(parsed)
            except Exception as e:
                logger.warning("个股行情批次 %d 刷新失败: %s", i // batch_size, e)

        if not all_items:
            logger.warning("个股行情全部刷新失败，无有效数据")
            return False

        # 3. 落库
        _upsert_stock_prices(db, all_items)
        logger.info("个股行情刷新完成: %d 只股票", len(all_items))
        return True

    except Exception as e:
        logger.error("个股行情刷新异常: %s", e)
        return False
    finally:
        if owns_db:
            db.close()


# ---------------------------------------------------------------------------
# 个股日 K 线（历史数据，用于计算昨日收盘价）
# ---------------------------------------------------------------------------

# K线数据源（多级降级）
KLINE_HOSTS = [
    "https://push2his.eastmoney.com",
    "https://push2hisdelay.eastmoney.com",
]


def _code_to_tencent(code: str) -> str:
    """纯数字代码 → 腾讯格式 (600519 → sh600519)"""
    code = code.strip()
    if code[0] in ('6', '9'):
        return f"sh{code}"
    else:
        return f"sz{code}"


async def _fetch_from_eastmoney(code: str, limit: int) -> Optional[list[dict]]:
    """从东方财富拉取K线（双host轮询+重试）。"""
    secid = code_to_secid(code)
    path = (
        f"/api/qt/stock/kline/get"
        f"?secid={secid}&ut={EASTMONEY_UT}"
        f"&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
        f"&klt=101&fqt=0&end=20500101&lmt={limit}"
    )

    max_retries = 2

    for host in KLINE_HOSTS:
        for attempt in range(max_retries + 1):
            try:
                url = f"{host}{path}"
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(MARKET_HTTP_TIMEOUT, connect=10),
                    follow_redirects=True,
                ) as client:
                    resp = await client.get(url, headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                        ),
                        "Referer": "https://quote.eastmoney.com/",
                        "Accept": "*/*",
                        "Accept-Language": "zh-CN,zh;q=0.9",
                    })
                    resp.raise_for_status()
                    raw = resp.json()

                if raw.get("rc") != 0 or not raw.get("data"):
                    raise ValueError(f"rc={raw.get('rc')}")

                klines = raw.get("data", {}).get("klines", [])
                if not klines:
                    return None

                result = []
                for line in klines:
                    parts = line.split(",")
                    if len(parts) < 7:
                        continue
                    result.append({
                        "code": code,
                        "trade_date": parts[0],
                        "open": float(parts[1]),
                        "close": float(parts[2]),
                        "high": float(parts[3]),
                        "low": float(parts[4]),
                        "volume": float(parts[5]),
                        "turnover": float(parts[6]) if len(parts) > 6 else 0.0,
                    })
                logger.debug("%s: 东财获取 %d 条", code, len(result))
                return result if result else None

            except (httpx.ConnectError, httpx.ConnectTimeout):
                if attempt < max_retries:
                    await asyncio.sleep(1)
                    continue
                break
            except Exception:
                if attempt < max_retries:
                    continue
                break

    return None


async def _fetch_from_tencent(code: str, limit: int) -> Optional[list[dict]]:
    """从腾讯财经拉取K线（备选数据源）。"""
    symbol = _code_to_tencent(code)
    url = (
        f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={symbol},day,,,{limit},qfq"
    )

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(MARKET_HTTP_TIMEOUT, connect=10),
            follow_redirects=True,
        ) as client:
            resp = await client.get(url, headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": "https://finance.qq.com/",
            })
            resp.raise_for_status()
            raw = resp.json()

        # 腾讯返回格式: data[symbol]['day'] 或 data[symbol]['qfqday']
        symbol_data = raw.get("data", {}).get(symbol, {})
        klines = symbol_data.get("day") or symbol_data.get("qfqday") or []

        if not klines:
            return None

        result = []
        for line in klines:
            # 腾讯格式: [date, open, close, high, low, volume]
            # 注意顺序不同于东财
            if len(line) < 6:
                continue
            result.append({
                "code": code,
                "trade_date": line[0],
                "open": float(line[1]),
                "close": float(line[2]),
                "high": float(line[3]),
                "low": float(line[4]),
                "volume": float(line[5]) if len(line) > 5 else 0.0,
                "turnover": float(line[6]) if len(line) > 6 else 0.0,
            })
        logger.debug("%s: 腾讯获取 %d 条", code, len(result))
        return result if result else None

    except Exception as e:
        logger.debug("%s: 腾讯获取失败: %s", code, e)
        return None


async def _fetch_from_sina(code: str, limit: int) -> Optional[list[dict]]:
    """从新浪财经拉取K线（备选数据源）。"""
    symbol = _code_to_tencent(code)  # 新浪格式与腾讯相同
    url = (
        f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
        f"?symbol={symbol}&scale=240&ma=no&datalen={limit}"
    )

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(MARKET_HTTP_TIMEOUT, connect=10),
            follow_redirects=True,
        ) as client:
            resp = await client.get(url, headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": "https://finance.sina.com.cn/",
            })
            resp.raise_for_status()
            text = resp.text

        # 新浪返回JS数组格式
        import json
        klines = json.loads(text) if text else []

        if not klines:
            return None

        result = []
        for k in klines:
            result.append({
                "code": code,
                "trade_date": k.get("day", ""),
                "open": float(k.get("open", 0)),
                "close": float(k.get("close", 0)),
                "high": float(k.get("high", 0)),
                "low": float(k.get("low", 0)),
                "volume": float(k.get("volume", 0)),
                "turnover": float(k.get("turnover", 0)) if k.get("turnover") else 0.0,
            })
        logger.debug("%s: 新浪获取 %d 条", code, len(result))
        return result if result else None

    except Exception as e:
        logger.debug("%s: 新浪获取失败: %s", code, e)
        return None


async def fetch_stock_daily_kline(code: str, limit: int = 90) -> Optional[list[dict]]:
    """拉取单只股票的日 K 线（多级降级：东财 → 腾讯 → 新浪）。"""
    # 1. 东方财富（首选）
    result = await _fetch_from_eastmoney(code, limit)
    if result:
        return result

    # 2. 腾讯财经（备选）
    result = await _fetch_from_tencent(code, limit)
    if result:
        return result

    # 3. 新浪财经（备选）
    result = await _fetch_from_sina(code, limit)
    if result:
        return result

    logger.warning("%s: 所有K线数据源均失败", code)
    return None


def upsert_stock_daily_prices(db: Session, data: list[dict]) -> None:
    """批量 upsert 个股日 K 线。"""
    if not data:
        return

    # 查询已存在的 (code, trade_date)
    keys = [(d["code"], d["trade_date"]) for d in data]
    existing_codes: set[tuple[str, str]] = set()
    for code in set(k[0] for k in keys):
        dates = [k[1] for k in keys if k[0] == code]
        existing = db.execute(
            select(StockDailyPrice).where(
                StockDailyPrice.code == code,
                StockDailyPrice.trade_date.in_(dates),
            )
        ).scalars().all()
        for r in existing:
            existing_codes.add((r.code, r.trade_date))

    for row in data:
        if (row["code"], row["trade_date"]) in existing_codes:
            existing = db.execute(
                select(StockDailyPrice).where(
                    StockDailyPrice.code == row["code"],
                    StockDailyPrice.trade_date == row["trade_date"],
                )
            ).scalar_one_or_none()
            if existing:
                existing.open = row["open"]
                existing.close = row["close"]
                existing.high = row["high"]
                existing.low = row["low"]
                existing.volume = row["volume"]
                existing.turnover = row["turnover"]
        else:
            db.add(StockDailyPrice(
                code=row["code"],
                trade_date=row["trade_date"],
                open=row["open"],
                close=row["close"],
                high=row["high"],
                low=row["low"],
                volume=row["volume"],
                turnover=row["turnover"],
            ))

    db.commit()


def get_prev_close(db: Session, code: str) -> Optional[float]:
    """获取某只股票的最近收盘价（用于计算每日盈亏）。"""
    row = db.execute(
        select(StockDailyPrice)
        .where(StockDailyPrice.code == code)
        .order_by(StockDailyPrice.trade_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    return row.close if row else None


# ---------------------------------------------------------------------------
# 批量日 K 线刷新（导入真实市场数据）
# ---------------------------------------------------------------------------

async def refresh_all_daily_klines(db: Optional[Session] = None, limit: int = 90) -> dict:
    """批量刷新所有持仓股票的日 K 线数据到 stock_daily_price 表。

    调用东方财富 push2his 接口，拉取每只股票最近 N 天的 K 线数据，
    增量更新到数据库中，确保数据的准确性、完整性和时效性。

    Args:
        db: 数据库会话（可选，不传则自行创建）
        limit: 拉取的历史天数，默认 90 天

    Returns:
        dict: 包含成功数量、失败数量、详细结果的字典
    """
    owns_db = db is None
    if owns_db:
        db = SessionLocal()

    try:
        # 1. 获取所有持仓股票代码
        codes = get_all_holding_codes(db)
        if not codes:
            logger.info("日K线刷新：无持仓股票，跳过")
            return {"success": 0, "failed": 0, "results": []}

        logger.info("开始刷新 %d 只股票的日K线数据（最近 %d 天）", len(codes), limit)

        results = []
        success_count = 0
        failed_count = 0

        # 2. 逐只拉取K线数据（含并发控制）
        sem = asyncio.Semaphore(5)  # 最多5个并发请求

        async def _fetch_and_upsert(code: str) -> dict:
            async with sem:
                try:
                    klines = await fetch_stock_daily_kline(code, limit=limit)
                    if klines:
                        upsert_stock_daily_prices(db, klines)
                        logger.info("✓ %s: 获取 %d 条K线数据", code, len(klines))
                        return {"code": code, "status": "success", "count": len(klines)}
                    else:
                        logger.warning("✗ %s: 无K线数据返回", code)
                        return {"code": code, "status": "empty", "count": 0}
                except Exception as e:
                    logger.warning("✗ %s: 拉取失败 - %s", code, e)
                    return {"code": code, "status": "failed", "error": str(e)}

        # 并发执行所有拉取任务
        tasks = [_fetch_and_upsert(code) for code in codes]
        task_results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in task_results:
            if isinstance(result, Exception):
                failed_count += 1
                results.append({"status": "exception", "error": str(result)})
            elif result.get("status") == "success":
                success_count += 1
                results.append(result)
            else:
                failed_count += 1
                results.append(result)

        logger.info("日K线刷新完成：成功 %d 只，失败 %d 只", success_count, failed_count)

        return {
            "success": success_count,
            "failed": failed_count,
            "total": len(codes),
            "results": results,
        }

    except Exception as e:
        logger.error("日K线刷新异常: %s", e)
        return {"success": 0, "failed": len(codes), "error": str(e), "results": []}
    finally:
        if owns_db:
            db.close()


async def import_stock_daily_data(code: str, trade_date: str, close: float,
                                  open_price: float = 0, high: float = 0,
                                  low: float = 0, volume: float = 0,
                                  turnover: float = 0) -> bool:
    """手动导入单条日线数据到 stock_daily_price 表。

    Args:
        code: 股票代码（如 600519）
        trade_date: 交易日期（如 2026-08-15）
        close: 收盘价
        open_price: 开盘价
        high: 最高价
        low: 最低价
        volume: 成交量
        turnover: 成交额

    Returns:
        bool: 是否成功
    """
    try:
        db = SessionLocal()
        data = [{
            "code": code,
            "trade_date": trade_date,
            "open": open_price,
            "close": close,
            "high": high or close,
            "low": low or close,
            "volume": volume,
            "turnover": turnover,
        }]
        upsert_stock_daily_prices(db, data)
        logger.info("已导入 %s @ %s: 收盘价 %.2f", code, trade_date, close)
        return True
    except Exception as e:
        logger.error("导入日线数据失败 %s: %s", code, e)
        return False
    finally:
        if 'db' in locals():
            db.close()
