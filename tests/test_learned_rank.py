"""The fitted order of Made for you (§131, `learned_rank.py`,
`tools/learn_rank.py`).

What has to hold, most important first:

* with no stored model - every deployment until somebody trains one - the
  ranking is exactly what it was;
* a model re-orders what cleared the floor and **never admits a tile under
  it** (it does decide which eligible tiles are visible - that is what an
  order is);
* a model that did not beat the hand-tuned order is never served, and
  neither is one fitted to a different feature set;
* training is point in time: an offer never sees its own tap;
* a wipe takes the model with the log it was fitted to.
"""
from __future__ import annotations

import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import learned_rank as LR  # noqa: E402
import topics as T  # noqa: E402
import learn_rank as tool  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_cache():
    LR.reset()
    T.reset_engagement()
    yield
    LR.reset()
    T.reset_engagement()


def store_at(tmp_path, name="e.db"):
    return T.EventStore(str(tmp_path / name))


def model(weights, beat=True):
    return LR.Model(list(weights), 0.0, [0.0] * 6, [1.0] * 6,
                    {"trained_at": 1234.0, "beat_hand": beat})


# --- the arithmetic ---------------------------------------------------------


def test_auc():
    assert LR.auc([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1]) == 1.0
    assert LR.auc([0.9, 0.8, 0.2, 0.1], [0, 0, 1, 1]) == 0.0
    assert LR.auc([0.5, 0.5, 0.5, 0.5], [0, 1, 0, 1]) == 0.5
    assert LR.auc([1, 2, 3], [1, 1, 1]) == 0.5  # nothing to compare


def test_fit_finds_the_signal():
    rng = random.Random(1)
    rows, labels = [], []
    for _ in range(600):
        live = float(rng.random() < 0.5)
        row = [rng.random(), 0.0, rng.random(), 0.0, live, 0.0]
        rows.append(row)
        labels.append(rng.random() < (0.6 if live else 0.1))
    fitted = LR.fit(rows, labels)
    weights = dict(zip(LR.FEATURES, fitted.weights))
    assert weights["live"] > 0.5
    assert abs(weights["affinity"]) < 0.3
    assert weights["semantic"] == 0.0  # no variance, no weight


def test_train_refuses_thin_data():
    model_, report = LR.train([[0.0] * 6] * 50, [False] * 50)
    assert model_ is None and "too few offers" in report["verdict"]


def test_a_model_that_cannot_beat_the_hand_order_says_so():
    """When the hand-tuned score already ranks perfectly there is nothing to
    beat, and the model is marked as not having beaten it."""
    rng = random.Random(2)
    rows, labels = [], []
    for _ in range(800):
        affinity = rng.random()
        rows.append([affinity, 0.0, 0.0, 0.0, 0.0, 0.0])
        labels.append(affinity > 0.7)
    fitted, report = LR.train(rows, labels)
    assert report["auc_hand"] == 1.0
    assert fitted is not None and fitted.meta["beat_hand"] is False


def test_a_model_round_trips_and_refuses_other_features():
    m = model([1, 2, 3, 4, 5, 6])
    back = LR.Model.from_json(m.to_json())
    assert back.weights == m.weights and back.meta["beat_hand"] is True
    import json
    data = json.loads(m.to_json())
    data["features"] = ["affinity", "something_else"]
    with pytest.raises(ValueError):
        LR.Model.from_json(json.dumps(data))


# --- serving ----------------------------------------------------------------


def test_no_model_is_no_change(tmp_path):
    store = store_at(tmp_path)
    assert LR.active(store) is None
    assert store.algo_stamp() == T.ALGO_VERSION
    assert LR.describe(store)["active"] is False


def test_a_losing_or_unreadable_model_is_never_served(tmp_path):
    store = store_at(tmp_path)
    store.save_learned_model(model([1] * 6, beat=False).to_json())
    assert LR.active(store) is None
    assert "did not beat" in LR.describe(store)["reason"]
    store.save_learned_model("{not json")
    LR.reset()
    assert LR.active(store) is None


