"""``send_notification`` crosses back into the main process (ARCH-088).

What is pinned here is that an announcement leaves two traces, not one: the
Telegram push it always had, plus a ``system`` turn on the conversation that
produced it — and that neither can cost the other. A session with nothing behind
it still gets its push, and a main process that cannot be reached still gets the
message out through the tool's own direct Telegram path.

Sync tests driving coroutines with ``asyncio.run``: ``pytest-asyncio`` is a dev
dependency but is not installed in this venv.
"""

import asyncio

import pytest
from aiohttp import ClientConnectorError
from bs4 import BeautifulSoup
from fastapi import HTTPException
from telegram.error import BadRequest

from condor.web.models import WebUser
from condor.web.routes.agents import NotifyRequest, notify_user
from mcp_servers.condor.exceptions import APIError
from mcp_servers.condor.tools import notification as notification_tool

# The pushes below target chat 1 — the caller's own private chat. A foreign
# chat id is refused since SEC-198 (see test_agents_chat_id_ownership.py).
CALLER = WebUser(id=1, role="user")


class _FakeBot:
    """Stands in for the resolved outbound bot.

    ``ok`` mirrors what ``_HttpBot`` returns; ``raises`` mirrors a live
    python-telegram-bot rejecting bad Markdown.
    """

    def __init__(self, ok=True, raises_with_parse_mode=False):
        self.ok = ok
        self.raises_with_parse_mode = raises_with_parse_mode
        self.calls: list[dict] = []

    async def send_message(self, **kw):
        self.calls.append(kw)
        if kw.get("parse_mode") and self.raises_with_parse_mode:
            raise BadRequest("can't parse entities")
        visible = (
            BeautifulSoup(kw["text"], "html.parser").get_text()
            if kw.get("parse_mode") == "HTML"
            else kw["text"]
        )
        if len(visible.encode("utf-16-le")) // 2 > 4096:
            raise BadRequest("Message is too long")
        return {"ok": self.ok}


@pytest.fixture
def bot(monkeypatch):
    """Resolve the outbound ladder to a fake, and capture transcript writes."""
    fake = _FakeBot()
    from condor.agents import delegate as delegate_module

    monkeypatch.setattr(delegate_module, "resolve_bot", lambda b=None: fake)
    return fake


@pytest.fixture
def notes(monkeypatch):
    written: list[tuple] = []
    from condor.runtime import conversations

    monkeypatch.setattr(
        conversations,
        "record_system",
        lambda user_id, conv_id, text, kind="": written.append(
            (user_id, conv_id, text, kind)
        ),
    )
    return written


def _resolves_to(monkeypatch, conversation_id: str, owner: int = CALLER.id):
    """Stub the session registry: a key names a live session recorded under
    ``owner`` with ``conversation_id`` on it, or no session at all when the
    conversation id is empty."""
    from condor.runtime import client
    from condor.runtime.models import SessionInfo

    async def fake_get_info(key):
        if not conversation_id:
            return None
        return SessionInfo(
            key=str(key),
            agent_key="condor",
            user_id=owner,
            conversation_id=conversation_id,
        )

    monkeypatch.setattr(client, "get_info", fake_get_info)


# ── The route: one push, one note ──


def test_a_live_session_gets_both_a_transcript_note_and_the_telegram_push(
    monkeypatch, bot, notes
):
    _resolves_to(monkeypatch, "conv-1")

    result = asyncio.run(
        notify_user(
            NotifyRequest(
                text="LP position closed",
                chat_id=1,
                session_key="web:1:slot-1",
            ),
            user=CALLER,
        )
    )

    assert result == {"sent": True, "recorded": True}
    assert notes == [(1, "conv-1", "LP position closed", "notification")]
    assert len(bot.calls) == 1
    assert bot.calls[0]["chat_id"] == 1


def test_the_note_is_scoped_to_the_jwt_caller_not_the_posted_user_id(
    monkeypatch, bot, notes
):
    """Mirror delegate: a caller cannot write into another transcript."""
    _resolves_to(monkeypatch, "conv-1")

    asyncio.run(
        notify_user(
            NotifyRequest(text="hi", chat_id=1, user_id=999, session_key="web:1:s"),
            user=CALLER,
        )
    )

    assert notes[0][0] == 1


