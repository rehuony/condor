"""Display helpers for routine names, durations, schedules and config previews."""

import logging
import re
import time

logger = logging.getLogger(__name__)


def _display_name(name: str) -> str:
    """Convert snake_case to Title Case."""
    return name.replace("_", " ").title()


def _format_duration(seconds: float) -> str:
    """Format seconds as human-readable duration."""
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"


def _format_schedule(schedule: dict) -> str:
    """Format schedule as human-readable string."""
    stype = schedule.get("type", "once")
    if stype == "once":
        return "One-time"
    elif stype == "interval":
        secs = schedule.get("interval_sec", 60)
        if secs < 60:
            return f"Every {secs}s"
        elif secs < 3600:
            return f"Every {secs // 60}m"
        else:
            return f"Every {secs // 3600}h"
    elif stype == "continuous":
        return "Continuous"
    elif stype == "daily":
        return f"Daily @ {schedule.get('daily_time', '09:00')}"
    return "Unknown"


def _format_ago(timestamp: float) -> str:
    """Format timestamp as 'X ago' string."""
    diff = time.time() - timestamp
    if diff < 60:
        return f"{int(diff)}s ago"
    elif diff < 3600:
        return f"{int(diff // 60)}m ago"
    elif diff < 86400:
        return f"{int(diff // 3600)}h ago"
    return f"{int(diff // 86400)}d ago"


def _config_preview(config: dict, max_items: int = 2) -> str:
    """Get short config preview."""
    items = list(config.items())[:max_items]
    return ", ".join(f"{k}={v}" for k, v in items)


def _result_preview(result: str, limit: int = 48) -> str:
    """The first readable line of a result, short enough not to wrap."""
    for line in result.splitlines():
        line = " ".join(re.sub(r"[*_`~|#>]", "", line).split())
        if line:
            return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"
    return ""
