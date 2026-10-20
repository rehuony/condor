"""The aiogram bot and dispatcher, wired to the project's handler contract.

:class:`TelegramApp` owns one :class:`aiogram.Bot`, one
:class:`aiogram.Dispatcher` and the :class:`~condor.telegram.state.UserStateStore`
behind ``context.user_data``. The dispatcher carries four fixed handlers —
message, callback query, stopped generation, error — that build a
:class:`~condor.telegram.update.TgUpdate`/:class:`~condor.telegram.update.TgContext`
pair and call whatever the current :class:`~condor.telegram.routes.RouteTable`
names. Handlers therefore keep their ``(update, context)`` signature and a hot
reload only swaps the table.

Updates are processed concurrently (aiogram's ``handle_as_tasks``); the
ingress in :mod:`condor.telegram.ingress` relies on that to order one chat's
turns without serialising the others.
"""

from __future__ import annotations

import asyncio
import logging
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Mapping

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.types import CallbackQuery, ErrorEvent, Message
from aiogram.types import MessageGenerationStopped as StoppedGeneration
from aiogram.types import Update

from .routes import RouteTable, get_routes
from .state import UserStateStore
from .update import TgContext, TgUpdate, command_args, command_name

log = logging.getLogger(__name__)

#: The update kinds the bot asks Telegram for. ``stopped_message_generation``
#: is what a user's tap on "stop" during a draft arrives as (Bot API 10.3).
ALLOWED_UPDATES = ["message", "callback_query", "stopped_message_generation"]

#: Chats whose ordinary messages reach the content router. Channel posts and
#: anything without a sender never do.
_ROUTED_CHAT_TYPES = frozenset({"private", "group", "supergroup"})

#: Service messages (someone joined, a pin, a title change, ...): visible in the
#: chat but not input. Kept as a list of the fields such a message carries.
_SERVICE_FIELDS = (
    "new_chat_members",
    "left_chat_member",
    "new_chat_title",
    "new_chat_photo",
    "delete_chat_photo",
    "group_chat_created",
    "supergroup_chat_created",
    "channel_chat_created",
    "message_auto_delete_timer_changed",
    "migrate_to_chat_id",
    "migrate_from_chat_id",
    "pinned_message",
    "proximity_alert_triggered",
    "boost_added",
    "chat_background_set",
    "forum_topic_created",
    "forum_topic_edited",
    "forum_topic_closed",
    "forum_topic_reopened",
    "general_forum_topic_hidden",
    "general_forum_topic_unhidden",
    "giveaway_created",
    "video_chat_scheduled",
    "video_chat_started",
    "video_chat_ended",
    "video_chat_participants_invited",
    "web_app_data",
    "users_shared",
    "chat_shared",
    "connected_website",
    "write_access_allowed",
    "refunded_payment",
    "successful_payment",
)

ErrorHandler = Callable[[TgUpdate | None, TgContext], Awaitable[None]]


def is_service_message(message: Message) -> bool:
    return any(getattr(message, name, None) for name in _SERVICE_FIELDS)


def make_bot(token: str, *, session: BaseSession | None = None) -> Bot:
    """A bot with no default parse mode: every send says what it sends."""
    return Bot(token, session=session, default=DefaultBotProperties())


