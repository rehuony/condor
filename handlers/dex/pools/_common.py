"""Helpers shared by the pool handlers: network checks, token cache, pool URLs."""

import logging

from config_manager import get_client
from utils.telegram_formatters import (
    KNOWN_TOKENS,
    resolve_token_symbol,
)

from .._shared import (
    build_token_cache,
)

logger = logging.getLogger(__name__)


# hapi's CLMM pool LISTING (/gateway/clmm/pools) is Solana-only — it accepts a
# bare Solana network name and rejects every non-Solana connector — so pools
# discovered through it live on this network. Anything reached by address
# carries its own network instead.
SOLANA_CLMM_NETWORK = "solana-mainnet-beta"


def _require_network(record: dict, what: str) -> str:
    """Read the network a pool/position lives on, failing loudly if it is absent.

    Everything that reaches these screens is stamped with its own network — a
    missing one means a stale cached record, and guessing 'solana-mainnet-beta'
    is how EVM pools silently became unreachable.
    """
    network = record.get("network")
    if not network:
        raise ValueError(
            f"{what} has no network recorded — reopen it from the menu to refresh it"
        )
    return network


async def get_token_cache_from_gateway(
    network: str = "solana-mainnet-beta", chat_id: int = None, context=None
) -> dict:
    """
    Fetch tokens from Gateway and build address->symbol cache.

    Args:
        network: Network ID (default: solana-mainnet-beta)
        chat_id: Chat ID for per-chat server selection
        context: Handler context, so the chat's effective server is used

    Returns:
        Dict mapping token addresses to symbols
    """
    try:
        client = await get_client(
            chat_id, user_data=context.user_data if context is not None else None
        )
    except Exception as e:
        logger.debug(f"Failed to get Gateway client for token cache: {e}")
        return dict(KNOWN_TOKENS)
    return await build_token_cache(client, [network])


def format_pair_from_addresses(
    base_token: str, quote_token: str, token_cache: dict = None
) -> str:
    """Format a trading pair from token addresses using symbols."""
    base_symbol = resolve_token_symbol(base_token, token_cache)
    quote_symbol = resolve_token_symbol(quote_token, token_cache)
    return f"{base_symbol}-{quote_symbol}"


def get_dex_pool_url(connector: str, pool_address: str, network: str = "") -> str:
    """
    Generate the DEX web app URL for a pool.

    Args:
        connector: DEX connector name (meteora, raydium, orca, uniswap, pancakeswap)
        pool_address: Pool address
        network: Network for EVM connectors (ethereum, arbitrum, base, etc.)

    Returns:
        URL to the pool on the DEX web app, or empty string if unknown
    """
    connector_lower = connector.lower()

    # Solana DEXes
    if connector_lower == "meteora":
        return f"https://app.meteora.ag/dlmm/{pool_address}?referrer=hummingbot"
    elif connector_lower == "raydium":
        return f"https://raydium.io/clmm/pool/{pool_address}"
    elif connector_lower == "orca":
        return f"https://www.orca.so/pools/{pool_address}"

    # EVM DEXes
    elif connector_lower == "uniswap":
        # Uniswap V3 pool URLs vary by network
        network_lower = network.lower() if network else "ethereum"
        if "arbitrum" in network_lower:
            return f"https://app.uniswap.org/explore/pools/arbitrum/{pool_address}"
        elif "base" in network_lower:
            return f"https://app.uniswap.org/explore/pools/base/{pool_address}"
        elif "optimism" in network_lower:
            return f"https://app.uniswap.org/explore/pools/optimism/{pool_address}"
        elif "polygon" in network_lower:
            return f"https://app.uniswap.org/explore/pools/polygon/{pool_address}"
        else:
            return f"https://app.uniswap.org/explore/pools/ethereum/{pool_address}"
    elif connector_lower == "pancakeswap":
        # PancakeSwap V3 pool URLs
        network_lower = network.lower() if network else "bsc"
        if "ethereum" in network_lower:
            return f"https://pancakeswap.finance/liquidity/{pool_address}?chain=eth"
        elif "arbitrum" in network_lower:
            return f"https://pancakeswap.finance/liquidity/{pool_address}?chain=arb"
        elif "base" in network_lower:
            return f"https://pancakeswap.finance/liquidity/{pool_address}?chain=base"
        else:
            return f"https://pancakeswap.finance/liquidity/{pool_address}?chain=bsc"

    return ""


