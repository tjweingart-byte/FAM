"""Production 429s on /api/audio: one episode, tapped, refused (PROBLEMS.md 70).

Render logs showed the same question - *"which longevity interventions have
real evidence"* - asked over and over and every `GET /api/audio` answered
`429 Too Many Requests`. Reproduced against the running server before anything
was changed: **six taps on one question, one every four seconds, well clear of
the three-second pace.** The first five were served. The sixth and everything
after it were refused for the rest of the UTC day.

So the refusal was the **free tier's daily allowance**, not the rate limiter -
and the allowance had been spent five times over on a single episode, because
it counted *requests*. Tapping the episode that is playing is something the
interface invites (the player's row says "tap to generate new episode") and
switching voice deliberately re-requests the same script, so a listener reaches
five without ever hearing a second episode.

What these tests pin:

1. One episode is charged once per window, however many times it is asked for -
   and a different episode still costs a unit, so the allowance still bites.
2. An attempt that produced nothing is not charged. The empty-episode 502 was
   the one failure path with no refund on it at all, so a server whose voice
   had gone away spent a listener's whole day on silence and then answered 429.
3. A request that cannot spend a model call - an Explore replay, or an episode
   whose script is already cached - is not paced as a generation.
4. The pace still paces, the allowance still refuses, and the two say which of
   them did it: in an access log both are the same three digits.
"""
from __future__ import annotations

import dataclasses
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import entitlements  # noqa: E402
import quotas  # noqa: E402
from cache import MemoryScriptCache, cache_key  # noqa: E402
from script_generator import plan_episode  # noqa: E402

QUERY = "which longevity interventions have real evidence"


@pytest.fixture
def client(monkeypatch, tmp_path):
    """A server with empty limiter state and quota counters of its own."""
    monkeypatch.setattr(appmod, "QUOTAS", quotas.QuotaStore(str(tmp_path / "q.db")))
    appmod._read_hits.clear()
    appmod._gen_tokens.clear()
    with TestClient(appmod.app) as c:
        c.get("/api/auth/me")      # take the session cookie, as a browser does
        yield c


@pytest.fixture
def enforced(monkeypatch):
    """Limits on, and small enough to reach inside a test."""
    monkeypatch.setattr(quotas, "settings_enforcing", lambda: True)
    monkeypatch.setenv("FREE_EPISODES_PER_DAY", "3")
    monkeypatch.setenv("FREE_EXPLORE_PER_DAY", "3")
    entitlements.reload_tiers()
    yield
    for name in entitlements.LIMIT_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    entitlements.reload_tiers()


@pytest.fixture
def unpaced(monkeypatch):
    """Measure the allowance, not the pace.

    The two are separate limits with separate tests. In this environment there
    is no API key, so demo mode never writes to the cache and every repeat of a
    question looks uncached to the pace - which would refuse these requests for
    the other reason and hide what they are asserting.
    """
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)


def audio(c: TestClient, query: str = QUERY, **params):
    args = {"q": query, "minutes": 3, "fmt": "pcm"}
    args.update(params)
    return c.get("/api/audio", params=args)


def used(c: TestClient, resource: str = "episode") -> int:
    return c.get("/api/entitlements").json()["usage"][resource]["used"]


# --- 1. the allowance counts episodes, not requests ------------------------

def test_the_same_episode_is_charged_once(client, enforced, unpaced):
    """The production fault, reproduced and then pinned.

    Ten taps on one question. Under the old rule the fourth was refused and
    every one after it, for the rest of the day, having heard one episode.
    """
    codes = [audio(client).status_code for _ in range(10)]
    assert 429 not in codes, (
        f"tapping one episode repeatedly spent the whole allowance: {codes}")
    assert used(client) == 1, (
        f"one episode was charged {used(client)} times")


