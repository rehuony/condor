"""Small position-group fixtures shared by trade-card behavior tests."""

from condor.trade_cards import TradePlan


def plan(**changes) -> TradePlan:
    return TradePlan.model_validate(
        {
            "trading_pair": "SOL-USDT",
            "side": "long",
            "entry_low": "99",
            "entry_high": "101",
            "stop_price": "95",
            "tp1_price": "105",
            "tp2_price": "110",
            **changes,
        }
    )


def quote(**changes) -> dict:
    return {
        "quantity": "1",
        "total_quantity": "1",
        "leg_quantities": ["0.5", "0.5"],
        "entry_price": "100",
        "current_price": "100",
        "entry_method": "MARKET",
        "margin": "10",
        "notional": "100",
        "max_loss": "5",
        "liquidation_price": "90",
        "profit_tp1": "2.5",
        "profit_tp2": "5",
        **changes,
    }


def group(card, status="OPEN", **changes) -> dict:
    return {
        **quote(),
        "id": card.id,
        "execution_id": card.id,
        "status": status,
        "filled_amount": "1",
        "remaining_amount": "1",
        "pnl": "0",
        "operation": None,
        "error": None,
        **changes,
    }
