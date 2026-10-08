from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from condor.fetchers.portfolio import (
    PORTFOLIO_HISTORY_RANGES,
    UNIFIED_ACCOUNT_NOTE,
    balance_value,
    connector_equity,
    dedupe_unified_accounts,
    equity_value,
)
from condor.server_data_service import ServerDataType, get_server_data_service
from condor.web.auth import require_server_access
from condor.web.models import (
    BalanceItem,
    ConnectorBalance,
    PortfolioHistoryPoint,
    PortfolioHistoryResponse,
    PortfolioResponse,
    WebUser,
)
from condor.web.routes._errors import upstream_error
from config_manager import get_config_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["portfolio"])


@router.get("/servers/{name}/portfolio", response_model=PortfolioResponse)
async def get_portfolio(
    name: str,
    refresh: bool = Query(False),
    user: WebUser = Depends(require_server_access),
):
    cm = get_config_manager()

    try:
        if refresh:
            # Bypass SDS cache — force exchange re-fetch via Hummingbot
            from condor.fetchers.portfolio import fetch_portfolio_refreshed

            client = await cm.get_client(name)
            state = await fetch_portfolio_refreshed(client)
            # Update SDS cache so subsequent non-refresh reads get fresh data
            get_server_data_service().put(name, ServerDataType.PORTFOLIO, state)
        else:
            state = await get_server_data_service().get_or_fetch(
                name, ServerDataType.PORTFOLIO
            )
    except Exception as e:
        logger.exception("Portfolio fetch failed for '%s'", name)
        raise upstream_error("Failed to get portfolio", e)

    if state is None:
        # Check if fetch is registered
        sds = get_server_data_service()
        registered = ServerDataType.PORTFOLIO in sds._fetch_registry
        logger.warning(
            "Portfolio returned None for %s (fetch registered: %s, health: %s)",
            name,
            registered,
            (
                sds.get_server_health(name).status.value
                if name in sds._health
                else "unknown"
            ),
        )
        raise HTTPException(
            status_code=502,
            detail=f"Failed to get portfolio: server '{name}' returned no data"
            + ("" if registered else " (fetch not registered — try restarting)"),
        )

    # Parse portfolio state into structured response
    # API returns: {account_name: {connector_name: [balance_dicts]}}
    state, unified = dedupe_unified_accounts(state)
    connectors: list[ConnectorBalance] = []
    total_usd = 0.0

    if isinstance(state, dict):
        for account_name, account_data in state.items():
            if not isinstance(account_data, dict):
                continue

            for connector_name, connector_balances in account_data.items():
                if not isinstance(connector_balances, list):
                    continue

                balances: list[BalanceItem] = []
                connector_total = 0.0

                for item in connector_balances:
                    if not isinstance(item, dict):
                        continue
                    token = item.get("token", item.get("asset", ""))
                    total_bal = float(item.get("units", item.get("total_balance", 0)))
                    available = float(
                        item.get(
                            "available_units", item.get("available_balance", total_bal)
                        )
                    )
                    usd_val = balance_value(item)

                    if not token:
                        continue

                    balances.append(
                        BalanceItem(
                            token=token,
                            total=total_bal,
                            available=available,
                            usd_value=usd_val,
                        )
                    )
                    connector_total += usd_val

                # Filter out zero-value tokens and sort by value descending
                balances = [b for b in balances if abs(b.usd_value) >= 0.01]
                balances.sort(key=lambda b: b.usd_value, reverse=True)

                equity = connector_equity(connector_balances, connector_name)
                connectors.append(
                    ConnectorBalance(
                        account_name=account_name,
                        connector=connector_name,
                        balances=balances,
                        total_usd=connector_total,
                        equity_usd=equity,
                        unrealized_pnl_usd=(
                            equity - connector_total if equity is not None else None
                        ),
                        note=(
                            UNIFIED_ACCOUNT_NOTE
                            if (account_name, connector_name) in unified
                            else None
                        ),
                    )
                )

    total_usd = sum(c.total_usd for c in connectors)
    equity = (
        sum(c.equity_usd for c in connectors if c.equity_usd is not None)
        if all(c.equity_usd is not None for c in connectors)
        else None
    )
    return PortfolioResponse(
        server=name,
        connectors=connectors,
        total_usd=total_usd,
        equity_usd=equity,
        unrealized_pnl_usd=(equity - total_usd if equity is not None else None),
    )


