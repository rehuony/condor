"""Persistent trade plans and the card's presentation state.

Execution belongs to the API position group. A card never infers that a timeout
means an order failed, and its execution target never follows chat navigation.
"""

from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TradePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trading_pair: str = Field(min_length=1)
    side: Literal["long", "short"]
    entry_low: Decimal = Field(gt=0, allow_inf_nan=False)
    entry_high: Decimal = Field(gt=0, allow_inf_nan=False)
    stop_price: Decimal = Field(gt=0, allow_inf_nan=False)
    tp1_price: Decimal = Field(gt=0, allow_inf_nan=False)
    tp2_price: Decimal = Field(gt=0, allow_inf_nan=False)
    timeframe: str = "1H"
    signal_type: str = ""
    score: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered_prices(self):
        if self.entry_low > self.entry_high:
            raise ValueError("Entry low must not exceed entry high")
        if self.side == "long":
            valid = (
                self.stop_price
                < self.entry_low
                <= self.entry_high
                < self.tp1_price
                <= self.tp2_price
            )
        else:
            valid = (
                self.tp2_price
                <= self.tp1_price
                < self.entry_low
                <= self.entry_high
                < self.stop_price
            )
        if not valid:
            raise ValueError(
                "Stop and targets must lie beyond the entry zone in the planned direction"
            )
        return self


class CardStatus(StrEnum):
    PROPOSED = "PROPOSED"
    SIZING = "SIZING"
    REVIEW = "REVIEW"
    PLACING = "PLACING"
    PENDING = "PENDING"
    OPEN = "OPEN"
    CLOSING = "CLOSING"
    RECONCILING = "RECONCILING"
    CLOSED = "CLOSED"
    DETACHED = "DETACHED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


UNORDERED = frozenset({CardStatus.PROPOSED, CardStatus.SIZING, CardStatus.REVIEW})
HIDDEN = frozenset(
    {CardStatus.REJECTED, CardStatus.EXPIRED, CardStatus.CANCELLED, CardStatus.DETACHED}
)
TERMINAL = HIDDEN | {CardStatus.CLOSED, CardStatus.FAILED}
EXECUTING = frozenset(
    {
        CardStatus.PLACING,
        CardStatus.PENDING,
        CardStatus.OPEN,
        CardStatus.CLOSING,
        CardStatus.RECONCILING,
    }
)
_TRANSITIONS = {
    CardStatus.PROPOSED: {
        CardStatus.SIZING,
        CardStatus.REJECTED,
        CardStatus.EXPIRED,
        CardStatus.FAILED,
    },
    CardStatus.SIZING: {
        CardStatus.PROPOSED,
        CardStatus.REVIEW,
        CardStatus.REJECTED,
        CardStatus.EXPIRED,
    },
    CardStatus.REVIEW: {
        CardStatus.SIZING,
        CardStatus.PLACING,
        CardStatus.EXPIRED,
        CardStatus.REJECTED,
    },
    CardStatus.PLACING: EXECUTING
    | {CardStatus.REVIEW, CardStatus.CLOSED, CardStatus.CANCELLED, CardStatus.FAILED},
    CardStatus.PENDING: EXECUTING
    | {CardStatus.CLOSED, CardStatus.CANCELLED, CardStatus.DETACHED, CardStatus.FAILED},
    CardStatus.OPEN: EXECUTING
    | {CardStatus.CLOSED, CardStatus.DETACHED, CardStatus.FAILED},
    CardStatus.CLOSING: EXECUTING
    | {CardStatus.CLOSED, CardStatus.CANCELLED, CardStatus.DETACHED, CardStatus.FAILED},
    CardStatus.RECONCILING: EXECUTING
    | {
        CardStatus.REVIEW,
        CardStatus.CLOSED,
        CardStatus.CANCELLED,
        CardStatus.DETACHED,
        CardStatus.FAILED,
    },
}


class Card(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    plan: TradePlan
    owner_id: int
    chat_id: int
    server_name: str
    server_fingerprint: str | None = None
    account_name: str
    connector_name: str
    status: CardStatus = CardStatus.PROPOSED
    created_at: float = Field(default_factory=time.time)
    expires_at: float
    signal_key: str | None = None
    message_id: int | None = None
    delivery_status: Literal["pending", "sent", "unknown", "failed"] = "pending"
    message_deleted: bool = False
    delete_failed: bool = False
    margin: str = "10"
    quote: dict = Field(default_factory=dict)
    execution: dict = Field(default_factory=dict)
    create_payload: dict | None = None
    pending_action: str | None = None
    operation_id: str | None = None
    operation_seq: int = 0
    confirm_action: str | None = None
    confirm_until: float = 0
    custom_input: bool = False
    note: str = ""
    last_render: str = ""
    #: When the message was last confirmed to still exist in the chat.
    last_checked: float = 0

    def transition(self, status: CardStatus) -> None:
        if status == self.status:
            return
        if status not in _TRANSITIONS.get(self.status, set()):
            raise ValueError(f"Invalid card transition: {self.status} -> {status}")
        self.status = status


def recovery_actions(card: Card) -> frozenset[str]:
    """Reduce known exposure during data outages without repeating entry work."""
    if card.status != CardStatus.RECONCILING:
        return frozenset()
    snapshot = card.execution
    operation = snapshot.get("operation") or {}
    if snapshot.get("id") != card.id:
        return frozenset()
    if card.pending_action and operation.get("id") != card.operation_id:
        return frozenset()
    if "available_actions" in snapshot:
        return frozenset(snapshot["available_actions"]) & {"close", "cancel_entry"}
    if card.pending_action or operation.get("status") == "PENDING":
        return frozenset()
    actions = set()
    if snapshot.get("status") == "PENDING":
        actions.add("cancel_entry")
    try:
        if (
            Decimal(str(snapshot.get("filled_amount", 0))) > 0
            and Decimal(str(snapshot.get("remaining_amount", 0))) > 0
        ):
            actions.add("close")
    except InvalidOperation:
        pass
    return frozenset(actions)


def management_actions(card: Card) -> frozenset[str]:
    """Share the execution API's controls between the view and action boundary."""
    if card.status == CardStatus.RECONCILING:
        return recovery_actions(card)
    if card.status not in {CardStatus.PENDING, CardStatus.OPEN}:
        return frozenset()
    snapshot = card.execution
    if (
        card.pending_action
        or (snapshot.get("operation") or {}).get("status") == "PENDING"
    ):
        return frozenset()
    allowed = (
        {"cancel_entry"}
        if card.status == CardStatus.PENDING
        else {"cancel_entry", "reduce_half", "close", "detach"}
    )
    if "available_actions" in snapshot:
        allowed.intersection_update(snapshot["available_actions"])
    elif card.status == CardStatus.OPEN:
        # Older persisted snapshots have the original three OPEN controls.
        # A missing capability never grants the newly added entry cancellation.
        allowed.discard("cancel_entry")
    if snapshot.get("entry_closed") is True:
        allowed.discard("cancel_entry")
    return frozenset(allowed)
