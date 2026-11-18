"""What the §521 comparison changed (§527).

Matched examples are off by default (no gain, more repetition); the plan
tells the writer that its landing is the episode's last sentence (the
writer ended where Shakespeare planned in only 35-40% of episodes); and the
comparison gained `none_low`, the writer at low effort, to measure what high
effort buys.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import os

import script_generator as sg
from agents import editor
from agents.editor import EditorAnswer, _Beat
from episode_intelligence import Brief
from script_generator import ScriptNotes, build_prompt, plan_episode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVIDENCE = ("SOURCE 1\nTitle: Fed holds\nPublished: 2026-11-02 (yesterday)\n"
            "Source type: established outlet\nKey evidence:\n"
            "The Fed held rates at 4.25 percent. Two members of the committee dissented.\n")


def test_matched_examples_are_off_by_default():
    assert sg.settings.example_matching is False
    plan = dataclasses.replace(plan_episode("how are the eagles doing", 1.5),
                               brief=Brief(category_path=["sports"]))
    assert "closest to this kind of story" not in build_prompt(plan)


def test_the_landing_wording_is_back_as_it_was():
    """Worded as the last sentence, the landing did not move (§527); the
    plain wording is back (§528)."""
    plan = dataclasses.replace(plan_episode("what did the fed do", 1.5), search=False,
                               evidence=EVIDENCE)
    sources = editor.number_packets(plan)
    answer = EditorAnswer(answers="x", summary=["P1.1"], opening=["P1.1"],
                          beats=[_Beat(purpose="the split", sentences=["P1.2"])],
                          landing=["P1.2"], thin=False, missing=[])
    built, _ = editor.to_outline(answer, sources)
    block = editor.writer_block(built, "x", sources)
    assert "\nLand on:\n- Two members of the committee dissented." in block
    assert "your last sentence" not in block


def _eval():
    spec = importlib.util.spec_from_file_location(
        "editor_eval", os.path.join(ROOT, "tools", "editor_eval.py"))
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


def test_none_low_writes_at_low_effort_and_the_rest_at_the_ahead_effort():
    tool = _eval()
    assert "none_low" in tool.ARMS and "none_low" not in tool.DEFAULT_ARMS
    edition = ScriptNotes(edition=True, ahead=True)
    with tool._Settings("none_low"):
        assert sg.writer_for(edition)[1] == "low"
    with tool._Settings("none"):
        assert sg.writer_for(edition)[1] == "medium"    # AHEAD_WRITER_EFFORT (§652)
    assert sg.writer_for(edition)[1] == "medium"        # taken back after


# ------------------------------------------------------------------ §627
def _adopt(monkeypatch, tool):
    """Put §626's example and the candidate pairs where the writer reads
    them, as they were when the arms were made (taken back out in §630)."""
    import pytest

    named = [c for c in tool.candidate_examples() if c[2] in tool.ADOPTED_626]
    pairs = tool.candidate_pairs()
    if not named or not pairs:
        pytest.skip("the candidates these arms were made for have moved")
    query, script, stem = named[0]
    monkeypatch.setattr(sg, "STYLE_EXAMPLES", sg.STYLE_EXAMPLES + [(query, script)])
    monkeypatch.setitem(sg.EXAMPLE_STEMS, query, stem)
    monkeypatch.setattr(sg, "TEACHING_PAIRS", pairs)
    return query, pairs


def test_the_arms_that_measured_what_the_adoption_bought(monkeypatch):
    tool = _eval()
    assert {"none_unpaired", "none_before_625"} <= set(tool.ARMS)
    query, _ = _adopt(monkeypatch, tool)
    with_all = sg.system_prompt()
    assert "<pair query=" in with_all and query in with_all
    with tool._Settings("none_unpaired"):
        unpaired = sg.system_prompt()
    with tool._Settings("none_before_625"):
        before = sg.system_prompt()
    with tool._Settings("none"):
        assert sg.system_prompt() == with_all
    assert "<pair query=" not in unpaired and query in unpaired
    assert "<pair query=" not in before and query not in before
    assert sg.system_prompt() == with_all               # taken back after


def _script(folder, i, arm, marks, claims=0, **more):
    import json

    import episode_quality

    marks = marks if isinstance(marks, dict) else {m: marks for m in episode_quality.METRICS}
    grade = {m: {"score": v} for m, v in marks.items()}
    grade["unsupported_claims"] = ["x"] * claims
    (folder / f"q{i:02d}.{arm}.json").write_text(json.dumps(
        {"arm": arm, "grade": grade, "ear": 1.0, "sentences": ["A."], **more}))


def test_a_comparison_is_paired_and_leaves_out_what_it_should(tmp_path):
    import episode_quality

    tool = _eval()
    # A question a pair was made from is left out, committed or candidate (§633).
    seen = tool.seen_questions()
    assert len(seen) == len(tool.candidate_pairs()) + len(sg.TEACHING_PAIRS)
    held_out = [i for i in range(len(tool.QUESTIONS)) if i not in seen][:5]
    for i, a, b, claims_a, claims_b in zip(held_out, (4, 4, 3, 5), (3, 4, 3, 3),
                                           (0, 1, 2, 0), (1, 1, 1, 2)):
        _script(tmp_path, i, "none", a, claims_a)
        _script(tmp_path, i, "none_before_625", b, claims_b)
    # A script short of one of the six marks is not compared at all (§633).
    short = {m: 5 for m in episode_quality.METRICS[:-1]}
    _script(tmp_path, held_out[4], "none", 1)
    _script(tmp_path, held_out[4], "none_before_625", short)
    if seen:
        _script(tmp_path, sorted(seen)[0], "none", 5)
        _script(tmp_path, sorted(seen)[0], "none_before_625", 1)
    table = tool.compare(tmp_path, "none", "none_before_625", leave_out=seen, draws=2000)
    assert table["questions"] == 4
    row = table["mean of six"]
    assert row["difference"] == 0.75 and (row["better"], row["worse"], row["level"]) == (2, 0, 2)
    # A real interval: inside the range of the differences, around the mean, not a point.
    assert 0 <= row["low"] < row["difference"] < row["high"] <= 2
    # Fewer unsupported claims is the better script, not the worse (§633).
    claims = table["unsupported claims"]
    assert claims["difference"] == -0.5
    assert (claims["better"], claims["worse"], claims["level"]) == (2, 1, 1)
    assert set(episode_quality.METRICS) <= set(table)       # every row, whatever the first script held


# ------------------------------------------------------------------ §633
def test_candidates_can_be_measured_without_adopting_them(monkeypatch):
    import dataclasses

    import pytest

    tool = _eval()
    examples, pairs = tool.candidate_examples(), tool.candidate_pairs()
    if not examples and not pairs:
        pytest.skip("nothing is waiting in examples/candidates/")
    plain, minutes = sg.system_prompt(), dict(sg.EXAMPLE_MINUTES)
    # Even with the pairs switched off in the environment, the arm shows them.
    monkeypatch.setattr(sg, "settings", dataclasses.replace(sg.settings, teaching_pairs=False))
    off = sg.system_prompt()
    with tool._Settings("none_candidates"):
        shown = sg.system_prompt()
        assert shown.count("<pair query=") == min(
            len(pairs) + len(sg.TEACHING_PAIRS), sg.MAX_TEACHING_PAIRS)
        for query, _, _ in examples:
            assert query in shown
        # In file-name order among the examples, as adopting them would give.
        stems = [sg.EXAMPLE_STEMS.get(q) or next(s for cq, _, s in examples if cq == q)
                 for q, _ in sg.STYLE_EXAMPLES]
        assert stems == sorted(stems)
    assert sg.system_prompt() == off and sg.EXAMPLE_MINUTES == minutes
    assert tool.instructions_mark("none_candidates") != tool.instructions_mark("none")


def test_a_failed_swap_leaves_nothing_swapped(monkeypatch):
    import pytest

    tool = _eval()
    plain, examples, pairs = sg.system_prompt(), sg.STYLE_EXAMPLES, sg.TEACHING_PAIRS
    settings = sg.settings

    def broken():
        raise UnicodeDecodeError("utf-8", b"", 0, 1, "a file that cannot be read")

    monkeypatch.setattr(tool, "candidate_pairs", broken)
    with pytest.raises(UnicodeDecodeError):
        with tool._Settings("none_candidates"):
            pass
    assert sg.system_prompt() == plain and sg.settings is settings
    assert sg.STYLE_EXAMPLES is examples and sg.TEACHING_PAIRS is pairs


def test_a_script_from_other_instructions_is_not_taken_for_this_arms(tmp_path, capsys):
    import asyncio
    import pickle

    from script_generator import plan_episode

    tool = _eval()
    question = tool.QUESTIONS[0]
    (tmp_path / "q00.plan.pickle").write_bytes(pickle.dumps(
        {"group": question[0], "question": question[1], "plan": plan_episode(question[1], 2)}))
    _script(tmp_path, 0, "none", 3, instructions="0123456789ab")
    asyncio.run(tool.run(None, tmp_path, [0], ["none"], True))
    assert "written from other instructions" in capsys.readouterr().out
    _script(tmp_path, 0, "none", 3, instructions=tool.instructions_mark("none"))
    asyncio.run(tool.run(None, tmp_path, [0], ["none"], True))
    assert "other instructions" not in capsys.readouterr().out
