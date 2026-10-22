"""Fixed-target authorization and the atomic position-group HTTP contract."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest

from condor.trade_cards import execution
from condor.trade_cards.models import Card
from tests.support.trade_cards import plan, quote


@pytest.fixture
def card():
    return Card(
        id="abc",
        plan=plan(),
        owner_id=7,
        chat_id=7,
        server_name="fixed",
        account_name="account",
        connector_name="binance_perpetual",
        expires_at=100,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("removed", [False, True])
async def test_revoked_or_deleted_fixed_target_never_falls_back(
    card, monkeypatch, removed
):
    import config_manager

    manager = SimpleNamespace(
        get_server=Mock(
            side_effect=lambda name: None if removed and name == "fixed" else {}
        ),
        has_server_access=Mock(
            side_effect=lambda owner, name, permission: name == "other"
        ),
        get_client=AsyncMock(),
        get_client_for_chat=AsyncMock(return_value="other server"),
    )
    monkeypatch.setattr(config_manager, "get_config_manager", lambda: manager)
    with pytest.raises(execution.ExecutionError, match="cannot trade"):
        await execution._client(card)
    manager.get_client.assert_not_awaited()
    manager.get_client_for_chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_preview_and_creation_use_exact_server_and_group_protocol(
    card, monkeypatch
):
    import config_manager

    client = object()
    manager = SimpleNamespace(
        get_server=Mock(return_value={"host": "fixed.test", "port": 8000}),
        has_server_access=Mock(return_value=True),
        get_client=AsyncMock(return_value=client),
    )
    card.server_fingerprint = execution.server_fingerprint(manager, "fixed")
    monkeypatch.setattr(config_manager, "get_config_manager", lambda: manager)
    call = AsyncMock(
        side_effect=[
            {
                "version": 1,
                "connectors": ["binance_perpetual"],
            },
            quote(),
            {"id": card.id, "status": "OPEN"},
        ]
    )
    monkeypatch.setattr(execution, "request", call)
    result = await execution.preview(card)
    assert result["quantity"] == "1"
    card.create_payload = execution.payload(card)
    await execution.open_group(card)
    assert [args.args[1:3] for args in call.await_args_list] == [
        ("get", "/executors/position-groups/capabilities"),
        ("post", "/executors/position-groups/preview"),
        ("put", "/executors/position-groups/abc"),
    ]
    assert all(args.args[0] is client for args in call.await_args_list)
    assert all(args.args == ("fixed",) for args in manager.get_client.await_args_list)
    assert call.await_args.kwargs["json"] == card.create_payload
    assert call.await_args.kwargs["unsupported"] is None


@pytest.mark.asyncio
async def test_mock_card_needs_an_executor_that_lists_mock(card, monkeypatch):
    import config_manager

    manager = SimpleNamespace(
        get_server=Mock(return_value={"host": "fixed.test", "port": 8000}),
        has_server_access=Mock(return_value=True),
        get_client=AsyncMock(return_value=object()),
    )
    card.server_fingerprint = execution.server_fingerprint(manager, "fixed")
    card.mode = "mock"
    monkeypatch.setattr(config_manager, "get_config_manager", lambda: manager)
    # An executor without modes predates mock: it would trade the card live.
    call = AsyncMock(return_value={"version": 1, "connectors": ["binance_perpetual"]})
    monkeypatch.setattr(execution, "request", call)
    with pytest.raises(execution.ExecutionError, match="mode"):
        await execution.preview(card)
    assert call.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_fingerprint", [False, True])
async def test_target_rebinding_or_missing_identity_stops_before_any_request(
    card, monkeypatch, missing_fingerprint
):
    import config_manager

    manager = SimpleNamespace(
        get_server=Mock(return_value={"host": "original.test", "port": 8000}),
        has_server_access=Mock(return_value=True),
        get_client=AsyncMock(),
    )
    if not missing_fingerprint:
        card.server_fingerprint = execution.server_fingerprint(manager, "fixed")
    manager.get_server.return_value = {"host": "replacement.test", "port": 8000}
    monkeypatch.setattr(config_manager, "get_config_manager", lambda: manager)
    call = AsyncMock()
    monkeypatch.setattr(execution, "request", call)
    with pytest.raises(execution.ExecutionError, match="server address"):
        await execution.read_group(card)
    manager.get_client.assert_not_awaited()
    call.assert_not_awaited()


@pytest.mark.asyncio
async def test_old_api_and_risk_errors_are_chinese_without_order_fallback(
    card, monkeypatch
):
    monkeypatch.setattr(execution, "_client", AsyncMock(return_value=object()))
    call = AsyncMock(
        side_effect=aiohttp.ClientResponseError(
            None, (), status=404, message="Not Found"
        )
    )
    monkeypatch.setattr(execution, "request", call)
    with pytest.raises(execution.ExecutionError, match="Update the executor"):
        await execution.preview(card)
    call.assert_awaited_once()
    call.side_effect = aiohttp.ClientResponseError(
        None, (), status=422, message="minimum notional violated"
    )
    with pytest.raises(execution.ExecutionError, match="minimum order"):
        await execution.open_group(card)
