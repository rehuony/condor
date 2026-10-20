"""Formatting and response-parsing helpers for GeckoTerminal data."""

import logging

from .._shared import format_compact_number

logger = logging.getLogger(__name__)


# Cache TTLs
TRENDING_CACHE_TTL = 120  # 2 minutes for trending data


POOL_CACHE_TTL = 60  # 1 minute for pool details


OHLCV_CACHE_TTL = 300  # 5 minutes for OHLCV data


# Network display names
NETWORK_NAMES = {
    "solana": "Solana",
    "eth": "Ethereum",
    "arbitrum": "Arbitrum",
    "base": "Base",
    "bsc": "BNB Chain",
    "polygon_pos": "Polygon",
    "avalanche": "Avalanche",
    "optimism": "Optimism",
    "sui-network": "Sui",
}


# Popular networks for quick access
POPULAR_NETWORKS = ["solana", "eth", "base", "arbitrum", "bsc"]


def _format_price(price: float) -> str:
    """Format price with appropriate precision"""
    if price is None:
        return "N/A"
    if price == 0:
        return "0"
    if abs(price) >= 1000:
        return f"${price:,.2f}"
    elif abs(price) >= 1:
        return f"${price:.4f}"
    elif abs(price) >= 0.0001:
        return f"${price:.6f}"
    else:
        return f"${price:.10f}"


def _format_volume(volume: float) -> str:
    """Format volume with K/M/B suffixes"""
    if volume is None:
        return "N/A"
    if volume >= 1_000_000_000:
        return f"${volume/1_000_000_000:.2f}B"
    elif volume >= 1_000_000:
        return f"${volume/1_000_000:.2f}M"
    elif volume >= 1_000:
        return f"${volume/1_000:.2f}K"
    else:
        return f"${volume:.2f}"


def _format_change(change: float) -> str:
    """Format price change with emoji"""
    if change is None:
        return "N/A"
    emoji = "🟢" if change >= 0 else "🔴"
    return f"{emoji} {change:+.2f}%"


def _get_nested_value(data: dict, *keys, default=None):
    """Get a value from dict trying multiple key patterns.

    Handles both nested dicts and flattened DataFrame columns.
    Example keys: ("volume_usd", "h24") will try:
    - data["volume_usd"]["h24"]
    - data["volume_usd.h24"]
    - data["volume_usd_h24"]
    """
    # Try nested access
    try:
        result = data
        for key in keys:
            if isinstance(result, dict):
                result = result.get(key)
            else:
                result = None
                break
        if result is not None:
            return result
    except (KeyError, TypeError):
        pass

    # Try dot-notation flattened key
    dot_key = ".".join(str(k) for k in keys)
    if dot_key in data:
        return data[dot_key]

    # Try underscore-notation flattened key
    underscore_key = "_".join(str(k) for k in keys)
    if underscore_key in data:
        return data[underscore_key]

    return default


def _parse_symbols_from_name(name: str) -> tuple:
    """Parse base and quote symbols from pool name like 'PENGU / SOL'"""
    if not name or "/" not in name:
        return "?", "?"
    parts = name.split("/")
    if len(parts) >= 2:
        return parts[0].strip(), parts[1].strip()
    return "?", "?"


def _get_pool_symbols(pool: dict) -> tuple:
    """Get base and quote symbols from pool data.

    Tries direct columns first, falls back to parsing from name.
    Returns (base_symbol, quote_symbol)
    """
    attrs = pool.get("attributes", pool) if isinstance(pool, dict) else {}

    base = _get_nested_value(attrs, "base_token_symbol")
    quote = _get_nested_value(attrs, "quote_token_symbol")

    # Fallback: parse from name
    if not base or not quote:
        name = _get_nested_value(attrs, "name") or ""
        parsed_base, parsed_quote = _parse_symbols_from_name(name)
        base = base or parsed_base
        quote = quote or parsed_quote

    return base, quote


