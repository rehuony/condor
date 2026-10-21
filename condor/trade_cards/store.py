"""One main-process store, with atomic snapshots and per-card operation locks."""

from __future__ import annotations

import asyncio
import json
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
        self._cancelled_custom_inputs: set[str] = set()
        if path.exists():
            raw = json.loads(path.read_text())
            if raw.get("version") != 1:
                raise ValueError("Unsupported trade card store version")
            self._cards = {
                key: Card.model_validate(value) for key, value in raw["cards"].items()
            }

    def lock(self, card_id: str) -> asyncio.Lock:
        return self._locks.setdefault(card_id, asyncio.Lock())

    def get(self, card_id: str) -> Card | None:
        card = self._cards.get(card_id)
        return card.model_copy(deep=True) if card is not None else None

    def all(self) -> list[Card]:
        return [card.model_copy(deep=True) for card in self._cards.values()]

    def find_signal(self, key: str) -> Card | None:
        return next(
            (
                card
                for card in self.all()
                if card.signal_key == key and card.delivery_status != "failed"
            ),
            None,
        )

    def save(self, card: Card) -> None:
        # No await between snapshot, atomic commit and memory replacement: all
        # producers share this process. Never mutate through a returned Card.
        if card.id in self._cancelled_custom_inputs:
            card.custom_input = False
        cards = {**self._cards, card.id: card.model_copy(deep=True)}
        atomic_write_json(
            self.path,
            {
                "version": 1,
                "cards": {
                    key: value.model_dump(mode="json") for key, value in cards.items()
                },
            },
        )
        self._cards = cards

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
