"""Telegram helper utilities for reusing callback-based handlers from commands."""

from datetime import datetime, timezone

from condor.telegram.types import Chat, Message


class MockQuery:
    """Stand-in callback query so command handlers can reuse callback flows."""

    def __init__(self, message, from_user):
        self.message = message
        self.from_user = from_user
        self.data = ""

    async def answer(self, text="", show_alert=False):
        pass


async def create_mock_query_from_message(update, initial_text="Loading..."):
    """Send a placeholder reply and wrap it as a query for callback handlers."""
    msg = await update.message.answer(initial_text)
    return MockQuery(msg, update.effective_user)


def message_handle(bot, chat_id: int, message_id: int) -> Message:
    """A bound ``Message`` for ``(chat_id, message_id)`` so it can be edited.

    Used where only the identifiers survive (deferred flows); the handle has
    no text or sender, only what ``edit_text``/``delete`` need.
    """
    return Message(
        message_id=message_id,
        date=datetime.now(timezone.utc),
        chat=Chat(id=chat_id, type="private" if chat_id > 0 else "supergroup"),
    ).as_(bot)
