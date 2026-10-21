"""Serialize message updates per chat without holding a chat for a whole turn."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from aiogram.types import InputRichMessage

from condor.telegram.errors import OperationResult, RateLimited, classify, retry_delay
from condor.telegram.transport import message_id_of
from condor.telegram.types import NO_LINK_PREVIEW

UPDATE_INTERVAL = 1.0
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MessageUpdate:
    text: str
    message_id: int | None = None
    mode: str | None = None
    reply_markup: object = None
    reply_to: int | None = None
    quiet: bool = True
    rich_html: str | None = None


class ChatWriter:
    def __init__(self, chat_id: int):
        self.chat_id = chat_id
        self.lock = asyncio.Lock()
        self.next_send = 0.0
        self._retry_until = 0.0

    async def write(
        self,
        bot,
        latest: Callable[[], MessageUpdate | None],
        *,
        request_id: str = "",
        on_attempt: Callable[[], None] | None = None,
    ):
        """Build the latest snapshot after waiting, never queue obsolete text."""
        if latest() is None:
            return None, None
        loop = asyncio.get_running_loop()
        waiting_at = loop.time()
        async with self.lock:
            acquired_at = loop.time()
            if latest() is None:
                return None, None
            delay = max(0.0, self.next_send - acquired_at)
            # Attribute overlapping gates to server cooldown first. Together
            # these account for the requested wait, without double counting it.
            retry_wait = min(delay, max(0.0, self._retry_until - acquired_at))
            if delay:
                await asyncio.sleep(delay)
            update = latest()
            if update is None:
                return None, None
            self.next_send = loop.time() + UPDATE_INTERVAL
            editing = update.message_id is not None
            kwargs = {
                "chat_id": self.chat_id,
                "reply_markup": update.reply_markup,
            }
            if update.mode == "rich":
                kwargs["rich_message"] = InputRichMessage(html=update.rich_html)
            else:
                kwargs.update(text=update.text, parse_mode=None)
                kwargs["link_preview_options"] = NO_LINK_PREVIEW
            if editing:
                kwargs["message_id"] = update.message_id
                method = bot.edit_message_text
            else:
                kwargs["disable_notification"] = update.quiet
                if update.reply_to is not None:
                    from aiogram.types import ReplyParameters

                    kwargs["reply_parameters"] = ReplyParameters(
                        message_id=update.reply_to, allow_sending_without_reply=True
                    )
                method = (
                    bot.send_rich_message if update.mode == "rich" else bot.send_message
                )
            if on_attempt is not None:
                on_attempt()
            attempted_at = loop.time()
            outcome = None
            try:
                result = await method(**kwargs)
                acknowledged = result is True or message_id_of(result) is not None
                message_id = (
                    (update.message_id if acknowledged else None)
                    if editing
                    else message_id_of(result)
                )
                outcome = (
                    OperationResult("confirmed", message_id)
                    if message_id is not None
                    else OperationResult("unknown")
                )
            except RateLimited as exc:
                self._retry_until = max(
                    self._retry_until, loop.time() + retry_delay(exc)
                )
                self.next_send = max(self.next_send, self._retry_until)
                outcome = OperationResult("failed", reason="rate_limit")
            except Exception as exc:
                outcome = classify(
                    exc,
                    operation="edit" if editing else "send",
                    parse_mode="HTML" if update.mode else None,
                )
                if outcome.status == "confirmed":
                    outcome = OperationResult("confirmed", update.message_id)
            finally:
                log.info(
                    "Telegram delivery timing request_id=%s operation=%s outcome=%s "
                    "lock_ms=%.1f throttle_ms=%.1f retry_after_ms=%.1f network_ms=%.1f",
                    request_id,
                    "edit" if editing else "send",
                    outcome.status if outcome is not None else "cancelled",
                    (acquired_at - waiting_at) * 1000,
                    (delay - retry_wait) * 1000,
                    retry_wait * 1000,
                    (loop.time() - attempted_at) * 1000,
                )
            return update, outcome


_writers: dict[int, ChatWriter] = {}


def chat_writer(chat_id: int) -> ChatWriter:
    return _writers.setdefault(chat_id, ChatWriter(chat_id))
