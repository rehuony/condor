"""MarkdownV2 captions and inline controls for a single photo message."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from condor.telegram.types import InlineKeyboardButton, InlineKeyboardMarkup
from utils import telegram_ui as ui

from .models import (
    HIDDEN,
    UNORDERED,
    Card,
    CardStatus,
    management_actions,
    recovery_actions,
)


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


def price(value) -> str:
    """Six significant digits: enough for any tick, without float or fee noise."""
    try:
        amount = Decimal(str(value))
        if not amount.is_finite():
            return "—"
    except (InvalidOperation, TypeError):
        return "—"
    if amount == 0:
        return "0"
    exponent = min(amount.adjusted() - 5, 0)
    text = format(amount.quantize(Decimal(1).scaleb(exponent)), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def usdt(value, signed: bool = False) -> str:
    try:
        amount = Decimal(str(value))
        if not amount.is_finite():
            return "—"
    except (InvalidOperation, TypeError):
        return "—"
    return f"{amount:+.2f}" if signed else f"{amount:.2f}"


def result(card: Card) -> tuple[str, str]:
    """How the trade ended, and its net PnL."""
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
        return label, "pending check"
    try:
        risk = Decimal(str(card.quote.get("max_loss", 0)))
        multiple = f" ({Decimal(str(pnl)) / risk:+.2f}R)" if risk > 0 else ""
        icon = "✅" if Decimal(str(pnl)) >= 0 else "❌"
    except InvalidOperation:
        icon, multiple = "✅", ""
    return f"{icon} {label}", f"{usdt(pnl, True)} USDT{multiple}"


def close_value(card: Card, fraction: Decimal) -> Decimal | None:
    """Unrealized PnL closing ``fraction`` of the remaining position locks in.

    ``side × (price − average entry) × remaining × fraction``, before fees. The
    card's ``pnl`` is the whole trade (realized legs included), so it is the
    same for both buttons and is not what either close realizes.
    """
    execution = card.execution
    try:
        mark = Decimal(str(execution["current_price"]))
        entry = Decimal(str(execution["entry_price"]))
        remaining = Decimal(str(execution["remaining_amount"]))
    except (KeyError, InvalidOperation, TypeError):
        return None
    direction = 1 if card.plan.side == "long" else -1
    value = direction * (mark - entry) * remaining * fraction
    return value if value.is_finite() else None


def close_label(card: Card, text: str, fraction: Decimal) -> str:
    value = close_value(card, fraction)
    shown = f" · {value:+.2f} USDT" if value is not None else ""
    return f"🔻 {text}{shown}"


def _percent(part: Decimal, whole: Decimal) -> str:
    return f"{abs(part) / whole * 100:.1f}%"


def entry_row(card: Card) -> tuple[str, str]:
    """Where the reviewed entry fills against the price it was quoted at."""
    plan, q = card.plan, card.quote
    try:
        now = Decimal(str(q["current_price"]))
        entry = Decimal(str(q["entry_price"]))
    except (KeyError, InvalidOperation, TypeError):
        return ("Entry", "—")
    if not (now.is_finite() and entry.is_finite() and now > 0):
        return ("Entry", "—")
    if q.get("entry_method") == "MARKET":
        return ("Entry", f"Market ≈ {price(entry)}")
    fill = f"Limit {price(entry)}"
    long = plan.side == "long"
    if (entry < now) if long else (entry > now):
        move = "dip" if long else "rise"
        fill += f" · {_percent(now - entry, now)} {move}"
    return ("Entry", fill)


def _when(stamp: float, fmt: str) -> str:
    return datetime.fromtimestamp(stamp, timezone(timedelta(hours=8))).strftime(fmt)


def live_heading(card: Card) -> str:
    at = card.live.get("at")
    return "📡 Market" + (f" · {_when(at, '%H:%M:%S')} (UTC+8)" if at else "")


def live_rows(card: Card, *, enter: bool = True) -> list[tuple[str, str]]:
    """The last refreshed price against the plan, and whether it can enter now."""
    plan = card.plan
    try:
        now = Decimal(str(card.live["price"]))
    except (KeyError, InvalidOperation, TypeError):
        return [("Now", "—")]
    if not now.is_finite() or now <= 0:
        return [("Now", "—")]
    long = plan.side == "long"
    shown = price(now)
    if plan.signal_price:
        change = (now - plan.signal_price) / plan.signal_price * 100
        # A change that rounds to nothing reads +0.00%, never -0.00%.
        shown += f" · {change.quantize(Decimal('0.01')) or Decimal(0):+.2f}% vs signal"
    rows = [
        ("Now", shown),
        (
            "To stop / TP1",
            f"{_percent(now - plan.stop_price, now)} / {_percent(plan.tp1_price - now, now)}",
        ),
    ]
    if not enter:
        return rows
    if plan.entry_low <= now <= plan.entry_high:
        entry = "Yes · in the zone"
    elif (now < plan.entry_low) if long else (now > plan.entry_high):
        edge = plan.entry_low if long else plan.entry_high
        entry = f"No · {_percent(now - edge, now)} {'below' if long else 'above'} zone"
    else:
        edge = plan.entry_high if long else plan.entry_low
        entry = f"Limit {price(edge)} · {_percent(now - edge, now)} {'dip' if long else 'rise'}"
    return [*rows, ("Enter now", entry)]


def validity_row(card: Card) -> tuple[str, str]:
    left = max(0, int(card.expires_at - (card.live.get("at") or card.created_at)))
    return (
        "Valid",
        f"{left // 3600}h {left % 3600 // 60:02d}m · until {_when(card.expires_at, '%H:%M')}",
    )


def _decimal(value) -> Decimal | None:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None
    return amount if amount.is_finite() else None


def position_rows(card: Card) -> list[tuple[str, str]]:
    """What was opened: average fill, size, the margin behind it, liquidation."""
    e = card.execution
    base = card.plan.trading_pair.split("-")[0]
    entry, filled = _decimal(e.get("entry_price")), _decimal(e.get("filled_amount"))
    margin = (
        filled * entry / card.leverage
        if entry is not None and filled
        else _decimal(e.get("margin"))
    )
    liquidation = _decimal(e.get("liquidation_price"))
    if liquidation is None:
        liquidation_text = "—"
    elif e.get("liquidation_price_kind") == "exchange":
        liquidation_text = price(liquidation)
    else:
        liquidation_text = f"≈ {price(liquidation)}"
    return [
        ("Entry", f"{price(entry)} · {number(filled)} {base}"),
        ("Margin", f"{usdt(margin)} USDT × {card.leverage} isolated"),
        ("Liquidation", liquidation_text),
    ]


def open_rows(card: Card) -> list[tuple[str, str]]:
    """The live state of an open position: price, net PnL, the stop it now
    has (trailing from breakeven once TP1 is reached) and what is left of it."""
    e, plan = card.execution, card.plan
    base = plan.trading_pair.split("-")[0]
    long = plan.side == "long"
    now, entry = _decimal(e.get("current_price")), _decimal(e.get("entry_price"))
    filled, left = _decimal(e.get("filled_amount")), _decimal(e.get("remaining_amount"))
    shown = price(now)
    if now and entry:
        change = ((now - entry) / entry * 100).quantize(Decimal("0.01")) or Decimal(0)
        shown += f" · {change:+.2f}% vs entry"
    pnl = _decimal(e.get("pnl"))
    pnl_text = f"{usdt(pnl, True)} USDT"
    if pnl is not None and entry and filled:
        margin = filled * entry / card.leverage
        pnl_text += f" ({pnl / margin * 100:+.1f}%)"
    rows = [("Now", shown), ("PnL", pnl_text)]
    open_legs = [
        leg for leg in e.get("legs", []) if _decimal(leg.get("remaining_amount"))
    ]
    trails = [
        stop
        for leg in open_legs
        if (stop := _decimal(leg.get("trailing_stop"))) is not None
    ]
    stop = (max(trails) if long else min(trails)) if trails else plan.stop_price
    stop_text = price(stop)
    if now:
        stop_text += f" · {_percent(now - stop, now)} away"
    rows.append(("Stop", stop_text))
    if trails or (filled and left is not None and left < filled):
        # The fill size is in the position rows; a trailing stop means TP1 was reached.
        rows.append(
            ("Left", f"{number(left)} {base}" + (" · trailing" if trails else ""))
        )
    return rows


def block(heading: str, rows: list[tuple[str, str]]) -> list[str]:
    """A bold section heading followed by ``• Label: *value*`` rows."""
    return [ui.section(heading), *(ui.bullet(label, value) for label, value in rows)]


def caption(card: Card) -> str:
    """MarkdownV2 caption in blank-line separated groups: title, plan, live
    market and the card's step, then at most one alert with the step's hint.

    The alert is the latest refusal or, failing that, why the card cannot be
    ordered now; each replaces the last instead of piling up below it.
    """
    plan = card.plan
    side = "🟢 Long" if plan.side == "long" else "🔴 Short"
    # Short enough for one line on a phone; the timeframe heads the plan.
    title = f"{plan.trading_pair} · {side}"
    plan_rows = [
        ("Entry", f"{price(plan.entry_low)} – {price(plan.entry_high)}"),
        ("Stop", price(plan.stop_price)),
        ("Take profit", f"{price(plan.tp1_price)} / {price(plan.tp2_price)}"),
    ]
    if card.status == CardStatus.PROPOSED and plan.score is not None:
        plan_rows.append(("Clarity", f"{number(plan.score)}/100"))
    groups: list[list[str]] = [
        # One line: the mode is part of what the card is, not a second heading.
        [
            (
                f"{ui.bold(title)} · {ui.italic('🧪 Mock')}"
                if card.mode == "mock"
                else ui.bold(f"{title} · ⚡ Live")
            )
        ],
        block(f"📍 Trade plan · {plan.timeframe}", plan_rows),
    ]
    if card.status in UNORDERED:
        rows = live_rows(card, enter=card.status != CardStatus.REVIEW)
        groups.append(block(live_heading(card), [*rows, validity_row(card)]))
    hints: list[str] = []
    if card.status == CardStatus.PROPOSED:
        hints.append(
            "Mock card: fills use live prices; no real orders are placed."
            if card.mode == "mock"
            else "Nothing is ordered until you confirm."
        )
    elif card.status == CardStatus.LEVERAGE:
        top = card.limits.get("max_leverage", 0)
        pair = card.limits.get("exchange_max_leverage")
        groups.append(
            block(
                "⚙️ Choose leverage",
                [("Allowed", f"1x – {top}x isolated" if top else "none")],
            )
        )
        if card.custom_input:
            hints.append("Send the leverage as a number, or /cancel to go back.")
        elif isinstance(pair, int) and top < pair:
            hints.append(
                f"The pair allows {pair}x; above {top}x liquidation comes before the stop."
            )
    elif card.status == CardStatus.SIZING:
        limits = card.limits
        groups.append(
            block(
                "💰 Choose margin",
                [
                    ("Leverage", f"{card.leverage}x isolated"),
                    (
                        "Allowed",
                        f"{usdt(limits.get('min_margin'))} – "
                        f"{usdt(limits.get('max_margin'))} USDT",
                    ),
                    ("Available", f"{usdt(limits.get('available_balance'))} USDT"),
                ],
            )
        )
        if card.custom_input:
            hints.append("Send the margin in USDT, or /cancel to go back.")
    elif card.status == CardStatus.REVIEW:
        q = card.quote
        base = plan.trading_pair.split("-")[0]
        groups.append(
            block(
                "🧮 Order review",
                [
                    entry_row(card),
                    ("Size", f"{number(q.get('quantity'))} {base}"),
                    ("Margin", f"{usdt(q.get('margin'))} USDT × {card.leverage}"),
                    (
                        "At stop",
                        f"{usdt(-Decimal(str(q.get('max_loss', 0))), True)} USDT",
                    ),
                    ("At TP1", f"{usdt(q.get('profit_tp1'), True)} USDT"),
                    ("At TP2", f"{usdt(q.get('profit_tp2'), True)} USDT"),
                    ("Liquidation", f"≈ {price(q.get('liquidation_price'))}"),
                ],
            )
        )
        hints.append(
            "Half exits at TP1; the rest trails from breakeven toward TP2. Fees included."
        )
    elif card.status == CardStatus.OPEN:
        groups += [
            block("💼 Position", position_rows(card)),
            block(live_heading(card), open_rows(card)),
        ]
        if card.execution.get("pnl_complete") is False:
            hints.append("PnL is not fully reconciled yet.")
    elif card.status == CardStatus.CLOSED:
        heading, pnl = result(card)
        groups.append(block(heading, [("PnL", pnl)]))
    elif card.status == CardStatus.PENDING:
        rows = [("Limit", price(card.quote.get("entry_price")))]
        if card.execution.get("current_price") is not None:
            rows.append(("Now", price(card.execution["current_price"])))
        groups.append(block("🕒 Limit order waiting to fill", rows))
        hints.append("Any unfilled part is cancelled at expiry.")
    elif card.status == CardStatus.FAILED:
        groups.append([ui.bold("❌ Trade not completed")])
        hints.append("Check the executor status.")
    elif card.status == CardStatus.RECONCILING:
        groups.append([ui.bold("🔍 Checking execution results")])
        hints.append("New positions and repeated actions are blocked.")
        if recovery_actions(card):
            hints.append(
                "A confirmed position can still be closed, and open entry orders "
                "can still be cancelled."
            )
    elif card.status == CardStatus.CLOSING:
        groups.append([ui.bold("⏳ Processing the position action")])
        hints.append("Wait for the result.")
    elif card.status == CardStatus.PLACING:
        groups.append([ui.bold("⏳ Submitting the order")])
        hints.append("Do not repeat the action.")
    elif card.status == CardStatus.DETACHED:
        groups.append([ui.bold("🛑 Management stopped")])
        hints.append("The position stays open; this card no longer manages orders.")
    elif card.status in HIDDEN:
        groups.append([ui.bold("Card ended")])
    if card.delete_failed:
        hints.append(
            "The message is not deleted yet; its buttons are disabled and deletion "
            "will be retried."
        )
    footer = []
    if card.note:
        footer.append(f"⚠️ {card.note[:240]}")
    elif card.blocked and card.status in UNORDERED:
        footer.append(f"⛔ Not orderable now: {card.blocked}")
    groups.append([ui.italic(text) for text in footer + hints])
    if card.confirm_action:
        label = {
            "reduce_half": "close half of the remaining position",
            "close": "close the whole position",
            "detach": "stop management, keep the position and delete the card",
        }[card.confirm_action]
        groups.append([ui.bold(f"⚠️ Tap again to confirm: {label}")])
    return ui.screen(*groups)


def keyboard(card: Card) -> InlineKeyboardMarkup | None:
    def button(text: str, action: str):
        return InlineKeyboardButton(text=text, callback_data=f"tc:{action}:{card.id}")

    if card.status in HIDDEN:
        return None
    actions = management_actions(card)
    if card.status == CardStatus.PROPOSED:
        rows = [[button("❌ Reject", "reject"), button("💰 Invest", "invest")]]
    elif card.status == CardStatus.LEVERAGE:
        top = card.limits.get("max_leverage", 0)
        presets = [lev for lev in (10, 25, 50) if lev <= top]
        row = [
            button(ui.selected(f"x{lev}", card.leverage == lev), f"l{lev}")
            for lev in presets
        ]
        if top and top not in presets:
            row.append(button(f"Max x{top}", "lmax"))
        rows = [row] if row else []
        rows.append([button("✏️ Custom", "custom"), button(ui.BACK, "back")])
    elif card.status == CardStatus.SIZING:
        low = Decimal(str(card.limits.get("min_margin", 0)))
        high = Decimal(str(card.limits.get("max_margin", 0)))
        rows = [
            [
                button(
                    ui.selected(f"Min {number(low)} USDT", card.margin == number(low)),
                    "mmin",
                )
            ]
        ]
        presets = [
            button(ui.selected(f"{amount} USDT", card.margin == amount), f"m{amount}")
            for amount in ("10", "20", "50")
            if low < Decimal(amount) <= high
        ]
        if presets:
            rows.append(presets)
        rows.append([button("✏️ Custom", "custom"), button(ui.BACK, "back")])
    elif card.status == CardStatus.REVIEW:
        first = (
            button(ui.REFRESH, "refresh")
            if card.blocked
            else button("✅ Place Order", "confirm")
        )
        rows = [[first, button(f"{ui.BACK} to Amount", "back")]]
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
    elif card.status in {CardStatus.CLOSED, CardStatus.FAILED}:
        # The caption already states the outcome and nothing is left to do.
        return None
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