def test_a_different_episode_still_costs_one(client, enforced, unpaced):
    """The allowance has to keep biting, or this is just a higher limit with
    extra steps. Three is the ceiling set above."""
    for n in range(3):
        assert audio(client, f"a distinct question number {n}").status_code == 200
    refused = audio(client, "a fourth and different question")
    assert refused.status_code == 429
    assert refused.headers.get("X-FAM-Refused-By") == "quota"
    assert "X-FAM-Quota" in refused.headers, "the refusal did not say what the limit was"


def test_switching_voice_does_not_cost_a_second_episode(client, enforced, unpaced):
    """Voice is deliberately not part of the cache key, which is what makes a
    voice switch free - no model call, the same script. Charging for it made
    the cheapest feature in the app cost a fifth of a free listener's day."""
    assert audio(client).status_code == 200
    assert audio(client, voice="debug:tone").status_code == 200
    assert used(client) == 1, "switching voice was charged as a second episode"


def test_a_follow_up_is_a_different_episode(client, enforced, unpaced):
    """The key must not be so coarse that it makes real episodes free. Go
    Deeper carries the parent topic, and that is a different episode from the
    same words asked cold."""
    assert audio(client).status_code == 200
    assert audio(client, context="a topic they just heard").status_code == 200
    assert used(client) == 2, "a follow-up rode on the episode it followed"


def test_a_longer_episode_is_a_different_episode(client, enforced, unpaced):
    """A three-minute script is written differently from a six-minute one - it
    is not the same one cut short, which is why duration is in the cache key."""
    assert audio(client, minutes=3).status_code == 200
    assert audio(client, minutes=6).status_code == 200
    assert used(client) == 2, "a different length rode on the first episode"


def test_an_attached_episode_is_charged_every_time(client, enforced, monkeypatch):
    """An episode built on somebody's own document is never cached and never
    shared, so it has no identity to ride on and every one is a real spend."""
    plan = plan_episode(QUERY, 3, "", None, False, ("a document",))
    assert appmod._episode_key(plan) == ""


def test_the_key_needs_no_model_call(client, monkeypatch):
    """`CACHE_SEMANTIC_KEY` computes the key with a Claude call. Making one to
    decide what to charge would put a round trip in front of the first word,
    which is the one cost this product refuses - so the episode is simply
    counted as new."""
    monkeypatch.setattr(appmod, "settings",
                        dataclasses.replace(appmod.settings, cache_semantic_key=True))
    assert appmod._episode_key(plan_episode(QUERY, 3)) == ""


# --- 2. nothing heard, nothing charged -------------------------------------

def test_an_episode_that_came_back_empty_is_not_charged(client, enforced, unpaced, monkeypatch):
    """The path that had no refund on it at all.

    A server that cannot speak - a remote voice asleep, a script that came back
    empty - answered 502 and kept the unit. Five of those and the listener was
    locked out until midnight UTC having heard nothing whatsoever.
    """
    async def nothing(*_a, **_k):
        return
        yield b""            # pragma: no cover - an empty async generator

    monkeypatch.setattr(appmod.PodcastPipeline, "stream_pcm", nothing)
    assert audio(client).status_code == 502
    assert used(client) == 0, "a silent 502 was charged as an episode"


def test_a_failure_is_not_remembered_as_paid_for(client, enforced, unpaced, monkeypatch):
    """A refunded episode must be forgotten as well as refunded, or the retry
    that finally works would be free - and failing would be a way to earn
    allowance."""
    def no_voice(*_a, **_k):
        raise appmod.TTSUnavailable("no engine here")

    working = appmod._make_pipeline
    monkeypatch.setattr(appmod, "_make_pipeline", no_voice)
    assert audio(client).status_code == 503
    assert used(client) == 0

    # Put back *only* the voice. `monkeypatch.undo()` would also undo the
    # `enforced` fixture - the two share one monkeypatch instance - and this
    # test read as passing for a while only because enforcement used to be the
    # default. It is not any more (the tier system ships switched off), so
    # undoing everything here silently measured a server with no limits on.
    monkeypatch.setattr(appmod, "_make_pipeline", working)
    assert audio(client).status_code == 200
    assert used(client) == 1, "the retry that worked was never charged"


