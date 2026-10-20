"""Scan & rank Solana CLMM memecoin pools by fee yield (fees/TVL) for LP entry.

Agent-local routine for solana_dex_lp_expert. Sources pools from GeckoTerminal
(trending + top-per-venue), filters to the configured quote asset + venues
(quote matched by mint on either pool side — GeckoTerminal orientation varies),
enriches the top candidates with on-chain CLMM pool-info (fee %, bin_step/tick,
mints), computes fee_yield = fees(window)/TVL, and returns a ranked shortlist
with the exact fields to open an lp_executor — including the MEMECOIN mint and a
mint-based trading pair (memecoins are not resolvable by symbol on Gateway, so
swaps/opens must use the mint). Base/quote orientation can differ between
GeckoTerminal and the connector (Orca reports SOL as base), so the memecoin mint
is resolved as the pool side that is NOT the quote asset.

Diversification: pass `exclude_pools` (held pool addresses) and/or `exclude_mints`
(held base mints) to keep the ranking from returning pools/tokens you already
hold — the agent should never open a 2nd slot on the same pool or token.
"""

import asyncio
import logging
import math
import re

from pydantic import BaseModel, Field, field_validator

from condor.pool_data import lp_provider_for_dex
from config_manager import get_client
from handlers.dex.geckoterminal import _extract_pool_data
from routines.base import RoutineContext

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"

GECKO_NETWORK = "solana"  # GeckoTerminal network id
CLMM_NETWORK = "solana-mainnet-beta"  # hummingbot-api CLMM network id
_GECKO_DEX_IDS = {"meteora": "meteora", "orca": "orca", "raydium": "raydium-clmm"}
_WINDOW_TO_FIELD = {"1h": "volume_1h", "6h": "volume_6h", "24h": "volume_24h"}
_QUOTE_MINTS = {
    "SOL": "So11111111111111111111111111111111111111112",
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
}


class Config(BaseModel):
    """Rank Solana CLMM memecoin pools by fee yield (fees/TVL) for LP entry."""

    quote_asset: str = Field(
        default="SOL", description="Quote token to require (SOL or USDC)"
    )
    venues: list[str] = Field(
        default=["meteora", "orca", "raydium"], description="CLMM venues to allow"
    )
    ranking_window: str = Field(
        default="24h", description="Volume window for fees/TVL: 1h, 6h, or 24h"
    )
    top_n: int = Field(default=10, ge=1, description="Number of ranked pools to return")
    min_tvl_usd: float = Field(
        default=25000.0, description="Minimum pool TVL (reserve) in USD"
    )
    exclude_pools: list[str] = Field(
        default=[], description="Pool addresses to exclude (already held)"
    )
    exclude_mints: list[str] = Field(
        default=[], description="Base token mints to exclude (already held)"
    )

    @field_validator("venues", "exclude_pools", "exclude_mints", mode="before")
    @classmethod
    def _coerce_list(cls, v):
        # Telegram config forms submit these as text ("" when left blank).
        if isinstance(v, str):
            return [s for s in re.split(r"[,\s]+", v.strip()) if s]
        return v


async def _gecko_get(path: str, params: dict | None = None) -> dict:
    """One GeckoTerminal call on the process-wide budget.

    Goes through ``pool_data`` rather than opening a session here: gecko's free
    tier is ~30 requests/minute per IP, shared with the dashboard and every other
    routine, and a scanner that fans out per venue is exactly what exhausts it.
    """
    from condor.pool_data import gecko_request

    return await gecko_request("GET", path, params=params)


def _num(v, default=0.0) -> float:
    try:
        value = float(v)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


