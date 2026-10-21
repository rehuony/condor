"""Scan perpetual trends on one connector and publish qualified plans as Trade Cards.

The scanner owns market selection, closed-candle signals, clarity scoring and
reports. Trade Cards owns rendering, publication deduplication, interaction and
execution. The clarity threshold remains 80, with no card-count or cooldown cap.
Historical validation used a 48-hour observation horizon; live positions have no
time-based exit. The preserved study is under .condor/research/trend-watch-clarity/.
"""

import asyncio
import logging
import math
import time
import uuid

import pandas as pd
import pandas_ta as ta
from pydantic import BaseModel, Field

from condor import paths, trade_cards
from condor.reports import ReportBuilder
from condor.trade_cards.models import TradePlan
from condor.trade_cards.render import venue_label
from config_manager import get_client
from routines.base import RoutineContext, RoutineResult, resolve_server_name

logger = logging.getLogger(__name__)
CATEGORY = "Monitoring"
INTERVAL_SEC = {"4h": 14400, "1h": 3600, "15m": 900}
SIG_NAME = {"pullback": "顺势回调", "breakout": "4H突破"}
SIDE_NAME = {"long": "做多", "short": "做空"}

# Tokenized stocks / ETFs / commodities listed as perps (base asset names).
DEFAULT_EXCLUDE = [
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
    """Scan trend pullbacks and publish plans meeting the clarity threshold."""

    connector_name: str = Field(default="binance_perpetual", description="交易所")
    top_n: int = Field(default=40, description="按 24h 成交额取前 N 个 -USDT 合约")
    min_quote_volume: float = Field(
        default=20_000_000, description="24h 成交额下限(USDT),低于不扫"
    )
    exclude: list[str] = Field(
        default=DEFAULT_EXCLUDE,
        description="排除的基础币名(股票/ETF/商品);≥5 个字符的项按包含匹配,如 SKHYNIX 也排除 CSOPSKHYNIX2L",
    )
    min_score: float = Field(default=80, description="走势清晰度(0–100)≥ 此值才出卡")
    enable_breakout: bool = Field(
        default=False, description="是否出 4H 突破卡片(回测样本内外均为负 R,默认关闭)"
    )
    pullback_atr: float = Field(
        default=0.6,
        description="顺势回调:现价距 1H EMA21 ≤ 此倍数 × ATR1H 视为回调到位",
    )
    vol_mult: float = Field(
        default=1.5,
        description="4H 突破(仅 enable_breakout=True 时生效):突破 K 线成交量 ≥ 前 20 根均量 × 此倍数",
    )
    stop_atr: float = Field(
        default=2.0, description="止损距离 = ATR1H × 此倍数(从进场中点算)"
    )
    tp1_r: float = Field(default=1.0, description="止盈1 = 止损距离 × 此倍数")
    tp2_r: float = Field(default=2.0, description="止盈2 = 止损距离 × 此倍数")
    send: bool = Field(
        default=True, description="是否发送卡片到 Telegram(关闭时不记入去重)"
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
    """ "① a;② b" — numbered so a mobile reader can count what is left."""
    return ";".join(f"{chr(0x2460 + i)} {s}" for i, s in enumerate(steps))


# ── data ────────────────────────────────────────────────────────────────


async def _candles(
    client, cfg: Config, pair: str, interval: str, days: int, sem
) -> pd.DataFrame:
    async with sem:
        rows = await client.market_data.get_candles_last_days(
            cfg.connector_name, pair, days=days, interval=interval
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
        if not pair.endswith("-USDT") or _excluded(pair[:-5], cfg.exclude):
            continue
        qv = float((t or {}).get("quote_volume") or 0)
        if qv >= cfg.min_quote_volume:
            rows.append((pair, qv))
    rows.sort(key=lambda r: -r[1])
    return [p for p, _ in rows[: cfg.top_n]]


# ── signals (same rules for every pair) ─────────────────────────────────


def _plan(side: str, last: float, atr1h: float, cfg: Config) -> dict:
    """Entry zone around the current price; stop/targets as fixed multiples of ATR1H (as backtested)."""
    sgn = 1 if side == "long" else -1
    risk = cfg.stop_atr * atr1h
    return dict(
        side=side,
        lo=last - 0.1 * atr1h,
        hi=last + 0.1 * atr1h,
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
            f"1H EMA9>21>50 多头排列,回踩 EMA21({_fmt(c.ema21)})后 15m 重新站上 EMA9 收阳"
        )
    else:
        pl["why"] = (
            f"1H EMA9<21<50 空头排列,反弹至 EMA21({_fmt(c.ema21)})后 15m 重新跌破 EMA9 收阴"
        )
    return pl, prog


def _breakout(h4, h1c, last, atr1h, cfg):
    """Latest closed 4H bar closes beyond the prior 20-bar high/low, trend-aligned, on volume.

    Only called when cfg.enable_breakout is on. near is (distance in ATR4H, text) or None;
    a pair already beyond the level (no fresh break) gets distance None and is not ranked.
    """
    b, p = h4.iloc[-1], h4.iloc[-2]
    atr4 = float(b.atr)
    hh20, ll20 = float(h4.high.iloc[-21:-1].max()), float(h4.low.iloc[-21:-1].min())
    hh20_p, ll20_p = float(h4.high.iloc[-22:-2].max()), float(h4.low.iloc[-22:-2].min())
    up4, down4 = b.ema20 > b.ema50, b.ema20 < b.ema50
    near = None
    if up4:
        d = (hh20 - b.close) / atr4
        near = (
            (d, f"4H 多头 · 收盘距前 20 根高点 {_fmt(hh20)} 还差 {d:.1f}×ATR4H")
            if d > 0
            else (None, f"4H 多头 · 收盘已在前 20 根高点 {_fmt(hh20)} 之上(不是新突破)")
        )
    elif down4:
        d = (b.close - ll20) / atr4
        near = (
            (d, f"4H 空头 · 收盘距前 20 根低点 {_fmt(ll20)} 还差 {d:.1f}×ATR4H")
            if d > 0
            else (None, f"4H 空头 · 收盘已在前 20 根低点 {_fmt(ll20)} 之下(不是新突破)")
        )
    vx = float(b.volume) / float(h4.volume.iloc[-21:-1].mean())

    if (
        b.close > hh20
        and p.close <= hh20_p
        and up4
        and 40 <= b.rsi <= 70
        and h1c > hh20
    ):
        if vx < cfg.vol_mult:
            return (
                None,
                near,
                f"4H 收盘突破前 20 根高点 {_fmt(hh20)},但突破 K 线量能不足 {_cmp(vx, '<', cfg.vol_mult, '×')}",
            )
        pl = _plan("long", last, atr1h, cfg)
        pl["bar"] = float(b.timestamp)
        pl["why"] = (
            f"4H 收盘 {_fmt(b.close)} 放量({vx:.1f}× 均量)站上前 20 根高点 {_fmt(hh20)},"
            f"4H EMA20>EMA50,RSI {b.rsi:.0f}"
        )
        return pl, near, None

    if b.close < ll20 and p.close >= ll20_p and down4 and b.rsi > 30 and h1c < ll20:
        if vx < cfg.vol_mult:
            return (
                None,
                near,
                f"4H 收盘跌破前 20 根低点 {_fmt(ll20)},但突破 K 线量能不足 {_cmp(vx, '<', cfg.vol_mult, '×')}",
            )
        pl = _plan("short", last, atr1h, cfg)
        pl["bar"] = float(b.timestamp)
        pl["why"] = (
            f"4H 收盘 {_fmt(b.close)} 放量({vx:.1f}× 均量)跌破前 20 根低点 {_fmt(ll20)},"
            f"4H EMA20<EMA50,RSI {b.rsi:.0f}"
        )
        return pl, near, None
    return None, near, None


def _scan_pair(cfg: Config, pair: str, data: dict) -> dict:
    """Evaluate one pair: signals, then the clarity score. Plans in out['plans'] are tradable."""
    base = pair.split("-")[0]
    m15_raw, h1_raw, h4_raw = data["m15"], data["h1"], data["h4"]
    out = dict(
        pair=pair,
        base=base,
        plans=[],
        blocked=[],
        notes=[],
        hits={},
        skip=None,
        prog=None,
        clar=None,
        gap=None,
        near_b=None,
    )
    if len(m15_raw) < 100 or len(h1_raw) < 60 or len(h4_raw) < 60:
        out["skip"] = (
            f"K 线不足(15m {len(m15_raw)} / 1H {len(h1_raw)} / 4H {len(h4_raw)} 根)"
        )
        return out
    m15, h1, h4 = _closed(m15_raw, "15m"), _closed(h1_raw, "1h"), _closed(h4_raw, "4h")
    last = float(m15_raw.close.iloc[-1])
    atr15, atr1h = float(m15.atr.iloc[-1]), float(h1.atr.iloc[-1])
    h4_up = bool(h4.ema20.iloc[-1] > h4.ema50.iloc[-1])
    out.update(
        last=last, atr_pct=atr15 / last * 100, rsi1h=float(h1.rsi.iloc[-1]), h4_up=h4_up
    )

    found = []  # (type, plan, tf, candles for card)
    pl, prog = _pullback(m15, h1, last, cfg)
    if pl:
        found.append(("pullback", pl, "1H", h1_raw))
    if cfg.enable_breakout:
        pl, out["near_b"], note = _breakout(
            h4, float(h1.close.iloc[-1]), last, atr1h, cfg
        )
        if pl:
            found.append(("breakout", pl, "4H", h4_raw))
        if note:
            out["notes"].append(note)
    for t, _, _, _ in found:
        out["hits"][t] = out["hits"].get(t, 0) + 1

    for t, pl, tf, df in found:
        c = _clarity(h4, h1, pl["side"])
        pl.update(
            type=t,
            tf=tf,
            df=df,
            pair=pair,
            base=base,
            atr_pct=out["atr_pct"],
            clar=c,
            score=c["score"],
        )
        if c["score"] < cfg.min_score:
            out["blocked"].append(
                dict(
                    plan=pl,
                    fails=[
                        f"清晰度 {_cmp(c['score'], '<', cfg.min_score)}({_parts(c)})"
                    ],
                )
            )
        else:
            out["plans"].append(pl)
    if len({pl["side"] for pl in out["plans"]}) > 1:
        out["notes"].append("多空信号同时出现,走势不清晰,等待")
        out["plans"] = []
    if prog:
        out["clar"] = _clarity(h4, h1, prog["side"])
        out["prog"] = prog
        if not out["plans"]:
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
    """ "1H效率 0.31 · 1H强度 1.42 · 4H同向" — the score's inputs, for captions and the report."""
    return f"1H效率 {c['er1h']:.2f} · 1H强度 {c['trend1h']:.2f} · 4H{'同向' if c['h4_with'] else '逆向'}"


def _gap(cfg, prog, clar, blocked) -> dict:
    """What a pair with aligned 1H EMAs still lacks before it becomes a pullback card.

    steps lists every missing condition in rule order; pairs are ranked by clarity score
    (highest first) in the report, so rank only breaks ties by missing price steps.
    """
    side = prog["side"]
    hit = next((b for b in blocked if b["plan"]["type"] == "pullback"), None)
    if hit:
        steps = hit["fails"]
        return dict(
            steps=steps, need=f"15m 已触发,{steps[0]}", rank=(-clar["score"], 0)
        )
    steps = []
    verb = "回踩" if side == "long" else "反弹"
    if prog["dist"] > cfg.pullback_atr:
        d = _dec(prog["dist"], cfg.pullback_atr)
        steps.append(
            f"{verb}到 EMA21 附近(现距 {prog['dist']:.{d}f}×ATR1H,需 ≤ {cfg.pullback_atr:.{d}f}×ATR1H)"
        )
    elif not prog["touched"]:
        steps.append(f"近 3 根 1H 还没{verb}到 EMA21 附近(±0.25×ATR1H 内)")
    if prog["turned"]:
        steps.append(
            "回调与 4H 突破方向相反,多空冲突"
        )  # only reachable with breakout on
    else:
        steps.append(f"15m {'收阳站上' if side == 'long' else '收阴跌破'} EMA9")
    if clar["score"] < cfg.min_score:
        steps.append(f"清晰度 {_cmp(clar['score'], '<', cfg.min_score)}")
    return dict(
        steps=steps,
        need=f"还差 {len(steps)} 步:{_numbered(steps)}",
        rank=(-clar["score"], len(steps)),
    )


# ── dedupe state (coin + side + signal type) ────────────────────────────


def _line(pl) -> str:
    return (
        f"{pl['base']} {SIDE_NAME[pl['side']]} · {SIG_NAME[pl['type']]} · {pl['tf']} · "
        f"进场 {_fmt(pl['lo'])}–{_fmt(pl['hi'])} · 止损 {_fmt(pl['stop'])} · "
        f"止盈 {_fmt(pl['tp1'])} / {_fmt(pl['tp2'])}(盈亏比 1:{pl['rr1']:.1f} / {pl['rr2']:.1f})"
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
        score=plan["score"],
    )


async def run(config: Config, context: RoutineContext) -> str | RoutineResult:
    t0 = time.time()
    client = await get_client(
        context.chat_id,
        user_data=context.user_data,
        server=resolve_server_name(context),
    )
    if not client:
        return "无可用服务器"

    tk = await client.market_data.get_tickers(config.connector_name)
    tickers = ((tk or {}).get("tickers") or {}).get(config.connector_name) or {}
    if not tickers:
        return f"取不到 {config.connector_name} 行情列表,本次跳过"
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
    skipped = [f"{r['base']}:{r['skip']}" for r in results if r["skip"]]
    results = [r for r in results if not r["skip"]]

    candidates, muted, cards = [], [], []
    for result in results:
        if result["plans"]:
            best = result["plans"][0]
            best["also"] = [SIG_NAME[p["type"]] for p in result["plans"][1:]]
            candidates.append(best)
    candidates.sort(key=lambda plan: -plan["score"])

    for plan in candidates:
        trade_plan = _trade_plan(plan)
        plan["caption"] = _line(plan)
        if config.send:
            if not context.chat_id or not context.owner_id:
                raise ValueError("Publishing trade cards requires an owner and chat")
            published = await trade_cards.publish(
                trade_plan,
                plan["df"],
                owner_id=context.owner_id,
                chat_id=context.chat_id,
                server_name=resolve_server_name(context),
                account_name="master_account",
                connector_name=config.connector_name,
                bot=context.bot,
                signal_id=f"trend_watch:{plan['type']}:{plan['tf']}:{plan['bar']}",
            )
            if published is None:
                muted.append(plan)
                continue
        else:
            # A preview does not reserve a signal or mutate any trading state.
            png = await asyncio.to_thread(
                trade_cards.render_chart,
                plan["df"],
                trade_plan,
                config.connector_name,
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
    types = ["pullback"] + (["breakout"] if config.enable_breakout else [])
    hits = {t: sum(r["hits"].get(t, 0) for r in results) for t in types}
    passed = {t: sum(1 for p in cards if p["type"] == t) for t in types}
    # Closest to a card: highest clarity first (see _gap)
    near = sorted((r for r in results if r["gap"]), key=lambda r: r["gap"]["rank"])
    closest = [
        f"{r['base']} {_fmt(r['last'])} · 1H {'多头' if r['prog']['side'] == 'long' else '空头'} · "
        f"清晰度 {r['clar']['score']:.0f} · {r['gap']['need']}"
        for r in near[:3]
    ]
    near_b = sorted(
        (
            r
            for r in results
            if r["near_b"] and r["near_b"][0] is not None and not r["plans"]
        ),
        key=lambda r: r["near_b"][0],
    )
    closest_b = [
        f"{r['base']} {_fmt(r['last'])} · {r['near_b'][1]}" for r in near_b[:3]
    ]
    muted_why = "同一根触发 K 线已发过卡"

    # ── report ──
    builder = ReportBuilder(f"Trend Watch · {venue_label(config.connector_name)}")
    builder.source("routine", "trend_watch")
    builder.tags(["trend", "monitor", "scanner"])
    builder.kpi("扫描币数", str(len(results)))
    builder.kpi("卡片", str(len(cards)))
    for t in types:
        builder.kpi(f"{SIG_NAME[t]} 命中/通过", f"{hits[t]}/{passed[t]}")
    builder.kpi("用时", f"{elapsed:.1f}s")

    def _row(p, status):
        c = p["clar"]
        return {
            "币种": p["base"],
            "方向": SIDE_NAME[p["side"]],
            "类型": SIG_NAME[p["type"]],
            "周期": p["tf"],
            "进场": f"{_fmt(p['lo'])}–{_fmt(p['hi'])}",
            "止损": _fmt(p["stop"]),
            "止盈1": _fmt(p["tp1"]),
            "止盈2": _fmt(p["tp2"]),
            "盈亏比": f"1:{p['rr1']:.1f}/{p['rr2']:.1f}",
            "清晰度": f"{p['score']:.0f}",
            "1H效率": f"{c['er1h']:.2f}",
            "1H强度": f"{c['trend1h']:.2f}",
            "4H": "同向" if c["h4_with"] else "逆向",
            "状态": status,
        }

    cols = [
        "币种",
        "方向",
        "类型",
        "周期",
        "进场",
        "止损",
        "止盈1",
        "止盈2",
        "盈亏比",
        "清晰度",
        "1H效率",
        "1H强度",
        "4H",
        "状态",
    ]
    if cards:
        builder.table(
            [_row(p, "卡片" if config.send else "卡片(未发送)") for p in cards], cols
        )

    # Every candidate that triggered but did not become a card, with what stopped it
    blocked_rows = [
        _row(b["plan"], _numbered(b["fails"])) for r in results for b in r["blocked"]
    ]
    blocked_rows += [_row(p, muted_why) for p in muted]
    if blocked_rows:
        builder.markdown("**已触发但没出卡**(清晰度不够 / 同一根 K 线已发过)")
        builder.table(blocked_rows, cols)
    rule = (
        f"**出卡条件**(顺势回调):① 1H EMA9/21/50 顺向排列 → ② 现价距 1H EMA21 ≤ "
        f"{config.pullback_atr:.2f}×ATR1H,且近 3 根 1H 回踩/反弹到 EMA21 附近 → ③ 15m 收阳站上(收阴跌破)EMA9 → "
        f"④ 走势清晰度 ≥ {config.min_score:g}(1H 效率比、1H 趋势强度、4H 同向三项平均)。"
        f"不限张数,同一根触发 K 线只出一次"
    )
    if config.enable_breakout:
        rule += f";4H 突破已开启(突破 K 线量比 ≥ {config.vol_mult:.2f}×)"
    builder.markdown(rule)

    def _status(r):
        if r["plans"]:
            return "合格信号"
        if r["gap"]:
            return r["gap"]["need"]
        return "1H EMA 未排列,无回调信号"

    scan_rows = sorted(
        results,
        key=lambda r: (not r["plans"], -(r["clar"]["score"] if r["clar"] else -1)),
    )
    scan_cols = [
        "币种",
        "现价",
        "1H排列",
        "距EMA21(×ATR1H)",
        "清晰度",
        "1H效率",
        "1H强度",
        "4H趋势",
        "离出卡",
    ]
    rows = []
    for r in scan_rows:
        p, c = r["prog"], r["clar"]
        row = {
            "币种": r["base"],
            "现价": _fmt(r["last"]),
            "1H排列": ("多头" if p["side"] == "long" else "空头") if p else "—",
            "距EMA21(×ATR1H)": f"{p['dist']:.2f}" if p else "—",
            "清晰度": f"{c['score']:.0f}" if c else "—",
            "1H效率": f"{c['er1h']:.2f}" if c else "—",
            "1H强度": f"{c['trend1h']:.2f}" if c else "—",
            "4H趋势": "多头" if r["h4_up"] else "空头",
            "离出卡": _status(r),
        }
        if config.enable_breakout:
            row["4H突破"] = r["near_b"][1] if r["near_b"] else "4H 均线无方向"
        rows.append(row)
    builder.table(rows, scan_cols + (["4H突破"] if config.enable_breakout else []))
    md = []
    for p in cards:
        extra = f"(同时满足:{'、'.join(p['also'])})" if p.get("also") else ""
        md.append(
            f"**{p['base']} {SIDE_NAME[p['side']]} · {SIG_NAME[p['type']]} · 清晰度 {p['score']:.0f}**"
            f"{extra}:{p['why']};{_parts(p['clar'])}"
        )
    notes = [f"{r['base']}:{n}" for r in results for n in r["notes"]]
    if notes:
        md.append("**备注**  \n" + "  \n".join(notes))
    if failed or skipped:
        md.append("**未扫描**:" + ";".join([f"{b}:拉取失败" for b in failed] + skipped))
    builder.markdown("\n\n".join(md) or "无信号")
    builder.manual_order()
    await builder.save()

    head = f"趋势扫描 · {len(results)} 币 · 用时 {elapsed:.0f}s"
    tail = []
    if muted:
        tail.append(
            f"{muted_why}:"
            + "、".join(f"{p['base']} {SIDE_NAME[p['side']]}" for p in muted)
        )
    if not cards:
        lines = [head, "结论:无合格信号,继续等待"]
        if closest:
            lines += ["最接近出卡(按清晰度):"] + [f"• {c}" for c in closest]
        if closest_b:
            lines += ["最接近 4H 突破:"] + [f"• {c}" for c in closest_b]
        # Keep no-signal summaries in the report instead of sending a message.
        return RoutineResult(text="\n".join(lines + tail), silent=True)
    lines = [
        head,
        f"结论:出卡 {len(cards)} 张{'' if config.send else '(send=False,未发送)'}",
    ]
    lines += [f"卡片 {i + 1}:{p['caption']}" for i, p in enumerate(cards)]
    lines += [f"预览图片:{p['card']}" for p in cards if p.get("card")]
    # Published cards already carry their context; do not send another summary.
    return RoutineResult(text="\n".join(lines + tail), silent=True)
