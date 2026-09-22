"""Who the evergreen bank is for, and what the crowd row is allowed to claim.

Three rules, all set at the owner's direction, and each one reverses something
this codebase had previously written down as deliberate:

1. **"What FAM can't stop listening to" holds plays and nothing else.** It
   used to top itself up from the bank so it was never empty (CLAUDE.md §124
   recorded that as a decision rather than an accident, precisely so undoing
   it had to be a decision too). The heading is a claim about this
   deployment's listeners, and twenty-eight tiles nobody has played do not
   support it.

2. **The bank is offered to a listener with no account, and to nobody else.**
   It is a first impression for somebody FAM knows nothing about and can keep
   nothing for. An account is where "show me what this is" stops being the
   question.

3. **The floor is swapped, not removed.** An account holder gets the startup
   set in the bank's place - eight time-anchored questions researched on the
   tap - so the generic tile they do get is about now, and so the page is
   never empty on a deployment with no live provider, which is every
   deployment today.

The thing worth testing here is not that `browse_inventory` returns a list.
It is that **every surface reads it** - §119's lesson, which this repo has
now paid for twice: a ladder with one definition and a caller still reading
the variable it replaced is a mechanism that is not built. So the tests below
go through `build_feed`, `build_section`, `rank_next_up` and the HTTP layer
rather than through the helper.
"""
from __future__ import annotations

import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import startup  # noqa: E402
import topics as T  # noqa: E402

BANK_IDS = {t.id for t in T.TOPIC_BANK}
STARTUP_IDS = {t.id for t in T.STARTUP_TOPICS}


@pytest.fixture
def store(tmp_path):
    return T.EventStore(str(tmp_path / "events.db"))


def play(store, user, topic_id, kind="play", ago=0.0):
    store.record(T.Event(user, kind, topic_id, "",
                         T.BANK_BY_ID[topic_id].tags, time.time() - ago))


def ids(feed, key):
    return [t["id"] for s in feed["sections"] if s["key"] == key
            for t in s["topics"]]


# --------------------------------------------------------------------------
# 1. the crowd row claims only what it can back
# --------------------------------------------------------------------------
def test_the_crowd_row_is_empty_until_somebody_plays(store):
    assert T.rank_most_played(store) == []
    feed = T.build_feed(store, "u")
    assert ids(feed, "most_played") == []


def test_the_crowd_rows_empty_sentence_is_about_this_app(store):
    """Not "nothing is popular", which is a claim about listeners this
    deployment has not got - §89, one rail over from where it was written."""
    reason = [s for s in T.build_feed(store, "u")["sections"]
              if s["key"] == "most_played"][0]["empty_reason"]
    assert reason
    assert "played" in reason.lower()


def test_one_play_fills_the_crowd_row(store):
    """Asserted on the ranker rather than on the page, because the page fills
    the personal rails first and one played topic is one the rail above may
    legitimately have claimed. What is being pinned is that the row is empty
    for want of plays and not for any other reason."""
    play(store, "stranger", "golf-evolution")
    assert [t.id for t in T.rank_most_played(store)] == ["golf-evolution"]


def test_the_crowd_row_never_reaches_past_what_was_played(store):
    """The whole of the change, stated as the invariant it is: whatever else
    is on the page, this row is a subset of the topics somebody played."""
    for topic_id in ("golf-evolution", "sleep-science", "chip-supply"):
        play(store, f"who-{topic_id}", topic_id)
    played = {"golf-evolution", "sleep-science", "chip-supply"}
    for has_account in (False, True):
        feed = T.build_feed(store, "u", has_account=has_account)
        assert set(ids(feed, "most_played")) <= played, has_account
        full = T.build_section(store, "u", "most_played",
                               has_account=has_account)
        assert {t["id"] for t in full["topics"]} <= played, has_account


# --------------------------------------------------------------------------
# 2 and 3. who the bank is for
# --------------------------------------------------------------------------
def test_a_guest_is_offered_the_bank(store):
    """With some listening behind them, so this is the warm ranker rather
    than the cold-start one. A guest who has done nothing at all gets the
    startup set leading the rail either way - that is §116 and it is not what
    this rule changed."""
    play(store, "guest", "golf-evolution", kind="complete")
    offered = set(ids(T.build_feed(store, "guest", has_account=False),
                      "from_history"))
    assert offered & BANK_IDS, "a listener with no account got no bank tiles"


def test_an_account_holder_is_never_offered_the_bank(store):
    play(store, "member", "golf-evolution", kind="complete")
    feed = T.build_feed(store, "member", has_account=True)
    for section in feed["sections"]:
        offered = {t["id"] for t in section["topics"]}
        assert not offered & BANK_IDS, (section["key"], offered & BANK_IDS)


def test_an_account_holders_floor_is_the_startup_set_not_an_empty_page(store):
    """The swap, and the reason it is a swap. Removing the bank on its own
    would empty this rail on any deployment with no live provider - which is
    every deployment today - and `WORLD_FLOOR` reserves its tiles on the
    stated premise that this rail has somewhere else to go."""
    play(store, "member", "golf-evolution", kind="complete")
    offered = set(ids(T.build_feed(store, "member", has_account=True),
                      "from_history"))
    assert offered, "Made for you went empty when the bank was withdrawn"
    assert offered <= STARTUP_IDS
    assert all(i.startswith(startup.ID_PREFIX) for i in offered), offered


