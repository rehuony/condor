"""Tests for one-time web login token redemption (condor/web/auth.py)."""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, WebSocketDisconnect
from starlette.testclient import TestClient

import condor.web.auth as auth
from condor.web.auth import create_login_token, redeem_login_token


def _reset_store():
    auth._pending_login_tokens.clear()


def test_valid_token_redeems_once():
    """A fresh token redeems successfully exactly once (single-use)."""
    _reset_store()
    token = create_login_token(123, username="u", first_name="f")

    info = redeem_login_token(token)
    assert info is not None
    assert info["user_id"] == 123

    # Single-use: a second redemption of the same token fails.
    assert redeem_login_token(token) is None


def test_expired_token_is_rejected():
    """A token older than the TTL is rejected."""
    _reset_store()
    token = create_login_token(123)
    # Backdate creation beyond the TTL.
    auth._pending_login_tokens[token]["created_at"] = (
        time.time() - auth._LOGIN_TOKEN_TTL - 1
    )
    assert redeem_login_token(token) is None


def test_unknown_token_is_rejected():
    """A random/unknown token is rejected without error."""
    _reset_store()
    assert redeem_login_token("does-not-exist") is None


def test_expired_redemptions_do_not_block_later_valid_redemption():
    """CORR-031: repeated expired-link clicks must not lock out a valid login.

    A user who opens several stale /web links (expired tokens) must still be
    able to redeem a fresh, valid token afterwards for the same user_id.
    """
    _reset_store()
    user_id = 999

    # Simulate many expired-token redemptions in a row.
    for _ in range(10):
        stale = create_login_token(user_id)
        auth._pending_login_tokens[stale]["created_at"] = (
            time.time() - auth._LOGIN_TOKEN_TTL - 1
        )
        assert redeem_login_token(stale) is None

    # A subsequent valid token must still redeem.
    valid = create_login_token(user_id)
    info = redeem_login_token(valid)
    assert info is not None
    assert info["user_id"] == user_id


@pytest.mark.parametrize("path", ["/ws", "/ws/chat"])
@pytest.mark.parametrize("subprotocol", [False, True])
def test_websockets_require_the_current_auth_subprotocol(
    monkeypatch, path, subprotocol
):
    from condor.web import ws_manager
    from condor.web.routes import chat_ws, ws
    from config_manager import UserRole

    monkeypatch.setattr(
        "config_manager.get_config_manager",
        lambda: SimpleNamespace(get_user_role=lambda user_id: UserRole.USER),
    )
    manager = ws_manager.WebSocketManager()
    monkeypatch.setattr(ws, "get_ws_manager", lambda: manager)
    monkeypatch.setattr(chat_ws, "_get_user_sessions", AsyncMock(return_value=[]))
    app = FastAPI()
    app.include_router(ws.router)
    app.include_router(chat_ws.router)
    token = auth.create_jwt(123)
    client = TestClient(app)

    if subprotocol:
        with client.websocket_connect(
            path, subprotocols=[auth.WS_AUTH_SUBPROTOCOL, token]
        ) as socket:
            assert socket.accepted_subprotocol == auth.WS_AUTH_SUBPROTOCOL
            if path == "/ws/chat":
                assert socket.receive_json() == {
                    "event": "sessions_list",
                    "sessions": [],
                }
    else:
        with pytest.raises(WebSocketDisconnect) as rejected:
            with client.websocket_connect(f"{path}?token={token}"):
                pytest.fail("a query token authenticated the socket")
        assert rejected.value.code == 4001
