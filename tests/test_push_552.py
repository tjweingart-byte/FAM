"""Notifications, one daily reminder per person, big stories and finals (PROBLEMS.md §552)."""

from __future__ import annotations

import asyncio
import dataclasses
import pathlib
import sys
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import app as appmod  # noqa: E402
import mixes as mixes_mod  # noqa: E402
import push  # noqa: E402
import push_stories  # noqa: E402
from config import settings  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text()
NY = "America/New_York"


def _fn(name):
    return INDEX.split("function " + name + "(", 1)[1].split("\n  }\n", 1)[0]


def _local(y, mo, d, h, mi, zone=NY):
    return datetime(y, mo, d, h, mi, tzinfo=ZoneInfo(zone)).timestamp()


@pytest.fixture
def keys(monkeypatch):
    patched = dataclasses.replace(settings, vapid_public_key="BPUBLIC",
                                  vapid_private_key="private",
                                  vapid_subject="mailto:ops@example.com", mix_reminders=True)
    monkeypatch.setattr(push, "_settings", lambda: patched)
    monkeypatch.setitem(sys.modules, "pywebpush", type(sys)("pywebpush"))


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(push, "_send_webpush",
                        lambda sub, payload, ps: out.append(payload) or True)
    return out


def _sub(pushes, user="u"):
    pushes.subscribe(user, {"endpoint": f"https://push.example/{user}",
                            "keys": {"p256dh": "k", "auth": "a"}})


def _store(tmp_path, *names):
    store = mixes_mod.MixStore(str(tmp_path / "m.db"))
    made = [store.create("u", name, ["f:nfl"]) for name in names or ("Gym",)]
    return store, made


# --- When the daily reminder goes ------------------------------------------

def test_the_learned_time_is_the_median_first_play():
    now = _local(2026, 11, 12, 12, 0)
    plays = [_local(2026, 11, d, 7, m) for d, m in ((9, 2), (10, 14), (11, 20))]
    plays += [_local(2026, 11, 11, 9, 0)]  # not the first play that day
    assert push.learned_time(plays, NY, now) == "07:15"
    # Too few days to trust: nothing learned.
    assert push.learned_time(plays[:2], NY, now) == ""
    # Older than the window does not count.
    assert push.learned_time([_local(2026, 10, d, 6, 0) for d in (1, 2, 3)], NY, now) == ""


def test_chosen_beats_an_old_mix_time_beats_learned_beats_default(tmp_path):
    store, (mix,) = _store(tmp_path)
    now = _local(2026, 11, 12, 12, 0)
    learned = [_local(2026, 11, d, 6, 40) for d in (9, 10, 11)]
    base = {"daily": True, "daily_at": "", "tz": NY, "chosen": False}
    assert push.daily_time(base, [mix], [], now)[::2] == ("07:30", "default")
    assert push.daily_time(base, [mix], learned, now)[::2] == ("06:40", "learned")
    store.update("u", mix.id, listen_at="08:10", listen_tz=NY)
    mix = store.get("u", mix.id)
    assert push.daily_time(base, [mix], learned, now)[::2] == ("08:10", "mix")
    # "When I usually listen", chosen here, drops the old client's mix time.
    assert push.daily_time(dict(base, chosen=True), [mix], learned, now)[::2] \
        == ("06:40", "learned")
    assert push.daily_time(dict(base, daily_at="21:00", chosen=True), [mix], learned, now)[::2] \
        == ("21:00", "chosen")


def test_one_reminder_per_person_however_many_mixes(tmp_path, monkeypatch, keys, sent):
    store, mixes = _store(tmp_path, "Gym", "Commute", "Bed")
    pushes = push.PushStore(str(tmp_path / "m.db"))
    _sub(pushes)
    monkeypatch.setattr(push, "_edition_ready", lambda now: True)
    monkeypatch.setattr(push, "spread_offset", lambda key, day: 0)
    now = _local(2026, 11, 12, 7, 31)
    assert push.tick(store, pushes, now) == [("u", "2026-11-12", 1)]
    assert len(sent) == 1
    assert push.tick(store, pushes, now + 60) == []