@router.get(
    "/servers/{name}/portfolio/history", response_model=PortfolioHistoryResponse
)
async def get_portfolio_history(
    name: str,
    range: str = Query("1D", pattern="^(1D|1W|1M|3M)$"),
    breakdown: bool = Query(False),
    user: WebUser = Depends(require_server_access),
):

    # The raw snapshots are the same regardless of *breakdown* (parsing happens
    # below), so the cache key is the range alone. SDS coalesces concurrent
    # reads of the same expired key into one backend fetch.
    try:
        history = await get_server_data_service().get_or_fetch(
            name, ServerDataType.PORTFOLIO_HISTORY, range_key=range
        )
    except Exception as e:
        logger.warning("Failed to get portfolio history: %s", e)
        history = None

    _, interval = PORTFOLIO_HISTORY_RANGES[range]

    logger.debug("Portfolio history response shape: %s", type(history))
    if isinstance(history, dict):
        logger.debug("Portfolio history keys: %s", list(history.keys()))

    entries, keyed = _extract_snapshot_entries(history)
    # A window must use ONE valuation basis. Old perpetual snapshots have no
    # floating PnL; mixing wallet and equity would fabricate a gain/loss at the
    # upgrade boundary. In that case keep a clearly labelled wallet series.
    use_equity = bool(entries) and all(
        _has_equity(_snapshot_state(snapshot, keyed)) for _, snapshot in entries
    )

    # Each entry is a snapshot, not a delta. Older API versions omitted empty
    # connectors; carrying them forward counts transferred money twice.
    points: list[PortfolioHistoryPoint] = []
    for ts, snapshot in entries:
        total = (
            0 if keyed else snapshot.get("total_value", snapshot.get("total_usd", 0))
        )
        if use_equity or total == 0:
            # Sum token values from nested structure
            # API returns {timestamp, state: {account: {connector: [balances]}}}
            cur_totals = _extract_connector_totals(
                _snapshot_state(snapshot, keyed), use_equity=use_equity
            )
            total = sum(cur_totals.values())
        points.append(PortfolioHistoryPoint(timestamp=ts, total_usd=float(total)))

    points.sort(key=lambda p: p.timestamp)

    top_tokens: list[str] = []
    if breakdown and points:
        top_tokens = _build_token_breakdown(
            entries, keyed, points, use_equity=use_equity
        )

    return PortfolioHistoryResponse(
        server=name,
        points=points,
        interval=interval,
        top_tokens=top_tokens,
        valuation="equity" if use_equity else "wallet",
    )


_SNAPSHOT_LIST_KEYS = ("data", "snapshots", "history", "points", "results")


def _snapshot_state(snapshot: Any, keyed: bool) -> Any:
    """Return the portfolio state within a snapshot entry."""
    return snapshot if keyed else snapshot.get("state", snapshot)


def _extract_snapshot_entries(history: Any) -> tuple[list[tuple[float, Any]], bool]:
    """Normalize a history response into sorted (timestamp, snapshot) entries.

    Handles three shapes: a bare list of snapshots, a dict with the snapshot
    list under a common key, or a dict mapping timestamps to portfolio states.
    Returns (entries, keyed); keyed=True means the dict-keyed-timestamp shape,
    where each entry holds a raw portfolio state instead of a snapshot dict.
    """
    snapshots = None
    if isinstance(history, list):
        snapshots = history
    elif isinstance(history, dict):
        # Try common keys
        for key in _SNAPSHOT_LIST_KEYS:
            if key in history and isinstance(history[key], list):
                snapshots = history[key]
                break
        if snapshots is None:
            # The dict itself maps timestamps → portfolio states
            keyed_entries: list[tuple[float, Any]] = [
                (_parse_timestamp(ts_key), snapshot_data)
                for ts_key, snapshot_data in history.items()
            ]
            keyed_entries.sort(key=lambda x: x[0])
            return keyed_entries, True

    entries: list[tuple[float, Any]] = []
    for snapshot in snapshots or []:
        if not isinstance(snapshot, dict):
            continue
        ts = snapshot.get("timestamp", snapshot.get("time", snapshot.get("t", 0)))
        if not ts:
            continue
        entries.append((_parse_timestamp(ts), snapshot))
    entries.sort(key=lambda x: x[0])
    return entries, False


