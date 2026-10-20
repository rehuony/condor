"""Compare market cap, fees, and revenue for Solana DEX tokens (ORCA, MET, RAY)."""

CATEGORY = "Market Data"

import asyncio
import logging
import math
from datetime import datetime, timezone

import aiohttp
import pandas as pd
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_client
from routines.base import RoutineResult

logger = logging.getLogger(__name__)

# Token configs: name -> (GeckoTerminal mint, Binance pair, DefiLlama slug)
DEFAULT_TOKENS = {
    "ORCA": {
        "mint": "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE",
        "trading_pair": "ORCA-USDT",
        "defillama": "orca",
    },
    "MET": {
        "mint": "METvsvVRapdj9cFLzq4Tr43xK4tAjQfwX76z3n6mWQL",
        "trading_pair": "MET-USDT",
        "defillama": "meteora",
    },
    "RAY": {
        "mint": "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",
        "trading_pair": "RAY-USDT",
        "defillama": "raydium",
    },
}

CONNECTOR = "binance"
DEFILLAMA_BASE = "https://api.llama.fi/summary/fees"


class Config(BaseModel):
    """Compare market cap and fee/revenue of DEX tokens over time. Default: ORCA, MET, RAY."""

    tokens: str = Field(
        default="ORCA,MET,RAY",
        description="Comma-separated token names (ORCA, MET, RAY)",
    )
    days: int = Field(default=90, description="Number of days of history")
    interval: str = Field(default="1d", description="Candle interval: 1d, 4h, 1h")
    normalize: bool = Field(
        default=True, description="Normalize to 100 at start for relative comparison"
    )
    send_to_telegram: bool = Field(default=True, description="Send chart to Telegram")


