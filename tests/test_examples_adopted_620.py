"""The three-minute example and the six before-and-after pairs (§619, §620).

The owner adopted all of §619's candidates, so the writer's cached
instructions now carry them. Each pair must stay the thing
`tools/teaching_pairs.py` would have kept: a rewrite the code prefers to
its draft, no longer than it, with a reason line.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib

import pytest

import script_generator as sg

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAIRS = sorted((ROOT / "examples" / "pairs").glob("*.txt"))


def _tool():
    spec = importlib.util.spec_from_file_location(
        "teaching_pairs", os.path.join(ROOT, "tools", "teaching_pairs.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_writer_is_shown_every_committed_pair():
    assert 1 <= len(PAIRS) <= sg.MAX_TEACHING_PAIRS      # one past six is never shown
    assert len(sg.TEACHING_PAIRS) == len(PAIRS)
    block = sg.teaching_pair_block()
    assert block.count("<pair query=") == len(PAIRS)
    assert block in sg.system_prompt()


@pytest.mark.parametrize("path", PAIRS, ids=lambda p: p.name)
def test_a_pair_is_one_the_code_prefers(path):
    tool = _tool()
    pair = sg.parse_pair(path.read_text(encoding="utf-8"))
    assert pair, path.name
    before = tool.flaws(tool._split(pair["before"]), pair["query"])
    after = tool.flaws(tool._split(pair["after"]), pair["query"])
    assert len(after) < len(before), (path.name, before, after)
    assert len(pair["after"].split()) <= len(pair["before"].split()), path.name
    assert pair["why"].startswith("The draft ") and pair["why"].endswith("the rewrite does not.")


def test_a_pair_never_says_what_it_does_not_have():
    # The house rule the pairs must not un-teach ([missed-not-absent]).
    for path in PAIRS:
        after = sg.parse_pair(path.read_text(encoding="utf-8"))["after"].lower()
        for phrase in ("i don't have", "we have is", "i can establish", "the reporting we"):
            assert phrase not in after, (path.name, phrase)


def test_there_is_an_example_at_every_whole_minute():
    minutes = {float(m) for m in sg.EXAMPLE_MINUTES.values()}
    assert {1.0, 2.0, 3.0, 4.0, 5.0} <= minutes
    assert sg.EXAMPLE_FACETS["3-virus-and-bacteria"] == "health"
    assert ' minutes="3"' in sg.style_example_block()
