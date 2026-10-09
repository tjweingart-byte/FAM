"""The load test (`tools/load_test.py`) aims at real routes and refuses to spend.

Locust is not installed here; the module's helpers import without it, which
is itself pinned below - importing Locust patches the standard library.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as appmod  # noqa: E402

# Loaded by path, not by putting tools/ on sys.path: tools/ holds scripts
# named like the modules they drive (tools/trending_bank.py), and a path entry
# in front would shadow the real ones for every test after this.
_spec = importlib.util.spec_from_file_location(
    "load_test", os.path.join(ROOT, "tools", "load_test.py"))
LT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(LT)


def test_every_path_it_requests_is_a_route():
    routes = {getattr(route, "path", "") for route in appmod.app.routes}
    missing = [p for p in LT.PATHS if p not in routes]
    assert not missing, "the load test asks for routes that do not exist: %s" % missing


def test_only_zero_spend_servers_are_load_tested():
    staging = {"environment": {"name": "staging", "zero_spend": True}}
    production = {"environment": {"name": "production", "zero_spend": False}}
    assert LT.spend_refusal(staging, allow_spend=False) == ""
    assert "not zero-spend" in LT.spend_refusal(production, allow_spend=False)
    # A server too old to say is treated as one that spends.
    assert LT.spend_refusal({}, allow_spend=False)
    # Unless somebody decided to pay for it.
    assert LT.spend_refusal(production, allow_spend=True) == ""


def test_plays_come_from_explore_cards():
    payload = {"episodes": [
        {"query": "how does a heat pump work", "minutes": 2},
        {"query": "", "minutes": 2},
        {"query": "no minutes"},
    ]}
    assert LT.playable(payload) == [("how does a heat pump work", 2)]
    assert LT.playable({}) == []


def test_a_kept_play_never_generates():
    url = urlparse(LT.audio_path("why is the sky blue", 2, cached_only=True,
                                 surface="explore"))
    params = parse_qs(url.query)
    assert url.path == "/api/audio"
    assert params["cached_only"] == ["1"]
    assert params["fmt"] == ["pcm"]
    fresh = parse_qs(urlparse(LT.audio_path("q", 2, cached_only=False,
                                            surface="search")).query)
    assert "cached_only" not in fresh


def test_importing_it_without_locust_defines_no_users():
    if LT.HttpUser is None:
        assert not hasattr(LT, "Listener")


# --- Apple's universal-links file (APP_STORE.md) -----------------------------
# Kept beside the load test because both are the App Store groundwork; the
# file is only answered once the developer account's ids exist.

def _aasa(team, bundle):
    from fastapi.testclient import TestClient
    settings = appmod.settings
    before = (settings.apple_team_id, settings.ios_bundle_id)
    object.__setattr__(settings, "apple_team_id", team)
    object.__setattr__(settings, "ios_bundle_id", bundle)
    try:
        return TestClient(appmod.app).get("/.well-known/apple-app-site-association")
    finally:
        object.__setattr__(settings, "apple_team_id", before[0])
        object.__setattr__(settings, "ios_bundle_id", before[1])


def test_no_app_configured_is_a_404_not_a_promise():
    assert _aasa("", "").status_code == 404
    assert _aasa("ABCDE12345", "").status_code == 404


def test_shared_links_open_the_app_once_it_exists():
    resp = _aasa("ABCDE12345", "net.familiarize.fam")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    detail = body["applinks"]["details"][0]
    assert detail["appIDs"] == ["ABCDE12345.net.familiarize.fam"]
    assert {c["/"] for c in detail["components"]} == {"/s/*", "/m/*"}
    assert body["webcredentials"]["apps"] == ["ABCDE12345.net.familiarize.fam"]
