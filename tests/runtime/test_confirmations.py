"""Tests for the confirmation registry (FEAT-010).

This sits on the mutation-authorization path, so the tests that matter most are
the ones pinning the *auto-approve* semantics (a safe tool and the unattended
DELEGATE path must behave exactly as before) and the ones proving every
non-approval outcome cancels.
"""

import asyncio
from types import SimpleNamespace

import pytest

from condor.runtime import confirmations as confirmations_module
from condor.runtime.confirmations import (
    CANCELLED,
    ConfirmationRegistry,
    ConfirmationStatus,
    build_permission_callback,
)
from tests.support.markdown_v2 import plain
from tests.support.runtime_sessions import install_session

# A tool the danger heuristic flags, and one it does not.
DANGEROUS = {"tool": "place_order", "input": {"trading_pair": "SOL-USDC"}}
SAFE = {"tool": "get_market_data", "input": {}}

OPTIONS = [
    {"optionId": "allow", "kind": "allow_once"},
    {"optionId": "deny", "kind": "reject_once"},
]


class _RecordingChannel:
    """Channel that records what it was asked to render."""

    def __init__(self):
        self.delivered = []

    async def deliver(self, pending):
        self.delivered.append(pending)


class _BrokenChannel:
    async def deliver(self, pending):
        raise RuntimeError("channel is down")


@pytest.fixture
def registry(monkeypatch):
    """A fresh registry, swapped in for the process-global singleton."""
    fresh = ConfirmationRegistry()
    monkeypatch.setattr(confirmations_module, "_registry", fresh)
    return fresh


# ── Registry mechanics ──


def test_resolve_wakes_waiter(registry):
    """wait_for returns as soon as resolve lands, not on the TTL."""

    async def scenario():
        pending = registry.register("tg:1", 1, "buy", DANGEROUS, OPTIONS, 30)

        async def answer():
            await asyncio.sleep(0.01)
            return await registry.resolve(pending.id, approved=True, by_user_id=1)

        answered, resolved = await asyncio.gather(
            answer(), registry.wait_for(pending.id)
        )
        return answered, resolved

    answered, resolved = asyncio.run(asyncio.wait_for(scenario(), timeout=2))
    assert answered is True
    assert resolved.status is ConfirmationStatus.APPROVED
    assert resolved.approved is True


def test_timeout_denies(registry):
    """An unanswered request expires as TIMEOUT — never as an approval."""

    async def scenario():
        pending = registry.register("tg:1", 1, "buy", DANGEROUS, OPTIONS, 0)
        return await registry.wait_for(pending.id)

    resolved = asyncio.run(asyncio.wait_for(scenario(), timeout=2))
    assert resolved.status is ConfirmationStatus.TIMEOUT
    assert resolved.approved is False


def test_double_resolve_idempotent(registry):
    """The first answer wins; a second is a no-op that reports False."""

    async def scenario():
        pending = registry.register("tg:1", 1, "buy", DANGEROUS, OPTIONS, 30)
        first = await registry.resolve(pending.id, approved=True, by_user_id=1)
        second = await registry.resolve(pending.id, approved=False, by_user_id=1)
        return first, second, pending.status

    first, second, status = asyncio.run(scenario())
    assert first is True
    assert second is False
    assert status is ConfirmationStatus.APPROVED  # the reject did not overwrite


def test_resolve_authz(registry):
    """Another user cannot answer your approval."""

    async def scenario():
        pending = registry.register(
            "tg:1",
            user_id=1,
            summary="buy",
            tool_call=DANGEROUS,
            options=OPTIONS,
            timeout_seconds=30,
        )
        stranger = await registry.resolve(pending.id, approved=True, by_user_id=999)
        owner = await registry.resolve(pending.id, approved=True, by_user_id=1)
        return stranger, owner

    stranger, owner = asyncio.run(scenario())
    assert stranger is False
    assert owner is True


def test_unknown_id_resolves_false(registry):
    assert asyncio.run(registry.resolve("nope", approved=True, by_user_id=1)) is False
    assert asyncio.run(registry.wait_for("nope")) is None


def test_list_pending_scoped_to_user(registry):
    registry.register("tg:1", 1, "a", DANGEROUS, OPTIONS, 30)
    registry.register("tg:2", 2, "b", DANGEROUS, OPTIONS, 30)

    assert [p.summary for p in registry.list_pending(user_id=1)] == ["a"]
    assert len(registry.list_pending()) == 2


def test_sweep_expires_and_wakes(registry):
    """The sweep denies an overdue entry rather than leaving it pending."""
    pending = registry.register("tg:1", 1, "a", DANGEROUS, OPTIONS, 0)
    registry.sweep()
    assert pending.status is ConfirmationStatus.TIMEOUT
    assert pending._event.is_set()
    assert registry.list_pending(user_id=1) == []