class TelegramApp:
    """The running bot: client, dispatcher, state, and the route trampolines."""

    def __init__(
        self,
        token: str,
        store: UserStateStore,
        *,
        session: BaseSession | None = None,
        bot: Bot | None = None,
    ) -> None:
        self.bot = bot if bot is not None else make_bot(token, session=session)
        self.dispatcher = Dispatcher()
        self.store = store
        #: Read-only views over the store's dicts, keyed by user and chat id.
        #: The same object per id, so a job and a handler share one bucket.
        self.user_data: Mapping[int, dict] = MappingProxyType(store.users)
        self.chat_data: Mapping[int, dict] = MappingProxyType(store.chats)
        self.username: str | None = None
        self.error_handler: ErrorHandler | None = None
        self._polling: asyncio.Task | None = None
        self._install()

    # -- lifecycle ----------------------------------------------------------

    async def initialize(self) -> None:
        """Learn who the bot is; needed to recognise ``/cmd@this_bot`` in groups."""
        me = await self.bot.me()
        self.username = me.username

    async def start_polling(self) -> None:
        """Poll in the background; returns once the loop is running."""
        if self._polling is not None:
            return
        self._polling = asyncio.create_task(
            self.dispatcher.start_polling(
                self.bot,
                handle_signals=False,
                close_bot_session=False,
                allowed_updates=ALLOWED_UPDATES,
            ),
            name="telegram.polling",
        )

    async def stop_polling(self) -> None:
        task, self._polling = self._polling, None
        if task is None:
            return
        try:
            await self.dispatcher.stop_polling()
        except RuntimeError:
            # Not polling (startup failed before the loop began); the task
            # below is still awaited so its exception surfaces here.
            pass
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def close(self) -> None:
        """Release the HTTP session; the counterpart of :meth:`initialize`."""
        await self.bot.session.close()

    async def feed(self, update: Update) -> Any:
        """Dispatch one update as polling would; what tests and probes call."""
        return await self.dispatcher.feed_update(self.bot, update)

    # -- contexts -----------------------------------------------------------

    def context(
        self, user_id: int | None, chat_id: int | None, args: list[str] = ()
    ) -> TgContext:
        return TgContext(
            bot=self.bot,
            application=self,
            user_data=self.store.get(user_id) if user_id is not None else None,
            chat_data=self.store.chat(chat_id) if chat_id is not None else None,
            args=args,
        )

    def _context_for(self, update: TgUpdate, args: list[str] = ()) -> TgContext:
        user = update.effective_user
        chat = update.effective_chat
        return self.context(user.id if user else None, chat.id if chat else None, args)

    # -- trampolines ----------------------------------------------------------

    def _install(self) -> None:
        dp = self.dispatcher

        # aiogram passes the enclosing ``Update`` to any handler that asks for
        # ``event_update``; it is kept on the TgUpdate for logging and tests.

        @dp.message()
        async def _on_message(message: Message, event_update: Update) -> None:
            await self._dispatch_message(message, event_update, get_routes())

        @dp.callback_query()
        async def _on_callback(query: CallbackQuery, event_update: Update) -> None:
            handler = get_routes().callback_for(query.data)
            if handler is None:
                return
            update = TgUpdate(callback_query=query, raw=event_update)
            await handler(update, self._context_for(update))

        @dp.stopped_message_generation()
        async def _on_stopped(event: StoppedGeneration, event_update: Update) -> None:
            handler = get_routes().stopped_generation
            if handler is None:
                return
            update = TgUpdate(stopped_message_generation=event, raw=event_update)
            await handler(update, self._context_for(update))

        @dp.errors()
        async def _on_error(event: ErrorEvent) -> bool:
            if self.error_handler is None:
                log.exception(
                    "Exception while handling an update:", exc_info=event.exception
                )
                return True
            update = TgUpdate.from_aiogram(event.update) if event.update else None
            context = TgContext(bot=self.bot, application=self, error=event.exception)
            await self.error_handler(update, context)
            return True

    async def _dispatch_message(
        self, message: Message, raw: Update, routes: RouteTable
    ) -> None:
        if message.from_user is None or message.chat.type not in _ROUTED_CHAT_TYPES:
            return
        update = TgUpdate(message=message, raw=raw)
        name = command_name(message, self.username)
        if name is not None:
            handler = routes.commands.get(name)
            if handler is not None:
                await handler(update, self._context_for(update, command_args(message)))
            return
        if routes.message is None or is_service_message(message):
            return
        await routes.message(update, self._context_for(update))
