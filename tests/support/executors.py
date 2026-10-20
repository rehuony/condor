"""Executor samples shared by archived-chart and quote-conversion tests."""

from types import SimpleNamespace


def executor_sample(
    *,
    opened: float,
    closed: float = 0.0,
    side: str = "BUY",
    pnl: float = 0.0,
    fees: float = 0.0,
    volume: float = 0.0,
    entry_price: float = 0.0,
    connector: str = "binance",
    pair: str = "BTC-USDT",
    config: dict | None = None,
    custom_info: dict | None = None,
):
    return SimpleNamespace(
        timestamp=opened,
        close_timestamp=closed,
        side=side,
        pnl=pnl,
        cum_fees_quote=fees,
        volume=volume,
        entry_price=entry_price,
        connector=connector,
        trading_pair=pair,
        config=config or {},
        custom_info=custom_info or {},
    )