def test_the_headline_is_an_episode_the_edition_wrote(tmp_path, monkeypatch):
    import daily_edition
    store, mixes = _store(tmp_path, "Gym", "Commute")
    store.update("u", mixes[1].id, topic_ids=["f:nfl", "f:basketball"])
    now = _local(2026, 11, 12, 7, 31)
    store.note_played("u", mixes[0].id)
    query = daily_edition.prompt_for(store.get("u", mixes[0].id).items[0], now)
    monkeypatch.setattr(push, "_edition_report", lambda now: {
        "episodes": {query: {"status": "written", "title": "Eagles trade for a corner"}}})
    msg = push.message_for("u", store, now)
    assert msg["title"] == "Your myFAM is ready"
    assert msg["body"] == "Eagles trade for a corner, and 1 more across 2 mixes."
    # No written title: counts only, never an invented headline.
    monkeypatch.setattr(push, "_edition_report", lambda now: {})
    assert push.message_for("u", store, now)["body"] == "2 episodes across 2 mixes."


def test_dormant_mixes_are_waiting_never_ready(tmp_path, monkeypatch):
    import daily_edition
    store, _ = _store(tmp_path)
    monkeypatch.setattr(daily_edition, "is_active", lambda mix, now=None: False)
    msg = push.message_for("u", store, _local(2026, 11, 12, 7, 31))
    assert msg["title"] == "Your myFAM is waiting" and "ready" not in msg["body"]


def test_no_reminder_on_a_day_they_already_listened(tmp_path, monkeypatch):
    store, _ = _store(tmp_path)
    pushes = push.PushStore(str(tmp_path / "m.db"))
    monkeypatch.setattr(push, "_edition_ready", lambda now: True)
    monkeypatch.setattr(push, "spread_offset", lambda key, day: 0)
    now = _local(2026, 11, 12, 7, 31)
    assert push.due(store, pushes, now, plays=lambda u: [_local(2026, 11, 12, 6, 5)]) == []
    assert push.due(store, pushes, now, plays=lambda u: [_local(2026, 11, 11, 6, 5)]) \
        == [("u", "2026-11-12")]


def test_ignored_reminders_back_off_then_stop(tmp_path, monkeypatch):
    store, _ = _store(tmp_path)
    pushes = push.PushStore(str(tmp_path / "m.db"))
    monkeypatch.setattr(push, "_edition_ready", lambda now: True)
    monkeypatch.setattr(push, "spread_offset", lambda key, day: 0)
    pushes.set_prefs("u", daily_at="09:00", tz=NY)
    for d in (9, 10, 11):  # three ignored reminders
        pushes.mark_sent("u", push.DAILY, f"2026-11-{d:02d}", 1, at=_local(2026, 11, d, 9, 0))
    now = _local(2026, 11, 12, 9, 1)
    assert push.due(store, pushes, now) == [], "yesterday was sent: today is skipped"
    assert push.due(store, pushes, now + 86400) == [("u", "2026-11-13")]
    # A play after the latest reminder resets it.
    played = [_local(2026, 11, 11, 12, 0)]
    assert push.due(store, pushes, now, plays=lambda u: played) == [("u", "2026-11-12")]
    # Eight ignored in a row: silence until they play again.
    for d in range(1, 9):
        pushes.mark_sent("u", push.DAILY, f"2026-10-{d:02d}", 1, at=_local(2026, 10, d, 9, 0))
    assert push.ignored_streak(pushes.sent("u", push.DAILY, 20), []) >= push.GIVE_UP_AFTER
    assert push.due(store, pushes, now + 86400) == []


