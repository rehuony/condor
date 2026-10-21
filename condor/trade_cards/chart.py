"""Render the shared trade-plan image without publishing or trading."""

from __future__ import annotations

import io
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.patches import Rectangle

from .models import TradePlan

TZ8 = timezone(timedelta(hours=8))
_RENDER_LOCK = Lock()
_FONT_DIR = Path(__file__).resolve().parent / "fonts"
_SERIF_FONT = _FONT_DIR / "LibreBaskerville-Regular.ttf"
_SANS_FONT = _FONT_DIR / "SourceHanSansCN-Regular.otf"


def _fmt(v: float) -> str:
    """Text price: ≥5 significant digits (≥2 decimals); below 1, ≥5 decimals."""
    v = float(v)
    if v == 0:
        return "0.00"
    if abs(v) >= 1:
        return f"{v:.{max(2, 4 - math.floor(math.log10(abs(v))))}f}"
    return f"{v:.{max(5, 3 - math.floor(math.log10(abs(v))))}f}"


def _px(v: float) -> str:
    """Card price: 4 significant digits (whole number ≥1000)."""
    if v >= 1000:
        return f"{v:.0f}"
    if v >= 1:
        return f"{v:.{3 - math.floor(math.log10(v))}f}"
    return _fmt(v)


def _fp(path: Path, size: float) -> FontProperties:
    return FontProperties(fname=path, size=size * 0.72)


BG, INK, MUTE = "#0F1113", "#EDE8DF", "#8A8D90"
GRN, RED = "#6DB5A0", "#D98B7A"


