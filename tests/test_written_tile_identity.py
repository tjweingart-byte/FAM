"""A written episode names and categorises its own tile (§189).

The owner's rule: the first listener of a live story may hear it under the
composer's title, written from headlines before anything was researched.
Once the episode is cached, every listener after them sees the title the
writer put on it, and the tile's picture, facet word and logged tags come
from the writer's `<<CATEGORY:>>` line - what was learned by the time the
episode was finished.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import cache as cache_mod  # noqa: E402
import categories as C  # noqa: E402
import script_generator as G  # noqa: E402
import thumbnails as th  # noqa: E402
import topics as T  # noqa: E402

QUERY = "What a chaotic press conference signals about the fight (Tyson Fury)"


@pytest.fixture
def tree():
    t = T.category_tree()
    C.apply_seed(t)
    return t


@pytest.fixture
def written(monkeypatch):
    """A memory cache holding the Fury-Joshua episode, as a writer left it."""
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", cache_mod.MemoryScriptCache())

    def put(query=QUERY, **extra):
        key = appmod._episode_key(appmod._validated_plan(query, 2))
        appmod.SCRIPT_CACHE.put(key, ["A sentence."], 600, query, "", 2, "",
                                "", "", **extra)
        return key
    return put


# --------------------------------------------------------------------------
# the writer's line
# --------------------------------------------------------------------------
def test_the_writer_is_asked_for_a_category_and_it_is_never_spoken():
    assert "<<CATEGORY:" in G.SYSTEM_PROMPT
    text = ("Fury and Joshua met in Riyadh. <<TITLE: Fury and Joshua's Riyadh "
            "Standoff>> <<SUMMARY: Why the press conference mattered.>> "
            "<<CATEGORY: heavyweight boxing>> <<NEXT: who is favoured>>")
    assert G.extract_category(text) == "heavyweight boxing"
    assert "CATEGORY" not in G.clean_for_speech(text)
    assert "boxing" not in G.clean_for_speech(text)


def test_the_category_is_kept_beside_the_script(tmp_path):
    for store in (cache_mod.MemoryScriptCache(),
                  cache_mod.SqliteScriptCache(str(tmp_path / "s.db"))):
        store.put("k", ["A sentence."], 600, "q", category="heavyweight boxing")
        assert store.category("k") == "heavyweight boxing"
        # A re-write that brings no category keeps the one it has, as a
        # title does.
        store.put("k", ["A sentence."], 600, "q")
        assert store.category("k") == "heavyweight boxing"


# --------------------------------------------------------------------------
# the tile, for every listener after the first
# --------------------------------------------------------------------------
def _story_tile(cached=True):
    return {"id": "story-fury", "source": "GNews", "query": QUERY,
            "title": "Fury and Joshua Face Off",
            "angle": "What a chaotic press conference signals",
            "tags": ["science", "sports"], "cached": cached,
            "thumb": "", "thumb_facet": ""}


def test_a_written_story_tile_takes_the_writers_title(tree, written):
    written(title="Fury and Joshua's Riyadh Standoff",
            summary="Why one press conference reset the heavyweight fight.",
            category="heavyweight boxing")
    tile = _story_tile()
    appmod._name_written_tiles([tile], 2)
    assert tile["title"] == "Fury and Joshua's Riyadh Standoff"
    assert tile["angle"] == "Why one press conference reset the heavyweight fight."


def test_the_first_listener_still_sees_the_composers_title(tree, written):
    """Not written yet: nothing to read, so the tile is the composer's."""
    tile = _story_tile(cached=False)
    appmod._name_written_tiles([tile], 2)
    assert tile["title"] == "Fury and Joshua Face Off"


def test_the_writers_category_redraws_the_picture_label_and_tags(tree, written):
    th.store().put(th.Thumb("boxing", th.STATUS_APPROVED, facet="sports",
                            mime="image/png"), b"img-boxing")
    written(title="Fury and Joshua's Riyadh Standoff",
            category="heavyweight boxing")
    tile = _story_tile()
    appmod._name_written_tiles([tile], 2)
    assert "boxing" in tile["tags"] and "science" not in tile["tags"]
    assert tile["thumb"] and tile["thumb_facet"] == "sports"


def test_a_category_without_a_picture_still_names_the_facet(tree, written):
    written(title="Fury and Joshua's Riyadh Standoff",
            category="mixed martial arts")
    tile = _story_tile()
    appmod._name_written_tiles([tile], 2)
    assert tile["thumb"] == "" and tile["thumb_facet"] == "sports"


def test_words_the_tree_cannot_place_change_nothing(tree, written):
    written(title="Fury and Joshua's Riyadh Standoff",
            category="something nobody has a branch for")
    tile = _story_tile()
    appmod._name_written_tiles([tile], 2)
    assert tile["tags"] == ["science", "sports"]


def test_a_bank_tile_keeps_its_own_title(tree, written):
    bank = T.TOPIC_BANK[0]
    written(query=bank.query, title="Something Else Entirely",
            category="heavyweight boxing")
    tile = {"id": bank.id, "source": "", "query": bank.query,
            "title": bank.title, "tags": list(bank.tags), "cached": True}
    appmod._name_written_tiles([tile], 2)
    assert tile["title"] == bank.title
    assert tile["tags"] == list(bank.tags)


# --------------------------------------------------------------------------
# what the taste model learns from a play
# --------------------------------------------------------------------------
def test_a_play_of_a_written_story_is_logged_under_its_category(tree, written):
    written(category="heavyweight boxing")
    tags = appmod._event_tags("story-fury", QUERY, 2)
    assert {"boxing", "combat sports", "sports"} <= set(tags)
    assert "science" not in tags


def test_a_bank_play_keeps_its_declared_tags(tree, written):
    bank = T.TOPIC_BANK[0]
    written(query=bank.query, category="heavyweight boxing")
    assert appmod._event_tags(bank.id, bank.query, 2) == bank.tags


def test_an_unwritten_play_is_logged_as_before(tree, written):
    assert appmod._event_tags("story-x", "never written", 2) == \
        T.tags_for_id("story-x", "never written")