def test_a_dead_session_key_still_pushes_and_notes_nothing(monkeypatch, bot, notes):
    _resolves_to(monkeypatch, "")

    result = asyncio.run(
        notify_user(NotifyRequest(text="ping", chat_id=1, session_key=""), user=CALLER)
    )

    # No conversation to write into, so no transcript note -- but the dashboard
    # bell is addressed to the user, not to a conversation, so it still records
    # and the call still counts as delivered (FEAT-048).
    assert result == {"sent": True, "recorded": True}
    assert notes == []
    assert len(bot.calls) == 1


def test_an_unwritable_transcript_does_not_cost_the_user_the_push(
    monkeypatch, bot, notes
):
    _resolves_to(monkeypatch, "conv-1")
    from condor.runtime import conversations

    def explode(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(conversations, "record_system", explode)

    result = asyncio.run(
        notify_user(
            NotifyRequest(text="ping", chat_id=1, session_key="web:1:s"), user=CALLER
        )
    )

    # ``recorded`` is now true on the bell alone (FEAT-048): the transcript
    # write failed, and that must not cost the user either delivery.
    assert result == {"sent": True, "recorded": True}
    assert len(bot.calls) == 1


def test_bad_markdown_is_retried_as_plain_text(monkeypatch, notes):
    _resolves_to(monkeypatch, "")
    fake = _FakeBot(raises_with_parse_mode=True)
    from condor.agents import delegate as delegate_module

    monkeypatch.setattr(delegate_module, "resolve_bot", lambda b=None: fake)

    result = asyncio.run(
        notify_user(NotifyRequest(text="a_b_c", chat_id=1), user=CALLER)
    )

    assert result["sent"] is True
    assert [c.get("parse_mode") for c in fake.calls] == ["HTML", None]


def test_a_web_session_with_no_chat_records_without_pushing(monkeypatch, bot, notes):
    """``chat_id=0`` means there is no Telegram chat behind this session."""
    _resolves_to(monkeypatch, "conv-1")

    result = asyncio.run(
        notify_user(
            NotifyRequest(text="done", chat_id=0, session_key="web:1:s"), user=CALLER
        )
    )

    assert result == {"sent": False, "recorded": True}
    assert bot.calls == []


def test_empty_text_is_rejected(monkeypatch, bot, notes):
    _resolves_to(monkeypatch, "conv-1")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(notify_user(NotifyRequest(text="", chat_id=1), user=CALLER))

    assert exc.value.status_code == 400


# ── The tool: prefers the main process, survives without it ──


def test_the_tool_goes_through_the_main_process_and_never_touches_telegram(monkeypatch):
    calls: list[tuple] = []

    async def fake_call(method, path, body=None, timeout=None):
        calls.append((method, path, body))
        return {"sent": True, "recorded": True}

    monkeypatch.setattr(notification_tool, "call_main_api", fake_call)
    monkeypatch.setattr(
        notification_tool.settings, "session_key", "web:1:slot-1", raising=False
    )
    monkeypatch.setattr(notification_tool, "Bot", _no_direct_bot)

    assert asyncio.run(notification_tool.send_notification("hello")) == {
        "sent": True,
        "recorded": True,
    }
    method, path, body = calls[0]
    assert (method, path) == ("POST", "/agents/notify")
    assert body["session_key"] == "web:1:slot-1"
    assert body["text"] == "hello"


def test_a_recorded_only_notification_is_not_reported_as_sent(monkeypatch):
    """A bell entry must never masquerade as a Telegram delivery receipt."""

    async def fake_call(*a, **kw):
        return {"sent": False, "recorded": True}

    monkeypatch.setattr(notification_tool, "call_main_api", fake_call)
    monkeypatch.setattr(notification_tool, "Bot", _no_direct_bot)

    assert asyncio.run(notification_tool.send_notification("hello")) == {
        "sent": False,
        "recorded": True,
    }


def test_the_tool_falls_back_to_direct_telegram_when_the_main_api_is_down(monkeypatch):
    posted: list[dict] = []
    monkeypatch.setattr(notification_tool, "call_main_api", _offline)
    monkeypatch.setattr(notification_tool.settings, "bot_token", "T", raising=False)
    monkeypatch.setattr(notification_tool.settings, "chat_id", 42, raising=False)
    monkeypatch.setattr(notification_tool, "Bot", _direct_bot(posted))

    assert asyncio.run(notification_tool.send_notification("hello")) == {
        "sent": True,
        "recorded": False,
    }
    assert posted[0]["chat_id"] == 42


def test_a_route_that_could_not_deliver_is_not_replayed(monkeypatch):
    async def fake_call(*a, **kw):
        return {"sent": False, "recorded": False}

    monkeypatch.setattr(notification_tool, "call_main_api", fake_call)
    monkeypatch.setattr(notification_tool, "Bot", _no_direct_bot)

    assert asyncio.run(notification_tool.send_notification("hello")) == {
        "sent": False,
        "recorded": False,
    }


@pytest.mark.parametrize(
    "reason", ["request timed out", "API error (403): Access denied"]
)
def test_an_unconfirmed_or_rejected_request_is_never_replayed(monkeypatch, reason):
    async def failed(*args, **kwargs):
        raise APIError(reason)

    monkeypatch.setattr(notification_tool, "call_main_api", failed)
    monkeypatch.setattr(notification_tool, "Bot", _no_direct_bot)

    result = asyncio.run(notification_tool.send_notification("hello"))

    assert result["sent"] is False
    assert reason in result["error"]
    assert "not replayed" in result["error"]
    # Whether the dashboard recorded a timed-out call is unknown.
    assert "recorded" not in result


# ── Bot stand-ins for the direct path ──


async def _offline(*args, **kwargs):
    raise APIError("connection refused") from ClientConnectorError(
        None, ConnectionRefusedError("connection refused")
    )


class _no_direct_bot:
    def __init__(self, *a, **kw):
        raise AssertionError("the tool must not talk to Telegram directly here")


def _direct_bot(posted: list, fail_on: int | None = None):
    class _Bot(_FakeBot):
        def __init__(self, *a, **kw):
            super().__init__()
            self.calls = posted

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def send_message(self, **kw):
            result = await super().send_message(**kw)
            if len(self.calls) == fail_on:
                raise BadRequest("second page rejected")
            return result

    return _Bot


@pytest.mark.parametrize("parse_mode", ["Markdown", "HTML", ""])
def test_long_notification_reaches_telegram_and_is_recorded_once(
    monkeypatch, bot, notes, parse_mode
):
    from condor.notifications import list_for

    _resolves_to(monkeypatch, "conv-1")
    text = "中文🔎 & 数据 " * 600 + "\n\nTG-RESTART-END"
    if parse_mode == "Markdown":
        text = "**测试标题**\n\n" + text
    elif parse_mode == "HTML":
        text = "<b>测试标题</b>\n\n" + text.replace("&", "&amp;")

    async def call_route(method, path, body, timeout=None):
        return await notify_user(NotifyRequest(**body), user=CALLER)

    monkeypatch.setattr(notification_tool, "call_main_api", call_route)
    monkeypatch.setattr(notification_tool.settings, "chat_id", CALLER.id)
    monkeypatch.setattr(notification_tool.settings, "session_key", "web:1:s")
    monkeypatch.setattr(notification_tool, "Bot", _no_direct_bot)

    result = asyncio.run(notification_tool.send_notification(text, parse_mode))

    assert result == {"sent": True, "recorded": True}
    assert len(bot.calls) > 1
    visible = "".join(
        (
            BeautifulSoup(c["text"], "html.parser").get_text()
            if c.get("parse_mode") == "HTML"
            else c["text"]
        )
        for c in bot.calls
    )
    assert visible.count("中文🔎 & 数据 ") == 600
    assert visible.endswith("TG-RESTART-END")
    if parse_mode:
        assert "<b>测试标题</b>" in bot.calls[0]["text"]
    assert notes == [(CALLER.id, "conv-1", text, "notification")]
    assert [n.text for n in list_for(CALLER.id)] == [text]


@pytest.mark.parametrize("http_response", [False, True])
def test_partial_delivery_reports_failure_without_replaying_sent_pages(
    monkeypatch, bot, notes, http_response
):
    from condor.notifications import list_for

    _resolves_to(monkeypatch, "conv-1")
    original_send = bot.send_message

    async def reject_second_page(**kw):
        result = await original_send(**kw)
        if len(bot.calls) == 2:
            if http_response:
                return {"ok": False, "description": "second page rejected"}
            raise BadRequest("second page rejected")
        return result

    async def call_route(method, path, body, timeout=None):
        return await notify_user(NotifyRequest(**body), user=CALLER)

    monkeypatch.setattr(bot, "send_message", reject_second_page)
    monkeypatch.setattr(notification_tool, "call_main_api", call_route)
    monkeypatch.setattr(notification_tool.settings, "chat_id", CALLER.id)
    monkeypatch.setattr(notification_tool.settings, "session_key", "web:1:s")
    monkeypatch.setattr(notification_tool, "Bot", _no_direct_bot)
    text = "消息🔎 " * 2400 + "最后一段"

    result = asyncio.run(notification_tool.send_notification(text))

    assert result == {"sent": False, "recorded": True, "error": "second page rejected"}
    assert len(bot.calls) == 2
    assert len(notes) == 1
    assert [n.text for n in list_for(CALLER.id)] == [text]


@pytest.mark.parametrize("parse_mode", ["Markdown", "HTML"])
@pytest.mark.parametrize("fail_on", [None, 2])
def test_direct_fallback_splits_long_messages_and_reports_partial_failure(
    monkeypatch, parse_mode, fail_on
):
    posted = []
    monkeypatch.setattr(notification_tool, "call_main_api", _offline)
    monkeypatch.setattr(notification_tool.settings, "bot_token", "T")
    monkeypatch.setattr(notification_tool.settings, "chat_id", 42)
    monkeypatch.setattr(notification_tool, "Bot", _direct_bot(posted, fail_on))
    body = "测试🔎 " * 2000 + "TG-RESTART-END"
    text = f"<b>{body}</b>" if parse_mode == "HTML" else f"**{body}**"

    result = asyncio.run(notification_tool.send_notification(text, parse_mode))

    assert len(posted) >= 2
    assert result["sent"] is (fail_on is None)
    assert result["recorded"] is False
    if fail_on:
        assert len(posted) == fail_on
        assert result["error"] == "second page rejected"
    else:
        assert "error" not in result
        visible = "".join(
            BeautifulSoup(c["text"], "html.parser").get_text() for c in posted
        )
        assert visible == body


# ── The rung every other test here stubs away ──


@pytest.fixture
def store(monkeypatch):
    """A private bell store and an empty sink registry (see test_notifications).

    conftest's ``$CONDOR_DATA_DIR`` already isolates the store's file.
    """
    from condor import notifications

    monkeypatch.setattr(notifications, "_push_sinks", [])


@pytest.fixture
def no_telegram(monkeypatch):
    """The real ladder with nothing above its bottom rung: no bot, no token."""

    class _NoBotStore:
        def get_bot(self):
            return None

    class _CM:
        def get_user(self, user_id):
            return {"id": user_id} if user_id == CALLER.id else None

    monkeypatch.setattr("condor.routine_store.get_routine_store", lambda: _NoBotStore())
    monkeypatch.setattr("config_manager.get_config_manager", lambda: _CM())
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)


def test_a_telegram_less_install_files_the_notification_once(
    monkeypatch, notes, store, no_telegram
):
    """The bell rung records the push; recording again filed it twice (ARCH-212).

    Deliberately does *not* stub ``resolve_bot``, unlike every other test in
    this file — the duplicate only existed below the two Telegram rungs, so a
    fake bot could never see it.
    """
    _resolves_to(monkeypatch, "")
    from condor.agents.delegate import resolve_bot
    from condor.notifications import NotifyBot, list_for

    assert isinstance(resolve_bot(), NotifyBot)

    result = asyncio.run(
        notify_user(
            NotifyRequest(text="the thing happened", chat_id=CALLER.id), user=CALLER
        )
    )

    # Nothing reached Telegram. Both the route and tool report the bell
    # delivery independently, without claiming that Telegram received it.
    assert result == {"sent": False, "recorded": True}
    items = list_for(CALLER.id)
    assert [(n.text, n.kind) for n in items] == [("the thing happened", "agent")]
