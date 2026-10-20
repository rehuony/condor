"""Market comparison keeps independent, missing and zero-valued data distinct."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aiohttp
import pandas as pd
import pytest

from condor.reports import ReportBuilder
from routines import mcap_comparison as mcap


def _session_response(payload, error=None):
    response = SimpleNamespace(
        raise_for_status=Mock(side_effect=error), json=AsyncMock(return_value=payload)
    )
    manager = SimpleNamespace()

    class ResponseContext:
        async def __aenter__(self):
            return response

        async def __aexit__(self, *_args):
            return None

    manager.get = Mock(return_value=ResponseContext())
    return manager, response


def test_token_info_never_substitutes_fdv_for_missing_market_cap(monkeypatch):
    monkeypatch.setattr(
        "condor.pool_data.gecko_request",
        AsyncMock(
            return_value={
                "data": {
                    "attributes": {
                        "price_usd": "0",
                        "fdv_usd": "9000000",
                        "market_cap_usd": None,
                    }
                }
            }
        ),
    )

    info = asyncio.run(mcap._fetch_token_info("mint"))

    assert info == {"price": 0.0, "fdv": 9_000_000.0, "mcap": None}


def test_fees_check_http_errors_before_accepting_json():
    error = aiohttp.ClientResponseError(
        request_info=SimpleNamespace(real_url="https://api.llama.fi/summary/fees/orca"),
        history=(),
        status=503,
        message="Service Unavailable",
    )
    session, response = _session_response({"error": "unavailable"}, error)

    with pytest.raises(aiohttp.ClientResponseError) as raised:
        asyncio.run(mcap._fetch_defillama_fees(session, "orca"))

    assert raised.value is error
    response.json.assert_not_awaited()


def test_missing_fee_totals_stay_absent_but_reported_zero_is_kept():
    session, _response = _session_response({"total24h": 0, "total7d": None})

    fees = asyncio.run(mcap._fetch_defillama_fees(session, "orca"))

    assert fees["total24h"] == 0
    assert fees["total7d"] is None
    assert fees["total30d"] is None
    assert fees["totalAllTime"] is None
    assert fees["chart"].empty


@pytest.mark.parametrize(
    "timestamps",
    [
        ["1704067200", "1704153600"],
        ["1704067200000", "1704153600000"],
        ["1704067200", "1704153600000"],
    ],
    ids=["seconds", "milliseconds", "mixed"],
)
def test_candle_dates_use_each_rows_timestamp_unit(monkeypatch, timestamps):
    client = SimpleNamespace(
        market_data=SimpleNamespace(
            get_candles_last_days=AsyncMock(
                return_value=[
                    {"timestamp": timestamp, "close": "2.5"} for timestamp in timestamps
                ]
            )
        )
    )
    get_client = AsyncMock(return_value=client)
    monkeypatch.setattr(mcap, "get_client", get_client)
    context = SimpleNamespace(_chat_id=7)

    candles = asyncio.run(mcap._fetch_candles(context, "ORCA-USDT", "1d", 90))

    assert candles["date"].tolist() == [
        pd.Timestamp("2024-01-01", tz="UTC"),
        pd.Timestamp("2024-01-02", tz="UTC"),
    ]
    assert candles["close"].tolist() == [2.5, 2.5]
    get_client.assert_awaited_once_with(7, context=context)


@pytest.fixture
def sources(monkeypatch):
    now = pd.Timestamp.now(tz="UTC")
    info = AsyncMock(
        return_value={"price": 2.0, "mcap": 100_000_000.0, "fdv": 200_000_000.0}
    )
    fees = AsyncMock(
        return_value={
            "total24h": 10.0,
            "total7d": 70.0,
            "total30d": 300.0,
            "totalAllTime": 1000.0,
            "chart": pd.DataFrame(
                {"timestamp": [now.timestamp()], "date": [now], "fees": [10.0]}
            ),
        }
    )
    candles = AsyncMock(
        return_value=pd.DataFrame(
            {
                "date": [now - pd.Timedelta(days=2), now - pd.Timedelta(days=1)],
                "close": [1.0, 2.0],
            }
        )
    )
    monkeypatch.setattr(mcap, "_fetch_token_info", info)
    monkeypatch.setattr(mcap, "_fetch_defillama_fees", fees)
    monkeypatch.setattr(mcap, "_fetch_candles", candles)
    return SimpleNamespace(info=info, fees=fees, candles=candles)


@pytest.fixture
def output(monkeypatch):
    recorded = SimpleNamespace(reports=[], figures=[])
    plotly = ReportBuilder.plotly

    def capture_figure(self, figure, **kwargs):
        recorded.figures.append(figure)
        return plotly(self, figure, **kwargs)

    async def save(self, *_args, **_kwargs):
        recorded.reports.append(self)
        return "report-1"

    monkeypatch.setattr(ReportBuilder, "plotly", capture_figure)
    monkeypatch.setattr(ReportBuilder, "save", save)
    return recorded


def _run():
    return asyncio.run(
        mcap.run(
            mcap.Config(tokens="ORCA", send_to_telegram=False, normalize=False),
            SimpleNamespace(_chat_id=0),
        )
    )


def _row(output):
    return next(
        section["rows"][0]
        for section in output.reports[0]._sections
        if section["type"] == "table"
    )


def test_gecko_failure_preserves_prices_and_fees_with_warning_first(sources, output):
    sources.info.side_effect = TimeoutError()

    result = _run()

    assert result.text.startswith(
        "*Data availability warnings*\n- GeckoTerminal (ORCA): TimeoutError: request timed out"
    )
    assert _row(output)["MCap"] == "N/A"
    assert _row(output)["FDV"] == "N/A"
    assert _row(output)["Fees 24h"] == "$10"
    figure = output.figures[0]
    assert figure.layout.annotations[0].text == "Price History"
    assert list(figure.data[0].y) == [1.0, 2.0]
    assert figure.data[1].name == "ORCA fees"
    html = output.reports[0]._render_sections()
    assert html.index("Data availability warnings") < html.index("plotly-graph-div")


def test_null_market_cap_keeps_fdv_separate_and_warns_about_missing_field(
    sources, output
):
    sources.info.return_value = {"price": 2.0, "mcap": None, "fdv": 9_000_000.0}

    result = _run()

    assert "GeckoTerminal (ORCA): Unavailable: market cap." in result.text
    assert "MCap N/A | FDV $9.0M" in result.text
    row = _row(output)
    assert row["MCap"] == "N/A"
    assert row["FDV"] == "$9.0M"
    assert row["MCap/Ann.Fees"] == "N/A"
    assert row["Estimated 90d Chg"] == "N/A"
    assert list(output.figures[0].data[0].y) == [1.0, 2.0]


def test_one_tokens_missing_valuation_preserves_other_token_and_all_price_series(
    sources, output
):
    sources.info.side_effect = [
        TimeoutError(),
        {"price": 4.0, "mcap": 200_000_000.0, "fdv": 300_000_000.0},
    ]
    orca_candles = sources.candles.return_value.copy()
    ray_candles = orca_candles.copy()
    ray_candles["close"] = [2.0, 4.0]
    sources.candles.side_effect = [orca_candles, ray_candles]

    result = asyncio.run(
        mcap.run(
            mcap.Config(tokens="ORCA,RAY", send_to_telegram=False, normalize=False),
            SimpleNamespace(_chat_id=0),
        )
    )

    assert "GeckoTerminal (ORCA): TimeoutError: request timed out" in result.text
    assert "GeckoTerminal (RAY):" not in result.text
    rows = next(
        section["rows"]
        for section in output.reports[0]._sections
        if section["type"] == "table"
    )
    assert rows[0]["Token"] == "ORCA"
    assert rows[0]["MCap"] == "N/A"
    assert rows[0]["Fees 24h"] == "$10"
    assert rows[1]["Token"] == "RAY"
    assert rows[1]["MCap"] == "$200.0M"
    assert rows[1]["Estimated 90d Chg"] == "+100.0%"
    assert rows[1]["Fees 24h"] == "$10"
    figure = output.figures[0]
    assert figure.layout.annotations[0].text == "Price History"
    assert [(trace.name, list(trace.y)) for trace in figure.data[:2]] == [
        ("ORCA", [1.0, 2.0]),
        ("RAY", [2.0, 4.0]),
    ]
    assert [trace.name for trace in figure.data[2:]] == ["ORCA fees", "RAY fees"]


@pytest.mark.parametrize("failed_source", ["fees", "candles"])
def test_independent_sources_survive_partial_failure(sources, output, failed_source):
    getattr(sources, failed_source).side_effect = RuntimeError("upstream unavailable")

    result = _run()

    source_name = "DefiLlama" if failed_source == "fees" else "Binance candles"
    assert f"{source_name} (ORCA): RuntimeError: upstream unavailable" in result.text
    assert _row(output)["MCap"] == "$100.0M"
    if failed_source == "fees":
        assert _row(output)["Fees 24h"] == "N/A"
        assert _row(output)["MCap/Ann.Fees"] == "N/A"
        assert (
            output.figures[0]
            .layout.annotations[0]
            .text.startswith("Estimated Market Cap")
        )
        assert list(output.figures[0].data[0].y) == [50_000_000.0, 100_000_000.0]
    else:
        assert _row(output)["Fees 24h"] == "$10"
        assert _row(output)["Estimated 90d Chg"] == "N/A"
        assert output.figures[0].data[0].name == "ORCA fees"
        assert output.figures[0].data[0].showlegend is True


def test_all_sources_failing_raises_with_source_causes(sources, output):
    sources.info.side_effect = TimeoutError()
    sources.fees.side_effect = RuntimeError("fees unavailable")
    sources.candles.side_effect = RuntimeError("candles unavailable")

    with pytest.raises(
        RuntimeError, match="All market comparison data sources failed"
    ) as raised:
        _run()

    assert isinstance(raised.value.__cause__, TimeoutError)
    assert "GeckoTerminal (ORCA)" in str(raised.value)
    assert "DefiLlama (ORCA)" in str(raised.value)
    assert "Binance candles (ORCA)" in str(raised.value)
    assert output.reports == []


def test_successful_empty_data_is_a_normal_no_data_report(sources, output):
    sources.info.return_value = {"price": None, "mcap": None, "fdv": None}
    sources.fees.return_value = {
        "total24h": None,
        "total7d": None,
        "total30d": None,
        "totalAllTime": None,
        "chart": pd.DataFrame(),
    }
    sources.candles.return_value = pd.DataFrame()

    result = _run()

    assert "No candle history returned" in result.text
    assert len(output.reports) == 1
    assert all(value == "N/A" for key, value in _row(output).items() if key != "Token")
    assert len(output.figures[0].data) == 0


def test_reported_zeros_are_not_missing_and_estimates_are_labeled(sources, output):
    sources.info.return_value = {"price": 0.0, "mcap": 0.0, "fdv": 0.0}
    sources.fees.return_value = {
        **sources.fees.return_value,
        "total24h": 0.0,
        "total7d": 0.0,
        "total30d": 0.0,
    }

    result = _run()

    assert "Data availability warnings" not in result.text
    assert "Historical market caps and changes are estimates" in result.text
    assert "constant current implied supply" in result.text
    row = _row(output)
    assert row["Price"] == "$0.0000"
    assert row["MCap"] == "$0"
    assert row["FDV"] == "$0"
    assert row["Fees 30d"] == "$0"
    assert row["Ann. Fees"] == "$0"
    assert row["MCap/Ann.Fees"] == "N/A"


def test_report_is_saved_before_short_photo_caption_with_full_summary_returned(
    sources, output, monkeypatch
):
    import plotly.graph_objects as go

    sources.info.side_effect = RuntimeError("temporarily unavailable " * 80)
    monkeypatch.setattr(
        go.Figure, "write_image", lambda self, buffer, **_kwargs: buffer.write(b"png")
    )

    async def send_photo(**kwargs):
        assert len(output.reports) == 1
        assert len(kwargs["caption"]) < 1024
        assert "Some data is unavailable" in kwargs["caption"]
        assert "parse_mode" not in kwargs

    bot = SimpleNamespace(send_photo=AsyncMock(side_effect=send_photo))
    result = asyncio.run(
        mcap.run(
            mcap.Config(tokens="ORCA", send_to_telegram=True),
            SimpleNamespace(_chat_id=7, bot=bot),
        )
    )

    bot.send_photo.assert_awaited_once()
    assert "temporarily unavailable " * 80 in result.text