def _format_pool_line(pool: dict, index: int = None) -> str:
    """Format a single pool line for list display"""
    # Handle both nested API response and flattened DataFrame
    attrs = pool.get("attributes", pool) if isinstance(pool, dict) else pool

    name = attrs.get("name", "Unknown") if isinstance(attrs, dict) else "Unknown"

    # Try multiple key patterns for symbols
    base_symbol = _get_nested_value(attrs, "base_token_symbol") or _get_nested_value(
        attrs, "base_token", "symbol"
    )
    quote_symbol = _get_nested_value(attrs, "quote_token_symbol") or _get_nested_value(
        attrs, "quote_token", "symbol"
    )

    # Fallback: parse from name (e.g., "PENGU / SOL")
    if not base_symbol or not quote_symbol:
        parsed_base, parsed_quote = _parse_symbols_from_name(name)
        base_symbol = base_symbol or parsed_base
        quote_symbol = quote_symbol or parsed_quote

    # Get price - try multiple patterns
    price = _get_nested_value(attrs, "base_token_price_usd")
    if price:
        try:
            price = float(price)
        except (ValueError, TypeError):
            price = None

    # Get volume - try nested and flattened patterns
    volume_24h = (
        _get_nested_value(attrs, "volume_usd", "h24")
        or _get_nested_value(attrs, "volume_usd_h24")
        or 0
    )
    if volume_24h:
        try:
            volume_24h = float(volume_24h)
        except (ValueError, TypeError):
            volume_24h = 0

    # Get price change - try nested and flattened patterns
    change_24h = _get_nested_value(
        attrs, "price_change_percentage", "h24"
    ) or _get_nested_value(attrs, "price_change_percentage_h24")
    if change_24h:
        try:
            change_24h = float(change_24h)
        except (ValueError, TypeError):
            change_24h = None

    # Build the line
    prefix = f"{index}. " if index is not None else "• "
    pair = f"{base_symbol}/{quote_symbol}"

    parts = [f"{prefix}{pair}"]
    if price:
        parts.append(_format_price(price))
    if change_24h is not None:
        parts.append(_format_change(change_24h))
    if volume_24h:
        parts.append(f"Vol: {_format_volume(volume_24h)}")

    return " | ".join(parts)


def _get_dex_short_name(dex_id: str) -> str:
    """Convert dex_id to short display name with pool type indicator"""
    if not dex_id:
        return "?"

    dex_id = dex_id.lower()

    # CLMM/Concentrated liquidity pools
    if "clmm" in dex_id or "dlmm" in dex_id or "whirlpool" in dex_id:
        if "raydium" in dex_id:
            return "Ray-CL"
        if "meteora" in dex_id:
            return "Met-CL"
        if "orca" in dex_id:
            return "Orc-CL"
        return "CLMM"

    # AMM pools
    if "raydium" in dex_id:
        return "Ray"
    if "meteora" in dex_id:
        return "Met"
    if "orca" in dex_id:
        return "Orca"
    if "pump" in dex_id:
        return "Pump"
    if "jupiter" in dex_id:
        return "Jup"
    if "moonshot" in dex_id:
        return "Moon"

    # Truncate unknown DEX names
    return dex_id[:6].title()


def _format_gecko_pool_table(pools: list) -> str:
    """Format pools as a compact table optimized for mobile

    Shows: #, Pair, DEX, Chg%, V/TVL, TVL
    Similar to CLMM pools format.
    """
    if not pools:
        return "_No pools found_"

    lines = []

    # Header - balanced for mobile (~44 chars)
    lines.append("```")
    lines.append(f"{'#':>2} {'Pair':<10} {'DEX':<6} {'Chg%':>5} {'V/T':>5} {'TVL':>6}")
    lines.append("─" * 44)

    for i, pool in enumerate(pools, 1):
        attrs = pool.get("attributes", pool) if isinstance(pool, dict) else pool

        # Get symbols
        base_symbol = _get_nested_value(attrs, "base_token_symbol") or "?"
        quote_symbol = _get_nested_value(attrs, "quote_token_symbol") or "?"
        if not base_symbol or base_symbol == "?":
            name = attrs.get("name", "") if isinstance(attrs, dict) else ""
            parsed_base, parsed_quote = _parse_symbols_from_name(name)
            base_symbol = parsed_base or base_symbol
            quote_symbol = parsed_quote or quote_symbol

        pair = f"{base_symbol[:6]}/{quote_symbol[:3]}"[:10]

        # DEX/Pool type
        dex_id = (
            _get_nested_value(attrs, "dex_id")
            or _get_nested_value(attrs, "dex", "id")
            or ""
        )
        dex_str = _get_dex_short_name(dex_id)

        # Price change 24h
        change = _get_nested_value(
            attrs, "price_change_percentage", "h24"
        ) or _get_nested_value(attrs, "price_change_percentage_h24")
        if change is not None:
            try:
                change_val = float(change)
                change_str = (
                    f"{change_val:+.0f}"
                    if abs(change_val) >= 10
                    else f"{change_val:+.1f}"
                )
            except (ValueError, TypeError):
                change_str = "—"
        else:
            change_str = "—"

        # Volume 24h and TVL for V/TVL ratio
        vol = (
            _get_nested_value(attrs, "volume_usd", "h24")
            or _get_nested_value(attrs, "volume_usd_h24")
            or 0
        )
        tvl = (
            _get_nested_value(attrs, "reserve_in_usd")
            or _get_nested_value(attrs, "reserve_usd")
            or 0
        )

        try:
            vol_val = float(vol)
            tvl_val = float(tvl)
        except (ValueError, TypeError):
            vol_val = 0
            tvl_val = 0

        # V/TVL ratio (volume/tvl) - good proxy for activity/potential returns
        if tvl_val > 0 and vol_val > 0:
            ratio = vol_val / tvl_val
            if ratio >= 10:
                ratio_str = f"{ratio:.0f}x"
            elif ratio >= 1:
                ratio_str = f"{ratio:.1f}x"
            else:
                ratio_str = f".{int(ratio*100):02d}x"
        else:
            ratio_str = "—"

        # TVL
        tvl_str = format_compact_number(tvl_val)

        lines.append(
            f"{i:>2} {pair:<10} {dex_str:<6} {change_str:>5} {ratio_str:>5} {tvl_str:>6}"
        )

    lines.append("```")

    return "\n".join(lines)


