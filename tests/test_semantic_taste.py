"""The semantic term in Made for you (§128, `taste_vectors.py`).

Two halves, and the first is the one that has to hold everywhere: **with no
model installed the ranking is exactly the one that shipped before this
existed.** The second is that with a model, a tile the tags cannot tell apart
from its neighbours is found by what it means.

The suite never uses the real model - `conftest.py` points the model
directory somewhere empty - so the semantic cases install a tiny,
deterministic encoder: a bag of concepts, where each word maps to a concept
axis. Enough to make "the fed held rates" and "mortgage costs" neighbours and
"crypto" a stranger, which is the property under test.
"""
from __future__ import annotations

import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import embeddings  # noqa: E402
import taste_vectors as TV  # noqa: E402
import topics as T  # noqa: E402

CONCEPTS = {
    "fed": 0, "rates": 0, "rate": 0, "mortgage": 0, "housing": 0, "interest": 0,
    "crypto": 1, "bitcoin": 1, "coins": 1,
    "golf": 2, "masters": 2, "augusta": 2,
    "chips": 3, "nvidia": 3, "semiconductor": 3,
}


def concept_encoder(texts):
    out = []
    for text in texts:
        vec = [0.0] * 6
        for word in embeddings.tokens(text):
            if word in CONCEPTS:
                vec[CONCEPTS[word]] += 1.0
        vec[5] += 0.05  # never a zero vector
        out.append(TV.unit(vec))
    return out


@pytest.fixture
def encoder():
    TV.set_encoder(concept_encoder)
    yield
    TV.set_encoder(None)


def tile(tid, title, query, tags=("money",), freshness=0.0):
    return T.Topic(tid, title, "", query, tuple(tags), "x", freshness=freshness)


HOUSING = tile("housing", "What mortgage costs are doing", "why are mortgage rates where they are")
CRYPTO = tile("crypto", "Where bitcoin goes next", "what is moving crypto coins")
GOLF = tile("golf", "Augusta, explained", "what makes the masters different", tags=("sports",))


def searched(text, at=1000.0, user="u"):
    return T.Event(user, "search", "", text, T.tags_for_text(text), at)


# --- no model: nothing changes ----------------------------------------------


def test_the_suite_has_no_model():
    """The guard for everything below it: a developer's installed model must
    not rank the suite's feeds."""
    assert not embeddings.model_installed()
    assert TV.enabled() is False
    report = TV.describe()
    assert report["enabled"] is False
    assert "install_embed_model" in report["reason"]


def test_without_a_model_there_is_no_semantic_term():
    events = [searched("why did the fed hold rates")]
    assert TV.for_listener(events, [HOUSING, CRYPTO], now=2000.0) == {}


def test_an_empty_semantic_table_is_the_old_ranking():
    profile = {"money": 1.0, "sports": 0.4}
    pool = [HOUSING, CRYPTO, GOLF]
    before = T.rank_from_history(profile, set(), candidates=pool)
    after = T.rank_from_history(profile, set(), candidates=pool, semantic={})
    assert [t.id for t in before] == [t.id for t in after]


def test_switched_off_means_off(encoder, monkeypatch):
    monkeypatch.setenv("SEMANTIC_TASTE", "0")
    assert TV.describe() == {"enabled": False, "reason": "SEMANTIC_TASTE=0"}
    assert TV.for_listener([searched("fed rates")], [HOUSING], now=2000.0) == {}


# --- with a model: meaning separates what tags cannot -----------------------


def test_meaning_separates_two_tiles_the_tags_score_identically(encoder):
    profile = {"money": 1.0}
    assert T._affinity(HOUSING, profile) == T._affinity(CRYPTO, profile)
    events = [searched("why did the fed hold interest rates")]
    semantic = TV.for_listener(events, [HOUSING, CRYPTO], now=2000.0)
    assert semantic.get("housing", 0) > 0
    assert "crypto" not in semantic
    ranked = T.rank_from_history(profile, set(), candidates=[CRYPTO, HOUSING],
                                 semantic=semantic)
    assert [t.id for t in ranked] == ["housing", "crypto"]


