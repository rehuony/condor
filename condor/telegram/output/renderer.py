"""Stream an agent turn as native rich-message drafts, then one final message.

Private chats get ``send_rich_message_draft`` while the model runs (native stop,
~1 s refresh so the bubble does not vanish) and ``send_rich_message`` at the
end. Groups and any chat that cannot take a draft skip progress and only send
the final. See ``docs/telegram-reply-delivery.md``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field

from aiogram.types import InputRichMessage

from condor.runtime.events import EventType, RuntimeEvent
from condor.telegram.errors import OperationResult, RateLimited, classify, retry_delay
from condor.telegram.format import RICH_MAX_LEN
from condor.telegram.format import split_text as _split_text
from condor.telegram.output.lease import OutputLease
from condor.telegram.output.runs import (
    RunTarget,
    allocate_draft_id,
    register_run,
    unregister_run,
)
from condor.telegram.transport import message_id_of, telegram_call
from condor.telegram.types import NO_LINK_PREVIEW

log = logging.getLogger(__name__)

DRAFT_INTERVAL = 1.0
QUEUED_LABEL = "⏳ Queued — waiting for the current answer to finish."
STOPPED_LABEL = "⏹ Stopped generating."
TERMINAL_TOOLS = {
    "completed",
    "failed",
    "blocked",
    "denied",
    "cancelled",
    "disconnected",
}


@dataclass
class DeliveryResult:
    status: str = "pending"  # complete, failed, partial, delivery_unknown
    message_ids: list[int] = field(default_factory=list)
    payloads: list[tuple[str, str | None]] = field(default_factory=list)
    confirmed: int = 0


class RichReplyStreamer:
    """One run owns its draft id, worker, and final delivery task."""

    def __init__(
        self,
        bot,
        chat_id: int,
        message_id: int | None = None,
        prefix: str = "",
        *,
        user_id: int | None = None,
        conversation_id: str = "",
        request_id: str = "",
        input_message_id: int | None = None,
        agent_slug: str = "",
        private: bool | None = None,
    ):
        self._bot, self._chat_id = bot, chat_id
        self._status_message_id = message_id
        self._prefix = prefix
        self._buffer = ""
        self._chat_ref = hashlib.sha256(str(chat_id).encode()).hexdigest()[:12]
        self._uses_draft = chat_id > 0 if private is None else private
        self.draft_id = allocate_draft_id() if self._uses_draft else 0
        self.target = RunTarget(
            chat_id,
            user_id,
            conversation_id,
            request_id,
            agent_slug,
            draft_id=self.draft_id,
        )
        self.target.nudge = self.nudge
        self.input_message_id = input_message_id
        self.state = "queued"
        self.tools: dict[str, str] = {}
        self._started = False
        self._queued = False
        self._done = False
        self._closed = False
        self._failed = False
        self._error_code = ""
        self._stop_reason = ""
        self._wake = asyncio.Event()
        self._interrupted = False
        self._edit_task: asyncio.Task | None = None
        self._final_task: asyncio.Task | None = None
        self._lease: OutputLease | None = None
        self._next_edit = 0.0
        self._last_status = ""
        self._progress_disabled = False
        self._rate_rejections = 0
        self._plain_only = False
        self.delivery = DeliveryResult()

    def nudge(self) -> None:
        """Another bot message hid this draft; resend it on the next tick."""
        self._interrupted = True
        self._wake.set()

    def _reserve_output(self):
        if self._lease is None:
            self._lease = OutputLease(self._chat_id)

    async def process_event(self, event: RuntimeEvent) -> None:
        if self._closed or self._done or self._final_task is not None:
            return
        if event.session_key and event.session_key != f"tg:{self._chat_id}":
            return
        if event.type == EventType.ACCEPTED:
            self.target.request_id = event.field("request_id", self.target.request_id)
            self.target.conversation_id = event.field(
                "conversation_id", self.target.conversation_id
            )
            self.target.agent_slug = event.field("agent_slug", self.target.agent_slug)
            return
        if event.type == EventType.QUEUED:
            self._queued = True
            self.state = "queued"
            self._wake.set()
            return
        if event.type == EventType.DONE:
            self._stop_reason = event.stop_reason
            self._done = True
            unregister_run(self.target)
            self._reserve_output()
        else:
            self._queued = False
            self._started = True
            self._reserve_output()
            register_run(self.target)
            if event.type == EventType.TEXT:
                if event.field("is_error", False):
                    self._failed = True
                else:
                    self._buffer += event.text
            elif event.type in (EventType.TOOL_CALL, EventType.TOOL_UPDATE):
                key = str(event.field("tool_call_id") or "")
                self.tools[key] = str(
                    event.field("status") or self.tools.get(key, "pending")
                )
            elif event.type == EventType.ERROR:
                self._failed = True
                self._error_code = str(event.field("code", ""))
        self._wake.set()

    def start_edit_loop(self) -> asyncio.Task:
        if self._edit_task is None:
            self._edit_task = asyncio.create_task(
                self._draft_loop(), name="telegram-agent-progress"
            )
        return self._edit_task

    def _waiting_approval(self) -> bool:
        return any(
            getattr(p.status, "value", p.status) == "pending"
            for p in self.target.approvals
        )

    def _status(self) -> str:
        if self._queued:
            self.state = "queued"
            return QUEUED_LABEL
        if self._waiting_approval():
            self.state = "waiting_approval"
            return "Waiting for your approval."
        self.state = "failed" if self._failed else "running"
        if self._failed:
            return "The turn encountered an error. Finishing delivery…"
        if any(status not in TERMINAL_TOOLS for status in self.tools.values()):
            return "Working on an operation…"
        if self.tools and all(status == "completed" for status in self.tools.values()):
            return "Operations returned. Preparing the response…"
        if self.tools:
            return "An operation did not complete successfully. Preparing the response…"
        return "Processing…"

    def _peer_owns_chat(self) -> bool:
        from condor.telegram.output.runs import _runs

        return any(
            target is not self.target and target.chat_id == self._chat_id
            for target in _runs.values()
        )

    async def _draft_loop(self):
        try:
            while not self._done and not self._closed:
                self._wake.clear()
                if (self._queued or self._started) and self._uses_draft:
                    if self._queued and self._peer_owns_chat():
                        pass
                    else:
                        self._reserve_output()
                        await self._lease.acquire()
                        if self._done or self._closed:
                            break
                        now = asyncio.get_running_loop().time()
                        if self._interrupted or now >= self._next_edit:
                            await self._flush_draft()
                            self._interrupted = False
                            self._next_edit = now + DRAFT_INTERVAL
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=0.25)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log_failure("progress", exc)

    async def stop_edit_loop(self):
        self._closed = True
        unregister_run(self.target)
        if self._edit_task and not self._edit_task.done():
            self._edit_task.cancel()
            await asyncio.gather(self._edit_task, return_exceptions=True)
        if (
            self._final_task is not None
            and self._final_task is not asyncio.current_task()
            and not self._final_task.done()
        ):
            self._final_task.cancel()
            await asyncio.gather(self._final_task, return_exceptions=True)
        if self._lease is not None and (
            self._final_task is None or self._final_task.done()
        ):
            self._lease.release()

    def _build_text(self, final: bool) -> str:
        if not final:
            status = self._status()
            if self._buffer.strip() and self.state not in {
                "queued",
                "waiting_approval",
            }:
                return self._buffer
            return status
        body = self._buffer
        if self._failed or self._stop_reason not in {"end_turn", "cancelled"}:
            self.state = "failed"
            reason = {
                "timeout": "The agent timed out.",
                "disconnected": "The agent disconnected.",
                "access_denied": "Your access changed; the agent did not execute this request.",
            }.get(self._stop_reason, "Could not finish this turn.")
            if self._error_code == "images_unsupported":
                reason = (
                    "This model cannot read images. Switch the model, or "
                    "describe what is in the picture."
                )
            body = f"{reason}\n\nPartial result:\n{body}" if body else reason
        elif self._stop_reason == "cancelled":
            self.state = "cancelled"
            body = (
                f"{STOPPED_LABEL}\n\nPartial result:\n{body}"
                if body
                else "Stopped before answering."
            )
        else:
            self.state = "completed"
            if not body.strip():
                body = "No response was returned."
                if self.tools:
                    body = (
                        "No answer was returned. Check the saved conversation "
                        "for operation results."
                    )
        return body

    def _pages(self, body, literal_prefix=""):
        source = "\n\n".join(part for part in (literal_prefix, body) if part)
        try:
            mode = None if self._plain_only else "rich"
            return [(chunk, mode) for chunk in _split_text(source, RICH_MAX_LEN)]
        except Exception as exc:
            log.warning("Telegram rendering failed error_type=%s", type(exc).__name__)
            return [(chunk, None) for chunk in _split_text(source)]

    async def _flush(self, final: bool = False):
        if final:
            return await self.finalize()
        return await self._flush_draft()

    async def _flush_draft(self) -> None:
        if (
            self._done
            or self._closed
            or self._progress_disabled
            or not self._uses_draft
            or (self._queued and self._peer_owns_chat())
        ):
            return
        if self.target.stopped:
            self._progress_disabled = True
            return
        text = self._build_text(False)
        self._last_status = self._status()
        if len(text) > RICH_MAX_LEN:
            text = text[:RICH_MAX_LEN]
        try:
            result = await self._draft_io(text)
        except RateLimited as exc:
            self._rate_rejections += 1
            self._next_edit = asyncio.get_running_loop().time() + max(
                DRAFT_INTERVAL, retry_delay(exc)
            )
            if self._rate_rejections >= 3:
                self._progress_disabled = True
            return
        if result.status != "confirmed":
            self._progress_disabled = True
            return
        self._rate_rejections = 0
        await self._drop_status_message()

    async def _drop_status_message(self) -> None:
        if self._status_message_id is None:
            return
        message_id, self._status_message_id = self._status_message_id, None
        try:
            await telegram_call(
                self._bot.delete_message,
                chat_id=self._chat_id,
                message_id=message_id,
                retry_rate_limits=False,
            )
        except Exception as exc:
            self._log_failure("cleanup", exc)

    async def finalize(self) -> DeliveryResult:
        if self._final_task is None:
            self._final_task = asyncio.create_task(
                self._finalize(), name="telegram-agent-final"
            )
        return await asyncio.shield(self._final_task)

    async def _finalize(self):
        self._done = True
        self._reserve_output()
        try:
            await self.stop_edit_loop()
            self.state = "finalizing"
            self.delivery.payloads = self._pages(self._build_text(True), self._prefix)
            await self._lease.acquire()
            await self._drop_status_message()
            while self.delivery.confirmed < len(self.delivery.payloads):
                index = self.delivery.confirmed
                text, mode = self.delivery.payloads[index]
                result = await self._final_io(text, mode, quiet=index > 0)
                if result.status == "failed" and result.reason == "connect":
                    result = await self._final_io(text, mode, quiet=index > 0)
                if result.reason == "too_long" and len(text) > 1:
                    self.delivery.payloads[index : index + 1] = [
                        (chunk, mode)
                        for chunk in _split_text(text, max(1, len(text) // 2))
                    ]
                    continue
                if result.reason == "parse" and mode:
                    self.delivery.payloads[index : index + 1] = [
                        (chunk, None) for chunk in _split_text(text)
                    ]
                    self._plain_only = True
                    continue
                if result.status != "confirmed":
                    self.delivery.status = (
                        "delivery_unknown"
                        if result.status == "unknown"
                        else ("partial" if index else "failed")
                    )
                    await self._retain_progress()
                    return self.delivery
                self.delivery.message_ids.append(result.message_id)
                self.delivery.confirmed += 1
            self.delivery.status = "complete"
            return self.delivery
        except asyncio.CancelledError:
            if self.delivery.status != "complete":
                self.delivery.status = "delivery_unknown"
            raise
        finally:
            self._lease.release()
            unregister_run(self.target)

    async def _retain_progress(self):
        log.warning(
            "Telegram final delivery status=%s chat_ref=%s confirmed=%d total=%d",
            self.delivery.status,
            self._chat_ref,
            self.delivery.confirmed,
            len(self.delivery.payloads),
        )

    async def _draft_io(self, markdown: str) -> OperationResult:
        try:
            result = await telegram_call(
                self._bot.send_rich_message_draft,
                chat_id=self._chat_id,
                draft_id=self.draft_id,
                rich_message=InputRichMessage(markdown=markdown),
                can_stop=True,
                keep_on_stop=True,
                retry_rate_limits=False,
            )
            if result is True or result is False:
                return OperationResult("confirmed")
            return OperationResult("confirmed")
        except RateLimited:
            raise
        except Exception as exc:
            outcome = classify(exc, operation="send", parse_mode="Markdown")
            if outcome.reason not in {"parse"}:
                self._log_failure("draft", exc)
            return outcome

    async def _final_io(
        self, text: str, mode: str | None, *, quiet: bool
    ) -> OperationResult:
        try:
            if mode == "rich":
                result = await telegram_call(
                    self._bot.send_rich_message,
                    chat_id=self._chat_id,
                    rich_message=InputRichMessage(markdown=text),
                    disable_notification=quiet,
                )
            else:
                result = await telegram_call(
                    self._bot.send_message,
                    chat_id=self._chat_id,
                    text=text,
                    disable_notification=quiet,
                    link_preview_options=NO_LINK_PREVIEW,
                )
            confirmed_id = message_id_of(result)
            if confirmed_id is None:
                return OperationResult("unknown")
            return OperationResult("confirmed", confirmed_id)
        except RateLimited:
            return OperationResult("failed", reason="rate_limit")
        except Exception as exc:
            error = str(exc).lower()
            if "too_long" in error or "too long" in error:
                return OperationResult("failed", reason="too_long")
            outcome = classify(
                exc, operation="send", parse_mode="Markdown" if mode == "rich" else None
            )
            if outcome.reason not in {"parse"}:
                self._log_failure("send", exc)
            return outcome

    def _log_failure(self, operation, exc):
        log.warning(
            "Telegram delivery failed operation=%s chat_ref=%s error_type=%s",
            operation,
            self._chat_ref,
            type(exc).__name__,
        )


# The name tests and handlers still patch.
TelegramStreamer = RichReplyStreamer
