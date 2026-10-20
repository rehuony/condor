"""Historical candle batches must honor the analysis horizon and timestamp units."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from condor.reports import ReportBuilder
from routines import technical_analysis as analysis
from routines.base import RoutineResult


def _candles(multiplier=1, *, missing_recent_minutes=0):
    start = pd.Timestamp("2026-10-16T00:00:00Z").timestamp()
    rows = []
    for index in range(1440):
        if 1080 <= index < 1080 + missing_recent_minutes:
            continue
        price = 100 + index / 100
        rows.append(
            {
                "timestamp": (start + index * 60) * multiplier,
                "open": price,
                "high": price + 1,
                "low": price - 1,
                "close": price + 0.5,
                "volume": 10,
            }
        )
    return list(reversed(rows))


@pytest.fixture
def isolated_run(monkeypatch):
    market_data = SimpleNamespace(
        get_candles_last_days=AsyncMock(), get_candles=AsyncMock()
    )
    monkeypatch.setattr(
        analysis,
        "get_client",
        AsyncMock(return_value=SimpleNamespace(market_data=market_data)),
    )
    save = AsyncMock(return_value="test-report")
    monkeypatch.setattr(ReportBuilder, "save", save)
    summary = Mock(wraps=analysis.format_analysis)
    monkeypatch.setattr(analysis, "format_analysis", summary)
    return market_data, save, summary


@pytest.mark.parametrize("multiplier", [1, 1000, "mixed"])
@pytest.mark.parametrize("historical_available", [True, False])
def test_six_hour_analysis_uses_only_recent_candles(
    isolated_run, multiplier, historical_available
):
    market_data, save, summary = isolated_run
    records = _candles(1 if multiplier == "mixed" else multiplier)
    if multiplier == "mixed":
        for index, record in enumerate(records):
            record["timestamp"] = str(record["timestamp"] * (1000 if index % 2 else 1))
    # API responses may wrap rows in either supported envelope.
    market_data.get_candles_last_days.return_value = (
        {"data": records} if historical_available else []
    )
    market_data.get_candles.return_value = {"candles": records}

    result = asyncio.run(
        analysis.run(
            analysis.Config(lookback="6h", send_chart=False), SimpleNamespace()
        )
    )

    assert isinstance(result, RoutineResult)
    df = summary.call_args.args[0]
    assert len(df) == 360
    assert df["datetime"].iloc[0] == pd.Timestamp("2026-10-16T18:00:00Z")
    assert df["datetime"].iloc[-1] == pd.Timestamp("2026-10-16T23:59:00Z")
    assert "6h @ 1m" in result.text
    assert "Price: 114.89" in result.text
    save.assert_awaited_once()
    if historical_available:
        market_data.get_candles.assert_not_awaited()
    else:
        assert market_data.get_candles.await_args.kwargs["max_records"] == 360


def test_sparse_recent_history_does_not_backfill_outside_the_requested_horizon(
    isolated_run,
):
    market_data, save, summary = isolated_run
    # A day of history is ample, but only 60 candles belong to its last 6 hours.
    market_data.get_candles_last_days.return_value = _candles(
        missing_recent_minutes=300
    )

    result = asyncio.run(
        analysis.run(
            analysis.Config(lookback="6h", send_chart=False), SimpleNamespace()
        )
    )

    assert result == "Not enough candles: got 60, need at least 65"
    summary.assert_not_called()
    save.assert_not_awaited()
