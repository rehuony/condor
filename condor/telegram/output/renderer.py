"""One request's lifecycle, stable message segments and delivery settlement."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from condor.runtime.events import EventType, RuntimeEvent
from condor.telegram.output.presentation import (
    SegmentBuffer,
    final_payload,
    render_preview,
    tool_action,
    tool_inputs,
)
from condor.telegram.output.runs import RunTarget, register_run, unregister_run
from condor.telegram.output.timing import ReplyTiming
from condor.telegram.output.writer import MessageUpdate, chat_writer
from condor.telegram.types import InlineKeyboardButton, InlineKeyboardMarkup

log = logging.getLogger(__name__)
STOPPED_LABEL = "⏹ Stopped generating."
_live: set[RichReplyStreamer] = set()
_TOOL_TERMINAL = {
    "completed",
    "failed",
    "blocked",
    "denied",
    "rejected",
    "cancelled",
    "canceled",
    "error",
}


@dataclass
class DeliveryResult:
    status: str = "pending"  # complete, failed, partial, delivery_unknown
    message_ids: list[int] = field(default_factory=list)
    payloads: list[tuple[str, str | None]] = field(default_factory=list)
    confirmed: int = 0


@dataclass
class _Page:
    message_id: int | None = None
    sent: MessageUpdate | None = None
    plain_only: bool = False
    unknown: bool = False
    uncertain_edit: bool = False


@dataclass
class _Tool:
    status: str = "pending"
    kind: str = ""
    name: str = ""
    title: str = ""
    arguments: dict = field(default_factory=dict)

    def label(self) -> str:
        return tool_action(
            self.title,
            self.kind,
            self.name,
            arguments=self.arguments,
            status=self.status,
        )


class RichReplyStreamer:
    """Own a request independently of menu selection and runtime execution."""

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
        timing: ReplyTiming | None = None,
    ):
        self._bot, self._chat_id = bot, chat_id
        self._progress = chat_id > 0 if private is None else private
        self.target = RunTarget(
            chat_id, user_id, conversation_id, request_id, agent_slug
        )
        self.target.on_stop = self._stop_display
        self.target.changed = self._changed
        self.input_message_id = input_message_id
        self.timing = timing or ReplyTiming(request_id)
        self.timing.mark("received")
        self.body = SegmentBuffer()
        self._prefix = prefix
        self._prefix_end = 0
        self._pages = [_Page(message_id)]
        self._final_payloads: list[tuple[str, str | None]] = []
        if message_id is not None:
            self.target.message_ids.add(message_id)
        self._phase = "Received. Preparing your request…"
        self.state = "received"
        self.tools: dict[str, _Tool] = {}
        self._response_phase = "Preparing response…"
        self._queued = False
        self._done = False
        self._closed = False
        self._failed = False
        self._error_code = ""
        self._input_error = ""
        self._stop_reason = ""
        self._frozen = False
        self._wake = asyncio.Event()
        self._edit_task: asyncio.Task | None = None
        self._final_task: asyncio.Task | None = None
        self.delivery = DeliveryResult()
        _live.add(self)

    def _changed(self) -> None:
        self._wake.set()

    def _stop_display(self) -> None:
        self._frozen = True
        self.state = "stopping"
        self._wake.set()

    def begin_preparation(self, label: str, cancel: Callable[[], None]) -> None:
        self.set_phase(label)
        self.target.cancel_preparation = cancel
        register_run(self.target)
        self.start_edit_loop()

    def set_phase(self, label: str) -> None:
        if not self._done and not self._frozen:
            self._phase = label or "Preparing your request…"
            self.state = "preparing"
            self._wake.set()

    def bind_input(self, *, conversation_id: str, prefix: str = "") -> None:
        if (
            self.target.conversation_id
            and self.target.conversation_id != conversation_id
        ):
            raise ValueError("A Telegram request cannot change conversations")
        self.target.conversation_id = conversation_id
        self._prefix = prefix

    def _append(self, text: str) -> None:
        if text and not self.body.body and self._prefix:
            self.body.append(self._prefix + "\n\n")
            self._prefix_end = len(self.body.body)
        self.body.append(text)

    async def process_event(self, event: RuntimeEvent) -> None:
        if self._closed or self._done or self._final_task is not None:
            return
        if event.session_key and event.session_key != f"tg:{self._chat_id}":
            return
        if event.type in {
            EventType.TEXT,
            EventType.THOUGHT,
            EventType.TOOL_CALL,
            EventType.TOOL_UPDATE,
        }:
            self.timing.mark("execution_started")
        if event.type == EventType.ACCEPTED:
            self.target.request_id = event.field("request_id", self.target.request_id)
            self.timing.request_id = self.target.request_id
            self.timing.mark("accepted")
            self.bind_input(
                conversation_id=event.field(
                    "conversation_id", self.target.conversation_id
                ),
                prefix=self._prefix,
            )
            self.target.agent_slug = event.field("agent_slug", self.target.agent_slug)
            self.target.accepted = True
            self.target.cancel_preparation = None
            self._phase = ""
            register_run(self.target)
        elif event.type == EventType.DONE:
            self.timing.mark("execution_done")
            self._stop_reason = event.stop_reason
            self._done = True
            unregister_run(self.target)
        elif event.type == EventType.QUEUED:
            self._queued = True
            self._phase = ""
        elif event.type == EventType.THOUGHT and self._queued:
            # A provider thought proves execution has started, but its text
            # remains private. Heartbeats alone cannot establish this boundary.
            self._queued = False
        elif event.type == EventType.TEXT:
            if event.field("is_error", False):
                self._failed = True
            elif event.text and not self._frozen:
                self.timing.mark("first_text")
                self._phase = ""
                self._queued = False
                self._append(event.text)
                self._response_phase = "Writing response…"
        elif event.type in (EventType.TOOL_CALL, EventType.TOOL_UPDATE):
            key = str(event.field("tool_call_id") or "")
            tool = self.tools.get(key)
            previous = (tool.status, tool.label()) if tool else None
            tool = tool or _Tool()
            for attribute, event_field in (
                ("status", "status"),
                ("kind", "kind"),
                ("name", "name"),
                ("title", "title"),
            ):
                value = event.field(event_field)
                if isinstance(value, str) and value.strip():
                    if (
                        attribute == "status"
                        and tool.status in _TOOL_TERMINAL
                        and value not in _TOOL_TERMINAL
                    ):
                        continue
                    setattr(tool, attribute, value)
            tool.arguments.update(tool_inputs(event.field("input")))
            # Only a changed visible action advances recency. Heartbeats and
            # output-only updates must not steal focus from another active tool.
            if previous != (tool.status, tool.label()):
                self.tools.pop(key, None)
                self.tools[key] = tool
                self._phase = ""
                self._queued = False
                if tool.status in _TOOL_TERMINAL and (
                    previous is None or previous[0] not in _TOOL_TERMINAL
                ):
                    self._response_phase = "Processing tool results…"
        elif event.type == EventType.ERROR:
            self._phase = ""
            self._queued = False
            self._failed = True
            self._error_code = str(event.field("code", ""))
            if self._error_code == "telegram_input":
                self._input_error = str(event.field("message", ""))
        self._wake.set()

    def _status(self) -> str:
        if self._frozen or self.target.stopped:
            self.state = "stopping"
            return "Stopping and clearing queued requests…"
        if any(
            getattr(p.status, "value", p.status) == "pending"
            for p in self.target.approvals
        ):
            self.state = "waiting_approval"
            return "Waiting for your confirmation…"
        if self._phase:
            self.state = "preparing"
            return self._phase
        if self._queued:
            self.state = "queued"
            return "Waiting for the previous request…"
        self.state = "running"
        for tool in reversed(tuple(self.tools.values())):
            if tool.status not in _TOOL_TERMINAL:
                return tool.label()
        return self._response_phase

    def _button(self):
        if self.target.stopped or not self.target.request_id:
            return None
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🛑 Stop and Clear Queue",
                        callback_data=f"agent:stop_run:{self.target.token}",
                    )
                ]
            ]
        )

    def _final_payload(self, segment):
        # Transcriptions are literal user content, not model-authored Markdown.
        if segment.start < self._prefix_end:
            return segment.text, None
        return final_payload(segment, self.body.body)

    def _updates(self, final: bool):
        segments = self.body.segments
        count = len(segments) or 1
        while len(self._pages) < count:
            self._pages.append(_Page())
        for index in range(count):
            page = self._pages[index]
            if page.unknown:
                return
            segment = segments[index] if segments else None
            active = index == count - 1
            html = None
            if final:
                text, html = self._final_payloads[index]
                if page.plain_only:
                    html = None
                mode = "rich" if html is not None else None
                markup = None
            else:
                text = segment.text if segment else ""
                if active:
                    text = render_preview(text, self._status())
                mode = None
                markup = self._button() if active else None
            update = MessageUpdate(
                text,
                page.message_id,
                mode,
                markup,
                self.input_message_id if index == 0 else None,
                rich_html=html,
            )
            # Compare with the acknowledged message id, including sends.
            if update != page.sent:
                yield index, update

    async def _deliver(self, final: bool) -> bool:
        """Drain latest snapshots; failures never re-enter the model runtime."""
        failures = 0
        while not self._closed:
            selected = None
            has_body = False

            def latest():
                nonlocal selected, has_body
                if self._closed or (not final and self._done):
                    return None
                selected = next(self._updates(final), None)
                has_body = bool(selected and self.body.segments)
                return selected[1] if selected else None

            update, result = await chat_writer(self._chat_id).write(
                self._bot,
                latest,
                request_id=self.target.request_id,
                on_attempt=lambda: self.timing.mark("first_send_attempt"),
            )
            if update is None:
                return True
            index = selected[0]
            page = self._pages[index]
            if result.status == "confirmed":
                self.timing.mark("first_send_ack")
                if has_body:
                    self.timing.mark("first_body_ack")
                page.uncertain_edit = False
                page.message_id = result.message_id
                self.target.message_ids.add(result.message_id)
                page.sent = replace(update, message_id=page.message_id)
                failures = 0
                if final and index == len(self._pages) - 1:
                    self.timing.mark("final_ack")
                continue
            if result.status == "unknown" and update.message_id is not None:
                page.uncertain_edit = True
            failures += 1
            if result.reason == "parse" and update.mode:
                page.plain_only = True
            elif result.reason == "missing" and update.message_id is not None:
                self.target.message_ids.discard(page.message_id)
                page.message_id = None
                page.sent = None
            elif result.status == "unknown" and update.message_id is None:
                page.unknown = True
                self.delivery.status = "delivery_unknown"
                return False
            elif result.reason not in {"connect", "rate_limit"} and not (
                result.status == "unknown" and update.message_id is not None
            ):
                return False
            if failures >= 3:
                return False
        return False

    def start_edit_loop(self) -> asyncio.Task:
        if self._edit_task is None:
            register_run(self.target)
            self._edit_task = asyncio.create_task(
                self._edit_loop(), name="telegram-agent-progress"
            )
        return self._edit_task

    async def _edit_loop(self):
        try:
            while not self._done and not self._closed:
                self._wake.clear()
                if self._progress:
                    await self._deliver(False)
                if not self._done and not self._closed:
                    await self._wake.wait()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Telegram progress failed error_type=%s", type(exc).__name__)

    def _settle_body(self) -> None:
        notice = ""
        if self._stop_reason == "cancelled" or self.target.stopped:
            self.state = "cancelled"
            notice = STOPPED_LABEL if self.body.body else "Stopped before answering."
        elif self._stop_reason == "duplicate":
            self.state = "completed"
            notice = "This request was already received."
        elif self._failed or self._stop_reason != "end_turn":
            self.state = "failed"
            notice = {
                "timeout": "The agent timed out.",
                "disconnected": "The agent disconnected.",
                "access_denied": "Your access changed; the agent did not execute this request.",
            }.get(self._stop_reason, "Could not finish this turn.")
            if self._input_error:
                notice = self._input_error
            elif self._error_code == "images_unsupported":
                notice = "This model cannot read images. Switch the model, or describe what is in the picture."
        else:
            self.state = "completed"
            if not self.body.body.strip():
                notice = (
                    "No answer was returned. Check the saved conversation for operation results."
                    if self.tools
                    else "No response was returned."
                )
        if notice:
            self._append(("\n\n" if self.body.body else "") + notice)
        self.body.finalize()

    async def finalize(self) -> DeliveryResult:
        if self._final_task is None:
            self._final_task = asyncio.create_task(
                self._finalize(), name="telegram-agent-final"
            )
        return await asyncio.shield(self._final_task)

    async def _finalize(self):
        self.timing.mark("finalize_start")
        self._done = True
        unregister_run(self.target)
        self._wake.set()
        try:
            # An in-flight edit must settle before any terminal edit starts.
            if self._edit_task:
                await self._edit_task
            self._settle_body()
            self._final_payloads = [
                self._final_payload(segment) for segment in self.body.segments
            ]
            complete = await self._deliver(True)
            self.delivery.payloads = [
                (text, "rich" if html is not None and not page.plain_only else None)
                for (text, html), page in zip(self._final_payloads, self._pages)
            ]
            self.delivery.message_ids = [
                p.message_id for p in self._pages if p.message_id is not None
            ]
            self.delivery.confirmed = sum(
                p.sent is not None
                and p.sent.text == text
                and p.sent.mode == mode
                and p.sent.reply_markup is None
                for p, (text, mode) in zip(self._pages, self.delivery.payloads)
            )
            if any(p.unknown or p.uncertain_edit for p in self._pages):
                self.delivery.status = "delivery_unknown"
            else:
                self.delivery.status = (
                    "complete"
                    if complete
                    else ("partial" if self.delivery.confirmed else "failed")
                )
            if self.delivery.status != "complete":
                log.warning(
                    "Telegram final delivery status=%s confirmed=%d total=%d",
                    self.delivery.status,
                    self.delivery.confirmed,
                    len(self.delivery.payloads),
                )
            return self.delivery
        except asyncio.CancelledError:
            self.delivery.status = "delivery_unknown"
            raise
        finally:
            self.timing.mark("delivery_finished")
            _live.discard(self)

    async def stop_edit_loop(self):
        self._closed = True
        unregister_run(self.target)
        self._wake.set()
        tasks = [
            task
            for task in (self._edit_task, self._final_task)
            if task and task is not asyncio.current_task() and not task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        _live.discard(self)


async def close_presenters() -> None:
    """Close delivery workers while the bot transport is still available."""
    await asyncio.gather(*(presenter.stop_edit_loop() for presenter in tuple(_live)))


TelegramStreamer = RichReplyStreamer
