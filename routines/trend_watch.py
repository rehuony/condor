"""Scan Binance USDT perpetual trend pullbacks and publish qualified plans as Trade Cards.

The scanner owns market selection, closed-candle signals, clarity scoring and
reports. Trade Cards owns rendering, publication deduplication, interaction and
execution; its executor supports Binance USDT perpetuals only, so that is the
only market scanned. The 4H breakout signal was retired: it was negative R in
and out of sample; so was the shallow 1H EMA9 pullback, which multiplied cards
without edge. Cards need clarity 80, a pair and side waits for its previous card
to end, and at most two cards per side are in play. The plan (1.5×ATR1H stop,
1.5R/3R targets, a 0.6×ATR1H entry zone) comes from the 2026-10-23 study under
data/research/trend-watch-signal/; earlier ones are under trend-watch-clarity/.
Backtests used a 72-hour observation horizon; live positions have no time-based
exit.
"""

import asyncio
import logging
import math
import time
import uuid
from typing import Literal

import pandas as pd
import pandas_ta as ta
from pydantic import BaseModel, Field

from condor import paths, trade_cards
from condor.reports import ReportBuilder
from condor.trade_cards.models import TERMINAL, TradePlan
from condor.trade_cards.render import venue_label
from config_manager import get_client
from routines.base import RoutineContext, RoutineResult, resolve_server_name

logger = logging.getLogger(__name__)
CATEGORY = "Monitoring"
INTERVAL_SEC = {"4h": 14400, "1h": 3600, "15m": 900}
#: The one market Trade Cards can execute on.
CONNECTOR = "binance_perpetual"
SIG_NAME = {"pullback": "Trend pullback"}
SIDE_NAME = {"long": "Long", "short": "Short"}

# Tokenized stocks / ETFs / commodities listed as perps (base asset names).
# Entries of 5+ characters match as substrings, so SKHYNIX also excludes
# CSOPSKHYNIX2L.
EXCLUDE = [
    "SOXL",
    "SOXS",
    "SNDK",
    "MU",
    "SKHYNIX",
    "SKHY",
    "SAMSUNG",
    "HYUNDAI",
    "DRAM",
    "EWY",
    "QQQ",
    "SPY",
    "TQQQ",
    "SQQQ",
    "KORU",
    "INTC",
    "NVDA",
    "TSLA",
    "AAPL",
    "MSFT",
    "GOOGL",
    "AMZN",
    "META",
    "AMD",
    "PLTR",
    "COIN",
    "HOOD",
    "MSTR",
    "CRCL",
    "SPCX",
    "SNXX",
    "XAU",
    "XAG",
    "XPT",
    "XPD",
    "PAXG",
    "XAUT",
    "CL",
    "BZ",
    "NATGAS",
    "COPPER",
]


class Config(BaseModel):
    """Scan Binance USDT perpetual trend pullbacks and publish qualified plans as trade cards."""

    # Market
    top_n: int = Field(
        title="Pairs to scan",
        default=40,
        description="Scan the top N USDT perpetuals by 24h volume",
    )
    min_quote_volume: float = Field(
        title="Min 24h volume (USDT)",
        default=20_000_000,
        description="Skip pairs under this 24h volume (USDT)",
    )
    # Signal
    min_score: float = Field(
        title="Min clarity",
        default=80,
        description="Minimum trend clarity (0–100) to publish a card",
    )
    pullback_atr: float = Field(
        title="Pullback (×ATR1H)",
        default=0.6,
        description="Pulled back when price is within this × ATR1H of the 1H EMA21",
    )
    # Plan
    stop_atr: float = Field(
        title="Stop (×ATR1H)",
        default=1.5,
        description="Stop distance = this × ATR1H from the signal price",
    )
    entry_atr: float = Field(
        title="Entry zone (×ATR1H)",
        default=0.6,
        description="Entry zone width on the stop side of the signal price; a price past it toward the stop is not entered",
    )
    tp1_r: float = Field(
        title="TP1 (R)",
        default=1.5,
        description="TP1 at this multiple of the stop distance",
    )
    tp2_r: float = Field(
        title="TP2 (R)",
        default=3.0,
        description="TP2 at this multiple of the stop distance",
    )
    # Delivery
    max_same_side: int = Field(
        title="Max cards per side",
        default=2,
        ge=1,
        description="At most this many long (or short) cards in play at once; a pair and side also waits for its previous card to end",
    )
    mode: Literal["live", "mock"] = Field(
        title="Mode",
        default="live",
        description="live places real orders; mock fills at live prices without touching the exchange",
    )
    send: bool = Field(
        title="Publish cards",
        default=True,
        description="Publish trade cards to Telegram; when off, only the report is saved and nothing is sent",
    )


