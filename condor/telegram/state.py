"""Per-user and per-chat state that outlives a restart.

``UserStateStore`` owns the dicts that handlers read as ``context.user_data``
and ``context.chat_data``. It is keyed by user id and shared across chats,
which is what the handlers were written against; it hands out the *same*
mutable dict for the same id forever, because ``AgentSession`` keeps a
reference to that dict rather than a copy.

On disk it is one standard-library pickle, ``{"version": 1, "users": {...},
"chats": {...}}``, written whole on every flush (the live file is a few KB):
no dirty tracking, so a write from a job or a web request cannot be missed.
Writes are atomic with a ``.bak`` of the previous file; ``EPHEMERAL_KEYS`` and
values that cannot be pickled (tasks, locks, clients) are left out rather than
failing the whole snapshot.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import pickle
from collections import defaultdict
from pathlib import Path
from typing import Any

from condor.fsutil import atomic_write_bytes

log = logging.getLogger(__name__)

FORMAT_VERSION = 1

# Keys that live in user_data at runtime but should NOT be persisted.
# They are rebuilt on demand (caches) or are large transient snapshots.
EPHEMERAL_KEYS = frozenset(
    {
        # Cache namespaces (rebuilt on demand, TTL-based)
        "_cache",
        "_cex_cache",
        "_bots_cache",
        "_executors_cache",
        "token_cache",
        # Portfolio snapshots (large data, rebuilt on /portfolio)
        "portfolio_balances",
        "portfolio_accounts_distribution",
        "portfolio_changes_24h",
        "portfolio_pnl_indicators",
        # Per-render API snapshots (re-fetched by the handler that reads
        # them; every read site falls back to a fresh fetch on a miss)
        "active_bots_data",
        "current_bot_info",
        "bot_runs_map",
        "controller_configs_list",
        "configs_type_filtered",
        "current_executor",
        "running_executors",
        "gs_candles",
        "selected_pool_info",
        "gecko_pools",
        "pool_list_cache",
        "positions_cache",
        "lp_positions_cache",
    }
)


def strip_ephemeral(buckets: dict[int, Any]) -> dict[int, Any]:
    """A shallow copy of ``buckets`` without the ephemeral keys.

    The live dicts are not touched: the running session keeps its caches.
    Buckets that need nothing stripped are passed through by identity.
    """
    cleaned: dict[int, Any] = {}
    for key, data in buckets.items():
        if isinstance(data, dict) and not EPHEMERAL_KEYS.isdisjoint(data):
            cleaned[key] = {k: v for k, v in data.items() if k not in EPHEMERAL_KEYS}
        else:
            cleaned[key] = data
    return cleaned


def _picklable(buckets: dict[int, Any], kind: str) -> dict[int, Any]:
    """Drop the values a snapshot cannot hold, saying which ones."""
    out: dict[int, Any] = {}
    for key, data in buckets.items():
        if not isinstance(data, dict):
            out[key] = data
            continue
        kept = {}
        for name, value in data.items():
            if isinstance(value, (asyncio.Future, asyncio.Lock, asyncio.Event)):
                continue
            try:
                pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
            except Exception as exc:  # pickling raises many unrelated types
                log.warning(
                    "Dropping unpicklable %s[%s][%r] from state snapshot: %s",
                    kind,
                    key,
                    name,
                    exc,
                )
                continue
            kept[name] = value
        out[key] = kept
    return out


class UserStateStore:
    """The durable ``user_data``/``chat_data`` dicts and their file."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.users: defaultdict[int, dict] = defaultdict(dict)
        self.chats: defaultdict[int, dict] = defaultdict(dict)
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._last_written: bytes | None = None

    # -- access -----------------------------------------------------------

    def get(self, user_id: int) -> dict:
        """This user's dict; the same object for the same id, created on demand."""
        return self.users[user_id]

    def chat(self, chat_id: int) -> dict:
        """This chat's dict; the same object for the same id, created on demand."""
        return self.chats[chat_id]

    def mark_dirty(self) -> None:
        """Ask for a flush at the next opportunity instead of the next tick."""
        self._wake.set()

    # -- disk -------------------------------------------------------------

    def load(self) -> None:
        """Read the store, falling back to ``.bak``."""
        data = self._read(self.path)
        if data is None and self._bak.exists():
            log.warning("State file unreadable, recovering from %s", self._bak)
            data = self._read(self._bak)
            if data is not None:
                log.info("Recovered state from backup")
        if data is None:
            if self.path.exists() or self._bak.exists():
                log.error("State file and backup unreadable; starting empty")
            return
        self.users.clear()
        self.chats.clear()
        self.users.update(data.get("users") or {})
        self.chats.update(data.get("chats") or {})

    def flush(self) -> None:
        """Write everything out now, atomically, keeping the previous file as ``.bak``."""
        payload = {
            "version": FORMAT_VERSION,
            "users": _picklable(strip_ephemeral(self.users), "users"),
            "chats": _picklable(dict(self.chats), "chats"),
        }
        buf = io.BytesIO()
        pickle.dump(payload, buf, protocol=pickle.HIGHEST_PROTOCOL)
        blob = buf.getvalue()
        if blob == self._last_written and self.path.exists():
            return
        if self.path.exists():
            try:
                os.replace(self.path, self._bak)
            except OSError:
                log.warning("Could not rotate %s to %s", self.path, self._bak)
        atomic_write_bytes(self.path, blob)
        self._last_written = blob

    # -- lifecycle --------------------------------------------------------

    async def start(self, interval: float = 10) -> None:
        """Flush every ``interval`` seconds, or sooner after :meth:`mark_dirty`."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(
            self._run(interval), name="UserStateStore.flush"
        )

    async def close(self) -> None:
        """Stop the flush loop and write the final snapshot."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        try:
            self.flush()
        except Exception:
            log.exception("Final state flush failed")

    async def _run(self, interval: float) -> None:
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), interval)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            try:
                self.flush()
            except Exception:
                log.exception("Periodic state flush failed")

    # -- helpers ----------------------------------------------------------

    @property
    def _bak(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".bak")

    @staticmethod
    def _read(path: Path) -> dict[str, Any] | None:
        try:
            with path.open("rb") as fh:
                data = pickle.load(fh)
        except FileNotFoundError:
            return None
        except Exception as exc:
            log.warning("Could not load %s: %s", path, exc)
            return None
        if not isinstance(data, dict) or "users" not in data:
            log.warning("%s is not a state file", path)
            return None
        return data
