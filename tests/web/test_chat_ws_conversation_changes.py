"""Telegram turns invalidate durable history in the owner's open web tabs."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from condor.runtime import SessionInfo, conversations
from condor.runtime.events import RuntimeEvent
from condor.web.routes import chat_ws

USER = 333


class _Socket:
    def __init__(self):
        self.sent: list[dict] = []
        self.received = asyncio.Event()

    async def send_text(self, raw):
        self.sent.append(json.loads(raw))
        self.received.set()


def _change(phase="accepted", *, busy=True):
    return {
        "user_id": USER,
        "conversation_id": "telegram-conversation",
        "session_key": "tg:9876",
        "phase": phase,
        "is_busy": busy,
        "sequence": 1 if phase == "accepted" else 2,
    }


def test_telegram_change_reaches_every_owners_tab_without_a_web_session(monkeypatch):
    first, second, other_user = _Socket(), _Socket(), _Socket()
    monkeypatch.setattr(
        chat_ws,
        "_attached_sockets",
        {USER: {first, second}, USER + 1: {other_user}},
    )
    prompt = AsyncMock(side_effect=AssertionError("must not replay the prompt"))
    monkeypatch.setattr(chat_ws.runtime, "prompt", prompt)

    async def scenario():
        await chat_ws._push_conversation_change(_change())
        await chat_ws._push_conversation_change(_change("completed", busy=False))

    asyncio.run(scenario())

    assert other_user.sent == []
    assert (
        first.sent
        == second.sent
        == [
            {
                "event": "conversation_changed",
                "conversation_id": "telegram-conversation",
                "session_key": "tg:9876",
                "phase": phase,
                "is_busy": busy,
                "sequence": 1 if phase == "accepted" else 2,
            }
            for phase, busy in [("accepted", True), ("completed", False)]
        ]
    )
    prompt.assert_not_called()


def test_a_stalled_or_closed_tab_does_not_hold_up_the_healthy_tab(monkeypatch):
    class ClosedSocket:
        async def send_text(self, raw):
            raise RuntimeError("closed")

    class StalledSocket:
        cancelled = False

        async def send_text(self, raw):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    async def scenario():
        healthy, stalled = _Socket(), StalledSocket()
        monkeypatch.setattr(
            chat_ws,
            "_attached_sockets",
            {USER: {healthy, stalled, ClosedSocket()}},
        )
        monkeypatch.setattr(chat_ws, "_SOCKET_SEND_TIMEOUT", 0.05)
        task = asyncio.create_task(chat_ws._push_conversation_change(_change()))
        await asyncio.wait_for(healthy.received.wait(), timeout=0.5)
        assert not task.done(), "the healthy tab should not wait for the stalled one"
        await asyncio.wait_for(task, timeout=0.5)
        return healthy, stalled

    healthy, stalled = asyncio.run(scenario())
    assert len(healthy.sent) == 1
    assert stalled.cancelled


def test_reconnected_web_roster_includes_work_queued_from_telegram(monkeypatch):
    infos = [
        SessionInfo(
            key=f"web:{USER}:{conversation_id}",
            user_id=USER,
            slot=conversation_id,
            conversation_id=conversation_id,
            surface="web",
            agent_key="claude-code",
            is_busy=local_busy,
        )
        for conversation_id, local_busy in [
            ("shared", False),
            ("local", True),
            ("idle", False),
        ]
    ]
    monkeypatch.setattr(chat_ws.runtime, "list_sessions", AsyncMock(return_value=infos))
    monkeypatch.setattr(
        chat_ws.turns,
        "is_busy",
        lambda user_id, cid: (user_id, cid) == (USER, "shared"),
    )
    monkeypatch.setattr(
        chat_ws.turns,
        "pending_count",
        lambda user_id, cid, key: int(
            (user_id, cid, key) == (USER, "local", f"web:{USER}:local")
        ),
    )

    roster = asyncio.run(chat_ws._get_user_sessions(USER))

    assert {item["conversation_id"]: item["is_busy"] for item in roster} == {
        "shared": True,
        "local": True,
        "idle": False,
    }
    assert {item["conversation_id"]: item["web_pending_turns"] for item in roster} == {
        "shared": 0,
        "local": 1,
        "idle": 0,
    }


def test_busy_takeover_does_not_stop_at_the_acceptance_event(monkeypatch):
    cancelled = []
    ws = _Socket()
    monkeypatch.setattr(chat_ws, "_attached_sockets", {})

    async def stream():
        yield RuntimeEvent.accepted()
        cancelled.append("previous turn")
        yield RuntimeEvent.queued()
        raise AssertionError("the answer must be consumed after releasing the gate")

    async def scenario():
        events = stream()
        await chat_ws._pump_one(ws, USER, events, "shared")
        await events.aclose()

    asyncio.run(scenario())
    assert cancelled == ["previous turn"]
    assert ws.sent == [{"event": "queued", "slot_id": "shared"}]


@pytest.mark.parametrize("spawned", [False, True])
def test_a_failed_reattach_closes_the_local_turn(monkeypatch, spawned):
    """Every submitted web turn completes, including failures before runtime."""
    conv = conversations.new_conversation(USER, "web")
    ws = _Socket()
    monkeypatch.setattr(chat_ws, "_attached_sockets", {})
    monkeypatch.setattr(chat_ws, "_active_prompt_tasks", {})
    monkeypatch.setattr(chat_ws, "_pending_spawns", {})
    monkeypatch.setattr(chat_ws, "_slot_gates", {})
    monkeypatch.setattr(chat_ws.runtime, "get_info", AsyncMock(return_value=None))
    monkeypatch.setattr(chat_ws, "_spawn", AsyncMock(return_value=spawned))

    asyncio.run(
        chat_ws._handle_send_message(
            ws, USER, {"slot_id": conv.id, "text": "hello", "client_id": "origin"}
        )
    )

    assert [frame for frame in ws.sent if frame["event"] == "prompt_done"] == [
        {
            "event": "prompt_done",
            "slot_id": conv.id,
            "stop_reason": "error",
            "client_id": "origin",
        }
    ]


def test_takeover_terminal_frame_keeps_its_origin(monkeypatch):
    ws = _Socket()
    monkeypatch.setattr(chat_ws, "_attached_sockets", {})

    async def stream():
        yield RuntimeEvent.done("error")

    asyncio.run(chat_ws._pump_one(ws, USER, stream(), "shared", client_id="origin"))
    assert ws.sent == [
        {
            "event": "prompt_done",
            "slot_id": "shared",
            "stop_reason": "error",
            "usage": None,
            "client_id": "origin",
        }
    ]
