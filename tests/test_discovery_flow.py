"""
The whole campaign gathering (Twitch.fetch_inventory) with the network faked: which source
gets used, the fallbacks, the merge with the official list, the caches and Reload.
"""

from __future__ import annotations

import json
import asyncio
from copy import deepcopy
from collections import deque
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

import twitch as twitch_module
from twitch import Twitch
from exceptions import GQLException
from constants import ClientType
from conftest import load_fixture


DASHBOARD = load_fixture("viewer_drops_dashboard.json")
INTEGRITY = load_fixture("integrity_failed.json")
AVAILABLE = load_fixture("available_drops.json")
DIRECTORY = load_fixture("game_directory.json")
LOL_DIRECTORY = DIRECTORY[0]  # League of Legends, 3 live channels (canal1..3)
LIVE_LOL = {"900001", "900002", "900003"}
KNOWN_OFFLINE = {
    "call-of-duty-warzone": ("512710", "Call of Duty: Warzone"),
    "smite-2": ("2094865572", "SMITE 2"),
    "albion-online": ("417528", "Albion Online"),
}


class FakeTwitch:
    """Answers the GQL operations the gathering uses, and counts them."""

    def __init__(self, *, official: str, scan_channels: bool = True, available: bool = True):
        self.official = official  # "ok", "integrity"
        self.scan_channels = scan_channels
        self.available = available
        self.calls: list[str] = []

    def answer(self, op: dict[str, Any]) -> dict[str, Any]:
        if "query" in op:
            self.calls.append("plain:currentUser")
            if self.official == "ok":
                return deepcopy(DASHBOARD)
            raise GQLException(INTEGRITY["errors"])
        name = op["operationName"]
        self.calls.append(name)
        variables = op.get("variables") or {}
        if name == "Inventory":
            return {"data": {"currentUser": {"inventory": {
                "dropCampaignsInProgress": [], "gameEventDrops": []}}}}
        if name == "ViewerDropsDashboard":
            if self.official == "ok":
                return deepcopy(DASHBOARD)
            raise GQLException(INTEGRITY["errors"])
        if name == "DropCampaignDetails":
            # gated the same way as the list
            raise GQLException(INTEGRITY["errors"])
        if name == "DirectoryPage_Game":
            slug = variables.get("slug")
            if slug == "league-of-legends":
                return deepcopy(LOL_DIRECTORY)
            if slug in KNOWN_OFFLINE:
                # a real game with no live drops-enabled stream right now
                game_id, game_name = KNOWN_OFFLINE[slug]
                return {"data": {"game": {
                    "id": game_id, "displayName": game_name, "streams": {"edges": []}}}}
            return {"data": {"game": None}}
        if name == "DirectoryGameRedirect":
            return {"data": {"game": None}}
        if name == "DropsHighlightService_AvailableDrops":
            channel_id = variables["channelID"]
            if self.available and channel_id == "900001":
                return deepcopy(AVAILABLE)
            return {"data": {"channel": {"id": channel_id, "viewerDropCampaigns": None}}}
        raise AssertionError(f"unexpected operation {name}")

    async def gql_request(self, ops):
        if isinstance(ops, list):
            return [self.answer(op) for op in ops]
        return self.answer(ops)

    def scan_page(self) -> dict[str, Any]:
        self.calls.append("scan")
        edges = []
        if self.scan_channels:
            for edge in LOL_DIRECTORY["data"]["game"]["streams"]["edges"]:
                node = edge["node"]
                edges.append({"cursor": "c", "node": {
                    "game": {"id": "21779", "name": "League of Legends",
                             "displayName": "League of Legends", "slug": "league-of-legends"},
                    "broadcaster": node["broadcaster"]}})
        return {"data": {"streams": {"edges": edges, "pageInfo": {"hasNextPage": False}}}}


def make_app(tmp_path, monkeypatch, fake: FakeTwitch, *, source: str = "auto"):
    monkeypatch.setattr(twitch_module, "DROPS_GAMES_PATH", tmp_path / "drops_games.json")
    snapshot = tmp_path / "known_drops_games.json"
    snapshot.write_text(json.dumps({
        "captured_at": "2026-11-20T00:00:00Z", "max_age_days": 45,
        "games": [{"name": "League of Legends", "id": "21779", "slug": "league-of-legends"}],
    }))
    monkeypatch.setattr(twitch_module, "KNOWN_DROPS_GAMES_PATH", snapshot)

    app = object.__new__(Twitch)
    printed: list[str] = []

    async def add_campaign(campaign):
        return None

    app.settings = SimpleNamespace(
        campaign_source=source, campaign_discovery=True, available_drops_check=True,
        exclude=set(), priority=[], dump=False,
    )
    app.gui = SimpleNamespace(
        status=SimpleNamespace(update=lambda *a: None),
        inv=SimpleNamespace(clear=lambda: None, add_campaign=add_campaign),
        channels=None, close_requested=False, print=printed.append,
    )
    app._client_type = ClientType.SMARTBOX
    app._qgl_limiter = asyncio.Semaphore(5)
    app._force_discovery = False
    app._discovery_cache = {}
    app._official_cache = None
    app._drops = {}
    app.inventory = []
    app._campaigns = {}
    app._mnt_triggers = deque()
    app._mnt_task = None
    app.gql_request = fake.gql_request

    async def get_auth():
        return SimpleNamespace(user_id=1)

    async def maintenance():
        return None

    @asynccontextmanager
    async def request(method, url, **kwargs):
        page = fake.scan_page()

        async def json_body():
            return page

        yield SimpleNamespace(json=json_body)

    app.get_auth = get_auth
    app._maintenance_task = maintenance
    app.request = request
    app.printed = printed  # type: ignore[attr-defined]
    return app


