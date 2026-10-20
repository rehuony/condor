"""The Telegram types the project uses, named in one place.

Handlers import keyboards, media, commands and update types from here rather
than from aiogram, so the library is imported from ``condor/telegram/`` and
nowhere else. Keyboard markup, commands and media are pydantic models and take
keyword arguments only (``InlineKeyboardButton(text=..., callback_data=...)``,
``InlineKeyboardMarkup(inline_keyboard=rows)``, ``BotCommand(command=...,
description=...)``).

``Update`` and ``Context`` are the project's own
:class:`~condor.telegram.update.TgUpdate` and
:class:`~condor.telegram.update.TgContext`: the pair every handler receives.
``Message``, ``CallbackQuery``, ``Chat`` and ``User`` are aiogram's, which is
what ``update.message`` and ``update.callback_query`` hold.
"""

from aiogram.types import BotCommand as BotCommand
from aiogram.types import BotCommandScopeAllGroupChats as BotCommandScopeAllGroupChats
from aiogram.types import (
    BotCommandScopeAllPrivateChats as BotCommandScopeAllPrivateChats,
)
from aiogram.types import BotCommandScopeChat as BotCommandScopeChat
from aiogram.types import BotCommandScopeDefault as BotCommandScopeDefault
from aiogram.types import BufferedInputFile as BufferedInputFile
from aiogram.types import CallbackQuery as CallbackQuery
from aiogram.types import Chat as Chat
from aiogram.types import InlineKeyboardButton as InlineKeyboardButton
from aiogram.types import InlineKeyboardMarkup as InlineKeyboardMarkup
from aiogram.types import InputMediaPhoto as InputMediaPhoto
from aiogram.types import LinkPreviewOptions as LinkPreviewOptions
from aiogram.types import Message as Message
from aiogram.types import User as User

from .update import TgContext as Context
from .update import TgUpdate as Update

#: Link previews off, the way every plain-text notice asks for it.
NO_LINK_PREVIEW = LinkPreviewOptions(is_disabled=True)

__all__ = [
    "BotCommand",
    "BotCommandScopeAllGroupChats",
    "BotCommandScopeAllPrivateChats",
    "BotCommandScopeChat",
    "BotCommandScopeDefault",
    "BufferedInputFile",
    "CallbackQuery",
    "Chat",
    "Context",
    "InlineKeyboardButton",
    "InlineKeyboardMarkup",
    "InputMediaPhoto",
    "LinkPreviewOptions",
    "Message",
    "NO_LINK_PREVIEW",
    "Update",
    "User",
]
