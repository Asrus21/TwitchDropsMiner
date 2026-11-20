"""
Where the drop campaigns come from, and the rules applied to them.

There are three sources, tried in this order by the "auto" setting:

- official: the account's campaigns list, the same data twitch.tv/drops/campaigns shows.
  Twitch has refused it to the clients the app can use since September 2026 (integrity
  check), so it's still asked, but rarely answers.
- scan: live drops-enabled streams are listed, and the most watched channels of each game
  are asked which drops the account has there (see Twitch.discover_campaigns).
- snapshot: known_drops_games.json, a list of games that had campaigns on a given day,
  used only when the other two find nothing.

Everything here is plain data in, data out (no network, no GUI), so it can be tested alone.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Protocol, Sequence


logger = logging.getLogger("TwitchDrops")
# The discovery summary and the --discover-debug results. Kept at INFO whatever -v says,
# so --log always records them (main.py sets it up).
discovery_logger = logging.getLogger("TwitchDrops.discovery")

JsonType = dict[str, Any]

SOURCE_AUTO = "auto"
SOURCE_OFFICIAL = "official"
SOURCE_SCAN = "scan"
SOURCE_SNAPSHOT = "snapshot"
CAMPAIGN_SOURCES: tuple[str, ...] = (SOURCE_AUTO, SOURCE_OFFICIAL, SOURCE_SCAN, SOURCE_SNAPSHOT)

# The same list as the persisted ViewerDropsDashboard query, asked as a plain query. Every
# field here is one the app already reads from that query's answer.
OFFICIAL_RAW_QUERY: str = (
    "{ currentUser { dropCampaigns { id name status startAt endAt accountLinkURL detailsURL "
    "game { id name displayName slug boxArtURL } self { isAccountConnected } } } }"
)

ACTIVE_STATUSES: tuple[str, ...] = ("ACTIVE", "UPCOMING")


class SourceUnavailable(Exception):
    """The source didn't give a usable answer (the message says why)."""


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def gql_error_text(response: JsonType) -> str:
    """The error messages of a GQL answer, without anything else from it."""
    errors = response.get("errors") or []
    messages = [str(e.get("message", "?")) for e in errors if isinstance(e, dict)]
    return "; ".join(messages)[:300]


def is_reward_campaign(campaign: JsonType) -> bool:
    """Reward campaigns ("Recompensas") aren't game Drops, the miner doesn't support them."""
    return campaign.get("__typename") == "RewardCampaign" or "unlockRequirements" in campaign


def official_campaigns(response: JsonType) -> tuple[list[JsonType], list[str]]:
    """
    Reads the campaigns list from a ViewerDropsDashboard (or the plain query) answer.

    Returns the drop campaigns, and the names of reward campaigns that were left out.
    Raises SourceUnavailable when Twitch didn't answer with a list.
    """
    if response.get("errors"):
        raise SourceUnavailable(gql_error_text(response))
    user = (response.get("data") or {}).get("currentUser")
    if not isinstance(user, dict):
        raise SourceUnavailable("no campaigns list in the answer (not logged in?)")
    listed = user.get("dropCampaigns")
    if not isinstance(listed, list):
        raise SourceUnavailable("the answer has no dropCampaigns list")
    rewards: list[str] = [
        str(c.get("name") or c.get("id"))
        for c in (user.get("rewardCampaignsAvailableToUser") or [])
        if isinstance(c, dict)
    ]
    campaigns: list[JsonType] = []
    for campaign in listed:
        if not isinstance(campaign, dict) or not campaign.get("id"):
            continue
        if is_reward_campaign(campaign):
            rewards.append(str(campaign.get("name") or campaign["id"]))
            continue
        campaigns.append(campaign)
    return campaigns, rewards


def is_expired(campaign: JsonType, now: datetime) -> bool:
    """Ended by its date (in UTC), or marked as anything but active/upcoming by Twitch."""
    status = campaign.get("status")
    if status is not None and status not in ACTIVE_STATUSES:
        return True
    ends = parse_timestamp(campaign.get("endAt"))
    return ends is not None and ends <= now


def drop_expired(
    campaigns: dict[str, JsonType], now: datetime
) -> tuple[dict[str, JsonType], int]:
    """The campaigns that haven't ended, and how many were left out."""
    kept = {cid: c for cid, c in campaigns.items() if not is_expired(c, now)}
    return kept, len(campaigns) - len(kept)


