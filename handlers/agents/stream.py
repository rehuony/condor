"""Telegram streaming via edit_message_text on a placeholder message."""

import asyncio
import logging

from telegram import Bot, InlineKeyboardMarkup
from telegram.error import BadRequest

from condor.runtime.events import EventType, RuntimeEvent
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

EDIT_INTERVAL = 0.5
_THINKING_FRAMES = ["Thinking.", "Thinking..", "Thinking..."]
QUEUED_LABEL = (
    "⏳ Queued — waiting for the current answer to finish.\n/stop interrupts it."
)
STOPPED_LABEL = "⏹ Stopped."


class TelegramStreamer:
    """Streams RuntimeEvents by editing a placeholder Telegram message."""

    def __init__(self, bot: Bot, chat_id: int, message_id: int, prefix: str = ""):
        self._bot = bot
        self._chat_id = chat_id
        self._message_id = message_id
        self._prefix = prefix
        self._buffer = ""
        self._active_tools: dict[str, bool] = {}
        self._had_tools = False
        self._plain_only = False
        self._parse_failed = False
        self._needs_edit = False
        self._edit_task: asyncio.Task | None = None
        self._done = False
        self._queued = False
        self._stop_reason: str | None = None
        self._tick = 0
        self._continuation_ids: list[int] = []
        # message_id -> (text, parse_mode, reply_markup) last known to be on
        # screen. Guards against re-sending an edit Telegram would reject as
        # "not modified". The markup belongs in the key: the final flush of a
        # long answer often changes nothing but the button's absence.
        self._last_sent: dict[int, tuple[str, str | None, object]] = {}
        # Built once so every flush passes the same object — cheap, and the
        # dedupe compare above short-circuits on identity.
        self._stop_markup = stop_generating_keyboard()

    # --- Event processing ---

    async def process_event(self, event: RuntimeEvent) -> None:
        # Emitted before the turn blocks on the session lock, so the placeholder
        # can say "waiting its turn" instead of pulsing "Thinking" at a user
        # whose message has not reached the agent yet.
        if event.type == EventType.QUEUED:
            self._queued = True
            self._needs_edit = True
            return
        # Anything else means the lock was won: this turn is live now.
        self._queued = False

        if event.type == EventType.TEXT:
            self._buffer += event.text
            self._needs_edit = True
        elif event.type == EventType.TOOL_CALL:
            tc_id = event.field("tool_call_id")
            self._active_tools[tc_id] = True
            self._had_tools = True
            self._needs_edit = True
        elif event.type == EventType.TOOL_UPDATE:
            self._handle_tool_update(event)
        elif event.type == EventType.HEARTBEAT:
            self._needs_edit = True
        elif event.type == EventType.ERROR:
            # Surface the failure in the message body rather than silently
            # ending the turn; DONE always follows, which stops the loop.
            self._buffer += f"\n\n{event.field('message', 'Stream error')}"
            self._needs_edit = True
        elif event.type == EventType.DONE:
            self._stop_reason = event.stop_reason
            self._done = True

    def _handle_tool_update(self, event: RuntimeEvent) -> None:
        tc_id = event.field("tool_call_id")
        status = event.field("status")
        if status in ("completed", "failed"):
            self._active_tools.pop(tc_id, None)
            self._needs_edit = True

    # --- Edit loop ---

    def start_edit_loop(self) -> asyncio.Task:
        self._edit_task = asyncio.create_task(self._edit_loop())
        return self._edit_task

    async def _edit_loop(self) -> None:
        try:
            while not self._done:
                self._tick += 1
                force = self._active_tools and self._tick % 10 == 0
                if self._queued:
                    # A queued turn has nothing to animate, and several can be
                    # stacked behind one answer: flushing on change only keeps
                    # N idle placeholders from each editing twice a second.
                    if self._needs_edit:
                        await self._flush(final=False)
                elif self._needs_edit or not self._buffer or force:
                    await self._flush(final=False)
                await asyncio.sleep(EDIT_INTERVAL)
        except asyncio.CancelledError:
            pass

    async def finalize(self) -> None:
        if self._edit_task and not self._edit_task.done():
            self._edit_task.cancel()
            try:
                await self._edit_task
            except asyncio.CancelledError:
                pass

        self._active_tools.clear()

        # The live loop has ended, so a failed final send will not get another
        # tick. Retry once, reusing the IDs of pages already acknowledged.
        for attempt in range(2):
            await self._flush(final=True)
            if not self._needs_edit:
                return
            if attempt == 0:
                await asyncio.sleep(EDIT_INTERVAL)
        log.warning(
            "Final Telegram reply was not fully delivered to chat %s", self._chat_id
        )

    # --- Build & flush ---

    def _build_text(self, final: bool) -> str:
        parts = [self._prefix] if self._prefix else []
        buf = self._buffer.strip()
        stopped = final and self._stop_reason == "cancelled"
        if buf:
            parts.append(buf)
        elif not final:
            parts.append(
                QUEUED_LABEL
                if self._queued
                else (
                    "Working..."
                    if self._active_tools
                    else _THINKING_FRAMES[self._tick % len(_THINKING_FRAMES)]
                )
            )
        elif stopped:
            parts.append("Stopped before answering.")
        else:
            parts.append("Done." if self._had_tools else "No response.")
        if stopped and buf:
            parts.append(STOPPED_LABEL)
        return "\n\n".join(parts)

    async def _flush(self, final: bool) -> None:
        self._needs_edit = False
        self._parse_failed = False
        text = self._build_text(final)
        parse_mode = "HTML" if final and not self._plain_only else None
        if final:
            pages = render_markdown(text)
            if self._plain_only:
                text = plain_text("".join(page.html for page in pages))
                chunks = _split_text(text, MAX_MESSAGE_LEN)
            else:
                chunks = [page.html for page in pages]
        else:
            chunks = _split_text(text, MAX_MESSAGE_LEN)
        if not chunks:
            chunks = ["No response."]
        markup = None if final else self._stop_markup
        await self._edit(self._message_id, chunks[0], parse_mode, markup)
        for i, chunk in enumerate(chunks[1:]):
            if i < len(self._continuation_ids):
                await self._edit(self._continuation_ids[i], chunk, parse_mode)
            else:
                msg_id = await self._send(chunk, parse_mode)
                if msg_id is None:
                    self._needs_edit = True
                    break  # Do not assign a later page to the missing page's slot.
                self._continuation_ids.append(msg_id)
        if self._parse_failed and not self._plain_only:
            self._plain_only = True
            await self._flush(final=final)
            return
        # Formatting removes Markdown syntax, so the final answer can need fewer
        # messages. Remove old tails instead of leaving duplicated partial text.
        if final and not self._needs_edit:
            while len(self._continuation_ids) > len(chunks) - 1:
                message_id = self._continuation_ids[-1]
                try:
                    await telegram_call(
                        self._bot.delete_message,
                        chat_id=self._chat_id,
                        message_id=message_id,
                    )
                except Exception:
                    log.warning(
                        "Could not remove stale continuation %s",
                        message_id,
                        exc_info=True,
                    )
                    break
                self._continuation_ids.pop()
                self._last_sent.pop(message_id, None)

    # --- Telegram I/O ---

    async def _edit(
        self,
        message_id: int,
        text: str,
        parse_mode: str | None = None,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        # A long answer splits at a paragraph boundary that stays put as the
        # buffer grows, so every chunk but the last is byte-identical on each
        # tick. Those edits only ever come back "not modified" — and each one
        # spends per-chat quota that the chunk which *did* change then waits
        # for. Skip what is already on screen.
        if self._last_sent.get(message_id) == (text, parse_mode, reply_markup):
            return
        try:
            await telegram_call(
                self._bot.edit_message_text,
                chat_id=self._chat_id,
                message_id=message_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
        except BadRequest as exc:
            if "not modified" not in str(exc).lower():
                self._parse_failed |= bool(
                    parse_mode
                    and ("parse" in str(exc).lower() or "entit" in str(exc).lower())
                )
                self._needs_edit = True
                log.warning("Failed to edit message: %s", exc)
                return
        except Exception:
            self._needs_edit = True
            log.warning("Failed to edit message", exc_info=True)
            return
        self._last_sent[message_id] = (text, parse_mode, reply_markup)

    async def _send(self, text: str, parse_mode: str | None = None) -> int | None:
        try:
            msg = await telegram_call(
                self._bot.send_message,
                chat_id=self._chat_id,
                text=text,
                parse_mode=parse_mode,
                disable_web_page_preview=True,
            )
            message_id = (
                (msg.get("result") or {}).get("message_id")
                if isinstance(msg, dict)
                else getattr(msg, "message_id", None)
            )
            if message_id is not None:
                self._last_sent[message_id] = (text, parse_mode, None)
            return message_id
        except BadRequest as exc:
            self._parse_failed |= bool(
                parse_mode
                and ("parse" in str(exc).lower() or "entit" in str(exc).lower())
            )
            log.warning("Failed to send message: %s", exc)
        except Exception:
            log.warning("Failed to send message", exc_info=True)
        return None
