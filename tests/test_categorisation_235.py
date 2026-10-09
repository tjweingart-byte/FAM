"""§235: the gaps between an episode and its picture.

* **A** the writer's category, given on two or more episodes and placed by
  nothing in the tree, is minted by the sweep - and an older episode that
  named it is placed by it from then on;
* **B** below every exact reader, the node a text is nearest by meaning;
* **C** a local branch, offered to the writer first on a town's question;
* **D** the scene writer paints a place's real geography, and a local
  subject as an ordinary town.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import categories as C  # noqa: E402
import category_audit  # noqa: E402
import category_near  # noqa: E402
import category_seed  # noqa: E402
import stories as S  # noqa: E402
import taste_vectors  # noqa: E402
import thumbnails as th  # noqa: E402
import topics as T  # noqa: E402


@pytest.fixture
def tree():
    t = T.category_tree()
    C.apply_seed(t)
    return t


# --------------------------------------------------------------------------
# A: the writer's unplaced category grows the tree
# --------------------------------------------------------------------------
def test_the_writers_words_become_a_phrase_the_tree_can_hold():
    assert C.writer_phrase("Strait of Hormuz") == "strait hormuz"
    assert C.writer_phrase("the Federal Reserve") == "federal reserve"
    assert C.writer_phrase("a 2026 fight") == "fight"
    assert C.writer_phrase("tariffs tankers treaties borders sanctions") == ""
    assert C.writer_phrase("of a") == ""


def test_a_category_given_on_two_episodes_is_a_subject(tree):
    category_audit.note("Iran's pressure in the Strait of Hormuz",
                        "Strait of Hormuz", "tap")
    exact = lambda w: S.resolve_category(w, near=False)  # noqa: E731
    assert C.writer_subjects(tree, exact) == [], "one episode is one choice"
    category_audit.note("Why tankers avoid the Strait of Hormuz",
                        "strait of hormuz", "tap")
    category_audit.note("Why tankers avoid the Strait of Hormuz",
                        "strait of hormuz", "prefetch")
    found = C.writer_subjects(tree, exact)
    assert [p for p, _ in found] == ["strait hormuz"]


def test_the_same_question_twice_is_one_episode(tree):
    for _ in range(3):
        category_audit.note("Strait of Hormuz explained", "Strait of Hormuz")
    assert C.writer_subjects(
        tree, lambda w: S.resolve_category(w, near=False)) == []


def test_a_category_the_tree_already_places_is_not_minted(tree):
    category_audit.note("The best new restaurants", "restaurants")
    category_audit.note("Where to eat this week", "Restaurants")
    assert C.writer_subjects(
        tree, lambda w: S.resolve_category(w, near=False)) == []


def test_the_sweep_mints_it_and_older_episodes_are_placed_by_it(tree):
    for q in ("Iran's pressure in the Strait of Hormuz",
              "Why tankers avoid the Strait of Hormuz"):
        category_audit.note(q, "Strait of Hormuz", "tap")
    assert S.resolve_category("Strait of Hormuz") == ""
    written = C.writer_subjects(
        tree, lambda w: S.resolve_category(w, near=False))
    minted = C.promote(tree, [], written=written)
    assert [n.id for n in minted] == ["strait hormuz"]
    node = tree.get("strait hormuz")
    assert node.source == C.SOURCE_WRITER
    assert node.parent_id == "world", "under the facet its questions point at"
    # The words were stored as written, and the tree as it is now places them.
    assert S.resolve_category("Strait of Hormuz") == "strait hormuz"
    # Placed now: the next sweep has nothing to add.
    assert C.writer_subjects(
        tree, lambda w: S.resolve_category(w, near=False)) == []


def test_the_sweep_takes_what_the_writer_gave(tree):
    result = asyncio.run(C.sweep(tree, [], written=[("strait hormuz", "world")]))
    assert result["minted"] == 1
    assert tree.get("strait hormuz").parent_id == "world"


def test_a_story_subject_is_not_relabelled_as_the_writers(tree):
    minted = C.promote(tree, [], always=["strait hormuz"],
                       written=[("strait hormuz", "world")])
    assert [n.source for n in minted] == [C.SOURCE_SEARCH]


# --------------------------------------------------------------------------
# B: the nearest node by meaning, beneath the phrases
# --------------------------------------------------------------------------
#: A deterministic stand-in for the sentence model: a text points along the
#: axis of the first concept word it holds.
_AXES = {"restaurants": 0, "dining": 0, "council": 1, "tankers": 2,
         "unrelated": 39}


def _encoder(texts):
    out = []
    for text in texts:
        vec = [0.0] * 40
        for word, axis in _AXES.items():
            if word in text.lower():
                vec[axis] = 1.0
                break
        else:
            # Anything else points its own way, so unrelated texts are apart.
            vec[3 + sum(map(ord, text)) % 36] = 1.0
        out.append(vec)
    return out


@pytest.fixture
def meaning():
    taste_vectors.set_encoder(_encoder)
    category_near.reset()
    yield
    taste_vectors.set_encoder(None)
    category_near.reset()


def test_with_no_model_nothing_is_placed_by_meaning(tree):
    assert category_near.nearest("River Road's dining") == ""
    assert S.resolve_category("River Road dining", near=True) == ""


def test_words_no_phrase_matches_land_on_the_nearest_node(tree, meaning):
    words = "Fair Haven's River Road dining"
    assert S.resolve_category(words) == "", "the sweep's reader stays exact"
    near = S.resolve_category(words, near=True)
    assert near in ("restaurants", "fine dining", "local restaurants"), near
    assert T._root_facet(near) == "culture"


def test_an_exact_phrase_always_outranks_meaning(tree, meaning):
    assert S.resolve_category("the town council and its food",
                              near=True) == "town council"


def test_nothing_near_enough_is_nothing(tree, meaning):
    assert S.resolve_category("an unrelated question", near=True) == ""


def test_a_facet_given_keeps_the_answer_under_it(tree, meaning):
    assert category_near.nearest("River Road dining", facet="world") == ""
    assert T._root_facet(
        category_near.nearest("River Road dining", facet="culture")) == "culture"


def test_a_tile_with_no_phrase_wears_the_nearest_nodes_picture(tree, meaning,
                                                              monkeypatch):
    near = category_near.nearest("Fair Haven's River Road dining")
    th.store().put(th.Thumb(near, th.STATUS_APPROVED, facet="culture",
                            mime="image/png"), b"img")
    th.store().put(th.Thumb("world", th.STATUS_APPROVED, facet="world",
                            mime="image/png"), b"globe")
    th._MEMO.clear()
    found = th.pick("Fair Haven's River Road dining", ("world",))
    assert found["node"] == near, "not the facet's globe"
    player = th.pick_for_player("Fair Haven's River Road dining",
                                tags=("world",))
    assert player["node"] == near


# --------------------------------------------------------------------------
# C: the local branch
# --------------------------------------------------------------------------
def test_every_local_node_is_seeded():
    seeded = {phrase for phrase, _ in category_seed.rows()}
    assert set(category_seed.LOCAL_NODES) <= seeded


def test_the_local_branch_sits_under_the_right_facets(tree):
    facets = {n: T._root_facet(n) for n in category_seed.LOCAL_NODES}
    assert facets["town council"] == "world"
    assert facets["local schools"] == "world"
    assert facets["food scene"] == "culture"
    assert facets["local restaurants"] == "culture"
    assert facets["local business"] == "business"
    assert facets["high school sports"] == "sports"


def test_a_towns_food_scene_is_a_phrase(tree):
    assert "food scene" in tree.match(
        "Fair Haven, New Jersey's River Road Food Scene")


def test_a_towns_question_is_offered_the_local_branch_first(tree):
    text = "What is opening on River Road in Fair Haven"
    lines = S.writer_vocabulary(text, town="Fair Haven, New Jersey, US")
    assert lines and "local news" in lines[0]
    assert any("food scene" in line for line in lines)
    assert not any("food scene" in line
                   for line in S.writer_vocabulary(text)), "only for a town"


def test_the_writer_prompt_carries_it_when_the_brief_names_a_town(tree):
    import episode_intelligence as EI
    import script_generator as G

    plan = G.plan_episode("What is new in Fair Haven", 2)
    plan.brief = EI.Brief(place="Fair Haven, New Jersey, US")
    assert "local news" in G.build_prompt(plan)


# --------------------------------------------------------------------------
# D: the scene writer
# --------------------------------------------------------------------------
def test_the_scene_writer_paints_geography_and_never_names_it():
    for words in ("real geography", "narrow strait", "oil tankers",
                  "without writing the name", "Never a flag"):
        assert words in th.WRITER_SYSTEM
    assert "person, character or country" in th.WRITER_SYSTEM, (
        "and still names no country")


def test_the_scene_writer_paints_a_local_subject_as_a_town():
    for words in ("main street", "town hall", "Never a globe"):
        assert words in th.WRITER_SYSTEM


def test_a_title_still_being_embedded_is_not_remembered_as_a_miss(tree,
                                                                  monkeypatch):
    """The picker never embeds on a page load (`inline=0`): a title whose
    vector is queued draws as before and is asked again next time."""
    th.store().put(th.Thumb("food scene", th.STATUS_APPROVED, facet="culture",
                            mime="image/png"), b"img")
    answers = iter([None, "food scene"])
    asked = []

    def lookup(text, facet="", inline=1):
        asked.append(inline)
        return next(answers)
    monkeypatch.setattr(category_near, "lookup", lookup)
    th._MEMO.clear()
    assert th.pick("River Road dining") is None
    assert th.pick("River Road dining")["node"] == "food scene"
    assert asked == [0, 0]
