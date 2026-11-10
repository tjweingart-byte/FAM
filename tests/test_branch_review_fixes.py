"""What the branch's review found, each fixed (§530). Made-up text, never a
source's."""
from __future__ import annotations

import asyncio
import dataclasses
import json
from contextlib import closing
from datetime import datetime, timedelta, timezone

import db as store_db
import episode_quality as q
from agents import editor, models
from agents.editor import EditorAnswer, _Beat
from script_generator import ScriptNotes, plan_episode


def test_a_shared_year_is_no_landing():
    assert q._plan_numbers("The board met on October 28, 2026 in Washington.") == set()
    assert q.plan_following({"landing": ["Shares of the company rose in 2026 after the merger."]},
                            ["An opening.", "The 2026 season begins in March."])["landing"] == 0.0


def test_the_questions_own_words_are_no_sign_of_use():
    plan = {"beats": [["The Federal Reserve held its benchmark rate steady on Wednesday "
                       "after a long debate among officials."]]}
    script = ["Mortgage rates at the Federal Reserve meeting were discussed by bankers."]
    assert q.plan_following(plan, script)["beats"] == 1.0          # without the question
    assert q.plan_following(plan, script,
                            query="what did the Federal Reserve decide")["beats"] == 0.0


def test_a_link_cut_in_its_address_leaves_no_bracket():
    assert editor._clean("They said [it had opened](https://example.com/a-b") == \
        "They said it had opened"


def test_live_facts_carry_how_old_they_are():
    import live_facts

    facts = live_facts.LiveFacts(domain="sports", source="test", as_of=datetime.now(
        timezone.utc) - timedelta(minutes=40), facts=["Hawks 21, Owls 14."], status="in_progress")
    lines = editor._live_sentences(facts)
    assert lines[0] == "Status: in progress, observed 40 minutes ago."
    assert lines[1] == "Hawks 21, Owls 14."


EVIDENCE = ("SOURCE 1\nTitle: t\nPublished: 2026-11-02 (yesterday)\nSource type: established outlet\n"
            "Key evidence:\nThe Fed held rates at 4.25 percent. Two members of the committee dissented.\n")


def _ask():
    answer = EditorAnswer(answers="x", summary=["P1.1"], opening=["P1.1"],
                          beats=[_Beat(purpose="y", sentences=["P1.2"])], landing=["P1.2"],
                          thin=False, missing=[])

    async def ask(request, notes=None, client=None):
        ask.request = request
        return models.AgentResult(models.OK, "editor", "outline", value=answer, model="m")
    return ask


def test_on_search_he_gets_no_worked_plans_and_no_shapes(monkeypatch):
    ask = _ask()
    monkeypatch.setattr(models, "ask", ask)
    plan = dataclasses.replace(plan_episode("what did the fed decide", 1.5), search=False,
                               evidence=EVIDENCE)
    asyncio.run(editor.outline(plan, ScriptNotes(), ahead=False))
    material = dict(ask.request.material)
    assert "Worked plans:" not in material["teaching"]
    assert "The brief's shape" not in material["evidence"]
    asyncio.run(editor.outline(plan, ScriptNotes(), ahead=True))
    material = dict(ask.request.material)
    assert "Worked plans:" in material["teaching"]
    assert "The brief's shape" in material["evidence"]


def test_no_facet_lookup_while_matching_is_off(monkeypatch):
    import script_generator

    def boom(plan):
        raise AssertionError("looked up a facet nobody reads")

    monkeypatch.setattr(script_generator, "episode_facet", boom)
    assert "Worked plans:" in editor._teaching(plan_episode("x", 1.5))


def _row(sentences):
    return {"key": "k", "sourced": 100, "at": 1_000.0, "query": "q", "minutes": 1,
            "origin": "trending", "structure": "", "category": "", "title": "",
            "writer_model": "", "prompt": "", "words": 3, "word_budget": 0,
            "sentences": json.dumps(sentences), "evidence": "", "measures": "{}",
            "editor_arm": ""}


def test_a_rewrite_forgets_the_old_texts_timings(tmp_path):
    store = q.QualityStore(str(tmp_path / "q.db"))
    store.record(_row(["One sentence here.", "Two."]), now=1_000.0)
    assert store.set_starts("k", 100, json.dumps({"sentences": 2, "shares": [0, 0.5]}))
    store.record(_row(["A different text.", "Also two."]), now=1_000.0)
    assert store.episodes_since(0)[0]["starts"] == ""
    store.record(_row(["A different text.", "Also two."]), now=1_000.0)   # same text again
    assert store.set_starts("k", 100, json.dumps({"sentences": 2, "shares": [0, 0.4]}))
    store.record(_row(["A different text.", "Also two."]), now=1_000.0)
    assert store.episodes_since(0)[0]["starts"] != ""                     # kept


