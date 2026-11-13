"""§599: the first reviewed tree was mostly junk grown from searches - lone
everyday words like "company" and "development". The placer may now say a
searched word is not a subject; it is taken out and never grown back. A lone
searched word waits when no placer will be asked. The download carries a few
titles per node so the reviewer sees what a category holds."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import types

import pytest

import categories as C
import category_review as R


@pytest.fixture
def store(tmp_path):
    s = C.CategoryStore(str(tmp_path / "categories.db"))
    C.apply_seed(s)
    return s


def _texts(phrase_texts):
    """Three listeners, each in two wordings - over both thresholds."""
    out = []
    for user in ("u1", "u2", "u3"):
        for text in phrase_texts:
            out.append((user, text))
    return out


SEARCHES = _texts(["which company makes the drone", "drone company news today",
                   "quantum widgets explained", "what are quantum widgets"])


def test_a_lone_searched_word_waits_without_a_placer(store):
    minted = {n.id for n in C.promote(store, SEARCHES, single_words=False)}
    assert "company" not in minted and "drone" not in minted
    assert "quantum widgets" in minted, "a phrase of two words still grows"


def test_a_story_subject_or_the_writers_word_is_never_held(store):
    minted = {n.id for n in C.promote(store, [], always=["zeppelins"],
                                      written=[("dirigibles", "tech")],
                                      single_words=False)}
    assert {"zeppelins", "dirigibles"} <= minted


def test_with_a_placer_a_lone_word_grows_as_before(store):
    minted = {n.id for n in C.promote(store, SEARCHES)}
    assert "company" in minted


def test_a_declined_word_leaves_the_tree_and_never_grows_back(store):
    C.promote(store, SEARCHES)
    assert store.get("company") is not None
    assert C.apply_declines(store, ["Company"], ["company"]) == 1
    assert store.get("company") is None
    assert "company" in store.declined()
    assert {n.id for n in C.promote(store, SEARCHES)}.isdisjoint({"company"})
    assert store.mint("company", source=C.SOURCE_SEARCH) is None
    # The owner can still add it, and a story's subject is not refused.
    assert store.mint("company", parent_id="business", source=C.SOURCE_STORY)


def test_a_reply_cannot_decline_what_it_was_not_asked_or_did_not_grow(store):
    C.promote(store, SEARCHES)
    # Not asked about it.
    assert C.apply_declines(store, ["company"], ["drone"]) == 0
    assert store.get("company") is not None
    # Asked, but the seed's own node is not the placer's to refuse.
    assert C.apply_declines(store, ["bengals"], ["bengals"]) == 0
    assert store.get("bengals") is not None
    # Nor a node the owner placed.
    store.owner_move("company", "business")
    assert C.apply_declines(store, ["company"], ["company"]) == 0


def test_a_wipe_forgets_what_was_declined(store):
    C.promote(store, SEARCHES)
    store.decline("company")
    store.clear()
    assert store.declined() == frozenset()


def test_the_placer_reply_takes_out_its_not_subjects(store, monkeypatch):
    C.promote(store, SEARCHES)
    reply = {"paths": [{"subject": "quantum widgets",
                        "path": ["tech", "quantum widgets"]}],
             "not_subjects": ["company"]}

    class FakeMessages:
        async def create(self, **kwargs):
            assert "not_subjects" in kwargs["output_config"]["format"]["schema"]["properties"]
            return types.SimpleNamespace(
                stop_reason="end_turn", usage=None,
                content=[types.SimpleNamespace(type="text", text=json.dumps(reply))])

    import anthropic_client
    monkeypatch.setattr(anthropic_client, "build_async_client",
                        lambda key: types.SimpleNamespace(messages=FakeMessages()))
    import dataclasses

    import config
    monkeypatch.setattr(config, "settings",
                        dataclasses.replace(config.settings, categories_place=True))
    asyncio.run(C.place(store, ["company", "quantum widgets"]))
    assert store.get("company") is None
    assert store.get("quantum widgets").parent_id == "tech"


def test_the_placer_is_told_what_a_non_subject_looks_like():
    assert "not_subjects" in C.PLACER_SYSTEM
    assert "company" in C.PLACER_SYSTEM
    assert C.PLACER_SCHEMA["required"] == ["paths", "not_subjects"]


def test_the_download_shows_what_each_node_holds(store):
    titles = {"bengals": ["Burrow's Return", "The Bengals' Line", "Week Nine", "Older"]}
    rows = list(csv.DictReader(io.StringIO(R.export_csv(store, {"bengals": 4}, titles))))
    assert tuple(rows[0]) == R.EXPORT_COLUMNS and "examples" in R.EXPORT_COLUMNS
    bengals = next(r for r in rows if r["node"] == "bengals")
    assert bengals["examples"] == "Burrow's Return | The Bengals' Line | Week Nine"
    assert "examples" in R.PROMPT


def test_both_caches_give_titles_per_node(tmp_path):
    import cache
    for c in (cache.MemoryScriptCache(),
              cache.SqliteScriptCache(str(tmp_path / "s.db"))):
        c.put("k1", ["One."], 3600, "how are the bengals", title="Burrow's Return",
              category_node="bengals")
        c.put("k2", ["Two."], 3600, "fed rates", category_node="federal reserve")
        got = c.node_titles()
        assert got["bengals"] == ["Burrow's Return"]
        assert got["federal reserve"] == ["fed rates"], "the question when untitled"