def test_quiet_hours_are_the_listeners_own():
    assert push.in_quiet_hours(NY, _local(2026, 11, 12, 23, 0))
    assert push.in_quiet_hours(NY, _local(2026, 11, 12, 7, 59))
    assert not push.in_quiet_hours(NY, _local(2026, 11, 12, 8, 0))
    assert not push.in_quiet_hours("Asia/Tokyo", _local(2026, 11, 12, 23, 0))


def test_prefs_default_on_and_keep_what_was_not_changed(tmp_path):
    pushes = push.PushStore(str(tmp_path / "m.db"))
    assert pushes.prefs("u") == {"daily": True, "daily_at": "", "breaking": True,
                                 "finals": True, "tz": "", "chosen": False}
    pushes.set_prefs("u", breaking=False, tz="Europe/London")
    pushes.set_prefs("u", daily_at="7:05")
    got = pushes.prefs("u")
    assert (got["breaking"], got["tz"], got["daily_at"], got["chosen"]) \
        == (False, "Europe/London", "07:05", True)
    with pytest.raises(ValueError):
        pushes.set_prefs("u", daily_at="lunch")
    pushes.subscribe("u", {"endpoint": "https://push.example/1",
                           "keys": {"p256dh": "k", "auth": "a"}})
    pushes.mark_sent("u", push.BREAKING, "b-1", 1)
    assert pushes.forget("u") == 1
    assert pushes.prefs("u")["breaking"] is True and not pushes.was_sent("u", push.BREAKING, "b-1")


# --- What somebody follows -------------------------------------------------

def test_followed_names_come_from_mix_subjects_only():
    item = mixes_mod.followed_item("f:nfl~Eagles")
    assert push_stories.names_of_item(item) == ["Eagles"]
    assert push_stories.names_of_item(mixes_mod.followed_item("f:nfl")) == ["NFL"]
    typed = SimpleNamespace(id="c:why do bonds move", title="Why do bonds move")
    assert push_stories.names_of_item(typed) == []


def test_a_name_matches_as_a_whole_phrase():
    assert push_stories.phrase_in("Eagles", "Philadelphia Eagles vs Dallas Cowboys")
    assert not push_stories.phrase_in("Eagle", "Philadelphia Eagles vs Dallas Cowboys")
    assert push_stories.phrase_in("Federal Reserve", "the federal reserve cut rates")
    assert not push_stories.phrase_in("Reserve Federal", "the federal reserve cut rates")
    # A field's own words never vouch: every story in the field carries them.
    assert push_stories.usable_names(["football", "Eagles", "US"]) == ["Eagles"]


# --- Big stories -----------------------------------------------------------

def _signal(subject, coverage, keywords):
    return SimpleNamespace(subject=subject, coverage=coverage, keywords=tuple(keywords),
                           suggested_query=f"{subject}: what is happening and why it matters")


def test_only_one_story_far_above_the_rest_is_proposed_for_everyone():
    signals = [_signal("A", 80, "a"), _signal("B", 30, "b"), _signal("C", 10, "c")]
    judged = push_stories.judge(signals, followed_bar=15, everyone_bar=40, ratio=2.0)
    assert [(s.subject, e) for s, e in judged] == [("A", True), ("B", False)]
    # A busy day - two big stories - is not one massive story.
    close = [_signal("A", 80, "a"), _signal("B", 60, "b")]
    assert [e for _s, e in push_stories.judge(close, 15, 40, 2.0)] == [False, False]


def test_the_same_story_across_sweeps_is_one_row_and_never_approved_by_the_detector(tmp_path):
    stories = push_stories.StoryStore(str(tmp_path / "m.db"))
    now = _local(2026, 11, 12, 14, 0)
    first = stories.note_breaking("Fed cuts rates", "q", ["fed", "rates", "powell"], 20,
                                  False, now)
    assert first["status"] == push_stories.WATCHING
    again = stories.note_breaking("Powell: the Fed cuts rates by half a point", "q2",
                                  ["fed", "rates", "powell", "half"], 70, True, now + 900)
    assert again["id"] == first["id"]
    assert again["status"] == push_stories.PROPOSED and again["peak"] == 70
    assert len(stories.recent(now - 3600)) == 1


