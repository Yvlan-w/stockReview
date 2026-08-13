"""WebSocket 实时推送接口测试。"""
import pytest
from starlette.websockets import WebSocketDisconnect

from app.core.security import create_access_token


def test_ws_connect_valid_token(client):
    token = create_access_token("u_admin", "admin")
    with client.websocket_connect(f"/ws/{token}") as ws:
        msg = ws.receive_json()
        assert msg == {"type": "connected", "user_id": "u_admin"}


def test_ws_invalid_token_rejected(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/bad.token.invalid") as ws:
            ws.receive_json()
    assert exc.value.code == 4401
