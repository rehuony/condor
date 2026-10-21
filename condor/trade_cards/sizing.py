"""Input checks; exchange rounding and risk quotes belong to the execution API."""

from decimal import Decimal, InvalidOperation


def margin_amount(value: str | Decimal) -> str:
    try:
        text = str(value).strip()
        if len(text) > 64:
            raise ValueError()
        amount = Decimal(text)
    except (InvalidOperation, ValueError):
        raise ValueError("请输入有效的 USDT 保证金金额。") from None
    if not amount.is_finite() or amount <= 0:
        raise ValueError("保证金必须是大于 0 的有限数值。")
    if abs(amount.adjusted()) > 18:
        raise ValueError("保证金金额超出可支持的精度或范围。")
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
            raise ValueError("执行端未返回完整的风险报价，请稍后重试。") from None
        if (
            not value.is_finite()
            or (value < 0 and name not in {"profit_tp1", "profit_tp2"})
            or (
                name in {"quantity", "entry_price", "current_price", "margin"}
                and value == 0
            )
        ):
            raise ValueError("执行端风险报价无效，未提交订单。")
    if quote.get("entry_method") not in {"MARKET", "LIMIT"}:
        raise ValueError("执行端未返回有效的入场方式。")
    return quote
