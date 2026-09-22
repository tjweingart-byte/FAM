"""The starter vocabulary: what it must be, and what it must not become.

`category_seed.py` is a floor under the ranking vocabulary for a deployment
that has no traffic yet. Two things have to hold for it to be worth having,
and a third for it to be safe:

* **every entry actually mints.** `categories.mint` returns None rather than
  raising for a phrase that is too long, contains a short word or a digit, or
  collides with the hand-written vocabulary - so a bad entry here is not an
  error, it is a node that silently never exists and a tree quietly smaller
  than the file claims. That failure is invisible in production and trivial
  to assert here.
* **it reaches the ranker.** A vocabulary nothing reads is a table. The join
  is `topics.topic_tags`, and before it existed the tree could see "college
  football" in a tile's question while `_affinity` scored that tile on
  `sports` alone.
* **it never blocks growth.** The seed is where the tree starts, not where it
  is held: a real sighting must still mint, and the model must still be able
  to move a seeded node without the next boot dragging it back.
"""
from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import categories as C  # noqa: E402
import category_seed as S  # noqa: E402
import topics as T  # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    store = C.CategoryStore(str(tmp_path / "categories.db"))
    monkeypatch.setattr(T, "_CATEGORIES", store)
    T.reset_topic_tags()
    yield store
    T.reset_topic_tags()


@pytest.fixture
def seeded(store):
    C.apply_seed(store)
    T.reset_topic_tags()
    return store


# --------------------------------------------------------------------------
# the file itself
# --------------------------------------------------------------------------
def test_every_seed_entry_is_mintable():
    """The failure this catches does not raise and does not log: `mint`
    returns None and the tree is one node smaller than the file says."""
    reserved = C._reserved_slugs()
    for phrase, _parent in S.rows():
        assert C.normalise(phrase) == phrase, f"{phrase!r} is not normalised"
        assert phrase not in reserved, f"{phrase!r} collides with a tag slug"
        words = phrase.split()
        assert len(words) <= C.MAX_PHRASE_WORDS, phrase
        for word in words:
            assert len(word) >= C.MIN_WORD, f"{phrase!r}: {word!r} is too short"
            assert not word.isdigit(), f"{phrase!r}: {word!r} is a number"


def test_the_whole_seed_actually_lands(store):
    assert C.apply_seed(store) == len(S.rows())
    assert len(store.nodes()) == len(S.rows())


def test_the_roots_are_the_facets_and_are_not_rows(seeded):
    """The eight facets are where the tree hangs from, not entries in it -
    `categories.py`'s own rule. A facet minted as a node would put two entries
    under one key in the profile `taste` builds."""
    facets = set(T.TAG_LABELS)
    assert not facets & set(seeded.nodes()), "a facet was minted as a node"
    for node in seeded.nodes().values():
        if node.depth == 1:
            assert node.parent_id in facets, (node.id, node.parent_id)


def test_it_covers_every_facet(seeded):
    """A cold start knows nothing about which of the eight a listener wants,
    so a seed that covered six would be quietly deciding that two kinds of
    listener get a blunter ranking."""
    covered = {n.parent_id for n in seeded.nodes().values() if n.depth == 1}
    assert covered == set(T.TAG_LABELS)


def test_it_has_the_levels_nobody_could_have_typed(seeded):
    """The middle of a branch is the valuable part: containment can only
    deepen a phrase under another phrase somebody searched for, so the
    intermediate levels are what a grown-from-nothing tree never gets."""
    assert seeded.report()["max_depth"] >= 2
    deep = [n for n in seeded.nodes().values() if n.depth >= 2]
    assert len(deep) >= 50, len(deep)


def test_a_seed_node_claims_no_listeners(seeded):
    """`MIN_LISTENERS` is the whole spam control. A seed that inflated the
    counts would be lying about the one number that decides what gets in."""
    for node in seeded.nodes().values():
        assert node.listeners == 0, node.id
        assert node.uses == 0, node.id
        assert node.source == C.SOURCE_SEED


def test_no_seed_node_claims_anything_about_the_world(seeded):
    """A category is the name of a subject, never a fact about one - §88 and
    §102 one layer further in. "federal reserve" is a subject; "fed cuts
    rates" would be a claim."""
    banned = {"wins", "won", "beat", "beats", "cuts", "cut", "rises", "rose",
              "falls", "fell", "record", "crisis", "dead", "dies", "wins"}
    for node in seeded.nodes().values():
        assert not (set(node.id.split()) & banned), node.id
        assert not any(c.isdigit() for c in node.id), node.id


# --------------------------------------------------------------------------
# it reaches the ranker
# --------------------------------------------------------------------------
def test_the_seed_tells_two_sports_tiles_apart(seeded):
    """The case that named the problem. Both tiles are `sports` in the
    hand-written vocabulary and nothing distinguishes them, which is exactly
    what `BROAD_MATCH_PENALTY` and `familiar_words` exist to work around."""
    nil = T.BANK_BY_ID["nil-arms-race"]
    golf = T.BANK_BY_ID["golf-evolution"]
    profile = {"college football": 1.0, "sports": 0.3}
    assert T._affinity(nil, profile) > T._affinity(golf, profile)

    profile = {"golf": 1.0, "sports": 0.3}
    assert T._affinity(golf, profile) > T._affinity(nil, profile)


