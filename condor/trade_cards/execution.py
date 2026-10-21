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
        raise ExecutionError("无权交易卡片所属服务器，或该服务器已被删除。", status=403)
    return manager


def server_fingerprint(manager, server_name: str) -> str:
    server = manager.get_server(server_name)
    if not server or not server.get("host") or not server.get("port"):
        raise ExecutionError("卡片所属服务器配置不完整，已暂停操作。")
    return hashlib.sha256(
        json.dumps([server["host"], int(server["port"])]).encode()
    ).hexdigest()


async def _client(card: Card):
    manager = authorize_target(card.owner_id, card.server_name)
    if card.server_fingerprint != server_fingerprint(manager, card.server_name):
        raise ExecutionError(
            "卡片所属服务器地址已变更或无法核实，已暂停操作，请核对原服务器。",
            status=403,
        )

    try:
        client = await manager.get_client(card.server_name)
    except Exception as exc:
        logger.warning(
            "Trade card server connection unavailable: %s", type(exc).__name__
        )
        raise ExecutionError("卡片所属交易服务器暂时不可用，请稍后重试。") from exc
    if client is None:
        raise ExecutionError("卡片所属交易服务器暂时不可用，请稍后重试。")
    # Client initialization awaits I/O; configuration and access may change
    # while it is suspended. Recheck before any card-specific request.
    authorize_target(card.owner_id, card.server_name)
    if card.server_fingerprint != server_fingerprint(manager, card.server_name):
        raise ExecutionError(
            "卡片所属服务器地址已变更，已暂停操作，请核对原服务器。", status=403
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
            "每笔止盈分仓低于交易所最小订单数量或金额，请增加保证金。",
        ),
        (
            ("insufficient", "available balance", "balance is"),
            "可用保证金不足，请减少金额或检查账户余额。",
        ),
        (
            ("autoaddmargin", "auto-add", "auto add", "auto_add"),
            "账户开启了自动追加保证金，无法按限定风险下单。",
        ),
        (
            (
                "existing position",
                "open position",
                "existing exposure",
                "position already",
                "symbol_busy",
            ),
            "该交易对已有持仓或其他托管计划，请先核对账户。",
        ),
        (
            ("open order", "active order", "existing order"),
            "该交易对已有挂单，请先核对账户。",
        ),
        (("liquidation",), "预计强平价未能确定位于止损之外，无法下单。"),
        (
            ("isolated", "margin mode", "margin_type", "margintype"),
            "未能确认账户采用逐仓保证金模式，无法下单。",
        ),
        (("leverage",), "未能确认账户杠杆为 10x，无法下单。"),
        (
            ("operation", "conflict", "different request"),
            "执行端有其他操作进行中或请求冲突，请刷新核对后重试。",
        ),
        (
            ("price", "market data", "snapshot", "unavailable"),
            "执行端行情或账户数据暂不可用，请稍后重试。",
        ),
        (
            ("expire", "entry zone", "stop", "target"),
            "计划已过期或当前价格不符合入场条件，请重新核对。",
        ),
        (
            ("support", "connector", "capabilit"),
            "执行端暂不支持该卡片模式或交易所，请检查执行端版本。",
        ),
    )
    for terms, message in mappings:
        if any(term in text for term in terms):
            return message
    return "执行端未能完成请求，请检查执行端状态后重试。"


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
            reason = "执行端尚未支持交易卡片接口，请先更新执行端。"
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
            "执行端尚未支持交易卡片接口，请先更新执行端。", status=501
        ) from exc
    except (aiohttp.ClientError, TimeoutError, AttributeError) as exc:
        raise ExecutionError("执行端响应未确认，正在安全核对，请勿重复下单。") from exc
    if not isinstance(result, dict):
        raise ExecutionError("执行端响应格式无效，正在安全核对。")
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
            "执行端不支持此卡片模式或交易所，请先更新执行端。", status=501
        )
    result = validate_quote(await _request(card, "POST", "/preview", payload(card)))
    liquidation = Decimal(str(result["liquidation_price"]))
    if (card.plan.side == "long" and liquidation >= card.plan.stop_price) or (
        card.plan.side == "short" and liquidation <= card.plan.stop_price
    ):
        raise ValueError("预计强平价没有位于止损之外，无法按此保证金方案下单。")
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
        raise ExecutionError("当前价格不可用，卡片状态暂未更新。") from None
    if not value.is_finite() or value <= 0:
        raise ExecutionError("当前价格不可用，卡片状态暂未更新。")
    return value
