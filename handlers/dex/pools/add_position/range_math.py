"""Range arithmetic for new positions: bins, percentages, ASCII range preview."""

import logging

logger = logging.getLogger(__name__)


def _calculate_max_range(
    current_price: float, bin_step: int, max_bins: int = 41
) -> tuple:
    """Calculate price range for liquidity position

    Args:
        current_price: Current pool price
        bin_step: Pool bin step in basis points (e.g., 100 = 1%)
        max_bins: Number of bins (default 41 = 20 bins each side + active bin)

    Returns:
        Tuple of (lower_price, upper_price)
    """
    if not current_price or not bin_step:
        return None, None

    try:
        # Bin step is in basis points (100 = 1% = 0.01)
        step_multiplier = 1 + (bin_step / 10000)

        # Calculate range: 20 bins below, 20 bins above (+ active bin = 41)
        half_bins = max_bins // 2

        lower_price = current_price / (step_multiplier**half_bins)
        upper_price = current_price * (step_multiplier**half_bins)

        return lower_price, upper_price
    except Exception:
        return None, None


def _bins_to_percent(bin_step: int, num_bins: int) -> float:
    """Convert number of bins to percentage change from current price.

    Args:
        bin_step: Pool bin step in basis points (e.g., 80 = 0.8%)
        num_bins: Number of bins from current price

    Returns:
        Percentage change (e.g., 17.3 for 17.3%)
    """
    if not bin_step or not num_bins:
        return 0.0
    step_multiplier = 1 + (bin_step / 10000)
    return (step_multiplier**num_bins - 1) * 100


def _percent_to_bins(bin_step: int, percent: float) -> int:
    """Convert percentage change to number of bins.

    Args:
        bin_step: Pool bin step in basis points
        percent: Percentage change (e.g., 17.3 for 17.3%)

    Returns:
        Number of bins (rounded)
    """
    if not bin_step or not percent:
        return 0
    import math

    step_multiplier = 1 + (bin_step / 10000)
    # percent% = (multiplier^n - 1) * 100
    # (percent/100 + 1) = multiplier^n
    # n = log(percent/100 + 1) / log(multiplier)
    try:
        n = math.log(abs(percent) / 100 + 1) / math.log(step_multiplier)
        return int(round(n))
    except (ValueError, ZeroDivisionError):
        return 0


def _price_to_percent(current_price: float, target_price: float) -> float:
    """Calculate percentage difference from current price to target price.

    Args:
        current_price: Current pool price
        target_price: Target price (lower or upper)

    Returns:
        Percentage difference (negative for lower, positive for upper)
    """
    if not current_price or not target_price:
        return 0.0
    return ((target_price / current_price) - 1) * 100


