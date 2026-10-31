"""Nando in code (§356): one node of the category tree per episode, placed
at the request from what the brief worked out, confirmed after the writer,
stored with the episode and read by the one reader - and a provider's
filing never left.
"""
from __future__ import annotations

import asyncio
import dataclasses
import sys
import pathlib

import pytest

import app as appmod
import cache as cache_mod
import categories as C
import category_audit
import live_captions
import script_generator as sg
import stories
import topics
from agents import categorizer, netenyahu
from episode_intelligence import Brief

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def tree():
    t = topics.category_tree()
    C.apply_seed(t)
    return t


def brief(**kw):
    kw.setdefault("query", "q")
    return Brief(**kw)


# ------------------------------------------------------------------ at the request
def test_the_briefs_category_words_outrank_a_word_in_the_question(tree):
    placed = categorizer.place(brief(category="extreme weather", subject="Miami heat wave"),
                               "Miami heat wave this week")
    assert placed.node == "extreme weather" and placed.how == "category"


def test_the_subject_places_when_the_words_do_not(tree):
    placed = categorizer.place(brief(category="", subject="Philadelphia Eagles"), "eagles score")
    # "Eagles" is a name that means two things (§358); "score" decides it,
    # and the subject names the team in full.
    assert placed.node == "philadelphia eagles" and placed.how in ("subject", "clash")


def test_a_question_phrase_on_another_facet_than_the_brief_is_passed_over(tree):
    # The brief says weather; "carolina hurricanes" would be the hockey team.
    placed = categorizer.place(brief(category="weather"), "hurricanes hitting the Carolina coast")
    assert placed.node == "extreme weather"


def test_with_no_brief_the_question_places_as_today(tree):
    for question in ("Stanley Cup odds", "why rents keep rising", "tariffs on Chinese steel"):
        assert categorizer.place(None, question).node == stories.resolve_category(question)


def test_a_facet_from_the_brief_is_broader_never_wrong(tree):
    facet = stories.resolve_category("sports")
    placed = categorizer.place(brief(category="sports"), "something the tree has no word for")
    assert placed.node == facet and placed.how == "facet"


def test_nothing_true_is_nothing(tree):
    placed = categorizer.place(brief(), "zxqv blorp")
    assert placed.node == "" and placed.path == ()


def test_the_path_runs_from_the_facet_down(tree):
    path = categorizer.place(brief(category="philadelphia eagles"), "eagles").path
    assert path[-1] == "philadelphia eagles" and path[0] in topics.FACETS


def test_placing_never_raises(monkeypatch, tree):
    monkeypatch.setattr(stories, "resolve_category",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("tree gone")))
    assert categorizer.place(brief(category="x"), "y") == categorizer.NOWHERE


# ------------------------------------------------------------------ a provider's filing
@pytest.fixture
def game(monkeypatch):
    story = stories.Story(subject="Hurricanes at Rangers", title="t", angle="a",
                          query="how did the Hurricanes do tonight", domain=stories.SPORTS,
                          tags=("sports", "ice hockey"))

    class Pool:
        def held(self, now=None):
            return [story]
    monkeypatch.setattr(stories, "pool", lambda: Pool())
    return story


def test_a_games_provider_filing_is_never_left(tree, game):
    field = categorizer.provider_field(game.query)
    assert field == ("sports",)
    placed = categorizer.place(brief(category="extreme weather"), game.query, field)
    assert placed.node == "ice hockey" and placed.how == "provider"
    node, how = categorizer.confirm(placed.node, "extreme weather", field)
    assert node == "ice hockey" and how == "kept"


def test_a_news_story_has_no_provider_field(tree, monkeypatch):
    story = stories.Story(subject="s", title="t", angle="a", query="q?",
                          domain=stories.ATTENTION, tags=("sports",))

    class Pool:
        def held(self, now=None):
            return [story]
    monkeypatch.setattr(stories, "pool", lambda: Pool())
    assert categorizer.provider_field("q?") == ()