def test_view_more_draws_the_same_inventory_as_the_rail(store):
    """Two surfaces, one ranking - so "View more" must not hand back the
    twenty-eight tiles the rail just decided this listener is not shown."""
    play(store, "member", "golf-evolution", kind="complete")
    full = T.build_section(store, "member", "from_history", has_account=True)
    assert not {t["id"] for t in full["topics"]} & BANK_IDS


def test_the_popup_obeys_the_same_rule_as_the_shelves(store):
    """It starts its first tile by itself after fifteen seconds, so it is the
    last surface that should get a back door into an inventory this listener
    has stopped being shown."""
    play(store, "member", "golf-evolution", kind="complete")
    picks = T.rank_next_up(store, "member", after_id="golf-evolution",
                           has_account=True)
    assert picks, "the popup came back empty"
    assert not {t.id for t in picks} & BANK_IDS


def test_a_guest_keeps_the_popups_bank(store):
    play(store, "guest", "golf-evolution", kind="complete")
    picks = T.rank_next_up(store, "guest", after_id="golf-evolution",
                           has_account=False)
    assert {t.id for t in picks} & BANK_IDS


# --------------------------------------------------------------------------
# the exemptions, which are decisions and not oversights
# --------------------------------------------------------------------------
def test_the_mix_picker_keeps_the_whole_bank(store):
    """A menu somebody opened is not the app offering them something - and a
    saved mix needs an account, so applying the rule here would empty the
    picker for exactly the listeners who can use it (§123, one screen over).
    A mix holds topic ids rather than audio, so a bank member in a mix is a
    fresh episode every morning and never a standing one replayed."""
    play(store, "member", "golf-evolution", kind="complete")
    ranked = T.rank_bank(T.taste(store.for_user("member")))
    assert {t.id for t in ranked} == BANK_IDS


def test_explore_new_keeps_the_bank(store):
    """Off the page entirely (`UNSHELVED`) and reached only by a listener who
    went looking to widen their taste. Widening it over eight questions whose
    facets this ranker mutes would leave nothing to widen into."""
    play(store, "member", "golf-evolution", kind="complete")
    picks = T.rank_might_like(T.taste(store.for_user("member")), exclude=set())
    assert {t.id for t in picks} & BANK_IDS


# --------------------------------------------------------------------------
# the wiring, which is the half that silently does not happen
# --------------------------------------------------------------------------
def test_the_default_is_the_generous_one():
    """A caller that has not been taught about accounts does not know, and the
    honest reading of "we do not know" is the cold-start one. Defaulting the
    other way would take the bank off every surface whose caller was never
    updated - and an emptier page is exactly the failure that looks like a
    design decision rather than a bug."""
    assert {t.id for t in T.browse_inventory([], has_account=False)} == BANK_IDS
    assert {t.id for t in T.browse_inventory([], has_account=True)} == STARTUP_IDS


def test_every_browse_entry_point_takes_the_flag():
    """Named one by one rather than swept, so adding a surface that offers
    episodes is a decision somebody writes down here."""
    import inspect
    for fn in (T.build_feed, T.build_section, T.rank_next_up):
        assert "has_account" in inspect.signature(fn).parameters, fn.__name__


def test_the_api_reads_the_account_and_not_a_parameter(monkeypatch, tmp_path):
    """`_has_account` comes off the session the server minted, like every
    other per-listener fact. A query string must never be able to ask for
    somebody else's inventory - the same rule `?user=` lost."""
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "EVENTS", T.EventStore(str(tmp_path / "a.db")))
    client = TestClient(appmod.app)

    # Warm, so this exercises the ordinary ranker rather than the cold-start
    # rail - a listener who has done nothing gets the startup set whichever
    # side of this rule they are on. The id comes off the session the server
    # minted, which is the whole point of the test below: there is no other
    # way to name this listener from out here.
    me = client.get("/api/auth/me").json()["user_id"]
    appmod.EVENTS.record(T.Event(me, "complete", "golf-evolution", "",
                                 T.BANK_BY_ID["golf-evolution"].tags,
                                 time.time()))

    def offered(url):
        body = client.get(url).json()
        return {t["id"] for s in body["sections"] for t in s["topics"]}

    assert offered("/api/myfam") & BANK_IDS, (
        "an anonymous session was not treated as a guest")
    assert offered("/api/myfam?has_account=1") & BANK_IDS, (
        "a query string changed the inventory")


def test_the_api_withholds_the_bank_from_an_account(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "EVENTS", T.EventStore(str(tmp_path / "b.db")))
    monkeypatch.setattr(appmod, "_has_account", lambda request: True)
    client = TestClient(appmod.app)

    body = client.get("/api/myfam").json()
    offered = {t["id"] for s in body["sections"] for t in s["topics"]}
    assert offered, "the page came back with nothing on it"
    assert not offered & BANK_IDS
