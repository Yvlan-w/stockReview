"""REST API 路由：认证 / 用户 / 客户 / 关系 / 风险预警 / 站内信。"""
import csv
import datetime as dt
import io
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from ..core.deps import get_current_user, require_roles
from ..core.security import create_access_token
from ..database import get_db
from ..models import (
    User, Client, Position, RiskAlert, Notification, Transaction,
    ROLE_ADMIN, ROLE_SERVICE, ROLE_ADVISOR, ROLE_USER,
    ALERT_OPEN, ALERT_ACK, ALERT_RESOLVED,
)
from ..schemas import (
    LoginRequest, TokenOut, UserOut, UserCreate, UserCreateOut, UserOptionsOut,
    ClientCreate, ClientUpdate, RelationsUpdate, PositionsUpdate, ClientOut, ClientCreateOut,
    RiskAlertOut, AlertStatusUpdate, NotificationOut, UnreadCountOut,
    RelationImportRow, RelationExportRow, RelationImportResult,
    TransactionCreate, TransactionOut,
)
from ..services import auth_service, client_service, risk_engine, notification_service
from ..services import market_service
from ..services import market_analysis_service

router = APIRouter(prefix="/api")


# ==================== 认证 ====================
@router.post("/auth/login", response_model=TokenOut)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = auth_service.authenticate(db, body.username, body.password)
    token = create_access_token(user.id, user.role, user.sub_role)
    return {"access_token": token, "token_type": "bearer", "user": UserOut.model_validate(user)}


@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


# ==================== 用户管理（管理员） ====================
@router.get("/users", response_model=List[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_roles(ROLE_ADMIN))):
    return db.query(User).order_by(User.id).all()


@router.get("/users/options", response_model=UserOptionsOut)
def user_options(db: Session = Depends(get_db), _: User = Depends(require_roles(ROLE_ADMIN, ROLE_SERVICE))):
    """开户/关系映射表单的顾问、客服下拉选项。"""
    advisors = db.query(User).filter(User.role == ROLE_ADVISOR, User.is_active.is_(True)).order_by(User.id).all()
    services = db.query(User).filter(User.role == ROLE_SERVICE, User.is_active.is_(True)).order_by(User.id).all()
    return {"advisors": advisors, "services": services}


@router.post("/users", response_model=UserCreateOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, db: Session = Depends(get_db), _: User = Depends(require_roles(ROLE_ADMIN))):
    user, initial_password = auth_service.create_user(db, body)
    out = UserCreateOut.model_validate(user)
    out.initial_password = initial_password
    return out