def test_the_provider_field_reads_no_database(tree, game, monkeypatch):
    import trending_bank

    monkeypatch.setattr(trending_bank, "stories_now",
                        lambda *a: (_ for _ in ()).throw(AssertionError("database read")))
    assert categorizer.provider_field(game.query) == ("sports",)


# ------------------------------------------------------------------ after the writer
@pytest.mark.parametrize("request_node, words, expected", [
    ("philadelphia eagles", "", ("philadelphia eagles", "kept")),
    ("philadelphia eagles", "zxqv blorp", ("philadelphia eagles", "kept")),
    ("", "philadelphia eagles", ("philadelphia eagles", "writer")),
    ("philadelphia eagles", "philadelphia eagles", ("philadelphia eagles", "confirmed")),
    ("nfl", "philadelphia eagles", ("philadelphia eagles", "narrowed")),
    ("philadelphia eagles", "nfl", ("philadelphia eagles", "confirmed")),
    ("stock market", "philadelphia eagles", ("philadelphia eagles", "refiled")),
    ("", "", ("", "")),
])
def test_confirmed_after_the_writer(tree, request_node, words, expected):
    assert categorizer.confirm(request_node, words) == expected


def test_settle_records_both_decisions_and_moves_the_players_picture(tree, monkeypatch):
    published = []
    monkeypatch.setattr(live_captions, "publish_category", lambda k, w: published.append((k, w)))
    notes = sg.ScriptNotes()
    notes.caption_key = "k1"
    notes.category = "philadelphia eagles"
    notes.category_placement = categorizer.place(brief(category="stock market"), "q")
    assert categorizer.settle(notes, "q") == "philadelphia eagles"
    settled = notes.category_settled
    assert {k: settled[k] for k in ("request", "request_how", "node", "how")} == {
        "request": "stock market", "request_how": "category",
        "node": "philadelphia eagles", "how": "refiled"}
    assert settled["confidence"] == categorizer.CONFIDENCE["category"]
    assert published == [("k1", "philadelphia eagles")]


def test_settle_is_nothing_when_nando_did_not_work_on_the_episode(tree):
    notes = sg.ScriptNotes()
    notes.category = "philadelphia eagles"
    assert categorizer.settle(notes, "q") == "" and notes.category_settled is None


# ------------------------------------------------------------------ stored with the episode
@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    if request.param == "memory":
        return cache_mod.MemoryScriptCache()
    return cache_mod.SqliteScriptCache(str(tmp_path / "scripts.db"))


def test_the_node_is_stored_beside_the_words(store):
    store.put("k", ["One.", "Two."], 3600, "q", category="eagles", category_node="philadelphia eagles")
    assert store.category("k") == "eagles"
    assert store.category_node("k") == "philadelphia eagles"


def test_new_words_without_a_node_clear_it_and_the_same_words_keep_it(store):
    store.put("k", ["One."], 3600, "q", category_node="nfl")
    store.put("k", ["One."], 3600, "q")
    assert store.category_node("k") == "nfl"
    store.put("k", ["Different."], 3600, "q")
    assert store.category_node("k") == ""


def test_a_cache_without_the_node_answers_nothing(store):
    store.put("k", ["One."], 3600, "q", category="eagles")
    assert store.category_node("k") == ""


# ------------------------------------------------------------------ the one reader
class Cache:
    def __init__(self, words="", node=""):
        self.words, self.node = words, node

    def category(self, key):
        return self.words

    def category_node(self, key):
        return self.node


def test_the_reader_takes_nandos_node_first(tree, monkeypatch):
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", Cache(words="stock market", node="philadelphia eagles"))
    assert appmod._episode_category("k") == "philadelphia eagles"


