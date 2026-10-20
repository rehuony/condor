"""Candles for markets Hummingbot's ``CandlesFactory`` does not serve.

Two kinds of connector land here:

* **Gateway DEX networks** (``solana-mainnet-beta``, ``base``, …) — Condor trades
  them through Gateway and there is no CEX candle feed at all.
* **On-chain CLOB/AMM venues Hummingbot *does* connect to but does not chart** —
  ``xrpl`` has orders, balances and an order book, yet is absent from
  ``/market-data/available-candle-connectors``, so its charts used to come back
  empty. GeckoTerminal indexes the XRPL AMM pools behind those pairs, so it
  answers here like any other DEX pool.

The difference between the two is only *how the pool is found*: a Solana pair
carries the mint as its base (``<mint>-SOL``), while an XRPL pair carries a ticker
(``SOLO-XRP``) that has to be searched for. Everything downstream — timeframe
snapping, window trimming, caching — is shared.

Both the REST candles route and the dashboard's WebSocket candle stream call
``fetch_dex_candles``; nothing else should build GeckoTerminal OHLCV requests.
"""

from __future__ import annotations

import logging
import re
import time

logger = logging.getLogger(__name__)

# A token or pool address: an EVM 0x-address or a base58 Solana pubkey. Guards the
# values that reach GeckoTerminal as URL path segments, and tells a real address
# apart from a plain ticker like "BTC".
ADDRESS_RE = re.compile(r"^(0x[0-9a-fA-F]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})$")


def uses_gecko_candles(connector: str) -> bool:
    """Whether this connector's candles come from GeckoTerminal, not the API."""
    from condor.pool_data import NETWORK_TO_GECKO

    return connector in NETWORK_TO_GECKO


def split_pair(trading_pair: str) -> tuple[str, str]:
    """``<base>-<quote>`` → (base, quote). LP/DEX pairs carry a raw mint as base."""
    dash = trading_pair.rfind("-")
    if dash <= 0:
        return trading_pair, ""
    return trading_pair[:dash], trading_pair[dash + 1 :]


async def _pool_ohlcv(
    pool_address: str,
    connector: str,
    timeframe: str,
    limit: int,
    before_timestamp: int | None,
    token: str = "base",
    use_cache: bool = True,
    *,
    strict: bool = False,
    requested_start: float | None = None,
) -> list:
    """Raw OHLCV rows, with opt-in error propagation for historical pages."""
    from condor.pool_data import fetch_ohlcv

    try:
        rows, err = await fetch_ohlcv(
            pool_address,
            connector,
            timeframe=timeframe,
            # Prices in the quote token, matching the scale of the entry and range
            # prices the executor overlays draw on the same chart.
            currency="token",
            # A live poll wants the newest candle, and pool_data's shared cache
            # holds a pool for five minutes — long enough to freeze a streaming
            # chart — so use_cache=False bypasses the cached copy.
            use_cache=use_cache,
            limit=limit,
            before_timestamp=before_timestamp,
            token=token,
            strict=strict,
            requested_start=requested_start,
        )
    except Exception as e:
        if strict:
            raise
        logger.warning(
            "GeckoTerminal OHLCV failed pool=%s connector=%s tf=%s: %s",
            pool_address,
            connector,
            timeframe,
            e,
        )
        return []
    return [] if err or not rows else rows


async def _resolve_pool(
    connector: str, trading_pair: str, *, strict: bool = False
) -> tuple[str, str]:
    """Find the pool a trading pair trades in. Returns ``(address, token_side)``.

    ``("", "base")`` when the pair names nothing GeckoTerminal can resolve — a
    ticker pair on a network whose bases are addresses, for instance, where a
    lookup would only spend a request to fail.
    """
    from condor.pool_data import (
        fetch_pair_top_pool,
        fetch_token_top_pool,
        uses_symbol_pairs,
    )

    base, quote = split_pair(trading_pair)

    if ADDRESS_RE.match(base):
        return await fetch_token_top_pool(base, connector, quote, strict=strict), "base"

    if uses_symbol_pairs(connector):
        address, inverted = await fetch_pair_top_pool(
            base, quote, connector, strict=strict
        )
        return address, ("quote" if inverted else "base")

    return "", "base"


