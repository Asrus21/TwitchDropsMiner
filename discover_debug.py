"""
The --discover-debug mode: which Twitch operations give the campaigns, with which client.

Run once on the user's own machine (it needs their login): every candidate operation is
asked with every known client, and one line per combination is written with the
operation, the client, the HTTP status, whether a campaigns list came back and its size,
and Twitch's error message. The lines go to the main window, to log.txt (with --log, which
--discover-debug turns on) and to discover_debug.txt next to the app, which is written and
closed when the test ends. Tokens, cookies and request headers are never written.

It makes about 25 requests, once, then the app carries on normally.
"""

from __future__ import annotations

import json
import asyncio
from time import monotonic
from datetime import datetime, timezone
from typing import Any, Callable, cast, TYPE_CHECKING

import aiohttp

from version import __version__
from constants import GQL_QUERIES, WORKING_DIR, ClientType
from campaign_sources import OFFICIAL_RAW_QUERY, discovery_logger, gql_error_text

if TYPE_CHECKING:
    from twitch import Twitch


RESULTS_PATH = WORKING_DIR / "discover_debug.txt"

JsonType = dict[str, Any]


def _count(path: tuple[str, ...]) -> Callable[[JsonType], int | None]:
    """How many items the list at `path` (under "data") has, or None if there's no list."""
    def counter(response: JsonType) -> int | None:
        value: Any = response.get("data")
        for key in path:
            if not isinstance(value, dict):
                return None
            value = value.get(key)
        return len(value) if isinstance(value, list) else None
    return counter


def _operations(
    user_id: str, campaign_id: str | None, channel_id: str | None
) -> list[tuple[str, JsonType, Callable]]:
    operations: list[tuple[str, JsonType, Callable]] = [
        (
            "ViewerDropsDashboard",
            GQL_QUERIES["Campaigns"],
            _count(("currentUser", "dropCampaigns")),
        ),
        (
            "ViewerDropsDashboard+rewards",
            GQL_QUERIES["Campaigns"].with_variables({"fetchRewardCampaigns": True}),
            _count(("currentUser", "dropCampaigns")),
        ),
        (
            "plain currentUser.dropCampaigns",
            cast(JsonType, {"query": OFFICIAL_RAW_QUERY}),
            _count(("currentUser", "dropCampaigns")),
        ),
        (
            "Inventory",
            GQL_QUERIES["Inventory"],
            _count(("currentUser", "inventory", "dropCampaignsInProgress")),
        ),
    ]
    if campaign_id is not None:
        operations.append((
            "DropCampaignDetails",
            GQL_QUERIES["CampaignDetails"].with_variables(
                {"channelLogin": user_id, "dropID": campaign_id}
            ),
            _count(("user", "dropCampaign", "timeBasedDrops")),
        ))
    if channel_id is not None:
        operations.append((
            "DropsHighlightService_AvailableDrops",
            GQL_QUERIES["AvailableDrops"].with_variables({"channelID": channel_id}),
            _count(("channel", "viewerDropCampaigns")),
        ))
    return operations


async def _post(
    twitch: Twitch, headers: dict[str, str], body: JsonType
) -> tuple[int, JsonType | None, float]:
    start = monotonic()
    async with twitch._qgl_limiter:
        async with twitch.request(
            "POST", "https://gql.twitch.tv/gql", json=body, headers=headers
        ) as response:
            status: int = response.status
            try:
                data: Any = await response.json(content_type=None)
            except (aiohttp.ContentTypeError, json.JSONDecodeError, ValueError):
                data = None
    return status, data if isinstance(data, dict) else None, monotonic() - start


def _first_campaign_id(response: JsonType | None) -> str | None:
    user = ((response or {}).get("data") or {}).get("currentUser") or {}
    for key in ("dropCampaigns",):
        for campaign in user.get(key) or []:
            if isinstance(campaign, dict) and campaign.get("id"):
                return str(campaign["id"])
    inventory = user.get("inventory") or {}
    for campaign in inventory.get("dropCampaignsInProgress") or []:
        if isinstance(campaign, dict) and campaign.get("id"):
            return str(campaign["id"])
    return None


async def run_discover_debug(twitch: Twitch) -> None:
    auth_state = await twitch.get_auth()
    current = twitch.client_type_name
    started = (
        f"Discover debug: start (logged in with the {current} client; "
        "testing every operation with every known client)"
    )
    discovery_logger.info(started)
    twitch.print(started)
    # a live drops-enabled channel and a campaign ID, to test the per-item operations
    channel_id: str | None = None
    try:
        streams = await twitch.gql_request(cast(Any, {"query": (
            "{ streams(first: 1, options: {systemFilters: [DROPS_ENABLED], sort: VIEWER_COUNT}) "
            "{ edges { node { broadcaster { id } } } } }"
        )}))
        edges = ((streams.get("data") or {}).get("streams") or {}).get("edges") or []
        if edges:
            channel_id = str(edges[0]["node"]["broadcaster"]["id"])
    except Exception as exc:
        discovery_logger.info(f"Discover debug: no live channel to test with ({type(exc).__name__})")
    campaign_id: str | None = None
    try:
        campaign_id = _first_campaign_id(await twitch.gql_request(GQL_QUERIES["Inventory"]))
    except Exception as exc:
        discovery_logger.info(f"Discover debug: no campaign to test with ({type(exc).__name__})")

    rows: list[str] = []
    for client_name, client_info in ClientType.by_name().items():
        for logged_in in (True, False):
            if not logged_in and client_name != current:
                continue  # the no-login baseline only once, with the current client
            headers = auth_state.headers(user_agent=client_info.USER_AGENT, gql=logged_in)
            headers["Client-Id"] = client_info.CLIENT_ID
            if logged_in:
                headers["Origin"] = str(client_info.CLIENT_URL)
                headers["Referer"] = str(client_info.CLIENT_URL)
            for name, body, counter in _operations(
                str(auth_state.user_id), campaign_id, channel_id
            ):
                try:
                    status, data, seconds = await _post(twitch, headers, body)
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    rows.append(
                        f"op={name} client={client_name} login={'yes' if logged_in else 'no'} "
                        f"failed={type(exc).__name__}"
                    )
                    continue
                count = counter(data) if data is not None else None
                errors = gql_error_text(data) if data is not None else ""
                if data is not None and not errors and isinstance(data.get("message"), str):
                    errors = str(data["message"])[:200]  # non-GQL error, e.g. a bad Client-ID
                rows.append(
                    f"op={name} client={client_name} login={'yes' if logged_in else 'no'} "
                    f"http={status} list={'yes' if count is not None else 'no'} "
                    f"count={count if count is not None else '-'} time={seconds:.1f}s"
                    + (f" errors={errors}" if errors else "")
                )
    # the main window and log.txt, one line per combination
    for row in rows:
        twitch.print(f"Discover debug: {row}")
        discovery_logger.info(f"Discover debug: {row}")
    # and a file of its own, written and closed right away
    header = [
        f"Twitch Drops Miner {__version__} - discover debug - "
        f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC",
        f"Logged in with the {current} client. No tokens, cookies or headers are written here.",
        "",
    ]
    try:
        with RESULTS_PATH.open("w", encoding="utf8") as file:
            file.write("\n".join(header + rows) + "\n")
        saved = f"Discover debug: done, {len(rows)} result(s) saved to {RESULTS_PATH}"
    except OSError as exc:
        saved = f"Discover debug: done, but {RESULTS_PATH} couldn't be written: {exc}"
    discovery_logger.info(saved)
    twitch.print(saved)

