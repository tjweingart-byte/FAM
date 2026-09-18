"""Interests, language - and what "Skip for now" costs.

Three separate claims are under test here, and they are worth naming because
each was a product decision before it was code:

1. A declared interest **changes the feed**. A picker that stores an answer
   nobody reads is worse than no picker, and this is the check that it is read.
2. The six-interest cap is enforced **on the way in**, not only by a disabled
   button. A cap only the client applies is not a cap.
3. Anything the server keeps for you needs an account, and anything you can
   hear does not.
"""
from __future__ import annotations

import calendar
import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import preferences as P  # noqa: E402
import topics as T  # noqa: E402

GOOD = "a-long-enough-password"


@pytest.fixture
def store(tmp_path):
    return P.PreferenceStore(str(tmp_path / "prefs.db"))


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)
    monkeypatch.setattr(appmod, "EVENTS", T.EventStore(str(tmp_path / "e.db")))
    return TestClient(appmod.app)


def sign_up(client, email="one@fam.test"):
    res = client.post("/api/auth/signup", json={"email": email, "password": GOOD})
    assert res.status_code == 200, res.text
    return client


# --- the store ------------------------------------------------------------


def test_an_unknown_listener_reads_as_the_defaults(store):
    """A missing row is a listener who has not chosen yet, not an error."""
    prefs = store.get("nobody")
    assert prefs.interests == () and prefs.language == "en"
    assert prefs.weekly_recap is True and prefs.intro_done is False


def test_saving_one_page_does_not_clear_the_other(store):
    """The intro saves interests, then language, from two screens."""
    store.save("u", interests=["tech", "money"])
    store.save("u", language="es")
    prefs = store.get("u")
    assert prefs.interests == ("tech", "money") and prefs.language == "es"


def test_there_is_no_cap_on_how_many_interests_somebody_has(store):
    """There used to be six, and the number was never doing anything a
    listener wanted: it made somebody with seven interests choose which one to
    lie about, and the ranker is perfectly happy to weigh eight (§99)."""
    every = list(T.TAG_LABELS)
    assert len(every) == 8
    store.save("u", interests=every)
    assert store.get("u").interests == tuple(every)


def test_unbounded_is_still_bounded_by_the_vocabulary(store):
    """No cap is not no validation. Every value has to be a facet and
    duplicates collapse, so eight is the most this can ever hold - a fact
    about the vocabulary rather than a rule anybody is told."""
    store.save("u", interests=list(T.TAG_LABELS) * 5)
    assert store.get("u").interests == tuple(T.TAG_LABELS)


def test_an_invented_interest_is_refused(store):
    with pytest.raises(P.PreferenceError):
        store.save("u", interests=["astrology"])


def test_duplicates_collapse_rather_than_eating_the_cap(store):
    store.save("u", interests=["tech", "tech", "money"])
    assert store.get("u").interests == ("tech", "money")


def test_an_unknown_language_is_refused(store):
    with pytest.raises(P.PreferenceError):
        store.save("u", language="klingon")


def test_every_interest_on_offer_is_a_tag_the_ranker_scores():
    """The picker and the ranking share one vocabulary or the picker is a lie.

    Not equality any more: the ranking vocabulary is deliberately wider than
    the pickable one (TAG_PARENT), so this asserts the three things that
    actually have to hold rather than that the two sets are the same size.
    """
    assert set(P.INTERESTS) <= set(T.TAG_WORDS), (
        "an interest that is not a TAG_WORDS tag can never rank anything")
    assert set(P.INTERESTS) == set(T.TAG_LABELS), (
        "the picker is built from TAG_LABELS; a facet missing a label is "
        "unpickable and one with no facet is unrankable")
    assert set(T.TAG_PARENT.values()) <= set(T.TAG_LABELS), (
        "a subtag whose parent is not a real facet can never be chosen for")
    assert set(T.TAG_PARENT) <= set(T.TAG_WORDS), (
        "a subtag with no keywords can never be matched from a free search")
    assert not (set(T.TAG_PARENT) & set(T.TAG_LABELS)), (
        "a tag cannot be both a facet and a subtag of another")


# --- the recap week -------------------------------------------------------

