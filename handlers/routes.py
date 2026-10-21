"""Which handler answers each Telegram command, callback and message.

The command menu users see lives next door in :mod:`handlers.commands`; every
command listed there must be routed here.
"""

import logging

from condor.telegram.routes import RouteTable, set_routes

logger = logging.getLogger(__name__)


def register_handlers() -> None:
    """Build the route table from the current handler modules and install it.

    Called at boot and after every hot reload: the dispatcher's fixed
    trampolines read the table at dispatch time, so installing a new one is
    all a reload needs.
    """
    # Import fresh versions after reload
    from condor.telegram.wake import register_wake
    from handlers import cancel_command
    from handlers.admin import admin_callback_handler, admin_command
    from handlers.admin.update import update_command
    from handlers.agents import (
        agent_callback_handler,
        agent_command,
        stop_command,
    )
    from handlers.bots import bots_callback_handler, bots_command, new_bot_command
    from handlers.cex import cex_callback_handler
    from handlers.config import CONFIG_CALLBACK_PATTERN, config_callback_handler
    from handlers.config.api_keys import keys_command
    from handlers.config.gateway import gateway_command
    from handlers.config.servers import servers_command
    from handlers.delegations import delegations_callback_handler, delegations_command
    from handlers.dex import dex_callback_handler, lp_command
    from handlers.executors import executors_callback_handler, executors_command
    from handlers.memory import memory_callback_handler, memory_command
    from handlers.messages import message_handler, track_input_chat
    from handlers.portfolio import portfolio_callback_handler, portfolio_command
    from handlers.routines import routines_callback_handler, routines_command
    from handlers.start import start_callback_handler, start_command
    from handlers.trade_cards import trade_cards_callback_handler
    from handlers.trading import trade_command as unified_trade_command
    from handlers.trading.router import unified_trade_callback_handler
    from handlers.web import web_command

    routes = RouteTable()

    routes.command("start", start_command)
    routes.command("portfolio", portfolio_command)
    routes.command("bots", bots_command)
    routes.command("new_bot", new_bot_command)
    routes.command("trade", unified_trade_command)  # Unified trade (CEX + DEX)
    routes.command("swap", unified_trade_command)  # Alias for /trade
    routes.command("lp", lp_command)
    routes.command("routines", routines_command)
    routes.command("executors", executors_command)
    routes.command("agent", agent_command)
    # Stop the chat's current requests and clear its already waiting inputs.
    routes.command("stop", stop_command)
    routes.command("delegations", delegations_command)
    routes.command("memory", memory_command)
    # Universal escape hatch for flows that arm a "next message is the answer"
    # input mode. Every such prompt advertises /cancel.
    routes.command("cancel", cancel_command)

    # Configuration commands (direct access)
    routes.command("servers", servers_command)
    routes.command("keys", keys_command)
    routes.command("gateway", gateway_command)
    routes.command("admin", admin_command)
    routes.command("update", update_command)
    routes.command("web", web_command)

    # Callback patterns are tried in this order; the first match wins.
    routes.callback("^start:", start_callback_handler)
    # Unified trade BEFORE cex/dex (for connector switching)
    routes.callback("^trade:", unified_trade_callback_handler)
    routes.callback("^tc:", trade_cards_callback_handler)
    routes.callback("^cex:", cex_callback_handler)
    routes.callback("^dex:", dex_callback_handler)
    routes.callback("^bots:", bots_callback_handler)
    routes.callback("^routines:", routines_callback_handler)
    routes.callback("^executors:", executors_callback_handler)
    routes.callback("^agent:", agent_callback_handler)
    routes.callback("^deleg:", delegations_callback_handler)
    routes.callback("^memory:", memory_callback_handler)
    routes.callback("^admin:", admin_callback_handler)
    routes.callback("^portfolio:", portfolio_callback_handler)
    routes.callback(CONFIG_CALLBACK_PATTERN, config_callback_handler)

    # One content router owns form priority and multimodal agent input.
    # Bind any input a command or button arms to the chat it was armed in.
    routes.commands = {
        name: track_input_chat(handler) for name, handler in routes.commands.items()
    }
    routes.callbacks = [
        (pattern, track_input_chat(handler)) for pattern, handler in routes.callbacks
    ]

    routes.message = message_handler
    register_wake()

    set_routes(routes)
    logger.info("Handlers registered successfully")
