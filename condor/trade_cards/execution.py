"""Thin client for atomic, idempotent API position groups.

Both live and dry-run execution run on that service. In particular this module
never emulates exchange stops and never falls back to individual executors.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from decimal import Decimal, InvalidOperation

import aiohttp

from condor.fetchers.raw_api import ApiRouteUnsupported, request

from .models import Card
from .sizing import validate_quote

PREFIX = "/executors/position-groups"
logger = logging.getLogger(__name__)


class ExecutionError(RuntimeError):
    def __init__(
        self, message: str, *, status: int | None = None, code: str | None = None
    ):
        super().__init__(message)
        self.status = status
        self.code = code


def authorize_target(owner_id: int, server_name: str):
    from config_manager import ServerPermission, get_config_manager

    manager = get_config_manager()
    if manager.get_server(server_name) is None or not manager.has_server_access(
        owner_id, server_name, ServerPermission.TRADER
    ):
        raise ExecutionError(
            "You cannot trade on this card's server, or the server was deleted.",
            status=403,
        )
    return manager


def server_fingerprint(manager, server_name: str) -> str:
    server = manager.get_server(server_name)
    if not server or not server.get("host") or not server.get("port"):
        raise ExecutionError(
            "This card's server configuration is incomplete; actions are paused."
        )
    return hashlib.sha256(
        json.dumps([server["host"], int(server["port"])]).encode()
    ).hexdigest()


async def _client(card: Card):
    manager = authorize_target(card.owner_id, card.server_name)
    if card.server_fingerprint != server_fingerprint(manager, card.server_name):
        raise ExecutionError(
            "This card's server address changed or cannot be verified; actions are paused. Check the original server.",
            status=403,
        )

    try:
        client = await manager.get_client(card.server_name)
    except Exception as exc:
        logger.warning(
            "Trade card server connection unavailable: %s", type(exc).__name__
        )
        raise ExecutionError(
            "This card's trading server is unavailable. Try again shortly."
        ) from exc
    if client is None:
        raise ExecutionError(
            "This card's trading server is unavailable. Try again shortly."
        )
    # Client initialization awaits I/O; configuration and access may change
    # while it is suspended. Recheck before any card-specific request.
    authorize_target(card.owner_id, card.server_name)
    if card.server_fingerprint != server_fingerprint(manager, card.server_name):
        raise ExecutionError(
            "This card's server address changed; actions are paused. Check the original server.",
            status=403,
        )
    return client


def user_error(reason) -> str:
    """Keep execution protocol errors useful without exposing server internals."""
    text = str(reason).lower()
    mappings = (
        (
            (
                "min_notional",
                "minimum notional",
                "minimum order",
                "minimum amount",
                "min_order",
            ),
            "Each take-profit part is below the exchange's minimum order size. Increase the margin.",
        ),
        (
            ("insufficient", "available balance", "balance is"),
            "Not enough available margin. Lower the amount or check the account balance.",
        ),
        (
            ("autoaddmargin", "auto-add", "auto add", "auto_add"),
            "The account has auto-add margin on, so the order cannot keep its risk limit.",
        ),
        (
            (
                "existing position",
                "open position",
                "existing exposure",
                "position already",
                "symbol_busy",
            ),
            "This pair already has a position or another managed plan. Check the account first.",
        ),
        (
            ("open order", "active order", "existing order"),
            "This pair already has open orders. Check the account first.",
        ),
        (
            ("liquidation",),
            "The estimated liquidation price could not be confirmed beyond the stop; no order was placed.",
        ),
        (
            ("isolated", "margin mode", "margin_type", "margintype"),
            "Could not confirm isolated margin mode; no order was placed.",
        ),
        (("leverage",), "Could not confirm 10x leverage; no order was placed."),
        (
            ("operation", "conflict", "different request"),
            "The executor is busy with another action or the request conflicts. Refresh and try again.",
        ),
        (
            ("price", "market data", "snapshot", "unavailable"),
            "The executor's market or account data is unavailable. Try again shortly.",
        ),
        (
            ("expire", "entry zone", "stop", "target"),
            "The plan expired or the price no longer meets the entry conditions.",
        ),
        (
            ("support", "connector", "capabilit"),
            "The executor does not support this card mode or exchange. Check the executor version.",
        ),
    )
    for terms, message in mappings:
        if any(term in text for term in terms):
            return message
    return (
        "The executor could not complete the request. Check its status and try again."
    )


async def _request(card: Card, method: str, suffix: str, body: dict | None = None):
    client = await _client(card)
    try:
        result = await request(
            client,
            method.lower(),
            PREFIX + suffix,
            json=body,
            timeout=aiohttp.ClientTimeout(total=30),
            unsupported=None,
        )
    except aiohttp.ClientResponseError as exc:
        logger.warning(
            "Trade card API rejected %s %s: HTTP %s", method, suffix, exc.status
        )
        reason = user_error(exc.message)
        if exc.status in {404, 405, 501}:
            reason = "The executor does not support trade cards yet. Update the executor first."
        code = str(exc.message).partition(":")[0]
        raise ExecutionError(
            reason,
            status=exc.status,
            code=(
                code
                if code in {"RISK_REJECTED", "SYMBOL_BUSY", "GROUP_CONFLICT"}
                else None
            ),
        ) from exc
    except ApiRouteUnsupported as exc:
        raise ExecutionError(
            "The executor does not support trade cards yet. Update the executor first.",
            status=501,
        ) from exc
    except (aiohttp.ClientError, TimeoutError, AttributeError) as exc:
        raise ExecutionError(
            "The executor's response is unconfirmed; it is being checked. Do not repeat the order."
        ) from exc
    if not isinstance(result, dict):
        raise ExecutionError(
            "The executor's response is malformed; it is being checked."
        )
    return result


def payload(card: Card) -> dict:
    plan = card.plan
    return {
        "account_name": card.account_name,
        "connector_name": card.connector_name,
        "trading_pair": plan.trading_pair,
        "side": plan.side,
        "entry_low": str(plan.entry_low),
        "entry_high": str(plan.entry_high),
        "stop_price": str(plan.stop_price),
        "tp1_price": str(plan.tp1_price),
        "tp2_price": str(plan.tp2_price),
        "margin": card.margin,
        "leverage": 10,
        "mode": card.mode,
        "entry_expires_at": card.expires_at,
    }


async def preview(card: Card) -> dict:
    capabilities = await _request(card, "GET", "/capabilities")
    if (
        capabilities.get("version") != 1
        or card.mode not in capabilities.get("modes", [])
        or card.connector_name not in capabilities.get("connectors", [])
    ):
        raise ExecutionError(
            "The executor does not support this card mode or exchange. Update the executor first.",
            status=501,
        )
    result = validate_quote(await _request(card, "POST", "/preview", payload(card)))
    liquidation = Decimal(str(result["liquidation_price"]))
    if (card.plan.side == "long" and liquidation >= card.plan.stop_price) or (
        card.plan.side == "short" and liquidation <= card.plan.stop_price
    ):
        raise ValueError(
            "The estimated liquidation price is not beyond the stop, so this margin cannot be used."
        )
    return result


async def open_group(card: Card) -> dict:
    return await _request(card, "PUT", f"/{card.id}", card.create_payload)


async def read_group(card: Card) -> dict:
    return await _request(card, "GET", f"/{card.id}")


async def command(card: Card) -> dict:
    return await _request(
        card,
        "POST",
        f"/{card.id}/commands",
        {"operation_id": card.operation_id, "action": card.pending_action},
    )


async def current_price(card: Card) -> Decimal:
    client = await _client(card)
    result = await asyncio.wait_for(
        client.market_data.get_prices(card.connector_name, [card.plan.trading_pair]),
        timeout=20,
    )
    try:
        value = Decimal(str(result.get("prices", result)[card.plan.trading_pair]))
    except (KeyError, TypeError, InvalidOperation, AttributeError):
        raise ExecutionError(
            "The current price is unavailable; the card was not updated."
        ) from None
    if not value.is_finite() or value <= 0:
        raise ExecutionError(
            "The current price is unavailable; the card was not updated."
        )
    return value
