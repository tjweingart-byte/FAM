"""The vocabulary that grows itself.

`topics.py` has always ranked in a hand-written vocabulary: eight facets,
twenty-nine subtags, thirty-seven keyword lists somebody typed. That is two
levels deep, and three separate mechanisms exist in that module purely to work
around things it cannot say - `SUBTAG_WEIGHT`, `BROAD_MATCH_PENALTY` and
`familiar_words`, the last of which gives up on the vocabulary entirely and
reads the listener's raw searches instead. When the fix for "recommend me
better" is to stop using the vocabulary, the vocabulary is the problem.

These tests hold four lines, and they are what make a self-growing vocabulary
safe rather than merely clever:

* **A node is a subject, not a sentence fragment.** Every sub-span of a phrase
  is seen by exactly the people who saw the phrase, so a listener threshold
  alone mints ten nodes for one four-word run.
* **Nothing here may call a model on a read path.** Minting happens in the
  background sweep; `match` is a word-set intersection.
* **It can add resolution and can never subtract availability.** No key, a
  timeout, a refusal, a malformed path: the feed falls back to the vocabulary
  that shipped before this existed.
* **A node never rewrites history.** Events keep the tags they were written
  with; what changes is that their *text* becomes readable in a vocabulary
  that did not exist when they were written.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import categories as C  # noqa: E402
import topics as T  # noqa: E402


@pytest.fixture
def tree(tmp_path):
    return C.CategoryStore(str(tmp_path / "categories.db"))


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """The tree `topics` reads is process-global. Point it somewhere empty so
    one test's vocabulary cannot rank another test's feed."""
    T.reset_category_tree()
    C.reset_sweep()
    monkeypatch.setenv("CATEGORIES_DB", str(tmp_path / "topics-tree.db"))
    yield
    T.reset_category_tree()
    C.reset_sweep()


def _asked(query: str, listeners: int, at: float = 1000.0):
    return [(f"u{i}", query) for i in range(listeners)]


# --- the tree has no depth limit -------------------------------------------


def test_the_tree_goes_as_deep_as_it_needs_to(tree):
    """The whole point. `sports -> american football -> nfl -> cincinnati
    bengals` is four levels and nothing here cares that it is four."""
    tree.mint("american football", parent_id="sports")
    tree.mint("nfl", parent_id="american football")
    tree.mint("cincinnati bengals", parent_id="nfl")
    assert tree.get("cincinnati bengals").depth == 3
    assert tree.ancestors("cincinnati bengals") == [
        "nfl", "american football", "sports"]


def test_a_match_carries_its_whole_ancestry(tree):
    """The rule `tags_for_text` already keeps for subtags: an episode about
    the Bengals is also about the NFL, American football and sport."""
    tree.mint("american football", parent_id="sports")
    tree.mint("nfl", parent_id="american football")
    tree.mint("cincinnati bengals", parent_id="nfl")
    assert set(tree.match("how did the cincinnati bengals do")) == {
        "cincinnati bengals", "nfl", "american football", "sports"}


def test_a_deeper_node_is_a_sharper_claim():
    """`SUBTAG_WEIGHT` at one level, generalised. This is the thing the
    reported complaint needed and the vocabulary could not express."""
    tree = T.category_tree()
    tree.mint("american football", parent_id="sports")
    tree.mint("nfl", parent_id="american football")
    tree.mint("cincinnati bengals", parent_id="nfl")
    assert T.tag_weight("sports") == 1.0
    assert (T.tag_weight("cincinnati bengals") > T.tag_weight("nfl")
            > T.tag_weight("american football") > T.tag_weight("sports"))


def test_an_orphan_scores_as_a_subtag_and_not_as_a_facet():
    """A phrase enough listeners searched for is at least as specific as a
    hand-written subtag. What it lacks is a home, not specificity."""
    T.category_tree().mint("bengals")
    assert T.tag_weight("bengals") == T.SUBTAG_WEIGHT


def test_a_cycle_is_refused_rather_than_created(tree):
    """A model returning "a is under b" for a b already under a is an
    ordinary thing for a model to do, and it is not worth a hung page."""
    tree.mint("nfl", parent_id="sports")
    tree.mint("bengals", parent_id="nfl")
    assert tree.reparent("nfl", "bengals") is False
    assert tree.get("nfl").parent_id == "nfl" or tree.get("nfl").parent_id == "sports"


def test_moving_a_node_moves_its_children_with_it(tree):
    """Depth is stored rather than walked on read, so it has to be
    maintained. A node moved without its children is a tree that lies about
    how specific its leaves are."""
    tree.mint("nfl")
    tree.mint("bengals", parent_id="nfl")
    tree.mint("american football", parent_id="sports")
    tree.reparent("nfl", "american football")
    assert tree.get("nfl").depth == 2
    assert tree.get("bengals").depth == 3


# --- a node is a subject, not a fragment -----------------------------------


def test_a_fragment_that_only_lives_inside_a_phrase_is_not_a_subject(tree):
    """The failure the first real tree showed: eight queries produced
    thirty-nine nodes, of which "reserve interest rate" was typical."""
    texts = (_asked("the federal reserve interest rate decision", 5)
             + _asked("what the federal reserve said about inflation", 5))
    minted = {n.id for n in C.promote(tree, texts)}
    assert "federal reserve" in minted
    assert "reserve interest rate" not in minted
    assert "federal reserve interest" not in minted


def test_a_subject_needs_more_than_one_wording(tree):
    """Forty people asking the identical question is one phrasing, and a
    phrase that only ever appears in one phrasing is a piece of a sentence."""
    minted = {n.id for n in C.promote(tree, _asked("premier league title race", 9))}
    assert "league title" not in minted and "title race" not in minted


def test_one_enthusiast_does_not_widen_everybody_s_vocabulary(tree):
    """Below the threshold somebody's interest is still served -
    `familiar_words` reads their raw searches - it just does not get to add a
    word to the ranking vocabulary for everyone else."""
    texts = [("solo", "competitive dog grooming results"),
             ("solo", "how competitive dog grooming is judged")]
    assert C.promote(tree, texts) == []


def test_a_time_word_is_never_a_subject(tree):
    """"last night" is when something happened. Three people asking about
    three unrelated things last night would have minted it as a branch."""
    texts = (_asked("what happened with the bengals last night", 5)
             + _asked("the bengals result last night", 5))
    assert "last night" not in {n.id for n in C.promote(tree, texts)}


def test_a_phrase_with_a_number_in_it_is_not_minted(tree):
    """"week 5" ages badly in a way a subject does not: next season the node
    is still there and still matching."""
    assert tree.mint("week 5") is None


def test_the_hand_written_vocabulary_owns_its_own_names(tree):
    """`chips` is a subtag slug and also a phrase people type. Minting it
    would put two entries under one key in the profile `taste` builds."""
    assert tree.mint("chips") is None
    assert tree.mint("sports") is None
    assert tree.mint("sports drama") is None, "a subtag slug, normalised"


def test_a_story_subject_bypasses_the_thresholds(tree):
    """It has already been judged worth composing a tile about, from four
    live sources, by the layer whose job is deciding what is being widely
    reported. That is a stronger statement than three people typing."""
    minted = {n.id for n in C.promote(tree, [], always=["undersea cables"])}
    assert "undersea cables" in minted


# --- the levels nobody typed -----------------------------------------------


def test_a_model_path_mints_the_levels_nobody_searched_for(tree):
    """The only way "American Football" and "NFL" exist. No amount of reading
    what listeners wrote invents a level none of them wrote."""
    tree.mint("cincinnati bengals", source=C.SOURCE_SEARCH)
    C.apply_paths(tree, [{"subject": "cincinnati bengals",
                          "path": ["sports", "american football", "nfl",
                                   "cincinnati bengals"]}])
    assert tree.get("american football").source == C.SOURCE_MODEL
    assert tree.get("cincinnati bengals").depth == 3


def test_a_path_that_does_not_start_at_a_root_is_dropped(tree):
    """Guessing which end was wrong is how a tree acquires a branch nobody
    can explain."""
    tree.mint("cincinnati bengals")
    C.apply_paths(tree, [{"subject": "cincinnati bengals",
                          "path": ["football", "cincinnati bengals"]}])
    assert tree.get("cincinnati bengals").parent_id == ""


def test_a_path_cannot_invent_a_subject_nobody_searched_for(tree):
    """One malformed reply must not be able to add subjects to everybody's
    vocabulary - which is the one thing the listener threshold exists for."""
    C.apply_paths(tree, [{"subject": "dressage", "path": ["sports", "dressage"]}])
    assert tree.get("dressage") is None


def test_a_later_sighting_does_not_drag_a_placed_node_back_up(tree):
    """Once a model has put something under "American Football", a keyless
    sighting must not move it to `sports`."""
    tree.mint("bengals", parent_id="nfl", source=C.SOURCE_MODEL)
    tree.mint("bengals", parent_id="sports", source=C.SOURCE_SEARCH)
    assert tree.get("bengals").parent_id == "nfl"


# --- availability ----------------------------------------------------------


def test_no_key_still_grows_a_real_tree(tree):
    """The rule `episode_intelligence` is built on, applied to a vocabulary:
    this layer may add resolution and may never take the page away."""
    texts = (_asked("what nvidia is doing with data centre chips", 5)
             + _asked("why nvidia is worth so much", 5))
    result = asyncio.run(C.sweep(tree, texts))
    assert result["minted"] >= 1 and result["placed"] == 0
    assert tree.get("nvidia") is not None
    assert tree.get("nvidia").degraded, "and it says which half placed it"


def test_the_sweep_is_the_only_thing_that_can_call_a_model():
    """Everything a browse page touches has to be free. `match`, `ancestors`
    and `tag_weight` are read on every tile of every feed."""
    for fn in (C.CategoryStore.match, C.CategoryStore.ancestors,
               C.CategoryStore.depth_of, C.promote, C.phrases_in, T.tag_weight):
        source = inspect.getsource(fn)
        for forbidden in ("await", "messages.create", "client"):
            assert forbidden not in source, f"{fn.__name__} reaches for a model"


def test_a_tree_that_cannot_be_opened_costs_resolution_and_not_the_feed(
        monkeypatch):
    monkeypatch.setattr(C, "CategoryStore",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")))
    T.reset_category_tree()
    assert T.tags_for_text("the cincinnati bengals") == ()
    assert T.tag_weight("anything") == 1.0


def test_the_sweep_never_raises(tree, monkeypatch):
    monkeypatch.setattr(C, "promote",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")))
    assert asyncio.run(C.sweep(tree, []))["failed"] is True


# --- history ---------------------------------------------------------------


def test_a_new_node_makes_old_history_legible_without_rewriting_it():
    """A vocabulary that only applied to events written after it appeared
    would take a month to be worth anything to anybody already here."""
    tree = T.category_tree()
    tree.mint("nfl", parent_id="sports")
    tree.mint("cincinnati bengals", parent_id="nfl")
    # Written before the node existed: its stored tags say only `sports`.
    event = T.Event("u1", "complete", "", "how did the cincinnati bengals do",
                    ("sports",), 1000.0)
    profile = T.taste([event], 1000.0)
    assert "cincinnati bengals" in profile
    assert event.tags == ("sports",), "the log itself is untouched"


def test_an_impression_never_mints_a_node(tmp_path):
    """The feed's own tiles must not be allowed to mint the vocabulary the
    feed is ranked in - the impression rule arriving through another door."""
    store = T.EventStore(str(tmp_path / "e.db"))
    store.record_impressions("u1", [("from_history", "chip-supply")], at=1000.0)
    assert store.subject_texts(0) == []


def test_a_node_nothing_matches_ages_out(tree):
    """A vocabulary that only grows ends up ranking on what people were
    interested in two years ago."""
    tree.mint("bengals", at=1000.0)
    assert tree.prune(1000.0 + C.NODE_TTL + 1) == 1
    assert tree.get("bengals") is None


def test_a_node_holding_a_level_up_is_never_pruned(tree):
    """Removing it would orphan everything below it into a flat list."""
    tree.mint("nfl", at=1000.0)
    tree.mint("bengals", parent_id="nfl", at=1e12)
    assert tree.prune(1000.0 + C.NODE_TTL + 1) == 0
