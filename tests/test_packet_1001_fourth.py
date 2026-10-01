"""The 10.1 implementations packet, the fourth set (PROBLEMS.md §186): the
sign-up card, the app's own address, Go Deeper over a playing episode, and
the listener's clock."""

from __future__ import annotations

import asyncio
import pathlib
import re
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import app as appmod  # noqa: E402
import listener_clock  # noqa: E402
import research  # noqa: E402
import script_generator  # noqa: E402
import topics  # noqa: E402
from config import settings  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text()
ENV_EXAMPLE = (ROOT / ".env.example").read_text()


def _fn(name):
    return INDEX.split("function " + name + "(", 1)[1].split("\n  }\n", 1)[0]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    with TestClient(appmod.app) as c:
        yield c


# --- 1. The sign-up screen shows myFAM's card ------------------------------


def test_the_sign_up_screen_has_no_lede_under_its_heading():
    welcome = INDEX[INDEX.index('id="screen-welcome"'):INDEX.index('id="screen-auth"')]
    assert "Listen to anything you want to know about" in welcome
    assert "entry-lede" not in welcome
    assert "Type any question and hear a short episode" not in welcome


def test_welcome_samples_carry_the_whole_tile(client, monkeypatch):
    tile = topics.Topic(id="t0", title="The Legs Behind a Faster Forehand",
                        subtitle="Why the swing starts at the ground.",
                        query="how do tennis players hit harder", tags=("sports",),
                        icon="sports")
    monkeypatch.setattr(topics, "rank_most_played", lambda *a, **k: [tile])
    monkeypatch.setattr(appmod, "_audio_is_kept", lambda q, m: True)
    got = client.get("/api/welcome").json()["episodes"][0]
    assert got["title"] == "The Legs Behind a Faster Forehand"
    assert got["subtitle"] == "Why the swing starts at the ground."
    assert "thumb" in got and "icon" in got
    assert got["minutes"] == appmod.BROWSE_MINUTES


def test_the_welcome_card_is_drawn_by_the_rails_own_helpers():
    draw = _fn("drawWelcomeSample")
    for helper in ("seedThumbOpen(ep)", 'seedHook("most_played", ep)',
                   'seedTagText("most_played", ep)', "seed-card-title"):
        assert helper in draw, helper
    # The whole title: the rail's two-line clamp is lifted on this card.
    assert re.search(r"\.ws-card \.seed-card-title\{[^}]*-webkit-line-clamp:unset", INDEX)


# --- 2. Invites name familiarize.net ---------------------------------------


def test_the_apps_home_is_familiarize_net_and_copied_where_it_is_used():
    assert settings.app_home_url == "https://familiarize.net"
    assert "APP_HOME_URL=https://familiarize.net" in ENV_EXAMPLE
    assert 'var FAM_HOME = "' + settings.app_home_url + '";' in INDEX


def test_an_invite_never_names_the_host_the_page_is_open_on():
    invite = _fn("inviteFriends")
    assert "location.origin" not in invite
    assert "AUTH.home" in invite and "FAM_HOME" in invite


def test_auth_me_says_where_the_app_lives(client):
    assert client.get("/api/auth/me").json()["home"] == settings.app_home_url


def test_a_referral_link_is_never_relative(monkeypatch):
    monkeypatch.setattr(appmod, "_public_base", lambda request=None: "")
    link = appmod._referral_link(None, "abc")
    assert link == settings.app_home_url + "/waitlist?referralCode=abc"


# --- 4. Go Deeper leaves the episode playing -------------------------------


def test_opening_go_deeper_does_not_stop_the_episode():
    assert "stopSpeech()" not in _fn("openGoDeeper")
    reel = _fn("openGoDeeperOnReel")
    assert "stopSpeech()" not in reel and "stopReelProgress()" not in reel


def test_an_episode_ending_under_go_deeper_starts_nothing_over_it():
    # The player: no album advance, no countdown while the sheet is up.
    # §190 put the queue between the guard and `onEnd`; the guard is first.
    end = INDEX[INDEX.index("if(goDeeperIsOpen()) return;\n        // Something queued"):]
    assert end.index("if(goDeeperIsOpen()) return;") < end.index("playQueueNext()")
    assert end.index("if(goDeeperIsOpen()) return;") < end.index("maybeOfferNextUp(")
    assert "goDeeperIsOpen()" in _fn("maybeOfferNextUp")
    # Explore: the reel does not deal the next card under it.
    reel = INDEX[INDEX.index("function playReel("):INDEX.index("function reelTogglePlay(")]
    assert reel.index("if(goDeeperIsOpen()) return;") < reel.index("nextReel();")