def _number(value: object) -> float | None:
    """Keep an absent or invalid metric distinct from a reported zero."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _fmt_mcap(v: float | None) -> str:
    if v is None:
        return "N/A"
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:.1f}M"
    if v >= 1e3:
        return f"${v / 1e3:.0f}K"
    return f"${v:,.0f}"


def _fmt_pct(v: float | None) -> str:
    return f"{v:+.1f}%" if v is not None else "N/A"


def _fmt_price(v: float | None) -> str:
    return f"${v:.4f}" if v is not None else "N/A"


def _mcap_change(current: float | None, candles: pd.DataFrame) -> float | None:
    if current is None or candles.empty or "mcap" not in candles:
        return None
    first = _number(candles["mcap"].iloc[0])
    return (current - first) / first * 100 if first is not None and first > 0 else None


async def _fetch_token_info(mint: str) -> dict:
    """Fetch current token info (price, fdv, mcap) from GeckoTerminal.

    On the process-wide gecko budget — the ~30/min free tier is per IP and shared
    with the dashboard, so this routine cannot pace itself in isolation.
    """
    from condor.pool_data import gecko_request

    data = await gecko_request("GET", f"networks/solana/tokens/{mint}")
    attrs = data.get("data", {}).get("attributes", {})
    return {
        "price": _number(attrs.get("price_usd")),
        "fdv": _number(attrs.get("fdv_usd")),
        "mcap": _number(attrs.get("market_cap_usd")),
    }


async def _fetch_defillama_fees(session: aiohttp.ClientSession, protocol: str) -> dict:
    """Fetch fee/revenue data from DefiLlama."""
    url = f"{DEFILLAMA_BASE}/{protocol}"
    async with session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()

    # Extract time series: [[timestamp, value], ...]
    chart = data.get("totalDataChart", [])
    df = pd.DataFrame(chart, columns=["timestamp", "fees"])
    if not df.empty:
        df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
        df["fees"] = pd.to_numeric(df["fees"], errors="coerce")
        df["date"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
        df = (
            df.dropna(subset=["date", "fees"])
            .sort_values("timestamp")
            .reset_index(drop=True)
        )

    return {
        "total24h": _number(data.get("total24h")),
        "total7d": _number(data.get("total7d")),
        "total30d": _number(data.get("total30d")),
        "totalAllTime": _number(data.get("totalAllTime")),
        "chart": df,
    }


async def _fetch_candles(
    context: ContextTypes.DEFAULT_TYPE, trading_pair: str, interval: str, days: int
) -> pd.DataFrame:
    """Fetch candles from Binance via the Hummingbot backend API."""
    client = await get_client(context._chat_id, context=context)
    result = await client.market_data.get_candles_last_days(
        CONNECTOR, trading_pair, days=days, interval=interval
    )
    if isinstance(result, list):
        records = result
    elif isinstance(result, dict):
        records = result.get("data", result.get("candles", []))
    else:
        records = []
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "timestamp" in df.columns:
        ts = pd.to_numeric(df["timestamp"], errors="coerce")
        seconds = ts.where(ts < 1e12, ts / 1000)
        df["date"] = pd.to_datetime(seconds, unit="s", utc=True, errors="coerce")
    elif "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], utc=True, errors="coerce")
    else:
        raise ValueError("Candle data is missing timestamps")
    df = df.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
    return df


async def run(
    config: Config, context: ContextTypes.DEFAULT_TYPE
) -> str | RoutineResult:
    # Parse requested tokens
    requested = [t.strip().upper() for t in config.tokens.split(",") if t.strip()]
    active_tokens = {
        name: info for name, info in DEFAULT_TOKENS.items() if name in requested
    }
    if not active_tokens:
        return f"No known tokens in '{config.tokens}'. Available: {', '.join(DEFAULT_TOKENS.keys())}"

    token_info = {}
    fee_data = {}
    price_data = {}
    warnings = []
    failures = []
    successful_sources = 0

    def warn(source: str, name: str, message: str) -> None:
        warning = f"{source} ({name}): {message}"
        warnings.append(warning)
        logger.warning(warning)

    def failed(source: str, name: str, error: Exception) -> None:
        failures.append(error)
        reason = str(error) or (
            "request timed out"
            if isinstance(error, TimeoutError)
            else "no details available"
        )
        warn(source, name, f"{type(error).__name__}: {reason}")

    # A failed source must not replace another source's valid data with zeros.
    async with aiohttp.ClientSession() as session:
        # Token info from GeckoTerminal. No hand-rolled sleep between calls: the
        # shared gate paces these against every other caller in the process,
        # which a local sleep cannot.
        for name, td in active_tokens.items():
            try:
                token_info[name] = await _fetch_token_info(td["mint"])
                successful_sources += 1
                missing = [
                    label
                    for key, label in (
                        ("price", "price"),
                        ("mcap", "market cap"),
                        ("fdv", "FDV"),
                    )
                    if token_info[name][key] is None
                ]
                if missing:
                    warn(
                        "GeckoTerminal",
                        name,
                        f"Unavailable: {', '.join(missing)}. FDV is reported separately from market cap.",
                    )
                logger.info(
                    f"{name}: price={_fmt_price(token_info[name]['price'])}, mcap={_fmt_mcap(token_info[name]['mcap'])}"
                )
            except Exception as e:
                failed("GeckoTerminal", name, e)

        # DefiLlama fees (parallel — no rate limit issues)
        fee_tasks = {
            name: _fetch_defillama_fees(session, td["defillama"])
            for name, td in active_tokens.items()
        }
        fee_results = await asyncio.gather(*fee_tasks.values(), return_exceptions=True)
        for name, result in zip(fee_tasks.keys(), fee_results):
            if isinstance(result, Exception):
                failed("DefiLlama", name, result)
            else:
                successful_sources += 1
                fee_data[name] = result
                missing = [
                    label
                    for key, label in (
                        ("total24h", "24h"),
                        ("total7d", "7d"),
                        ("total30d", "30d"),
                    )
                    if result[key] is None
                ]
                if missing:
                    warn(
                        "DefiLlama",
                        name,
                        f"Unavailable fee totals: {', '.join(missing)}.",
                    )
                if result["chart"].empty:
                    warn("DefiLlama", name, "No fee history returned.")

    # Step 2: Fetch price candles from Binance (sequential — same connector)
    for name, td in active_tokens.items():
        try:
            df = await _fetch_candles(
                context, td["trading_pair"], config.interval, config.days
            )
            successful_sources += 1
            if df.empty:
                warn("Binance candles", name, "No candle history returned.")
                continue

            info = token_info.get(name, {})
            price = info.get("price")
            mcap = info.get("mcap")
            if price is not None and price > 0 and mcap is not None:
                implied_supply = mcap / price
                df["mcap"] = df["close"] * implied_supply

            df["token"] = name
            price_data[name] = df
        except Exception as e:
            failed("Binance candles", name, e)

    if successful_sources == 0:
        raise RuntimeError(
            "All market comparison data sources failed: " + "; ".join(warnings)
        ) from failures[0]

    # Build summary
    lines = []
    if warnings:
        lines.extend(
            [
                "*Data availability warnings*",
                *[f"- {warning}" for warning in warnings],
                "",
            ]
        )
    lines.extend(["*Market Cap Comparison — Solana DEX Tokens*", ""])

    # MCap section
    for name in active_tokens:
        info = token_info.get(name, {})
        change = _mcap_change(info.get("mcap"), price_data.get(name, pd.DataFrame()))
        lines.append(
            f"- {name}: MCap {_fmt_mcap(info.get('mcap'))} | FDV {_fmt_mcap(info.get('fdv'))} | "
            f"Price {_fmt_price(info.get('price'))} | Estimated {config.days}d change {_fmt_pct(change)}"
        )
    lines.extend(
        [
            "",
            "Historical market caps and changes are estimates using candle prices and a constant current implied supply "
            "(reported market cap / current price). They do not track historical circulating supply.",
        ]
    )

    # Fee/Revenue section
    lines.append("")
    lines.append("*Protocol Fees (DefiLlama)*")
    for name in active_tokens:
        fd = fee_data.get(name, {})
        f24 = fd.get("total24h")
        f7d = fd.get("total7d")
        f30d = fd.get("total30d")
        lines.append(
            f"- {name}: 24h {_fmt_mcap(f24)} | 7d {_fmt_mcap(f7d)} | 30d {_fmt_mcap(f30d)}"
        )

    # MCap/Fees ratio (valuation)
    lines.append("")
    lines.append("*MCap / Annualized Fees (30d fees × 12)*")
    for name in active_tokens:
        mcap = token_info.get(name, {}).get("mcap")
        f30d = fee_data.get(name, {}).get("total30d")
        if mcap is not None and f30d is not None and f30d > 0:
            annualized = f30d * 12
            ratio = mcap / annualized
            lines.append(f"- {name}: {ratio:.1f}x (ann. fees {_fmt_mcap(annualized)})")
        else:
            lines.append(f"- {name}: N/A")

    summary = "\n".join(lines)

    # Build charts
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    colors = {"ORCA": "#00D1FF", "MET": "#E8A317", "RAY": "#8B5CF6"}

    # Without every token's valuation, plot the independent candle prices
    # instead of mixing prices and market caps on the same axis.
    estimated_mcap = bool(price_data) and all(
        "mcap" in df for df in price_data.values()
    )
    history_key = "mcap" if estimated_mcap else "close"
    history_title = (
        "Estimated Market Cap (constant current implied supply)"
        if estimated_mcap
        else "Price History"
    )
    normalize_history = (
        config.normalize
        and bool(price_data)
        and all(df[history_key].iloc[0] > 0 for df in price_data.values())
    )

    # Figure with 2 rows: available price/valuation history + independent fees.
    fig = make_subplots(
        rows=2,
        cols=1,
        row_heights=[0.55, 0.45],
        shared_xaxes=True,
        vertical_spacing=0.08,
        subplot_titles=[history_title, "Daily Protocol Fees"],
    )

    # Row 1: Market-cap estimates, or prices when valuations are unavailable.
    y_label = "Estimated Market Cap (USD)" if estimated_mcap else "Price (USD)"
    if normalize_history:
        y_label = "Indexed (100 = start)"
    for name in active_tokens:
        if name not in price_data:
            continue
        df = price_data[name]
        if df.empty:
            continue
        y_values = df[history_key]
        if normalize_history:
            y_values = (y_values / y_values.iloc[0]) * 100
        fig.add_trace(
            go.Scatter(
                x=df["date"],
                y=y_values,
                mode="lines",
                name=name,
                line=dict(color=colors.get(name, "#FFFFFF"), width=2),
                hovertemplate=f"{name}<br>%{{x|%b %d}}<br>%{{y:,.4g}}<extra></extra>",
                legendgroup=name,
            ),
            row=1,
            col=1,
        )

    # Row 2: Daily fees (7d rolling avg for smoothing)
    for name in active_tokens:
        fd = fee_data.get(name, {})
        chart_df = fd.get("chart", pd.DataFrame())
        if chart_df.empty:
            continue
        # Filter to requested days
        cutoff = datetime.now(timezone.utc).timestamp() - (config.days * 86400)
        chart_df = chart_df[chart_df["timestamp"] >= cutoff].copy()
        if chart_df.empty:
            continue
        chart_df["fees_7d_avg"] = chart_df["fees"].rolling(7, min_periods=1).mean()
        fig.add_trace(
            go.Scatter(
                x=chart_df["date"],
                y=chart_df["fees_7d_avg"],
                mode="lines",
                name=f"{name} fees",
                line=dict(color=colors.get(name, "#FFFFFF"), width=2, dash="dot"),
                hovertemplate=f"{name}<br>%{{x|%b %d}}<br>$%{{y:,.0f}}<extra></extra>",
                legendgroup=name,
                showlegend=name not in price_data,
            ),
            row=2,
            col=1,
        )

    fig.update_layout(
        title=f"Solana DEX Comparison — {config.days}d",
        paper_bgcolor="#0d1117",
        plot_bgcolor="#161b22",
        font=dict(color="#c9d1d9", size=11),
        legend=dict(orientation="h", yanchor="bottom", y=1.04, xanchor="right", x=1),
        margin=dict(l=60, r=30, t=110, b=40),
        hovermode="x unified",
        height=700,
    )
    for row in [1, 2]:
        fig.update_xaxes(gridcolor="#21262d", row=row, col=1)
        fig.update_yaxes(gridcolor="#21262d", row=row, col=1)
    fig.update_yaxes(title_text=y_label, tickformat=",.4g", row=1, col=1)
    fig.update_yaxes(
        title_text="Daily Fees (7d avg, USD)", tickformat="$,.0f", row=2, col=1
    )

    # Save HTML report
    from condor.reports import ReportBuilder

    builder = ReportBuilder(f"Solana DEX Comparison — {config.days}d")
    builder.source("routine", "mcap_comparison").tags(
        ["market-cap", "fees", "solana"] + [n.lower() for n in active_tokens]
    )
    # Preserve the warning-first summary above the charts and table.
    builder.manual_order().markdown(summary)
    builder.plotly(fig)

    table_rows = []
    for name in active_tokens:
        info = token_info.get(name, {})
        fd = fee_data.get(name, {})
        df = price_data.get(name, pd.DataFrame())
        change = _mcap_change(info.get("mcap"), df)
        f30d = fd.get("total30d")
        annualized = f30d * 12 if f30d is not None else None
        mcap = info.get("mcap")
        table_rows.append(
            {
                "Token": name,
                "Price": _fmt_price(info.get("price")),
                "MCap": _fmt_mcap(mcap),
                f"Estimated {config.days}d Chg": _fmt_pct(change),
                "FDV": _fmt_mcap(info.get("fdv")),
                "Fees 24h": _fmt_mcap(fd.get("total24h")),
                "Fees 30d": _fmt_mcap(f30d),
                "Ann. Fees": _fmt_mcap(annualized),
                "MCap/Ann.Fees": (
                    f"{mcap / annualized:.1f}x"
                    if mcap is not None and annualized is not None and annualized > 0
                    else "N/A"
                ),
            }
        )
    builder.table(table_rows)
    await builder.save()

    # The full summary is returned below. Keep the image caption short so
    # source warnings cannot exceed Telegram's caption limit or break Markdown.
    if config.send_to_telegram and getattr(context, "bot", None):
        import io

        chat_id = context._chat_id if hasattr(context, "_chat_id") else None
        if chat_id:
            buf = io.BytesIO()
            fig.write_image(buf, format="png", width=900, height=700, scale=2)
            buf.seek(0)
            caption = f"Solana DEX Comparison — {config.days}d"
            if warnings:
                caption += "\nSome data is unavailable. See the report and run summary for source warnings."
            await context.bot.send_photo(chat_id=chat_id, photo=buf, caption=caption)

    return RoutineResult(text=summary)