# ==================== 客户 ====================
@router.get("/clients", response_model=List[ClientOut])
def list_clients(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    clients = client_service.list_visible_clients(db, user)
    return [client_service.serialize_client(c) for c in clients]


@router.post("/clients", response_model=ClientCreateOut, status_code=status.HTTP_201_CREATED)
def create_client(body: ClientCreate, db: Session = Depends(get_db), user: User = Depends(require_roles(ROLE_ADMIN, ROLE_SERVICE))):
    client, login = client_service.create_client(db, body, creator=user)
    result = client_service.serialize_client(client)
    result["login"] = login
    return result


@router.get("/clients/{client_id}", response_model=ClientOut)
def get_client(client_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    client = client_service.get_visible_client(db, user, client_id)
    return client_service.serialize_client(client)


@router.put("/clients/{client_id}", response_model=ClientOut)
def update_client(client_id: str, body: ClientUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    client = client_service.get_visible_client(db, user, client_id)
    client = client_service.update_client_fields(db, client, body)
    return client_service.serialize_client(client)


@router.put("/clients/{client_id}/positions", response_model=ClientOut)
def update_client_positions(client_id: str, body: PositionsUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    client = client_service.get_visible_client(db, user, client_id)
    client = client_service.update_client_positions(db, client, body.positions)
    return client_service.serialize_client(client)


@router.delete("/clients/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_client(client_id: str, db: Session = Depends(get_db), _: User = Depends(require_roles(ROLE_ADMIN))):
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "客户不存在")
    db.delete(client)
    db.commit()
    return None


# ==================== 关系映射（管理员） ====================
@router.put("/clients/{client_id}/relations", response_model=ClientOut)
def update_relations(client_id: str, body: RelationsUpdate, db: Session = Depends(get_db), _: User = Depends(require_roles(ROLE_ADMIN))):
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "客户不存在")
    client = client_service.update_client_relations(db, client, body)
    return client_service.serialize_client(client)


@router.get("/relations/export", response_model=List[RelationExportRow])
def export_relations(db: Session = Depends(get_db), _: User = Depends(require_roles(ROLE_ADMIN))):
    """导出关系映射（JSON 数组）。"""
    return client_service.export_relations(db)


@router.get("/relations/export/csv")
def export_relations_csv(db: Session = Depends(get_db), _: User = Depends(require_roles(ROLE_ADMIN))):
    """导出关系映射（CSV，service_ids 以 | 分隔）。"""
    rows = client_service.export_relations(db)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["client_id", "name", "advisor_id", "advisor_name", "service_ids"])
    for r in rows:
        writer.writerow([r["client_id"], r["name"], r["advisor_id"], r["advisor_name"], "|".join(r["service_ids"])])
    return Response(content=buf.getvalue(), media_type="text/csv; charset=utf-8")


@router.post("/relations/import", response_model=RelationImportResult)
def import_relations(body: List[RelationImportRow], db: Session = Depends(get_db), _: User = Depends(require_roles(ROLE_ADMIN))):
    """批量导入关系映射（JSON 数组）。"""
    return client_service.import_relations(db, body)


@router.post("/relations/import/csv", response_model=RelationImportResult)
async def import_relations_csv(request: Request, db: Session = Depends(get_db), _: User = Depends(require_roles(ROLE_ADMIN))):
    """批量导入关系映射（CSV，列：client_id,name,advisor_id,service_ids）。"""
    raw = (await request.body()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(raw))
    rows: List[RelationImportRow] = []
    errors = []
    for i, line in enumerate(reader):
        line_no = i + 2  # 表头占第 1 行
        try:
            client_id = (line.get("client_id") or "").strip() or None
            name = (line.get("name") or "").strip()
            advisor_id = (line.get("advisor_id") or "").strip()
            raw_services = (line.get("service_ids") or "").replace("；", "|").replace(";", "|")
            service_ids = [s.strip() for s in raw_services.split("|") if s.strip()]
            rows.append(RelationImportRow(
                client_id=client_id, name=name, advisor_id=advisor_id, service_ids=service_ids,
            ))
        except Exception:
            errors.append({"row": line_no, "detail": "字段格式错误"})
    result = client_service.import_relations(db, rows)
    result["errors"] = errors + result["errors"]
    return result


# ==================== 风险预警 ====================
@router.post("/risk/evaluate/{client_id}", response_model=List[RiskAlertOut])
async def evaluate_risk(client_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    client = client_service.get_visible_client(db, user, client_id)
    alerts, recipients = risk_engine.run_risk_evaluation(db, client)
    if recipients:
        await notification_service.push_notification(recipients, {
            "type": "risk_alert",
            "client_id": client.id,
            "client_name": client.name,
            "count": len(alerts),
        })
    return alerts


@router.get("/clients/{client_id}/alerts", response_model=List[RiskAlertOut])
def list_alerts(client_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    client_service.get_visible_client(db, user, client_id)  # 校验可见性
    return db.query(RiskAlert).filter(RiskAlert.client_id == client_id).order_by(RiskAlert.id.desc()).all()


@router.patch("/alerts/{alert_id}", response_model=RiskAlertOut)
def update_alert_status(alert_id: int, body: AlertStatusUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    alert = db.get(RiskAlert, alert_id)
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "预警不存在")
    if body.status not in (ALERT_OPEN, ALERT_ACK, ALERT_RESOLVED):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "非法预警状态")
    # 仅客服/顾问/管理员可处理；且需在其可见客户范围内
    if user.role not in (ROLE_ADMIN, ROLE_SERVICE, ROLE_ADVISOR):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权限处理预警")
    client_service.get_visible_client(db, user, alert.client_id)
    alert.status = body.status
    db.commit()
    db.refresh(alert)
    return alert


# ==================== 站内信 ====================
@router.get("/notifications", response_model=List[NotificationOut])
def list_notifications(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return (
        db.query(Notification)
        .filter(Notification.recipient_id == user.id)
        .order_by(Notification.id.desc())
        .all()
    )


@router.get("/notifications/unread-count", response_model=UnreadCountOut)
def unread_count(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    count = (
        db.query(Notification)
        .filter(Notification.recipient_id == user.id, Notification.is_read.is_(False))
        .count()
    )
    return {"unread": count}


@router.post("/notifications/{notification_id}/read", response_model=NotificationOut)
def mark_read(notification_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    n = db.get(Notification, notification_id)
    if n is None or n.recipient_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "消息不存在")
    n.is_read = True
    db.commit()
    db.refresh(n)
    return n


@router.post("/notifications/read-all", response_model=UnreadCountOut)
def mark_all_read(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.query(Notification).filter(
        Notification.recipient_id == user.id, Notification.is_read.is_(False)
    ).update({"is_read": True})
    db.commit()
    return {"unread": 0}


# ==================== 行情（公开，无需登录） ====================
@router.get("/market/realtime")
def get_market_realtime(db: Session = Depends(get_db)):
    """返回实时行情快照（直接读库，<1ms）。空库返回 data=null + pending 状态。"""
    snap = market_service.get_snapshot(db)
    if snap is None:
        return {"data": None, "fetch_status": "pending", "updated_at": None}
    return {
        "data": snap.data,
        "fetch_status": snap.fetch_status,
        "fetch_error": snap.fetch_error,
        "updated_at": snap.updated_at.isoformat() if snap.updated_at else None,
    }


@router.get("/market/kline")
def get_market_kline(db: Session = Depends(get_db), index: str = "1.000001"):
    """返回指定指数的日 K 线（最多 40 条，直接读库）。
    支持指数代码: 1.000001(上证), 0.399001(深证), 0.399006(创业板), 1.000688(科创50)。
    """
    rows = market_service.get_kline(db, index_code=index)
    result = []
    for r in rows:
        obj = {
            "fullDate": r.trade_date,
            "date": r.trade_date[5:],
            "open": r.open, "close": r.close, "high": r.high, "low": r.low,
            "volume": r.volume, "turnover": r.turnover,
            "changePct": r.change_pct,
            "indexCode": r.index_code,
        }
        if r.raw:
            raw = r.raw
            if len(raw) > 6: obj["amplitude"] = float(raw[7]) if raw[7] else 0.0
            if len(raw) > 9: obj["change"] = float(raw[9]) if raw[9] else 0.0
            if len(raw) > 10: obj["turnoverRate"] = float(raw[10]) if raw[10] else 0.0
        result.append(obj)
    return {"data": result, "index": index}


@router.get("/market/sectors")
def get_market_sectors(db: Session = Depends(get_db), limit: int = 80):
    """返回行业板块行情（按涨跌幅降序，直接读库）。空库返回 []。"""
    rows = market_service.get_sectors(db, limit=limit)
    result = []
    for r in rows:
        result.append({
            "code": r.sector_code,
            "name": r.sector_name,
            "changePct": r.change_pct,
            "turnover": r.turnover,
            "upCount": r.up_count,
            "downCount": r.down_count,
        })
    return {"data": result}


@router.get("/market/analysis")
def get_market_analysis(db: Session = Depends(get_db)):
    """返回市场深度分析：高低切 / 领涨方向 / 核心驱动因素。"""
    result = market_analysis_service.get_market_analysis(db)
    return {"data": result}


# ==================== 个股行情 ====================
@router.get("/stocks/prices")
def get_stock_prices_api(codes: str = "", db: Session = Depends(get_db)):
    """批量查询个股实时行情（?codes=600519,000001）。

    不传 codes 参数时自动查询所有活跃持仓股票。
    """
    from ..services.stock_price_service import get_stock_prices as _get, get_all_holding_codes
    if codes.strip():
        code_list = [c.strip() for c in codes.split(",") if c.strip()]
    else:
        code_list = get_all_holding_codes(db)

    prices = _get(db, code_list)
    return {
        "prices": {
            code: data for code, data in prices.items() if data is not None
        },
        "updated_at": dt.datetime.utcnow().isoformat(),
    }


# ==================== 组合估值 / 盈亏 ====================
@router.get("/clients/{client_id}/portfolio")
def get_client_portfolio(
    client_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """返回客户的实时组合估值（含实时价格、总盈亏、今日盈亏）。"""
    client = client_service.get_visible_client(db, user, client_id)
    from ..services.pnl_service import compute_portfolio
    portfolio = compute_portfolio(db, client)
    return portfolio


@router.get("/clients/{client_id}/pnl-history")
def get_client_pnl_history(
    client_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    range: int = 30,
):
    """返回客户最近 N 天的盈亏历史（收益曲线数据）。"""
    client = client_service.get_visible_client(db, user, client_id)
    from ..services.pnl_service import get_pnl_history
    return get_pnl_history(db, client.id, range_days=range)


@router.post("/admin/snapshots/recalculate")
def recalculate_snapshots_api(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    client_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
):
    """用历史K线收盘价重算盈亏快照（管理员，数据修复机制）。

    Args:
        client_id: 指定客户ID，为空则全部客户
        start_date: 起始日期 YYYY-MM-DD（默认：最早交易记录日期）
        end_date: 截止日期 YYYY-MM-DD（默认：今天）
    """
    from ..core.deps import require_roles
    require_roles(ROLE_ADMIN)(user)
    from ..services.pnl_service import recalculate_snapshots
    return recalculate_snapshots(db, client_id=client_id,
                                 start_date=start_date, end_date=end_date)


@router.get("/admin/snapshots/validate")
def validate_snapshots_api(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    client_id: Optional[str] = None,
):
    """校验快照数据完整性（管理员）：缺失交易日 / 非交易日快照 / 连续同值 / NULL 值。"""
    from ..core.deps import require_roles
    require_roles(ROLE_ADMIN)(user)
    from ..services.pnl_service import validate_snapshot_integrity
    return validate_snapshot_integrity(db, client_id=client_id)


@router.post("/stocks/refresh")
async def trigger_stock_refresh(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """手动触发一次个股行情刷新（管理员）。"""
    from ..core.deps import require_roles
    require_roles(ROLE_ADMIN)(user)
    from ..services.stock_price_service import refresh_stock_prices
    ok = await refresh_stock_prices(db)
    return {"status": "ok" if ok else "fail"}


@router.post("/stocks/refresh-daily-kline")
async def trigger_daily_kline_refresh(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = 90,
):
    """手动触发日K线数据刷新，导入真实市场数据到 stock_daily_price 表（管理员）。

    Args:
        limit: 拉取的历史天数，默认90天
    """
    from ..core.deps import require_roles
    require_roles(ROLE_ADMIN)(user)
    from ..services.stock_price_service import refresh_all_daily_klines
    result = await refresh_all_daily_klines(db, limit=limit)
    return {"status": "ok", "data": result}


# ==================== 系统配置（手续费等） ====================

@router.get("/settings/trading-fees")
def get_trading_fees(user: User = Depends(get_current_user)):
    """获取当前手续费配置（所有登录用户可读）。"""
    from .. import config
    return {
        "commission_rate": config.TRADING_FEE_COMMISSION,
        "min_commission": config.TRADING_FEE_MIN_COMMISSION,
        "stamp_tax_rate": config.TRADING_FEE_STAMP_TAX,
        "transfer_fee_rate": config.TRADING_FEE_TRANSFER_FEE,
        "description": {
            "commission": "佣金费率（双边收取，最低5元）",
            "stamp_tax": "印花税（仅卖出收取）",
            "transfer_fee": "过户费（沪深两市双边收取）",
        }
    }


@router.put("/settings/trading-fees")
def update_trading_fees(
    body: dict,
    user: User = Depends(get_current_user),
):
    """更新手续费配置（管理员）。修改后立即生效（进程内）。"""
    from ..core.deps import require_roles
    require_roles(ROLE_ADMIN)(user)
    from .. import config

    if "commission_rate" in body:
        config.TRADING_FEE_COMMISSION = float(body["commission_rate"])
    if "min_commission" in body:
        config.TRADING_FEE_MIN_COMMISSION = float(body["min_commission"])
    if "stamp_tax_rate" in body:
        config.TRADING_FEE_STAMP_TAX = float(body["stamp_tax_rate"])
    if "transfer_fee_rate" in body:
        config.TRADING_FEE_TRANSFER_FEE = float(body["transfer_fee_rate"])

    return {
        "status": "ok",
        "commission_rate": config.TRADING_FEE_COMMISSION,
        "min_commission": config.TRADING_FEE_MIN_COMMISSION,
        "stamp_tax_rate": config.TRADING_FEE_STAMP_TAX,
        "transfer_fee_rate": config.TRADING_FEE_TRANSFER_FEE,
    }


# ==================== 交易记录 ====================
@router.post("/clients/{client_id}/transactions", response_model=TransactionOut, status_code=status.HTTP_201_CREATED)
def create_transaction(
    client_id: str,
    body: TransactionCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """创建交易记录（买入/卖出）。卖出时会自动计算已实现盈亏（含手续费）。"""
    client = client_service.get_visible_client(db, user, client_id)

    # 确定交易日期
    trade_date = body.trade_date or dt.date.today().isoformat()

    # 手续费计算异常（范围非法等）统一转 422
    try:
        return _create_transaction_impl(db, client_id, body, trade_date)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))


def _create_transaction_impl(db: Session, client_id: str, body, trade_date: str) -> Transaction:

    # 计算已实现盈亏（仅卖出时，含手续费）
    realized_pnl = 0.0
    if body.action == "sell":
        from ..services.pnl_service import calc_realized_pnl_with_fee
        # 从现有持仓获取成本价（如果未提供 cost_price）
        cost_price = body.cost_price
        if cost_price is None:
            position = db.query(Position).filter(
                Position.client_id == client_id,
                Position.code == body.code,
            ).first()
            if position:
                cost_price = position.cost_price
            else:
                cost_price = 0.0

        # 使用含手续费的计算
        pnl_result = calc_realized_pnl_with_fee(
            buy_price=cost_price,
            sell_price=body.price,
            quantity=body.quantity,
            fee_mode=body.fee_mode,
            fee_value=body.fee_value,
        )
        realized_pnl = pnl_result["net_pnl"]  # 净盈亏（扣除所有手续费）

    # 买入：按指定模式（或全局默认配置）计算本笔手续费
    fee_amount = 0.0
    if body.action == "buy":
        from ..services.pnl_service import calc_buy_fee
        buy_fee = calc_buy_fee(body.price * body.quantity, body.fee_mode, body.fee_value)
        fee_amount = buy_fee["total_fee"]
    elif body.action == "sell":
        fee_amount = pnl_result.get("fee_amount", 0.0)

    tx = Transaction(
        client_id=client_id,
        code=body.code,
        name=body.name,
        action=body.action,
        quantity=body.quantity,
        price=body.price,
        cost_price=body.cost_price,
        fee_mode=body.fee_mode,
        fee_value=body.fee_value,
        fee_amount=fee_amount,
        realized_pnl=realized_pnl,
        trade_date=trade_date,
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return tx


@router.get("/clients/{client_id}/transactions", response_model=List[TransactionOut])
def list_transactions(
    client_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = 100,
):
    """获取客户的交易记录列表。"""
    client = client_service.get_visible_client(db, user, client_id)
    transactions = db.query(Transaction).filter(
        Transaction.client_id == client_id
    ).order_by(Transaction.trade_date.desc(), Transaction.id.desc()).limit(limit).all()
    return transactions
