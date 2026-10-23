"""Gateway token symbols are fetched once per network, concurrently (WP-A6).

The DEX menu, the LP screen and the pool views used to each walk the
position networks in series and call Gateway per network. They share
``build_token_cache`` now, and bulk controller writes share a bounded
concurrent runner.
"""

import asyncio
from types import SimpleNamespace

from handlers.bots import controller_handlers
from handlers.dex import _shared
from handlers.dex import liquidity as liquidity_mod
from handlers.dex import menu as menu_mod
from utils.telegram_formatters import KNOWN_TOKENS


class _Gateway:
    def __init__(self, fail=()):
        self.calls = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.fail = set(fail)

    async def get_network_tokens(self, network):
        self.calls.append(network)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(0.01)
        self.in_flight -= 1
        if network in self.fail:
            raise RuntimeError("gateway down")
        return {"tokens": [{"address": f"{network}-addr", "symbol": network.upper()}]}


def _client(gateway):
    return SimpleNamespace(gateway=gateway)


def test_networks_are_fetched_concurrently_and_a_failure_is_skipped(monkeypatch):
    monkeypatch.setattr(_shared, "_token_symbols", {})
    gateway = _Gateway(fail={"ethereum-base"})
    client = _client(gateway)

    cache = asyncio.run(
        _shared.build_token_cache(
            client, ["solana-mainnet-beta", "ethereum-base", "solana-mainnet-beta"]
        )
    )

    assert gateway.calls.count("solana-mainnet-beta") == 1
    assert gateway.max_in_flight == 2
    assert cache["solana-mainnet-beta-addr"] == "SOLANA-MAINNET-BETA"
    assert "ethereum-base-addr" not in cache
    assert all(cache[k] == v for k, v in KNOWN_TOKENS.items())


def test_symbols_are_memoized_per_client_until_the_ttl_lapses(monkeypatch):
    monkeypatch.setattr(_shared, "_token_symbols", {})
    gateway = _Gateway()
    client = _client(gateway)
    other = _client(_Gateway())

    asyncio.run(_shared.build_token_cache(client, ["solana-mainnet-beta"]))
    asyncio.run(_shared.build_token_cache(client, ["solana-mainnet-beta"]))
    assert gateway.calls == ["solana-mainnet-beta"]

    asyncio.run(_shared.build_token_cache(other, ["solana-mainnet-beta"]))
    assert gateway.calls == ["solana-mainnet-beta"]  # another client, another cache

    clock = [1000.0]
    monkeypatch.setattr(_shared, "_token_clock", lambda: clock[0])
    monkeypatch.setattr(_shared, "_token_symbols", {})
    asyncio.run(_shared.build_token_cache(client, ["solana-mainnet-beta"]))
    clock[0] += _shared.TOKEN_SYMBOLS_TTL + 1
    asyncio.run(_shared.build_token_cache(client, ["solana-mainnet-beta"]))
    assert gateway.calls.count("solana-mainnet-beta") == 3


def test_a_failed_fetch_is_not_memoized(monkeypatch):
    monkeypatch.setattr(_shared, "_token_symbols", {})
    gateway = _Gateway(fail={"solana-mainnet-beta"})
    client = _client(gateway)

    asyncio.run(_shared.build_token_cache(client, ["solana-mainnet-beta"]))
    gateway.fail.clear()
    cache = asyncio.run(_shared.build_token_cache(client, ["solana-mainnet-beta"]))

    assert len(gateway.calls) == 2
    assert cache["solana-mainnet-beta-addr"] == "SOLANA-MAINNET-BETA"


def test_menu_and_lp_screen_resolve_symbols_through_the_shared_cache(monkeypatch):
    monkeypatch.setattr(_shared, "_token_symbols", {})
    gateway = _Gateway()
    positions = [
        {
            "status": "OPEN",
            "network": "solana-mainnet-beta",
            "base_token_amount": 1.0,
            "quote_token_amount": 0.0,
        },
        {
            "status": "OPEN",
            "network": "ethereum-base",
            "base_token_amount": 1.0,
            "quote_token_amount": 0.0,
        },
    ]

    class Clmm:
        async def search_positions(self, **kwargs):
            return {"data": positions}

    client = SimpleNamespace(gateway=gateway, gateway_clmm=Clmm())

    menu_data = asyncio.run(menu_mod._fetch_lp_positions(client))
    lp_data = asyncio.run(liquidity_mod._fetch_lp_positions(client))

    assert menu_data["token_cache"]["ethereum-base-addr"] == "ETHEREUM-BASE"
    assert lp_data["token_cache"]["solana-mainnet-beta-addr"] == "SOLANA-MAINNET-BETA"
    # Two networks, fetched once each across both screens.
    assert sorted(gateway.calls) == ["ethereum-base", "solana-mainnet-beta"]


def test_bulk_controller_writes_run_bounded_and_report_each_failure():
    in_flight = {"now": 0, "max": 0}

    async def operation(config_id):
        in_flight["now"] += 1
        in_flight["max"] = max(in_flight["max"], in_flight["now"])
        await asyncio.sleep(0.01)
        in_flight["now"] -= 1
        if config_id.endswith("7"):
            raise RuntimeError(f"boom {config_id}")

    ids = [f"cfg{i}" for i in range(10)]
    saved, failed = asyncio.run(
        controller_handlers._run_batched(ids, operation, verb="save")
    )

    assert in_flight["max"] == controller_handlers._BULK_CONCURRENCY
    assert saved == [i for i in ids if not i.endswith("7")]
    assert failed == [("cfg7", "boom cfg7")]
