"""Hot reload: re-execute handler modules when their files change."""

import importlib
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

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
    root = _PROJECT_ROOT / "handlers"
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


async def watch_and_reload(application) -> None:
    """Watch for file changes and reload handlers automatically."""
    try:
        from watchfiles import DefaultFilter, awatch
    except ImportError:
        logger.warning(
            "watchfiles not installed. Auto-reload disabled. Install with: uv add watchfiles"
        )
        return

    handlers_path = _PROJECT_ROOT / "handlers"
    routines_path = _PROJECT_ROOT / "routines"
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
            # Imported here, after the reload, so the fresh modules are the ones
            # that run: both live under handlers/ and were just re-executed.
            from handlers.commands import register_bot_commands
            from handlers.routes import register_handlers

            register_handlers()
            # Refresh the Telegram command menus too, so a menu edit shows up
            # without requiring a full process restart.
            await register_bot_commands(application.bot)
            logger.info("✅ Auto-reloaded handlers successfully")
        except Exception as e:
            logger.error(f"❌ Auto-reload failed: {e}", exc_info=True)
