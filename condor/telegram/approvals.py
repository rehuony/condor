"""Trade approvals asked and answered over Telegram.

The pending approval itself lives in :mod:`condor.runtime.confirmations`;
this module only renders it as an inline keyboard and posts the answer back.
That split is what lets the same request be answered from the dashboard or
over HTTP instead, whichever answers first.

Everything that knows the button wire format lives here, so a handler never
spells ``agent:confirm_trade:`` itself.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from condor.runtime.confirmations import (
    ConfirmationStatus,
    PendingConfirmation,
    build_permission_callback,
    get_registry,
)
from condor.telegram.transport import message_id_of, telegram_call

APPROVE_PREFIX = "confirm_trade:"
REJECT_PREFIX = "reject_trade:"


class MessageSender(Protocol):
    async def send_message(self, *args: Any, **kwargs: Any) -> Any: ...

    async def edit_message_reply_markup(self, *args: Any, **kwargs: Any) -> Any: ...


class TelegramChannel:
    """Renders a pending confirmation as an inline keyboard in a chat."""

    def __init__(self, bot: MessageSender, chat_id: int):
        self._bot = bot
        self._chat_id = chat_id

    async def deliver(self, pending: PendingConfirmation) -> None:
        from condor.telegram.output.runs import note_approval

        note_approval(self._chat_id, pending)
        if pending.status is not ConfirmationStatus.PENDING:
            return
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Approve",
                        callback_data=approval_callback(pending.id, True),
                    ),
                    InlineKeyboardButton(
                        text="Reject",
                        callback_data=approval_callback(pending.id, False),
                    ),
                ]
            ]
        )
        # Naming the asker is not decoration: one chat can talk to several
        # agents on several servers, and "Approve this action?" on its own does
        # not say whose action, or where it lands.
        asked_by = f"\nRequested by {pending.origin}\n" if pending.origin else ""
        # What the call leaves behind (a controller overwrite's impact), sent
        # as plain text like the summary: no parse mode, nothing to escape.
        detail = f"\n\n{pending.detail}" if pending.detail else ""
        result = await self._bot.send_message(
            chat_id=self._chat_id,
            text=(
                f"Trade Confirmation\n{asked_by}\n"
                f"{pending.summary}{detail}\n\nApprove this action?"
            ),
            reply_markup=keyboard,
            disable_notification=False,
        )
        message_id = message_id_of(result)
        if message_id is not None:

            async def clear_keyboard(_pending):
                await telegram_call(
                    self._bot.edit_message_reply_markup,
                    chat_id=self._chat_id,
                    message_id=message_id,
                    reply_markup=None,
                    retry_rate_limits=False,
                )

            get_registry().on_resolved(pending, clear_keyboard)


def approval_callback(request_id: str, approved: bool) -> str:
    """Callback data for the Approve/Reject button of one request."""
    prefix = APPROVE_PREFIX if approved else REJECT_PREFIX
    return f"agent:{prefix}{request_id}"


def parse_approval(action: str) -> tuple[str, bool] | None:
    """``(request_id, approved)`` for an ``agent:`` action, else ``None``.

    ``action`` is the part after the ``agent:`` module prefix, which is what
    the agents callback router has in hand.
    """
    for prefix, approved in ((APPROVE_PREFIX, True), (REJECT_PREFIX, False)):
        if action.startswith(prefix):
            return action[len(prefix) :], approved
    return None


def permission_callback(
    bot: MessageSender, chat_id: int, user_id: int, *, session_key: str
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Confirmation gate for one chat, delivered as an inline keyboard.

    The pending approval is registered centrally, so the same request can also
    be answered from the dashboard or over HTTP.
    """
    return build_permission_callback(
        session_key=session_key,
        user_id=user_id,
        channels=[TelegramChannel(bot, chat_id)],
    )


async def resolve_confirmation(
    request_id: str, approved: bool, by_user_id: int | None = None
) -> bool:
    """Record the user's tap on Approve/Reject.

    Returns True if the request was found and this user was allowed to answer
    it. False also covers "someone already answered", which is why the caller
    reports it as expired rather than as an error.
    """
    return await get_registry().resolve(
        request_id, approved=approved, by_user_id=by_user_id
    )