def _draw_card(df: pd.DataFrame, plan: TradePlan) -> bytes:
    pair, tf = plan.trading_pair, plan.timeframe
    serif = lambda s: _fp(_SERIF_FONT, s)  # noqa: E731
    sans = lambda s: _fp(_SANS_FONT, s)  # noqa: E731
    df = df.tail(30).reset_index(drop=True)
    times = [datetime.fromtimestamp(t, TZ8) for t in df["timestamp"]]
    px_last = float(df["close"].iloc[-1])
    short = plan.side == "short"
    lo_p, hi_p, stop, tp1, tp2 = map(
        float,
        (
            plan.entry_low,
            plan.entry_high,
            plan.stop_price,
            plan.tp1_price,
            plan.tp2_price,
        ),
    )
    entry = (lo_p + hi_p) / 2
    risk = abs(entry - stop)
    rr1, rr2 = abs(tp1 - entry) / risk, abs(tp2 - entry) / risk
    above_c, below_c = (RED, GRN) if short else (GRN, RED)

    W, H = 1024, 1280
    fig = plt.figure(figsize=(W / 100, H / 100), dpi=100, facecolor=BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.axis("off")

    def T(x, y, s, f, c=INK, ha="left", va="baseline"):
        return ax.text(x, y, s, fontproperties=f, color=c, ha=ha, va=va)

    base, quote = pair.split("-")
    T(62, 104, "交 易 计 划", sans(26), MUTE)
    T(962, 104, "T R A D E   N O T E", sans(20), MUTE, ha="right")
    T(
        62,
        232,
        f"{base} / {quote}",
        serif(88 * min(1, 9 / (len(base) + len(quote) + 3))),
    )
    T(62, 292, f"永续合约 · {tf} · UTC+8", sans(28), MUTE)
    ax.add_patch(plt.Circle((738, 228), 11, color=RED if short else GRN, lw=0))
    T(962, 242, "做空  SHORT" if short else "做多  LONG", sans(32), INK, ha="right")

    CT, CB, CL, CR = 350, 790, 60, 620
    BX0, BX1 = 640, 890
    pad = 0.004 * px_last
    lo = min(df["low"].min(), stop, tp2) - pad
    hi = max(df["high"].max(), stop, tp2) + pad
    Y = lambda p: CT + (hi - p) / (hi - lo) * (CB - CT)  # noqa: E731
    pitch = (CR - CL) / len(df)
    X = lambda i: CL + pitch * (i + 0.5)  # noqa: E731
    for i, r in df.iterrows():
        c = GRN if r.close >= r.open else RED
        ax.plot(
            [X(i)] * 2, [Y(r.high), Y(r.low)], color=c, lw=1.4, solid_capstyle="butt"
        )
        top, bot = Y(max(r.open, r.close)), Y(min(r.open, r.close))
        ax.add_patch(
            Rectangle(
                (X(i) - pitch * 0.30, top),
                pitch * 0.60,
                max(bot - top, 1.5),
                color=c,
                lw=0,
            )
        )
    xl = X(len(df) - 1)
    ax.add_patch(plt.Circle((xl, Y(px_last)), 6, fc=BG, ec=INK, lw=1.6, zorder=5))

    top_p, bot_p = (stop, tp2) if short else (tp2, stop)
    ax.add_patch(
        Rectangle(
            (BX0, Y(top_p)),
            BX1 - BX0,
            Y(entry) - Y(top_p),
            color=above_c,
            alpha=0.11,
            lw=0,
        )
    )
    ax.add_patch(
        Rectangle(
            (BX0, Y(entry)),
            BX1 - BX0,
            Y(bot_p) - Y(entry),
            color=below_c,
            alpha=0.11,
            lw=0,
        )
    )
    ax.add_patch(
        Rectangle(
            (BX0, Y(hi_p)), BX1 - BX0, Y(lo_p) - Y(hi_p), color=INK, alpha=0.14, lw=0
        )
    )
    dash = (0, (4, 4))
    ax.plot([BX0, BX1], [Y(stop)] * 2, color=RED, lw=1.3, ls=dash)
    ax.plot([BX0, BX1], [Y(tp2)] * 2, color=GRN, lw=1.3, ls=dash)
    ax.plot([BX0, BX1], [Y(tp1)] * 2, color=GRN, lw=1.0, ls=dash, alpha=0.6)
    ax.plot([BX0, BX1], [Y(entry)] * 2, color=MUTE, lw=1.3, ls=dash)
    # Keep the four right-hand labels at least 26px apart (tight short-term plans).
    labels = sorted(
        [
            (Y(stop), "止损", RED),
            (Y(entry), "进场", INK),
            (Y(tp1), "止盈1", GRN),
            (Y(tp2), "止盈2", GRN),
        ]
    )
    ys = [y for y, _, _ in labels]
    for i in range(1, len(ys)):
        ys[i] = max(ys[i], ys[i - 1] + 26)
    shift = max(0.0, ys[-1] - (CB + 10))
    LX = BX1 + 14
    for y, (_, s, c) in zip(ys, labels):
        T(LX, y - shift, s, sans(22), c, va="center")

    ty = CB + 46
    for i in (i for i in (2, 9, 16) if i < len(df) - 3):
        T(X(i), ty, times[i].strftime("%m-%d %H:%M"), sans(17), MUTE, ha="center")
    tl = times[-1]
    ax.plot([xl, xl], [Y(px_last) + 14, ty - 26], color=MUTE, lw=0.8, alpha=0.5)
    T(xl, ty, tl.strftime("%m-%d %H:%M"), sans(19), INK, ha="center")
    T(xl, ty + 26, f"现价 {_px(px_last)}", sans(18), MUTE, ha="center")

    rng = f"{_px(lo_p)}–{_px(hi_p)}"
    cols = [
        (62, "进场区间", None, rng, 46),
        (336, "止损价", RED, _px(stop), 66),
        (560, "止盈价 1", GRN, _px(tp1), 66),
        (778, "止盈价 2", GRN, _px(tp2), 66),
    ]
    for index, (x, lab, dot, val, fs) in enumerate(cols):
        off = 0
        if dot:
            ax.add_patch(plt.Circle((x + 7, 944), 7, color=dot, lw=0))
            off = 24
        T(x + off, 954, lab, sans(24), MUTE)
        price_text = T(x, 1030, val, serif(fs))
        # Fit actual glyph widths; character counts vary across fonts and prices.
        right = cols[index + 1][0] if index + 1 < len(cols) else 962
        available = right - x - 16
        width = price_text.get_window_extent(fig.canvas.get_renderer()).width
        if width > available:
            price_text.set_fontsize(price_text.get_fontsize() * available / width)
        T(x, 1078, quote, sans(22), MUTE)

    now = times[-1]
    ax.plot([62, 962], [1136, 1136], color=MUTE, lw=0.8, alpha=0.5)
    T(62, 1200, "计划盈亏比", sans(26), MUTE)
    T(250, 1202, f"1 : {rr1:.1f} / {rr2:.1f}", serif(40))
    T(
        962,
        1200,
        f"数据截至 {now.strftime('%m-%d %H:%M')} · 币安永续",
        sans(22),
        MUTE,
        ha="right",
    )

    buf = io.BytesIO()
    fig.savefig(buf, dpi=100, facecolor=BG)
    plt.close(fig)
    return buf.getvalue()


def render_chart(candles, plan: TradePlan | dict) -> bytes:
    """Return a PNG using actual OHLC candles; this has no delivery side effects."""
    plan = TradePlan.model_validate(plan)
    df = pd.DataFrame(candles).copy()
    required = ["timestamp", "open", "high", "low", "close"]
    if df.empty or any(column not in df for column in required):
        raise ValueError("At least one OHLC candle with a timestamp is required")
    df = df[required].astype(float).sort_values("timestamp").tail(30)
    if not all(math.isfinite(value) for value in df.to_numpy().flat):
        raise ValueError("Candle values must be finite")
    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("Candle prices must be positive")
    if df.timestamp.iloc[-1] > 1e11:
        df["timestamp"] /= 1000
    # Matplotlib owns global font and figure state, including across worker threads.
    with _RENDER_LOCK:
        previous = set(plt.get_fignums())
        try:
            return _draw_card(df, plan)
        finally:
            for number in set(plt.get_fignums()) - previous:
                plt.close(number)
