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


def test_none_low_writes_at_low_effort_and_the_rest_at_high():
    tool = _eval()
    assert "none_low" in tool.ARMS and "none_low" not in tool.DEFAULT_ARMS
    edition = ScriptNotes(edition=True, ahead=True)
    with tool._Settings("none_low"):
        assert sg.writer_for(edition)[1] == "low"
    with tool._Settings("none"):
        assert sg.writer_for(edition)[1] == "high"
    assert sg.writer_for(edition)[1] == "high"          # taken back after


# ------------------------------------------------------------------ §621
def _adopt(monkeypatch):
    """Put §620's example and one pair where the writer reads them, as they
    were when the arms were made (they were taken back out in §624)."""
    import pathlib

    folder = pathlib.Path(ROOT) / "examples" / "candidates"
    query, _, script = (folder / "3-virus-and-bacteria.txt").read_text().partition("\n\n")
    monkeypatch.setattr(sg, "STYLE_EXAMPLES", sg.STYLE_EXAMPLES + [(query.strip(), script.strip())])
    monkeypatch.setitem(sg.EXAMPLE_STEMS, query.strip(), "3-virus-and-bacteria")
    pairs = [sg.parse_pair(p.read_text()) for p in sorted((folder / "pairs").glob("*.txt"))]
    monkeypatch.setattr(sg, "TEACHING_PAIRS", pairs)
    return pairs


def test_the_arms_that_measure_what_an_adoption_buys(monkeypatch):
    tool = _eval()
    assert {"none_unpaired", "none_before_619"} <= set(tool.ARMS)
    _adopt(monkeypatch)
    with_all = sg.system_prompt()
    assert "<pair query=" in with_all and "virus and bacteria" in with_all
    with tool._Settings("none_unpaired"):
        unpaired = sg.system_prompt()
    with tool._Settings("none_before_619"):
        before = sg.system_prompt()
    with tool._Settings("none"):
        assert sg.system_prompt() == with_all
    assert "<pair query=" not in unpaired and "virus and bacteria" in unpaired
    assert "<pair query=" not in before and "virus and bacteria" not in before
    assert sg.system_prompt() == with_all               # taken back after


def test_a_comparison_leaves_out_the_questions_the_pairs_were_made_from(tmp_path, monkeypatch):
    import json

    import episode_quality

    tool = _eval()
    # The six pairs are candidates, not committed (§624) - and a question a
    # candidate was made from is left out too (§627).
    seen = tool.seen_questions()
    assert len(seen) == len(tool.candidate_pairs()) == 6

    def script(i, arm, mark, claims):
        grade = {m: {"score": mark} for m in episode_quality.METRICS}
        grade["unsupported_claims"] = ["x"] * claims
        (tmp_path / f"q{i:02d}.{arm}.json").write_text(json.dumps(
            {"arm": arm, "grade": grade, "ear": 1.0, "sentences": ["A."]}))

    held_out = [i for i in range(len(tool.QUESTIONS)) if i not in seen][:4]
    for i in held_out:
        script(i, "none", 4, 0)
        script(i, "none_before_619", 3, 1)
    one_seen = sorted(seen)[0]
    script(one_seen, "none", 5, 0)
    script(one_seen, "none_before_619", 1, 0)
    table = tool.compare(tmp_path, "none", "none_before_619", leave_out=seen, draws=200)
    assert table["questions"] == 4
    assert table["mean of six"] == {"difference": 1.0, "low": 1.0, "high": 1.0,
                                    "better": 4, "worse": 0, "level": 0}
    # One fewer unsupported claim a script is better, not worse (§627).
    assert table["unsupported claims"] == {"difference": -1.0, "low": -1.0, "high": -1.0,
                                           "better": 4, "worse": 0, "level": 0}
    everything = set(range(len(tool.QUESTIONS)))
    assert tool.compare(tmp_path, "none", "none_before_619",
                        leave_out=everything - seen, draws=200)["questions"] == 1


# ------------------------------------------------------------------ §627
def test_candidates_can_be_measured_without_adopting_them():
    tool = _eval()
    plain = sg.system_prompt()
    assert "<pair query=" not in plain and "virus and bacteria" not in plain
    minutes = dict(sg.EXAMPLE_MINUTES)
    with tool._Settings("none_candidates"):
        shown = sg.system_prompt()
        assert shown.count("<pair query=") == len(tool.candidate_pairs()) == 6
        assert 'minutes="3"' in shown and "virus and bacteria" in shown
    with tool._Settings("none"):
        assert sg.system_prompt() == plain
    # Taken back after: nothing a listener hears is touched.
    assert sg.system_prompt() == plain and sg.TEACHING_PAIRS == []
    assert sg.EXAMPLE_MINUTES == minutes
