"""行情数据源：后端代理，前端只读自己数据库中的快照。

后台定时调多源行情（东财实时 / 新浪+腾讯 K 线） → 解析 → 落库。
API 路由直接查库返回，不再从前端发起跨域请求。
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Optional, Callable

import httpx
from sqlalchemy import select, delete
from sqlalchemy.orm import Session

from ..config import (
    MARKET_SECIDS, SH_INDEX_SECID, SZ_INDEX_SECID, CY_INDEX_SECID, KC_INDEX_SECID,
    INDEX_SECIDS, INDEX_LABELS, EASTMONEY_UT,
    MARKET_REALTIME_FIELDS, MARKET_KLINE_DAYS, MARKET_HTTP_TIMEOUT,
    SECTOR_FS, SECTOR_FIELDS, SECTOR_PAGE_SIZE,
)
from ..database import SessionLocal
from ..models import MarketSnapshot, MarketKline, MarketSector

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 实时行情（东财，双 host）
# ---------------------------------------------------------------------------
REALTIME_HOSTS = [
    "https://push2.eastmoney.com/api/qt/ulist.np/get",
    "https://push2delay.eastmoney.com/api/qt/ulist.np/get",
]


def _build_realtime_url(host: str) -> str:
    return (
        f"{host}?fltt=2&invt=2&ut={EASTMONEY_UT}"
        f"&fields={MARKET_REALTIME_FIELDS}&secids={MARKET_SECIDS}"
    )


# ---------------------------------------------------------------------------
# K 线（多源轮询：东财 → 新浪 → 腾讯，解析后字段完全对齐）
# 增量策略：
#   - 首次空库：拉 40 天全量（init）
#   - 后续：只拉最近 5 天（覆盖周末/节假日），对比 DB 做 insert-or-update
#   - 支持多指数：上证、深证、创业板、科创50
# ---------------------------------------------------------------------------

# 增量刷新时拉取最近几天（覆盖周末/节假日后仍有遗漏的可能）
KLINE_INCREMENTAL_DAYS = 5


def _build_kline_eastmoney(secid: str, limit: int = MARKET_KLINE_DAYS) -> tuple[str, Callable[[dict], list[dict]]]:
    """生成东财 K 线 URL 及解析函数（支持任意指数 secid）。"""
    url = (
        f"https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={secid}&ut=fa5fd1943c7b386f172d6893dbfba10b"
        f"&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        f"&klt=101&fqt=0&end=20500101&lmt={limit}"
    )

    def parse(data: dict) -> list[dict]:
        klines = data.get("data", {}).get("klines", [])
        pre_kprice = float(data.get("data", {}).get("preKPrice", 0) or 0)
        result = []
        prev_close = pre_kprice if pre_kprice > 0 else None
        for line in klines:
            parts = line.split(",")
            if len(parts) < 7:
                continue
            open_ = float(parts[1])
            close = float(parts[2])
            high = float(parts[3])
            low = float(parts[4])
            volume = float(parts[5])
            turnover = float(parts[6]) if len(parts) > 6 else 0.0
            change_pct = float(parts[8]) if len(parts) > 8 and parts[8] else None
            if change_pct is None and prev_close and prev_close > 0:
                change_pct = round((close - prev_close) / prev_close * 100, 2)
            elif change_pct is None:
                change_pct = 0.0
            result.append({
                "fullDate": parts[0], "date": parts[0][5:],
                "open": open_, "close": close,
                "high": high, "low": low,
                "volume": volume, "turnover": turnover,
                "changePct": change_pct,
                "raw": parts, "source": "eastmoney",
                "indexCode": secid,
            })
            prev_close = close
        return result

    return url, parse


def _build_kline_sina(secid: str, limit: int = MARKET_KLINE_DAYS) -> tuple[str, Callable[[dict], list[dict]]]:
    """生成新浪 K 线 URL 及解析函数（仅支持 sh000001 / sz399001 等格式）。"""
    symbol_map = {
        "1.000001": "sh000001",
        "0.399001": "sz399001",
        "0.399006": "sz399006",
        "1.000688": "sh000688",
    }
    symbol = symbol_map.get(secid, "sh000001")
    url = (
        f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={limit}"
    )

    def parse(data: list) -> list[dict]:
        if not isinstance(data, list):
            raise ValueError("新浪返回非数组")
        result = []
        prev_close = None
        for row in data:
            full_date = row.get("day", "")
            if not full_date:
                continue
            open_ = float(row.get("open", 0))
            close = float(row.get("close", 0))
            high = float(row.get("high", 0))
            low = float(row.get("low", 0))
            vol = float(row.get("volume", 0)) / 100  # 股→手
            if prev_close and prev_close > 0:
                change_pct = round((close - prev_close) / prev_close * 100, 2)
            else:
                change_pct = 0.0
            result.append({
                "fullDate": full_date, "date": full_date[5:],
                "open": open_, "close": close,
                "high": high, "low": low,
                "volume": vol,
                "turnover": vol * (high + low) / 2,
                "changePct": change_pct,
                "raw": row, "source": "computed",
                "indexCode": secid,
            })
            prev_close = close
        return result

    return url, parse


def _build_kline_tencent(secid: str, limit: int = MARKET_KLINE_DAYS) -> tuple[str, Callable[[dict], list[dict]]]:
    """生成腾讯 K 线 URL 及解析函数。"""
    symbol_map = {
        "1.000001": "sh000001",
        "0.399001": "sz399001",
        "0.399006": "sz399006",
        "1.000688": "sh000688",
    }
    symbol = symbol_map.get(secid, "sh000001")
    url = (
        f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={symbol},day,,,{limit},qfq"
    )

    def parse(data: dict) -> list[dict]:
        klines = data.get("data", {}).get(symbol, {}).get("day", [])
        if not klines:
            raise ValueError("腾讯返回无 day 数据")
        result = []
        prev_close = None
        for arr in klines:
            if len(arr) < 6:
                continue
            full_date = arr[0]
            open_ = float(arr[1])
            close = float(arr[2])
            high = float(arr[3])
            low = float(arr[4])
            vol = float(arr[5])
            if prev_close and prev_close > 0:
                change_pct = round((close - prev_close) / prev_close * 100, 2)
            else:
                change_pct = 0.0
            result.append({
                "fullDate": full_date, "date": full_date[5:],
                "open": open_, "close": close,
                "high": high, "low": low,
                "volume": vol,
                "turnover": vol * (high + low) / 2,
                "changePct": change_pct,
                "raw": arr, "source": "computed",
                "indexCode": secid,
            })
            prev_close = close
        return result

    return url, parse


KLINE_SOURCE_BUILDERS = [
    _build_kline_eastmoney,
    _build_kline_sina,
    _build_kline_tencent,
]


# ---------------------------------------------------------------------------
# HTTP 层
# ---------------------------------------------------------------------------
async def _fetch_json(url: str, timeout: int = MARKET_HTTP_TIMEOUT) -> dict:
    """后端 HTTP fetch（无浏览器介入，无 CORS/Referer 问题）。"""
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(url, headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        })
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# 解析层
# ---------------------------------------------------------------------------
def _parse_realtime(data: dict, prev_volume_fallback: float = 0.0) -> dict:
    items = data.get("data", {}).get("diff", [])
    sh = next((i for i in items if i.get("f13") == 1 and i.get("f12") == "000001"), None)
    sz = next((i for i in items if i.get("f13") == 0 and i.get("f12") == "399001"), None)
    cy = next((i for i in items if i.get("f13") == 0 and i.get("f12") == "399006"), None)
    kc = next((i for i in items if i.get("f13") == 1 and i.get("f12") == "000688"), None)

    if not all([sh, sz, cy]):
        raise ValueError(f"实时返回缺少必要指数: sh={bool(sh)} sz={bool(sz)} cy={bool(cy)} kc={bool(kc)}")

    adv_count = (sh["f104"] or 0) + (sz["f104"] or 0)
    dec_count = (sh["f105"] or 0) + (sz["f105"] or 0)
    flat_count = (sh["f106"] or 0) + (sz["f106"] or 0)
    total_stocks = adv_count + dec_count + flat_count

    total_turnover_yi = ((sh["f6"] or 0) + (sz["f6"] or 0)) / 1e8
    prev_volume = prev_volume_fallback if prev_volume_fallback > 0 else total_turnover_yi * 0.95

    def _build_index(item, name):
        return {
            "name": name,
            "value": item["f2"],
            "prevClose": (item["f2"] or 0) - (item["f4"] or 0),
            "change": item["f4"],
            "changePct": item["f3"],
        }

    return {
        "indices": [
            _build_index(sh, "上证指数"),
            _build_index(sz, "深证成指"),
            _build_index(cy, "创业板指"),
            _build_index(kc, "科创50") if kc else _build_index(sh, "科创50"),
        ],
        "totalVolume": total_turnover_yi,
        "prevVolume": prev_volume,
        "advCount": adv_count, "decCount": dec_count, "flatCount": flat_count,
        "totalStocks": total_stocks,
    }


# ---------------------------------------------------------------------------
# 持久化层
# ---------------------------------------------------------------------------
def _upsert_snapshot(db: Session, payload: dict, status: str = "ok",
                     error: str | None = None) -> None:
    snap = db.get(MarketSnapshot, 1)
    if snap is None:
        snap = MarketSnapshot(id=1, data=payload, fetch_status=status, fetch_error=error)
        db.add(snap)
    else:
        snap.data = payload
        snap.fetch_status = status
        snap.fetch_error = error
        snap.updated_at = dt.datetime.utcnow()
    db.commit()


def _upsert_klines(db: Session, parsed: list[dict], is_incremental: bool = False) -> None:
    """K 线 insert-or-update（支持多指数，index_code + trade_date 联合唯一）。"""
    # 批量查已有行
    keys = [(r["indexCode"], r["fullDate"]) for r in parsed]
    existing_map: dict[tuple[str, str], MarketKline] = {}
    if keys:
        # 逐指数查
        for secid in set(k[0] for k in keys):
            dates = [k[1] for k in keys if k[0] == secid]
            existing_rows = db.execute(
                select(MarketKline).where(
                    MarketKline.index_code == secid,
                    MarketKline.trade_date.in_(dates),
                )
            ).scalars().all()
            for r in existing_rows:
                existing_map[(r.index_code, r.trade_date)] = r

    upgraded_count = 0
    inserted_count = 0
    updated_count = 0

    for row in parsed:
        key = (row["indexCode"], row["fullDate"])
        existing = existing_map.get(key)
        if existing:
            if existing.data_source != "eastmoney" and row["source"] == "eastmoney":
                upgraded_count += 1
            existing.open = row["open"]
            existing.close = row["close"]
            existing.high = row["high"]
            existing.low = row["low"]
            existing.volume = row["volume"]
            existing.turnover = row["turnover"]
            existing.change_pct = row["changePct"]
            existing.data_source = row["source"]
            existing.raw = row.get("raw")
            updated_count += 1
        else:
            db.add(MarketKline(
                index_code=row["indexCode"],
                trade_date=row["fullDate"], open=row["open"], close=row["close"],
                high=row["high"], low=row["low"], volume=row["volume"],
                turnover=row["turnover"], change_pct=row["changePct"],
                data_source=row["source"], raw=row.get("raw"),
            ))
            inserted_count += 1

    # 全量模式：删除过期数据（按指数分别处理）
    if not is_incremental and parsed:
        for secid in set(r["indexCode"] for r in parsed):
            secid_rows = [r for r in parsed if r["indexCode"] == secid]
            if len(secid_rows) >= MARKET_KLINE_DAYS:
                srted = sorted(secid_rows, key=lambda x: x["fullDate"])
                oldest = srted[-MARKET_KLINE_DAYS]["fullDate"]
                db.execute(delete(MarketKline).where(
                    MarketKline.index_code == secid,
                    MarketKline.trade_date < oldest,
                ))

    db.commit()
    logger.info(
        "K线落库: inserted=%d updated=%d upgraded=%d (source=%s, mode=%s)",
        inserted_count, updated_count, upgraded_count,
        parsed[0]["source"] if parsed else "?",
        "incremental" if is_incremental else "full",
    )


# ---------------------------------------------------------------------------
# 公开的刷新入口（后台定时任务调用；内部自己开 session，不占调用方连接）
# ---------------------------------------------------------------------------

async def refresh_realtime() -> bool:
    """拉取实时行情 → 解析 → 覆盖落库。DB session 在 fetch 之后才打开。"""
    last_err: Optional[Exception] = None
    payload: Optional[dict] = None

    for host in REALTIME_HOSTS:
        try:
            raw = await _fetch_json(_build_realtime_url(host))
            if raw.get("rc") != 0 or not raw.get("data"):
                raise ValueError(f"东财实时返回异常 rc={raw.get('rc')}")
            logger.info("实时行情 fetch 成功 (host=%s)", host)
            payload = _parse_realtime(raw, prev_volume_fallback=0.0)
            break
        except Exception as e:
            last_err = e
            logger.warning("实时行情 host=%s 失败: %s", host, e)

    db = SessionLocal()
    try:
        if payload is not None:
            last = db.get(MarketSnapshot, 1)
            if last and last.data and last.data.get("prevVolume"):
                payload["prevVolume"] = last.data["prevVolume"]
            _upsert_snapshot(db, payload, status="ok", error=None)
            return True
        else:
            existing = db.get(MarketSnapshot, 1)
            _upsert_snapshot(db, existing.data if existing else {}, status="fail",
                             error=str(last_err) if last_err else "全部 host 失败")
            logger.error("实时行情全部 host 失败: %s", last_err)
            return False
    finally:
        db.close()


async def refresh_kline() -> bool:
    """K 线增量刷新（支持多指数并行）。

    对所有指数类型（上证、深证、创业板、科创50）分别执行：
    - 空库或存在 computed 源 → 全量初始化 40 天
    - 正常 → 增量刷新最近 5 天
    """
    db_for_check = SessionLocal()
    try:
        total_rows = db_for_check.execute(
            select(MarketKline.id)
        ).scalars().all()
        needs_init = len(total_rows) == 0

        computed_rows = db_for_check.execute(
            select(MarketKline).where(MarketKline.data_source != "eastmoney")
        ).scalars().all()
        needs_upgrade = len(computed_rows) > 0
    finally:
        db_for_check.close()

    if needs_init:
        logger.info("K 线: 空库 → 全量初始化 (40 天)")
        return await _refresh_kline_full()
    elif needs_upgrade:
        logger.info("K 线: 存在 computed 数据 → 触发精确源升级")
        return await _refresh_kline_full()
    else:
        logger.info("K 线: 增量刷新 (最近 %d 天)", KLINE_INCREMENTAL_DAYS)
        return await _refresh_kline_incremental()


async def _fetch_kline_for_index(secid: str, limit: int) -> Optional[list[dict]]:
    """对单个指数尝试多源拉取 K 线。"""
    last_err = None
    for builder in KLINE_SOURCE_BUILDERS:
        url, parse_fn = builder(secid, limit)
        source_name = builder.__name__.replace("_build_kline_", "")
        try:
            raw = await _fetch_json(url)
            parsed = parse_fn(raw)
            if not parsed:
                raise ValueError("解析后无数据")
            logger.info("K 线 fetch [%s] %s: %d rows", INDEX_LABELS.get(secid, secid), source_name, len(parsed))
            return parsed
        except Exception as e:
            last_err = e
            logger.warning("K 线 [%s] %s 失败: %s", INDEX_LABELS.get(secid, secid), source_name, e)
    logger.error("K 线 [%s] 全部源失败: %s", INDEX_LABELS.get(secid, secid), last_err)
    return None


async def _refresh_kline_full() -> bool:
    """拉全量 K 线（40天），对所有指数并行拉取。"""
    import asyncio

    tasks = [_fetch_kline_for_index(secid, MARKET_KLINE_DAYS) for secid in INDEX_SECIDS]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_parsed = []
    ok = True
    for secid, result in zip(INDEX_SECIDS, results):
        if isinstance(result, Exception) or result is None:
            logger.error("K 线全量 [%s] 失败: %s", INDEX_LABELS.get(secid, secid), result)
            ok = False
            continue
        all_parsed.extend(result)

    if not all_parsed:
        return False

    db = SessionLocal()
    try:
        _upsert_klines(db, all_parsed, is_incremental=False)
        return True
    finally:
        db.close()


async def _refresh_kline_incremental() -> bool:
    """拉最近 5 天 K 线，对所有指数并行拉取。"""
    import asyncio

    tasks = [_fetch_kline_for_index(secid, KLINE_INCREMENTAL_DAYS) for secid in INDEX_SECIDS]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_parsed = []
    ok = True
    for secid, result in zip(INDEX_SECIDS, results):
        if isinstance(result, Exception) or result is None:
            logger.warning("K 线增量 [%s] 跳过: %s", INDEX_LABELS.get(secid, secid), result)
            ok = False
            continue
        all_parsed.extend(result)

    if not all_parsed:
        return False

    db = SessionLocal()
    try:
        _upsert_klines(db, all_parsed, is_incremental=True)
        _check_kline_integrity(db)
        return True
    finally:
        db.close()


def _check_kline_integrity(db: Session) -> None:
    """增量刷新后，检查 40 天窗口内是否有日期缺失。"""
    from datetime import datetime, timedelta

    existing_dates = set(
        db.execute(select(MarketKline.trade_date)).scalars().all()
    )
    if not existing_dates:
        return

    latest = max(existing_dates)
    now = datetime.strptime(latest, "%Y-%m-%d")
    expected = []
    for i in range(MARKET_KLINE_DAYS):
        d = now - timedelta(days=i)
        if d.weekday() < 5:
            expected.append(d.strftime("%Y-%m-%d"))
    expected_set = set(expected)

    missing = expected_set - existing_dates
    if missing:
        logger.warning("K 线完整性: %d 个交易日缺失: %s", len(missing), sorted(missing)[:5])


# ---------------------------------------------------------------------------
# 行业板块（东财 m:90+t:2，双 host + 分页拉取）
# ---------------------------------------------------------------------------
SECTOR_HOSTS = [
    "https://push2.eastmoney.com/api/qt/clist/get",
    "https://push2delay.eastmoney.com/api/qt/clist/get",
]


def _build_sector_url(host: str, page: int = 1) -> str:
    return (
        f"{host}?pn={page}&pz={SECTOR_PAGE_SIZE}&po=1&np=1"
        f"&ut=bd1d9ddb04089700cf9c27f6f7426281&fltt=2&invt=2&fid=f3"
        f"&fs={SECTOR_FS}&fields={SECTOR_FIELDS}"
    )


def _parse_sector_page(raw: dict) -> list[dict]:
    diff = raw.get("data", {}).get("diff", [])
    result = []
    for item in diff:
        result.append({
            "sector_code": item.get("f12", ""),
            "sector_name": item.get("f14", ""),
            "change_pct": float(item.get("f3", 0) or 0),
            "turnover": float(item.get("f20", 0) or 0),
            "up_count": int(item.get("f104", 0) or 0),
            "down_count": int(item.get("f105", 0) or 0),
            "raw": item,
        })
    return result


async def _fetch_all_sectors() -> list[dict]:
    """分页拉取全部行业板块（最多 496 个，分 3 页）。双 host 轮询。"""
    for host in SECTOR_HOSTS:
        try:
            all_sectors: list[dict] = []
            page = 1
            while True:
                raw = await _fetch_json(_build_sector_url(host, page))
                if raw.get("rc") != 0 or not raw.get("data"):
                    raise ValueError(f"板块接口异常 rc={raw.get('rc')}")
                parsed = _parse_sector_page(raw)
                all_sectors.extend(parsed)
                total = raw.get("data", {}).get("total", 0)
                if len(all_sectors) >= total or not parsed:
                    break
                page += 1
                if page > 10:
                    break
            logger.info("行业板块拉取完成 (host=%s): %d 个行业", host, len(all_sectors))
            return all_sectors
        except Exception as e:
            logger.warning("板块 host=%s 失败: %s", host, e)
    raise RuntimeError("全部板块 host 失败")


def _upsert_sectors(db: Session, sectors: list[dict]) -> None:
    existing_codes: set[str] = set()
    if sectors:
        codes = [s["sector_code"] for s in sectors]
        existing_rows = db.execute(
            select(MarketSector).where(MarketSector.sector_code.in_(codes))
        ).scalars().all()
        existing_codes = {r.sector_code for r in existing_rows}

    now = dt.datetime.utcnow()
    for row in sectors:
        if row["sector_code"] in existing_codes:
            existing = db.execute(
                select(MarketSector).where(MarketSector.sector_code == row["sector_code"])
            ).scalar_one_or_none()
            if existing:
                existing.sector_name = row["sector_name"]
                existing.change_pct = row["change_pct"]
                existing.turnover = row["turnover"]
                existing.up_count = row["up_count"]
                existing.down_count = row["down_count"]
                existing.raw = row.get("raw")
                existing.updated_at = now
        else:
            db.add(MarketSector(
                sector_code=row["sector_code"], sector_name=row["sector_name"],
                change_pct=row["change_pct"], turnover=row["turnover"],
                up_count=row["up_count"], down_count=row["down_count"],
                raw=row.get("raw"), updated_at=now,
            ))
            existing_codes.add(row["sector_code"])
    db.commit()


async def refresh_sectors() -> bool:
    """拉取行业板块 → 解析 → upsert 落库。"""
    try:
        sectors = await _fetch_all_sectors()
    except Exception as e:
        logger.error("行业板块拉取失败: %s", e)
        return False

    if not sectors:
        logger.warning("行业板块返回空数据")
        return False

    db = SessionLocal()
    try:
        _upsert_sectors(db, sectors)
        return True
    finally:
        db.close()


async def refresh_all() -> None:
    import asyncio
    await asyncio.gather(
        refresh_realtime(),
        refresh_kline(),
        return_exceptions=True,
    )


# ---------------------------------------------------------------------------
# 公开的读接口（API 路由调用）
# ---------------------------------------------------------------------------

def get_snapshot(db: Session) -> Optional[MarketSnapshot]:
    return db.get(MarketSnapshot, 1)


def get_kline(db: Session, index_code: str = "1.000001") -> list[MarketKline]:
    """返回指定指数的日 K 线（最多 MARKET_KLINE_DAYS 条，按日期升序）。"""
    return db.execute(
        select(MarketKline)
        .where(MarketKline.index_code == index_code)
        .order_by(MarketKline.trade_date.desc())
        .limit(MARKET_KLINE_DAYS)
    ).scalars().all()[::-1]


def get_sectors(db: Session, limit: int = 80) -> list[MarketSector]:
    return db.execute(
        select(MarketSector).order_by(MarketSector.turnover.desc()).limit(limit)
    ).scalars().all()


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def is_trading_hours() -> bool:
    now = dt.datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    morning = dt.time(9, 15) <= t <= dt.time(11, 30)
    afternoon = dt.time(13, 0) <= t <= dt.time(15, 5)
    return morning or afternoon