# ── The auto-approve semantics that must not change ──


def test_safe_tool_not_registered(registry):
    """A non-dangerous tool is approved without touching the registry."""
    channel = _RecordingChannel()
    cb = build_permission_callback("tg:1", 1, [channel])

    outcome = asyncio.run(cb(SAFE, OPTIONS))

    assert outcome == {"outcome": {"outcome": "selected", "optionId": "allow"}}
    assert channel.delivered == []
    assert registry.list_pending() == []


def test_delegate_auto_approve_unchanged(registry):
    """DELEGATE passes permission_callback=None, so ACP auto-approves.

    Nothing in this feature may make an unattended run start asking a human;
    this pins that the registry is simply not on that path.
    """
    # The unattended path never builds a callback at all.
    assert registry.list_pending() == []

    # And a builder with no channels denies rather than hanging, so a
    # misconfigured caller fails closed instead of stalling forever.
    cb = build_permission_callback("agent:x", 1, channels=[])
    assert asyncio.run(asyncio.wait_for(cb(DANGEROUS, OPTIONS), timeout=2)) == CANCELLED
    assert registry.list_pending() == []


def test_blocked_tool_refused(registry, monkeypatch):
    """A tool that would bypass RBAC is refused without asking anyone.

    BLOCKED_TOOLS is empty today, so the entry is injected: the point is that
    the gate still fires the moment something is added to that set.
    """
    monkeypatch.setattr(
        "condor.runtime.danger.BLOCKED_TOOLS", {"place_order"}, raising=False
    )
    channel = _RecordingChannel()
    cb = build_permission_callback("tg:1", 1, [channel])

    outcome = asyncio.run(cb({"tool": "place_order", "input": {}}, OPTIONS))

    assert outcome == CANCELLED
    assert channel.delivered == []
    assert registry.list_pending() == []


# ── End-to-end through the callback ──


def test_callback_approves_after_resolve(registry):
    channel = _RecordingChannel()
    cb = build_permission_callback("tg:1", 1, [channel], timeout_seconds=30)

    async def scenario():
        task = asyncio.create_task(cb(DANGEROUS, OPTIONS))
        # Wait for the request to be rendered before answering it.
        for _ in range(100):
            if channel.delivered:
                break
            await asyncio.sleep(0.005)
        pending = channel.delivered[0]
        await registry.resolve(pending.id, approved=True, by_user_id=1)
        return await task

    outcome = asyncio.run(asyncio.wait_for(scenario(), timeout=2))
    assert outcome == {"outcome": {"outcome": "selected", "optionId": "allow"}}


def test_callback_cancels_on_denial(registry):
    channel = _RecordingChannel()
    cb = build_permission_callback("tg:1", 1, [channel], timeout_seconds=30)

    async def scenario():
        task = asyncio.create_task(cb(DANGEROUS, OPTIONS))
        for _ in range(100):
            if channel.delivered:
                break
            await asyncio.sleep(0.005)
        await registry.resolve(channel.delivered[0].id, approved=False, by_user_id=1)
        return await task

    assert asyncio.run(asyncio.wait_for(scenario(), timeout=2)) == CANCELLED


def test_callback_cancels_on_timeout(registry):
    """TTL expiry cancels the tool call instead of letting it through."""
    channel = _RecordingChannel()
    cb = build_permission_callback("tg:1", 1, [channel], timeout_seconds=0)

    assert asyncio.run(asyncio.wait_for(cb(DANGEROUS, OPTIONS), timeout=2)) == CANCELLED


def test_dual_channel_first_wins(registry):
    """Both surfaces render it; whoever answers first decides."""
    telegram = _RecordingChannel()
    web = _RecordingChannel()
    cb = build_permission_callback("tg:1", 1, [telegram, web], timeout_seconds=30)

    async def scenario():
        task = asyncio.create_task(cb(DANGEROUS, OPTIONS))
        for _ in range(100):
            if telegram.delivered and web.delivered:
                break
            await asyncio.sleep(0.005)
        cid = telegram.delivered[0].id
        # Same entry reached both channels — one request, two renderings.
        assert web.delivered[0].id == cid
        web_first = await registry.resolve(cid, approved=True, by_user_id=1)
        telegram_late = await registry.resolve(cid, approved=False, by_user_id=1)
        return web_first, telegram_late, await task

    web_first, telegram_late, outcome = asyncio.run(
        asyncio.wait_for(scenario(), timeout=2)
    )
    assert web_first is True
    assert telegram_late is False  # the late tap is a no-op, not a reversal
    assert outcome == {"outcome": {"outcome": "selected", "optionId": "allow"}}


