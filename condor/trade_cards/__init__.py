"""Trade cards: source-independent plans, Telegram publication and tracking.

Publish from the Condor main process. Only main.startup/teardown own the tracker;
ordinary imports (including scanner and handler reloads) have no side effects.
"""

from .models import Card, CardStatus, TradePlan
from .service import get_card, list_cards, publish
from .tracker import start_tracker, stop_tracker


def render_chart(candles, plan: TradePlan | dict) -> bytes:
    """Render a plan without publishing it or changing card state."""
    from .chart import render_chart as draw

    return draw(candles, TradePlan.model_validate(plan))


__all__ = [
    "TradePlan",
    "Card",
    "CardStatus",
    "publish",
    "render_chart",
    "get_card",
    "list_cards",
    "start_tracker",
    "stop_tracker",
]
