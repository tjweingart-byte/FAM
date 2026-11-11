"""What changed in a mix since the listener last heard it (§538).

R5 on the mix card and Q5 inside the mix, both fed by `signals.py` through
`/api/mixes`. These pin the rules the owner's picks rest on: a signal is a
measurement (a story in the Trending edition or the pool, a game gone final),
about a subject the mix follows specifically, newer than the last listen,
gone once today's episode is heard, one per mix, and never able to take the
mixes list down.
"""
from __future__ import annotations

import os
import pathlib
import sys
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import mixes as M  # noqa: E402
import saved as saved_mod  # noqa: E402
import signals as S  # noqa: E402
import stories  # noqa: E402

PASSWORD = "a-long-enough-password"
NOW = 1_800_000_000.0
INDEX = (pathlib.Path(__file__).resolve().parent.parent / "static" / "index.html").read_text()


def story(title, *, first_seen=NOW - 600, coverage=0, live_status="", status_at=0.0,
          query=""):
    return SimpleNamespace(id="s-" + title[:8], title=title, subject=title, query=query or title,
                           anchors=(), first_seen=first_seen, coverage=coverage,
                           live_status=live_status, status_at=status_at)


EAGLES = M.followed_item("f:nfl~Eagles")
NFL = M.followed_item("f:nfl")
STOCKS = M.followed_item("f:stocks")


# --- what counts as being about a followed subject ---------------------------


def test_a_named_team_or_subject_is_specific():
    assert S.specific_subject("Eagles lose their left tackle for a month") == ("nfl", "Eagles")
    assert S.specific_subject("NFL trade deadline passes quietly")[0] == "nfl"
    assert S.specific_subject("Formula 1 penalty reshuffles the title fight")[0] == "formula1"


def test_a_whole_field_is_not_a_subject():
    """`episode_subject`'s facet-wide last resort is refused: money news is
    not news about Stocks & Economy (§155's rule). "Fed" carries the macro
    subtag, which is Stocks & Economy's own, so it does count."""
    import topics

    assert topics.episode_subject("Bond yields climb") == ("stocks", "")
    assert S.specific_subject("Bond yields climb") == ("", "")
    assert S.specific_subject("Fed holds rates")[0] == "stocks"


def test_a_whole_subject_takes_its_teams_and_a_team_takes_only_itself():
    eagles = S.specific_subject("Eagles lose their left tackle")
    chiefs = S.specific_subject("Chiefs sign a kicker")
    assert S.matches(S.subject_of(NFL), eagles) and S.matches(S.subject_of(NFL), chiefs)
    assert S.matches(S.subject_of(EAGLES), eagles)
    assert not S.matches(S.subject_of(EAGLES), chiefs)


def test_typed_and_bank_items_never_carry_a_signal():
    typed = M.custom_item("what my council is doing about the high street")
    assert S.subject_of(typed) is None
    assert S.item_signal(typed, [story("Eagles win")], [], S.Heard(), NOW) is None


# --- trending -----------------------------------------------------------------


def test_a_trending_edition_story_newer_than_the_last_listen_is_a_signal():
    heard = S.Heard(at=NOW - 3600, title="Monday's Eagles")
    found = S.item_signal(EAGLES, [story("Eagles lose their left tackle")], [], heard, NOW)
    assert found and found.kind == S.TRENDING
    assert found.headline == "Eagles lose their left tackle"


def test_a_story_the_listener_was_already_past_is_not():
    heard = S.Heard(at=NOW - 60)
    old = story("Eagles lose their left tackle", first_seen=NOW - 3600)
    assert S.item_signal(EAGLES, [old], [], heard, NOW) is None


def test_never_heard_reaches_back_one_day_only():
    fresh = story("Eagles lose their left tackle", first_seen=NOW - 3600)
    stale = story("Eagles lose their left tackle", first_seen=NOW - 2 * 86400)
    assert S.item_signal(EAGLES, [fresh], [], S.Heard(), NOW)
    assert S.item_signal(EAGLES, [stale], [], S.Heard(), NOW) is None


def test_quiet_once_todays_episode_is_heard():
    heard = S.Heard(today=True)
    assert S.item_signal(EAGLES, [story("Eagles lose their left tackle")], [], heard, NOW) is None


def test_a_pool_story_needs_real_coverage_to_count_as_trending():
    thin = story("Eagles lose their left tackle", coverage=S.TRENDING_MIN_OUTLETS - 1)
    wide = story("Eagles lose their left tackle", coverage=S.TRENDING_MIN_OUTLETS)
    assert S.item_signal(EAGLES, [], [thin], S.Heard(), NOW) is None
    assert S.item_signal(EAGLES, [], [wide], S.Heard(), NOW).kind == S.TRENDING


# --- a major update -----------------------------------------------------------


def test_a_game_gone_final_since_the_last_listen_is_a_major_update():
    heard = S.Heard(at=NOW - 7200)
    game = story("Eagles at Cowboys", live_status="final", status_at=NOW - 600,
                 first_seen=NOW - 86400)
    found = S.item_signal(EAGLES, [], [game], heard, NOW)
    assert found and found.kind == S.UPDATE and S.LABELS[found.kind] == "Major update"


def test_a_result_the_listener_has_already_heard_about_is_not():
    heard = S.Heard(at=NOW - 60)
    game = story("Eagles at Cowboys", live_status="final", status_at=NOW - 600)
    assert S.item_signal(EAGLES, [], [game], heard, NOW) is None