# --- 5. The listener's clock -----------------------------------------------

#: 03:30 UTC on Friday 2 October 2026 - 8:30pm on the Thursday in California.
LATE = datetime(2026, 10, 2, 3, 30, tzinfo=timezone.utc)


def test_the_now_line_is_on_the_listeners_clock():
    with listener_clock.using("America/Los_Angeles"):
        assert script_generator.now_line() == listener_clock.now_line()
        assert listener_clock.now_line().split()[-1] in ("PDT", "PST")
        assert listener_clock.say(LATE) == "Thursday 1 October 2026 at 20:30 PDT"
    with listener_clock.using("Europe/London"):
        assert listener_clock.say(LATE) == "Friday 2 October 2026 at 4:30 BST"


def test_no_listener_means_the_editions_zone_never_utc():
    with listener_clock.server():
        assert listener_clock.current() == ""
        assert "UTC" not in listener_clock.say(LATE)
        assert listener_clock.say(LATE).startswith("Thursday 1 October")


def test_an_unreadable_zone_is_ignored():
    with listener_clock.using("Not/AZone"):
        assert listener_clock.current() == ""
    assert listener_clock.clean("America/Chicago") == "America/Chicago"
    assert listener_clock.clean("x" * 200) == ""


def test_an_article_from_this_evening_is_today_not_yesterday():
    # Dated Thursday (the US date it ran); asked at 8:30pm Pacific, when the
    # server's UTC calendar has already turned over to Friday.
    thursday = datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert research.age_phrase(thursday, LATE) == "yesterday"
    pacific = LATE.astimezone(ZoneInfo("America/Los_Angeles"))
    assert research.age_phrase(thursday, pacific) == "today"
    with listener_clock.using("America/Los_Angeles"):
        # The default `now` is the listener's own.
        assert listener_clock.now().utcoffset().total_seconds() < 0


def test_the_request_header_sets_the_clock(client):
    seen = {}

    @appmod.app.get("/api/_test_clock_1001")
    async def _probe():  # pragma: no cover - registered only for this test
        seen["zone"] = listener_clock.current()
        return {"ok": True}

    # Ahead of the static mount, which would otherwise answer the path.
    routes = appmod.app.router.routes
    routes.insert(0, routes.pop())
    try:
        client.get("/api/_test_clock_1001", headers={"X-FAM-TZ": "America/Los_Angeles"})
        assert seen["zone"] == "America/Los_Angeles"
        client.get("/api/_test_clock_1001")
        assert seen["zone"] == ""
        client.get("/api/_test_clock_1001", headers={"X-FAM-TZ": "Mars/Olympus"})
        assert seen["zone"] == ""
    finally:
        appmod.app.router.routes[:] = [
            r for r in appmod.app.router.routes
            if getattr(r, "path", "") != "/api/_test_clock_1001"]


def test_a_task_started_by_a_request_can_go_back_to_the_servers_clock():
    async def run():
        listener_clock.set_for_request("America/Los_Angeles")

        async def cycle():
            listener_clock.set_for_request("")
            return listener_clock.current()

        inside = await asyncio.create_task(cycle())
        return inside, listener_clock.current()

    inside, outside = asyncio.run(run())
    assert inside == "" and outside == "America/Los_Angeles"


def test_prefetch_and_the_edition_reset_the_clock():
    prefetch_src = (ROOT / "prefetch.py").read_text()
    edition_src = (ROOT / "daily_edition.py").read_text()
    assert 'listener_clock.set_for_request("")' in prefetch_src
    assert 'listener_clock.set_for_request("")' in edition_src


def test_the_client_sends_its_zone_and_settings_can_pin_one():
    assert 'headers.set("X-FAM-TZ", zone)' in INDEX
    assert listener_clock.HEADER == "X-FAM-TZ"
    assert "localPrefs().time_zone" in _fn("listenerZone")
    assert 'settingsRow("Time zone"' in INDEX
    for name, _label in re.findall(r'\["([A-Za-z_/]+)", "([^"]+)"\]',
                                   INDEX[INDEX.index("var TIME_ZONES"):
                                         INDEX.index("function zoneLabel(")]):
        assert listener_clock.clean(name) == name, name