def test_broken_channel_does_not_swallow_request(registry):
    """One channel failing still leaves the request answerable on the other."""
    good = _RecordingChannel()
    cb = build_permission_callback(
        "tg:1", 1, [_BrokenChannel(), good], timeout_seconds=30
    )

    async def scenario():
        task = asyncio.create_task(cb(DANGEROUS, OPTIONS))
        for _ in range(100):
            if good.delivered:
                break
            await asyncio.sleep(0.005)
        await registry.resolve(good.delivered[0].id, approved=True, by_user_id=1)
        return await task

    outcome = asyncio.run(asyncio.wait_for(scenario(), timeout=2))
    assert outcome == {"outcome": {"outcome": "selected", "optionId": "allow"}}


# ── Teardown denies whatever the session was still asking (SEC-094) ──


def test_destroying_a_session_denies_its_pending_confirmation(registry, monkeypatch):
    """Teardown answers the approval nobody is left to act on."""
    from condor.runtime import sessions as sessions_module
    from condor.runtime.keys import SessionKey

    key = SessionKey.telegram(42)
    install_session(monkeypatch, key)
    pending = registry.register(str(key), 1, "sell everything", DANGEROUS, OPTIONS, 300)
    other = registry.register("tg:99", 1, "unrelated", DANGEROUS, OPTIONS, 300)

    assert asyncio.run(sessions_module._destroy_session_internal(key)) is True

    assert pending.status is ConfirmationStatus.DENIED
    assert pending._event.is_set()
    # Scoped to the session that died — another chat's approval is untouched.
    assert other.status is ConfirmationStatus.PENDING


def test_respawn_strands_no_tappable_approval(registry, monkeypatch):
    """Switching agent under the same key cancels the call and expires the tap.

    The switch destroys and recreates ``tg:42``, so the old identity's request
    must come back CANCELLED and its Approve button must no longer resolve —
    which is what makes Telegram answer "Request expired." instead of
    "Approved." for a call that would now run as whoever is bound.
    """
    from condor.runtime import sessions as sessions_module
    from condor.runtime.keys import SessionKey

    key = SessionKey.telegram(42)
    install_session(monkeypatch, key)
    channel = _RecordingChannel()
    cb = build_permission_callback(str(key), 1, [channel], timeout_seconds=300)

    async def scenario():
        task = asyncio.create_task(cb(DANGEROUS, OPTIONS))
        for _ in range(100):
            if channel.delivered:
                break
            await asyncio.sleep(0.005)
        await sessions_module._destroy_session_internal(key)
        outcome = await task
        # The stale tap arrives after the switch.
        late_tap = await registry.resolve(
            channel.delivered[0].id, approved=True, by_user_id=1
        )
        return outcome, late_tap

    outcome, late_tap = asyncio.run(asyncio.wait_for(scenario(), timeout=3))
    assert outcome == CANCELLED
    assert late_tap is False


def test_reaping_a_dead_session_denies_its_pending_confirmation(registry, monkeypatch):
    """Process death is a teardown too, even though it skips the destroy path."""
    from condor.runtime import sessions as sessions_module
    from condor.runtime.keys import SessionKey

    key = SessionKey.telegram(7)
    install_session(monkeypatch, key, alive=False)
    pending = registry.register(str(key), 1, "buy the top", DANGEROUS, OPTIONS, 300)

    asyncio.run(sessions_module._enforce_session_budget(1))

    assert pending.status is ConfirmationStatus.DENIED
    assert str(key) not in sessions_module._sessions


def test_denying_twice_is_a_no_op(registry, monkeypatch):
    """The steer path and the teardown path cannot double-resolve one entry."""
    from condor.runtime import sessions as sessions_module
    from condor.runtime.keys import SessionKey

    key = SessionKey.telegram(42)
    install_session(monkeypatch, key)
    registry.register(str(key), 1, "sell everything", DANGEROUS, OPTIONS, 300)

    assert registry.deny_pending_for_session(str(key)) == 1
    assert asyncio.run(sessions_module._destroy_session_internal(key)) is True
    assert registry.deny_pending_for_session(str(key)) == 0