# 2026-09-06 is a Sunday; 2026-09-09 the Wednesday after it. timegm rather
# than mktime: week_start works in UTC, and mktime would read these as local
# time - so the test would quietly measure the machine's timezone instead.
SUNDAY = calendar.timegm(time.strptime("2026-09-06 09:00", "%Y-%m-%d %H:%M"))
WEDNESDAY = SUNDAY + 3 * 86400
NEXT_SUNDAY = SUNDAY + 7 * 86400


def test_a_week_is_named_by_the_sunday_that_started_it():
    assert P.week_start(SUNDAY) == "2026-09-06"
    assert P.week_start(WEDNESDAY) == "2026-09-06", "midweek is still that week"
    assert P.week_start(NEXT_SUNDAY) == "2026-09-13"


def test_the_recap_scheduling_is_gone_and_the_week_function_is_not(store):
    """The popup is removed - myFAM's "What you missed last week" rail is what
    replaced it - so nothing asks whether a recap is due.

    `week_start` and the `recap_week` column stay. A pure function of the
    clock and a stored date cost nothing to keep and are what a scheduled
    digest would be built on; dropping the column is a migration with no
    benefit.
    """
    assert not hasattr(store, "recap_due")
    assert not hasattr(store, "mark_recap_seen")
    assert P.week_start(WEDNESDAY) == "2026-09-06"
    assert store.get("u").recap_week == ""


# --- interests actually reach the feed ------------------------------------


def test_a_declared_interest_ranks_the_feed_for_a_listener_with_no_history():
    """The reason to ask at all: "Made for you" is empty without this."""
    profile = T.taste([], interests=["science"])
    assert profile.get("science", 0) > 0
    ranked = T.rank_from_history(profile, set())
    assert ranked, "six chosen interests still produced an empty personal shelf"
    assert any("science" in t.tags for t in ranked)


def test_behaviour_outweighs_a_declaration_once_there_is_any():
    """An intro answer is a starting position, not a rule. Someone who chose
    Sport and then finished three tech episodes should get tech."""
    now = time.time()
    events = [T.Event("u", "complete", "ai-agents", "", ("tech",), now)] * 3
    profile = T.taste(events, now, interests=["sports"])
    assert profile["tech"] > profile["sports"]


# --- the API and the gate -------------------------------------------------


def test_the_choices_are_public_but_the_answers_are_not(client):
    """The intro runs before anyone has an account, so it must be able to list
    what is on offer - and must not claim an anonymous answer was saved."""
    body = client.get("/api/preferences").json()
    assert len(body["interests_available"]) == T.PICKER_SIZE
    assert len(body["interests_all"]) == len(T.TAG_LABELS)
    assert body["interests_source"] in ("played", "default")
    assert body["languages"]
    # No cap is served, because there is none to serve.
    assert "max_interests" not in body
    # And every offered interest carries the short name the wheel draws.
    assert all(i["short"] and i["label"].startswith(i["short"])
               for i in body["interests_available"])
    assert body["account"] is False and body["saved"] is False


def test_a_language_that_changes_nothing_says_so(client):
    """PROBLEMS.md's oldest lesson: a setting that silently does nothing is
    the failure this project has paid for most often."""
    assert client.get("/api/preferences").json()["language_active"] is False


def test_storing_a_preference_needs_an_account(client):
    res = client.post("/api/preferences", json={"interests": ["tech"]})
    assert res.status_code == 401
    assert "account" in res.json()["error"].lower()


def test_an_account_stores_and_returns_them(client):
    sign_up(client)
    saved = client.post("/api/preferences",
                        json={"interests": ["tech", "money"], "language": "fr",
                              "intro_done": True}).json()
    assert saved["interests"] == ["tech", "money"] and saved["language"] == "fr"
    body = client.get("/api/preferences").json()
    assert body["saved"] is True and body["intro_done"] is True


def test_every_interest_at_once_is_accepted(client):
    """All eight, over the API, with no cap anywhere between here and the
    store. The refusal this replaces named a number nobody had asked for."""
    sign_up(client)
    res = client.post("/api/preferences", json={"interests": list(T.TAG_LABELS)})
    assert res.status_code == 200
    assert res.json()["interests"] == list(T.TAG_LABELS)