def apply_official(
    campaigns: dict[str, JsonType], official: Iterable[JsonType]
) -> dict[str, JsonType]:
    """
    Puts the official list's data over the same campaigns from the other sources (the list
    wins on name, dates, status, account link), and returns the listed campaigns that no
    other source had: those have no drops data yet.
    """
    pending: dict[str, JsonType] = {}
    for listed in official:
        campaign = campaigns.get(listed["id"])
        if campaign is None:
            pending[listed["id"]] = listed
            continue
        for key in ("name", "status", "startAt", "endAt", "accountLinkURL", "detailsURL"):
            if listed.get(key):
                campaign[key] = listed[key]
        if isinstance(listed.get("self"), dict) and "isAccountConnected" in listed["self"]:
            campaign.setdefault("self", {})
            campaign["self"]["isAccountConnected"] = listed["self"]["isAccountConnected"]
        if isinstance(listed.get("game"), dict):
            game = campaign.setdefault("game", {})
            for key, value in listed["game"].items():
                if value and not game.get(key):
                    game[key] = value
    return pending


def is_linked(campaign: JsonType) -> bool:
    linked = (campaign.get("self") or {}).get("isAccountConnected")
    return linked is not False


def has_drops_left(campaign: JsonType) -> bool:
    for drop in campaign.get("timeBasedDrops") or []:
        state = drop.get("self") or {}
        if state.get("isClaimed"):
            continue
        required = drop.get("requiredMinutesWatched") or 0
        if required > 0 and (state.get("currentMinutesWatched") or 0) < required:
            return True
    return False


class DiscoveryReport:
    """The numbers behind the "Campaign discovery:" log line."""

    def __init__(self, source: str):
        self.source = source
        self.total = 0
        self.mineable = 0
        self.need_link = 0
        self.expired = 0
        self.rewards = 0
        self.waiting: list[str] = []  # games with campaigns and no live channel (yet)
        self.notes: list[str] = []

    def count(self, campaigns: dict[str, JsonType], pending: dict[str, JsonType]) -> None:
        """Counts the final campaigns, and the listed ones still without drops data."""
        self.total = len(campaigns) + len(pending)
        self.need_link = 0
        self.mineable = 0
        for campaign in list(campaigns.values()) + list(pending.values()):
            if not is_linked(campaign):
                self.need_link += 1
        for campaign in campaigns.values():
            if is_linked(campaign) and has_drops_left(campaign):
                self.mineable += 1

    def line(self) -> str:
        text = (
            f"Campaign discovery: source={self.source}, campaigns={self.total}, "
            f"mineable={self.mineable}, no live stream={len(self.waiting)}, "
            f"expired ignored={self.expired}, need account link={self.need_link}"
        )
        if self.rewards:
            text += f", reward campaigns ignored={self.rewards}"
        if self.notes:
            text += ". " + "; ".join(self.notes)
        return text


class _Sortable(Protocol):
    @property
    def ends_at(self) -> datetime: ...

    @property
    def game(self) -> Any: ...


def mining_order(
    campaigns: Sequence[_Sortable], priority: Sequence[str], ending_soonest: bool
) -> list[_Sortable]:
    """
    The priority list's games first (in its order), then, with ending_soonest, the ones
    that end first.
    """
    ordered = list(campaigns)
    if ending_soonest:
        ordered.sort(key=lambda c: c.ends_at)
    rank = {name: index for index, name in enumerate(priority)}
    ordered.sort(key=lambda c: rank.get(c.game.name, len(rank)))
    return ordered


def load_snapshot(path: Path, now: datetime) -> list[JsonType]:
    """
    The games of known_drops_games.json that haven't ended, as {"id", "name", "slug"}.

    Entries past their ends_at are skipped, and the whole list stops being used once it's
    older than its max_age_days.
    """
    try:
        with path.open("r", encoding="utf8") as file:
            data: JsonType = json.load(file)
    except (OSError, ValueError) as exc:
        logger.warning(f"Unable to read the games snapshot: {exc}")
        return []
    captured = parse_timestamp(data.get("captured_at"))
    max_age = timedelta(days=int(data.get("max_age_days") or 45))
    if captured is not None and now - captured > max_age:
        logger.warning(
            f"The games snapshot is from {captured:%Y-%m-%d}, too old to use (over {max_age.days} days)"
        )
        return []
    games: list[JsonType] = []
    for entry in data.get("games") or []:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        ends = parse_timestamp(entry.get("ends_at"))
        if ends is not None and ends <= now:
            continue
        games.append({
            "id": str(entry.get("id") or ""),
            "name": entry["name"],
            "slug": entry.get("slug") or "",
        })
    return games
