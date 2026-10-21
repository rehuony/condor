"""MarkdownV2 captions and inline controls for a single photo message."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from condor.telegram.types import InlineKeyboardButton, InlineKeyboardMarkup
from utils import telegram_ui as ui

from .models import HIDDEN, Card, CardStatus, management_actions, recovery_actions


def venue_label(connector_name: str) -> str:
    """``binance_perpetual`` → ``Binance Perp``, as the dashboard's
    ``formatConnectorName`` names it."""
    name = re.sub(r"_perpetual$", " perp", connector_name).replace("_", " ")
    return re.sub(r"\b[a-z]", lambda m: m.group().upper(), name)


def number(value, signed: bool = False) -> str:
    try:
        amount = Decimal(str(value))
        if not amount.is_finite():
            return "—"
    except (InvalidOperation, TypeError):
        return "—"
    text = format(amount, ".6f").rstrip("0").rstrip(".") or "0"
    return ("+" if signed and amount > 0 else "") + text


def result_row(card: Card) -> str:
    info = card.execution
    reason = str(info.get("close_reason") or "").upper()
    label = {
        "TAKE_PROFIT": "Take profit",
        "TP1": "TP1",
        "TP2": "TP2",
        "STOP_LOSS": "Stop loss",
        "TRAILING_STOP": "Breakeven trail",
        "MANUAL": "Manual close",
        "MANUAL_CLOSE": "Manual close",
        "CLOSED": "Closed",
        "CANCELLED": "Cancelled",
        "CANCELED": "Cancelled",
    }.get(reason, "Closed")
    if reason == "TAKE_PROFIT":
        targets = {
            str(leg.get("number") or str(leg.get("level_id", "")).rsplit(":", 1)[-1])
            for leg in info.get("legs", [])
            if str(leg.get("close_reason", "")).upper() == "TAKE_PROFIT"
        }
        label = "TP2" if "2" in targets else "TP1" if "1" in targets else "Take profit"
    pnl = info.get("pnl")
    if info.get("pnl_complete") is False or pnl is None:
        return f"{label} · PnL pending check"
    try:
        risk = Decimal(str(card.quote.get("max_loss", 0)))
        multiple = f" ({number(Decimal(str(pnl)) / risk, True)}R)" if risk > 0 else ""
        icon = "✅" if Decimal(str(pnl)) >= 0 else "❌"
    except InvalidOperation:
        icon, multiple = "✅", ""
    return f"{icon} {label} · {number(pnl, True)} USDT{multiple}"


def close_value(card: Card, fraction: Decimal) -> Decimal | None:
    """Unrealized PnL closing ``fraction`` of the remaining position locks in.

    ``side × (price − average entry) × remaining × fraction``, before fees. The
    card's ``pnl`` is the whole trade (realized legs included), so it is the
    same for both buttons and is not what either close realizes.
    """
    execution = card.execution
    try:
        price = Decimal(str(execution["current_price"]))
        entry = Decimal(str(execution["entry_price"]))
        remaining = Decimal(str(execution["remaining_amount"]))
    except (KeyError, InvalidOperation, TypeError):
        return None
    direction = 1 if card.plan.side == "long" else -1
    value = direction * (price - entry) * remaining * fraction
    return value if value.is_finite() else None


def close_label(card: Card, text: str, fraction: Decimal) -> str:
    value = close_value(card, fraction)
    shown = f" · {value:+.2f} USDT" if value is not None else ""
    return f"🔻 {text}{shown}"


def block(heading: str, rows: list[tuple[str, str]]) -> list[str]:
    """A bold section heading followed by ``• Label: *value*`` rows."""
    return [ui.section(heading), *(ui.bullet(label, value) for label, value in rows)]


def caption(card: Card) -> str:
    """MarkdownV2 caption: title, plan, then the block for the card's status."""
    plan = card.plan
    side = "🟢 Long" if plan.side == "long" else "🔴 Short"
    lines = [
        ui.bold(f"{plan.trading_pair} · {side} · {plan.timeframe}"),
        "",
        *block(
            "📍 Trade plan",
            [
                ("Entry", f"{number(plan.entry_low)} – {number(plan.entry_high)}"),
                ("Stop", number(plan.stop_price)),
                ("Take profit", f"{number(plan.tp1_price)} / {number(plan.tp2_price)}"),
            ],
        ),
        "",
    ]
    notes: list[str] = []
    if card.status == CardStatus.PROPOSED:
        expires = datetime.fromtimestamp(card.expires_at, timezone(timedelta(hours=8)))
        rows = [("Valid until", f"{expires:%m-%d %H:%M} (UTC+8)")]
        if plan.score is not None:
            rows.insert(0, ("Clarity", f"{number(plan.score)}/100"))
        lines += block("⏳ Awaiting decision", rows)
        notes.append(
            "Pick an amount to see the risk. Nothing is ordered until you confirm."
        )
    elif card.status == CardStatus.SIZING:
        lines += block(
            "💰 Choose margin",
            [("Leverage", "10x isolated"), ("Last", f"{number(card.margin)} USDT")],
        )
        if card.custom_input:
            notes.append(
                "Send a custom margin amount, or /cancel to return to the amounts."
            )
    elif card.status == CardStatus.REVIEW:
        q = card.quote
        entry = "Market" if q.get("entry_method") == "MARKET" else "Limit"
        lines += block(
            "🧮 Order review",
            [
                ("Entry", f"{entry} · ref {number(q.get('entry_price'))}"),
                ("Quantity", number(q.get("quantity"))),
                ("Margin", f"{number(q.get('margin'))} USDT · 10x"),
                ("Notional", f"{number(q.get('notional'))} USDT"),
                ("Loss at stop", f"{number(q.get('max_loss'))} USDT"),
                ("Est. liquidation", number(q.get("liquidation_price"))),
                (
                    "Est. profit",
                    f"{number(q.get('profit_tp1'), True)} / "
                    f"{number(q.get('profit_tp2'), True)} USDT",
                ),
            ],
        )
        notes += [
            "Half closes at each take profit; after TP1 the rest trails at 1R "
            "breakeven, with no time limit.",
            "Estimates include open and close fees; slippage and funding change "
            "the result.",
        ]
    elif card.status == CardStatus.OPEN:
        e = card.execution
        rows = [
            ("Fill price", number(e.get("entry_price"))),
            ("Remaining", number(e.get("remaining_amount"))),
            ("Price", number(e.get("current_price"))),
            ("PnL", f"{number(e.get('pnl'), True)} USDT"),
        ]
        liquidation = e.get("liquidation_price")
        if e.get("liquidation_price_kind") == "exchange" and liquidation is not None:
            rows.append(("Exchange liquidation", number(liquidation)))
        else:
            notes.append("The exchange liquidation price is unavailable for now.")
        try:
            price = Decimal(str(e["current_price"]))
            distance = abs(price - plan.stop_price) / price * 100
            rows.append(("To planned stop", f"{distance:.2f}%"))
        except (KeyError, InvalidOperation, ZeroDivisionError):
            pass
        lines += block("📈 Position open", rows)
        if e.get("pnl_complete") is False:
            notes.insert(0, "PnL is not fully reconciled yet.")
    elif card.status == CardStatus.CLOSED:
        lines.append(ui.bold(result_row(card)))
    elif card.status == CardStatus.PENDING:
        lines.append(ui.bold("🕒 Limit order waiting to fill"))
        notes.append("Any unfilled part is cancelled at expiry.")
    elif card.status == CardStatus.FAILED:
        lines.append(ui.bold("❌ Trade not completed"))
        notes.append("Check the executor status.")
    elif card.status == CardStatus.RECONCILING:
        lines.append(ui.bold("🔍 Checking execution results"))
        notes.append("New positions and repeated actions are blocked.")
        if recovery_actions(card):
            notes.append(
                "A confirmed position can still be closed, and open entry orders "
                "can still be cancelled."
            )
    elif card.status == CardStatus.CLOSING:
        lines.append(ui.bold("⏳ Processing the position action"))
        notes.append("Wait for the result.")
    elif card.status == CardStatus.PLACING:
        lines.append(ui.bold("⏳ Submitting the order"))
        notes.append("Do not repeat the action.")
    elif card.status == CardStatus.DETACHED:
        lines.append(ui.bold("🛑 Management stopped"))
        notes.append("The position stays open; this card no longer manages orders.")
    elif card.status in HIDDEN:
        lines.append(ui.bold("Card ended"))
    if card.delete_failed:
        notes.append(
            "The message is not deleted yet; its buttons are disabled and deletion "
            "will be retried."
        )
    if card.note:
        notes.append(card.note[:240])
    lines += [ui.italic(note) for note in notes]
    if card.confirm_action:
        label = {
            "reduce_half": "close half of the remaining position",
            "close": "close the whole position",
            "detach": "stop management, keep the position and delete the card",
        }[card.confirm_action]
        lines += ["", ui.bold(f"⚠️ Tap again to confirm: {label}")]
    return "\n".join(lines)


