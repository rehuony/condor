"""The Telegram command menu: what a chat lists after typing ``/``.

The tuples below are the menu, in display order: the general commands a new
user meets first, then feature groups by how often they are used. Descriptions
stay short (about 22 characters at most) so a phone shows each row without
truncation. A command listed here must also be routed in
:mod:`handlers.routes`; the menu only advertises, it never dispatches.

This module lives under ``handlers/`` so the file watcher reloads it: an edit
here reaches the menu without a process restart.
"""

import logging

from condor.telegram.types import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    BotCommandScopeDefault,
)

logger = logging.getLogger(__name__)

#: Everyone's menu (default scope).
PUBLIC_COMMANDS: tuple[tuple[str, str], ...] = (
    # General: what a first-time user reaches for
    ("start", "View command guide"),
    ("cancel", "Cancel pending input"),
    ("web", "Open web dashboard"),
    # Trading
    ("portfolio", "View balances and PnL"),
    ("trade", "Trade on CEX and DEX"),
    ("lp", "Manage liquidity pools"),
    # AI assistant
    ("agent", "Manage AI chats"),
    ("stop", "Stop and clear queue"),
    ("delegations", "Track delegated tasks"),
    ("memory", "Manage AI memory"),
    # Strategies and automation
    ("executors", "Manage trade executors"),
    ("bots", "Manage trading bots"),
    ("new_bot", "Create bot configs"),
    ("routines", "Run or schedule tasks"),
    # Setup
    ("servers", "Manage API servers"),
    ("keys", "Manage exchange keys"),
    ("gateway", "Manage DEX gateway"),
)

#: Appended to the public menu in the admin's private chat.
ADMIN_COMMANDS: tuple[tuple[str, str], ...] = (
    ("admin", "Manage users and roles"),
    ("update", "Check for updates"),
)


def _menu(entries: tuple[tuple[str, str], ...]) -> list[BotCommand]:
    return [BotCommand(command=name, description=text) for name, text in entries]


async def register_bot_commands(bot) -> None:
    """Publish the public menu for everyone and the admin overlay."""
    from utils.config import ADMIN_USER_ID

    # Clear any previously set commands for all scopes to avoid stale overrides
    stale_scopes = [
        BotCommandScopeDefault(),
        BotCommandScopeAllPrivateChats(),
        BotCommandScopeAllGroupChats(),
    ]
    if ADMIN_USER_ID:
        stale_scopes.append(BotCommandScopeChat(chat_id=int(ADMIN_USER_ID)))
    for scope in stale_scopes:
        try:
            await bot.delete_my_commands(scope=scope)
        except Exception:
            pass

    # Each step is wrapped on its own so a transient failure in one never blocks
    # the other (or the rest of startup).
    try:
        await bot.set_my_commands(_menu(PUBLIC_COMMANDS))
    except Exception as e:
        logger.warning(f"Failed to set public commands: {e}", exc_info=True)

    # A chat scope overrides the default one, so the admin's menu repeats the
    # public commands before adding its own.
    if ADMIN_USER_ID:
        try:
            await bot.set_my_commands(
                _menu(PUBLIC_COMMANDS + ADMIN_COMMANDS),
                scope=BotCommandScopeChat(chat_id=int(ADMIN_USER_ID)),
            )
        except Exception as e:
            logger.warning(f"Failed to set admin-specific commands: {e}", exc_info=True)