def test_confirmation_names_the_agent_and_server(registry, monkeypatch):
    """An approval says which identity raised it, and where it would act."""
    from condor.runtime.keys import SessionKey
    from condor.telegram.approvals import TelegramChannel

    key = SessionKey.telegram(42)
    install_session(
        monkeypatch,
        key,
        label="Executor Manager",
        agent_slug="executor_manager",
        server_name="hb-prod",
    )
    channel = _RecordingChannel()
    cb = build_permission_callback(str(key), 1, [channel], timeout_seconds=300)

    async def scenario():
        task = asyncio.create_task(cb(DANGEROUS, OPTIONS))
        for _ in range(100):
            if channel.delivered:
                break
            await asyncio.sleep(0.005)
        pending = channel.delivered[0]
        await registry.resolve(pending.id, approved=False, by_user_id=1)
        await task
        return pending

    pending = asyncio.run(asyncio.wait_for(scenario(), timeout=3))
    assert pending.origin == "Executor Manager on hb-prod"
    assert pending.to_wire()["origin"] == "Executor Manager on hb-prod"

    sent = {}

    class _Bot:
        async def send_message(self, **kwargs):
            sent.update(kwargs)

    pending = registry.register(
        str(key), 1, "buy", DANGEROUS, OPTIONS, origin=pending.origin
    )
    asyncio.run(TelegramChannel(_Bot(), 42).deliver(pending))
    assert "Executor Manager on hb-prod" in plain(sent["text"])


def test_request_cancellation_denies_only_its_approvals_and_rejects_late_requests(
    registry, monkeypatch
):
    from condor.runtime import turns

    monkeypatch.setattr(turns, "_turns", {})
    queue = turns.state(1, "conversation")
    queue.pending.update(first="tg:1", later="tg:1")
    queue.active_key, queue.active_request = "tg:1", "first"
    first = registry.register("tg:1", 1, "first", DANGEROUS, OPTIONS)
    later = registry.register(
        "tg:1",
        1,
        "later",
        DANGEROUS,
        OPTIONS,
        conversation_id="conversation",
        request_id="later",
    )
    turns.cancel_request(1, "conversation", "first")
    assert registry.deny_pending_for_request(1, "conversation", "first") == 1
    assert first.status is ConfirmationStatus.DENIED
    assert later.status is ConfirmationStatus.PENDING
    late = registry.register("tg:1", 1, "late", DANGEROUS, OPTIONS)
    assert late.status is ConfirmationStatus.DENIED
    assert asyncio.run(build_permission_callback("tg:1", 1)(SAFE, OPTIONS)) == CANCELLED


@pytest.mark.parametrize("resolution", ["stop", "resolve", "timeout"])
def test_telegram_approval_keyboard_is_removed_when_resolution_precedes_send_receipt(
    registry, resolution
):
    from condor.telegram.approvals import TelegramChannel

    async def scenario():
        sending, receipt = asyncio.Event(), asyncio.Event()
        edited = []

        class Bot:
            async def send_message(self, **kwargs):
                sending.set()
                await receipt.wait()
                return SimpleNamespace(message_id=19)

            async def edit_message_reply_markup(self, **kwargs):
                edited.append(kwargs)
                return True

        pending = registry.register("tg:1", 1, "buy", DANGEROUS, OPTIONS)
        delivery = asyncio.create_task(TelegramChannel(Bot(), 1).deliver(pending))
        await sending.wait()
        if resolution == "stop":
            registry.deny_pending_for_session("tg:1")
        elif resolution == "resolve":
            await registry.resolve(pending.id, approved=True, by_user_id=1)
        else:
            pending.timeout_seconds = 0
            await registry.wait_for(pending.id)
        receipt.set()
        await delivery
        await registry.drain()
        assert edited == [{"chat_id": 1, "message_id": 19, "reply_markup": None}]

    asyncio.run(scenario())


def test_cancelled_permission_waiter_discards_its_approval(registry):
    async def scenario():
        delivered = asyncio.Event()

        class Channel:
            async def deliver(self, pending):
                self.pending = pending
                delivered.set()

        channel = Channel()
        task = asyncio.create_task(
            build_permission_callback("tg:1", 1, [channel])(DANGEROUS, OPTIONS)
        )
        await delivered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert registry.get(channel.pending.id) is None
        assert channel.pending.status is ConfirmationStatus.DENIED

    asyncio.run(scenario())


def test_origin_is_empty_when_the_session_is_unknown(registry):
    """Attribution is best-effort: no session, no claim about who is asking."""
    channel = _RecordingChannel()
    cb = build_permission_callback("tg:31337", 1, [channel], timeout_seconds=300)

    async def scenario():
        task = asyncio.create_task(cb(DANGEROUS, OPTIONS))
        for _ in range(100):
            if channel.delivered:
                break
            await asyncio.sleep(0.005)
        pending = channel.delivered[0]
        await registry.resolve(pending.id, approved=False, by_user_id=1)
        await task
        return pending

    pending = asyncio.run(asyncio.wait_for(scenario(), timeout=3))
    assert pending.origin == ""
