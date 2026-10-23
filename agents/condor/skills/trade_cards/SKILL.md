---
name: trade_cards
description: Render trade-plan charts and publish interactive Telegram cards with leverage and margin sizing, review, live refresh, execution tracking, and position management through the independent Trade Cards module.
when_to_use: Use when presenting an entry, stop-loss, and take-profit plan; publishing a trade card; or inspecting an order or position created from a card. Trend Watch already publishes through this module. This skill does not schedule scanners or authorize trades.
source: builtin
---

# Trade Cards

Use `condor.trade_cards` as the single implementation for trade-plan images and
interactive cards. Trend Watch supplies signals; the card module owns delivery,
interaction, persistence and tracking. The Hummingbot API owns order execution
and position protection. Do not load routine files with `importlib`, start
`trailing_entry`, edit card JSON, or reproduce trading logic in ad-hoc code.

## Render or publish

Use `run_code`, which runs in the Condor process, with its existing `context` and
market-data client. Fetch actual candles for the plan's timeframe and construct
one plan for one market and direction:

```python
from condor.trade_cards import TradePlan, render_chart, publish
from routines.base import resolve_server_name

plan = TradePlan(
    trading_pair="SOL-USDT", side="long",
    entry_low="102.7", entry_high="103.2",
    stop_price="101.2", tp1_price="105.1", tp2_price="106.9",
    timeframe="1H",
)
# `candles` must contain actual timestamp/open/high/low/close values.
png = render_chart(candles, plan)
```

The values above illustrate the interface; fetch current data and derive the
actual plan before presenting it. Rendering has no publication or trading side
effects. For a conversation-only image, save the PNG under `/tmp/condor_out/`
and return it through the existing image-delivery tools. Inspect the image for
missing glyphs and overlapping text before sending it. Keep the existing dark
chart style and plan prices; change the shared renderer when a style change is
requested.

For an interactive Telegram card, publish directly rather than separately
sending the image:

```python
card = await publish(
    plan, candles,
    owner_id=context.owner_id, chat_id=context.chat_id,
    server_name=resolve_server_name(context),
    account_name="master_account", connector_name="binance_perpetual",
    bot=context.bot,
)
```

`mode` is `live` (the default) or `mock`. Use `mode="mock"` when the user wants
to test the flow without real orders; Trend Watch takes it from its `mode`
setting. Publishing does not place an order: the owner chooses a leverage and a
margin and confirms the review on the card. Each card keeps its mode and server/account
identity. Never turn a mock card into a live one or silently switch its trading
target.

Automated sources supply a stable `signal_id` describing the signal type,
timeframe and trigger candle. `publish` returns `None` for an already reserved
signal in the same recipient and trading scope. Card-count limits belong to the
source: Trend Watch lets a pair and side wait for its previous card to end and
keeps at most `max_same_side` (default 2) cards per side in play. A send with an unknown outcome retains its reservation to prevent
duplicate delivery. An unknown Telegram message ID cannot be recovered
automatically. Pure preview does not reserve a signal.

`publish` checks the live price first and raises `PlanOutdated` without
sending or reserving when the price is already beyond the stop or TP1; the
tracker would otherwise expire and delete that card on its first tick. A test
card that must not fill therefore keeps the price between the stop and TP1 with
the entry zone away from it (for a long: stop < entry zone < price < TP1).

## Owner interaction and execution rules

- One Telegram photo message is edited in place. Only its owner can operate it.
  `💰 Invest` opens leverage, then margin, then a review. `✅ Place Order` is
  the order confirmation; do not add a second confirmation message.
- Leverage presets are **x10 / x25 / x50** plus custom whole numbers. The card
  shows the allowed range: the lower of the pair's exchange maximum and the
  highest leverage whose estimated liquidation stays beyond the stop. Presets
  outside it are hidden (a `Max` button covers a range below them); custom
  values outside it are refused on the card and can be sent again.
- Margin comes after leverage because the minimum depends on it. The card shows
  the minimum, the maximum (available balance and leverage bracket) and the
  available balance; presets **10 / 20 / 50 USDT** below the minimum are hidden
  and `Min` selects the minimum. Every card starts at 10x (or the range's top
  if lower) and the minimum margin: nothing is remembered between cards.
  `/cancel` exits custom entry. The review displays actual rounded quantities,
  notional, margin × leverage, estimated loss at the stop, liquidation
  estimate, target profits and entry method.
