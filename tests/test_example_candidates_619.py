"""Candidates for the writer's examples, waiting on the owner (§619).

`examples/candidates/` holds a three-minute example and before-and-after
pairs written for the owner to read. Until the owner moves one into
`examples/` or `examples/pairs/`, the writer's instructions must not change
by a byte - and each candidate must already be the thing it would become.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib

import pytest

import config
import script_generator as sg

ROOT = pathlib.Path(__file__).resolve().parent.parent
CANDIDATES = ROOT / "examples" / "candidates"
EXAMPLES = sorted(CANDIDATES.glob("*.txt"))
PAIRS = sorted((CANDIDATES / "pairs").glob("*.txt"))


def _tool():
    spec = importlib.util.spec_from_file_location(
        "teaching_pairs", os.path.join(ROOT, "tools", "teaching_pairs.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_there_are_candidates_to_read():
    assert EXAMPLES and PAIRS


def test_the_writer_is_shown_no_candidate():
    block = sg.style_example_block() + sg.teaching_pair_block()
    loaded = {query for query, _ in sg.STYLE_EXAMPLES} | {p["query"] for p in sg.TEACHING_PAIRS}
    for path in EXAMPLES + PAIRS:
        text = path.read_text(encoding="utf-8")
        query = text.partition("\n")[0].strip()
        if query in loaded:        # the owner moved it in: no longer only a candidate
            continue
        body = sg.parse_pair(text).get("after") or text.partition("\n\n")[2].strip()
        assert body.split(". ")[1] not in block, path.name


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_a_candidate_example_is_about_its_length(path):
    minutes = int(path.name.split("-", 1)[0])
    head, _, body = path.read_text(encoding="utf-8").partition("\n\n")
    assert head.strip() and "\n" not in head.strip()
    budget = minutes * config.Settings().target_wpm
    assert 0.9 * budget <= len(body.split()) <= 1.35 * budget, path.name


@pytest.mark.parametrize("path", PAIRS, ids=lambda p: p.name)
def test_a_candidate_pair_is_a_pair_the_code_prefers(path):
    tool = _tool()
    pair = sg.parse_pair(path.read_text(encoding="utf-8"))
    assert pair, path.name
    before = tool.flaws(tool._split(pair["before"]), pair["query"])
    after = tool.flaws(tool._split(pair["after"]), pair["query"])
    assert len(after) < len(before), (path.name, before, after)
    # No longer than the draft it fixes: a rewrite teaches, it does not add.
    assert len(pair["after"].split()) <= len(pair["before"].split()), path.name
    assert pair["why"].startswith("The draft ") and pair["why"].endswith("the rewrite does not.")
