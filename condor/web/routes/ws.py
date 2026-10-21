from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from condor.web.auth import extract_ws_token
from condor.web.ws_manager import get_ws_manager

router = APIRouter()
log = logging.getLogger(__name__)


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    auth_token, accept_subprotocol = extract_ws_token(ws)
    manager = get_ws_manager()
    conn = await manager.connect(ws, auth_token, subprotocol=accept_subprotocol)
    if conn is None:
        return

    try:
        while True:
            raw = await ws.receive_text()
            try:
                await manager.handle_message(conn, raw)
            except WebSocketDisconnect:
                raise
            except Exception:
                # One malformed/failing message must not tear down the whole
                # multiplexed connection (all subscribed channels).
                log.exception("WS message error for user %s (ignored)", conn.user_id)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("WS error for user %s", conn.user_id)
    finally:
        manager.disconnect(conn)
