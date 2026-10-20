"""What a hummingbot-api server runs, whether its source is current, and bot defaults.

What the Settings → Hummingbot API panel asks about the server the navbar points at
(FEAT-121):

``/system/info`` — the versions and the provenance, offline. Which hummingbot-api and
which hummingbot library, and the API's own container: the image reference, the local
image ID, a registry digest when one matches (``None`` for a local build, never filled
with the image ID), Docker's image creation time, the source-build labels under
``build`` (repository, full revision, branch, the UTC build start and whether the tree
was dirty) and the compose project. It describes the immutable image of the *running*
container, so rebuilding a tag does not change it until the stack is redeployed. It
also carries the ``MARKET_DATA_*`` tunables the process resolved, which the panel shows
read-only — the API cannot rewrite its own ``.env``.

``/system/update-check`` — an explicit comparison of the running build's revision with
``main`` of the source repository. The API makes at most two bounded GitHub requests and
answers one of ``up_to_date``, ``update_available``, ``ahead``, ``diverged``,
``unknown`` or ``error`` with a reason. It contacts GitHub, so it runs only when a user
asks; nothing polls it. Updating stays a manual build and deploy on the API host.

``/bot-orchestration/rate-oracle/config`` — the slice of a credentials profile's
``conf_client.yml`` that a deploy copies into every new bot: the rate oracle source, the
global token, and the share of an exchange's API rate limit one instance may spend. It
applies to bots deployed *after* a change; running bots keep the copy they were built
with.

All three are past the pinned client's surface, so all go through
:mod:`condor.fetchers.raw_api` — see that module for why, and for why a 404 here means
"this server's API does not offer this yet" rather than "this server is down".

**One account.** Condor deploys with ``credentials_profile: master_account``
(``handlers/bots/controller_handlers/deploy_streamlined.py``) and its settings routes already read that
account, so that is the profile these functions ask about. The API accepts others; there
is no Condor path that deploys from one, so exposing a picker would offer a choice that
changes nothing.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import aiohttp

from condor.fetchers import raw_api

logger = logging.getLogger(__name__)

#: The route that reports the server's own versions and image provenance.
SYSTEM_INFO_PATH = "/system/info"

#: The route that reads and writes the bot client defaults in a credentials profile's
#: ``conf_client.yml``. One constant so the read and the write can never disagree.
CLIENT_CONFIG_PATH = "/bot-orchestration/rate-oracle/config"

#: The credentials profile Condor deploys from, and therefore the only one it configures.
CLIENT_CONFIG_ACCOUNT = "master_account"

#: Compares the running build with the source repository's ``main``. Contacts GitHub.
UPDATE_CHECK_PATH = "/system/update-check"

#: The API makes up to two GitHub requests of at most about 10 seconds each, so the
#: proxy waits longer than both rather than reporting its own timeout as the API's.
UPDATE_CHECK_TIMEOUT = aiohttp.ClientTimeout(total=45)


class ApiServerSettingsUnsupported(raw_api.ApiRouteUnsupported):
    """This server's hummingbot-api predates these routes.

    Not an outage: an older build answers 404 to a well-formed request, and the panel
    should say "deploy a newer hummingbot-api" rather than report it offline. A
    subclass of the shared capability exception so either name catches it.
    """


async def fetch_system_info(client) -> dict[str, Any]:
    """The server's versions, image provenance and market-data tunables.

    Raises:
        ApiServerSettingsUnsupported: The server's API predates ``/system/info``.
        aiohttp.ClientResponseError: Any other upstream failure, status preserved.
    """
    return await raw_api.request(
        client,
        "get",
        SYSTEM_INFO_PATH,
        unsupported=ApiServerSettingsUnsupported,
    )


async def fetch_client_config(
    client, account: str = CLIENT_CONFIG_ACCOUNT
) -> dict[str, Any]:
    """The client defaults bots deployed from ``account`` will inherit.

    Returns the persisted ``rate_oracle_source``, ``global_token`` and
    ``rate_limits_share_pct``, plus ``available_sources`` — the source list comes from
    the server rather than from a constant here, because the sources a bot can use are
    whatever the *server's* bundled hummingbot knows about.

    Raises:
        ApiServerSettingsUnsupported: The server's API predates the route.
        aiohttp.ClientResponseError: Any other upstream failure, status preserved.
    """
    return await raw_api.request(
        client,
        "get",
        CLIENT_CONFIG_PATH,
        params={"account_name": account},
        unsupported=ApiServerSettingsUnsupported,
    )


async def update_client_config(
    client,
    *,
    rate_oracle_source: Optional[str] = None,
    global_token_name: Optional[str] = None,
    global_token_symbol: Optional[str] = None,
    rate_limits_share_pct: Optional[float] = None,
    account: str = CLIENT_CONFIG_ACCOUNT,
) -> dict[str, Any]:
    """Change some of the client defaults; fields left ``None`` are not touched.

    Validation stays upstream. The server checks the oracle source against its own
    bundled hummingbot and the share against ``ClientConfigMap``'s own ``0 < pct <= 100``,
    then answers 400/422 — re-checking here would mean a second copy of a rule that has
    to agree with a *remote* library version, which is the copy that goes stale.

    Raises:
        ApiServerSettingsUnsupported: The server's API predates the route.
        aiohttp.ClientResponseError: Any other upstream failure, status preserved — in
            particular the 400/422 that names an invalid value, which the caller forwards.
    """
    changes: dict[str, Any] = {}
    if rate_oracle_source is not None:
        changes["rate_oracle_source"] = {"name": rate_oracle_source}

    global_token: dict[str, Any] = {}
    if global_token_name is not None:
        global_token["global_token_name"] = global_token_name
    if global_token_symbol is not None:
        global_token["global_token_symbol"] = global_token_symbol
    if global_token:
        changes["global_token"] = global_token

    if rate_limits_share_pct is not None:
        changes["rate_limits_share_pct"] = rate_limits_share_pct

    return await raw_api.request(
        client,
        "put",
        CLIENT_CONFIG_PATH,
        params={"account_name": account},
        json=changes,
        unsupported=ApiServerSettingsUnsupported,
    )


async def fetch_update_check(client) -> dict[str, Any]:
    """Compare the server's running source revision with ``main``; one GitHub check.

    The verdict is the API's: Condor neither reads GitHub nor infers commit relations.
    A network failure on the API's side is a 200 with ``status: "error"`` and a reason.

    Raises:
        ApiServerSettingsUnsupported: The server's API predates ``/system/update-check``.
        aiohttp.ClientResponseError: Any other upstream failure, status preserved.
    """
    return await raw_api.request(
        client,
        "get",
        UPDATE_CHECK_PATH,
        timeout=UPDATE_CHECK_TIMEOUT,
        unsupported=ApiServerSettingsUnsupported,
    )
