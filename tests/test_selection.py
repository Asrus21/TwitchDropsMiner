"""
Campaigns were counted as mineable while the miner picked none (16.2.0): the count didn't
use the Priority mode. The count and the choice now use the same rule.
"""

from __future__ import annotations

from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

import pytest

import campaign_sources as cs
from constants import PriorityMode
from inventory import DropsCampaign
from twitch import Twitch


NOW = datetime.now(timezone.utc)
NEXT_HOUR = NOW + timedelta(hours=1)


def campaign(
    game: str, *, linked: bool = True, badge: bool = False, starts_in_days: float = -1,
    badges_emotes: bool = False,
):
    iso = lambda days: (NOW + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # the same settings object the miner's rule reads, as in the app
    settings = SimpleNamespace(
        priority=[], exclude=set(), enable_badges_emotes=badges_emotes, available_drops_check=True
    )
    app = SimpleNamespace(settings=settings, gui=SimpleNamespace(channels=None), _campaigns={})
    benefit = {"id": f"b-{game}", "name": "Item"}
    if badge:
        benefit["distributionType"] = "BADGE"
    data = Twitch._campaign_from_available(app, {
        "id": f"c-{game}", "name": game, "endAt": iso(5),
        "game": {"id": str(abs(hash(game)) % 10**6), "displayName": game},
        "timeBasedDrops": [{
            "id": f"d-{game}", "name": "Drop", "startAt": iso(starts_in_days), "endAt": iso(5),
            "requiredMinutesWatched": 60, "benefitEdges": [{"benefit": benefit}],
        }],
    }, {}, {})
    data["self"]["isAccountConnected"] = linked
    return DropsCampaign(app, data, {})


@pytest.mark.parametrize("kwargs, settings, expected", [
    ({}, {}, None),
    ({}, {"priority_only": True}, cs.BLOCK_NOT_IN_PRIORITY),
    ({}, {"priority_only": True, "priority": ["Game"]}, None),
    ({}, {"exclude": {"Game"}}, cs.BLOCK_EXCLUDED),
    ({"linked": False}, {}, cs.BLOCK_NOT_LINKED),
    ({"badge": True}, {}, cs.BLOCK_BADGES_OFF),
    ({"badge": True}, {"badges_emotes": True}, None),
    ({"starts_in_days": 2}, {}, cs.BLOCK_NOTHING_NOW),
])
def test_selection_blocker_with_real_campaigns(kwargs, settings, expected):
    """Campaigns converted from the channel data, through the same rule the miner uses."""
    rule = {"priority": [], "exclude": set(), "priority_only": False, "badges_emotes": False, **settings}
    built = campaign("Game", badges_emotes=rule["badges_emotes"], **kwargs)
    assert cs.selection_blocker(built, stamp=NEXT_HOUR, **rule) == expected


def test_nothing_picked_is_explained():
    printed: list[str] = []
    app = SimpleNamespace(
        settings=SimpleNamespace(priority_mode=PriorityMode.PRIORITY_ONLY),
        print=printed.append,
    )
    blockers = [cs.BLOCK_NOT_IN_PRIORITY] * 45 + [cs.BLOCK_NOT_LINKED] * 2
    Twitch._explain_nothing_picked(app, blockers, True, [])
    assert "45 not in the priority list" in printed[0]
    assert "2 need the game account linked" in printed[0]
    assert "list is empty" in printed[1]
    # with games in the list, the hint doesn't say it's empty
    printed.clear()
    Twitch._explain_nothing_picked(app, [cs.BLOCK_NOT_IN_PRIORITY], True, ["Rust"])
    assert "only the listed games" in printed[1]
