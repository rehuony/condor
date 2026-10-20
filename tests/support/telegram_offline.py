"""An aiogram ``Bot`` and :class:`TelegramApp` that never reach the network.

:class:`OfflineSession` is a real :class:`aiogram.client.session.aiohttp.AiohttpSession`
whose HTTP client is replaced, so aiogram's own request building, response
checking and error wrapping run exactly as in production. A test supplies a
``respond(method, fields)`` callable that returns the Telegram ``result``
payload, a full ``{"ok": False, ...}`` envelope to provoke an API error, or
raises an ``aiohttp`` exception to provoke a network error.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import Update

from condor.telegram.app import TelegramApp
from condor.telegram.state import UserStateStore

BOT_USER = {
    "id": 123,
    "is_bot": True,
    "first_name": "Condor",
    "username": "condor_test",
}

Responder = Callable[[str, dict], Any]


def _fields(form) -> dict:
    """The string fields of an ``aiohttp.FormData`` as a plain dict."""
    out = {}
    for options, _headers, value in getattr(form, "_fields", ()):
        if isinstance(value, str):
            out[options["name"]] = value
    return out


class _Response:
    def __init__(self, status: int, payload: dict):
        self.status = status
        self._payload = payload

    async def text(self) -> str:
        return json.dumps(self._payload)


class _Post:
    def __init__(self, outcome):
        self._outcome = outcome

    async def __aenter__(self):
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        payload = self._outcome
        if isinstance(payload, dict) and "ok" in payload:
            return _Response(payload.get("error_code", 200), payload)
        return _Response(200, {"ok": True, "result": payload})

    async def __aexit__(self, *exc) -> bool:
        return False


class _Client:
    def __init__(self, session: "OfflineSession"):
        self._session = session

    def post(self, url: str, data=None, timeout=None):
        method = url.rsplit("/", 1)[-1]
        fields = _fields(data)
        self._session.requests.append((method, fields))
        return _Post(self._session.respond(method, fields))


def default_respond(method: str, fields: dict) -> Any:
    """Acknowledge every method the way Telegram would on success."""
    if method == "getMe":
        return BOT_USER
    if method in {
        "sendMessage",
        "editMessageText",
        "sendPhoto",
        "sendDocument",
        "sendRichMessage",
    }:
        default_respond.counter += 1
        return {
            "message_id": default_respond.counter,
            "date": 1,
            "chat": {"id": int(fields.get("chat_id", 0)), "type": "private"},
            "from": BOT_USER,
            "text": fields.get("text", ""),
        }
    return True


default_respond.counter = 100


class OfflineSession(AiohttpSession):
    """aiogram's session with the HTTP client stubbed out."""

    def __init__(self, respond: Responder | None = None):
        super().__init__()
        self.respond: Responder = respond or default_respond
        self.requests: list[tuple[str, dict]] = []

    async def create_session(self):
        return _Client(self)

    async def close(self) -> None:
        pass


def offline_app(tmp_path, respond: Responder | None = None) -> TelegramApp:
    """A :class:`TelegramApp` over an offline session and a fresh state store."""
    store = UserStateStore(tmp_path / "state.pickle")
    return TelegramApp("123:offline-test", store, session=OfflineSession(respond))


def update_from(bot, data: dict) -> Update:
    """Parse a Bot API update payload and bind its objects to ``bot``."""
    return Update.model_validate(data, context={"bot": bot})


def message_update(
    bot,
    content: dict | None = None,
    *,
    kind: str = "message",
    chat_type: str = "private",
    chat_id: int = 7,
    user_id: int = 7,
    message_id: int = 11,
    update_id: int = 20,
) -> Update:
    """One incoming message-like update for ``chat_id``/``user_id``."""
    data = {
        "message_id": message_id,
        "date": 1,
        "chat": {"id": chat_id, "type": chat_type},
        "from": {"id": user_id, "is_bot": False, "first_name": "Tester"},
        **(content or {}),
    }
    return update_from(bot, {"update_id": update_id, kind: data})


def now() -> datetime:
    return datetime.now(timezone.utc)
