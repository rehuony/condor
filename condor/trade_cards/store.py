"""One main-process store, with atomic snapshots and per-card operation locks."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from condor.fsutil import atomic_write_json
from condor.paths import data_dir

from .models import Card


class CardStore:
    def __init__(self, path: Path):
        self.path = path
        self.publish_lock = asyncio.Lock()
        self._locks: dict[str, asyncio.Lock] = {}
        self._cards: dict[str, Card] = {}
        #: Signal keys of deleted cards, kept until they expire so a signal
        #: whose card is gone still cannot publish a second one.
        self._reserved: dict[str, float] = {}
        self._cancelled_custom_inputs: set[str] = set()
        if path.exists():
            raw = json.loads(path.read_text())
            if raw.get("version") != 1:
                raise ValueError("Unsupported trade card store version")
            self._cards = {
                key: Card.model_validate(value) for key, value in raw["cards"].items()
            }
            self._reserved = dict(raw.get("reserved_signals", {}))

    def lock(self, card_id: str) -> asyncio.Lock:
        return self._locks.setdefault(card_id, asyncio.Lock())

    def get(self, card_id: str) -> Card | None:
        card = self._cards.get(card_id)
        return card.model_copy(deep=True) if card is not None else None

    def all(self) -> list[Card]:
        return [card.model_copy(deep=True) for card in self._cards.values()]

    def signal_reserved(self, key: str) -> bool:
        """A card exists for this signal, or did until recently."""
        if self._reserved.get(key, 0) > time.time():
            return True
        return any(
            card.signal_key == key and card.delivery_status != "failed"
            for card in self._cards.values()
        )

    def save(self, card: Card) -> None:
        # No await between snapshot, atomic commit and memory replacement: all
        # producers share this process. Never mutate through a returned Card.
        if card.id in self._cancelled_custom_inputs:
            card.custom_input = False
        self._commit(
            {**self._cards, card.id: card.model_copy(deep=True)}, self._reserved
        )

    def delete(self, card: Card) -> None:
        """Forget a card whose message is gone, keeping its signal reserved."""
        reserved = dict(self._reserved)
        if card.signal_key and card.delivery_status != "failed":
            # Until well past the card's validity, so its trigger bar is stale.
            reserved[card.signal_key] = max(card.expires_at, time.time()) + 3600
        cards = {key: value for key, value in self._cards.items() if key != card.id}
        self._commit(cards, reserved)

    def _commit(self, cards: dict[str, Card], reserved: dict[str, float]) -> None:
        now = time.time()
        reserved = {key: until for key, until in reserved.items() if until > now}
        atomic_write_json(
            self.path,
            {
                "version": 1,
                "cards": {
                    key: value.model_dump(mode="json") for key, value in cards.items()
                },
                "reserved_signals": reserved,
            },
        )
        self._cards = cards
        self._reserved = reserved

    def cancel_custom_input(self, card: Card) -> None:
        # A menu transition is synchronous and can run while a card action
        # awaits Telegram. Preserve cancellation if that action saves later.
        self._cancelled_custom_inputs.add(card.id)
        card.custom_input = False
        self.save(card)

    def allow_custom_input(self, card_id: str) -> None:
        self._cancelled_custom_inputs.discard(card_id)


_store: CardStore | None = None


def get_store() -> CardStore:
    global _store
    path = data_dir() / "trade_cards.json"
    if _store is None or _store.path != path:
        _store = CardStore(path)
    return _store