@pytest.fixture
def story_env(tmp_path, monkeypatch, keys, sent):
    stories = push_stories.StoryStore(str(tmp_path / "m.db"))
    push_stories.reset(stories)
    pushes = push.PushStore(str(tmp_path / "m.db"))
    for user in ("fan", "other"):
        _sub(pushes, user)
        pushes.set_prefs(user, tz=NY)
    written = []

    async def fake_write(row, generator, cache, now=None):
        written.append(row["id"])
        stories.written(row["id"], "Eagles trade for a corner", now or 0)
        return "Eagles trade for a corner"

    monkeypatch.setattr(push_stories, "write", fake_write)
    monkeypatch.setattr(push_stories, "note_finals", lambda now=None, pool=None: 0)
    yield SimpleNamespace(stories=stories, pushes=pushes, sent=sent, written=written)
    push_stories.reset(None)


def _follows(user):
    return ["Eagles"] if user == "fan" else ["Lakers"]


class _Source:
    def __init__(self, *signals):
        self.signals = list(signals)

    async def collect(self, limit):
        return self.signals


def test_a_follower_is_told_automatically_once_it_is_written(story_env):
    now = _local(2026, 11, 12, 14, 0)
    src = _Source(_signal("Eagles trade for star cornerback", 20, ["eagles", "trade"]))
    got = asyncio.run(push_stories.tick(story_env.pushes, _follows, now=now, source=src))
    assert [(u, d) for _id, u, d in got] == [("fan", 1)]
    assert story_env.written, "sent before its episode was written"
    msg = story_env.sent[0]
    assert msg["title"] == "Big story" and "Eagles trade for a corner" in msg["body"]
    assert msg["url"].startswith("/?listen=")
    # Told once.
    assert asyncio.run(push_stories.tick(story_env.pushes, _follows, now=now + 60,
                                         source=src)) == []


def test_everyone_only_after_the_owner_approves(story_env):
    now = _local(2026, 11, 12, 14, 0)
    src = _Source(_signal("Earthquake strikes the capital", 90, ["earthquake"]),
                  _signal("Something smaller", 20, ["smaller"]))
    assert asyncio.run(push_stories.tick(story_env.pushes, _follows, now=now,
                                         source=src)) == []
    row = next(r for r in story_env.stories.recent(now - 60)
               if r["subject"].startswith("Earthquake"))
    assert row["status"] == push_stories.PROPOSED
    story_env.stories.decide(row["id"], push_stories.APPROVED, now)
    got = asyncio.run(push_stories.tick(story_env.pushes, _follows, now=now + 60, source=src))
    assert sorted(u for _id, u, _d in got) == ["fan", "other"]


def test_a_dismissed_story_reaches_nobody_more(story_env):
    now = _local(2026, 11, 12, 14, 0)
    src = _Source(_signal("Earthquake strikes the capital", 90, ["earthquake"]))
    asyncio.run(push_stories.tick(story_env.pushes, _follows, now=now, source=src))
    row = story_env.stories.recent(now - 60)[0]
    story_env.stories.decide(row["id"], push_stories.DISMISSED, now)
    assert asyncio.run(push_stories.tick(story_env.pushes, _follows, now=now + 60,
                                         source=src)) == []


def test_quiet_hours_hold_a_story_until_morning_if_still_big(story_env):
    night = _local(2026, 11, 12, 23, 30)
    src = _Source(_signal("Eagles trade for star cornerback", 20, ["eagles", "trade"]))
    assert asyncio.run(push_stories.tick(story_env.pushes, _follows, now=night,
                                         source=src)) == []
    morning = _local(2026, 11, 13, 8, 1)
    # Seen by the detector again before morning: still big, so it goes.
    asyncio.run(push_stories.detect(_local(2026, 11, 13, 7, 0), source=src))
    got = asyncio.run(push_stories.tick(story_env.pushes, _follows, now=morning,
                                        source=_Source()))
    assert [u for _id, u, _d in got] == ["fan"]