- Unordered cards refresh every 60 seconds: the live price against the signal
  price, the distance to the stop and TP1, whether it can enter now, and the
  time left; the leverage and margin steps re-read their limits and the review
  re-reads its quote. A review that can no longer be ordered says why and
  replaces `Place Order` with `Refresh` until it can. Confirmation still checks
  again. The chart is redrawn once per closed 15m bar.
- Every refused action writes its reason on the card (and in a popup when the
  executor answered quickly); a button is answered exactly once. The card shows
  one alert at a time, below its rows: the latest refusal, otherwise why it
  cannot be ordered now. A closed or failed card keeps no buttons.
- Live cards support Binance USDT single-asset isolated margin at the chosen
  leverage with automatic margin addition disabled. Existing positions, orders
  or another managed group on the same symbol prevent a new group. The API
  sets and then verifies the exchange's actual margin mode and leverage before
  submitting orders.
- At confirmation, a price inside the entry zone selects MARKET; a price past
  the zone toward the targets selects a LIMIT at the zone's near edge and waits
  for the pullback. A price past the zone toward the stop is refused rather
  than filled outside the plan. Trend Watch puts the zone on the stop side of
  the signal price (0.6×ATR1H wide), so no fill is worse than the planned
  reward/risk. There is no trailing entry. Quantity and risk preflight belong
  to the API; do not substitute fixed maintenance-margin assumptions or enlarge
  the budget to meet a minimum order size.
- Two executor legs divide the position between TP1 and TP2. Both use the
  absolute stop. The second leg arms its trailing protection at TP1, moves the
  stop at least to its actual entry price, then follows by the initial 1R.
  Price breakeven does not imply zero fees. Exits use mark-price triggers.
- The four-hour TTL limits proposals and entry orders, **not held positions**.
  At expiry, cancel remaining entry quantity and retain protection for any
  fills. Positions exit by SL, TP, trailing protection or a manual action;
  never add `time_limit` or a 48-hour forced close.
- `Close Half` means half the currently remaining position, including after TP1.
  Close and detach actions require a second tap. Below-minimum reductions are
  refused rather than silently turned into a full close.
- Rejection, unfilled cancellation and invalid unused proposals remove the
  message. Completed trades retain their result. Confirmed `🛑 Stop Managing` cancels
  outstanding managed orders, keeps the actual position, and deletes the
  message; it is not a position close. If deletion fails, remove its controls,
  display the detached state and retry deletion.

## Inspect and handle failures

```python
from condor.trade_cards import get_card, list_cards

cards = list_cards(owner_id=context.owner_id)
card = get_card(card_id, owner_id=context.owner_id)
```

Use the card's persisted identity and tracked API group. Telegram adapters use
`condor.trade_cards.service.act(card_id, action, owner_id=..., chat_id=...,
message_id=..., bot=..., value=...)`; it owns authorization, card locks, confirmations and
execution calls. Ordinary chat requests should direct the owner to those card
buttons. Do not call routine management, bypass review/confirmation, or create
replacement executors. Never use another user's card or a guessed order ID.

Distinguish proposed, pending, open, reconciling, closing, closed and detached.
An API error is not evidence of no order or zero position. During an unknown
submission or recovery, do not manually resubmit, delete the record or describe
the position as closed. The service can replay the exact persisted request and
operation ID against the same API; it must never create a new identity to retry
an unknown result. Read the API's actual group state after recovery.

Mock uses live prices but a separate simulated broker in the executor and
performs no exchange writes. It exercises interaction and state transitions,
not real fills or slippage. Condor restarts do not stop API-managed positions;
Hummingbot API downtime interrupts its software protection until reconciliation
and management resume. Report this distinction when discussing recovery.

## Presentation and scanning

Telegram captions, buttons and alerts are English, like the rest of the UI;
conversation replies still follow the user's language. In conversation,
pair the chart with a concise conclusion, prices, selected margin if known,
entry method, leverage, risk estimate, reasons and invalidation conditions.
Keep execution amounts out of the image itself.

Trend Watch publishes only the 1H EMA21 pullback with clarity ≥ 80 (the shallow
EMA9 pullback and the 4H breakout were retired). Its plan is a 1.5×ATR1H stop
from the signal price, TP1/TP2 at 1.5R/3R and a 0.6×ATR1H entry zone, from the
2026-10-23 study under `data/research/trend-watch-signal/`. The same trigger
candle is deduplicated, and the card limits above apply. `send=False` renders a
preview and report without publishing or affecting existing cards. Do not
create, start or schedule a scanner unless the user requests it. Historical
backtests used a 72-hour observation horizon; live positions have no timed exit.
