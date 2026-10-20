"""Per-chat output order: a turn's final delivery waits for its predecessor."""

from __future__ import annotations

import asyncio

_tails: dict[int, asyncio.Future] = {}


class OutputLease:
    """Reserve output order now, without blocking the runtime event consumer."""

    def __init__(self, chat_id: int):
        self.chat_id = chat_id
        self._previous = _tails.get(chat_id)
        self._finished = asyncio.get_running_loop().create_future()
        self._released = False
        _tails[chat_id] = self._finished

    async def acquire(self) -> None:
        if self._previous is not None:
            await asyncio.shield(self._previous)

    def release(self) -> None:
        if self._released:
            return
        self._released = True

        def finish(_=None):
            if not self._finished.done():
                self._finished.set_result(None)
            if _tails.get(self.chat_id) is self._finished:
                _tails.pop(self.chat_id, None)

        # A cancelled waiter must not let the next turn overtake its predecessor.
        if self._previous is not None and not self._previous.done():
            self._previous.add_done_callback(finish)
        else:
            finish()