def run(app) -> None:
    async def go():
        await Twitch.fetch_inventory(app)
        if app._mnt_task is not None:
            app._mnt_task.cancel()
    asyncio.run(go())


def discovery_line(caplog) -> str:
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Campaign discovery:")]
    assert len(lines) == 1, lines
    return lines[0]


def test_official_list_is_used_and_merged(tmp_path, monkeypatch, caplog):
    caplog.set_level("INFO", logger="TwitchDrops")
    fake = FakeTwitch(official="ok")
    app = make_app(tmp_path, monkeypatch, fake)
    run(app)
    campaigns = {c.id: c for c in app.inventory}
    # c-lol: listed officially, its drops came from the channels; c-scan-only rode along
    assert set(campaigns) == {"c-lol", "c-scan-only"}
    assert campaigns["c-lol"].name == "League Drops"  # the official name won
    assert "scan" not in fake.calls  # no full scan when the official list answers
    line = discovery_line(caplog)
    assert "source=official" in line
    # Warzone and SMITE 2 are linked, active and without a live channel; Albion isn't linked
    assert "no live stream=2" in line
    assert "expired ignored=2" in line
    assert "need account link=1" in line
    assert "reward campaigns ignored=2" in line
    assert any("example.invalid/link-albion" in m for m in app.printed)
    assert any("Call of Duty: Warzone" in m and "SMITE 2" in m for m in app.printed)
    assert not [r for r in caplog.records if "doesn't know the game" in r.getMessage()]
    # the official games are remembered for the scan
    remembered = json.loads((tmp_path / "drops_games.json").read_text())
    assert {"512710", "2094865572", "21779"} <= set(remembered)


def test_scan_takes_over_when_the_official_list_is_refused(tmp_path, monkeypatch, caplog):
    caplog.set_level("INFO", logger="TwitchDrops")
    fake = FakeTwitch(official="integrity")
    app = make_app(tmp_path, monkeypatch, fake)
    run(app)
    assert {c.id for c in app.inventory} == {"c-lol", "c-scan-only"}
    # both ways of asking for the list were tried once, then the scan ran
    assert fake.calls.count("ViewerDropsDashboard") == 1
    assert fake.calls.count("plain:currentUser") == 1
    assert "scan" in fake.calls
    line = discovery_line(caplog)
    assert "source=scan" in line and "official list unavailable" in line
    assert "failed integrity check" in line


def test_snapshot_when_everything_else_fails(tmp_path, monkeypatch, caplog):
    caplog.set_level("INFO", logger="TwitchDrops")
    # no official list, and the stream list comes back empty
    fake = FakeTwitch(official="integrity", scan_channels=False)
    app = make_app(tmp_path, monkeypatch, fake)
    run(app)
    assert {c.id for c in app.inventory} == {"c-lol", "c-scan-only"}
    assert "source=snapshot" in discovery_line(caplog)


def test_scan_only_setting_never_asks_for_the_official_list(tmp_path, monkeypatch, caplog):
    caplog.set_level("INFO", logger="TwitchDrops")
    fake = FakeTwitch(official="ok")
    app = make_app(tmp_path, monkeypatch, fake, source="scan")
    run(app)
    assert "ViewerDropsDashboard" not in fake.calls and "plain:currentUser" not in fake.calls
    assert "source=scan" in discovery_line(caplog)


def test_caches_and_reload(tmp_path, monkeypatch):
    fake = FakeTwitch(official="integrity")
    app = make_app(tmp_path, monkeypatch, fake)
    run(app)
    first = list(fake.calls)
    # a second fetch within 30 minutes reuses both the official answer and the scan
    run(app)
    second = fake.calls[len(first):]
    assert "ViewerDropsDashboard" not in second and "scan" not in second
    # the Reload button skips the caches
    app._force_discovery = True
    run(app)
    third = fake.calls[len(first) + len(second):]
    assert "ViewerDropsDashboard" in third and "scan" in third
    assert app._force_discovery is False