def test_a_result_outranks_a_trend_and_one_signal_shows_per_mix():
    trend = S.Found(S.TRENDING, "a", "NFL", "x", coverage=500, at=NOW)
    update = S.Found(S.UPDATE, "b", "Eagles", "y", coverage=1, at=NOW - 10)
    assert S.strongest([None, trend, update]) is update


def test_the_pool_records_when_a_games_status_changed():
    """`status_at` is the clock "since you last listened" needs; `live_as_of`
    moves on every sweep and cannot be it."""
    signal = stories.Signal(subject="Eagles at Cowboys", observation="score",
                            domain=stories.SPORTS, live_line="PHI 7 DAL 3",
                            live_status="in_progress")
    held = replace(stories.Story(subject="Eagles at Cowboys", title="Eagles at Cowboys",
                                 angle="", query="q", domain=stories.SPORTS,
                                 source="test", tags=(), first_seen=NOW - 3600,
                                 last_seen=NOW - 3600, shelf_life=86400.0),
                   live_status="in_progress", status_at=NOW - 3600)
    same = stories._seen_again(held, signal, NOW - 1800)
    assert same.status_at == NOW - 3600
    final = stories._seen_again(same, replace(signal, live_status="final"), NOW)
    assert final.live_status == "final" and final.status_at == NOW


def test_bubbles_never_invent_a_before():
    assert S.bubbles(S.Heard(), "Today's", "")["then"] is None
    both = S.bubbles(S.Heard(at=NOW - 86400, title="Monday's"), "Today's", "Mon")
    assert both == {"then": {"day": "Mon", "title": "Monday's"}, "now": {"title": "Today's"}}


# --- through /api/mixes -------------------------------------------------------


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)
    monkeypatch.setattr(appmod, "MIXES", M.MixStore(str(tmp_path / "api.db")))
    monkeypatch.setattr(appmod, "SAVED", saved_mod.SavedStore(str(tmp_path / "saved.db")))
    monkeypatch.setattr(appmod, "_picture_url", lambda q, t="", m=0: "/api/thumb/nfl")
    c = TestClient(appmod.app)
    assert c.post("/api/auth/signup", json={"email": "sig@fam.test",
                                            "password": PASSWORD}).status_code == 200
    c.post("/api/mixes", json={"name": "Game day", "topic_ids": ["f:nfl~Eagles", "f:formula1"]})
    return c


def _with_story(monkeypatch, *found):
    now = time.time()
    monkeypatch.setattr(appmod.trending_bank, "stories_in_memory",
                        lambda n=None: [SimpleNamespace(**dict(vars(s), first_seen=now - 60))
                                        for s in found])


def test_the_mixes_list_carries_r5_and_q5(client, monkeypatch):
    _with_story(monkeypatch, story("Eagles lose their left tackle"))
    mix = client.get("/api/mixes").json()["mixes"][0]
    assert mix["signal"] == {"kind": "trending", "label": "Trending",
                             "item_id": "f:nfl~Eagles", "topic": "Eagles",
                             "headline": "Eagles lose their left tackle",
                             "picture": "/api/thumb/nfl"}
    eagles, f1 = mix["items"]
    assert eagles["signal"]["kind"] == "trending"
    assert eagles["since"]["then"] is None          # never heard: one bubble
    assert eagles["since"]["now"]["title"] == "Eagles lose their left tackle"
    assert "signal" not in f1 and "since" not in f1


def test_the_last_listen_becomes_the_then_bubble(client, monkeypatch):
    mix = client.get("/api/mixes").json()["mixes"][0]
    yesterday = M.earlier_prompts(mix["items"][0]["prompt"], 1)[0]
    appmod.SAVED.note_listen(_user(client), yesterday, 1.5, "dailyfam",
                             title="Eagles lose their left tackle",
                             at=time.time() - 86400)
    _with_story(monkeypatch, story("Eagles name a new starter"))
    eagles = client.get("/api/mixes").json()["mixes"][0]["items"][0]
    assert eagles["since"]["then"]["title"] == "Eagles lose their left tackle"
    assert eagles["since"]["then"]["day"]


def test_heard_today_means_quiet(client, monkeypatch):
    mix = client.get("/api/mixes").json()["mixes"][0]
    appmod.SAVED.note_listen(_user(client), mix["items"][0]["prompt"], 1.5, "dailyfam",
                             title="Today's Eagles")
    _with_story(monkeypatch, story("Eagles lose their left tackle"))
    mix = client.get("/api/mixes").json()["mixes"][0]
    assert "signal" not in mix and "signal" not in mix["items"][0]


def test_a_broken_signal_never_takes_the_list_down(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no")
    monkeypatch.setattr(S, "item_signal", boom)
    res = client.get("/api/mixes")
    assert res.status_code == 200 and "signal" not in res.json()["mixes"][0]


def _user(client) -> str:
    """Whose mix it is: the listener the API tied it to (the id is never
    served to a client, so the test reads it from the store)."""
    return appmod.MIXES.all_mixes()[0].user_id


# --- the interface ------------------------------------------------------------


def test_the_card_and_the_mix_draw_r5_and_q5():
    assert "function mixSignalCardHTML(m)" in INDEX
    assert "if(m.signal) return mixSignalCardHTML(m);" in INDEX
    assert "function mixBubblesHTML(since)" in INDEX
    assert 'onclick="playWhatChanged()"' in INDEX and "or play all " in INDEX


def test_the_signal_colours_exist_in_both_themes():
    dark = INDEX.split(":root{", 1)[1].split("}", 1)[0]
    light = INDEX.split('html[data-theme="light"]{', 1)[1].split("}", 1)[0]
    for token in ("--sig-tr:", "--sig-up:", "--sig-ink:"):
        assert token in dark and token in light, token
