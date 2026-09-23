"""The second question: will they tap it.

`_affinity` has always answered *will this listener like it*. Nothing
answered *will they actually press it*, except `fatigue`, which can only say
no. This is the positive half, and the notable thing about it is that it
needed no new collection: every impression already carried the listener, the
tile and the rail, and every play already carried the listener and the tile.

The tests here are mostly about the ways this term could go wrong rather than
about the arithmetic, because the arithmetic is four lines and the failure
modes are what make it a decision:

* an engagement term optimised alone converges on whatever is most clickable
  for everybody, which is a row this page already has;
* a raw rate buries every new tile, because a new tile has no taps yet;
* and a ranking change nobody can measure afterwards is one nobody can
  defend, which is what `ALGO_VERSION` on every impression is for.
"""
from __future__ import annotations

import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import topics as T  # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    T.reset_engagement()
    yield
    T.reset_engagement()


@pytest.fixture
def store(tmp_path):
    return T.EventStore(str(tmp_path / "myfam.db"))


def _feed(store, topic_id, listeners, taps, at=1000.0):
    """Offer one tile to `listeners` people and have `taps` of them play it."""
    for i in range(listeners):
        user = f"{topic_id}-u{i}"
        store.record_impressions(user, [("from_history", topic_id)], at=at)
        if i < taps:
            store.record(T.Event(user, "play", topic_id, "", (), at=at + 60))


# --- the arithmetic --------------------------------------------------------


def test_nothing_measured_is_no_table_rather_than_a_table_of_ones():
    """An empty dict is what every caller already treats as "no evidence".
    A full one would hide the difference between a feed with no impressions
    and a feed where everything is exactly average."""
    assert T.engagement({}, 0, 0) == {}
    assert T.engagement({"a": (10, 0)}, 0, 0) == {}


def test_a_tile_nobody_has_seen_scores_exactly_one():
    table = T.engagement({"seen": (100, 20)}, 100, 20)
    assert table.get("never-shown", 1.0) == 1.0


def test_a_new_tile_is_not_buried_for_being_new():
    """The failure a raw rate would have shipped with: two impressions and no
    tap is 0% conversion, and 0% would put it at the floor forever."""
    table = T.engagement({"new": (2, 0), "old": (400, 56)}, 402, 56)
    assert table["new"] > 0.85


def test_enough_offers_with_nothing_taken_does_reach_the_floor():
    """The other side of the same coin - shrinkage is a slow start, not an
    exemption. Twenty occasions and no taps is a measurement."""
    shown = int(T.ENGAGEMENT_PRIOR) * 3
    table = T.engagement({"dud": (shown, 0), "rest": (400, 56)},
                         shown + 400, 56)
    assert table["dud"] == T.ENGAGEMENT_FLOOR


def test_the_multiplier_is_bounded_in_both_directions():
    """It reorders comparable tiles; it must never outvote what somebody
    actually wants."""
    table = T.engagement({"perfect": (200, 200), "dead": (200, 0)}, 400, 200)
    assert table["perfect"] == T.ENGAGEMENT_CEILING
    assert table["dead"] == T.ENGAGEMENT_FLOOR


def test_it_is_a_ratio_so_two_deployments_are_comparable():
    """A feed converting at 4% and one converting at 30% both produce
    multipliers around 1.0. What moves a tile is being better than its own
    feed, not an absolute number somebody has to tune per deployment."""
    quiet = T.engagement({"a": (1000, 40), "b": (1000, 40)}, 2000, 80)
    busy = T.engagement({"a": (1000, 300), "b": (1000, 300)}, 2000, 600)
    assert quiet["a"] == pytest.approx(1.0, abs=0.01)
    assert busy["a"] == pytest.approx(1.0, abs=0.01)


# --- the query -------------------------------------------------------------


def test_an_occasion_is_the_unit_not_a_render(store):
    """The same unit fatigue counts, so the positive and negative signals
    cannot disagree about what "shown" means."""
    for i in range(30):
        store.record_impressions("u1", [("from_history", "a")], at=1000.0 + i)
    totals, offered, _taken = store.engagement_totals()
    assert totals["a"][0] == 1 and offered == 1


def test_a_play_with_no_impression_behind_it_is_not_a_conversion(store):
    """Otherwise an episode somebody found by searching would be scored as a
    tile that converts, and the best-performing tiles in the feed would be
    the ones nobody reached through the feed."""
    store.record(T.Event("stranger", "play", "a", "", (), at=1000.0))
    totals, _o, taken = store.engagement_totals()
    assert taken == 0 and totals == {}


