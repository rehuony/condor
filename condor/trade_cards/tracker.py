"""One process-lifetime tracker; never owned by a routine or handler reload."""

import asyncio
import logging

from .models import TERMINAL
from .service import refresh_card
from .store import get_store

logger = logging.getLogger(__name__)
INTERVAL_SECONDS = 20
_task: asyncio.Task | None = None


async def tick(bot) -> None:
    limit = asyncio.Semaphore(4)

    async def refresh(card):
        async with limit:
            try:
                await refresh_card(card.id, bot)
            except Exception:
                logger.exception("Trade card tracker failed for %s", card.id)

    # Terminal cards can still need a message update/deletion after a network
    # failure. refresh_card never repeats their trading operation.
    await asyncio.gather(
        *(
            refresh(card)
            for card in get_store().all()
            if card.status not in TERMINAL
            or (card.message_id and not card.message_deleted)
        )
    )


async def _run(bot) -> None:
    while True:
        try:
            await tick(bot)
        except Exception:
            logger.exception("Trade card tracker iteration failed")
        await asyncio.sleep(INTERVAL_SECONDS)


async def start_tracker(bot) -> None:
    global _task
    get_store()  # Refuse to silently discard a corrupt store before starting.
    if _task is None or _task.done():
        _task = asyncio.create_task(_run(bot), name="trade_cards.tracker")


async def stop_tracker() -> None:
    global _task
    task, _task = _task, None
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