def _extract_pools_from_response(result, limit: int = 10) -> list:
    """Extract pools list from various response formats.

    Handles:
    - pandas DataFrame (library default)
    - Dict with 'data' key: {"data": [...]}
    - Direct list: [...]
    - Object with 'data' attribute
    """
    pools = []

    if result is None:
        logger.warning("GeckoTerminal returned None")
        return pools

    # Check for pandas DataFrame first (library returns DataFrames)
    try:
        import pandas as pd

        if isinstance(result, pd.DataFrame):
            # Convert DataFrame rows to list of dicts
            pools = result.to_dict("records")
            # Log ALL columns for debugging
            logger.info(
                f"GeckoTerminal DataFrame columns ({len(result.columns)}): {list(result.columns)}"
            )
            if pools and len(pools) > 0:
                # Log first pool's data to see available fields
                first_pool = pools[0]
                logger.info(f"GeckoTerminal first pool sample data:")
                for key, value in first_pool.items():
                    # Truncate long values
                    val_str = str(value)[:100] if value is not None else "None"
                    logger.info(f"  {key}: {val_str}")
            return pools[:limit] if pools else []
    except ImportError:
        pass

    # If it's already a list, use directly
    if isinstance(result, list):
        pools = result
    # If it's a dict with 'data' key
    elif isinstance(result, dict):
        pools = result.get("data", [])
    # If it has a 'data' attribute (object-style response)
    elif hasattr(result, "data"):
        data = result.data
        if isinstance(data, list):
            pools = data
        elif isinstance(data, dict):
            pools = data.get("data", [])
    else:
        logger.warning(f"Unknown GeckoTerminal response type: {type(result)}")
        # Try to_dict if available (DataFrame-like)
        if hasattr(result, "to_dict"):
            try:
                pools = result.to_dict("records")
            except Exception as e:
                logger.error(f"Failed to convert to dict: {e}")
        else:
            try:
                pools = list(result)
            except (TypeError, ValueError):
                logger.error(f"Cannot extract pools from response: {result}")

    # Log pool structure for dict/list responses
    if pools and len(pools) > 0:
        first_pool = pools[0]
        if isinstance(first_pool, dict):
            # Check if it has 'attributes' (API JSON response format)
            if "attributes" in first_pool:
                attrs = first_pool.get("attributes", {})
                rels = first_pool.get("relationships", {})
                logger.info(
                    f"GeckoTerminal pool attributes keys: {list(attrs.keys()) if isinstance(attrs, dict) else 'N/A'}"
                )
                logger.info(
                    f"GeckoTerminal pool relationships keys: {list(rels.keys()) if isinstance(rels, dict) else 'N/A'}"
                )
                logger.info(f"GeckoTerminal first pool sample:")
                for key, value in attrs.items():
                    val_str = str(value)[:80] if value is not None else "None"
                    logger.info(f"  attr.{key}: {val_str}")
                # Log DEX info specifically (might indicate pool type)
                dex_data = rels.get("dex", {}).get("data", {})
                logger.info(f"  DEX: {dex_data}")
            else:
                # Flat dict (DataFrame-converted)
                logger.info(f"GeckoTerminal pool keys: {list(first_pool.keys())}")

    logger.debug(f"Extracted {len(pools)} pools from response")
    return pools[:limit] if pools else []