def test_an_invented_interest_is_still_a_message_not_a_stack_trace(client):
    sign_up(client)
    res = client.post("/api/preferences", json={"interests": ["astrology"]})
    assert res.status_code == 400
    assert "astrology" in res.json()["error"]


def test_stored_interests_rank_myfam_without_being_asked_for(client):
    """The account path: the feed reads them, the client never sends them."""
    sign_up(client)
    client.post("/api/preferences", json={"interests": ["science"]})
    feed = client.get("/api/myfam").json()
    made_for_you = [s for s in feed["sections"] if s["key"] == "from_history"][0]
    assert made_for_you["topics"], "a chosen interest left the personal shelf empty"


def test_an_anonymous_listener_can_still_be_ranked_for_this_one_request(client):
    """Their intro answers live in their own browser and nowhere else, so a
    hint on the request is the only route by which the ranker can honour them.
    It is validated against a fixed vocabulary and never stored."""
    feed = client.get("/api/myfam", params={"interests": "science,health"}).json()
    made_for_you = [s for s in feed["sections"] if s["key"] == "from_history"][0]
    assert made_for_you["topics"]


def test_a_rubbish_interests_hint_costs_a_shelf_not_the_page(client):
    res = client.get("/api/myfam", params={"interests": "astrology,;;"})
    assert res.status_code == 200


# --- the recap is gone from the API too -----------------------------------


def test_nothing_serves_a_weekly_recap_any_more(client, app_mod=None):
    """Removed rather than left unread, on the Piper reasoning: an endpoint
    left behind is an invitation to draw a popup for it again."""
    import app as appmod
    paths = {getattr(route, "path", "") for route in appmod.app.routes}
    assert not [p for p in paths if "recap" in p], \
        "a recap route is still registered"


def test_the_preference_is_still_accepted_and_still_stored(client):
    """Read by nothing, and kept: it is what a scheduled digest would read on
    the day there is one, and dropping the column buys nothing."""
    sign_up(client)
    client.post("/api/preferences", json={"weekly_recap": False})
    assert client.get("/api/preferences").json()["weekly_recap"] is False


# --- what skip mode still gets -------------------------------------------


def test_skip_mode_can_still_hear_everything(client):
    """The line the gate must not cross. Nothing that leads to audio is gated,
    because a login in front of the first word breaks the one-sentence spec."""
    for path in ("/api/myfam", "/api/topics", "/api/explore", "/api/godeeper",
                 "/api/profile", "/api/nextup", "/api/explorenew", "/api/voices"):
        assert client.get(path).status_code == 200, f"{path} was gated"


def test_skip_mode_cannot_save_a_mix(client):
    assert client.get("/api/mixes").status_code == 401
    assert client.post("/api/mixes", json={"name": "Mine"}).status_code == 401


def test_signing_up_later_keeps_what_they_already_did(client):
    """accounts.py's whole design, restated as a gate consequence: skip mode
    then signup must not read as starting over."""
    client.post("/api/event", json={"kind": "complete", "topic_id": "ai-agents"})
    before = client.get("/api/auth/me").json()["user_id"]
    sign_up(client)
    assert client.get("/api/auth/me").json()["user_id"] == before
    # The completion they recorded before signing up is still theirs, which is
    # what "signing up does not start you over" means. Read off the feed,
    # which is what reads the log now.
    assert client.get("/api/myfam").json()["personalised"] is True


# --- which interests reach a profile --------------------------------------


def test_hiding_an_interest_changes_the_profile_and_not_the_ranking(client):
    """"Which topics they choose to publicly share", from the edit screen.

    It must not touch the ranker. An interest is a statement about what to
    play; hiding it is a statement about a screen, and conflating the two
    would quietly change somebody's feed when they tidied their profile."""
    sign_up(client)
    client.post("/api/preferences", json={"interests": ["tech", "sports", "money"]})
    client.post("/api/preferences", json={"hidden_interests": ["sports"]})

    stored = client.get("/api/preferences").json()
    assert stored["interests"] == ["tech", "sports", "money"], \
        "hiding an interest removed it"
    assert stored["hidden_interests"] == ["sports"]
    assert stored["public_interests"] == ["tech", "money"]

    # And the feed still ranks on all three - the ranker reads `interests`.
    feed = client.get("/api/myfam").json()
    assert feed["personalised"] is True


