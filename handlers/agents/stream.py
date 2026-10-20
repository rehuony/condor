"""Quiet progress and acknowledged final delivery for Telegram agent turns.

TEXT has no authoritative commentary/final boundary in either current provider.
Keep every TEXT chunk in order; presentation never rewrites model history.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field

import httpx
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter

from condor.runtime.events import EventType, RuntimeEvent
from condor.telegram_output import OutputLease, RunTarget, register_run, unregister_run
from condor.telegram_text import (
    MAX_MESSAGE_LEN,
    plain_text,
    render_markdown,
)
from condor.telegram_text import split_text as _split_text
from condor.telegram_text import (
    telegram_call,
)

from .menu import stop_generating_keyboard

log = logging.getLogger(__name__)
STATUS_INTERVAL = 5.0
TYPING_INTERVAL = 4.0
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


@dataclass(frozen=True)
class OperationResult:
    status: str  # confirmed, failed, unknown
    message_id: int | None = None
    reason: str = ""


@dataclass
class DeliveryResult:
    status: str = "pending"  # complete, failed, partial, delivery_unknown
    message_ids: list[int] = field(default_factory=list)
    payloads: list[tuple[str, str | None]] = field(default_factory=list)
    confirmed: int = 0


class TelegramStreamer:
    """One run owns its temporary messages, worker and final delivery task."""

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
        mode: str | None = None,
        edit_interval: float | None = None,
    ):
        from utils.config import telegram_agent_reply_settings

        configured_mode, configured_interval = telegram_agent_reply_settings()
        self.mode = mode or configured_mode
        self.edit_interval = (
            configured_interval if edit_interval is None else edit_interval
        )
        self._bot, self._chat_id = bot, chat_id
        self._message_id = message_id
        self._prefix = prefix  # Durable transcription, delivered with the final body.
        self._buffer = ""
        self._chat_ref = hashlib.sha256(str(chat_id).encode()).hexdigest()[:12]
        self.target = RunTarget(
            chat_id, user_id, conversation_id, request_id, agent_slug
        )
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
        self._edit_task: asyncio.Task | None = None
        self._typing_task: asyncio.Task | None = None
        self._final_task: asyncio.Task | None = None
        self._lease: OutputLease | None = None
        self._continuation_ids: list[int] = []
        self._last_sent: dict[int, tuple[str, str | None, object]] = {}
        self._next_edit = 0.0
        self._last_status = ""
        self._progress_disabled = False
        self._replacement_used = False
        self._rate_rejections = 0
        self._plain_only = False
        self.delivery = DeliveryResult()

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
                # Provider exception text can include secrets and tool returns.
                # Keep diagnostics in the existing audit path, never echo them.
                self._failed = True
                self._error_code = str(event.field("code", ""))
        self._wake.set()

    def start_edit_loop(self) -> asyncio.Task:
        if self._edit_task is None:
            self._edit_task = asyncio.create_task(
                self._edit_loop(), name="telegram-agent-progress"
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

    async def _edit_loop(self):
        try:
            while not self._done and not self._closed:
                self._wake.clear()
                if self._queued:
                    if asyncio.get_running_loop().time() >= self._next_edit:
                        await self._flush(final=False)
                elif self._started:
                    self._reserve_output()
                    await self._lease.acquire()
                    if self._done or self._closed:
                        break
                    if self._typing_task is None:
                        self._typing_task = asyncio.create_task(
                            self._typing_loop(), name="telegram-agent-typing"
                        )
                    status = self._status()
                    now = asyncio.get_running_loop().time()
                    # Approval transitions are actionable; other statuses are quiet.
                    urgent = (
                        status == "Waiting for your approval."
                        and status != self._last_status
                    )
                    if now >= self._next_edit or urgent:
                        await self._flush(final=False)
                        self._next_edit = max(
                            self._next_edit,
                            now
                            + (
                                self.edit_interval
                                if self.mode == "edit"
                                else STATUS_INTERVAL
                            ),
                        )
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=0.25)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log_failure("progress", exc)
        finally:
            await self._stop_typing()

    async def _typing_loop(self):
        failures = 0
        while not self._done and not self._closed:
            if not self._waiting_approval():
                try:
                    await telegram_call(
                        self._bot.send_chat_action,
                        chat_id=self._chat_id,
                        action="typing",
                        retry_rate_limits=False,
                    )
                    failures = 0
                except RetryAfter as exc:
                    failures += 1
                    if failures >= 3:
                        return
                    await asyncio.sleep(self._retry_seconds(exc))
                except Exception as exc:
                    self._log_failure("typing", exc)
                    failures += 1
                    if failures >= 3:
                        return
            await asyncio.sleep(TYPING_INTERVAL)

    async def _stop_typing(self):
        if self._typing_task and not self._typing_task.done():
            self._typing_task.cancel()
            await asyncio.gather(self._typing_task, return_exceptions=True)

    async def stop_edit_loop(self):
        self._closed = True
        unregister_run(self.target)
        if self._edit_task and not self._edit_task.done():
            self._edit_task.cancel()
            await asyncio.gather(self._edit_task, return_exceptions=True)
        await self._stop_typing()
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
        if self._final_task is not asyncio.current_task() and self.delivery.status in {
            "pending",
            "delivery_unknown",
        }:
            if not self._done:
                self.state = "cancelled"
            if self._message_id is not None:
                try:
                    async with asyncio.timeout(3):
                        await self._remove_stop(self._message_id)
                except TimeoutError:
                    pass

    def _build_text(self, final: bool) -> str:
        if not final:
            status = self._status()
            if (
                self.mode == "edit"
                and self._buffer.strip()
                and self.state not in {"queued", "waiting_approval"}
            ):
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
                reason = "This model cannot read images. Switch the model, or describe what is in the picture."
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
                    body = "No answer was returned. Check the saved conversation for operation results."
        return body

    @staticmethod
    def _pages(body, literal_prefix=""):
        try:
            return TelegramStreamer._formatted_pages(body, literal_prefix)
        except Exception as exc:
            log.warning("Telegram rendering failed error_type=%s", type(exc).__name__)
            source = "\n\n".join(part for part in (literal_prefix, body) if part)
            return [(chunk, None) for chunk in _split_text(source)]

    @staticmethod
    def _formatted_pages(body, literal_prefix=""):
        pages = render_markdown(
            body, literal_prefix=literal_prefix, max_wire_len=MAX_MESSAGE_LEN
        )
        result = []
        for page in pages:
            if len(page.html.encode("utf-16-le")) // 2 > MAX_MESSAGE_LEN:
                result.extend(
                    (chunk, None) for chunk in _split_text(plain_text(page.html))
                )
            else:
                result.append((page.html, "HTML"))
        source = "\n\n".join(part for part in (literal_prefix, body) if part)
        return result or [(chunk, None) for chunk in _split_text(source)]

    async def _flush(self, final: bool = False):
        if final:
            return await self.finalize()
        if self._done or self._closed or self._progress_disabled:
            return
        text = self._build_text(False)
        self._last_status = self._status()
        # Progress uses plain text; edit previews use the existing final renderer.
        chunks = (
            self._pages(text)
            if self.mode == "edit" and not self._queued
            else [(text, None)]
        )
        if self._plain_only:
            plain = "".join(plain_text(body) if mode else body for body, mode in chunks)
            chunks = [(chunk, None) for chunk in _split_text(plain)]
        markup = (
            stop_generating_keyboard(self.target.token)
            if self._started and self.target.request_id
            else None
        )
        ids = ([self._message_id] + self._continuation_ids) if self._message_id else []
        for index, (body, mode) in enumerate(chunks):
            current = ids[index] if index < len(ids) else None
            try:
                result = await self._present(
                    current, body, mode, markup if index == 0 else None
                )
            except RetryAfter as exc:
                self._rate_rejections += 1
                self._next_edit = asyncio.get_running_loop().time() + max(
                    self.edit_interval, self._retry_seconds(exc)
                )
                if self._rate_rejections >= 3:
                    self._progress_disabled = True
                return
            if result.reason == "parse" and mode and not self._plain_only:
                self._plain_only = True
                return await self._flush(final=False)
            if result.status != "confirmed":
                self._progress_disabled = True
                return
            if index == 0:
                self._message_id = result.message_id
            elif index >= len(ids):
                self._continuation_ids.append(result.message_id)
            else:
                self._continuation_ids[index - 1] = result.message_id
        self._rate_rejections = 0

    async def _present(self, message_id, text, mode, markup):
        result = await self._io(
            "edit" if message_id else "send",
            text=text,
            mode=mode,
            markup=markup,
            message_id=message_id,
            quiet=True,
            progress=True,
        )
        if result.reason == "missing" and not self._replacement_used:
            self._replacement_used = True
            return await self._present(None, text, mode, markup)
        return result

    async def finalize(self) -> DeliveryResult:
        if self._final_task is None:
            self._final_task = asyncio.create_task(
                self._finalize(), name="telegram-agent-final"
            )
        # Concurrent callers share one sender; the owner closes it in finally.
        return await asyncio.shield(self._final_task)

    async def _finalize(self):
        self._done = True
        self._reserve_output()
        try:
            await self.stop_edit_loop()
            self.state = "finalizing"
            self.delivery.payloads = self._pages(self._build_text(True), self._prefix)
            await self._lease.acquire()
            while self.delivery.confirmed < len(self.delivery.payloads):
                index = self.delivery.confirmed
                text, mode = self.delivery.payloads[index]
                result = await self._io("send", text=text, mode=mode, quiet=index > 0)
                # Only a definite non-delivery is eligible for one retry.
                if result.status == "failed" and result.reason == "connect":
                    result = await self._io(
                        "send", text=text, mode=mode, quiet=index > 0
                    )
                if result.reason == "parse" and mode:
                    self.delivery.payloads[index : index + 1] = [
                        (chunk, None) for chunk in _split_text(plain_text(text))
                    ]
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
            await self._cleanup()
            return self.delivery
        except asyncio.CancelledError:
            if self.delivery.status != "complete":
                self.delivery.status = "delivery_unknown"
            raise
        finally:
            self._lease.release()
            unregister_run(self.target)

    async def _retain_progress(self):
        notice = (
            "Delivery could not be confirmed; the answer was not resent. "
            "Open this conversation in the dashboard to read the saved result."
        )
        # Never erase a partial preview. Only edit a known message, never send
        # another notification after an ambiguous sendMessage outcome.
        if self._message_id is not None:
            previous = self._last_sent.get(self._message_id)
            text = (
                plain_text(previous[0])
                if previous and previous[1]
                else (previous[0] if previous else "")
            )
            if self.mode == "edit" and text:
                await self._remove_stop(self._message_id)
            else:
                await self._io("edit", text=notice, message_id=self._message_id)
        log.warning(
            "Telegram final delivery status=%s chat_ref=%s confirmed=%d total=%d",
            self.delivery.status,
            self._chat_ref,
            self.delivery.confirmed,
            len(self.delivery.payloads),
        )

    async def _cleanup(self):
        for message_id in (
            [self._message_id] if self._message_id else []
        ) + self._continuation_ids:
            try:
                result = await telegram_call(
                    self._bot.delete_message,
                    chat_id=self._chat_id,
                    message_id=message_id,
                )
                if result is not True and not (
                    isinstance(result, dict)
                    and result.get("ok") is True
                    and result.get("result") is True
                ):
                    raise ValueError("Missing acknowledgement")
            except Exception as exc:
                self._log_failure("cleanup", exc)
                await self._io(
                    "edit",
                    text="Finished. The response is below.",
                    message_id=message_id,
                )
                await self._remove_stop(message_id)

    async def _remove_stop(self, message_id):
        try:
            await telegram_call(
                self._bot.edit_message_reply_markup,
                chat_id=self._chat_id,
                message_id=message_id,
                reply_markup=None,
            )
        except Exception as exc:
            self._log_failure("remove_stop", exc)

    @staticmethod
    def _retry_seconds(exc):
        value = exc.retry_after
        return max(
            0,
            value.total_seconds() if hasattr(value, "total_seconds") else float(value),
        )

    @staticmethod
    def _message_id_of(result):
        if isinstance(result, dict):
            result = result.get("result")
            value = result.get("message_id") if isinstance(result, dict) else None
        else:
            value = getattr(result, "message_id", None)
        return value if type(value) is int and value > 0 else None

    async def _io(
        self,
        operation,
        *,
        text,
        mode=None,
        markup=None,
        message_id=None,
        quiet=True,
        progress=False,
    ):
        payload = (text, mode, markup)
        if operation == "edit" and self._last_sent.get(message_id) == payload:
            return OperationResult("confirmed", message_id)
        kwargs = dict(
            chat_id=self._chat_id,
            text=text,
            parse_mode=mode,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
        if operation == "send":
            kwargs["disable_notification"] = quiet
        else:
            kwargs["message_id"] = message_id
        try:
            method = (
                self._bot.send_message
                if operation == "send"
                else self._bot.edit_message_text
            )
            result = await telegram_call(
                method, retry_rate_limits=not progress, **kwargs
            )
            confirmed_id = self._message_id_of(result)
            if operation == "edit" and (
                result is True
                or confirmed_id == message_id
                or isinstance(result, dict)
                and result.get("result") is True
            ):
                confirmed_id = message_id
            if confirmed_id is None:
                return OperationResult("unknown")
            self._last_sent[confirmed_id] = payload
            return OperationResult("confirmed", confirmed_id)
        except RetryAfter:
            if progress:
                raise
            return OperationResult("failed", reason="rate_limit")
        except BadRequest as exc:
            error = str(exc).lower()
            if operation == "edit" and "not modified" in error:
                self._last_sent[message_id] = payload
                return OperationResult("confirmed", message_id)
            if mode and ("parse" in error or "entit" in error):
                return OperationResult("failed", reason="parse")
            if operation == "edit" and (
                "not found" in error
                or "can't be edited" in error
                or "cannot be edited" in error
            ):
                return OperationResult("failed", reason="missing")
            self._log_failure(operation, exc)
            return OperationResult("failed", reason="rejected")
        except Forbidden as exc:
            self._log_failure(operation, exc)
            return OperationResult("failed", reason="forbidden")
        except (NetworkError, httpx.ConnectTimeout, httpx.ConnectError) as exc:
            self._log_failure(operation, exc)
            cause = exc
            # PTB wraps HTTPX failures; only preconnection failures are safe to retry.
            while isinstance(cause, NetworkError) and cause.__cause__ is not None:
                cause = cause.__cause__
            if isinstance(cause, (httpx.ConnectTimeout, httpx.ConnectError)):
                return OperationResult("failed", reason="connect")
            return OperationResult("unknown")
        except Exception as exc:
            # Read/write failures can follow a successful send.
            self._log_failure(operation, exc)
            return OperationResult("unknown")

    def _log_failure(self, operation, exc):
        log.warning(
            "Telegram delivery failed operation=%s chat_ref=%s error_type=%s",
            operation,
            self._chat_ref,
            type(exc).__name__,
        )