def test_an_existing_store_gains_the_starts_column(tmp_path):
    """A quality store from before §521 is widened, as §454's was."""
    path = str(tmp_path / "old.db")
    with closing(store_db.connect(path, isolation_level=None)) as db:
        db.execute("""CREATE TABLE episodes (
                          key TEXT NOT NULL, sourced INTEGER NOT NULL, at REAL NOT NULL,
                          query TEXT NOT NULL DEFAULT '', minutes INTEGER NOT NULL DEFAULT 0,
                          origin TEXT NOT NULL DEFAULT '', structure TEXT NOT NULL DEFAULT '',
                          category TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
                          writer_model TEXT NOT NULL DEFAULT '', prompt TEXT NOT NULL DEFAULT '',
                          words INTEGER NOT NULL DEFAULT 0, word_budget INTEGER NOT NULL DEFAULT 0,
                          sentences TEXT NOT NULL DEFAULT '[]', evidence TEXT NOT NULL DEFAULT '',
                          measures TEXT NOT NULL DEFAULT '{}', grade TEXT NOT NULL DEFAULT '',
                          grade_model TEXT NOT NULL DEFAULT '',
                          grade_dollars REAL NOT NULL DEFAULT 0,
                          graded_at REAL NOT NULL DEFAULT 0, PRIMARY KEY (key, sourced))""")
        db.execute("INSERT INTO episodes (key, sourced, at) VALUES ('old', 1, 1000.0)")
    store = q.QualityStore(path)
    q.QualityStore(path)                                   # a second open is a no-op
    assert store.set_starts("old", 1, json.dumps({"sentences": 1, "shares": [0]}))
    assert json.loads(store.episodes_since(0)[0]["starts"])["shares"] == [0]


# ------------------------------------------------------------------ the second review
def test_clock_times_and_ordinals_are_not_figures():
    for text in ("The vote is at 2 p.m. Eastern.", "It closed at 4 pm on Friday.",
                 "It is on the 3rd of November."):
        assert q._plan_numbers(text) == set(), text
    assert q._plan_numbers("Revenue rose 12 percent to 4.2 billion.") >= {12.0, 4.2}


def test_a_business_story_about_subscribers_keeps_its_evidence():
    kept = "Netflix said 9 million more households chose to subscribe to the ad tier this year."
    assert editor._split(kept) == [kept]
    assert editor._split("Subscribe to our newsletter for daily updates now.") == []


def test_the_live_status_says_a_closed_market_and_a_delayed_feed():
    import live_facts

    facts = live_facts.LiveFacts(domain="markets", source="test",
                                 as_of=datetime.now(timezone.utc) - timedelta(minutes=5),
                                 facts=["Index 5,100."], status="final",
                                 market_closed=True, delayed_seconds=900)
    assert editor._live_sentences(facts)[0] == (
        "Status: final, observed 5 minutes ago, the market is closed, "
        "the feed runs 15 minutes behind.")


def test_the_shape_instructions_are_only_for_ahead_of_the_tap(monkeypatch):
    ask = _ask()
    monkeypatch.setattr(models, "ask", ask)
    plan = dataclasses.replace(plan_episode("what did the fed decide", 1.5), search=False,
                               evidence=EVIDENCE)
    asyncio.run(editor.outline(plan, ScriptNotes(), ahead=False))
    assert "The brief chose a shape" not in ask.request.instructions
    asyncio.run(editor.outline(plan, ScriptNotes(), ahead=True))
    assert "The brief chose a shape" in ask.request.instructions


def _listened(store, key, heard, sourced=100, listener="l"):
    store.note_listen(key, sourced, listener, heard, 60, 1, now=1_000.0)


def test_a_finished_listen_reaches_the_landing_and_stops_nowhere(tmp_path):
    store = q.QualityStore(str(tmp_path / "q.db"))
    store.record(_row(["The first sentence of it.", "The second sentence here.",
                       "The landing sentence is last."]), now=1_000.0)
    store.set_starts("k", 100, json.dumps({"sentences": 3, "shares": [0, 0.4, 0.93]}))
    _listened(store, "k", 52.5)                      # 0.875 heard: finished
    out = q.drop_off(days=1, min_reached=1, now=1_060.0, quality=store)
    landing = next(s for s in out["worst"] if s["position"] == 2)
    assert landing["reached"] == 1 and landing["stopped"] == 0


def test_an_unstamped_listen_goes_to_the_newest_version(tmp_path):
    store = q.QualityStore(str(tmp_path / "q.db"))
    old, new = _row(["Old text one here.", "Old text two."]), _row(["New text one here.", "New."])
    new["sourced"] = 200
    store.record(old, now=1_000.0)
    store.record(new, now=1_000.0)
    _listened(store, "k", 5, sourced=0)
    out = q.drop_off(days=1, min_reached=1, now=1_060.0, quality=store)
    assert {s["sentence"] for s in out["worst"]} <= {"New text one here.", "New."}
