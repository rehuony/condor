"""Input checks; exchange rounding and risk quotes belong to the execution API."""

from decimal import Decimal, InvalidOperation


def margin_amount(value: str | Decimal) -> str:
    try:
        text = str(value).strip()
        if len(text) > 64:
            raise ValueError()
        amount = Decimal(text)
    except (InvalidOperation, ValueError):
        raise ValueError("Enter a valid USDT margin amount.") from None
    if not amount.is_finite() or amount <= 0:
        raise ValueError("Margin must be a finite number above 0.")
    if abs(amount.adjusted()) > 18:
        raise ValueError(
            "The margin amount is outside the supported precision or range."
        )
    return format(amount.normalize(), "f")


def leverage_value(value: str | int) -> int:
    text = str(value).strip().lower().removeprefix("x").removesuffix("x").strip()
    # The pair's own range is checked against the executor's limits.
    if not text.isdigit() or len(text) > 4 or int(text) < 1:
        raise ValueError("Enter a whole-number leverage, such as 20.")
    return int(text)


def check_leverage(leverage: int, limits: dict) -> None:
    top = limits["max_leverage"]
    if top < 1:
        raise ValueError(
            "No leverage keeps the liquidation price beyond this plan's stop."
        )
    if not 1 <= leverage <= top:
        raise ValueError(f"Choose a leverage from 1x to {top}x for this pair.")


def check_margin(margin: str, limits: dict) -> None:
    low, high = Decimal(limits["min_margin"]), Decimal(limits["max_margin"])
    if low > high:
        raise ValueError(
            f"The minimum margin {low} USDT is above what the account allows "
            f"({high} USDT). Lower the leverage or add funds."
        )
    if not low <= Decimal(margin) <= high:
        raise ValueError(f"Enter a margin from {low} to {high} USDT.")


def validate_limits(limits: dict) -> dict:
    try:
        for name in ("max_leverage", "exchange_max_leverage", "leverage"):
            if isinstance(limits[name], bool) or not isinstance(limits[name], int):
                raise TypeError
        for name in ("min_margin", "max_margin", "available_balance", "current_price"):
            value = Decimal(str(limits[name]))
            if not value.is_finite() or value < 0:
                raise ValueError
    except (KeyError, TypeError, ValueError, InvalidOperation):
        raise ValueError(
            "The executor did not return valid leverage and margin limits. Try again shortly."
        ) from None
    return limits


def validate_quote(quote: dict) -> dict:
    for name in (
        "quantity",
        "entry_price",
        "current_price",
        "margin",
        "notional",
        "max_loss",
        "liquidation_price",
        "profit_tp1",
        "profit_tp2",
    ):
        try:
            value = Decimal(str(quote[name]))
        except (KeyError, InvalidOperation, TypeError):
            raise ValueError(
                "The executor did not return a complete risk quote. Try again shortly."
            ) from None
        if (
            not value.is_finite()
            or (value < 0 and name not in {"profit_tp1", "profit_tp2"})
            or (
                name in {"quantity", "entry_price", "current_price", "margin"}
                and value == 0
            )
        ):
            raise ValueError(
                "The executor's risk quote is invalid; no order was submitted."
            )
    if quote.get("entry_method") not in {"MARKET", "LIMIT"}:
        raise ValueError("The executor did not return a valid entry method.")
    return quote
