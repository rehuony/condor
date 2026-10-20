"""A woken turn renders in Telegram like any other answer (FEAT-034, slice 5).

Wake turns use the same rich-draft presenter as a typed request. Opening the
sink performs no Telegram I/O; the final answer is a new rich message.
"""

import asyncio

import pytest

from condor.agents import delegate as delegate_module
from condor.runtime import SessionKey
from condor.runtime import wake as runtime_wake
from condor.runtime.events import EventType, RuntimeEvent
from condor.runtime.keys import TELEGRAM
from condor.telegram.errors import bad_request
from condor.telegram.types import NO_LINK_PREVIEW
from handlers.agents import wake as tg_wake


class _Message:
    def __init__(self, message_id: int):
        self.message_id = message_id


class _FakeBot:
    """A live ``Bot``: returns Message objects."""

    def __init__(self):
        self.sent: list[dict] = []
        self.edits: list[dict] = []

    async def send_message(self, **kw):
        self.sent.append(kw)
        return _Message(100 + len(self.sent))

    async def send_rich_message(self, **kw):
        payload = dict(kw)
        payload["text"] = getattr(kw.get("rich_message"), "markdown", "")
        self.sent.append(payload)
        return _Message(100 + len(self.sent))

    async def send_rich_message_draft(self, **kw):
        return True

    async def delete_message(self, **kw):
        return True

    async def edit_message_text(self, **kw):
        self.edits.append(kw)
        return None


class _MuteBot(_FakeBot):
    """A sender that acknowledges nothing: no message ever comes back."""

    async def send_message(self, **kw):
        self.sent.append(kw)
        return None

    async def send_rich_message(self, **kw):
        self.sent.append(kw)
        return None

    async def send_rich_message_draft(self, **kw):
        return True


@pytest.fixture(autouse=True)
def _register_wake():
    tg_wake.register_wake()


@pytest.fixture
def bot(monkeypatch):
    fake = _FakeBot()
    monkeypatch.setattr(delegate_module, "resolve_bot", lambda b=None: fake)
    return fake


def _events():
    return [
        RuntimeEvent(
            type=EventType.TEXT,
            data={"text": "the pools I found are thin; not worth it"},
        ),
        RuntimeEvent.done("end_turn"),
    ]


async def _drive(sink):
    await sink.open()
    for event in _events():
        await sink.on_event(event)
    await sink.close()


def test_wake_uses_a_formal_new_final_message(bot):
    asyncio.run(_drive(tg_wake.TelegramWakeSink(42)))
    assert bot.sent[-1]["text"] == "the pools I found are thin; not worth it"
    assert bot.sent[-1]["disable_notification"] is False


def test_a_chat_this_process_cannot_reach_leaves_the_sink_inert(monkeypatch):
    """No placeholder is not a failed turn: the answer is still recorded."""
    fake = _MuteBot()
    monkeypatch.setattr(delegate_module, "resolve_bot", lambda b=None: fake)

    asyncio.run(_drive(tg_wake.TelegramWakeSink(42)))

    assert fake.edits == []


def test_only_a_telegram_key_resolves_to_a_chat_sink():
    assert tg_wake._telegram_wake_sink(SessionKey.telegram(42), None) is not None
    # A web key has no chat behind it; the web sink owns that surface.
    assert tg_wake._telegram_wake_sink(SessionKey.web(1, "conv-1"), 1) is None


# ── The note half of the same channel (CORR-266) ──
#
# A note is not a turn: nothing is prompted and nothing is recorded, so the
# sink only has to put one already-written line in the chat. Telegram
# registered a wake sink but no note sink, so ``wake.deliver_note`` ran its
# whole gauntlet for a ``tg:`` key and then dropped the note with no log line.


class _Info:
    """What the registry says about a session, as ``deliver_note`` reads it."""

    def __init__(self, conversation_id: str, user_id: int = 7, alive: bool = True):
        self.conversation_id = conversation_id
        self.user_id = user_id
        self.alive = alive


@pytest.fixture
def live_session(monkeypatch):
    """A live Telegram session sitting on ``conv-1``."""

    async def info(key):
        return _Info("conv-1")

    monkeypatch.setattr(runtime_wake, "_session_info", info)


def test_the_note_sink_is_registered_once_at_startup():
    """Wake sinks are attached by ``register_wake``, not by importing handlers."""
    tg_wake.register_wake()
    assert runtime_wake._note_sinks.get(TELEGRAM) is tg_wake._deliver_note


def test_a_note_reaches_the_chat_instead_of_returning_false(bot, live_session):
    shown = asyncio.run(
        runtime_wake.deliver_note(
            session_key="tg:42",
            conversation_id="conv-1",
            text="pool-scan finished: 3 pools",
            kind="routine",
        )
    )

    assert shown is True
    assert len(bot.sent) == 1
    assert bot.sent[0]["chat_id"] == 42
    assert bot.sent[0]["text"] == "⚙️ pool-scan finished: 3 pools"
    assert "rich_message" in bot.sent[0]


def test_the_marker_says_what_the_note_is_about():
    """A routine outcome and a delegation outcome must not read alike, and
    neither may read as the agent's own words."""
    assert tg_wake.note_text("done", "routine").startswith("⚙️")
    assert tg_wake.note_text("done", "delegation").startswith("🤖")
    assert tg_wake.note_text("done", "something-new").startswith(
        tg_wake.DEFAULT_NOTE_MARKER
    )


def test_markup_telegram_rejects_is_retried_as_plain_text(monkeypatch, live_session):
    """The user must get the note, ugly, rather than not get it at all -- the
    same rule ``notifications._send`` applies to a completion notice."""

    class _PickyBot(_FakeBot):
        async def send_rich_message(self, **kw):
            raise bad_request("can't parse entities")

        async def send_message(self, **kw):
            if kw.get("parse_mode"):
                raise bad_request("can't parse entities")
            self.sent.append(kw)
            return _Message(1)

    fake = _PickyBot()
    monkeypatch.setattr(delegate_module, "resolve_bot", lambda b=None: fake)

    shown = asyncio.run(
        runtime_wake.deliver_note(
            session_key="tg:42",
            conversation_id="conv-1",
            text="boom *unbalanced",
            kind="routine",
        )
    )

    assert shown is True
    assert fake.sent == [
        {
            "chat_id": 42,
            "text": "⚙️ boom *unbalanced",
            "link_preview_options": NO_LINK_PREVIEW,
        }
    ]


def test_a_chat_this_process_cannot_reach_does_not_raise(monkeypatch, live_session):
    """Same contract as ``TelegramWakeSink.open``: the note is in the
    transcript either way, so an undeliverable one costs the caller nothing."""

    class _DeadBot(_FakeBot):
        async def send_rich_message(self, **kw):
            raise RuntimeError("chat not found")

        async def send_message(self, **kw):
            raise RuntimeError("chat not found")

    monkeypatch.setattr(delegate_module, "resolve_bot", lambda b=None: _DeadBot())

    assert (
        asyncio.run(
            runtime_wake.deliver_note(
                session_key="tg:42",
                conversation_id="conv-1",
                text="done",
                kind="routine",
            )
        )
        is False
    )


def test_a_web_key_is_not_this_sinks_business(bot):
    """The web sink owns that surface; this one has no chat to address."""
    asyncio.run(
        tg_wake._deliver_note(SessionKey.web(1, "conv-1"), 1, "done", "routine")
    )

    assert bot.sent == []