def test_nothing_hidden_means_everything_shared(client):
    """Which is what every row written before this column existed says. The
    shared set stored instead would default every profile in the app to an
    empty pill row that reads as broken."""
    sign_up(client)
    client.post("/api/preferences", json={"interests": ["tech", "money"]})
    body = client.get("/api/preferences").json()
    assert body["hidden_interests"] == []
    assert body["public_interests"] == ["tech", "money"]


def test_an_unknown_facet_cannot_be_hidden(client):
    """Refused the same way an unknown interest is, by the same cleaner.
    Hiding something that is not a facet is a client bug, and one that
    silently stored would make the editor's pills disagree with the store."""
    sign_up(client)
    r = client.post("/api/preferences", json={"hidden_interests": ["astrology"]})
    assert r.status_code == 400
    assert client.get("/api/preferences").json()["hidden_interests"] == []


# --- named subjects from the catalogue ------------------------------------


def test_a_chosen_subject_is_stored_and_read_back(store):
    """The plus button's whole job: it has to still be there tomorrow."""
    store.save("u", topics=["ai", "movies-tv"])
    assert store.get("u").topics == ("ai", "movies-tv")


def test_subjects_and_interests_are_separate_vocabularies(store):
    """Two columns on purpose. `interests` is the eight pickable facets;
    the catalogue names subjects those eight cannot say."""
    store.save("u", interests=["tech"], topics=["ai"])
    got = store.get("u")
    assert got.interests == ("tech",)
    assert got.topics == ("ai",)
    with pytest.raises(P.PreferenceError):
        store.save("u", interests=["ai"])          # a subject is not a facet
    with pytest.raises(P.PreferenceError):
        store.save("u", topics=["tech"])           # and a facet is not a subject


def test_an_unknown_subject_is_refused(store):
    """A stored id that names nothing contributes no tags, so it would sit on
    the profile looking chosen while the ranker ignored it."""
    with pytest.raises(P.PreferenceError):
        store.save("u", topics=["not-a-real-subject"])


def test_subjects_survive_a_partial_save(store):
    """The settings screens write one field at a time."""
    store.save("u", topics=["ai"])
    store.save("u", language="en")
    assert store.get("u").topics == ("ai",), "an unrelated save dropped them"


def test_every_catalogue_subject_can_be_chosen():
    """The picker offers the catalogue, so the store has to accept all of it."""
    ids = [i.id for i in T.INTEREST_CATALOGUE]
    assert P.clean_topics(ids) == tuple(ids)
    assert len(ids) > 40, "precondition: this is the long list, not the eight"


def test_the_plus_button_reaches_the_ranker_over_the_api(client):
    """End to end, the way the catalogue's plus button actually works.

    An anonymous listener's choices live in their browser, so they arrive as
    the same ranking hint the intro's answers use. The profile and the feed
    both have to honour them, or the button is a setting that changes nothing.
    """
    assert client.get("/api/profile").json()["subjects"] == []
    body = client.get("/api/profile?topics=ai").json()
    assert "tech" in body["subjects"], "a chosen subject belongs on the profile"

    feed = client.get("/api/myfam?topics=ai").json()
    assert feed["personalised"], "and it has to reach the feed"
    made = [s for s in feed["sections"] if s["key"] == "from_history"]
    assert made and made[0]["topics"], "'Made for you' should not be empty"


def test_the_profile_shows_at_most_four_subjects(client):
    many = "sports,money,health,world,culture,tech"
    subjects = client.get("/api/profile?interests=" + many).json()["subjects"]
    assert len(subjects) == 4, subjects


def test_a_malformed_subject_hint_costs_nothing(client):
    """A bad hint must never be what stops the page loading."""
    r = client.get("/api/profile?topics=not-real,also-not-real")
    assert r.status_code == 200
    assert r.json()["subjects"] == []