def test_switched_off_means_off(tmp_path, monkeypatch):
    store = store_at(tmp_path)
    store.save_learned_model(model([1] * 6).to_json())
    assert LR.active(store) is not None
    monkeypatch.setenv("LEARNED_RANK", "0")
    LR.reset()
    assert LR.active(store) is None


def test_it_reorders_and_never_changes_membership():
    profile = {"money": 1.0, "sports": 0.5, "tech": 0.2}
    pool = list(T.TOPIC_BANK)
    hand = T.rank_from_history(profile, set(), candidates=pool, limit=100)
    # A model that likes exactly what the hand order likes least.
    contrary = model([-5.0, 0, 0, 0, 0, 0])
    learned = T.rank_from_history(profile, set(), candidates=pool, limit=100,
                                  learned=contrary)
    assert {t.id for t in learned} == {t.id for t in hand}
    affinities = [T._affinity(t, profile) for t in learned]
    assert affinities == sorted(affinities), "the model's order, not the hand's"
    assert [t.id for t in learned] != [t.id for t in hand]


def test_the_stamp_names_the_model(tmp_path):
    store = store_at(tmp_path)
    store.save_learned_model(model([1] * 6).to_json())
    assert store.algo_stamp() == T.ALGO_VERSION + "+lr1234"
    store.record_impressions("u", [("from_history", "fed-next-move")], at=5.0)
    assert store.impressions_for("u")[0].algo == T.ALGO_VERSION + "+lr1234"


def test_a_wipe_takes_the_model(tmp_path):
    store = store_at(tmp_path)
    store.save_learned_model(model([1] * 6).to_json())
    assert store.learned_model()
    store.clear()
    assert store.learned_model() == ""
    assert LR.active(store) is None


# --- training ---------------------------------------------------------------


HOUR = 3600.0


def test_an_offer_never_sees_its_own_tap(tmp_path):
    """Point in time. The play being predicted is the only play this tile has
    ever had; if any feature were read as of today it would already show it -
    the profile liking the tile, or the tile's tap rate."""
    store = store_at(tmp_path)
    _with_history(store, "u")
    tile = T.BANK_BY_ID["fed-next-move"]
    store.record_impressions("u", [("from_history", tile.id)], at=10 * HOUR)
    store.record(T.Event("u", "play", tile.id, "", tile.tags, 10 * HOUR + 60))
    rows, labels, counts = tool.training_rows(store, now=20 * HOUR, since=0)
    assert counts["used"] == 1 and labels == [True]
    affinity, _sem, fatigue, engagement, _live, _broad = rows[0]
    before = T.taste([T.Event("u", "search", "", BROAD_HISTORY,
                              T.tags_for_text(BROAD_HISTORY), 1.0)], 10 * HOUR)
    assert affinity == pytest.approx(T._affinity(tile, before))
    assert engagement == 0.0 and fatigue == 0.0


#: A history broad enough that every bank tile clears `RELEVANCE_FLOOR`, with
#: affinities spread from ~0.6 to ~2.2 - so the hand-tuned order has a real
#: opinion about every offer, which is what the model has to beat.
BROAD_HISTORY = ("football transfer money stock market business startup ai "
                 "tech health sleep film music science space world election")


def _with_history(store, user):
    store.record(T.Event(user, "search", "", BROAD_HISTORY,
                         T.tags_for_text(BROAD_HISTORY), 1.0))


