"""The tier system, built and switched off - and the screen for the day it is on.

Two decisions live here, and they are separate on purpose.

**The tiers are not activated.** Every part of the mechanism stays - tiers,
limits, counters, reservations, refunds, the refusal and the screen it raises -
and `ENFORCE_QUOTAS` turns the whole of it on in one place. What is deliberately
not happening yet is refusing a listener, because nothing sells them a way past
a refusal: there is no checkout, so an enforced free tier is a wall with no door.
The risk that buys, stated rather than discovered: spend per listener is
*visible* (`metering.py`) and not *capped*. Generation is still paced.

**The refusal, when it is switched on, names what the listener was doing.**
"That is all 5 of your episodes" sends someone looking for episodes they
apparently spent; "your daily limit for searches" is about the thing they
pressed. The word comes from `entitlements.service_label` and travels on the
verdict, so the web app and the iOS client cannot word it differently, and the
numbers travel in the response *body* as well as the header - a header is the
one part of a response a client routinely cannot reach.
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import entitlements  # noqa: E402
import quotas  # noqa: E402


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "QUOTAS", quotas.QuotaStore(str(tmp_path / "q.db")))
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    appmod._read_hits.clear()
    with TestClient(appmod.app) as c:
        c.get("/api/auth/me")
        yield c


@pytest.fixture
def enforced(monkeypatch):
    """The day somebody turns it on."""
    monkeypatch.setattr(quotas, "settings_enforcing", lambda: True)
    monkeypatch.setenv("FREE_EPISODES_PER_DAY", "1")
    monkeypatch.setenv("FREE_EXPLORE_PER_DAY", "1")
    entitlements.reload_tiers()
    yield
    for name in entitlements.LIMIT_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    entitlements.reload_tiers()


def audio(c: TestClient, query: str, **params):
    args = {"q": query, "minutes": 3, "fmt": "pcm"}
    args.update(params)
    return c.get("/api/audio", params=args)


# --- 1. built, and not switched on -----------------------------------------

def test_quotas_are_off_by_default(monkeypatch):
    """The temporary half of this change, asserted where it is decided.

    Read from a fresh `config` with the environment cleared, because the whole
    point is the *default* - a deploy that sets nothing must not enforce.
    """
    import importlib

    import config

    monkeypatch.delenv("ENFORCE_QUOTAS", raising=False)
    monkeypatch.setenv("FAM_IGNORE_DOTENV", "1")
    importlib.reload(config)
    try:
        assert config.settings.enforce_quotas is False, (
            "the tier system is meant to be built and not activated")
    finally:
        importlib.reload(config)


def test_the_example_config_agrees(monkeypatch):
    """§54: a setting is settled only where it is copied. Following the
    documented setup must not switch limits on."""
    import pathlib

    text = (pathlib.Path(__file__).resolve().parent.parent / ".env.example").read_text()
    assert "ENFORCE_QUOTAS=0" in text
    assert "ENFORCE_QUOTAS=1" not in text


def test_nothing_is_refused_while_it_is_off(client):
    """Six episodes against a free tier that allows five. With enforcement off
    the listener hears all six, and that is the point of switching it off."""
    codes = [audio(client, f"a question number {n}").status_code for n in range(6)]
    assert 429 not in codes, f"a listener was refused with the tiers switched off: {codes}"


def test_the_machinery_is_still_there(client):
    """Switched off is not removed. The tiers, the limits and the counters all
    still answer, because the day this is switched on is meant to be one
    setting and not a rebuild."""
    plans = client.get("/api/plans").json()
    assert [t["name"] for t in plans["tiers"]] == list(entitlements.TIERS)
    assert plans["current"] == "free"
    assert any(t["limits"] for t in plans["tiers"]), "the tiers lost their limits"

    usage = client.get("/api/entitlements").json()["usage"]
    assert set(usage) == set(entitlements.RESOURCES)


def test_the_server_says_whether_limits_are_live(client):
    """A tier system that is not enforcing looks exactly like one that is,
    until somebody reaches a limit. "Are limits on?" is the question a beta
    asks most, so the server answers it rather than being inferred from."""
    tiers = client.get("/api/health").json()["tiers"]
    assert tiers["enforced"] is False
    assert tiers["source"] in ("env", "default")
    assert tiers["tiers"] == list(entitlements.TIERS)


def test_switching_it_on_is_one_setting(client, enforced):
    """The other half of "built, not activated": one environment variable, and
    the whole thing bites. Nothing else in this test changed."""
    assert audio(client, "the first question").status_code == 200
    assert audio(client, "the second question").status_code == 429


# --- 2. the refusal names what they were doing -----------------------------

@pytest.mark.parametrize("surface, params, expected", [
    ("search", {}, "searches"),
    ("myfam", {"topic_id": "chip-supply"}, "episodes"),
    ("godeeper", {"context": "a topic they just heard"}, "follow-ups"),
])
def test_the_refusal_is_about_the_thing_they_pressed(client, enforced, surface,
                                                     params, expected):
    """One allowance, three ways of spending it, three different sentences."""
    assert audio(client, "the one they get", **params).status_code == 200
    refused = audio(client, "the one over the line", **params)
    assert refused.status_code == 429
    body = refused.json()
    assert body["quota"]["service"] == expected, f"{surface} was named wrong"
    assert body["quota"]["title"] == (
        f"You've reached your daily limit for {expected}"), body["quota"]["title"]
    assert expected in body["error"], "the sentence did not name the service either"


def test_explore_is_named_as_explore(client, enforced, monkeypatch):
    """Explore has its own, looser allowance, and it is not "episodes"."""
    from cache import MemoryScriptCache, cache_key
    from script_generator import plan_episode

    # A card that really replays, so the first swipe spends its unit. A miss
    # would 409 and be refunded - correctly - and never reach the ceiling.
    cache = MemoryScriptCache()
    plan = plan_episode("an episode somebody already generated", 3)
    cache.put(cache_key(plan.query, plan.minutes, None, plan.context, plan.search),
              ["A sentence that was already written."], 3600, plan.query, "", 3)
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", cache)

    assert audio(client, plan.query, cached_only=1).status_code == 200
    refused = audio(client, "a second card from the feed", cached_only=1)
    assert refused.status_code == 429
    assert refused.json()["quota"]["service"] == "Explore episodes"
    assert refused.json()["quota"]["title"] == (
        "You've reached your daily limit for Explore episodes")


def test_a_weekly_limit_says_weekly(monkeypatch, tmp_path):
    """The adjective comes from the window, so a Plus listener is not told
    their weekly allowance is daily."""
    monkeypatch.setattr(quotas, "settings_enforcing", lambda: True)
    monkeypatch.setenv("PLUS_EPISODES_PER_WEEK", "1")
    entitlements.reload_tiers()
    try:
        store = quotas.QuotaStore(str(tmp_path / "q.db"))
        store.reserve("u", "plus", "episode", service="searches")
        with pytest.raises(quotas.QuotaExceeded) as caught:
            store.reserve("u", "plus", "episode", service="searches")
        assert caught.value.verdict.title == (
            "You've reached your weekly limit for searches")
        assert "this week" in caught.value.verdict.message
    finally:
        monkeypatch.delenv("PLUS_EPISODES_PER_WEEK", raising=False)
        entitlements.reload_tiers()


def test_a_verdict_that_allowed_has_no_headline():
    """There is no "you've reached your limit" for an allowance that is fine.
    A screen built from a granted verdict would announce a limit nobody hit."""
    granted = quotas.Verdict(True, "episode", "free", "day", 1, 5, 0.0,
                             service="searches")
    assert granted.title == ""


def test_the_noun_is_never_blank():
    """A refusal reading "your daily limit for " is worse than one using the
    ledger's word, so the label falls back rather than going empty."""
    assert entitlements.service_label("episode", "a surface nobody defined") == "episodes"
    assert entitlements.service_label("nonsense", "") == "episodes"
    refused = quotas.Verdict(False, "explore", "free", "day", 3, 3, 0.0)
    assert refused.title == "You've reached your daily limit for Explore episodes"