def test_a_tile_carries_what_the_tree_finds_in_its_question(seeded):
    tags = T.topic_tags(T.BANK_BY_ID["nil-arms-race"])
    assert "college football" in tags
    assert "american football" in tags, "a node must bring its ancestors"
    assert set(T.BANK_BY_ID["nil-arms-race"].tags) <= set(tags), (
        "the declared tags must survive")


def test_an_empty_tree_leaves_every_tile_exactly_as_it_was(store):
    """The rule this whole layer lives by: it may add resolution and may
    never take the page away. A deployment with no tree ranks precisely as it
    did before any of this existed."""
    for topic in T.TOPIC_BANK:
        assert T.topic_tags(topic) == topic.tags


def test_the_declared_tags_are_never_dropped(seeded):
    for topic in list(T.TOPIC_BANK) + list(T.STARTUP_TOPICS):
        assert set(topic.tags) <= set(T.topic_tags(topic)), topic.id


def test_the_seed_sharpens_the_bank_rather_than_flattening_it(seeded):
    """A floor, stated as one: the seed may only add distinctions. If two
    tiles were already distinguishable they must stay so."""
    before = len({tuple(sorted(t.tags)) for t in T.TOPIC_BANK})
    after = len({T.topic_tags(t) for t in T.TOPIC_BANK})
    assert after >= before


def test_the_tile_memo_is_dropped_when_the_tree_changes(store):
    """A node minted by the sweep has to be visible on the next page, not at
    the next restart."""
    nil = T.BANK_BY_ID["nil-arms-race"]
    assert "college football" not in T.topic_tags(nil)
    store.mint("college football", parent_id="sports", source=C.SOURCE_SEARCH)
    assert "college football" in T.topic_tags(nil), "the memo went stale"


def test_reading_tiles_is_cheap_enough_for_a_browse_page(seeded):
    """A bound rather than a number, which is §122's lesson: nothing about
    correctness says how big the tree gets, and the last thing added to this
    path without measuring cost 134ms of a page load."""
    tiles = list(T.TOPIC_BANK) + list(T.STARTUP_TOPICS)
    started = time.perf_counter()
    for _ in range(50):
        for topic in tiles:
            T.topic_tags(topic)
    elapsed = time.perf_counter() - started
    assert elapsed < 0.5, f"{len(tiles)} tiles x 50 took {elapsed:.3f}s"


# --------------------------------------------------------------------------
# it never blocks growth
# --------------------------------------------------------------------------
def test_seeding_twice_adds_nothing(seeded):
    assert C.apply_seed(seeded) == 0
    assert len(seeded.nodes()) == len(S.rows())


def test_re_seeding_never_drags_a_placed_node_back(seeded):
    """Once the placer has moved something, the seed is history. This is the
    difference between a floor under the vocabulary and a ceiling on it."""
    # The seed puts `hollywood` under `film`. A placer reading real searches
    # is perfectly entitled to decide it belongs under `celebrity` instead,
    # and after that this file is a record of where the tree started.
    assert seeded.get("hollywood").parent_id == "film"
    assert seeded.reparent("hollywood", "celebrity")

    C.apply_seed(seeded)
    assert seeded.get("hollywood").parent_id == "celebrity"


def test_re_seeding_does_not_make_the_vocabulary_immortal(seeded):
    """`last_seen` must not move on a node the seed did not add, or a process
    restart would look exactly like somebody being interested in something
    and nothing would ever be pruned."""
    seeded.mint("passing fashion trend", parent_id="culture",
                source=C.SOURCE_SEARCH, at=100.0)
    C.apply_seed(seeded, at=time.time())
    assert seeded.get("passing fashion trend").last_seen == 100.0


def test_a_real_sighting_still_mints_over_the_seed(seeded):
    """The seed is a floor, not a ceiling: a phrase nobody declared gets in
    on its own merits exactly as it did before."""
    before = len(seeded.nodes())
    seeded.mint("cincinnati bengals", parent_id="american football",
                source=C.SOURCE_SEARCH, listeners=4)
    assert len(seeded.nodes()) == before + 1
    assert seeded.get("cincinnati bengals").source == C.SOURCE_SEARCH


def test_prune_keeps_the_seed_and_still_drops_what_went_quiet(seeded):
    """Two different questions. `NODE_TTL` asks whether a subject stopped
    being talked about, which is about an observation; a seed node was never
    an observation, and on the deployment it exists for every leaf of it
    looks stale by construction."""
    seeded.mint("forgotten subject", parent_id="culture",
                source=C.SOURCE_SEARCH, at=1.0)
    dropped = seeded.prune(now=C.NODE_TTL + 1000.0)
    assert dropped == 1
    assert seeded.get("forgotten subject") is None
    assert len(seeded.nodes()) == len(S.rows())


def test_the_report_separates_declared_from_learned(seeded):
    seeded.mint("cincinnati bengals", parent_id="sports",
                source=C.SOURCE_SEARCH, listeners=4)
    report = C.seed_report(seeded)
    assert report["seeded"] == len(S.rows())
    assert report["learned"] == 1
    assert report["seed_available"] == len(S.rows())
