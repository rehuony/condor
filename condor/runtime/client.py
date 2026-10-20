"""Transport-agnostic entry point to the session runtime.

Every caller outside ``condor/runtime/`` goes through this module — never
through ``condor.runtime.sessions`` — so that swapping the in-process registry
for an HTTP client is a config flip rather than a rewrite.

``CONDOR_RUNTIME_MODE`` selects the transport:

* ``local`` (default) — thin await-through to ``condor.runtime.sessions``.
* ``http`` — reserved for the runtime process split; not implemented yet.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from contextlib import aclosing
from typing import AsyncIterator, Callable, Literal

from condor.runtime import conversations, secrets, turns
from condor.runtime.events import EventType, RuntimeEvent
from condor.runtime.keys import SessionKey
from condor.runtime.models import PromptRequest, SessionInfo, SessionSpec
from condor.runtime.timeouts import TIMEOUTS

log = logging.getLogger(__name__)

# What a caller wants done when the session is already answering something.
#
# ``reject``  — do not opt in. The session's stuck-prompt deadline applies, so
#               a genuinely busy session fails fast, exactly as before.
# ``queue``   — wait for the turn ahead to finish, then run. The session lock is
#               FIFO, so several queued messages are answered in order.
# ``steer``   — stop the turn ahead first, then run. The session is long-lived,
#               so the new turn keeps everything the interrupted one had worked
#               out; this is "interrupt and redirect", not a restart.
BusyPolicy = Literal["reject", "queue", "steer"]

LOCAL = "local"
HTTP = "http"

MODE = os.environ.get("CONDOR_RUNTIME_MODE", LOCAL)

# Request ids whose durable acceptance is in progress, keyed by owner. The
# transcript write runs off the event loop, so between ``has_request`` saying
# "new" and the index saying "seen" there is a window another arrival of the
# same id could slip through; registering here synchronously closes it.
_in_flight: set[tuple[int, str]] = set()


def _local():
    """Return the local registry module.

    Imported lazily: ``condor.runtime.sessions`` pulls in handler helpers that
    in turn import this module, so a module-level import would be circular.
    """
    if MODE != LOCAL:
        raise NotImplementedError(
            f"CONDOR_RUNTIME_MODE={MODE!r} is not implemented yet; only "
            f"{LOCAL!r} is supported."
        )
    from condor.runtime import sessions

    return sessions


def bound_agent_context(bound, user_id: int, platform: str, agent_key: str = "") -> str:
    """Opening context for a chat bound to a specialist Agent."""
    return _local().bound_agent_context(bound, user_id, platform, agent_key)


async def create_session(
    spec: SessionSpec,
    *,
    permission_callback=None,
    user_data: dict | None = None,
) -> SessionInfo:
    """Provision (or reuse) the session described by ``spec``."""
    session = await _local().get_or_create_session(
        spec, permission_callback=permission_callback, user_data=user_data
    )
    return session.info()


async def get_info(key: SessionKey) -> SessionInfo | None:
    """Serializable view of a session, or None when it does not exist."""
    session = _local().get_session(key)
    return session.info() if session else None


async def conversation_for_session(session_key: str) -> str:
    """Resolve a session key to the conversation currently on that session.

    The one place that knows how to turn a raw key into a conversation id, so
    every producer of async work that reports back to its origin — delegations,
    notifications, routine runs — agrees on what "the conversation behind this
    call" means.

    Answered on demand rather than cached at spawn because the conversation id
    does not exist when the MCP subprocess starts (``get_or_create_session``
    mints it after the client is up) — by call time it is settled.

    A missing, malformed or dead key is not an error: it means "no conversation
    behind this call", which is the truth for a tick- or
    scheduler-started run and for anything predating this provenance.
    """
    if not session_key:
        return ""
    try:
        info = await get_info(SessionKey.parse(session_key))
        return info.conversation_id if info else ""
    except Exception:
        log.debug("Could not resolve session key %r", session_key, exc_info=True)
        return ""


async def list_sessions(
    user_id: int | None = None, *, include_detached: bool = False
) -> list[SessionInfo]:
    """List sessions, optionally filtered by owning user.

    ``include_detached`` adds the slots whose subprocess was reaped but whose
    conversation is still there, reported with ``alive`` false. That is the
    roster a frontend rebuilds its tab strip from; the default stays "what is
    running right now".
    """
    return _local().list_sessions(user_id, include_detached=include_detached)


async def destroy(key: SessionKey) -> bool:
    """Destroy a session. Returns True if one existed."""
    return await _local().destroy_session(key)


async def destroy_all() -> None:
    """Destroy every session. Called on shutdown."""
    await _local().destroy_all_sessions()


def _deny_pending_confirmations(raw_key: str) -> None:
    """Resolve as denied whatever the interrupted turn was still asking about.

    Imported lazily for the same reason ``_local()`` is: the confirmation
    registry pulls in handler helpers through its permission callback.
    """
    from condor.runtime.confirmations import get_registry

    try:
        denied = get_registry().deny_pending_for_session(raw_key)
    except Exception:  # noqa: BLE001 - never let cleanup break the new turn
        log.warning("Could not deny pending confirmations for %s", raw_key)
        return
    if denied:
        log.info("Denied %d pending confirmation(s) on steering %s", denied, raw_key)


def _attach_usage(done: RuntimeEvent, session, recorder) -> None:
    """Charge this turn what the client spent since the last one (FEAT-120).

    Taken here, the fifth concern this funnel owns on the same argument as the
    other four: every chat turn on every surface ends here, so the clients only
    count and never have to know what a turn is — and none of the half-dozen
    places that synthesize a ``PromptDone`` has to learn about usage. Riding the
    DONE event rather than a new one keeps the wire shape: consumers gain a key.
    """
    turn_usage = session.take_usage()
    done.data["usage"] = turn_usage.to_dict()
    recorder.note_usage(turn_usage)


async def _settle_reservation(acquire: asyncio.Task) -> bool:
    """Cancel a pending lock reservation; True when it had already acquired."""
    if not acquire.done():
        acquire.cancel()
        try:
            await acquire
        except asyncio.CancelledError:
            pass
    return bool(
        not acquire.cancelled() and acquire.exception() is None and acquire.result()
    )


async def _abandon_reservation(queue, acquire: asyncio.Task) -> None:
    """Give up a reservation made for a turn that will never run."""
    if await _settle_reservation(acquire):
        queue.lock.release()


async def prompt(
    key: SessionKey,
    req: PromptRequest,
    *,
    on_busy: BusyPolicy = "reject",
    can_execute: Callable[[], bool] | None = None,
) -> AsyncIterator[RuntimeEvent]:
    """Accept durably, serialize by conversation, then stream one AI turn.

    Delivery is a consumer of this generator, never a condition of acceptance.
    The conversation queue spans transports; each model client receives any
    completed history it has not seen before it handles the next input.
    ``can_execute`` rechecks surface authorization after queueing and session
    refresh, immediately before execution. Refused input remains in the audit
    transcript but is excluded from future model context.
    """
    raw_key = str(key)
    session = _local().get_session(key)
    if session is None:
        yield RuntimeEvent.error(f"No session for {raw_key}", session_key=raw_key)
        yield RuntimeEvent.done("no_session", session_key=raw_key)
        return

    owner, conversation_id = session.user_id, session.conversation_id
    flight_key: tuple[int, str] | None = None
    if req.request_id and owner is not None:
        flight_key = (owner, req.request_id)
        if flight_key in _in_flight or conversations.has_request(owner, req.request_id):
            yield RuntimeEvent.done("duplicate", session_key=raw_key)
            return
        _in_flight.add(flight_key)

    clean, _ = secrets.redact(req.text)
    view, _ = secrets.redact(req.view_context)
    wire = f"{view}\n\n{clean}" if view.strip() else clean
    request_id = req.request_id or uuid.uuid4().hex
    queue_id = conversation_id or raw_key
    queue = turns.state(owner, queue_id)
    queue.pending[request_id] = raw_key
    recorder = conversations.Recorder(
        owner,
        conversation_id,
        clean,
        agent_key=session.agent_key,
        agent_slug=session.agent_slug,
        user_kind=req.user_kind,
        request_id=request_id,
        attachments=[
            {"id": image.id, "mime": image.mime, "bytes": len(image.data)}
            for image in req.images
            if image.id
        ],
    )
    # Reserve FIFO position before the first await: the durable write below
    # runs in a thread, and two inputs whose writes finish out of order must
    # still be answered in the order they were sent.
    was_busy = len(queue.pending) > 1 or session.is_busy
    acquire = asyncio.create_task(queue.lock.acquire())
    try:
        # Off the loop: the fsync'd transcript append would otherwise stall
        # the Telegram poller and the dashboard alike. Nothing is yielded to
        # the consumer until it has landed, so acceptance stays atomic from
        # the caller's point of view. ``accept_lock`` is FIFO, so the
        # transcript keeps the arrival order the execution queue has.
        async with queue.accept_lock:
            await asyncio.to_thread(recorder.accept)
    except Exception:
        # Reservation first: ``release`` only forgets an idle queue, and the
        # reservation may already hold its lock.
        await _abandon_reservation(queue, acquire)
        turns.release(owner, queue_id, request_id)
        log.exception("Could not save incoming message for session %s", raw_key)
        yield RuntimeEvent.error(
            "Could not save your message. The agent was not run; please retry.",
            session_key=raw_key,
        )
        yield RuntimeEvent.done("error", session_key=raw_key)
        return
    finally:
        # Accepted or failed, the index (or its absence) now answers for the
        # id; the in-flight guard was only for the write's duration.
        if flight_key is not None:
            _in_flight.discard(flight_key)

    locked = False
    executed = False
    outcome = "aborted"
    try:
        turns.publish(owner, conversation_id, raw_key, "accepted")
        accepted = RuntimeEvent.accepted(session_key=raw_key)
        accepted.data.update(
            request_id=request_id,
            conversation_id=conversation_id,
            agent_slug=session.agent_slug,
        )
        yield accepted
        if was_busy:
            if on_busy == "steer":
                active = queue.active_key or (raw_key if session.is_busy else "")
                if active:
                    _deny_pending_confirmations(active)
                    await abort(SessionKey.parse(active))
            if on_busy != "reject":
                yield RuntimeEvent.queued(session_key=raw_key)
        timeout = (
            TIMEOUTS.prompt_queue
            if on_busy != "reject"
            else _local().PROMPT_LOCK_TIMEOUT
        )
        try:
            await asyncio.wait_for(asyncio.shield(acquire), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                "Agent is busy and not responding. Your message was saved; please retry when it is idle."
            ) from exc
        locked = True
        queue.active_key = raw_key
        queue.active_request = request_id

        if request_id in queue.cancelled:
            outcome = "cancelled"
            recorder.observe(
                RuntimeEvent.error(
                    "Request cancelled before execution.", session_key=raw_key
                )
            )
            yield RuntimeEvent.done("cancelled", session_key=raw_key)
            return

        # A queued request may outlive a model switch or session teardown.
        session = _local().get_session(key)
        if (
            session is None
            or session.conversation_id != conversation_id
            or session.user_id != owner
        ):
            raise RuntimeError(
                "The session changed while your message was waiting. Your message was saved; reopen this conversation to continue."
            )
        reloaded = await _local().refresh_if_stale(key)
        if reloaded:
            session = _local().get_session(key)
            if session is None:
                raise RuntimeError("The session could not be reloaded.")
            if session.conversation_id != conversation_id or session.user_id != owner:
                raise RuntimeError(
                    "The session changed while your message was waiting. Your message was saved; reopen this conversation to continue."
                )
            yield RuntimeEvent.reloaded(reloaded, session_key=raw_key)
            conversations.record_system(
                owner,
                conversation_id,
                f"Reloaded to apply configuration changes ({', '.join(reloaded)})",
                kind="reload",
            )
        recorder.bind_agent(session.agent_key, session.agent_slug)
        if request_id in queue.cancelled:
            outcome = "cancelled"
            recorder.observe(
                RuntimeEvent.error(
                    "Request cancelled before execution.", session_key=raw_key
                )
            )
            yield RuntimeEvent.done("cancelled", session_key=raw_key)
            return
        if can_execute is not None:
            try:
                allowed = can_execute()
            except Exception as exc:
                # A failing access lookup is a refusal, never permission. Its
                # exception may contain credentials, so log only the type.
                log.warning(
                    "Prompt authorization failed error_type=%s", type(exc).__name__
                )
                allowed = False
            if not allowed:
                outcome = "error"
                failure = RuntimeEvent.error(
                    "Your access changed. This message was saved but was not sent to the agent.",
                    session_key=raw_key,
                )
                done = RuntimeEvent.done("access_denied", session_key=raw_key)
                recorder.observe(failure)
                # Mark the durable result before yielding: closing the consumer
                # after ERROR must still exclude the refused input from replay.
                recorder.observe(done)
                queue.active_request = ""
                yield failure
                yield done
                return
        if req.images and not getattr(session.client, "accepts_images", True):
            outcome = "error"
            failure = RuntimeEvent.error(
                f"{session.label} cannot read images. Switch the model, or describe what is in the picture.",
                session_key=raw_key,
            )
            failure.data["code"] = "images_unsupported"
            done = RuntimeEvent.done("error", session_key=raw_key)
            recorder.observe(failure)
            recorder.observe(done)
            queue.active_request = ""
            yield failure
            yield done
            return

        revision = _local().conversation_revision(owner, conversation_id)
        if conversation_id and getattr(session, "history_revision", 0) != revision:
            replay = conversations.replay_context(
                owner,
                conversation_id,
                exclude_request_ids=turns.pending_requests(owner, conversation_id),
            )
            if replay:
                wire = (
                    "Updated conversation history from the shared transcript. "
                    "Use this as context; do not execute earlier requests again.\n\n"
                    f"{replay}\n\nCurrent user message:\n{wire}"
                )
        executed = True
        async with aclosing(
            session.prompt_stream(
                wire,
                images=req.images or None,
                lock_timeout=TIMEOUTS.prompt_queue if on_busy != "reject" else None,
            )
        ) as stream:
            async for event in stream:
                runtime_event = RuntimeEvent.from_acp(event, session_key=raw_key)
                if runtime_event.type is EventType.DONE:
                    outcome = "done"
                    _attach_usage(runtime_event, session, recorder)
                    # A delivered terminal event is no longer stoppable, even
                    # while its consumer holds the generator open to finalize UI.
                    queue.active_request = ""
                recorder.observe(runtime_event)
                yield runtime_event
    except Exception as exc:
        outcome = "error"
        log.exception("Prompt failed for session %s", raw_key)
        failure = RuntimeEvent.error(str(exc), session_key=raw_key)
        recorder.observe(failure)
        yield failure
        done = RuntimeEvent.done("error", session_key=raw_key)
        recorder.observe(done)
        if queue.active_request == request_id:
            queue.active_request = ""
        if executed:
            _attach_usage(done, session, recorder)
        yield done
    finally:
        owns_lock = locked or await _settle_reservation(acquire)
        if executed:
            recorder.note_usage(session.take_usage())
        if outcome == "aborted":
            recorder.observe(
                RuntimeEvent.error(
                    "Response interrupted. The saved request will not be executed again automatically.",
                    session_key=raw_key,
                )
            )
            recorder.observe(RuntimeEvent.done("cancelled", session_key=raw_key))
        recorder.flush()
        if owns_lock:
            queue.active_key = ""
            queue.active_request = ""
            queue.lock.release()
        turns.release(owner, queue_id, request_id)
        if executed:
            # Notes or cancelled queued inputs may have landed while the model
            # ran. They were not read by this client and must be replayed next time.
            session.history_revision = revision + recorder.written_turns
        turns.publish(owner, conversation_id, raw_key, "completed")


async def prompt_once(key: SessionKey, text: str) -> str:
    """Send a prompt and wait for the whole reply.

    For turns the user never sees streamed — asking the agent to summarize
    itself for /compact, and re-injecting that summary into the fresh session.
    Raises KeyError when the session is gone, since every caller here has just
    checked that it exists.

    Redacted for the same reason ``prompt`` is (FEAT-056), and not because the
    text is usually a template: /compact's custom instructions are a *user's*
    words, wrapped in a template and sent from here, so without this line the
    funnel had a second door — Telegram deleted the message carrying a pasted
    phrase and told the user "it was not sent to the agent" while this call
    sent it. ``findings`` are dropped on purpose: the surface that took the
    text already scanned its own copy and owns the notice.
    """
    session = _local().get_session(key)
    if session is None:
        raise KeyError(f"No session for {key}")
    text, _ = secrets.redact(text)
    return await session.client.prompt(text)


async def abort(key: SessionKey) -> bool:
    """Abort the in-flight prompt. Returns True if a session existed."""
    session = _local().get_session(key)
    if session is None:
        if key.surface != "web" or not key.owner.isdecimal() or not key.slot:
            return False
        owner, conversation_id = int(key.owner), key.slot
    else:
        owner, conversation_id = session.user_id, session.conversation_id or str(key)
    active = turns.cancel_current(owner, conversation_id)
    if active:
        session = _local().get_session(SessionKey.parse(active))
        if session is not None and (
            session.user_id != owner
            or (session.conversation_id or str(session.key)) != conversation_id
        ):
            # Telegram reuses its session key when switching conversations.
            # Stop belongs to the old turn, never the new session at that key.
            return True
    if session is None:
        return bool(active)
    _deny_pending_confirmations(str(session.key))
    if session.is_busy or not active:
        await session.abort()
    return True


async def abort_turn(user_id: int, conversation_id: str, request_id: str) -> bool:
    """Stop exactly the active request in its original user's conversation.

    Returns False for queued, finished, already stopped or unknown requests.
    Capture the concrete session before awaiting cancellation: Telegram can
    reuse its key for another conversation while an old response is on screen.
    A missing or rebound session still leaves the original turn marked stopped,
    but its replacement's confirmations and provider are never touched.
    """
    active = turns.cancel_current(user_id, conversation_id, request_id=request_id)
    if not active:
        return False
    session = _local().get_session(SessionKey.parse(active))
    if session is not None and (
        session.user_id == user_id
        and (session.conversation_id or str(session.key)) == conversation_id
    ):
        _deny_pending_confirmations(str(session.key))
        if session.is_busy:
            await session.abort()
    return True


# The FEAT-008 ``get_live()`` escape hatch is gone: prompting, context
# injection and liveness checks all go through the functions above, so no
# caller outside this package holds a live session object any more.
