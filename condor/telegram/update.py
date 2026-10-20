"""The update and context a handler receives: the project's own library boundary.

aiogram hands the dispatcher a frozen ``Update`` whose ``message`` or
``callback_query`` is set, with nothing that says "the user behind this update"
or carries per-user state. Handlers were written against ``update.message``,
``update.callback_query``, ``update.effective_*`` and a ``context`` with
``bot``/``user_data``/``chat_data``/``args``, so those two shapes are defined
here, built once per update by :mod:`condor.telegram.app`, and passed to every
handler as ``(update, context)``.

Neither type is a compatibility shim over another library: they are the only
update and context the handlers know, and tests build them directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Sequence

from aiogram.types import (
    CallbackQuery,
    Chat,
    Message,
    MessageGenerationStopped,
    Update,
    User,
)

if TYPE_CHECKING:
    from .app import TelegramApp


class TgUpdate:
    """One inbound update with the message or callback query it carries."""

    __slots__ = ("raw", "message", "callback_query", "stopped_message_generation")

    def __init__(
        self,
        *,
        message: Message | None = None,
        callback_query: CallbackQuery | None = None,
        stopped_message_generation: MessageGenerationStopped | None = None,
        raw: Update | None = None,
    ) -> None:
        self.raw = raw
        self.message = message
        self.callback_query = callback_query
        self.stopped_message_generation = stopped_message_generation

    @classmethod
    def from_aiogram(cls, update: Update) -> "TgUpdate":
        return cls(
            message=update.message,
            callback_query=update.callback_query,
            stopped_message_generation=update.stopped_message_generation,
            raw=update,
        )

    @property
    def update_id(self) -> int | None:
        return self.raw.update_id if self.raw is not None else None

    @property
    def effective_message(self) -> Message | None:
        if self.message is not None:
            return self.message
        if self.callback_query is not None:
            message = self.callback_query.message
            return message if isinstance(message, Message) else None
        return None

    @property
    def effective_user(self) -> User | None:
        if self.message is not None:
            return self.message.from_user
        if self.callback_query is not None:
            return self.callback_query.from_user
        return None

    @property
    def effective_chat(self) -> Chat | None:
        message = self.effective_message
        if message is not None:
            return message.chat
        if self.stopped_message_generation is not None:
            return self.stopped_message_generation.chat
        return None

    def with_text(self, text: str) -> "TgUpdate":
        """A copy whose message text is ``text``; the original stays intact.

        Form processors read ``message.text``; a rich message or a document's
        caption is normalised into that field for them without touching the
        update the conversational fallthrough sees.
        """
        assert self.message is not None
        # ``model_copy`` keeps the bot binding, so ``answer`` still works.
        message = self.message.model_copy(update={"text": text})
        return TgUpdate(message=message, raw=self.raw)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        kind = (
            "message"
            if self.message
            else "callback_query" if self.callback_query else "stopped"
        )
        return f"TgUpdate({kind}, id={self.update_id})"


class TgContext:
    """What a handler has besides the update: the bot, state and command args.

    ``user_data`` and ``chat_data`` are the live dicts of
    :class:`condor.telegram.state.UserStateStore`; ``application`` is the
    :class:`~condor.telegram.app.TelegramApp` that owns them, for code that
    needs another user's bucket (scheduled routines). ``error`` is set only on
    the context handed to the error handler.
    """

    __slots__ = ("bot", "application", "user_data", "chat_data", "args", "error")

    def __init__(
        self,
        *,
        bot: Any,
        application: "TelegramApp | None" = None,
        user_data: dict | None = None,
        chat_data: dict | None = None,
        args: Sequence[str] = (),
        error: BaseException | None = None,
    ) -> None:
        self.bot = bot
        self.application = application
        self.user_data = user_data
        self.chat_data = chat_data
        self.args = list(args)
        self.error = error


def command_args(message: Message) -> list[str]:
    """The words after a ``/command`` in ``message``, exposed as ``context.args``."""
    text = message.text or ""
    return text.split()[1:]


def command_name(message: Message, bot_username: str | None) -> str | None:
    """``"start"`` for ``/start`` or ``/start@this_bot``; None for anything else.

    A command addressed to another bot (``/start@other_bot``) is not ours, which
    matters in groups where several bots listen.
    """
    text = message.text or ""
    if not text.startswith("/"):
        return None
    entities = message.entities or ()
    if not any(e.type == "bot_command" and e.offset == 0 for e in entities):
        return None
    token = text.split(maxsplit=1)[0][1:]
    name, _, mention = token.partition("@")
    if mention and bot_username and mention.lower() != bot_username.lower():
        return None
    return name.lower() or None