def test_a_node_the_tree_no_longer_holds_falls_back_to_the_words(tree, monkeypatch):
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", Cache(words="stock market", node="merged away"))
    assert appmod._episode_category("k") == "stock market"


def test_an_episode_nando_never_filed_reads_as_before(tree, monkeypatch):
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", Cache(words="stock market"))
    assert appmod._episode_category("k") == "stock market"


# ------------------------------------------------------------------ wired into the episode
def _prepare(monkeypatch, agents, query="Miami heat wave this week"):
    async def research_(self, plan, notes=None):
        return dataclasses.replace(plan, evidence="SOURCE 1\nTitle: x")

    async def understand(self, plan, notes=None):
        return plan
    monkeypatch.setattr(sg.ScriptGenerator, "research", research_)
    monkeypatch.setattr(sg.ScriptGenerator, "understand", understand)
    published = []
    monkeypatch.setattr(live_captions, "publish_category", lambda k, w: published.append(w))
    plan = dataclasses.replace(sg.plan_episode(query, 2),
                               brief=brief(query=query, category="extreme weather"))
    notes = sg.ScriptNotes()
    notes.agents = agents
    notes.caption_key = "k"
    asyncio.run(sg.ScriptGenerator.__new__(sg.ScriptGenerator).prepare(plan, notes))
    return notes, published


def test_the_request_is_placed_and_the_picture_follows_when_nando_works(tree, monkeypatch):
    notes, published = _prepare(monkeypatch, netenyahu.EpisodeRun(("categorizer",), {}))
    assert notes.category_placement.node == "extreme weather"
    assert published == ["extreme weather"]


def test_nothing_is_placed_while_nando_is_off(tree, monkeypatch):
    notes, published = _prepare(monkeypatch, netenyahu.OFF)
    assert notes.category_placement is None and published == []


def test_every_write_path_settles_and_audits_through_nando():
    for name in ("pipeline.py", "prefetch.py", "daily_edition.py", "trending_bank.py"):
        source = (ROOT / name).read_text(encoding="utf-8")
        assert source.count("categorizer.settle(notes") == 1, name
        assert 'nando=getattr(notes, "category_settled", None)' in source, name
        # Passed only when there is one, so a cache that predates it is
        # called exactly as before.
        assert '["category_node"] = node' in source, name


# ------------------------------------------------------------------ the audit
def test_the_audit_logs_both_of_nandos_decisions(tree):
    nando = {"request": "stock market", "request_how": "category",
             "node": "philadelphia eagles", "how": "refiled"}
    category_audit.note("q1", "philadelphia eagles", "tap", composer="", nando=nando)
    category_audit.note("q2", "nfl", "tap", composer="")
    found = category_audit.summary(days=1)["nando"]
    assert found["filed"] == 1
    assert found["after_the_writer"] == {"refiled": 1}
    assert found["at_the_request"] == {"category": 1}
    assert found["refiled_rate"] == 1.0
    assert found["refiled"][0]["after"] == "philadelphia eagles"


def test_no_filings_report_none_not_zero(tree):
    assert category_audit.summary(days=1)["nando"]["refiled_rate"] is None


# ------------------------------------------------------------------ the measurement
def test_the_forty_questions_are_labelled_with_nodes_the_seed_holds():
    sys.path.insert(0, str(ROOT / "tools"))
    import nando_eval

    tree_ = nando_eval.seeded_tree()
    assert len(nando_eval.QUESTIONS) == 40
    assert len({q for q, _l in nando_eval.QUESTIONS}) == 40
    for _question, label in nando_eval.QUESTIONS:
        assert tree_.get(label) is not None, label


def test_nando_in_code_is_never_wronger_than_today():
    sys.path.insert(0, str(ROOT / "tools"))
    import nando_eval

    result = nando_eval.run(nando_eval.code_places())
    today, nando = result.values()
    assert sum(nando["totals"].values()) == 40
    assert nando["totals"]["wrong"] <= today["totals"]["wrong"]
