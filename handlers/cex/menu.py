"""
CEX Trading main menu - redirects to unified trade menu
"""

import asyncio
import logging

from condor.telegram.types import Context, Update

logger = logging.getLogger(__name__)

# The background loading task per user. Module state rather than
# ``user_data``: an ``asyncio.Task`` in ``user_data`` reaches the persistence
# snapshot, and a Task cannot be pickled.
_loading_tasks: dict[int, asyncio.Task] = {}


def cancel_cex_loading_task(context) -> None:
    """Cancel any pending CEX menu loading task"""
    owner = context.user_data.get("_user_id") or id(context.user_data)
    task = _loading_tasks.pop(owner, None)
    if task and not task.done():
        task.cancel()
        logger.debug("Cancelled pending CEX menu loading task")


async def show_cex_menu(update: Update, context: Context) -> None:
    """Display main CEX trading menu - redirects to unified trade menu"""
    from .trade import handle_trade

    await handle_trade(update, context)


async def handle_close(update: Update, context: Context) -> None:
    """Handle closing the CEX trading interface"""
    from .trade import handle_close as trade_close

    await trade_close(update, context)
