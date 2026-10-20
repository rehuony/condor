import asyncio
import importlib
import logging
import os
import sys
from functools import partial
from pathlib import Path
from urllib.parse import urlparse

from condor import paths
from condor.scheduler import get_scheduler
from condor.telegram.app import TelegramApp
from condor.telegram.errors import BadRequest, NetworkFailure, Unauthorized
from condor.telegram.routes import RouteTable, set_routes
from condor.telegram.state import UserStateStore
from condor.telegram.transport import close_standalone_bot
from condor.telegram.types import (
    NO_LINK_PREVIEW,
    BotCommand,
    Context,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from handlers import cancel_command, clear_all_input_states
from utils.auth import restricted
from utils.config import (
    LOCAL_MODE,
    TELEGRAM_TOKEN,
    USE_TAILSCALE,
    WEB_HOST,
    WEB_PORT,
    WEB_URL,
    ConfigError,
    check_local_user,
    check_startup_config,
    warn_shadowed_env,
)

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# aiogram logs one INFO line per handled update ("Update id=... is handled"),
# which is noise next to the handlers' own logging; real dispatch problems
# arrive as warnings and errors and still get through. httpx is used by the
# Hummingbot API client and logs each request URL at INFO.
logging.getLogger("aiogram.event").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)


def _dashboard_button_url(url: str) -> str | None:
    """The dashboard link Telegram will accept in an inline URL button, if any.

    Telegram rejects ``localhost`` outright ("Wrong HTTP URL") but accepts the
    loopback IP, so the local default (``http://localhost:8088``) still gets a
    button by swapping the host — which is what a Telegram client running on
    the same machine needs anyway. Any other dotless hostname (a bare LAN name)
    has no such equivalent, so those keep the copy-paste link only.
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if host == "localhost":
        parsed = parsed._replace(netloc=parsed.netloc.replace(host, "127.0.0.1", 1))
        return parsed.geturl()
    if "." not in host:
        return None
    return url


@restricted
async def web_command(update: Update, context: Context) -> None:
    """Generate a one-time login link for the web dashboard."""
    from condor.web.auth import create_login_token

    user = update.effective_user
    token = create_login_token(user.id, user.username or "", user.first_name or "")

    url = f"{WEB_URL}/login?token={token}"
    _hostname = urlparse(WEB_URL).hostname or ""
    is_localhost = (
        "localhost" in WEB_URL or "127.0.0.1" in WEB_URL or "." not in _hostname
    )

    button_url = _dashboard_button_url(url)
    keyboard = (
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🌐 Open Dashboard", url=button_url)]
            ]
        )
        if button_url
        else None
    )

    if is_localhost:
        # A loopback button only opens on the machine running the bot, so the
        # raw link stays visible for a phone on the same network to adapt.
        text = (
            f"🌐 *Web Dashboard*\n\n"
            f"Open this link in your browser:\n`{url}`\n\n"
            f"_Link valid for 5 minutes\\._"
        )
    else:
        text = (
            "🌐 *Web Dashboard*\n\n"
            "Tap the button below to open the dashboard\\.\n"
            "_Link valid for 5 minutes\\._"
        )

    try:
        await update.message.answer(
            text, reply_markup=keyboard, parse_mode="MarkdownV2"
        )
    except BadRequest:
        # Telegram refuses URL buttons it can't parse (host without a public
        # TLD, an unusual scheme). Losing the button must not lose the link.
        if keyboard is None:
            raise
        await update.message.answer(
            f"🌐 *Web Dashboard*\n\n"
            f"Open this link in your browser:\n`{url}`\n\n"
            f"_Link valid for 5 minutes\\._",
            parse_mode="MarkdownV2",
        )


async def start(update: Update, context: Context) -> None:
    """Start the conversation and display available commands (BotFather style)."""
    from config_manager import UserRole, get_config_manager
    from utils.auth import _capture_identity, _notify_admin_new_user

    tg_user = update.effective_user
    user_id = tg_user.id
    username = tg_user.username or ""
    # What to print where the reply says "Username:". An absent handle is shown
    # as absent rather than stored as the "No username" string the record used
    # to carry (FEAT-088) — the sentinel was a display default that leaked into
    # config.yml and became a name the admin panel then had to render.
    handle_display = f"@{username}" if username else "not set"

    cm = get_config_manager()
    role = cm.get_user_role(user_id)

    # Handle blocked users
    if role == UserRole.BLOCKED:
        await update.message.answer("Access denied.")
        return

    # Handle pending users
    if role == UserRole.PENDING:
        reply_text = f"""Access Pending

Your access request is awaiting admin approval.

Your Info:
User ID: {user_id}
Username: {handle_display}

You will be notified when approved."""
        await update.message.answer(reply_text)
        return

    # Handle new users - register as pending
    if role is None:
        is_new = cm.register_pending(
            user_id,
            username,
            first_name=tg_user.first_name,
            last_name=tg_user.last_name,
        )
        if is_new:
            await _notify_admin_new_user(context, user_id, username)

        reply_text = f"""Access Request Submitted