def keyboard(card: Card) -> InlineKeyboardMarkup | None:
    def button(text: str, action: str):
        return InlineKeyboardButton(text=text, callback_data=f"tc:{action}:{card.id}")

    if card.status in HIDDEN:
        return None
    actions = management_actions(card)
    if card.status == CardStatus.PROPOSED:
        rows = [[button("❌ Reject", "reject"), button("💰 Invest", "invest")]]
    elif card.status == CardStatus.SIZING:
        rows = [
            [
                button(
                    ui.selected(f"{amount} USDT", card.margin == amount), f"m{amount}"
                )
                for amount in ("10", "20", "50")
            ],
            [button("✏️ Custom", "custom"), button(ui.BACK, "back")],
        ]
        if card.margin not in {"10", "20", "50"}:
            rows.insert(1, [button(f"Last: {number(card.margin)} USDT", "last")])
    elif card.status == CardStatus.REVIEW:
        rows = [
            [
                button("✅ Place Order", "confirm"),
                button(f"{ui.BACK} to Amount", "invest"),
            ]
        ]
    elif card.status == CardStatus.PENDING:
        row = [button(ui.REFRESH, "refresh")]
        if "cancel_entry" in actions:
            row.insert(0, button("🛑 Cancel Order", "cancel_entry"))
        rows = [row]
    elif card.confirm_action and card.confirm_action in actions:
        rows = [
            [
                button("✅ Confirm", f"yes_{card.confirm_action}"),
                button(ui.BACK, "back"),
            ]
        ]
    elif card.status == CardStatus.OPEN:
        # One row each, so the value each close would lock in is never cut off
        rows = [
            [button(close_label(card, text, fraction), action)]
            for text, action, fraction in (
                ("Close Half", "reduce_half", Decimal("0.5")),
                ("Close All", "close", Decimal(1)),
            )
            if action in actions
        ]
        if "cancel_entry" in actions:
            rows.append([button("🛑 Cancel Remaining Entry", "cancel_entry")])
        row = [button(ui.REFRESH, "refresh")]
        if "detach" in actions:
            row.insert(0, button("🛑 Stop Managing", "detach"))
        rows.append(row)
    elif card.status == CardStatus.CLOSED:
        rows = [[button(result_row(card), "noop")]]
    elif card.status == CardStatus.FAILED:
        rows = [[button("❌ Trade Not Completed", "noop")]]
    elif card.status == CardStatus.RECONCILING and recovery_actions(card):
        actions = recovery_actions(card)
        rows = [[button(ui.REFRESH, "refresh")]]
        if "close" in actions:
            rows.insert(0, [button("🔻 Close All", "close")])
        if "cancel_entry" in actions:
            rows.insert(0, [button("🛑 Cancel Entry Order", "cancel_entry")])
    else:
        rows = [[button(ui.REFRESH, "refresh")]]
    return InlineKeyboardMarkup(inline_keyboard=rows)
