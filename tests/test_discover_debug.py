"""
The --discover-debug mode: one log line per operation and client, never a token or header.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

from twitch import _AuthState
from constants import ClientType
import discover_debug
from discover_debug import run_discover_debug
from conftest import load_fixture


SECRET_TOKEN = "zzsecrettokenzz0123456789abcdef"
SECRET_SESSION = "zzsessionzz42"
DASHBOARD = load_fixture("viewer_drops_dashboard.json")
INTEGRITY = load_fixture("integrity_failed.json")


def fake_twitch(sent: list[dict[str, Any]], printed: list[str]):
    app = SimpleNamespace()
    app.client_type_name = "SMARTBOX"
    app._client_type = ClientType.SMARTBOX
    app._qgl_limiter = asyncio.Semaphore(5)
    auth = object.__new__(_AuthState)
    auth._twitch = app
    auth.access_token = SECRET_TOKEN
    auth.session_id = SECRET_SESSION
    auth.device_id = "zzdevicezz"
    auth.user_id = 1

    async def get_auth():
        return auth

    def answer(headers: dict[str, str], body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        client = headers["Client-Id"]
        if client == ClientType.MOBILE_WEB.CLIENT_ID:
            return 400, {"error": "Bad Request", "status": 400,
                         "message": 'The "Client-ID" header is invalid.'}
        logged_in = "Authorization" in headers
        name = body.get("operationName", "plain")
        if name in ("ViewerDropsDashboard", "plain") and "streams" not in body.get("query", ""):
            if not logged_in:
                return 200, {"data": {"currentUser": None}}
            if client == ClientType.WEB.CLIENT_ID:
                return 200, deepcopy(DASHBOARD)
            return 200, deepcopy(INTEGRITY)
        if name == "plain":  # the live channel lookup
            return 200, {"data": {"streams": {"edges": [{"node": {"broadcaster": {"id": "77"}}}]}}}
        if name == "Inventory":
            return 200, {"data": {"currentUser": {"inventory": {
                "dropCampaignsInProgress": [{"id": "c-1"}], "gameEventDrops": []}}}}
        if name == "DropCampaignDetails":
            return 200, deepcopy(INTEGRITY)
        if name == "DropsHighlightService_AvailableDrops":
            return 200, {"data": {"channel": {"id": "77", "viewerDropCampaigns": [{"id": "x"}]}}}
        raise AssertionError(name)

    @asynccontextmanager
    async def request(method, url, *, json, headers):
        sent.append({"headers": dict(headers), "body": json})
        status, data = answer(headers, json)

        async def body(**kwargs):
            return data

        yield SimpleNamespace(status=status, json=body)

    async def gql_request(op):
        headers = auth.headers(gql=True)
        return answer(headers, op)[1]

    app.get_auth = get_auth
    app.request = request
    app.gql_request = gql_request
    app.print = printed.append
    return app


def test_one_line_per_operation_and_client_without_secrets(caplog, tmp_path, monkeypatch):
    caplog.set_level("INFO", logger="TwitchDrops")
    results = tmp_path / "discover_debug.txt"
    monkeypatch.setattr(discover_debug, "RESULTS_PATH", results)
    sent: list[dict[str, Any]] = []
    printed: list[str] = []
    asyncio.run(run_discover_debug(fake_twitch(sent, printed)))
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Discover debug: op=")]
    # 6 operations x (4 clients logged in + 1 no-login baseline)
    assert len(lines) == 30
    web = [line for line in lines if "op=ViewerDropsDashboard " in line and "client=WEB" in line][0]
    assert "http=200 list=yes count=7" in web
    smartbox = [line for line in lines if "op=ViewerDropsDashboard " in line and "client=SMARTBOX login=yes" in line][0]
    assert "list=no" in smartbox and "errors=failed integrity check" in smartbox
    mobile = [line for line in lines if "client=MOBILE_WEB" in line][0]
    assert "http=400" in mobile and 'The "Client-ID" header is invalid.' in mobile
    baseline = [line for line in lines if "login=no" in line]
    assert len(baseline) == 6 and all("client=SMARTBOX" in line for line in baseline)
    # the requests did carry the session, the log never does
    assert any(f"OAuth {SECRET_TOKEN}" == r["headers"].get("Authorization") for r in sent)
    # every result also shows in the main window...
    shown = [m for m in printed if m.startswith("Discover debug: op=")]
    assert shown == [f"Discover debug: {line.split('Discover debug: ', 1)[1]}" for line in lines]
    # ...and in a file of its own, already written and closed
    saved = results.read_text(encoding="utf8").splitlines()
    assert saved[0].startswith("Twitch Drops Miner ") and "discover debug" in saved[0]
    assert len([row for row in saved if row.startswith("op=")]) == 30
    assert str(results) in printed[-1] and "30 result(s)" in printed[-1]
    everything = "\n".join(
        [r.getMessage() for r in caplog.records] + printed + saved
    )
    for secret in (SECRET_TOKEN, SECRET_SESSION, "zzdevicezz", "Authorization", "OAuth "):
        assert secret not in everything