def _build_token_breakdown(
    entries: list[tuple[float, Any]],
    keyed: bool,
    points: list[PortfolioHistoryPoint],
    use_equity: bool = False,
) -> list[str]:
    """Populate each point's per-token values, collapsing beyond the top 8 into "Other".

    Mutates ``point.tokens`` in place and returns the top token names.
    """
    # Use only this snapshot's holdings. A missing token may have been sold
    # or withdrawn; a past holding is not evidence that it still exists.
    ts_token_map: dict[float, dict[str, float]] = {}
    for ts, snapshot in entries:
        token_vals = _extract_token_values(
            _snapshot_state(snapshot, keyed), use_equity=use_equity
        )
        if not token_vals:
            continue
        ts_token_map[ts] = token_vals

    if not ts_token_map:
        return []

    # Determine top 8 tokens by aggregate value
    agg: dict[str, float] = {}
    for tv in ts_token_map.values():
        for token, val in tv.items():
            agg[token] = agg.get(token, 0) + abs(val)
    top_tokens = sorted(agg, key=lambda t: agg[t], reverse=True)[:8]
    top_set = set(top_tokens)

    # Populate token breakdown on each point, collapsing rest into "Other"
    for point in points:
        tv = ts_token_map.get(point.timestamp, {})
        if not tv:
            continue
        tokens_out: dict[str, float] = {}
        other = 0.0
        for token, val in tv.items():
            if token in top_set:
                tokens_out[token] = val
            else:
                other += val
        if other != 0:
            tokens_out["Other"] = other
        point.tokens = tokens_out

    if "Other" in {t for p in points for t in p.tokens} and "Other" not in top_tokens:
        top_tokens.append("Other")
    return top_tokens


def _parse_timestamp(val: object) -> float:
    """Parse a timestamp that may be numeric or ISO 8601 string."""
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        try:
            return float(val)
        except ValueError:
            pass
        try:
            dt = datetime.fromisoformat(val)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except (ValueError, TypeError):
            pass
    return 0.0


def _has_equity(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    data, _ = dedupe_unified_accounts(data)
    for account in data.values():
        if not isinstance(account, dict):
            return False
        for connector, balances in account.items():
            if (
                not isinstance(balances, list)
                or connector_equity(balances, connector) is None
            ):
                return False
    return True


def _extract_token_values(data: object, use_equity: bool = False) -> dict[str, float]:
    """Extract per-token USD values from a portfolio snapshot."""
    tokens: dict[str, float] = {}
    if not isinstance(data, dict):
        return tokens
    data, _ = dedupe_unified_accounts(data)
    for val in data.values():
        if isinstance(val, dict):
            for connector, inner in val.items():
                if isinstance(inner, list):
                    for item in inner:
                        if isinstance(item, dict):
                            token = item.get("token", item.get("asset", ""))
                            usd = (
                                equity_value(item, connector)
                                if use_equity
                                else balance_value(item)
                            )
                            if usd is None:
                                continue
                            if token and usd != 0:
                                tokens[token] = tokens.get(token, 0) + usd
    return tokens


def _extract_connector_totals(
    data: object, use_equity: bool = False
) -> dict[str, float]:
    """Extract per-connector USD totals from a portfolio snapshot."""
    totals: dict[str, float] = {}
    if not isinstance(data, dict):
        return totals
    data, _ = dedupe_unified_accounts(data)
    for account, val in data.items():
        if isinstance(val, dict):
            for connector, inner in val.items():
                key = f"{account}:{connector}"
                if isinstance(inner, list):
                    s = 0.0
                    for item in inner:
                        if isinstance(item, dict):
                            value = (
                                equity_value(item, connector)
                                if use_equity
                                else balance_value(item)
                            )
                            if value is not None:
                                s += value
                    totals[key] = s
                elif isinstance(inner, (int, float)):
                    totals[key] = float(inner)
        elif isinstance(val, (int, float)):
            totals[account] = float(val)
    return totals