async def run(config: Config, context: RoutineContext) -> str:
    client = await get_client(context.chat_id, user_data=context.user_data)
    if not client:
        return "No server available — cannot fetch CLMM pool-info."

    quote = config.quote_asset.strip().upper()
    quote_mint = _QUOTE_MINTS.get(quote)
    if not quote_mint:
        return f"lp_scanner: unsupported quote_asset '{quote}' — supported: {', '.join(_QUOTE_MINTS)}."
    venues = list(dict.fromkeys(v.strip().lower() for v in config.venues))
    unsupported = [v for v in venues if v not in _GECKO_DEX_IDS]
    if unsupported or not venues:
        raise ValueError(
            f"Choose CLMM venues from {', '.join(_GECKO_DEX_IDS)}; received {venues}."
        )
    vol_field = _WINDOW_TO_FIELD.get(config.ranking_window, "volume_24h")
    excl_pools = {p for p in config.exclude_pools if p}
    excl_mints = {m for m in config.exclude_mints if m}

    # 1. Source candidates from GeckoTerminal: global trending + top-per-venue.
    raw: list[dict] = []
    sources = [("trending page 1", f"networks/{GECKO_NETWORK}/trending_pools")]
    sources.extend(
        (
            f"{_GECKO_DEX_IDS[v]} page 1",
            f"networks/{GECKO_NETWORK}/dexes/{_GECKO_DEX_IDS[v]}/pools",
        )
        for v in venues
    )
    results = await asyncio.gather(
        *[_gecko_get(path, {"page": 1}) for _, path in sources], return_exceptions=True
    )
    source_errors = {}
    for (source, _), result in zip(sources, results):
        if not isinstance(result, Exception) and (
            not isinstance(result, dict) or not isinstance(result.get("data"), list)
        ):
            result = ValueError("GeckoTerminal returned an invalid pool list")
        if isinstance(result, Exception):
            source_errors[source] = (
                f"{type(result).__name__}: {str(result) or 'request failed'}"
            )
            logger.warning("lp_scanner: %s failed: %s", source, result)
        else:
            raw.extend(result["data"])
    succeeded_sources = len(sources) - len(source_errors)
    if not succeeded_sources:
        details = "; ".join(
            f"{source}: {error}" for source, error in source_errors.items()
        )
        raise RuntimeError(
            f"GeckoTerminal unavailable: all {len(sources)} LP sources failed. {details}"
        )
    coverage = (
        f"Coverage: {succeeded_sources}/{len(sources)} GeckoTerminal sources succeeded."
    )
    warnings = []
    if source_errors:
        warnings.append(
            f"Incomplete scan. {coverage} Failed sources: "
            + "; ".join(f"{source}: {error}" for source, error in source_errors.items())
        )

    def with_warnings(text: str) -> str:
        return "\n".join([*(f"Warning: {warning}" for warning in warnings), text])

    # 2. Parse via shared extractor, filter (quote, venue, TVL floor, excludes), dedupe by address.
    seen: set[str] = set()
    candidates: list[dict] = []
    stages = {
        stage: set()
        for stage in ("unique", "CLMM", "quote", "TVL", "volume", "eligible")
    }
    invalid_rows = 0
    for p in raw:
        try:
            d = _extract_pool_data(p)
        except (AttributeError, TypeError, ValueError):
            invalid_rows += 1
            continue
        addr = d.get("address") or ""
        if not addr:
            invalid_rows += 1
            continue
        if addr in seen:
            continue
        stages["unique"].add(addr)
        base_sym = d.get("base_token_symbol") or ""
        quote_sym = d.get("quote_token_symbol") or ""
        if not base_sym or not quote_sym or "?" in base_sym or "?" in quote_sym:
            invalid_rows += 1
            continue
        dex = d.get("dex_id") or ""
        provider = lp_provider_for_dex(dex, GECKO_NETWORK)
        connector = provider.split("/", 1)[0] if provider else ""
        if connector not in venues:
            continue
        stages["CLMM"].add(addr)
        # Match the quote side by MINT and accept either GeckoTerminal orientation:
        # a SOL-quoted scan must also find pools Gecko lists as SOL/USDC (SOL as base).
        gecko_base_mint = d.get("base_token_address") or ""
        gecko_quote_mint = d.get("quote_token_address") or ""
        price_usd = _num(d.get("base_token_price_usd"))
        if gecko_quote_mint == quote_mint:
            pass
        elif gecko_base_mint == quote_mint:
            base_sym, quote_sym = quote_sym, base_sym
            gecko_base_mint, gecko_quote_mint = gecko_quote_mint, gecko_base_mint
            price_usd = _num(d.get("quote_token_price_usd"))
        else:
            continue
        stages["quote"].add(addr)
        tvl = _num(d.get("reserve_usd"))
        if tvl < config.min_tvl_usd:
            continue
        stages["TVL"].add(addr)
        vol_window = _num(d.get(vol_field))
        if vol_window <= 0:
            continue
        stages["volume"].add(addr)
        if addr in excl_pools or gecko_base_mint in excl_mints:
            continue
        seen.add(addr)
        stages["eligible"].add(addr)
        candidates.append(
            {
                "pool_address": addr,
                "dex": dex,
                "connector": connector,
                "lp_provider": provider,
                "base_symbol": base_sym,
                "quote_symbol": quote_sym,
                "trading_pair": f"{base_sym}-{quote_sym}",
                "gecko_base_mint": gecko_base_mint,
                "gecko_quote_mint": gecko_quote_mint,
                "tvl_usd": tvl,
                "vol_window": vol_window,
                "price_usd": price_usd,
            }
        )

    stage_summary = (
        f"Stages: {len(raw)} source rows, "
        + ", ".join(f"{len(addresses)} {stage}" for stage, addresses in stages.items())
        + f" ({invalid_rows} invalid rows; {len(excl_pools)} pool and {len(excl_mints)} mint exclusions)."
    )
    if not candidates:
        outcome = (
            "No pools returned by the successful sources."
            if not raw
            else f"No {quote}-quoted CLMM pools on {venues} passed the filters "
            f"(TVL >= ${config.min_tvl_usd:,.0f}, window {config.ranking_window})."
        )
        return with_warnings(f"lp_scanner: {outcome}\n{coverage}\n{stage_summary}")

    # Pre-rank by turnover (vol/TVL) so we only enrich the most promising ones.
    candidates.sort(
        key=lambda c: c["vol_window"] / max(c["tvl_usd"], 1.0), reverse=True
    )
    shortlist = candidates[: max(config.top_n * 2, config.top_n)]

    # 3. Enrich top candidates with CLMM pool-info (fee %, bin/tick, price, mints).
    enrichment_errors = {}
    enrichment_rejections = {"invalid fee": 0, "excluded mint": 0, "missing mint": 0}

    async def _enrich(c: dict) -> dict | None:
        try:
            info = await client.gateway_clmm.get_pool_info(
                connector=c["connector"],
                network=CLMM_NETWORK,
                pool_address=c["pool_address"],
            )
            if not isinstance(info, dict):
                raise ValueError("CLMM pool-info returned an invalid response")
        except Exception as e:
            enrichment_errors[c["pool_address"]] = (
                f"{type(e).__name__}: {str(e) or 'request failed'}"
            )
            logger.info(
                f"lp_scanner: pool-info failed for {c['dex']} {c['pool_address']}: {e}"
            )
            return None
        fee_pct = _num(info.get("fee_pct") or info.get("base_fee_percentage"))
        if fee_pct <= 0:
            enrichment_rejections["invalid fee"] += 1
            return None
        # Resolve the MEMECOIN mint = the pool side that is NOT the quote asset.
        mints = [
            info.get("base_token_address"),
            info.get("quote_token_address"),
            c.get("gecko_base_mint"),
            c.get("gecko_quote_mint"),
        ]
        mints = [m for m in mints if m]
        memecoin_mint = next((m for m in mints if m != quote_mint), "")
        if not memecoin_mint:
            enrichment_rejections["missing mint"] += 1
            return None
        # Diversification: drop tokens already held.
        if memecoin_mint and memecoin_mint in excl_mints:
            enrichment_rejections["excluded mint"] += 1
            return None
        c["fee_pct"] = fee_pct
        c["bin_step_or_tick"] = info.get("bin_step") or info.get("tick_spacing")
        price = info.get("price")
        if price is not None:
            c["price"] = _num(price)
        c["base_mint"] = memecoin_mint
        c["mint_pair"] = (
            f"{memecoin_mint}-{quote}" if memecoin_mint else c["trading_pair"]
        )
        c["fee_yield"] = (c["vol_window"] * (fee_pct / 100.0)) / max(c["tvl_usd"], 1.0)
        return c

    enriched = [r for r in await asyncio.gather(*[_enrich(c) for c in shortlist]) if r]
    enrichment_summary = (
        f"Enrichment: {len(shortlist)} requested, {len(shortlist) - len(enrichment_errors)} responses, "
        f"{len(enrichment_errors)} fetch failures, {len(enriched)} usable; rejected "
        + ", ".join(
            f"{count} {reason}" for reason, count in enrichment_rejections.items()
        )
        + "."
    )
    if enrichment_errors:
        details = "; ".join(
            f"{address}: {error}" for address, error in enrichment_errors.items()
        )
        if len(enrichment_errors) == len(shortlist):
            raise RuntimeError(
                with_warnings(
                    f"CLMM pool-info unavailable: every candidate lookup failed. {details}\n{coverage}\n{stage_summary}\n{enrichment_summary}"
                )
            )
        warnings.append(
            f"Incomplete enrichment. {enrichment_summary} Failed pools: {details}"
        )
    if not enriched:
        return with_warnings(
            f"lp_scanner: found {len(candidates)} {quote} pools but none usable after "
            f"successful pool-info checks and exclusions.\n{coverage}\n{stage_summary}\n{enrichment_summary}"
        )

    # 4. Final rank by fee yield.
    enriched.sort(key=lambda c: c["fee_yield"], reverse=True)
    ranked = enriched[: config.top_n]

    rows = []
    for i, c in enumerate(ranked, 1):
        rows.append(
            {
                "#": i,
                "Pair": c["trading_pair"],
                "MintPair": c.get("mint_pair"),
                "BaseMint": c.get("base_mint"),
                "Venue": c["dex"],
                "lp_provider": c["lp_provider"],
                "Pool": c["pool_address"],
                "TVL": f"${c['tvl_usd']:,.0f}",
                f"Vol({config.ranking_window})": f"${c['vol_window']:,.0f}",
                "Fee%": f"{c['fee_pct']:.3f}",
                "FeeYield": f"{c['fee_yield'] * 100:.3f}%",
                "Bin/Tick": c.get("bin_step_or_tick"),
                "Price": f"{c.get('price', c['price_usd']):.6g}",
            }
        )

    columns = [
        "#",
        "Pair",
        "MintPair",
        "BaseMint",
        "Venue",
        "lp_provider",
        "Pool",
        "TVL",
        f"Vol({config.ranking_window})",
        "Fee%",
        "FeeYield",
        "Bin/Tick",
        "Price",
    ]

    from condor.reports import ReportBuilder

    builder = ReportBuilder(f"LP Scanner — {quote}-quoted CLMM yield ranking")
    builder.source("routine", "lp_scanner").tags(
        ["lp", "solana", "clmm", quote.lower()]
    )
    for warning in warnings:
        builder.markdown(f"Warning: {warning}")
    builder.kpi("Candidates", str(len(candidates)))
    builder.kpi("Ranked", str(len(ranked)))
    builder.kpi("Top FeeYield", rows[0]["FeeYield"] if rows else "-")
    builder.kpi("Window", config.ranking_window)
    builder.markdown(
        f"Fee yield = fees(**{config.ranking_window}**)/TVL (fees ≈ vol × fee%). "
        f"Venues: {', '.join(venues)}. Quote: **{quote}**. Min TVL: ${config.min_tvl_usd:,.0f}. "
        f"Excluded {len(excl_pools)} pools / {len(excl_mints)} held mints. "
        f"Use **MintPair** for the entry swap and lp_executor trading_pair."
    )
    builder.markdown(f"{coverage}\n\n{stage_summary}\n\n{enrichment_summary}")
    builder.table(rows, columns)
    builder.manual_order()
    await builder.save()

    summary = with_warnings(
        f"Ranked {len(ranked)} {quote} CLMM pools by fee yield "
        f"(from {len(candidates)} candidates). Top: {rows[0]['Pair']} @ "
        f"{rows[0]['Venue']} ({rows[0]['FeeYield']} yield). Use MintPair for swap + lp_executor.\n"
        f"{coverage}\n{stage_summary}\n{enrichment_summary}"
    )

    try:
        from routines.base import RoutineResult

        return RoutineResult(text=summary, table_data=rows, table_columns=columns)
    except Exception:
        lines = [summary, ""]
        for r in rows:
            lines.append(
                f"{r['#']}. {r['Pair']} @ {r['Venue']} | yield {r['FeeYield']} | "
                f"TVL {r['TVL']} | {r['lp_provider']} | pool {r['Pool']} | mintpair {r['MintPair']}"
            )
        return "\n".join(lines)
