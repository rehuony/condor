---
name: trade_cards
description: Render trade-plan charts and publish interactive Telegram cards with margin sizing, review, execution tracking, and position management through the independent Trade Cards module.
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

The default execution mode comes from `TRADE_CARDS_MODE` (`dry_run` by default).
An explicit `mode="dry_run"` always creates a simulated card. Use `mode="live"`
only when the user requests a live interactive card. Publishing does not place
an order: the owner chooses a margin and confirms the review on the card.
Each card keeps its original mode and server/account identity. Never convert a
simulated card into a live one or silently switch its trading target.

Automated sources supply a stable `signal_id` describing the signal type,
timeframe and trigger candle. `publish` returns `None` for an already reserved
signal in the same recipient and trading scope. There is no card-count cap or
cooldown. A send with an unknown outcome retains its reservation to prevent
duplicate delivery. An unknown Telegram message ID cannot be recovered
automatically. Pure preview does not reserve a signal.

## Owner interaction and execution rules

- One Telegram photo message is edited in place. Only its owner can operate it.
  Investment opens margin selection, then a review. `✅ 确认下单` is the order
  confirmation; do not add a second confirmation message.
- Presets are **10 / 20 / 50 USDT of margin**, plus custom input. Remember the
  last selection. `/cancel` exits custom entry. The review displays actual
  rounded quantities, notional, margin, estimated loss at the stop, liquidation
  estimate, target profits and entry method.
- Live cards support Binance USDT single-asset isolated margin at **10x** with
  automatic margin addition disabled. Existing positions, orders or another
  managed group on the same symbol prevent a new group. The API verifies the
  exchange's actual margin mode and leverage before submitting orders.
- At confirmation, a price inside the entry zone selects MARKET; otherwise
  LIMIT uses the nearest zone edge. A marketable limit can fill immediately.
  There is no trailing entry. Quantity and risk preflight belong to the API;
  do not substitute fixed maintenance-margin assumptions or enlarge the budget
  to meet a minimum order size.
- Two executor legs divide the position between TP1 and TP2. Both use the
  absolute stop. The second leg arms its trailing protection at TP1, moves the
  stop at least to its actual entry price, then follows by the initial 1R.
  Price breakeven does not imply zero fees. Exits use mark-price triggers.
- The four-hour TTL limits proposals and entry orders, **not held positions**.
  At expiry, cancel remaining entry quantity and retain protection for any
  fills. Positions exit by SL, TP, trailing protection or a manual action;
  never add `time_limit` or a 48-hour forced close.
- `平一半` means half the currently remaining position, including after TP1.
  Close and detach actions require a second tap. Below-minimum reductions are
  refused rather than silently turned into a full close.
- Rejection, unfilled cancellation and invalid unused proposals remove the
  message. Completed trades retain their result. Confirmed `停止托管` cancels
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
message_id=..., bot=...)`; it owns authorization, card locks, confirmations and
execution calls. Ordinary chat requests should direct the owner to those card
buttons. Do not call routine management, bypass review/confirmation, or create
replacement executors. Never use another user's card or a guessed order ID.

Distinguish proposed, pending, open, reconciling, closing, closed and detached.
An API error is not evidence of no order or zero position. During an unknown
submission or recovery, do not manually resubmit, delete the record or describe
the position as closed. The service can replay the exact persisted request and
operation ID against the same API; it must never create a new identity to retry
an unknown result. Read the API's actual group state after recovery.

Dry-run uses live prices but a separate simulated broker and performs no
exchange writes. It can exercise interaction and state transitions, not prove
real fills or slippage. Condor restarts do not stop API-managed positions;
Hummingbot API downtime interrupts its software protection until reconciliation
and management resume. Report this distinction when discussing recovery.

## Presentation and scanning

Telegram captions, buttons and alerts use Simplified Chinese. In conversation,
pair the chart with a concise conclusion, prices, selected margin if known,
entry method, leverage, risk estimate, reasons and invalidation conditions.
Keep execution amounts out of the image itself.

Trend Watch retains its clarity score and threshold of 80, with breakout off by
default. Only the same trigger candle is deduplicated. `send=False` renders a
preview and report without publishing or affecting existing cards. Do not
create, start or schedule a scanner unless the user requests it. Historical
backtests used a 48-hour observation horizon; live positions have no timed exit.