Your request has been sent to the admin for approval.

Your Info:
User ID: {user_id}
Username: {handle_display}

You will be notified when approved."""
        await update.message.answer(reply_text)
        return

    # User is approved (USER or ADMIN role)
    _capture_identity(update)
    clear_all_input_states(context)

    reply_text = """I can help you create and manage trading bots on any CEX or DEX using Hummingbot API servers\\.

See [this manual](https://github.com/rehuony/condor#readme) if you're new to Condor\\.

You can control me by sending these commands:

/keys \\- add exchange API keys
/portfolio \\- view balances across exchanges
/bots \\- deploy and manage trading bots
/trade \\- place CEX and DEX orders
/agent \\- AI trading assistant
/web \\- open the web dashboard"""

    await update.message.answer(
        reply_text, parse_mode="MarkdownV2", link_preview_options=NO_LINK_PREVIEW
    )


@restricted
async def start_callback_handler(update: Update, context: Context) -> None:
    """Handle callbacks from the start menu."""
    query = update.callback_query
    await query.answer()

    data = query.data
    action = data.split(":")[1] if ":" in data else data

    # Handle cancel - delete the message
    if action == "cancel":
        await query.message.delete()
        return

    # Handle navigation to config options
    if data.startswith("start:"):
        if action == "config_servers":
            from handlers import clear_all_input_states
            from handlers.config.servers import show_api_servers

            clear_all_input_states(context)
            await show_api_servers(query, context)
        elif action == "config_keys":
            from handlers import clear_all_input_states
            from handlers.config.api_keys import show_api_keys

            clear_all_input_states(context)
            await show_api_keys(query, context)
        elif action == "config_gateway":
            from handlers import clear_all_input_states
            from handlers.config.gateway import show_gateway_menu

            clear_all_input_states(context)
            context.user_data.pop("dex_state", None)
            context.user_data.pop("cex_state", None)
            await show_gateway_menu(query, context)
        elif action == "admin":
            from handlers import clear_all_input_states
            from handlers.admin import _show_admin_menu

            clear_all_input_states(context)
            await _show_admin_menu(query, context)


# Modules the handlers import from that do not live under ``handlers/``, and
# that are safe to re-execute. Everything under ``handlers/`` itself is
# discovered by :func:`_discover_handler_modules`.
#
# ``routines/`` is deliberately absent: routines have their own mtime-aware
# discovery in ``routines.base.discover_routines(force_reload=True)``, which
# owns reimporting individual routine modules. Only the base module is listed,
# so that machinery itself stays fresh.
_EXTRA_RELOAD_MODULES = (
    "config_manager",
    "utils.auth",
    "utils.telegram_formatters",
    "routines.base",
)


def _discover_handler_modules() -> list[str]:
    """Every module under ``handlers/``, children before parents.

    Derived rather than hand-listed. A hand-maintained list drifts, and it
    drifts *silently*: ``reload_handlers`` skips any name not in
    ``sys.modules``, so a stale entry is a no-op and a missing entry means the
    watcher logs a successful reload while the running bot keeps the old code.
    That is worse than no hot-reload, because it reports success. The list this
    replaced named three ``handlers.dex.swap_*`` modules that no longer exist
    and omitted six that do, including ``handlers.dex.router``.

    Naming every module matters because ``importlib.reload`` is NOT recursive:
    reloading ``handlers.dex`` re-executes its ``__init__``, but its
    ``from .router import ...`` re-binds the *cached* ``handlers.dex.router``,
    so an edit there is missed unless that module is reloaded in its own right.

    For the same reason children sort before parents: by the time a package's
    ``__init__`` re-executes, the submodules it imports from have already been
    refreshed.
    """
    root = Path(__file__).parent / "handlers"
    modules: set[str] = set()
    for path in root.rglob("*.py"):
        parts = path.relative_to(root.parent).with_suffix("").parts
        # Per-agent runtime stores can sit under a watched tree (FEAT-003) and
        # are data, not code — the file watcher skips them for the same reason.
        if "__pycache__" in parts or "store" in parts:
            continue
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            modules.add(".".join(parts))
    return sorted(modules, key=lambda name: (-name.count("."), name))


def reload_handlers():
    """Reload all handler modules."""
    # NOTE: no "condor.runtime.*" module belongs here. The runtime holds live
    # subprocess handles (agent sessions); re-executing those modules resets
    # the registry and silently orphans every running agent. The discovery
    # above only walks handlers/, so it cannot pull them in.
    modules_to_reload = [*_EXTRA_RELOAD_MODULES, *_discover_handler_modules()]

    reloaded = 0
    for module_name in modules_to_reload:
        if module_name in sys.modules:
            importlib.reload(sys.modules[module_name])
            logger.debug(f"Reloaded module: {module_name}")
            reloaded += 1
    logger.info(f"Reloaded {reloaded} modules")

    # Re-register fetch functions after reload (preserves in-memory cache)
    try:
        from condor.server_data_service import register_default_fetches as sds_register

        sds_register()
    except Exception as e:
        logger.warning(f"Failed to re-register SDS fetches: {e}")


def register_handlers(application: TelegramApp) -> None:
    """Build the route table from the current handler modules and install it.

    Called at boot and after every hot reload: the dispatcher's fixed
    trampolines read the table at dispatch time, so installing a new one is
    all a reload needs.
    """
    # Import fresh versions after reload
    from condor.telegram.wake import register_wake
    from handlers.admin import admin_callback_handler, admin_command
    from handlers.admin.update import update_command
    from handlers.agents import (
        agent_callback_handler,
        agent_command,
        handle_stopped_generation,
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
    from handlers.messages import message_handler
    from handlers.portfolio import portfolio_callback_handler, portfolio_command
    from handlers.routines import routines_callback_handler, routines_command
    from handlers.trading import trade_command as unified_trade_command
    from handlers.trading.router import unified_trade_callback_handler

    routes = RouteTable()

    routes.command("start", start)
    routes.command("portfolio", portfolio_command)
    routes.command("bots", bots_command)
    routes.command("new_bot", new_bot_command)
    routes.command("trade", unified_trade_command)  # Unified trade (CEX + DEX)
    routes.command("swap", unified_trade_command)  # Alias for /trade
    routes.command("lp", lp_command)
    routes.command("routines", routines_command)
    routes.command("executors", executors_command)
    routes.command("agent", agent_command)
    # Interrupts the answer in flight without touching the session — the
    # dashboard's Stop button, for a surface that has no buttons while streaming.
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
    routes.message = message_handler
    routes.stopped_generation = handle_stopped_generation
    register_wake()

    set_routes(routes)
    logger.info("Handlers registered successfully")


async def sync_server_permissions() -> None:
    """
    Ensure all servers in config have permission entries.
    Registers any unregistered servers with admin as owner.
    """
    from config_manager import get_config_manager

    cm = get_config_manager()
    for server_name in cm.list_servers():
        cm.ensure_server_registered(server_name)

    logger.info("Synced server permissions")


async def register_bot_commands(application: TelegramApp) -> None:
    """Register the Telegram command menus (public for everyone, admin overlay).

    Extracted from ``startup`` so it can also run on hot-reload — otherwise a
    newly added command (e.g. /delegations) gets its dispatch handler reloaded
    but never shows up in the menu until a full process restart.
    """
    from condor.telegram.types import (
        BotCommandScopeAllGroupChats,
        BotCommandScopeAllPrivateChats,
        BotCommandScopeChat,
        BotCommandScopeDefault,
    )
    from utils.config import ADMIN_USER_ID

    # Clear any previously set commands for all scopes to avoid stale overrides
    for scope in [
        BotCommandScopeDefault(),
        BotCommandScopeAllPrivateChats(),
        BotCommandScopeAllGroupChats(),
    ]:
        try:
            await application.bot.delete_my_commands(scope=scope)
        except Exception:
            pass

    if ADMIN_USER_ID:
        try:
            await application.bot.delete_my_commands(
                scope=BotCommandScopeChat(chat_id=int(ADMIN_USER_ID))
            )
        except Exception:
            pass

    # 1) Public commands — registered by default for ALL users (default scope is
    #    the universal fallback every user resolves to unless a more specific
    #    scope overrides it). Wrapped independently so a transient failure here
    #    never blocks the admin step (or the rest of startup) from running.
    commands = [
        BotCommand(command="start", description="Welcome message and setup"),
        BotCommand(command="portfolio", description="View balances across exchanges"),
        BotCommand(command="agent", description="AI trading assistant"),
        BotCommand(command="stop", description="Stop the answer being generated"),
        BotCommand(command="delegations", description="Monitor background agent tasks"),
        BotCommand(
            command="memory",
            description="Review what the assistant remembers about you",
        ),
        BotCommand(
            command="executors", description="Deploy and manage trading executors"
        ),
        BotCommand(command="bots", description="Deploy and manage trading bots"),
        BotCommand(command="new_bot", description="Create bot configurations"),
        BotCommand(command="routines", description="Run configurable Python scripts"),
        BotCommand(command="trade", description="Place CEX and DEX orders"),
        BotCommand(command="lp", description="Liquidity pool management"),
        BotCommand(command="servers", description="Manage Hummingbot API servers"),
        BotCommand(command="keys", description="Configure exchange API credentials"),
        BotCommand(command="gateway", description="Gateway for DEX trading"),
        BotCommand(command="web", description="Open the web dashboard"),
        BotCommand(command="cancel", description="Abort the current input flow"),
    ]
    try:
        await application.bot.set_my_commands(commands)
    except Exception as e:
        logger.warning(f"Failed to set public commands: {e}", exc_info=True)

    # 2) Admin-only commands — layered on top of the public ones, visible only in
    #    the admin user's own command menu (chat scope overrides the default).
    if ADMIN_USER_ID:
        admin_commands = commands + [
            BotCommand(
                command="admin", description="Admin panel - manage users and access"
            ),
            BotCommand(command="update", description="Check for and install updates"),
        ]
        try:
            await application.bot.set_my_commands(
                admin_commands, scope=BotCommandScopeChat(chat_id=int(ADMIN_USER_ID))
            )
        except Exception as e:
            logger.warning(f"Failed to set admin-specific commands: {e}", exc_info=True)


async def _notify_interrupted_runs(bot, report) -> None:
    """Tell each owner, once, what the restart found.

    One summary per chat rather than a message per run: a crash with several
    live loops would otherwise spam the user at the worst possible moment.
    """
    from condor.notifications import announce, user_for_chat

    by_chat: dict[int, list] = {}
    for run in report.interrupted:
        status = None
        try:
            from condor.runtime.registry_file import read_status

            status = read_status(run.session_dir)
        except Exception:
            pass
        chat_id = (status or {}).get("chat_id")
        if chat_id:
            by_chat.setdefault(int(chat_id), []).append(run)

    for chat_id, runs in by_chat.items():
        lines = [f"Found {len(runs)} interrupted run(s) after restart:"]
        for run in runs:
            suffix = " — restarted" if run.restarted else ""
            lines.append(f"• {run.label} (last tick {run.last_tick}){suffix}")
        text = "\n".join(lines)
        # Telegram and the bell (FEAT-048) in one call: ``announce`` resolves
        # the sender once and files the notice exactly once, including when the
        # sender *is* the bell (local mode, FEAT-049). A private chat id is the
        # owner's user id; a group has no dashboard owner, so a group summary is
        # simply not filed anywhere.
        try:
            await announce(
                user_for_chat(chat_id), chat_id, text, kind="system", bot=bot
            )
        except Exception:
            logger.warning("Could not notify chat %s about interrupted runs", chat_id)


def _outbound_bot(application: TelegramApp):
    """The object this process sends user-facing messages through.

    Telegram mode: the real bot, exactly as before. Local mode: there is no
    Telegram behind the placeholder token, so outbound messages go to the
    dashboard bell instead (:class:`condor.notifications.NotifyBot`, FEAT-048).
    Everything downstream — the routine store, the session health monitor, the
    interrupted-run summaries — keeps calling ``send_message`` and neither knows
    nor cares which surface it reached.
    """
    if not LOCAL_MODE:
        return application.bot
    from condor.notifications import NotifyBot

    return NotifyBot()


# Strong references to fire-and-forget boot tasks. asyncio only holds a weak
# one, so a task nobody awaits can be garbage-collected mid-flight; keeping the
# handle here (and discarding it on completion) is the documented fix.
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _spawn_background(coro, label: str) -> asyncio.Task:
    """Run ``coro`` off the critical path, logging rather than swallowing errors.

    For work the process wants done soon but must not wait for — cache warm-up
    being the case that used to gate the uvicorn bind behind a dead server's
    connect timeouts.
    """
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)

    def _report(t: asyncio.Task) -> None:
        if not t.cancelled() and t.exception() is not None:
            logger.error("%s failed: %s", label, t.exception())

    task.add_done_callback(_report)
    return task


async def startup(application: TelegramApp) -> None:
    """Bring the process up: commands, caches, supervisors, boot reconciliation.

    :func:`_run_dual` drives the lifecycle by hand (``initialize`` → this →
    ``start_polling``) so it can run uvicorn alongside the bot. Called
    explicitly from there, before the first update is served.
    """
    # First, before anything reads a conversation or reconciles a delegation:
    # settle where the runtime store lives (FEAT-051). Idempotent, so this is a
    # no-op on every boot after the first; it is a named public function rather
    # than inline code because a second entry point (a CLI, a worker) would have
    # to call it too.
    from condor.migrations import ensure_migrated

    ensure_migrated()

    if _STATE_STORE is not None:
        await _STATE_STORE.start(interval=10)

    # Timed work (scheduled routines, update checks, reflection) runs on our
    # own scheduler, in both modes; jobs registered below are picked up by it.
    get_scheduler().start()

    # Sync server permissions (ensures all servers have ownership entries)
    await sync_server_permissions()

    # Whisper is loaded lazily on the first audio message. Text/image-only
    # installations do not need an idle speech model or a startup download.

    # Same reason, different work: bringing the backtest archive to its tiered
    # v2 layout (FEAT-075) parses and re-compresses every saved payload, which
    # is minutes on a store measured in gigabytes. It is deliberately NOT part
    # of ``ensure_migrated`` above -- that one runs before the first update is
    # served. The archive degrades honestly while this runs (summaries report
    # ``status: unknown`` and the dashboard says "indexing"), so the tab stays
    # usable throughout, and the work is per-file and idempotent so a crash
    # resumes rather than restarts.
    from condor.backtest_store import migrate_backtest_archive

    asyncio.get_event_loop().run_in_executor(None, migrate_backtest_archive)

    # Whatever this process pushes at users from here on. In local mode there is
    # no Telegram to push to, so it is the dashboard bell (FEAT-048) instead —
    # which is what keeps the documented "context.bot is never None" contract
    # true for routines with no bot behind them.
    outbound_bot = _outbound_bot(application)

    # Register command menus (public + admin overlay). Pure Telegram: there is
    # no command menu to publish when nothing polls.
    if not LOCAL_MODE:
        await register_bot_commands(application)

    # Restore scheduled routine jobs from persistence
    from handlers.routines import restore_scheduled_jobs

    await restore_scheduled_jobs(application)

    # Inject Telegram bot into routine store so web-triggered routines can send messages
    from condor.routine_store import get_routine_store

    get_routine_store().set_bot(outbound_bot)

    # Start ServerDataService (unified server-centric cache)
    from condor.server_data_service import get_server_data_service
    from condor.server_data_service import register_default_fetches as sds_register

    sds_register()
    sds = get_server_data_service()
    sds.start()
    # Warm the cache in the background, never on the critical path: every
    # subscription's initial fetch opens a client, and client creation is
    # serialized per server (ConfigManager._client_locks), so one unreachable
    # server used to hold the Telegram start and the uvicorn bind behind a
    # queue of connect timeouts. A cold read degrades to what a down server
    # already returns (None) and the poll loop fills it in; a reachable server
    # is warm within a second either way.
    _spawn_background(sds.auto_subscribe_servers(), "SDS auto-subscribe")

    # Ride the ticker-pool poll the SDS just started: an hourly price snapshot
    # per server is the only source of 24h change on the CLOB side, and it costs
    # no upstream request (FEAT-053).
    from condor import ticker_history

    ticker_history.install_listener()

    # Start agent session health monitor. The health monitor is process
    # lifecycle, not a session operation, so it is driven off the module
    # directly rather than through the client facade.
    from condor.runtime import sessions as runtime_sessions
    from condor.runtime.confirmations import get_registry

    await runtime_sessions.start_health_monitor(outbound_bot)
    # Sweeps expired approvals so a request nobody answers is denied, not leaked.
    await get_registry().start()

    # Settle whatever the previous process left running: mark orphaned loops
    # interrupted, restart only the ones that opted in, and tell the owner once.
    from condor.runtime.loops import get_supervisor

    try:
        report = await get_supervisor().reconcile_boot()
        if report.total:
            logger.warning(
                "Boot reconciliation: %d interrupted, %d restarted",
                report.total,
                len(report.restarted),
            )
            await _notify_interrupted_runs(outbound_bot, report)
    except Exception:
        logger.exception("Boot reconciliation failed; continuing startup")

    # Schedule periodic update checks (notifies admin)
    from handlers.admin.update import schedule_update_checks

    schedule_update_checks(outbound_bot)

    # Read finished conversations for local memory and intent extraction.
    # Disabled when CONDOR_REFLECTION=off.
    try:
        from condor.agents import reflection

        reflection.register_jobs()
    except Exception:
        logger.exception("Reflection job registration failed (continuing without it)")

    # Start file watcher
    asyncio.create_task(watch_and_reload(application))


async def _close_telegram_input() -> None:
    """Close ingress and shared media workers in either application mode."""
    from condor.telegram.ingress import close_ingress
    from condor.telegram.input import close_resolver

    await close_ingress()
    await close_resolver()
    from utils.transcribe import close_transcriber

    await close_transcriber()


async def teardown(application: TelegramApp) -> None:
    """Wind the process down: stop supervisors, flush state, close clients.

    The counterpart to :func:`startup`; called explicitly from :func:`_run_dual`.
    """
    from condor.runtime import client as runtime
    from condor.runtime import sessions as runtime_sessions
    from condor.runtime.confirmations import get_registry

    get_scheduler().shutdown()
    await runtime_sessions.stop_health_monitor()
    await get_registry().stop()
    await runtime.destroy_all()

    # Stop all trading agents. Graceful stop, deliberately NOT the shutdown
    # sequence — winding down positions is an emergency action, not what a
    # restart should do. Each engine records its final state on the way out.
    from condor.runtime.conversations import flush_all as flush_conversations
    from condor.runtime.loops import get_supervisor
    from condor.runtime.state import flush_all

    await get_supervisor().stop_all()
    # Writes are debounced, so force the last one out on a clean shutdown.
    flush_all()
    # A prompt still streaming when the bot went down holds its turn in
    # memory; write it out rather than losing the last thing that was said.
    flush_conversations()
    if _STATE_STORE is not None:
        await _STATE_STORE.close()

    # Stop WebSocket manager
    from condor.web.ws_manager import get_ws_manager

    get_ws_manager().stop()

    # Stop ServerDataService
    from condor.server_data_service import get_server_data_service

    get_server_data_service().stop()

    # Close cached Hummingbot API clients (ConfigManager)
    from config_manager import get_config_manager

    await get_config_manager().close_all_clients()

    # Close MCP hummingbot client
    from mcp_servers.hummingbot_api.hummingbot_client import hummingbot_client

    await hummingbot_client.close()


async def watch_and_reload(application: TelegramApp) -> None:
    """Watch for file changes and reload handlers automatically."""
    try:
        from watchfiles import DefaultFilter, awatch
    except ImportError:
        logger.warning(
            "watchfiles not installed. Auto-reload disabled. Install with: uv add watchfiles"
        )
        return

    handlers_path = Path(__file__).parent / "handlers"
    routines_path = Path(__file__).parent / "routines"
    # ``agents/`` is deliberately NOT watched: AgentStore reads AGENT.md from
    # disk on every call, so there is no cache to bust (the assistant cache that
    # justified watching it is gone with FEAT-033), while agents write journals
    # and strategy state under the same tree — watching it would thrash reloads.
    watch_paths = [handlers_path, routines_path]
    logger.info(f"👀 Watching for changes in: {', '.join(str(p) for p in watch_paths)}")

    class _ReloadFilter(DefaultFilter):
        """Ignore per-agent runtime stores (FEAT-003).

        A store can sit under a watched tree (an agent-owned ``routines/`` dir
        next to its ``store/``), so without this every memory/skill write would
        thrash a full handler reload.
        """

        def __call__(self, change, path: str) -> bool:
            if f"{os.sep}store{os.sep}" in path:
                return False
            return super().__call__(change, path)

    async for changes in awatch(*watch_paths, watch_filter=_ReloadFilter()):
        logger.info(f"📝 Detected changes: {changes}")
        try:
            reload_handlers()
            register_handlers(application)
            # Refresh the Telegram command menus too, so a newly added/removed
            # command shows up without requiring a full process restart.
            await register_bot_commands(application)
            logger.info("✅ Auto-reloaded handlers successfully")
        except Exception as e:
            logger.error(f"❌ Auto-reload failed: {e}", exc_info=True)


# The one store behind ``context.user_data``/``context.chat_data``; set in
# :func:`main` so :func:`startup` and :func:`teardown` can start and close it.
_STATE_STORE: UserStateStore | None = None


def get_state_store() -> UserStateStore:
    """Build the user-state store for this installation.

    - ``$CONDOR_STATE_FILE`` overrides the file; the default is
      ``<data_dir>/condor_state.pickle``, resolved through
      ``condor.paths.data_dir()`` so ``$CONDOR_DATA_DIR`` repoints the whole
      operational store at once rather than this one file.
    """
    data_dir = paths.data_dir()
    path = Path(os.getenv("CONDOR_STATE_FILE", data_dir / "condor_state.pickle"))
    path.parent.mkdir(parents=True, exist_ok=True)
    return UserStateStore(path)


async def error_handler(update: Update | None, context: Context) -> None:
    """Handle errors gracefully."""
    if isinstance(context.error, NetworkFailure):
        # Handler errors land here as well. Only polling retries; an already
        # dispatched update is not replayed by this handler.
        phase = "update" if update is not None else "background"
        # A transport exception can contain a URL with the bot token.
        logger.warning(
            "Telegram network failure stage=%s update_id=%s error_type=%s; "
            "this handler does not retry the update",
            phase,
            getattr(update, "update_id", None),
            type(context.error).__name__,
        )
        return

    logger.exception("Exception while handling an update:", exc_info=context.error)


def main() -> None:
    """Run the bot."""
    # Refuse to start on a configuration that cannot mean what it says: telegram
    # mode (the default) with no token used to surface as an InvalidToken
    # traceback from inside the bot library, and must never be quietly read as
    # "local mode".
    # check_local_user() is the same idea one layer in — local mode logs in with
    # no password, so who it logs in as is settled here, at boot, and not as a
    # 500 from /auth/local-login with the browser already open.
    try:
        check_startup_config()
        check_local_user()
    except ConfigError as exc:
        logger.error("%s", exc)
        raise SystemExit(1)
    warn_shadowed_env()

    # Reap any ACP/MCP subprocess trees orphaned by a prior hard kill (kill -9,
    # OOM, power loss) before we spawn our own — those bypass teardown().
    try:
        from condor.acp.client import reap_stale_acp_trees

        reaped = reap_stale_acp_trees(TELEGRAM_TOKEN)
        if reaped:
            logger.info("Reaped %d stale ACP/MCP process(es) from a prior run", reaped)
    except Exception:
        logger.exception("Startup ACP reaper failed (continuing)")

    # User state (trading context, last used parameters, preferences): loaded
    # here, served as context.user_data by the application, flushed by
    # startup() and teardown().
    global _STATE_STORE
    _STATE_STORE = get_state_store()
    _STATE_STORE.load()

    # In local mode there is no token and nothing polls; the placeholder exists
    # only so the bot object (and with it the handler contexts) can be built at
    # all. Nothing ever calls Telegram with it.
    application = TelegramApp(TELEGRAM_TOKEN or "0:local", _STATE_STORE)

    # Register all handlers
    register_handlers(application)
    application.error_handler = error_handler

    # Run TG bot + web server concurrently in a manual event loop
    try:
        asyncio.run(_run_dual(application))
    finally:
        # /update asks for a restart by signalling a normal shutdown, so the
        # re-exec happens here: after the loop is gone and teardown has flushed
        # state and reaped subprocesses. In a `finally` because a restart is
        # still the right outcome even if a shutdown step blew up on the way
        # out. Replacing the image in place keeps the tmux pane alive.
        from utils.updater import exec_restart, restart_pending

        if restart_pending():
            exec_restart()  # never returns


def _web_server_config(web_app):
    """uvicorn's config for the dashboard, isolated so the bind address is testable.

    ``WEB_HOST`` is ``0.0.0.0`` in telegram mode (unchanged) and loopback in
    local mode, where the dashboard has no login at all, or when Tailscale is
    enabled, where `tailscale serve` is what actually exposes it — see
    :func:`utils.config.resolve_web_host` for why that is not negotiable by
    accident.
    """
    import uvicorn

    return uvicorn.Config(
        web_app,
        host=WEB_HOST,
        port=WEB_PORT,
        log_level="info",
        access_log=False,
    )


async def _run_dual(application: TelegramApp) -> None:
    """Run the Telegram bot and FastAPI web server concurrently."""
    import signal

    import uvicorn

    from condor.web.app import create_app
    from condor.web.ws_manager import get_ws_manager

    # Initialize and start the Telegram application. startup() runs between
    # initialize() and start_polling(), so commands, caches and boot
    # reconciliation are settled before the first update is dispatched.
    #
    # Local mode skips that lifecycle rather than faking it (FEAT-049): the
    # application is built but never initialized, so nothing polls and no
    # handler can dispatch — the Telegram surface is inert, not mocked.
    # startup() starts the scheduler directly, which is all scheduled routines,
    # update checks and signals actually need.
    if LOCAL_MODE:
        await startup(application)
    else:
        try:
            await application.initialize()
        except Unauthorized:
            # Nothing else has started; release the session and name the
            # cause instead of leaving a traceback and an unclosed connector.
            await application.close()
            raise SystemExit(
                "Telegram rejected TELEGRAM_TOKEN (Unauthorized). Check the "
                "token in .env, or set CONDOR_MODE=local to run without Telegram."
            )
        await startup(application)
        await application.start_polling()

    # Create and start the web server. With Tailscale on, WEB_HOST
    # (utils.config.resolve_web_host) is normally this node's own tailnet
    # address, which needs no proxy: the port is on the tailnet and on no
    # public interface, full stop.
    #
    # `tailscale serve` is only the fallback for when that address could not be
    # resolved -- daemon not up yet, node not registered -- in which case the
    # bind failed closed to loopback and a proxy is the only way anything
    # reaches it. Serve needs the daemon socket, which refuses unprivileged
    # callers, so this often cannot succeed either; say so plainly rather than
    # leaving the dashboard silently unreachable.
    web_app = create_app()
    if USE_TAILSCALE and LOCAL_MODE:
        # Local mode keeps loopback even with Tailscale on: the dashboard has
        # no login, and a tailnet is a smaller audience than the internet, not
        # an authenticated one. Intended, so no proxy and no warning.
        logger.info(
            "Dashboard on http://%s:%s — local mode stays on loopback even "
            "with Tailscale enabled (no login on this dashboard).",
            WEB_HOST,
            WEB_PORT,
        )
    elif USE_TAILSCALE:
        from utils.tailscale import is_tailnet_ip

        if is_tailnet_ip(WEB_HOST):
            logger.info(
                "Dashboard on http://%s:%s — reachable on the tailnet only.",
                WEB_HOST,
                WEB_PORT,
            )
        elif os.environ.get("WEB_HOST", "").strip():
            # An explicit WEB_HOST is a deliberate opt-out, not a failed
            # lookup. Warning about a tailnet address we were never asked to
            # find would report someone's own choice as a fault.
            logger.info(
                "Dashboard on http://%s:%s — WEB_HOST was set explicitly.",
                WEB_HOST,
                WEB_PORT,
            )
        else:
            from utils.tailscale import ensure_serve

            logger.warning(
                "USE_TAILSCALE is set but this node's tailnet address could "
                "not be determined, so the dashboard bound to %s. Falling "
                "back to `tailscale serve`.",
                WEB_HOST,
            )
            if not await ensure_serve(WEB_PORT):
                logger.error(
                    "Dashboard bound to %s; tailnet forwarding could not be "
                    "confirmed, so it will NOT be reachable remotely. Check "
                    "`tailscale status`, then restart Condor to pick up the "
                    "tailnet address.",
                    WEB_HOST,
                )
    server = uvicorn.Server(_web_server_config(web_app))

    # Start WebSocket manager
    get_ws_manager().start()

    # A run the journal still calls live is a run no process is driving: this one
    # just booted, and a run is only ever executed by the process that started
    # it. Judged before anything else looks at it, and deliberately *outside*
    # the admin block below -- an install with no ADMIN_USER_ID used to leave
    # the journal live for ever, which is a settings panel stuck on a spinner
    # that neither a reload nor a relaunch could clear.
    from condor import updates

    try:
        finished = await updates.finalize_pending_run()
    except Exception:
        logger.warning("Could not finalize the pending update run", exc_info=True)
        finished = None

    # Notify admin that Condor has started
    from utils.config import ADMIN_USER_ID

    if ADMIN_USER_ID:
        try:
            # Report the version we came up on: after a /update restart this is
            # the confirmation that the new commit is the one actually running.
            from utils.updater import get_current_branch, get_local_commit

            branch, commit = await asyncio.gather(
                get_current_branch(), get_local_commit()
            )
            version = f" ({branch} @ {commit})" if commit else ""
            boot_text = f"Condor is online and ready.{version}"

            # Judged above, before the admin block, so the panel is correct even
            # on an install with nobody to tell. What is left here is the
            # telling: a Condor old enough to exec itself at the end of an
            # update could not report its own outcome, because the process that
            # would have is the one that died (FEAT-070).
            if finished is not None and finished.state == "succeeded":
                moved = f"{(finished.from_commit or '')[:7]} → {commit}"
                boot_text = f"Update complete: {branch} {moved}. Condor is ready."
            elif finished is not None:
                boot_text = f"Update failed: {finished.error} Condor is ready.{version}"

            # One call reaches Telegram *and* the dashboard bell (FEAT-048), so
            # an admin who only has the browser open still sees which commit
            # came up -- and whether the update that asked for it worked.
            from condor import notifications

            await notifications.announce(
                int(ADMIN_USER_ID),
                None if LOCAL_MODE else int(ADMIN_USER_ID),
                boot_text,
                kind="system",
                bot=None if LOCAL_MODE else application.bot,
            )
        except Exception as e:
            logger.warning(f"Failed to send startup notification to admin: {e}")

    if LOCAL_MODE:
        logger.info(
            "Starting Condor in local mode (no Telegram): dashboard on http://%s:%s",
            WEB_HOST,
            WEB_PORT,
        )
    else:
        logger.info(
            "Starting Condor: Telegram bot + web dashboard on port %s", WEB_PORT
        )

    # Handle shutdown signals
    shutdown_event = asyncio.Event()

    def _signal_handler():
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    # The boot got this far, so a later crash should take the tmux session with
    # it again -- otherwise `make status` would report a dead Condor as running.
    # exec_restart() turned this on so that a failed handover stayed readable;
    # this is the other half, mirroring what `make run` does after its probe.
    from utils.updater import set_tmux_remain_on_exit

    set_tmux_remain_on_exit(False)

    # Run uvicorn as a task
    web_task = asyncio.create_task(server.serve())
    stop_task = asyncio.create_task(shutdown_event.wait())

    try:
        # Exit on a shutdown signal OR if the web server stops/crashes on its own.
        await asyncio.wait({web_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        # Always run teardown — even if the run raised — so it (runtime.destroy_all
        # + engine.stop) reaps every ACP subprocess tree.
        logger.info("Shutting down...")
        stop_task.cancel()
        server.should_exit = True
        try:
            await web_task
        except Exception:
            logger.exception("Web server crashed during shutdown")
        # Run each step independently so one failure can't skip the rest.
        # teardown() runs last. Local mode never started polling, so there is
        # nothing to stop; it still owns the shared speech workers used by
        # dashboard audio uploads and the standalone sender, if one was built.
        if LOCAL_MODE:
            steps = (
                ("telegram_input.close", _close_telegram_input),
                ("standalone_bot.close", close_standalone_bot),
                ("teardown", partial(teardown, application)),
            )
        else:
            steps = (
                ("polling.stop", application.stop_polling),
                ("telegram_input.close", _close_telegram_input),
                ("bot.close", application.close),
                ("standalone_bot.close", close_standalone_bot),
                ("teardown", partial(teardown, application)),
            )
        for name, step in steps:
            try:
                await step()
            except Exception:
                logger.exception("Shutdown step %s failed", name)


if __name__ == "__main__":
    main()