def _synthetic_log(store, clicky, seed=3, section="from_history"):
    """Listeners whose tags like every tile, offered them on Made for you, who
    tap the `clicky` ones - chosen from the tiles the tags score *lowest* -
    far more than the rest. The hand-tuned order ranks by affinity with
    engagement as a bounded multiplier, so it keeps putting the high-affinity
    tiles first; a model can learn that the tap rate matters more here. That
    is the case a fitted order exists for: the hand weights, not the signals,
    are wrong."""
    rng = random.Random(seed)
    ids = [t.id for t in T.TOPIC_BANK]
    for hour in range(1, 160):
        for n in range(4):
            user = f"u{hour}-{n}"
            _with_history(store, user)
            shown = rng.sample(ids, 6)
            at = hour * HOUR
            store.record_impressions(user, [(section, i) for i in shown], at=at)
            for tid in shown:
                if rng.random() < (0.55 if tid in clicky else 0.04):
                    tile = T.BANK_BY_ID[tid]
                    store.record(T.Event(user, "play", tid, "", tile.tags, at + 60))


LOW_AFFINITY = {"morning-mindset", "anxiety-loop", "fed-next-move", "golf-evolution"}


def test_a_model_that_wins_is_stored_and_served(tmp_path):
    store = store_at(tmp_path)
    _synthetic_log(store, LOW_AFFINITY)
    now = 200 * HOUR
    rows, labels, counts = tool.training_rows(store, now=now, since=0)
    assert counts["unresolved"] == 0 and counts["under_floor"] == 0
    assert sum(labels) > 2 * LR.MIN_POSITIVES
    fitted, report = LR.train(rows, labels, now=now)
    assert report["auc_hand"] > 0.5, "the baseline has a real opinion here"
    assert report["beat_hand"], report
    weights = dict(zip(LR.FEATURES, fitted.weights))
    assert weights["engagement"] > 0
    store.save_learned_model(fitted.to_json(), now)
    assert LR.active(store, now) is not None


def test_offers_on_other_rails_are_not_training_rows(tmp_path):
    """The review finding this pins: the model only ever orders Made for you
    and the popup, so a Trending or crowd-row offer - never ranked by the
    hand-tuned score - must not be the ground it is judged on. They still
    count towards the tap rates, as serving's engagement table does."""
    store = store_at(tmp_path)
    _synthetic_log(store, LOW_AFFINITY, section="world_trending")
    rows, _labels, counts = tool.training_rows(store, now=200 * HOUR, since=0)
    assert rows == [] and counts["other_rails"] == counts["offers"]


def test_offers_under_the_floor_are_not_training_rows(tmp_path):
    """No history: every hand score is zero, the baseline is all ties, and a
    model would 'beat' it for free. Those offers could never have reached a
    model in serving, so they are not rows."""
    store = store_at(tmp_path)
    store.record_impressions("u", [("from_history", "fed-next-move")], at=HOUR)
    rows, _labels, counts = tool.training_rows(store, now=2 * HOUR, since=0)
    assert rows == [] and counts["under_floor"] == 1


def test_training_is_linear_at_a_realistic_size(tmp_path):
    """§122: a test that never runs at production scale inspects rather than
    verifies. Tens of thousands of offers is a month on a small deployment;
    the first version of this pass rescanned every earlier tap for every
    offer, which is fine on fifty rows and an afternoon on a real log."""
    import time as _time

    store = store_at(tmp_path)
    rng = random.Random(5)
    ids = [t.id for t in T.TOPIC_BANK]
    users = [f"u{i}" for i in range(120)]
    for user in users:
        _with_history(store, user)
    rows_written = 0
    for hour in range(1, 30 * 24, 4):
        for user in rng.sample(users, 10):
            shown = rng.sample(ids, 12)
            at = hour * HOUR
            store.record_impressions(user, [("from_history", i) for i in shown], at=at)
            rows_written += len(shown)
            for tid in shown:
                if rng.random() < 0.05:
                    store.record(T.Event(user, "play", tid, "",
                                         T.BANK_BY_ID[tid].tags, at + 60))
    started = _time.perf_counter()
    rows, _labels, counts = tool.training_rows(store, now=31 * 24 * HOUR, since=0)
    elapsed = _time.perf_counter() - started
    assert counts["used"] > 15000
    assert elapsed < 10.0, f"{counts['used']} offers took {elapsed:.1f}s"