# --- 3. the numbers reach the screen ---------------------------------------

def test_the_verdict_travels_in_the_body_not_only_the_header(client, enforced):
    """The limit screen needs the figures, and a header is the one part of a
    response a client routinely cannot reach: `fetch` hides it cross-origin
    without `expose_headers`, and every wrapper that turns a failed response
    into an exception keeps the body and drops the rest."""
    assert audio(client, "the one they get").status_code == 200
    refused = audio(client, "the one over the line")

    assert "X-FAM-Quota" in refused.headers, "the header form was dropped"
    body = refused.json()
    assert body["refused_by"] == "quota"
    quota = body["quota"]
    for field in ("service", "title", "message", "used", "limit", "resets_at",
                  "window", "unlimited", "remaining"):
        assert field in quota, f"the screen cannot draw itself without {field}"
    assert quota["used"] == quota["limit"] == 1
    assert quota["remaining"] == 0
    assert quota["resets_at"] > 0, "nothing to tell them about when it comes back"


def test_a_pacing_refusal_carries_no_quota(client, monkeypatch):
    """The two refusals stay distinguishable. A pacing 429 must not raise the
    limit screen - "wait three seconds" and "wait until tomorrow" are opposite
    remedies, and the screen is only right for one of them."""
    import dataclasses

    monkeypatch.undo()          # put the real `_rate_limit` back
    monkeypatch.setattr(appmod, "QUOTAS", quotas.QuotaStore(":memory:"))
    # `rate_limit_seconds` pinned long: the bucket refills one start per
    # RATE_LIMIT_SECONDS (3s), and a slow CI runner once took longer than
    # that to stream the first episode, so the second was let through (§138).
    monkeypatch.setattr(appmod, "settings",
                        dataclasses.replace(appmod.settings, rate_limit_burst=1,
                                            rate_limit_seconds=3600))
    appmod._gen_tokens.clear()
    c = TestClient(appmod.app)
    c.get("/api/auth/me")
    audio(c, "the first question")
    refused = audio(c, "the second question")
    assert refused.status_code == 429
    assert refused.json().get("refused_by") == "pace"
    assert "quota" not in refused.json(), "a pacing refusal would raise the limit screen"