def test_a_story_no_longer_big_by_morning_is_not_sent(story_env):
    night = _local(2026, 11, 12, 23, 30)
    src = _Source(_signal("Eagles trade for star cornerback", 20, ["eagles", "trade"]))
    asyncio.run(push_stories.tick(story_env.pushes, _follows, now=night, source=src))
    morning = _local(2026, 11, 13, 8, 1)
    assert asyncio.run(push_stories.tick(story_env.pushes, _follows, now=morning,
                                         source=_Source())) == []


def test_nothing_is_sent_that_could_not_be_written(tmp_path, monkeypatch, keys, sent):
    stories = push_stories.StoryStore(str(tmp_path / "m.db"))
    push_stories.reset(stories)
    try:
        pushes = push.PushStore(str(tmp_path / "m.db"))
        _sub(pushes, "fan")
        monkeypatch.setattr(push_stories, "note_finals", lambda now=None, pool=None: 0)
        now = _local(2026, 11, 12, 14, 0)
        src = _Source(_signal("Eagles trade for star cornerback", 20, ["eagles"]))
        # No writer on this server.
        assert asyncio.run(push_stories.tick(pushes, _follows, now=now, source=src)) == []
        assert sent == []
        assert "no writer" in stories.recent(now - 60)[0]["detail"]
    finally:
        push_stories.reset(None)


def test_the_kind_switch_is_the_listeners(story_env):
    story_env.pushes.set_prefs("fan", breaking=False)
    now = _local(2026, 11, 12, 14, 0)
    src = _Source(_signal("Eagles trade for star cornerback", 20, ["eagles", "trade"]))
    assert asyncio.run(push_stories.tick(story_env.pushes, _follows, now=now,
                                         source=src)) == []


# --- Finals ----------------------------------------------------------------

def test_a_followed_final_is_told_with_its_score(tmp_path, monkeypatch, keys, sent):
    import live_facts
    import stories as stories_mod
    store = push_stories.StoryStore(str(tmp_path / "m.db"))
    push_stories.reset(store)
    try:
        pushes = push.PushStore(str(tmp_path / "m.db"))
        _sub(pushes, "fan")
        _sub(pushes, "other")
        now = _local(2026, 11, 12, 22, 0) - 3600  # 21:00, before quiet hours
        game = SimpleNamespace(id="g1", domain=stories_mod.SPORTS,
                               live_status=live_facts.FINAL,
                               live_line="Final · Eagles 27–24 Cowboys",
                               subject="Philadelphia Eagles vs Dallas Cowboys", title="",
                               query="Eagles vs Cowboys", status_at=now, live_as_of=now,
                               last_seen=now)
        assert push_stories.note_finals(now, pool=SimpleNamespace(stories=[game])) == 1
        assert push_stories.note_finals(now, pool=SimpleNamespace(stories=[game])) == 0

        async def fake_write(row, generator, cache, now=None):
            store.written(row["id"], "Eagles hold off Dallas", now or 0)
            return "Eagles hold off Dallas"

        monkeypatch.setattr(push_stories, "write", fake_write)
        monkeypatch.setattr(push_stories, "note_finals", lambda now=None, pool=None: 0)
        monkeypatch.setattr(push_stories, "_LAST_DETECT", now)
        got = asyncio.run(push_stories.tick(pushes, _follows, now=now))
        assert [u for _id, u, _d in got] == ["fan"]
        assert sent[0]["title"] == "Eagles 27–24 Cowboys · Final"
        assert sent[0]["body"] == "The recap is ready. Tap to listen."
    finally:
        push_stories.reset(None)


