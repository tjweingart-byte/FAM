"""The category vocabulary (§374): other ways of saying each node, and the
named things filed under one - derived from data FAM has, or drafted by
Nando's offline task and checked by a person - read by Nando's code.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys

import pytest

import categories as C
import category_vocabulary as V
import topics
from agents import categorizer, models
from agents.categorizer import Prior

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import draft_category_vocabulary as D  # noqa: E402


@pytest.fixture
def tree():
    t = topics.category_tree()
    C.apply_seed(t)
    V.reset()
    yield t
    V.reset()


@pytest.fixture
def drafted(tmp_path, monkeypatch):
    path = tmp_path / "category_vocabulary.json"

    def write(nodes=None, entities=None):
        path.write_text(json.dumps({"version": 1, "nodes": nodes or {},
                                    "entities": entities or {}}))
        V.reset()
    monkeypatch.setattr(V, "PATH", path)
    write()
    return write


# ------------------------------------------------------------------ derived from data FAM has
def test_a_teams_nickname_names_the_team_in_a_sports_question(tree, drafted):
    assert V.match("Lakers game tonight")[0] == ("los angeles lakers",)


def test_a_nickname_alone_is_not_read_as_a_team_outside_sport(tree, drafted):
    assert V.match("the best jazz festivals this summer")[0] == ()
    assert V.match("Jazz win in overtime")[0] == ("utah jazz",)


@pytest.mark.parametrize("question", [
    "jazz festival lineup", "jazz brunch near me this season",
    "Lakers trade rumors"])          # "trade", "season", "lineup" are not sport words
def test_words_a_festival_or_a_menu_also_use_are_not_sport_words(tree, drafted, question):
    assert V.match(question)[0] == ()


def test_a_two_word_nickname(tree, drafted):
    assert V.match("Red Sox roster moves")[0] == ("boston red sox",)


def test_an_interests_label_names_its_node(tree, drafted):
    nodes, kind, wording = V.match("what is happening in cryptocurrency")
    assert nodes == ("crypto",) and kind == "wording" and wording == "cryptocurrency"


# ------------------------------------------------------------------ drafted, then checked
def test_drafted_wordings_and_entities_are_read(tree, drafted):
    drafted(nodes={"rental market": {"wordings": ["rents", "rent prices"]}},
            entities={"taylor swift": {"node": "live touring", "kind": "person"}})
    assert V.match("why rents keep rising")[0] == ("rental market",)
    assert V.match("Taylor Swift tour dates")[:2] == (("live touring",), "entity")


def test_a_wording_two_nodes_share_is_a_tie(tree, drafted):
    drafted(nodes={"rental market": {"wordings": ["housing costs"]},
                   "house prices": {"wordings": ["housing costs"]}})
    assert set(V.match("housing costs keep climbing")[0]) == {"rental market", "house prices"}


def test_the_longest_wording_wins_and_an_entity_outranks_a_wording(tree, drafted):
    drafted(nodes={"rental market": {"wordings": ["rents"]},
                   "urban housing": {"wordings": ["rents in cities"]}},
            entities={"zillow group": {"node": "house prices", "kind": "company"}})
    assert V.match("rents in cities")[0] == ("urban housing",)
    assert V.match("Zillow Group says rents in cities fell")[0] == ("house prices",)


def test_a_one_word_entity_is_read_only_in_its_field(tree, drafted):
    # Review, §376: "Bills", "Lions" are also everyday words.
    drafted(entities={"bills": {"node": "nfl", "kind": "team"},
                      "lions": {"node": "nfl", "kind": "team"},
                      "mercedes": {"node": "formula one", "kind": "team"}})
    assert V.match("the spending bills in congress")[0] == ()
    assert V.match("lions at the zoo")[0] == ()
    assert V.match("Bills game tonight")[0] == ("nfl",)
    assert V.match("mercedes", facet="sports")[0] == ("formula one",)


def test_an_entity_keeps_the_context_a_nickname_asked_for(tree, drafted):
    drafted(entities={"jazz": {"node": "utah jazz", "kind": "team"}})
    assert V.match("the best jazz festivals this summer")[0] == ()


def test_a_facet_keeps_the_match_inside_it(tree, drafted):
    drafted(nodes={"rental market": {"wordings": ["rents"]}})
    assert V.match("rents", facet="sports")[0] == ()


def test_nodes_and_entities_the_tree_does_not_hold_are_ignored(tree, drafted):
    drafted(nodes={"no such node": {"wordings": ["xyz"]}},
            entities={"acme": {"node": "no such node"}})
    assert V.match("xyz acme")[0] == ()


def test_an_unreadable_file_is_no_vocabulary_and_never_raises(tree, tmp_path, monkeypatch):
    bad = tmp_path / "v.json"
    bad.write_text("{not json")
    monkeypatch.setattr(V, "PATH", bad)
    V.reset()
    assert V.load(bad) == {"version": 1, "nodes": {}, "entities": {}}
    assert V.match("Lakers game tonight")[0] == ("los angeles lakers",)


def test_the_pending_draft_is_never_read(tree, drafted, monkeypatch):
    pending = V.PATH.with_name("category_vocabulary.pending.json")
    pending.write_text(json.dumps({"version": 1, "entities": {},
                                   "nodes": {"rental market": {"wordings": ["rents"]}}}))
    monkeypatch.setattr(V, "PENDING", pending)
    V.reset()
    assert V.match("why rents keep rising")[0] == ()


def test_the_repositorys_vocabulary_holds_only_nodes_the_seed_has():
    data = json.loads((ROOT / "category_vocabulary.json").read_text())
    t = C.CategoryStore(str(ROOT / "tmp_vocab_check.db"))
    try:
        C.apply_seed(t)
        for node in data["nodes"]:
            assert t.get(node) is not None, node
        for name, entry in data["entities"].items():
            assert t.get(entry["node"]) is not None, name
    finally:
        pathlib.Path(t.path).unlink(missing_ok=True)


def test_the_vocabulary_is_data_not_a_rule_book():
    from agents import rulebooks

    names = set(rulebooks.book_paths())
    assert not any("vocabulary" in n for n in names)


# ------------------------------------------------------------------ Nando reads it
def test_nando_places_by_a_wording_after_the_trees_own_phrases(tree, drafted):
    drafted(nodes={"rental market": {"wordings": ["rents"]}})
    placed = categorizer.suggest(None, "why rents keep rising")
    assert placed.node == "rental market" and placed.how == "wording"
    assert placed.confidence == categorizer.CONFIDENCE["wording"]


def test_nando_files_a_tie_under_what_its_nodes_share(tree, drafted):
    drafted(nodes={"nba": {"wordings": ["hoops"]}, "wnba": {"wordings": ["hoops"]}})
    placed = categorizer.suggest(None, "hoops tonight")
    assert placed.how == "shared" and placed.node in ("basketball", "sports")


def test_a_named_thing_places_its_node(tree, drafted):
    drafted(entities={"taylor swift": {"node": "live touring", "kind": "person"}})
    placed = categorizer.suggest(None, "Taylor Swift tour dates")
    assert placed.node == "live touring" and placed.how == "entity"


def test_a_dailyfam_specific_narrows_through_the_vocabulary(tree, drafted):
    prior = categorizer.prior_for(
        "The latest on Eagles (NFL) as of Saturday, October 31, 2026. Cover only Eagles")
    assert prior == Prior("philadelphia eagles", "dailyfam")


# ------------------------------------------------------------------ the drafting tool
def test_the_tool_cleans_wordings():
    assert D.clean_wordings("rental market", ["Rents!", "rents", "rental market", "",
                                               "a b c d e", "Rent Prices"]) == ["rents", "rent prices"]


class FakeResult:
    def __init__(self, value):
        self.ok, self.value, self.status, self.reason = True, value, "ok", ""


def test_the_tool_drops_answers_for_nodes_it_did_not_ask_or_the_tree_lacks(tree, monkeypatch):
    async def ask(request, **_kw):
        assert request.agent == "categorizer" and request.rule_book == "nando.md"
        assert request.model_setting == "categorizer_model"
        assert not any(tag == "request" for tag, _t in request.material)  # FAM's own material
        return FakeResult(D.NodeDraft(nodes=[
            D.NodeWords(node="rental market", definition="d", excludes="", wordings=["Rents"]),
            D.NodeWords(node="made up", definition="d", excludes="", wordings=["x"])]))
    monkeypatch.setattr(models, "ask", ask)
    kept, problems = asyncio.run(D.draft_nodes(tree, limit=400))
    assert kept["rental market"]["wordings"] == ["rents"]
    assert "made up" not in kept and any("made up" in p for p in problems)


def test_the_tool_keeps_only_entities_under_real_nodes(tree, monkeypatch):
    async def ask(request, **_kw):
        return FakeResult(D.EntityDraft(entities=[
            D.Entity(name="Miami Heat", node="nba", kind="team"),
            D.Entity(name="Nowhere FC", node="no node", kind="team"),
            D.Entity(name="Mystery", node="none", kind="other")]))
    monkeypatch.setattr(models, "ask", ask)
    kept, problems = asyncio.run(D.draft_entities(tree))
    assert kept["miami heat"] == {"node": "nba", "kind": "team"}
    assert "nowhere fc" not in kept and "mystery" not in kept


def test_the_tool_sends_nothing_when_the_agent_is_off(tree):
    async def go():
        return await D.draft_nodes(tree, limit=3)
    kept, problems = asyncio.run(go())
    assert kept == {} and problems and "off" in problems[0]


def test_a_wikidata_id_is_kept_only_for_one_exact_label(monkeypatch):
    import wikimedia

    hits = {"search": [{"id": "Q1", "label": "Rental market"}, {"id": "Q2", "label": "Rental"}]}
    monkeypatch.setattr(wikimedia, "_wikidata", lambda params: hits)
    assert D.wikidata_id("rental market") == "Q1"
    hits["search"].append({"id": "Q3", "label": "rental market"})
    assert D.wikidata_id("rental market") == ""
    monkeypatch.setattr(wikimedia, "_wikidata", lambda params: 1 / 0)
    assert D.wikidata_id("rental market") == ""


def test_the_dry_run_sends_nothing(monkeypatch, capsys):
    async def ask(*_a, **_k):
        raise AssertionError("sent")
    monkeypatch.setattr(models, "ask", ask)
    monkeypatch.setattr(sys, "argv", ["draft_category_vocabulary.py", "--dry-run"])
    assert D.main() == 0
    assert "would ask about" in capsys.readouterr().out