async def _fetch_pool_info(
    client, pool_address: str, connector: str, network: str
) -> dict:
    """Fetch detailed pool info including bins

    Args:
        client: Gateway client
        pool_address: Pool address to fetch
        connector: Connector name (meteora, raydium, uniswap, ...)
        network: Network ID in '{chain}-{network}' form the pool lives on

    Returns:
        Pool info dict with bins data
    """
    try:
        if hasattr(client, "gateway_clmm"):
            result = await client.gateway_clmm.get_pool_info(
                connector=connector,
                network=network,
                pool_address=pool_address,
            )
            return result or {}
    except Exception as e:
        error_str = str(e)
        # If get_pool_info fails with validation error (pool not in DLMM list),
        # try finding via get_pools search as fallback
        if "validation error" in error_str.lower() or "Field required" in error_str:
            logger.info(
                f"Pool {pool_address[:12]}... not found via get_pool_info, trying get_pools search"
            )
            try:
                search_result = await client.gateway_clmm.get_pools(
                    connector=connector, search_term=pool_address, limit=1
                )
                pools = search_result.get("pools", [])
                if pools:
                    pool_info = pools[0]
                    pool_info["address"] = pool_address
                    logger.info(
                        f"Found pool via get_pools: {pool_info.get('trading_pair', 'Unknown')}"
                    )
                    return pool_info
            except Exception as search_e:
                logger.warning(f"get_pools search also failed: {search_e}")
        else:
            logger.warning(f"Failed to fetch pool info: {e}")
    return {}


async def _fetch_token_balances(
    client, network: str, base_symbol: str, quote_symbol: str
) -> dict:
    """Fetch wallet balances for base and quote tokens

    Args:
        client: Gateway client
        network: Network name (e.g., 'solana-mainnet-beta')
        base_symbol: Base token symbol (e.g., 'MET', 'SOL') - NOT the address
        quote_symbol: Quote token symbol (e.g., 'USDC') - NOT the address

    Returns:
        Dict with 'base_balance', 'quote_balance', 'base_value', 'quote_value'
    """
    result = {
        "base_balance": 0.0,
        "quote_balance": 0.0,
        "base_value": 0.0,
        "quote_value": 0.0,
    }

    try:
        if not hasattr(client, "portfolio"):
            return result

        # Fetch portfolio state
        state = await client.portfolio.get_state()
        if not state:
            return result

        # Normalize token symbols for comparison
        base_upper = base_symbol.upper() if base_symbol else ""
        quote_upper = quote_symbol.upper() if quote_symbol else ""

        # Network name normalization for connector matching
        # e.g., 'solana-mainnet-beta' -> match 'solana', 'gateway_solana', etc.
        network_key = network.split("-")[0].lower() if network else ""

        for account_name, account_data in state.items():
            for connector_name, balances in account_data.items():
                connector_lower = connector_name.lower()
                # Check if this is a gateway connector matching our network
                is_match = (
                    network_key in connector_lower
                    or "gateway" in connector_lower
                    and network_key in connector_lower
                )

                if is_match and balances:
                    for bal in balances:
                        token = bal.get("token", "").upper()
                        units = float(bal.get("units", 0) or 0)
                        value = float(bal.get("value", 0) or 0)

                        if token == base_upper:
                            result["base_balance"] = units
                            result["base_value"] = value
                        elif token == quote_upper:
                            result["quote_balance"] = units
                            result["quote_value"] = value

    except Exception as e:
        logger.warning(f"Error fetching token balances: {e}")

    return result
