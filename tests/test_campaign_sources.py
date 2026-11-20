"""
The campaign rules (campaign_sources.py): reading the official list, ended campaigns,
reward campaigns, merging with the other sources, ordering and the snapshot.
"""

from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest

import campaign_sources as cs
from conftest import FIXTURES, REPO, load_fixture


NOW = datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)


def test_official_list_is_read_and_reward_campaigns_are_left_out():
    campaigns, rewards = cs.official_campaigns(load_fixture("viewer_drops_dashboard.json"))
    ids = [c["id"] for c in campaigns]
    assert ids == ["c-warzone", "c-smite", "c-albion", "c-lol", "c-old", "c-stale"]
    # both ways Twitch can send reward campaigns
    assert rewards == ["GTAVI Launch Celebration", "Reward in the wrong list"]


@pytest.mark.parametrize(
    "response, reason",
    [
        (load_fixture("integrity_failed.json"), "failed integrity check"),
        ({"data": {"currentUser": None}}, "not logged in"),
        ({"data": {"currentUser": {"dropCampaigns": None}}}, "no dropCampaigns"),
    ],
)
def test_official_list_unavailable(response, reason):
    with pytest.raises(cs.SourceUnavailable, match=reason):
        cs.official_campaigns(response)


def test_ended_campaigns_are_left_out():
    campaigns, _ = cs.official_campaigns(load_fixture("viewer_drops_dashboard.json"))
    kept, expired = cs.drop_expired({c["id"]: c for c in campaigns}, NOW)
    # c-old is marked EXPIRED; c-stale still says ACTIVE but its end date has passed
    assert expired == 2
    assert set(kept) == {"c-warzone", "c-smite", "c-albion", "c-lol"}
    # ending in one second is still on
    edge = {"status": "ACTIVE", "endAt": "2026-11-20T15:00:01Z"}
    assert not cs.is_expired(edge, NOW)
    assert cs.is_expired({**edge, "endAt": "2026-11-20T15:00:00Z"}, NOW)


def test_mining_order_ends_soonest_after_the_priority_list():
    def campaign(game: str, ends: str):
        return SimpleNamespace(game=SimpleNamespace(name=game), ends_at=cs.parse_timestamp(ends))

    lol = campaign("League of Legends", "2026-12-01T00:00:00Z")
    warzone = campaign("Call of Duty: Warzone", "2026-11-21T02:59:59Z")
    smite = campaign("SMITE 2", "2026-11-22T02:59:59Z")
    items = [lol, smite, warzone]
    assert cs.mining_order(items, [], ending_soonest=True) == [warzone, smite, lol]
    # the priority list always comes first
    assert cs.mining_order(items, ["League of Legends"], ending_soonest=True) == [lol, warzone, smite]
    # without ending_soonest the given order stays (after the priority list)
    assert cs.mining_order(items, [], ending_soonest=False) == items


def test_official_list_wins_over_the_scan():
    listed, _ = cs.official_campaigns(load_fixture("viewer_drops_dashboard.json"))
    channel = load_fixture("available_drops.json")["data"]["channel"]
    # the scan found c-lol (with drops) under another name, and c-scan-only
    scanned = {c["id"]: deepcopy(c) for c in channel["viewerDropCampaigns"]}
    scanned["c-lol"]["self"] = {"isAccountConnected": False}  # the scan's guess
    pending = cs.apply_official(scanned, listed)
    lol = scanned["c-lol"]
    assert lol["name"] == "League Drops"  # the official name
    assert lol["self"]["isAccountConnected"] is True  # the official link status
    assert lol["timeBasedDrops"][0]["id"] == "d-lol-1"  # the scan's drops stay
    assert "c-scan-only" in scanned  # nothing the scan found is lost
    # listed campaigns no other source had are pending (no drops data yet)
    assert set(pending) == {"c-warzone", "c-smite", "c-albion", "c-old", "c-stale"}


def test_report_line():
    listed, _ = cs.official_campaigns(load_fixture("viewer_drops_dashboard.json"))
    channel = load_fixture("available_drops.json")["data"]["channel"]
    campaigns = {c["id"]: deepcopy(c) for c in channel["viewerDropCampaigns"]}
    active = [c for c in listed if not cs.is_expired(c, NOW)]
    pending = cs.apply_official(campaigns, active)
    report = cs.DiscoveryReport(cs.SOURCE_OFFICIAL)
    report.expired = 2
    report.rewards = 1
    report.count(campaigns, pending)
    report.waiting = ["SMITE 2"]
    line = report.line()
    assert line.startswith("Campaign discovery: source=official, campaigns=5, mineable=2, ")
    assert "no live stream=1, expired ignored=2, need account link=1" in line
    assert "reward campaigns ignored=1" in line


def test_snapshot_skips_ended_games_and_gets_too_old(tmp_path):
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps({
        "captured_at": "2026-11-20T00:00:00Z",
        "max_age_days": 45,
        "games": [
            {"name": "Ended", "id": "1", "slug": "ended", "ends_at": "2026-11-20T10:00:00Z"},
            {"name": "Still on", "id": "2", "slug": "still-on", "ends_at": "2026-11-21T00:00:00Z"},
            {"name": "No end date", "id": "3", "slug": ""},
            {"id": "4"},  # no name: ignored
        ],
    }))
    games = cs.load_snapshot(path, NOW)
    assert [g["name"] for g in games] == ["Still on", "No end date"]
    assert cs.load_snapshot(path, datetime(2027, 1, 10, tzinfo=timezone.utc)) == []
    assert cs.load_snapshot(tmp_path / "missing.json", NOW) == []


def test_bundled_snapshot():
    path = REPO / "known_drops_games.json"
    games = cs.load_snapshot(path, NOW)
    assert len(games) == 79
    assert all(g["id"] and g["slug"] for g in games)
    # three of them end on 20/11 (Brasília) and seven on 21/11
    after_20 = cs.load_snapshot(path, datetime(2026, 11, 21, 12, tzinfo=timezone.utc))
    after_21 = cs.load_snapshot(path, datetime(2026, 11, 22, 12, tzinfo=timezone.utc))
    assert (len(after_20), len(after_21)) == (76, 69)
    assert "Call of Duty: Warzone" not in {g["name"] for g in after_20}


def test_fixtures_have_no_secrets():
    for path in FIXTURES.glob("*.json"):
        text = path.read_text(encoding="utf8").lower()
        for word in ("oauth", "authorization", "auth-token", "client-integrity", "cookie"):
            assert word not in text, f"{path.name} mentions {word}"
