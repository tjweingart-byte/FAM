"""Training the writer (§502): high effort where nobody waits, the ear
checks, where listeners stop, and before-and-after pairs made before launch.
"""
from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys

import pytest

import episode_quality
import script_generator as sg
from script_generator import ScriptNotes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ------------------------------------------------------------------ 1. effort
def test_the_writer_thinks_hard_only_where_nobody_waits():
    assert sg.writer_for(ScriptNotes())[1] == "low"                    # searchFAM
    assert sg.writer_for(None)[1] == "low"
    for flags in ({"ahead": True}, {"edition": True}, {"prefetch": True}):
        assert sg.writer_for(ScriptNotes(**flags))[1] == "high", flags
    assert sg.writer_for(ScriptNotes(ahead=True))[0] == sg.writer_for(ScriptNotes())[0]


def test_an_editions_own_effort_still_wins(monkeypatch):
    monkeypatch.setattr(sg, "settings", dataclasses.replace(
        sg.settings, edition_writer_effort="medium", ahead_writer_effort="high"))
    assert sg.writer_for(ScriptNotes(edition=True))[1] == "medium"
    assert sg.writer_for(ScriptNotes(ahead=True))[1] == "high"


def test_the_effort_is_copied_and_reported():
    text = open(os.path.join(ROOT, ".env.example")).read()
    assert "\nAHEAD_WRITER_EFFORT=high\n" in text and "\nTEACHING_PAIRS=1\n" in text
    source = open(os.path.join(ROOT, "app.py")).read()
    assert '"ahead_writer_effort": writer_for(ScriptNotes(ahead=True))[1]' in source


# ------------------------------------------------------------------ 6. the ear
def test_the_ear_checks_name_each_fault():
    out = episode_quality.ear([
        "Despite mounting pressure from several directions over many long months of argument, the Fed held.",
        "\"We will wait,\" said Powell.",
        "Revenue rose 12 percent to 4.2 billion dollars, up from 3.1 billion.",
        "As you can see, the rise is large (and growing).",
        "He bought eggs, milk, bread, butter, jam and tea.",
        "The Fed held rates on Wednesday."])
    checks = out["checks"]
    assert checks["front_loaded"] == [0] and checks["said_after"] == [1]
    assert checks["figures"] == [2] and checks["visual"] == [3]
    assert checks["brackets"] == [3] and checks["long_list"] == [4]
    assert out["score"] == round(1 / 6, 3)


def test_a_run_of_one_length_reads_as_a_list():
    assert episode_quality.ear(["One two three four."] * 4 + ["Then a longer sentence that "
                                "breaks the run for good."])["checks"]["monotone"] == [0, 1, 2, 3]


def test_a_time_phrase_lead_is_not_front_loaded():
    assert episode_quality.ear(["In early September the government took over Fannie Mae and "
                                "Freddie Mac, the two giants of the mortgage market."]
                               )["checks"]["front_loaded"] == []


def test_the_house_examples_pass_the_ear():
    """Tuned so the owner's examples read as the ear checks want."""
    import re

    scores = []
    for _, script in sg.STYLE_EXAMPLES:
        scores.append(episode_quality.ear(re.split(r"(?<=[.!?])\s+", " ".join(script.split())))
                      ["score"])
    assert min(scores) >= 0.7 and sum(scores) / len(scores) >= 0.85


def test_every_measure_carries_the_ear():
    assert "score" in episode_quality.measure(["The Fed held rates."])["ear"]


# ------------------------------------------------------------------ 4. where they stop
def _row(key, sentences):
    return {"key": key, "sourced": 100, "at": 1_000.0, "query": f"q {key}", "minutes": 1,
            "origin": "trending", "structure": "", "category": "", "title": "",
            "writer_model": "", "prompt": "", "words": 30, "word_budget": 0,
            "sentences": json.dumps(sentences), "evidence": "", "measures": "{}",
            "editor_arm": ""}


SENTENCES = ["The Fed held rates on Wednesday.", "Powell said they would wait.",
             "Despite mounting pressure from many directions and over many months of "
             "argument, the board stayed put.", "Stocks rose.", "Mortgage rates fell."]


@pytest.fixture
def quality(tmp_path):
    return episode_quality.QualityStore(str(tmp_path / "q.db"))