def test_a_play_before_the_impression_is_not_a_conversion(store):
    store.record(T.Event("u1", "play", "a", "", (), at=1000.0))
    store.record_impressions("u1", [("from_history", "a")], at=9000.0)
    totals, _o, taken = store.engagement_totals()
    assert totals["a"] == (1, 0) and taken == 0


def test_the_totals_recover_a_real_difference(store):
    _feed(store, "hot", listeners=100, taps=40)
    _feed(store, "cold", listeners=100, taps=2)
    totals, offered, taken = store.engagement_totals()
    table = T.engagement(totals, offered, taken)
    assert table["hot"] > 1.2 and table["cold"] < 0.8


# --- what it is allowed to touch -------------------------------------------


def test_the_crowd_row_never_reads_it():
    """That row is what everybody plays. A term for what everybody taps would
    make it that twice, and it would stop being identical for everyone -
    which is what makes it the cheapest section to serve."""
    source = inspect.getsource(T.rank_most_played)
    assert "engage" not in source


def test_the_missed_rail_never_reads_it():
    """Every tile on it is one this listener was already shown and did not
    take. A global "people pass this over" term is that same passing-over
    measured again, more widely."""
    assert "engage" not in inspect.getsource(T.rank_missed)


def test_the_friends_rail_never_reads_it():
    """It ranks what named people played. A global tap rate has no business
    reordering a fact about somebody's friends."""
    assert "engage" not in inspect.getsource(T.rank_friends)


def test_made_for_you_does_read_it(store):
    """The rail the term exists for."""
    profile = {"tech": 1.0}
    tiles = [T.Topic("a", "A", "", "a", ("tech",), "tech"),
             T.Topic("b", "B", "", "b", ("tech",), "tech")]
    plain = T.rank_from_history(profile, set(), candidates=tiles)
    lifted = T.rank_from_history(profile, set(), candidates=tiles,
                                 engage={"b": T.ENGAGEMENT_CEILING})
    assert [t.id for t in plain] == ["a", "b"]
    assert [t.id for t in lifted] == ["b", "a"]


def test_a_tile_nobody_taps_can_fall_out_of_a_rail_that_claims_relevance():
    """Applied before the floor on purpose: "Made for you" says these were
    chosen for this listener, and one nobody has ever pressed is a weak
    claim to that."""
    tile = T.Topic("a", "A", "", "a", ("tech",), "tech")
    profile = {"tech": T.RELEVANCE_FLOOR * 1.4}
    assert T.rank_from_history(profile, set(), candidates=[tile])
    assert not T.rank_from_history(profile, set(), candidates=[tile],
                                   engage={"a": T.ENGAGEMENT_FLOOR})


# --- it must never be what breaks the page ---------------------------------


def test_a_broken_store_costs_the_term_and_never_the_feed():
    """The rule every read in `topics.py` keeps, and the one this nearly
    broke: `engagement_for` read `store.path` before anything else, and a
    store too broken to have opened a file does not have one."""
    class Broken(T.EventStore):
        def __init__(self):
            pass

        def _conn(self):
            raise RuntimeError("disk gone")

    assert T.engagement_for(Broken()) == {}


def test_the_table_is_computed_once_for_the_deployment(store, monkeypatch):
    """One answer serves every listener, which is what makes it affordable on
    a browse path. Without the cache this is two aggregate queries per page
    load per listener."""
    calls = []
    real = store.engagement_totals
    monkeypatch.setattr(store, "engagement_totals",
                        lambda since=0.0: calls.append(1) or real(since))
    T.engagement_for(store, now=1000.0)
    T.engagement_for(store, now=1000.0 + T.ENGAGEMENT_CACHE_SECONDS / 2)
    assert len(calls) == 1
    T.engagement_for(store, now=1000.0 + T.ENGAGEMENT_CACHE_SECONDS + 1)
    assert len(calls) == 2


def test_two_databases_do_not_share_a_table(tmp_path):
    """A module-level number would let one test's feed be ranked by another
    test's log, and one deployment's by another's."""
    a = T.EventStore(str(tmp_path / "a.db"))
    b = T.EventStore(str(tmp_path / "b.db"))
    _feed(a, "hot", listeners=100, taps=90)
    assert T.engagement_for(a, now=2000.0)
    assert T.engagement_for(b, now=2000.0) == {}


# --- it has to be measurable afterwards ------------------------------------


def test_the_ranking_version_moved():
    """A ranking change that cannot be told apart from the one before it in
    the impression log is a ranking change nobody can judge. This is the
    assertion that fails if somebody adds a signal and forgets."""
    assert T.ALGO_VERSION == "2026-09-23.2"
