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


def test_the_app_it_opens_is_still_in_static():
    for path in LT.ASSETS:
        name = "index.html" if path == "/" else path.lstrip("/")
        assert os.path.isfile(os.path.join(ROOT, "static", name)), path


def test_it_polls_as_often_as_the_app_does():
    """The poll is most of the load; a test polling slower than the app
    measures a quieter server than the one people use (settings-copied)."""
    import re
    page = open(os.path.join(ROOT, "static", "index.html")).read()
    found = re.search(r"var NOTIF_POLL_MS = (\d+);", page)
    assert found, "NOTIF_POLL_MS is no longer in static/index.html"
    assert LT.NOTIF_POLL_SECONDS * 1000 == int(found.group(1))


def test_every_shape_reaches_full_load_and_is_judged_there():
    for shape in ("smoke", "ramp", "spike", "soak", "breakpoint"):
        steps = LT.shape_steps(shape, 1000)
        judged = [users for _s, users, _r, gate in steps if gate]
        assert judged, shape
        if shape != "smoke":
            assert max(users for _s, users, _r, _g in steps) >= 1000, shape
            assert judged[0] >= 1000, "%s is judged below full load" % shape
    ramp = LT.shape_steps("ramp", 1000)
    assert [users for _s, users, _r, _g in ramp] == [100, 250, 500, 750, 1000]
    assert LT.shape_steps("ramp", 1000, step_seconds=60)[0][0] == 60
    try:
        LT.shape_steps("gentle", 1000)
    except ValueError:
        pass
    else:
        raise AssertionError("an unknown shape ran as something else")


def test_a_listener_listens_before_tapping_again():
    assert LT.listening_seconds(2, 1.0, 1.0) == 120
    assert LT.listening_seconds(2, 1.0, 0.0) == 36      # a third, at least
    assert LT.listening_seconds(2, 0.0, 1.0) == 0       # a stress run
    assert LT.listening_seconds(0, 1.0, 1.0) == 0


def _row(requests=100, failures=0, p95=10, p99=20):
    return {"requests": requests, "failures": failures, "p95": p95, "p99": p99}


def test_a_run_within_budget_passes():
    rows = {"audio: first byte [kept]": _row(p95=400, p99=900),
            "/api/myfam": _row(p95=120), "/api/notifications": _row(p95=20),
            "page: app shell": _row(p95=900, p99=1500),
            "audio: whole [kept]": _row(p95=60000, p99=90000)}
    assert LT.verdict(rows) == []


def test_errors_slow_audio_and_no_audio_each_fail():
    errors = {"audio: first byte [kept]": _row(), "/api/myfam": _row(failures=5)}
    assert "error rate" in LT.verdict(errors)[0]
    slow = {"audio: first byte [kept]": _row(p95=1500, p99=1800)}
    assert LT.verdict(slow) == ["audio: first byte [kept]: p95 1500 ms (budget 1000 ms)"]
    silent = {"/api/myfam": _row()}
    assert "no audio was played" in LT.verdict(silent)[0]
    assert LT.verdict({}) == ["no requests were made"]


def test_slower_than_the_baseline_fails_beyond_the_slack():
    quiet = {"audio: first byte [kept]": _row(), "/api/myfam": _row(p95=40)}
    busy = {"audio: first byte [kept]": _row(), "/api/myfam": _row(p95=200)}
    assert any("slower" in p for p in LT.verdict(busy, quiet))
    # 4 ms becoming 9 ms is inside the slack, not a regression.
    tiny = {"audio: first byte [kept]": _row(p95=9), "/api/myfam": _row(p95=40)}
    assert LT.verdict(tiny, {"audio: first byte [kept]": _row(p95=4)}) == []


def test_a_locust_csv_reads_back_as_rows(tmp_path):
    csv_file = tmp_path / "run_stats.csv"
    csv_file.write_text(
        "Type,Name,Request Count,Failure Count,Median Response Time,"
        "Average Response Time,Min Response Time,Max Response Time,"
        "Average Content Size,Requests/s,Failures/s,50%,66%,75%,80%,90%,95%,"
        "98%,99%,99.9%,99.99%,100%\n"
        "GET,/api/myfam,30,1,29,29.1,21,39,100,0.25,0.0,29,32,33,34,37,38,39,39,39,39,39\n"
        ",Aggregated,30,1,29,29.1,21,39,100,0.25,0.0,29,32,33,34,37,38,39,39,39,39,39\n")
    assert LT.rows_from_csv(str(csv_file)) == {
        "/api/myfam": {"requests": 30, "failures": 1, "p95": 38.0, "p99": 39.0}}


def test_the_step_table_draws_every_step():
    table = LT.step_table([
        {"users": 100, "rps": 40.2, "fail_ratio": 0.0, "p95_all": 30,
         "p95_audio": 20, "p95_page": 110, "p95_notifications": None}])
    assert "| 100 | 40 | 0.00% | 30 ms | 20 ms | 110 ms | - |" in table


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
