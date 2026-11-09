"""The story shapes are the signed playbook's, word for word (§505).

The owner (2026-11-09): "Keep the list as drafted and sign the wording".
Each shape's text lives in the journalism playbook's Part 3, "Story
shapes", which the owner signs (the rule-book manifest covers the playbook) -
waiting in `docs/agent_rule_proposals/` until it is signed;
`episode_intelligence.STRUCTURES` must hold the same words, so the writer
reads exactly what was signed.
"""
from __future__ import annotations

import os
import re

import episode_intelligence as ei

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _playbook() -> str:
    """The playbook as it will be signed: the proposal while it waits for
    the owner's signature (docs/agent_rule_proposals/), else the signed one.
    A session never edits the signed book itself."""
    proposed = os.path.join(ROOT, "docs", "agent_rule_proposals", "journalism_playbook.md")
    signed = os.path.join(ROOT, "docs", "journalism_playbook.md")
    return open(proposed if os.path.exists(proposed) else signed, encoding="utf-8").read()


def _shapes_section() -> str:
    text = _playbook()
    start = text.index("### Story shapes")
    return text[start:text.index("\n## Part 4", start)]


def test_every_shape_is_the_playbooks_word_for_word():
    section = " ".join(_shapes_section().split())
    for key, shape in ei.STRUCTURES.items():
        assert f"(`{key}`)" in section, key
        if shape:
            assert f"*The shape:* {shape}." in section, key


def test_the_playbook_names_no_shape_the_code_lacks():
    named = set(re.findall(r"\(`([a-z_]+)`\)", _shapes_section()))
    assert named == set(ei.STRUCTURES)


def test_each_shape_opens_and_lands_and_says_what_it_never_does():
    for key, shape in ei.STRUCTURES.items():
        if shape:
            assert shape.startswith("open on "), key
            assert "; land on " in shape and "; never " in shape, key


def test_no_shape_ends_on_a_forecast_or_a_summary_any_more():
    """The three endings §503 found against the rules are gone."""
    joined = " ".join(ei.STRUCTURES.values())
    for old in ("then what happens next", "then what it implies",
                "the way to think about it afterwards"):
        assert old not in joined


def test_the_writer_reads_the_signed_words():
    note = ei.build_structure_note(ei.Brief(structure="sports_recap"))
    assert ei.STRUCTURES["sports_recap"] in note
    assert "Drop any part you have nothing real for" in note
