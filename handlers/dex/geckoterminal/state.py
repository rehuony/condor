"""Per-user explorer state (network, view) and Gecko-to-Gateway mappings."""

import logging

from condor.pool_data import GECKO_TO_GATEWAY_NETWORK as _GECKO_TO_GATEWAY_NETWORK

logger = logging.getLogger(__name__)


DEFAULT_GECKO_NETWORK = "solana"


DEFAULT_GECKO_VIEW = "trending"  # trending, top, new


VIEW_LABELS = {
    "trending": "🔥 Trending",
    "top": "📈 Top Pools",
    "new": "🆕 New Pools",
}


VIEW_CYCLE = ["trending", "top", "new"]


def get_gecko_state(user_data: dict) -> tuple:
    """Get current network and view from user state"""
    network = user_data.get("gecko_selected_network", DEFAULT_GECKO_NETWORK)
    view = user_data.get("gecko_selected_view", DEFAULT_GECKO_VIEW)
    return network, view


def set_gecko_network(user_data: dict, network: str) -> None:
    """Set selected network"""
    user_data["gecko_selected_network"] = network


def set_gecko_view(user_data: dict, view: str) -> None:
    """Set selected view type"""
    user_data["gecko_selected_view"] = view


def cycle_gecko_view(user_data: dict) -> str:
    """Cycle to next view type and return the new view"""
    current_view = user_data.get("gecko_selected_view", DEFAULT_GECKO_VIEW)
    current_idx = VIEW_CYCLE.index(current_view) if current_view in VIEW_CYCLE else 0
    next_idx = (current_idx + 1) % len(VIEW_CYCLE)
    new_view = VIEW_CYCLE[next_idx]
    user_data["gecko_selected_view"] = new_view
    return new_view


# GeckoTerminal to Gateway network mapping. Defined in pool_data, next to its
# inverse (NETWORK_TO_GECKO), because the web pool browser routes its rows by it.
GECKO_TO_GATEWAY_NETWORK = _GECKO_TO_GATEWAY_NETWORK


# Default connectors by network chain
NETWORK_DEFAULT_CONNECTOR = {
    "solana": "jupiter",
    "eth": "uniswap",
    "base": "uniswap",
    "arbitrum": "uniswap",
    "bsc": "pancakeswap",
    "polygon_pos": "quickswap",
    "avalanche": "traderjoe",
    "optimism": "uniswap",
}