def test_a_tile_the_tags_missed_can_clear_the_floor(encoder):
    """The reason the term is added rather than multiplied: a tile on a facet
    the profile does not hold scores zero on tags, and a multiplier on zero
    is zero."""
    profile = {"money": 1.0}
    chips = tile("chips", "Why Nvidia matters", "the semiconductor chips race",
                 tags=("tech",))
    assert T._affinity(chips, profile) == 0
    events = [searched("nvidia semiconductor chips")]
    semantic = TV.for_listener(events, [chips], now=2000.0)
    without = T.rank_from_history(profile, set(), candidates=[chips])
    with_ = T.rank_from_history(profile, set(), candidates=[chips], semantic=semantic)
    assert without == [] and [t.id for t in with_] == ["chips"]


def test_a_semantic_match_is_not_a_broad_match(encoder):
    """`BROAD_MATCH_PENALTY` is for a subject the listener was never near. A
    live story close to something they searched is exactly having been
    near it, so it is not damped."""
    profile = {"money": 1.0}
    story = tile("st-housing", "Mortgage costs this week",
                 "what the housing market did this week", freshness=0.5)
    far = tile("st-crypto", "Bitcoin this week", "what crypto coins did",
               freshness=0.5)
    events = [searched("fed interest rates")]
    semantic = TV.for_listener(events, [story, far], now=2000.0)
    ranked = T.rank_from_history(profile, set(), candidates=[far, story],
                                 semantic=semantic, floor=0.0)
    assert ranked[0].id == "st-housing"


def test_only_behaviour_is_history(encoder):
    """An impression must never become taste - here as everywhere else."""
    shown = T.Event("u", T.IMPRESSION, "housing", "", ("money",), 1000.0)
    assert TV.history([shown], 2000.0, T.EVENT_WEIGHT, T._decay, lambda _i: HOUSING) == []
    skip = T.Event("u", "skip", "housing", "", ("money",), 1000.0)
    assert TV.history([skip], 2000.0, T.EVENT_WEIGHT, T._decay, lambda _i: HOUSING) == []


def test_a_play_is_read_as_the_tile_it_played(encoder):
    played = T.Event("u", "play", "housing", "", ("money",), 1000.0)
    past = TV.history([played], 2000.0, T.EVENT_WEIGHT, T._decay,
                      {"housing": HOUSING}.get)
    assert past == [(TV.tile_text(HOUSING), 1.0)]


def test_every_surface_that_ranks_made_for_you_passes_it(encoder, tmp_path, monkeypatch):
    """The rail, its View more screen and the post-episode popup are one
    ranking (the rule `build_section` exists to keep), so all three have to
    hand it the same semantic term - one that forgot would be a second
    answer to the same question."""
    store = T.EventStore(str(tmp_path / "e.db"))
    store.record(searched("what is the fed doing about interest rates", at=1000.0))
    seen = []
    real = T.rank_from_history

    def spy(*args, **kwargs):
        seen.append(kwargs.get("semantic") or {})
        return real(*args, **kwargs)

    monkeypatch.setattr(T, "rank_from_history", spy)
    T.build_feed(store, "u", now=2000.0, floors={})
    T.build_section(store, "u", "from_history", now=2000.0, floors={})
    T.rank_next_up(store, "u", now=2000.0)
    assert len(seen) == 3
    for semantic in seen:
        assert semantic.get("fed-next-move", 0) > 0
    assert store.algo_stamp().endswith("+sem")


# --- the browse path never waits --------------------------------------------


def test_a_page_embeds_only_a_few_texts_itself(monkeypatch):
    calls = []
    done = threading.Event()

    def counting(texts):
        calls.append(len(texts))
        if len(calls) > 1:
            done.set()
        return concept_encoder(texts)

    TV.set_encoder(None)
    monkeypatch.setattr(TV, "_encoder", lambda: counting)
    texts = [f"text number {i}" for i in range(40)]
    got = TV.vectors(texts, inline=3)
    assert calls[0] == 3
    assert len(got) == 3
    assert done.wait(5), "the rest is embedded in the background"
    TV.reset()


def test_a_broken_encoder_is_no_term_not_an_error(monkeypatch):
    def broken(_texts):
        raise RuntimeError("onnx fell over")

    TV.set_encoder(broken)
    try:
        assert TV.for_listener([searched("fed rates")], [HOUSING], now=2000.0) == {}
    finally:
        TV.set_encoder(None)
