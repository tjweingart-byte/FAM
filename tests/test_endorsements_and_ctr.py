"""The signals the app was collecting and throwing away, and the measurement
nobody had taken.

Three things somebody can do about an episode after hearing it - send it to a
person, post it to their followers, keep it for later - and until this change
not one of them reached the taste model. `share` was the sharp case: `app.py`
had recorded the event since the messages feature shipped, `EVENT_KINDS` did
not list the kind, and `EventStore.record` dropped every row with a warning.
Nothing failed, nothing was slower, and a strong signal simply did not exist.

The second half is the impression-to-play join. Every impression written since
the feature shipped carries the listener, the tile, the rail and the ranking
version; every play carries the listener, the tile and the time. That is a
click-through rate and nothing read it. These tests pin the three ways such a
number can be made dishonest: counting renders instead of occasions, giving
every offer before a play the credit for it, and reporting a rate from a
handful of rows.
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import saved as saved_mod  # noqa: E402
import social as S  # noqa: E402
import topics as T  # noqa: E402

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import ctr_report  # noqa: E402


@pytest.fixture
def store(tmp_path):
    return T.EventStore(str(tmp_path / "myfam.db"))


# --- the three endorsements -----------------------------------------------


def test_every_endorsement_is_a_kind_the_log_accepts(store):
    """The bug, directly: a kind app.py writes and the log does not know."""
    for kind in T.ENDORSEMENTS:
        store.record(T.Event("u1", kind, "", "nvidia earnings", ("tech",)))
    kinds = {e.kind for e in store.for_user("u1")}
    assert kinds == set(T.ENDORSEMENTS), (
        "an endorsement app.py records must survive the write, or the signal "
        "is collected and silently discarded"
    )


def test_an_endorsement_outranks_a_play_and_not_a_completion():
    """Sending somebody an episode is a stronger claim about taste than
    pressing play and a weaker one than sitting through the whole thing."""
    for kind in T.ENDORSEMENTS:
        assert T.EVENT_WEIGHT[kind] > T.EVENT_WEIGHT["play"]
        assert T.EVENT_WEIGHT[kind] < T.EVENT_WEIGHT["complete"]


def test_sharing_and_vibing_weigh_the_same():
    """One is sent to a person and the other posted to followers. A rule
    making either worth more would need a number nobody can tune."""
    assert T.EVENT_WEIGHT["share"] == T.EVENT_WEIGHT["vibe"]


def test_an_endorsement_moves_the_taste_profile(store):
    """The whole point: it has to reach `taste`, not merely be stored."""
    before = T.taste(store.for_user("u1"))
    store.record(T.Event("u1", "share", "", "how chips are made", ("tech", "chips")))
    after = T.taste(store.for_user("u1"))
    assert not before
    assert after.get("chips", 0) > 0


def test_an_impression_is_still_not_an_endorsement():
    """The line this change must not cross. Being shown something says
    nothing about whether you liked it."""
    assert T.IMPRESSION not in T.EVENT_WEIGHT
    assert T.IMPRESSION not in T.ENDORSEMENTS


# --- they are actually written, from the endpoints -------------------------


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "EVENTS", T.EventStore(str(tmp_path / "e.db")))
    monkeypatch.setattr(appmod, "SOCIAL", S.SocialStore(str(tmp_path / "s.db")))
    monkeypatch.setattr(appmod, "SAVED", saved_mod.SavedStore(str(tmp_path / "v.db")))
    return TestClient(appmod.app)


def test_a_vibe_is_recorded_as_taste(client):
    # An account's vibe: since §127 a guest's is posted and not remembered.
    client.post("/api/auth/signup", json={"email": "vibe@fam.test",
                                          "password": "a-long-enough-password"})
    client.post("/api/vibe", json={"query": "why chip fabs cost so much",
                                   "title": "Fabs", "minutes": 3})
    kinds = [e.kind for e in appmod.EVENTS.for_user(_listener(client))]
    assert "vibe" in kinds


def test_saving_is_recorded_and_unsaving_is_not(client, monkeypatch):
    monkeypatch.setattr(appmod, "_require_account", lambda request: "acct-1")
    client.post("/api/saved", json={"query": "how the grid balances",
                                    "minutes": 3, "title": "Grid"})
    assert [e.kind for e in appmod.EVENTS.for_user("acct-1")] == ["save"]
    client.delete("/api/saved?q=how the grid balances&minutes=3")
    assert [e.kind for e in appmod.EVENTS.for_user("acct-1")] == ["save"], (
        "un-saving is a shelf being tidied, not a skip - reading it as a "
        "negative would punish the listeners who use the shelf most"
    )


def _listener(client) -> str:
    """Whichever anonymous id the server minted for this test client."""
    return client.get("/api/auth/me").json().get("user_id", "")


# --- the impression-to-play join ------------------------------------------


def test_a_refresh_is_not_a_rejection(store):
    """Renders are not occasions. Counting rows would report the most
    reloaded feed as the least effective one."""
    for i in range(20):
        store.record_impressions("u1", [("from_history", "a")], at=1000.0 + i)
    rows = store.impression_outcomes()
    assert len(rows) == 1


def test_a_separate_occasion_is_a_separate_offer(store):
    store.record_impressions("u1", [("from_history", "a")], at=1000.0)
    store.record_impressions("u1", [("from_history", "a")],
                             at=1000.0 + 5 * T.FATIGUE_BUCKET)
    assert len(store.impression_outcomes()) == 2


def test_only_the_last_offer_before_a_play_is_credited(store):
    """A tile that had to be shown nine times before it was taken did not
    convert nine times. Crediting every offer would make the tiles nobody
    wants look like the ones that work."""
    for day in range(5):
        store.record_impressions("u1", [("from_history", "a")],
                                 at=1000.0 + day * 86400)
    store.record(T.Event("u1", "play", "a", "", (), at=1000.0 + 4 * 86400 + 60))
    rows = store.impression_outcomes()
    assert len(rows) == 5
    assert sum(1 for r in rows if r["taken"]) == 1
    taken = [r for r in rows if r["taken"]][0]
    assert taken["at"] == 1000.0 + 4 * 86400, "the credit goes to the last touch"
    assert taken["lag"] == 60


def test_a_tile_shown_again_after_it_was_played_is_not_an_offer(store):
    """That is the feed repeating itself, not something they turned down."""
    store.record_impressions("u1", [("from_history", "a")], at=1000.0)
    store.record(T.Event("u1", "play", "a", "", (), at=2000.0))
    store.record_impressions("u1", [("from_history", "a")], at=90_000.0)
    rows = store.impression_outcomes()
    assert len(rows) == 1 and rows[0]["taken"]


def test_the_rail_that_showed_it_is_carried_through(store):
    """`--by section` is the question this whole join exists to answer."""
    store.record_impressions("u1", [("world_trending", "a")], at=1000.0)
    assert store.impression_outcomes()[0]["section"] == "world_trending"


def test_the_ranking_version_is_carried_through(store):
    """Two versions of the ranking on one log must be a GROUP BY."""
    store.record_impressions("u1", [("from_history", "a")], algo="old", at=1000.0)
    assert store.impression_outcomes()[0]["algo"] == "old"


# --- the report -----------------------------------------------------------


def test_a_thin_group_has_no_rate_rather_than_a_bad_one():
    """Three shows and one tap is not a 33% conversion rate, and printing it
    as one invites somebody to act on it."""
    rows = [{"topic_id": "a", "section": "from_history", "algo": "v1",
             "taken": i == 0, "lag": 1.0 if i == 0 else None, "at": float(i),
             "user_id": "u1"}
            for i in range(3)]
    assert ctr_report.collect(rows, "section")[0]["rate"] is None


def test_a_measured_group_gets_its_rate():
    rows = [{"topic_id": "a", "section": "from_history", "algo": "v1",
             "taken": i < 5, "lag": 1.0 if i < 5 else None, "at": float(i),
             "user_id": f"u{i}"}
            for i in range(20)]
    group = ctr_report.collect(rows, "section")[0]
    assert group["shown"] == 20 and group["taken"] == 5
    assert group["rate"] == pytest.approx(0.25)


def test_the_topic_dimension_reads_the_right_field():
    """`row["topic"]` does not exist, and reading it does not raise - it
    groups the whole feed into one nameless bucket and reports the global
    rate as though it were a per-tile finding."""
    rows = [{"topic_id": t, "section": "s", "algo": "v1", "taken": False,
             "lag": None, "at": 1.0, "user_id": "u1"} for t in ("a", "b")]
    assert len(ctr_report.collect(rows, "topic")) == 2


def test_an_unrated_group_sorts_below_a_measured_one():
    """A thin tail must not be able to look like the worst-performing part
    of the feed."""
    rows = ([{"topic_id": "a", "section": "busy", "algo": "v1", "taken": False,
              "lag": None, "at": 1.0, "user_id": f"u{i}"} for i in range(20)]
            + [{"topic_id": "b", "section": "thin", "algo": "v1", "taken": True,
                "lag": 1.0, "at": 1.0, "user_id": "z"}])
    keys = [g["key"] for g in ctr_report.collect(rows, "section")]
    assert keys == ["busy", "thin"]


def test_an_empty_log_reports_silence_and_not_failure(store, capsys, monkeypatch):
    """Zero out of zero reads as a broken feed and is actually no data -
    the rule `prefetch.py` already keeps for its own hit rate."""
    monkeypatch.setattr(ctr_report.topics_mod, "EventStore", lambda: store)
    ctr_report.report(days=30, dimension="section")
    out = capsys.readouterr().out
    assert "No impressions in the window" in out
    assert "0.0%" not in out


def test_the_report_recovers_a_difference_between_rails(store, monkeypatch):
    """The end to end check: a feed where one rail converts better must come
    back out of the join saying so."""
    for u in range(20):
        store.record_impressions(f"u{u}", [("from_history", "a"),
                                           ("world_trending", "b")], at=1000.0)
        if u < 10:
            store.record(T.Event(f"u{u}", "play", "a", "", (), at=1100.0))
        if u < 2:
            store.record(T.Event(f"u{u}", "play", "b", "", (), at=1100.0))
    monkeypatch.setattr(ctr_report.topics_mod, "EventStore", lambda: store)
    groups = {g["key"]: g for g in
              ctr_report.collect(store.impression_outcomes(), "section")}
    assert groups["from_history"]["rate"] == pytest.approx(0.5)
    assert groups["world_trending"]["rate"] == pytest.approx(0.1)
