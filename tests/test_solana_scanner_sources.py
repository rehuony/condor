"""Scanners distinguish unavailable upstreams from genuine empty rankings."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agents.solana_dex_lp_expert.routines import lp_scanner
from condor import pool_data
from condor.reports import ReportBuilder
from routines import solana_pool_scanner

SOL = lp_scanner._QUOTE_MINTS["SOL"]
USDC = lp_scanner._QUOTE_MINTS["USDC"]
TOKEN = "token-mint"


def pool(
    address="pool-a",
    *,
    dex="orca",
    base=TOKEN,
    quote=SOL,
    tvl=40_000,
    volume=100_000,
    volume_1h=12_000,
):
    symbols = {SOL: "SOL", USDC: "USDC"}
    return {
        "attributes": {
            "address": address,
            "name": f"{symbols.get(base, 'MEME')} / {symbols.get(quote, 'MEME')}",
            "reserve_in_usd": str(tvl),
            "volume_usd": {"h24": str(volume), "h6": "0", "h1": str(volume_1h)},
            "base_token_price_usd": "1.5",
            "quote_token_price_usd": "150",
            "price_change_percentage": {"h24": "1"},
        },
        "relationships": {
            "dex": {"data": {"id": dex}},
            "base_token": {"data": {"id": f"solana_{base}"}},
            "quote_token": {"data": {"id": f"solana_{quote}"}},
        },
    }


@pytest.fixture
def runtime(monkeypatch):
    lookup = AsyncMock(return_value={"fee_pct": 0.3})
    client = SimpleNamespace(gateway_clmm=SimpleNamespace(get_pool_info=lookup))
    monkeypatch.setattr(lp_scanner, "get_client", AsyncMock(return_value=client))
    monkeypatch.setattr(solana_pool_scanner, "_build_chart", lambda *_: (None, None))
    reports = []

    async def save(builder):
        reports.append(builder._render_sections())
        return "test-report"

    monkeypatch.setattr(ReportBuilder, "save", save)
    return SimpleNamespace(
        context=SimpleNamespace(chat_id=1, user_data={}, bot=None),
        lookup=lookup,
        reports=reports,
    )


def source(monkeypatch, replies):
    """Substitute the shared REST boundary; no scanner can reach the network."""
    calls = []

    async def request(method, path, *, params=None):
        assert method == "GET"
        venue = "trending" if path.endswith("trending_pools") else path.split("/")[-2]
        page = int((params or {}).get("page", 1))
        calls.append((venue, page))
        reply = replies.get((venue, page), {"data": []})
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(pool_data, "gecko_request", request)
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("scanner", ["general", "lp"])
@pytest.mark.parametrize(
    "failure", [RuntimeError("429 rate limited"), TimeoutError(), {}]
)
async def test_all_unavailable_sources_raise_instead_of_claiming_no_pools(
    monkeypatch, runtime, scanner, failure
):
    calls = source(
        monkeypatch,
        {(dex, 1): failure for dex in ("trending", "orca", "meteora", "raydium-clmm")},
    )
    module = solana_pool_scanner if scanner == "general" else lp_scanner
    config = module.Config(pages=1) if scanner == "general" else module.Config()

    with pytest.raises(RuntimeError, match="GeckoTerminal unavailable") as raised:
        await module.run(config, runtime.context)

    assert "orca page 1" in str(raised.value)
    assert "passed the filters" not in str(raised.value)
    if isinstance(failure, TimeoutError):
        assert "TimeoutError: request failed" in str(raised.value)
    assert len(calls) == (3 if scanner == "general" else 4)
    assert not runtime.reports
    runtime.lookup.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("scanner", ["general", "lp"])
async def test_successful_empty_sources_remain_a_normal_empty_result(
    monkeypatch, runtime, scanner
):
    source(monkeypatch, {})
    module = solana_pool_scanner if scanner == "general" else lp_scanner
    config = module.Config(pages=1) if scanner == "general" else module.Config()

    result = await module.run(config, runtime.context)

    assert "No pools returned" in result
    assert "Warning:" not in result
    assert "0 source rows" in result
    runtime.lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_general_scanner_preserves_partial_pages_and_leads_with_warning(
    monkeypatch, runtime
):
    calls = source(
        monkeypatch,
        {
            ("orca", 1): {"data": [pool()]},
            ("orca", 2): TimeoutError(),
            ("raydium-clmm", 1): {"data": [pool("pool-ray", dex="raydium-clmm")]},
        },
    )

    result = await solana_pool_scanner.run(
        solana_pool_scanner.Config(pages=2), runtime.context
    )

    assert result.text.startswith("Warning: incomplete scan.")
    assert "5/6 GeckoTerminal pages succeeded" in result.text
    assert "orca page 2: TimeoutError: request failed" in result.text
    assert "2 unique pools passed all filters" in result.text
    assert set(calls) == {
        (dex, page) for dex in ("orca", "meteora", "raydium-clmm") for page in (1, 2)
    }
    html = runtime.reports[0]
    assert html.index("Warning: incomplete scan") < html.index("<table")
    assert "raydium.io/clmm/create-position/?pool_id=pool-ray" in html
    assert "www.orca.so/pools/pool-a" in html


@pytest.mark.asyncio
async def test_partial_scan_without_matches_does_not_hide_missing_coverage(
    monkeypatch, runtime
):
    source(
        monkeypatch,
        {
            ("orca", 1): {"data": [pool(tvl=10_000)]},
            ("meteora", 1): RuntimeError("upstream unavailable"),
        },
    )

    result = await solana_pool_scanner.run(
        solana_pool_scanner.Config(pages=1), runtime.context
    )

    assert result.startswith("Warning: incomplete scan.")
    assert "No pools match" in result
    assert "1 volume, 0 TVL" in result
    assert "meteora page 1" in result


@pytest.mark.asyncio
async def test_lp_partial_discovery_keeps_results_and_report_warning(
    monkeypatch, runtime
):
    calls = source(
        monkeypatch,
        {
            ("trending", 1): RuntimeError("429 rate limited"),
            ("orca", 1): {"data": [pool()]},
        },
    )

    result = await lp_scanner.run(lp_scanner.Config(), runtime.context)

    assert result.text.startswith("Warning: Incomplete scan.")
    assert "3/4 GeckoTerminal sources succeeded" in result.text
    assert result.table_data[0]["Pool"] == "pool-a"
    assert ("raydium-clmm", 1) in calls and ("raydium", 1) not in calls
    html = runtime.reports[0]
    assert html.index("Warning: Incomplete scan") < html.index('class="kpi-bar"')


@pytest.mark.asyncio
async def test_incomplete_trending_row_does_not_hide_valid_venue_row(
    monkeypatch, runtime
):
    incomplete = pool()
    incomplete["attributes"]["name"] = "Unknown"
    source(
        monkeypatch,
        {
            ("trending", 1): {"data": [incomplete]},
            ("orca", 1): {"data": [pool()]},
        },
    )

    result = await lp_scanner.run(lp_scanner.Config(), runtime.context)

    assert [row["Pool"] for row in result.table_data] == ["pool-a"]
    assert "2 source rows, 1 unique, 1 CLMM" in result.text
    runtime.lookup.assert_awaited_once()


@pytest.mark.asyncio
async def test_lp_filters_mints_in_both_orientations_and_maps_clmm_connector(
    monkeypatch, runtime
):
    reverse = pool("reverse", base=SOL, quote=TOKEN)
    source(
        monkeypatch,
        {
            ("trending", 1): {
                "data": [
                    reverse,
                    pool("ray-clmm", dex="raydium-clmm"),
                    pool("ray-amm", dex="raydium"),
                    pool("wrong-quote", quote=USDC),
                    pool("low-tvl", tvl=24_999),
                    pool("no-volume", volume_1h=0),
                    pool("held-pool"),
                    pool("held-mint", base="held-token"),
                    reverse,
                ]
            }
        },
    )

    result = await lp_scanner.run(
        lp_scanner.Config(
            ranking_window="1h",
            exclude_pools=["held-pool"],
            exclude_mints=["held-token"],
        ),
        runtime.context,
    )

    rows = {row["Pool"]: row for row in result.table_data}
    assert set(rows) == {"reverse", "ray-clmm"}
    assert rows["reverse"]["MintPair"] == f"{TOKEN}-SOL"
    assert rows["ray-clmm"]["lp_provider"] == "raydium/clmm"
    assert rows["ray-clmm"]["Venue"] == "raydium-clmm"
    assert rows["ray-clmm"]["FeeYield"] == "0.090%"
    assert {call.kwargs["connector"] for call in runtime.lookup.await_args_list} == {
        "orca",
        "raydium",
    }
    assert (
        "9 source rows, 8 unique, 7 CLMM, 6 quote, 5 TVL, 4 volume, 2 eligible"
        in result.text
    )


@pytest.mark.asyncio
async def test_lp_enrichment_fetch_failures_and_rejections_are_reported_separately(
    monkeypatch, runtime
):
    source(
        monkeypatch,
        {("orca", 1): {"data": [pool("available"), pool("timeout"), pool("zero-fee")]}},
    )

    async def lookup(**kwargs):
        address = kwargs["pool_address"]
        if address == "timeout":
            raise TimeoutError()
        return {"fee_pct": 0 if address == "zero-fee" else 0.3}

    runtime.lookup.side_effect = lookup
    result = await lp_scanner.run(lp_scanner.Config(), runtime.context)

    assert [row["Pool"] for row in result.table_data] == ["available"]
    assert result.text.startswith("Warning: Incomplete enrichment.")
    assert "2 responses, 1 fetch failures, 1 usable" in result.text
    assert "1 invalid fee" in result.text
    assert "timeout: TimeoutError: request failed" in result.text
    assert runtime.reports[0].index("Warning: Incomplete enrichment") < runtime.reports[
        0
    ].index("<table")


@pytest.mark.asyncio
async def test_lp_all_enrichment_failures_raise_but_valid_rejections_do_not(
    monkeypatch, runtime
):
    source(monkeypatch, {("orca", 1): {"data": [pool()]}})
    runtime.lookup.side_effect = TimeoutError()

    with pytest.raises(RuntimeError, match="CLMM pool-info unavailable"):
        await lp_scanner.run(lp_scanner.Config(), runtime.context)

    runtime.lookup.side_effect = None
    runtime.lookup.return_value = {"fee_pct": 0}
    result = await lp_scanner.run(lp_scanner.Config(), runtime.context)
    assert "none usable after successful pool-info checks" in result
    assert "0 fetch failures, 0 usable; rejected 1 invalid fee" in result
    assert "Warning:" not in result


@pytest.mark.asyncio
async def test_lp_successful_sources_can_genuinely_have_no_filter_matches(
    monkeypatch, runtime
):
    source(monkeypatch, {("orca", 1): {"data": [pool(tvl=24_999)]}})

    result = await lp_scanner.run(lp_scanner.Config(), runtime.context)

    assert "No SOL-quoted CLMM pools" in result
    assert "passed the filters" in result
    assert "1 quote, 0 TVL" in result
    assert "Warning:" not in result
    runtime.lookup.assert_not_awaited()