def test_finals_ride_the_sweep_and_ask_nothing():
    src = (ROOT / "push_stories.py").read_text()
    assert "balldontlie.shared_client" not in src and "client.get(" not in src


# --- The app ---------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    with TestClient(appmod.app) as c:
        yield c


def test_the_prefs_endpoints(client):
    assert client.post("/api/push/prefs", json={"breaking": False}).status_code == 401
    client.post("/api/auth/signup", json={"email": "a@b.com", "password": "password12"})
    got = client.get("/api/push").json()["prefs"]
    assert got["daily"] and got["breaking"] and got["finals"]
    assert got["daily_source"] == "default" and got["daily_time"] == "07:30"
    res = client.post("/api/push/prefs", json={"daily_at": "6:45", "finals": False,
                                               "tz": NY}).json()["prefs"]
    assert res["daily_time"] == "06:45" and res["daily_source"] == "chosen"
    assert res["finals"] is False
    assert client.post("/api/push/prefs", json={"daily_at": "soon"}).status_code == 400


def test_the_admin_page_is_admin_only(client):
    assert client.get("/api/admin/push").status_code in (401, 403, 404)
    assert client.post("/api/admin/push/decide",
                       json={"id": "b-1", "status": "approved"}).status_code in (401, 403, 404)
    src = (ROOT / "app.py").read_text()
    body = src.split("async def admin_push_decide(", 1)[1].split("\n\n\n", 1)[0]
    assert "_require_admin(request)" in body and "push.deliver" not in body


def test_the_app_follows_only_what_was_chosen():
    src = (ROOT / "app.py").read_text()
    body = src.split("def _push_follows(", 1)[1].split("\n\n\n", 1)[0]
    assert "names_of_item" in body and "names_of_interests" in body
    assert "taste(" not in body


# --- The page --------------------------------------------------------------

def test_settings_has_the_three_switches():
    body = _fn("renderSettings")
    assert '"Daily reminder"' in body and "openDailyTimeMenu()" in body
    assert "setPushSwitch('breaking'" in body and "setPushSwitch('finals'" in body


def test_a_notification_tap_opens_what_it_names():
    body = _fn("openNotificationLink")
    assert 'params.get("listen")' in body and 'setTab("playfam")' in body
    play = _fn("playNotifiedStory")
    assert "exploreMinutes: BROWSE_MINUTES" in play and "prompt: query" in play
    assert ".then(openNotificationLink)" in INDEX


def test_the_env_example_documents_the_new_settings():
    example = (ROOT / ".env.example").read_text()
    for line in ("PUSH_BREAKING=1", "PUSH_BREAKING_FOLLOWED_OUTLETS=15",
                 "PUSH_BREAKING_EVERYONE_OUTLETS=40", "PUSH_BREAKING_RATIO=2.0",
                 "PUSH_FINALS=1"):
        assert line in example


# --- §552 follow-up: the sweep runs for people with finals on ----------------

def test_the_push_loop_hands_the_sweep_only_finals_followers_teams(tmp_path, monkeypatch):
    import stories as stories_mod
    stories_mod.reset()
    push_stories.reset(None)
    pushes = push.PushStore(str(tmp_path / "m.db"))
    pushes.set_prefs("off", finals=False)
    follows = {"fan": ["Eagles"], "off": ["Cowboys"]}
    now = _local(2026, 11, 12, 14, 0)
    got = push_stories.publish_finals_followed(["fan", "off"], pushes,
                                               lambda u: follows[u], now)
    assert got == ("Eagles",)
    # Finals off on the server: nobody's teams are swept for it.
    patched = dataclasses.replace(settings, push_finals=False)
    monkeypatch.setattr(push_stories, "_settings", lambda: patched)
    later = now + push_stories.FOLLOWED_SECONDS
    assert push_stories.publish_finals_followed(["fan"], pushes,
                                                lambda u: follows[u], later) == ()
    stories_mod.reset()
    push_stories.reset(None)
