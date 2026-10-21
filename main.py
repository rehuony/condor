"""Condor's entry point: ``uv run python main.py``.

Owns process startup, the Telegram and web event loop, and shutdown. Telegram
commands live in ``handlers/``; reusable services live in ``condor/``.
"""

import asyncio
import logging
import os
from functools import partial
from pathlib import Path

from condor import paths
from condor.scheduler import get_scheduler
from condor.telegram.app import TelegramApp
from condor.telegram.errors import NetworkFailure, Unauthorized
from condor.telegram.state import UserStateStore
from condor.telegram.transport import close_standalone_bot
from condor.telegram.types import Context, Update
from condor.web.server import ensure_dashboard_reachable, web_server_config
from handlers.routes import register_handlers
from utils.config import (
    LOCAL_MODE,
    TELEGRAM_TOKEN,
    WEB_HOST,
    WEB_PORT,
    ConfigError,
    check_local_user,
    check_startup_config,
    warn_shadowed_env,
)

logger = logging.getLogger(__name__)


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


def outbound_bot(application: TelegramApp):
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

    :func:`serve` drives the lifecycle by hand (``initialize``
    → this → ``start_polling``) so it can run uvicorn alongside the bot. Called
    explicitly from there, before the first update is served.
    """
    # Recovering a current archive index can read large payloads. Finish it off
    # the event loop before scheduled jobs or requests can initialize the store.
    from condor.backtest_store import get_backtest_store

    await asyncio.to_thread(get_backtest_store)

    await application.store.start(interval=10)

    # Timed work (scheduled routines, update checks, reflection) runs on our
    # own scheduler, in both modes; jobs registered below are picked up by it.
    get_scheduler().start()

    # Sync server permissions (ensures all servers have ownership entries)
    await sync_server_permissions()

    # Whisper is loaded lazily on the first audio message. Text/image-only
    # installations do not need an idle speech model or a startup download.

    # Whatever this process pushes at users from here on. In local mode there is
    # no Telegram to push to, so it is the dashboard bell (FEAT-048) instead —
    # which is what keeps the documented "context.bot is never None" contract
    # true for routines with no bot behind them.
    sender = outbound_bot(application)

    # Register command menus (public + admin overlay). Pure Telegram: there is
    # no command menu to publish when nothing polls.
    if not LOCAL_MODE:
        from handlers.commands import register_bot_commands

        await register_bot_commands(application.bot)

    # Restore scheduled routine jobs from persistence
    from handlers.routines import restore_scheduled_jobs

    await restore_scheduled_jobs(application)

    # Inject Telegram bot into routine store so web-triggered routines can send messages
    from condor.routine_store import get_routine_store

    get_routine_store().set_bot(sender)

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

    await runtime_sessions.start_health_monitor(sender)
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
            await _notify_interrupted_runs(sender, report)
    except Exception:
        logger.exception("Boot reconciliation failed; continuing startup")

    # Schedule periodic update checks (notifies admin)
    from handlers.admin.update import schedule_update_checks

    schedule_update_checks(sender)

    # Read finished conversations for local memory and intent extraction.
    # Disabled when CONDOR_REFLECTION=off.
    try:
        from condor.agents import reflection

        reflection.register_jobs()
    except Exception:
        logger.exception("Reflection job registration failed (continuing without it)")

    # Start file watcher
    from condor.reload import watch_and_reload

    asyncio.create_task(watch_and_reload(application))


async def announce_boot(application: TelegramApp) -> None:
    """Settle the pending update run, then tell the admin which commit came up."""
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

    if not ADMIN_USER_ID:
        return
    try:
        # Report the version we came up on: after a /update restart this is
        # the confirmation that the new commit is the one actually running.
        from utils.updater import get_current_branch, get_local_commit

        branch, commit = await asyncio.gather(get_current_branch(), get_local_commit())
        version = f" ({branch} @ {commit})" if commit else ""
        boot_text = f"Condor is online and ready.{version}"

        if finished is not None:
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


async def close_telegram_input() -> None:
    """Close ingress and shared media workers in either application mode."""
    from condor.telegram.ingress import close_ingress
    from condor.telegram.input import close_resolver

    await close_ingress()
    await close_resolver()
    from utils.transcribe import close_transcriber

    await close_transcriber()


async def close_telegram_output() -> None:
    """Settle approval cleanup and delivery before closing bot transports."""
    from condor.runtime.confirmations import get_registry
    from condor.telegram.output.renderer import close_presenters

    await get_registry().stop()
    await close_presenters()
    await get_registry().drain()


async def teardown(application: TelegramApp) -> None:
    """Wind the process down: stop supervisors, flush state, close clients.

    The counterpart to :func:`startup`; called explicitly from
    :func:`serve`.
    """
    from condor.runtime import client as runtime
    from condor.runtime import sessions as runtime_sessions

    get_scheduler().shutdown()
    await runtime_sessions.stop_health_monitor()

    # Stop all trading agents. Graceful stop, deliberately NOT the shutdown
    # sequence — winding down positions is an emergency action, not what a
    # restart should do. Each engine records its final state on the way out.
    from condor.runtime.conversations import flush_all as flush_conversations
    from condor.runtime.loops import get_supervisor
    from condor.runtime.state import flush_all

    await get_supervisor().stop_all()
    await runtime.destroy_all()
    # Writes are debounced, so force the last one out on a clean shutdown.
    flush_all()
    # A prompt still streaming when the bot went down holds its turn in
    # memory; write it out rather than losing the last thing that was said.
    flush_conversations()
    await application.store.close()

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


def run() -> None:
    """Configure logging and run the bot."""
    # Enable logging
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )

    # aiogram logs one INFO line per handled update ("Update id=... is handled"),
    # which is noise next to the handlers' own logging; real dispatch problems
    # arrive as warnings and errors and still get through. httpx is used by the
    # Hummingbot API client and logs each request URL at INFO.
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

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
    store = get_state_store()
    store.load()

    # In local mode there is no token and nothing polls; the placeholder exists
    # only so the bot object (and with it the handler contexts) can be built at
    # all. Nothing ever calls Telegram with it.
    application = TelegramApp(TELEGRAM_TOKEN or "0:local", store)

    # Register all handlers
    register_handlers()
    application.error_handler = error_handler

    # Run TG bot + web server concurrently in a manual event loop
    try:
        asyncio.run(serve(application))
    finally:
        # /update asks for a restart by signalling a normal shutdown, so the
        # re-exec happens here: after the loop is gone and teardown has flushed
        # state and reaped subprocesses. In a `finally` because a restart is
        # still the right outcome even if a shutdown step blew up on the way
        # out. Replacing the image in place keeps the tmux pane alive.
        from utils.updater import exec_restart, restart_pending

        if restart_pending():
            exec_restart()  # never returns


async def serve(application: TelegramApp) -> None:
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

    web_app = create_app()
    await ensure_dashboard_reachable()
    server = uvicorn.Server(web_server_config(web_app))

    # Start WebSocket manager
    get_ws_manager().start()

    await announce_boot(application)

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
        # Run each step independently so one failure can't skip the rest.
        # Close execution and delivery before their bot transports. Local mode
        # never started polling; it still owns the shared speech workers used
        # by dashboard audio and the standalone sender, if one was built.
        if LOCAL_MODE:
            steps = (
                ("telegram_input.close", close_telegram_input),
                ("teardown", partial(teardown, application)),
                ("telegram_output.close", close_telegram_output),
                ("standalone_bot.close", close_standalone_bot),
            )
        else:
            steps = (
                ("polling.stop", application.stop_polling),
                ("telegram_input.close", close_telegram_input),
                ("teardown", partial(teardown, application)),
                ("telegram_output.close", close_telegram_output),
                ("bot.close", application.close),
                ("standalone_bot.close", close_standalone_bot),
            )
        for name, step in steps:
            try:
                await step()
            except Exception:
                logger.exception("Shutdown step %s failed", name)
        try:
            await web_task
        except Exception:
            logger.exception("Web server crashed during shutdown")


if __name__ == "__main__":
    run()
