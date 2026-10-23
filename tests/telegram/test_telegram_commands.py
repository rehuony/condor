"""The Telegram command menu advertises only routed commands, per scope."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from condor.telegram.routes import get_routes
from condor.telegram.types import BotCommandScopeChat, BotCommandScopeDefault
from handlers import commands
from handlers.routes import register_handlers


def test_every_menu_entry_is_routed_and_fits_one_row():
    register_handlers()
    entries = commands.PUBLIC_COMMANDS + commands.ADMIN_COMMANDS
    names = [name for name, _ in entries]

    assert len(names) == len(set(names))
    assert set(names) <= set(get_routes().commands)
    assert all(len(text) <= 22 for _, text in entries)


def test_admin_chat_gets_public_menu_plus_admin_commands(monkeypatch):
    monkeypatch.setattr("utils.config.ADMIN_USER_ID", 111)
    bot = SimpleNamespace(delete_my_commands=AsyncMock(), set_my_commands=AsyncMock())

    asyncio.run(commands.register_bot_commands(bot))

    published = {
        type(call.kwargs.get("scope", BotCommandScopeDefault())): [
            c.command for c in call.args[0]
        ]
        for call in bot.set_my_commands.await_args_list
    }
    public = [name for name, _ in commands.PUBLIC_COMMANDS]
    admin = [name for name, _ in commands.ADMIN_COMMANDS]
    assert published[BotCommandScopeDefault] == public
    assert published[BotCommandScopeChat] == public + admin
    assert bot.delete_my_commands.await_count == 4