async def fetch_dex_candles(
    connector: str,
    pool_address: str | None,
    trading_pair: str,
    interval: str,
    start_time: float | None,
    end_time: float | None,
    use_cache: bool = True,
    *,
    strict: bool = False,
) -> list[dict]:
    """Candles for a DEX/LP/XRPL pair from GeckoTerminal.

    Prefers the executor's own pool. If that yields nothing — a closed slot, an
    executor that never recorded one, or a connector like ``xrpl`` that never has
    one — falls back to resolving the pool from the trading pair itself: the base
    token's deepest pool *in the same quote token* for address-based pairs, or the
    deepest pool with matching symbols for ticker-based ones. A pool on another
    quote would draw a plausible chart on the wrong price scale, so no match means
    no candles.

    Note the volume column is USD (GeckoTerminal reports ``volume_usd``) while the
    CEX path reports base units.

    Returns candle dicts (``timestamp``/``open``/``high``/``low``/``close``/
    ``volume``), ascending.

    ``strict=True`` is for bounded historical pages: it preserves inclusive
    request boundaries, raises upstream failures, and never substitutes a
    different pool when an explicit pool has no trades in that window.
    """
    from condor.pool_data import candles_needed, normalize_timeframe, timeframe_seconds

    timeframe = normalize_timeframe(interval)
    tf_seconds = timeframe_seconds(timeframe)
    # The chart asks for a window; GeckoTerminal answers with a count walking back
    # from before_timestamp. This is how many candles that window is, so a
    # ten-minute position is not handed ~16h of 1m candles to squeeze into itself.
    # It sizes the *answer*, not the upstream request: pool_data holds each pool as
    # one growing series and buys a full page whenever it has to go upstream.
    count = candles_needed(start_time, end_time, timeframe)

    # Strict pages pin their exact inclusive end via Gecko's exclusive ceiling.
    # Other callers only pin windows that have already closed — which also
    # makes it immutable, and so servable from the stored series forever. A live
    # chart pinning its drifting end instead would re-ask upstream for a window it
    # already holds; "latest candles" is both correct and cacheable for it.
    before = (
        int(end_time) + 1
        if strict and end_time is not None
        else (
            int(end_time)
            if end_time is not None and end_time < time.time() - tf_seconds
            else None
        )
    )
    history_options = {
        "strict": strict,
        "requested_start": start_time if strict else None,
    }

    rows = []
    if pool_address:
        rows = await _pool_ohlcv(
            pool_address,
            connector,
            timeframe,
            count,
            before,
            use_cache=use_cache,
            **history_options,
        )

    # A historical hole in a specified pool is not a reason to stitch in a
    # different pool's candles. Legacy executor previews keep their fallback.
    if not rows and not (strict and pool_address):
        resolved, token = await _resolve_pool(connector, trading_pair, strict=strict)
        if resolved and resolved != pool_address:
            rows = await _pool_ohlcv(
                resolved,
                connector,
                timeframe,
                count,
                before,
                token=token,
                use_cache=use_cache,
                **history_options,
            )

    # Rows are [timestamp, open, high, low, close, volume_usd, (datetime)], ascending.
    candles: list[dict] = []
    # Legacy previews keep the candle containing their (possibly mid-bar) start;
    # paged history has exact bounds and must not add a row from the prior page.
    lower = start_time if strict or start_time is None else start_time - tf_seconds
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        try:
            ts = float(row[0])
            if lower is not None and ts < lower:
                continue
            if end_time is not None and ts > end_time:
                continue
            candles.append(
                {
                    "timestamp": ts,
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5]),
                }
            )
        except (TypeError, ValueError):
            continue
    return candles
