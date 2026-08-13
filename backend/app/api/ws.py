"""WebSocket 实时推送：客户端携带 JWT 连接，接收站内信实时推送。"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..core.security import decode_access_token
from ..database import SessionLocal
from ..models import User
from ..services.notification_service import manager

ws_router = APIRouter()


@ws_router.websocket("/ws/{token}")
async def websocket_endpoint(websocket: WebSocket, token: str):
    # 鉴权：解析 JWT
    try:
        payload = decode_access_token(token)
        user_id = payload.get("sub")
    except Exception:
        await websocket.close(code=4401)
        return

    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            await websocket.close(code=4401)
            return
    finally:
        db.close()

    await manager.connect(user_id, websocket)
    try:
        await websocket.send_json({"type": "connected", "user_id": user_id})
        while True:
            await websocket.receive_text()  # 保持连接（客户端可发心跳）
    except WebSocketDisconnect:
        manager.disconnect(user_id, websocket)
    except Exception:
        manager.disconnect(user_id, websocket)
