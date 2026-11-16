"""The candidates folder (§625-§630).

A three-minute example and six before-and-after pairs were adopted (§626),
measured (§629) and taken back out (§630). They are kept, unloaded, in
`examples/candidates/`. The writer's instructions must not carry them, and
each must still be the thing it would become if it were measured again and
moved in.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import re

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


def test_the_writer_is_shown_no_candidate():
    if not (EXAMPLES or PAIRS):
        pytest.skip("nothing is waiting in examples/candidates/")
    block = sg.system_prompt()
    loaded = {query for query, _ in sg.STYLE_EXAMPLES} | {p["query"] for p in sg.TEACHING_PAIRS}
    for path in EXAMPLES + PAIRS:
        text = path.read_text(encoding="utf-8")
        assert text.partition("\n")[0].strip() not in loaded, path.name
        body = sg.parse_pair(text).get("after") or text.partition("\n\n")[2].strip()
        assert body[:80] not in block, path.name
    # And nothing is half-adopted: no facet, no worked plan in the loaded folder.
    for path in EXAMPLES:
        assert path.stem not in sg.EXAMPLE_FACETS
        assert not (ROOT / "examples" / "plans" / f"{path.stem}.json").exists()


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_a_candidate_example_is_about_its_length(path):
    minutes = int(path.name.split("-", 1)[0])
    head, _, body = path.read_text(encoding="utf-8").partition("\n\n")
    assert head.strip() and "\n" not in head.strip()
    budget = minutes * config.Settings().target_wpm
    assert 0.9 * budget <= len(body.split()) <= 1.35 * budget, path.name


@pytest.mark.parametrize("path", PAIRS, ids=lambda p: p.name)
def test_a_candidate_pair_is_one_the_code_prefers(path):
    tool = _tool()
    pair = sg.parse_pair(path.read_text(encoding="utf-8"))
    assert pair, path.name
    before = tool.flaws(tool._split(pair["before"]), pair["query"])
    after = tool.flaws(tool._split(pair["after"]), pair["query"])
    assert len(after) < len(before), (path.name, before, after)
    assert len(pair["after"].split()) <= len(pair["before"].split()), path.name
    assert pair["why"].startswith("The draft ") and pair["why"].endswith("the rewrite does not.")
    for sentence in re.split(r"(?<=[.!?])\s+", " ".join(pair["after"].split())):
        assert not sg.is_material_sentence(sentence), (path.name, sentence)


def test_the_folder_says_what_the_measurement_found():
    readme = (CANDIDATES / "README.md").read_text(encoding="utf-8")
    assert "storytelling was marked lower" in readme and "Measure first" in readme
    assert "none_candidates" in readme and "new folder" in readme