# ── formatting ──────────────────────────────────────────────────────────


def _fmt(v: float) -> str:
    """Text price: ≥5 significant digits (≥2 decimals); below 1, ≥5 decimals."""
    v = float(v)
    if v == 0:
        return "0.00"
    if abs(v) >= 1:
        return f"{v:.{max(2, 4 - math.floor(math.log10(abs(v))))}f}"
    return f"{v:.{max(5, 3 - math.floor(math.log10(abs(v))))}f}"


def _dec(x: float, thr: float) -> int:
    """2 decimals, 3 when rounding would print the threshold itself (0.698 → "0.70 < 0.70")."""
    return 3 if f"{x:.2f}" == f"{thr:.2f}" else 2


def _cmp(x: float, op: str, thr: float, unit: str = "") -> str:
    """Actual value vs threshold in the same unit and precision: "0.59 < 0.70", "0.91× > 0.80×"."""
    d = _dec(x, thr)
    return f"{x:.{d}f}{unit} {op} {thr:.{d}f}{unit}"


def _numbered(steps: list[str]) -> str:
    """ "① a; ② b" — numbered so a mobile reader can count what is left."""
    return "; ".join(f"{chr(0x2460 + i)} {s}" for i, s in enumerate(steps))


# ── data ────────────────────────────────────────────────────────────────


async def _candles(
    client, cfg: Config, pair: str, interval: str, days: int, sem
) -> pd.DataFrame:
    async with sem:
        rows = await client.market_data.get_candles_last_days(
            CONNECTOR, pair, days=days, interval=interval
        )
    if isinstance(rows, dict):
        rows = rows.get("data") or rows.get("candles") or []
    df = pd.DataFrame(rows or [])
    if df.empty:
        return df
    df = df[["timestamp", "open", "high", "low", "close", "volume"]].astype(float)
    if df["timestamp"].iloc[-1] > 1e11:
        df["timestamp"] /= 1000
    df = df.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    for n in (9, 20, 21, 50):
        df[f"ema{n}"] = ta.ema(df["close"], length=n)
    df["rsi"] = ta.rsi(df["close"], length=14)
    df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)
    return df


async def _fetch_pair(client, cfg: Config, pair: str, sem) -> dict:
    m15, h1, h4 = await asyncio.gather(
        _candles(client, cfg, pair, "15m", 2, sem),
        _candles(client, cfg, pair, "1h", 7, sem),
        _candles(client, cfg, pair, "4h", 30, sem),
    )
    return dict(m15=m15, h1=h1, h4=h4)