def test_each_sentence_has_an_exit_rate(quality):
    quality.record(_row("k", SENTENCES), now=1_000.0)
    assert quality.set_starts("k", 102, json.dumps({"sentences": 5,
                                                    "shares": [0, 0.2, 0.4, 0.7, 0.9]}))
    for i, heard in enumerate([5, 25, 45, 45, 50, 58, 60, 60, 60, 60, 60, 60]):
        quality.note_listen("k", 100, f"l{i}", heard, 60, 1, now=1_000.0)
    out = episode_quality.drop_off(days=1, min_reached=1, now=1_060.0, quality=quality)
    assert (out["episodes"], out["listens"], out["measured"]) == (1, 12, 1)
    worst = out["worst"][0]
    assert (worst["position"], worst["reached"], worst["stopped"]) == (3, 10, 3)
    assert worst["sentence"] == "Stocks rose."
    third = next(s for s in out["worst"] if s["position"] == 2)
    assert third["ear"] == ["front_loaded"]
    assert out["by_ear_check"]["front_loaded"]["flagged"]["reached"] == 11
    assert set(out["by_place"]) == {"opening", "middle", "landing"}
    assert "l0" not in json.dumps(out)


def test_without_timings_words_place_the_sentences(quality):
    quality.record(_row("k", SENTENCES), now=1_000.0)
    quality.note_listen("k", 100, "l", 1, 60, 1, now=1_000.0)
    out = episode_quality.drop_off(days=1, min_reached=1, now=1_060.0, quality=quality)
    assert out["measured"] == 0 and out["worst"][0]["position"] == 0


def test_the_starts_are_kept_once_as_shares(quality):
    episode_quality.reset(quality)
    try:
        quality.record(_row("k", SENTENCES[:2]), now=1_000.0)
        episode_quality.note_starts("k", 101, SENTENCES[:2], [0.0, 3.0], 6.0)
        episode_quality.note_starts("k", 101, SENTENCES[:2], [0.0, 1.0], 6.0)   # once
        episode_quality.note_starts("k", 101, SENTENCES[:2], [0.0], 6.0)        # mismatched
        row = quality.episodes_since(0)[0]
        assert json.loads(row["starts"]) == {"sentences": 2, "shares": [0.0, 0.5]}
    finally:
        episode_quality.reset()


def test_the_pipeline_keeps_the_starts_with_the_audio():
    source = open(os.path.join(ROOT, "pipeline.py")).read()
    assert "episode_quality.note_starts" in source


# ------------------------------------------------------------------ 3. pairs
def _tool():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "teaching_pairs", os.path.join(ROOT, "tools", "teaching_pairs.py"))
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


def test_a_pair_round_trips_and_the_writer_reads_it(monkeypatch):
    tool = _tool()
    text = tool.pair_text("what did the fed do", "In this episode we look at the Fed.",
                          "The Fed held rates on Wednesday.", "The draft opened by orienting.")
    pair = sg.parse_pair(text)
    assert pair["after"] == "The Fed held rates on Wednesday."
    monkeypatch.setattr(sg, "TEACHING_PAIRS", [pair])
    block = sg.teaching_pair_block()
    assert '<pair query="what did the fed do">' in block and "<why>The draft opened" in block
    assert block in sg.system_prompt()
    monkeypatch.setattr(sg, "settings", dataclasses.replace(sg.settings, teaching_pairs=False))
    assert sg.teaching_pair_block() == ""


def test_with_no_pairs_the_instructions_are_unchanged():
    assert sg.TEACHING_PAIRS == [] or all(sg.TEACHING_PAIRS)
    if not sg.TEACHING_PAIRS:
        assert sg.system_prompt() == sg.SYSTEM_PROMPT + sg.style_example_block()


def test_the_reason_is_built_from_what_was_fixed():
    tool = _tool()
    assert tool.why_line(["recap", "long"], ["long"]) == \
        "The draft ended on a recap; the rewrite does not."
    assert tool.why_line(["recap"], ["recap"]) == ""
    assert set(tool.FLAW_WORDS) >= set(episode_quality.EAR_CHECKS)


def test_the_code_names_a_drafts_flaws():
    tool = _tool()
    found = tool.flaws(["In this episode, we take a look at the Fed.", "It held rates.",
                        "So, to sum up, the Fed held rates."], "what did the fed do")
    assert "recap" in found


def test_never_on_a_deployed_server():
    env = {**os.environ, "RENDER": "true"}
    out = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "teaching_pairs.py"),
                          "--propose"], capture_output=True, text=True, env=env, timeout=120)
    assert out.returncode == 2 and "never on a deployed service" in out.stdout


def test_nothing_on_the_server_imports_it():
    import re

    imports = re.compile(r"^\s*(?:from\s+tools(?:\.teaching_pairs|\s+import\s+.*teaching_pairs)"
                         r"|import\s+tools\.teaching_pairs)", re.M)
    for folder in (ROOT, os.path.join(ROOT, "agents")):
        for name in os.listdir(folder):
            if name.endswith(".py"):
                text = open(os.path.join(folder, name)).read()
                assert not imports.search(text), name
                assert "teaching_pairs.py" not in text or "tools/teaching_pairs.py" in text, name