def test_a_repeat_gives_back_nothing_when_it_fails(client, enforced, unpaced, monkeypatch):
    """The other direction, which is the one that would have been exploitable:
    a repeat rides on the first reservation, so a repeat that fails must not
    refund a unit nobody ever took."""
    assert audio(client).status_code == 200
    assert used(client) == 1

    def no_voice(*_a, **_k):
        raise appmod.TTSUnavailable("no engine here")

    monkeypatch.setattr(appmod, "_make_pipeline", no_voice)
    for _ in range(3):
        assert audio(client).status_code == 503
    assert used(client) == 1, "failing on a replay handed back allowance"


# --- 3. what cannot spend is not paced -------------------------------------

def test_one_episode_click_is_never_paced(client):
    """The interface's opening reads and then one tap. Nothing in that
    sequence may answer 429."""
    for path in ("/api/health", "/api/auth/me", "/api/topics", "/api/myfam",
                 "/api/explore", "/api/voices"):
        assert client.get(path).status_code == 200, f"{path} was throttled"
    assert audio(client).status_code != 429, "one tap on one episode was refused"


def test_replaying_a_written_episode_is_not_paced(client, monkeypatch):
    """The script is in the shared cache, so the pipeline replays it and no
    model call is possible. Pacing that only stops a listener pressing play."""
    monkeypatch.setattr(appmod, "settings",
                        dataclasses.replace(appmod.settings, rate_limit_burst=1))
    cache = MemoryScriptCache()
    plan = plan_episode(QUERY, 3)
    cache.put(cache_key(plan.query, plan.minutes, None, plan.context, plan.search),
              ["A sentence that was already written.", "And a second one."],
              3600, plan.query, "", 3)
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", cache)

    codes = [audio(client).status_code for _ in range(6)]
    assert 429 not in codes, f"a replay was paced as a generation: {codes}"


def test_an_unwritten_episode_is_still_paced(client, monkeypatch):
    """The cache probe must not become a way past the pace. Nothing is cached
    here, so every one of these could spend a model call."""
    monkeypatch.setattr(appmod, "settings",
                        dataclasses.replace(appmod.settings, rate_limit_burst=2))
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", MemoryScriptCache())
    codes = [audio(client, f"nobody has ever asked this {n}").status_code
             for n in range(5)]
    assert 429 in codes[2:], f"uncached generations were never paced: {codes}"
    assert codes[:2] == [200, 200], "the allowed burst was refused"


def test_explore_replays_are_not_paced(client):
    """Swiping the feed generates nothing. Pinned again here because the
    pacing decision moved."""
    codes = [audio(client, "nothing cached here at all", cached_only=1).status_code
             for _ in range(4)]
    assert set(codes) == {409}, f"a replay-only swipe was throttled: {codes}"


# --- 4. a refusal says which refusal it is ---------------------------------

def test_the_two_refusals_are_told_apart(client, enforced, monkeypatch):
    """`429` in an access log is the same three digits whether the pace or the
    allowance said no, and the two have opposite fixes: wait three seconds, or
    wait until tomorrow. A day went into looking at the wrong one."""
    monkeypatch.setattr(appmod, "settings",
                        dataclasses.replace(appmod.settings, rate_limit_burst=1))
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", MemoryScriptCache())
    assert audio(client, "a first question").status_code == 200
    paced = audio(client, "a second question")
    assert paced.status_code == 429
    assert paced.headers.get("X-FAM-Refused-By") == "pace"
    assert int(paced.headers["Retry-After"]) >= 1

    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    for n in range(4):
        audio(client, f"question {n} of several")
    out = audio(client, "the one over the line")
    assert out.status_code == 429
    assert out.headers.get("X-FAM-Refused-By") == "quota"
    assert "today" in out.json()["error"], "the refusal did not say when it comes back"