def _closed(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    """Drop the still-forming last candle."""
    if not df.empty and df["timestamp"].iloc[-1] + INTERVAL_SEC[interval] > time.time():
        return df.iloc[:-1].reset_index(drop=True)
    return df


def _excluded(base: str, exclude: list[str]) -> bool:
    return any(
        base == x or (len(x) >= 5 and x in base) for x in (e.upper() for e in exclude)
    )


def _universe(tickers: dict, cfg: Config) -> list[str]:
    rows = []
    for pair, t in tickers.items():
        if not pair.endswith("-USDT") or _excluded(pair[:-5], EXCLUDE):
            continue
        qv = float((t or {}).get("quote_volume") or 0)
        if qv >= cfg.min_quote_volume:
            rows.append((pair, qv))
    rows.sort(key=lambda r: -r[1])
    return [p for p, _ in rows[: cfg.top_n]]


# ── signals (same rules for every pair) ─────────────────────────────────


def _plan(side: str, last: float, atr1h: float, cfg: Config) -> dict:
    """Stop/targets as fixed multiples of ATR1H from the signal price (as backtested).

    The entry zone sits on the stop side of the signal price, so a fill is never
    worse than the plan: price past the signal price waits for a limit at it.
    """
    sgn = 1 if side == "long" else -1
    risk = cfg.stop_atr * atr1h
    edge = last - sgn * cfg.entry_atr * atr1h
    return dict(
        side=side,
        lo=min(last, edge),
        hi=max(last, edge),
        mid=last,
        stop=last - sgn * risk,
        tp1=last + sgn * cfg.tp1_r * risk,
        tp2=last + sgn * cfg.tp2_r * risk,
        rr1=cfg.tp1_r,
        rr2=cfg.tp2_r,
    )


def _pullback(m15, h1, last, cfg):
    """1H EMA9/21/50 aligned, price back at 1H EMA21, 15m momentum turning with the trend.

    Returns (plan or None, progress or None). progress records how far the pair got through
    the steps (side, dist to EMA21 in ATR1H, touched, turned) so the report can say what is
    still missing; None means the 1H EMAs are not aligned at all.
    """
    c = h1.iloc[-1]
    atr = float(c.atr)
    up = c.ema9 > c.ema21 > c.ema50 and c.close > c.ema50
    dn = c.ema9 < c.ema21 < c.ema50 and c.close < c.ema50
    if not (up or dn):
        return None, None
    side = "long" if up else "short"
    dist = abs(last - c.ema21) / atr
    if up:
        touched = h1.low.iloc[-3:].min() <= c.ema21 + 0.25 * atr
    else:
        touched = h1.high.iloc[-3:].max() >= c.ema21 - 0.25 * atr
    prog = dict(side=side, dist=dist, touched=bool(touched), turned=False)
    if dist > cfg.pullback_atr or not touched:
        return None, prog
    k, prev = m15.iloc[-1], m15.iloc[-4:-1]
    if up:
        turn = (
            k.close > k.ema9
            and k.close > k.open
            and k.rsi > m15.rsi.iloc[-2]
            and (prev.close < prev.ema9).any()
        )
    else:
        turn = (
            k.close < k.ema9
            and k.close < k.open
            and k.rsi < m15.rsi.iloc[-2]
            and (prev.close > prev.ema9).any()
        )
    if not turn:
        return None, prog
    prog["turned"] = True
    pl = _plan(side, last, atr, cfg)
    pl["bar"] = float(k.timestamp)  # trigger candle: one signal per closed 15m bar
    if up:
        pl["why"] = (
            f"1H EMA9>21>50 bullish stack; pulled back to EMA21 ({_fmt(c.ema21)}), then a green 15m close back above EMA9"
        )
    else:
        pl["why"] = (
            f"1H EMA9<21<50 bearish stack; bounced to EMA21 ({_fmt(c.ema21)}), then a red 15m close back below EMA9"
        )
    return pl, prog


def _scan_pair(cfg: Config, pair: str, data: dict) -> dict:
    """Evaluate one pair: the pullback signal, then its clarity. ``plan`` is tradable."""
    base = pair.split("-")[0]
    m15_raw, h1_raw, h4_raw = data["m15"], data["h1"], data["h4"]
    out = dict(
        pair=pair,
        base=base,
        plan=None,
        blocked=None,
        hit=False,
        skip=None,
        prog=None,
        clar=None,
        gap=None,
    )
    if len(m15_raw) < 100 or len(h1_raw) < 60 or len(h4_raw) < 60:
        out["skip"] = (
            f"not enough candles (15m {len(m15_raw)} / 1H {len(h1_raw)} / 4H {len(h4_raw)})"
        )
        return out
    m15, h1, h4 = _closed(m15_raw, "15m"), _closed(h1_raw, "1h"), _closed(h4_raw, "4h")
    last = float(m15_raw.close.iloc[-1])
    atr15 = float(m15.atr.iloc[-1])
    h4_up = bool(h4.ema20.iloc[-1] > h4.ema50.iloc[-1])
    out.update(
        last=last, atr_pct=atr15 / last * 100, rsi1h=float(h1.rsi.iloc[-1]), h4_up=h4_up
    )

    pl, prog = _pullback(m15, h1, last, cfg)
    if pl:
        out["hit"] = True
        c = _clarity(h4, h1, pl["side"])
        pl.update(
            type="pullback",
            tf="1H",
            df=h1_raw,
            pair=pair,
            base=base,
            atr_pct=out["atr_pct"],
            clar=c,
            score=c["score"],
        )
        if c["score"] < cfg.min_score:
            out["blocked"] = dict(
                plan=pl,
                fails=[f"clarity {_cmp(c['score'], '<', cfg.min_score)} ({_parts(c)})"],
            )
        else:
            out["plan"] = pl
    if prog:
        out["clar"] = _clarity(h4, h1, prog["side"])
        out["prog"] = prog
        if not out["plan"]:
            out["gap"] = _gap(cfg, prog, out["clar"], out["blocked"])
    return out


def _clarity(h4, h1, side) -> dict:
    """Trend clarity 0–100: mean of three 0–1 parts, each signed so 'with the trade' is positive.

    er1h: 1H efficiency ratio over 20 bars (0 → 0, ≥ 0.5 → 1); trend1h: (EMA9−EMA50)/ATR1H
    (≤ 0.3 → 0, ≥ 2.0 → 1); h4: 4H EMA20 vs EMA50 on the trade's side (1 / 0). Anchors and the
    component set were chosen on the in-sample backtest (see module docstring).
    """
    sgn = 1 if side == "long" else -1
    c, b4 = h1.iloc[-1], h4.iloc[-1]
    cl = h1.close.iloc[-21:]
    path = float(cl.diff().abs().sum())
    er = sgn * float(cl.iloc[-1] - cl.iloc[0]) / path if path > 0 else 0.0
    t1 = sgn * float(c.ema9 - c.ema50) / float(c.atr)
    h4_with = (b4.ema20 > b4.ema50) == (side == "long")
    parts = dict(
        er1h=min(1.0, max(0.0, er / 0.5)),
        trend1h=min(1.0, max(0.0, (t1 - 0.3) / 1.7)),
        h4=1.0 if h4_with else 0.0,
    )
    return dict(
        score=round(100 * sum(parts.values()) / 3, 1),
        er1h=er,
        trend1h=t1,
        h4_with=bool(h4_with),
        parts=parts,
    )


def _parts(c) -> str:
    """ "1H eff 0.31 · 1H strength 1.42 · 4H aligned" — the score's inputs, for captions and the report."""
    return f"1H eff {c['er1h']:.2f} · 1H strength {c['trend1h']:.2f} · 4H {'aligned' if c['h4_with'] else 'opposed'}"


def _gap(cfg, prog, clar, blocked) -> dict:
    """What a pair with aligned 1H EMAs still lacks before it becomes a pullback card.

    steps lists every missing condition in rule order; pairs are ranked by clarity score
    (highest first) in the report, so rank only breaks ties by missing price steps.
    ``triggered`` marks a pair whose price setup is complete and only clarity is short.
    """
    if blocked:
        return dict(steps=blocked["fails"], triggered=True, rank=(-clar["score"], 0))
    side = prog["side"]
    steps = []
    verb = "pull back" if side == "long" else "bounce"
    if prog["dist"] > cfg.pullback_atr:
        d = _dec(prog["dist"], cfg.pullback_atr)
        steps.append(
            f"{verb} to near EMA21 (now {prog['dist']:.{d}f}×ATR1H away, needs ≤ {cfg.pullback_atr:.{d}f}×ATR1H)"
        )
    elif not prog["touched"]:
        steps.append(
            f"no {verb} to near EMA21 in the last 3 1H bars (within ±0.25×ATR1H)"
        )
    steps.append(
        f"15m {'green close above' if side == 'long' else 'red close below'} EMA9"
    )
    if clar["score"] < cfg.min_score:
        steps.append(f"clarity {_cmp(clar['score'], '<', cfg.min_score)}")
    return dict(steps=steps, triggered=False, rank=(-clar["score"], len(steps)))


def _need(gap: dict) -> str:
    """One-cell summary for the report table."""
    prefix = "15m triggered; " if gap["triggered"] else f"{len(gap['steps'])} left: "
    return prefix + _numbered(gap["steps"])


# ── dedupe state (coin + side + signal type) ────────────────────────────


def _line(pl) -> str:
    return (
        f"{pl['base']} {SIDE_NAME[pl['side']]} · {SIG_NAME[pl['type']]} · {pl['tf']} · "
        f"entry {_fmt(pl['lo'])}–{_fmt(pl['hi'])} · stop {_fmt(pl['stop'])} · "
        f"TP {_fmt(pl['tp1'])} / {_fmt(pl['tp2'])} (R:R 1:{pl['rr1']:.1f} / {pl['rr2']:.1f})"
    )


def _trade_plan(plan: dict) -> TradePlan:
    return TradePlan(
        trading_pair=plan["pair"],
        side=plan["side"],
        entry_low=str(plan["lo"]),
        entry_high=str(plan["hi"]),
        stop_price=str(plan["stop"]),
        tp1_price=str(plan["tp1"]),
        tp2_price=str(plan["tp2"]),
        timeframe=plan["tf"],
        signal_type=plan["type"],
        signal_price=str(plan["mid"]),
        score=plan["score"],
    )


def _limited(plan: dict, active: list, cfg: Config) -> str | None:
    """Why a qualified plan is held back by the cards still in play, if it is."""
    same_side = [card for card in active if card.plan.side == plan["side"]]
    if any(card.plan.trading_pair == plan["pair"] for card in same_side):
        return "a card for this pair and side is still active"
    if len(same_side) >= cfg.max_same_side:
        return f"{len(same_side)} {plan['side']} cards are already active"
    return None


def _summary(head: str, verdict: str, sections: list[tuple[str, list[str]]]) -> str:
    """The run's chat-sized summary: a heading, a verdict, then titled groups."""
    blocks = [f"{head}\n{verdict}"]
    for title, lines in sections:
        if lines:
            blocks.append("\n".join([title, *lines]))
    return "\n\n".join(blocks)


def _closest_lines(r) -> list[str]:
    """A near-miss pair as a bullet with its missing steps indented beneath."""
    trend = "bullish" if r["prog"]["side"] == "long" else "bearish"
    tag = " · triggered" if r["gap"]["triggered"] else ""
    lines = [
        f"• {r['base']} {_fmt(r['last'])} · 1H {trend} · clarity {r['clar']['score']:.0f}{tag}"
    ]
    lines += [f"  {chr(0x2460 + i)} {step}" for i, step in enumerate(r["gap"]["steps"])]
    return lines


async def run(config: Config, context: RoutineContext) -> str | RoutineResult:
    t0 = time.time()
    client = await get_client(
        context.chat_id,
        user_data=context.user_data,
        server=resolve_server_name(context),
    )
    if not client:
        return "No server available"

    tk = await client.market_data.get_tickers(CONNECTOR)
    tickers = ((tk or {}).get("tickers") or {}).get(CONNECTOR) or {}
    if not tickers:
        return f"Could not load the {CONNECTOR} ticker list; skipped this run"
    pairs = _universe(tickers, config)

    sem = asyncio.Semaphore(10)
    fetched = await asyncio.gather(
        *[_fetch_pair(client, config, p, sem) for p in pairs], return_exceptions=True
    )
    results, failed = [], []
    for pair, data in zip(pairs, fetched):
        if isinstance(data, Exception):
            logger.warning(f"trend_watch {pair}: {data}")
            failed.append(pair.split("-")[0])
            continue
        results.append(_scan_pair(config, pair, data))
    skipped = [f"{r['base']}: {r['skip']}" for r in results if r["skip"]]
    results = [r for r in results if not r["skip"]]

    candidates = sorted(
        (r["plan"] for r in results if r["plan"]), key=lambda plan: -plan["score"]
    )
    # Cards still in play, so a pair and side waits for its previous card to end
    # and one side cannot pile up (the same market move bet several times).
    active = (
        [
            card
            for card in trade_cards.list_cards(owner_id=context.owner_id)
            if card.status not in TERMINAL and card.connector_name == CONNECTOR
        ]
        if config.send and context.owner_id
        else []
    )
    muted, cards = [], []
    for plan in candidates:
        trade_plan = _trade_plan(plan)
        plan["caption"] = _line(plan)
        if config.send:
            if not context.chat_id or not context.owner_id:
                raise ValueError("Publishing trade cards requires an owner and chat")
            if reason := _limited(plan, active, config):
                muted.append((plan, reason))
                continue
            try:
                published = await trade_cards.publish(
                    trade_plan,
                    plan["df"],
                    owner_id=context.owner_id,
                    chat_id=context.chat_id,
                    server_name=resolve_server_name(context),
                    account_name="master_account",
                    connector_name=CONNECTOR,
                    bot=context.bot,
                    signal_id=f"trend_watch:{plan['type']}:{plan['tf']}:{plan['bar']}",
                    mode=config.mode,
                )
            except trade_cards.PlanOutdated as exc:
                # Price moved past the plan since the scan; the others still go out.
                skipped.append(f"{plan['base']}: {exc}")
                continue
            if published is None:
                muted.append((plan, "a card was already sent for this trigger bar"))
                continue
            active.append(published)
        else:
            # A preview does not reserve a signal or mutate any trading state.
            png = await asyncio.to_thread(
                trade_cards.render_chart, plan["df"], trade_plan, CONNECTOR
            )
            preview = (
                paths.data_dir()
                / "trade_cards"
                / "previews"
                / f"{uuid.uuid4().hex}.png"
            )
            preview.parent.mkdir(parents=True, exist_ok=True)
            preview.write_bytes(png)
            plan["card"] = str(preview)
        cards.append(plan)

    elapsed = time.time() - t0
    hits = sum(r["hit"] for r in results)
    # Closest to a card: highest clarity first (see _gap)
    near = sorted((r for r in results if r["gap"]), key=lambda r: r["gap"]["rank"])

    # ── report ──
    builder = ReportBuilder(f"Trend Watch · {venue_label(CONNECTOR)}")
    builder.source("routine", "trend_watch")
    builder.tags(["trend", "monitor", "scanner"])
    builder.kpi("Pairs scanned", str(len(results)))
    builder.kpi("Signals", str(hits))
    builder.kpi("Cards", str(len(cards)))
    builder.kpi("Duration", f"{elapsed:.1f}s")

    def _row(p, status):
        c = p["clar"]
        return {
            "Pair": p["base"],
            "Side": SIDE_NAME[p["side"]],
            "TF": p["tf"],
            "Entry": f"{_fmt(p['lo'])}–{_fmt(p['hi'])}",
            "Stop": _fmt(p["stop"]),
            "TP1": _fmt(p["tp1"]),
            "TP2": _fmt(p["tp2"]),
            "R:R": f"1:{p['rr1']:.1f}/{p['rr2']:.1f}",
            "Clarity": f"{p['score']:.0f}",
            "1H eff": f"{c['er1h']:.2f}",
            "1H strength": f"{c['trend1h']:.2f}",
            "4H": "aligned" if c["h4_with"] else "opposed",
            "Status": status,
        }

    cols = [
        "Pair",
        "Side",
        "TF",
        "Entry",
        "Stop",
        "TP1",
        "TP2",
        "R:R",
        "Clarity",
        "1H eff",
        "1H strength",
        "4H",
        "Status",
    ]
    if cards:
        builder.table(
            [_row(p, "Card" if config.send else "Card (not sent)") for p in cards], cols
        )

    # Every candidate that triggered but did not become a card, with what stopped it
    blocked_rows = [
        _row(r["blocked"]["plan"], _numbered(r["blocked"]["fails"]))
        for r in results
        if r["blocked"]
    ]
    blocked_rows += [_row(p, why) for p, why in muted]
    if blocked_rows:
        builder.markdown(
            "**Triggered without a card** (a filter failed, or a card limit applied)"
        )
        builder.table(blocked_rows, cols)
    builder.markdown(
        f"**Card rules** (trend pullback): ① 1H EMA9/21/50 stacked with the trend → "
        f"② price within {config.pullback_atr:.2f}×ATR1H of the 1H EMA21, with a pullback "
        f"or bounce to near EMA21 in the last 3 1H bars → ③ a green 15m close above "
        f"(red close below) EMA9 → ④ trend clarity ≥ {config.min_score:g} (mean of 1H "
        f"efficiency ratio, 1H trend strength and 4H alignment). Plan: stop "
        f"{config.stop_atr:g}×ATR1H, TP1/TP2 {config.tp1_r:g}R/{config.tp2_r:g}R, entry zone "
        f"{config.entry_atr:g}×ATR1H on the stop side. Limits: a pair and side waits for its "
        f"previous card to end; at most {config.max_same_side} cards per side; one card per "
        f"trigger bar"
    )

    def _status(r):
        if r["plan"]:
            return "qualified signal"
        if r["gap"]:
            return _need(r["gap"])
        return "1H EMAs not stacked; no pullback signal"

    scan_rows = sorted(
        results,
        key=lambda r: (not r["plan"], -(r["clar"]["score"] if r["clar"] else -1)),
    )
    scan_cols = [
        "Pair",
        "Price",
        "1H stack",
        "To EMA21 (×ATR1H)",
        "Clarity",
        "1H eff",
        "1H strength",
        "4H trend",
        "To card",
    ]
    rows = []
    for r in scan_rows:
        p, c = r["prog"], r["clar"]
        rows.append(
            {
                "Pair": r["base"],
                "Price": _fmt(r["last"]),
                "1H stack": (
                    ("bullish" if p["side"] == "long" else "bearish") if p else "—"
                ),
                "To EMA21 (×ATR1H)": f"{p['dist']:.2f}" if p else "—",
                "Clarity": f"{c['score']:.0f}" if c else "—",
                "1H eff": f"{c['er1h']:.2f}" if c else "—",
                "1H strength": f"{c['trend1h']:.2f}" if c else "—",
                "4H trend": "bullish" if r["h4_up"] else "bearish",
                "To card": _status(r),
            }
        )
    builder.table(rows, scan_cols)
    md = [
        f"**{p['base']} {SIDE_NAME[p['side']]} · clarity {p['score']:.0f}**: "
        f"{p['why']}; {_parts(p['clar'])}"
        for p in cards
    ]
    if failed or skipped:
        md.append(
            "**Not scanned**: "
            + "; ".join([f"{b}: fetch failed" for b in failed] + skipped)
        )
    builder.markdown("\n\n".join(md) or "No signals")
    builder.manual_order()
    await builder.save()

    head = f"Trend scan · {len(results)} pairs · {elapsed:.0f}s"
    not_scanned = [f"• {b}: fetch failed" for b in failed] + [f"• {s}" for s in skipped]
    if not cards:
        text = _summary(
            head,
            "Result: no qualified signal; still waiting",
            [
                (
                    "Closest to a card (by clarity)",
                    [line for r in near[:3] for line in _closest_lines(r)],
                ),
                (
                    "Skipped by card limits",
                    [
                        f"• {p['base']} {SIDE_NAME[p['side']]}: {why}"
                        for p, why in muted
                    ],
                ),
                ("Not scanned", not_scanned),
            ],
        )
    else:
        sent = "" if config.send else " (send is off, nothing was sent)"
        text = _summary(
            head,
            f"Result: {len(cards)} cards{sent}",
            [
                ("Cards", [f"• {p['caption']}" for p in cards]),
                (
                    "Skipped by card limits",
                    [
                        f"• {p['base']} {SIDE_NAME[p['side']]}: {why}"
                        for p, why in muted
                    ],
                ),
                ("Previews", [f"• {p['card']}" for p in cards if p.get("card")]),
                ("Not scanned", not_scanned),
            ],
        )
    # Published cards carry their own context and a scan without a card is not
    # news, so a scheduled run never posts this summary; it stays in the report
    # and the routine's last result.
    return RoutineResult(text=text, silent=True)