def _validate_bin_range(
    lower_price: float,
    upper_price: float,
    current_price: float,
    bin_step: int,
    max_bins: int = 68,
) -> tuple:
    """Validate that the price range doesn't exceed max bins.

    Args:
        lower_price: Lower price bound
        upper_price: Upper price bound
        current_price: Current pool price
        bin_step: Pool bin step in basis points
        max_bins: Maximum allowed bins (default 68 to be safe, max is 69)

    Returns:
        Tuple of (is_valid, total_bins, error_message)
    """
    if not all([lower_price, upper_price, current_price, bin_step]):
        return True, 0, None

    try:
        step_multiplier = 1 + (bin_step / 10000)
        import math

        # Calculate bins from current price to each bound
        lower_bins = abs(
            math.log(lower_price / current_price) / math.log(step_multiplier)
        )
        upper_bins = abs(
            math.log(upper_price / current_price) / math.log(step_multiplier)
        )
        total_bins = int(round(lower_bins + upper_bins)) + 1  # +1 for active bin

        if total_bins > max_bins:
            max_pct = _bins_to_percent(bin_step, max_bins // 2)
            return (
                False,
                total_bins,
                f"Range too wide: {total_bins} bins (max {max_bins}). Try L:{max_pct:.1f}% U:{max_pct:.1f}%",
            )

        return True, total_bins, None
    except Exception:
        return True, 0, None


def _get_default_range_percent(bin_step: int, num_bins: int = 20) -> tuple:
    """Get default lower and upper percentages based on bin_step and number of bins.

    Args:
        bin_step: Pool bin step in basis points
        num_bins: Number of bins each side (default 20)

    Returns:
        Tuple of (lower_pct, upper_pct) e.g., (-17.3, 17.3)
    """
    pct = _bins_to_percent(bin_step, num_bins)
    return (-pct, pct)


def _generate_range_ascii(
    bins: list,
    lower_price: float,
    upper_price: float,
    current_price: float,
    width: int = 20,
    max_bins_per_side: int = 40,
    bin_step: int = 2,
) -> str:
    """Generate improved ASCII visualization of liquidity with selected range markers

    Args:
        bins: List of bin data with price and liquidity
        lower_price: Selected lower bound
        upper_price: Selected upper bound
        current_price: Current pool price
        width: Width of the bar chart
        max_bins_per_side: Maximum bins to show on each side of current price (default 40)
        bin_step: Step between displayed bins (default 2, shows every other bin)

    Returns:
        ASCII chart string (no leading/trailing code blocks - handled by caller)
    """
    if not bins:
        return ""

    # Process bins
    bin_data = []
    for b in bins:
        base = float(b.get("base_token_amount", 0) or 0)
        quote = float(b.get("quote_token_amount", 0) or 0)
        price = float(b.get("price", 0) or 0)
        if price > 0:
            bin_data.append({"price": price, "liq": base + quote})

    if not bin_data:
        return ""

    # Sort bins by price
    bin_data.sort(key=lambda x: x["price"])

    # Find current price index for centering
    current_idx = 0
    if current_price:
        for i, b in enumerate(bin_data):
            if b["price"] >= current_price:
                current_idx = i
                break
        else:
            current_idx = len(bin_data) - 1

    # Calculate range to display: max_bins_per_side on each side of current price
    start_idx = max(0, current_idx - max_bins_per_side)
    end_idx = min(len(bin_data), current_idx + max_bins_per_side + 1)

    # Get subset of bins in display range
    display_bins = bin_data[start_idx:end_idx]

    if not display_bins:
        return ""

    # Find max liquidity for scaling (from all bins for consistent scale)
    max_liq = max((b["liq"] for b in bin_data), default=0)
    if max_liq <= 0:
        max_liq = 1  # Avoid division by zero

    # Build histogram with step sampling
    lines = []
    for i, b in enumerate(display_bins):
        # Only show every bin_step bins, but always show bins near current price and bounds
        price = b["price"]
        is_current = (
            current_price
            and abs(price - current_price) / max(current_price, 0.0001) < 0.03
        )
        near_lower = (
            lower_price and abs(price - lower_price) / max(lower_price, 0.0001) < 0.03
        )
        near_upper = (
            upper_price and abs(price - upper_price) / max(upper_price, 0.0001) < 0.03
        )

        # Skip bins not at step interval unless they're special (current, lower, upper bounds)
        if i % bin_step != 0 and not is_current and not near_lower and not near_upper:
            continue

        liq = b["liq"]

        # Calculate bar length
        bar_len = int((liq / max_liq) * width) if max_liq > 0 else 0

        # Determine if in range
        in_range = (
            lower_price <= price <= upper_price
            if lower_price and upper_price
            else False
        )

        # Build bar with different characters for in/out of range
        bar = "█" * bar_len if in_range else "░" * bar_len

        # Marker column - use intuitive symbols
        if is_current:
            marker = "◄"  # Current price marker
        elif near_lower:
            marker = "↓"  # Lower bound marker
        elif near_upper:
            marker = "↑"  # Upper bound marker
        else:
            marker = " "

        # Format price compactly
        if price >= 1:
            p_str = f"{price:.4f}"[:7]
        else:
            p_str = f"{price:.5f}"[:7]

        lines.append(f"{p_str} |{bar:<{width}}|{marker}")

    return "\n".join(lines)