def _parse_token_address_from_id(token_id: str) -> str:
    """Parse token address from GeckoTerminal token ID.

    Token IDs are in format: network_address (e.g., solana_So11111111111111...)
    """
    if not token_id:
        return ""
    # Split on first underscore to handle addresses that might contain underscores
    parts = token_id.split("_", 1)
    return parts[1] if len(parts) > 1 else token_id


def _extract_pool_data(pool: dict) -> dict:
    """Extract relevant data from pool response.

    Handles both nested API response and flattened DataFrame columns.
    """
    if not isinstance(pool, dict):
        logger.warning(f"Pool is not a dict: {type(pool)}")
        return {}

    attrs = pool.get("attributes", pool)
    relationships = pool.get("relationships", {})

    # Get name and parse symbols as fallback
    name = _get_nested_value(attrs, "name") or "Unknown"
    base_symbol = _get_nested_value(attrs, "base_token_symbol")
    quote_symbol = _get_nested_value(attrs, "quote_token_symbol")

    # Fallback: parse from name (e.g., "PENGU / SOL")
    if not base_symbol or not quote_symbol:
        parsed_base, parsed_quote = _parse_symbols_from_name(name)
        base_symbol = base_symbol or parsed_base
        quote_symbol = quote_symbol or parsed_quote

    # Extract token addresses from relationships
    # GeckoTerminal returns token IDs in format: network_address
    base_token_id = _get_nested_value(relationships, "base_token", "data", "id") or ""
    quote_token_id = _get_nested_value(relationships, "quote_token", "data", "id") or ""
    base_token_address = _parse_token_address_from_id(base_token_id)
    quote_token_address = _parse_token_address_from_id(quote_token_id)

    # Fallback: try direct keys for flattened DataFrames
    if not base_token_address:
        base_token_address = (
            _get_nested_value(attrs, "base_token_address")
            or _get_nested_value(attrs, "base_token", "address")
            or ""
        )
    if not quote_token_address:
        quote_token_address = (
            _get_nested_value(attrs, "quote_token_address")
            or _get_nested_value(attrs, "quote_token", "address")
            or ""
        )

    # For flattened DataFrames, try direct keys first
    return {
        "id": pool.get("id", ""),
        "name": name,
        "address": _get_nested_value(attrs, "address") or "",
        "base_token_symbol": base_symbol,
        "quote_token_symbol": quote_symbol,
        "base_token_address": base_token_address,
        "quote_token_address": quote_token_address,
        "base_token_price_usd": _get_nested_value(attrs, "base_token_price_usd"),
        "quote_token_price_usd": _get_nested_value(attrs, "quote_token_price_usd"),
        "base_token_price_native": _get_nested_value(
            attrs, "base_token_price_native_currency"
        ),
        "fdv_usd": _get_nested_value(attrs, "fdv_usd"),
        "market_cap_usd": _get_nested_value(attrs, "market_cap_usd"),
        "reserve_usd": _get_nested_value(attrs, "reserve_in_usd"),
        "volume_24h": _get_nested_value(attrs, "volume_usd", "h24")
        or _get_nested_value(attrs, "volume_usd_h24")
        or 0,
        "volume_6h": _get_nested_value(attrs, "volume_usd", "h6")
        or _get_nested_value(attrs, "volume_usd_h6")
        or 0,
        "volume_1h": _get_nested_value(attrs, "volume_usd", "h1")
        or _get_nested_value(attrs, "volume_usd_h1")
        or 0,
        "price_change_24h": _get_nested_value(attrs, "price_change_percentage", "h24")
        or _get_nested_value(attrs, "price_change_percentage_h24"),
        "price_change_6h": _get_nested_value(attrs, "price_change_percentage", "h6")
        or _get_nested_value(attrs, "price_change_percentage_h6"),
        "price_change_1h": _get_nested_value(attrs, "price_change_percentage", "h1")
        or _get_nested_value(attrs, "price_change_percentage_h1"),
        "transactions_24h": _get_nested_value(attrs, "transactions", "h24")
        or _get_nested_value(attrs, "transactions_h24")
        or {},
        "dex_id": (
            _get_nested_value(relationships, "dex", "data", "id")
            or _get_nested_value(pool, "dex_id")
            or _get_nested_value(pool, "dex")
            or ""
        ),
        "network": (
            _get_nested_value(relationships, "network", "data", "id")
            or _get_nested_value(pool, "network_id")
            or _get_nested_value(pool, "network")
            or ""
        ),
        "pool_created_at": _get_nested_value(attrs, "pool_created_at"),
    }
