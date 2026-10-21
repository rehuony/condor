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
