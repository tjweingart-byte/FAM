"""docs/FAM_AGENTS.md is the source of the tabbed agents page (§311).

Each agent's `##` carries the same sub-tabs, names only pipeline stages the
page can draw, and the builder gives every `##` a tab.
"""
import importlib.util
import pathlib
import re

import pytest

DOCS = pathlib.Path(__file__).resolve().parent.parent / "docs"
SRC = (DOCS / "FAM_AGENTS.md").read_text()
SUBTABS = ["Guardrails", "Prompting it, and breaking it", "Plugin options",
           "Settings and open questions"]


def _builder():
    spec = importlib.util.spec_from_file_location("build_agents_html", DOCS / "build_agents_html.py")
    pytest.importorskip("markdown")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sections():
    return re.split(r"^## ", SRC, flags=re.M)[1:]


def test_every_agent_has_the_same_subtabs():
    agents = [s for s in _sections() if not s.startswith(("Shared rules", "Template"))]
    assert {s.split("\n", 1)[0] for s in agents} == {
        "1. Overlord", "2. Contextualizer", "3. Editor in chief",
        "4. Script supervisor", "5. Housemaid (Consuela)", "6. IT guy (Keith Wong)"}
    for section in agents + [s for s in _sections() if s.startswith("Template")]:
        subs = re.findall(r"^### (.*)$", section, flags=re.M)
        assert subs == SUBTABS, (section.split("\n", 1)[0], subs)


def test_stages_are_ones_the_page_draws():
    builder = _builder()
    known = {sid for sid, _name, _lane in builder.STAGES}
    for section in _sections():
        stages = builder.stages_of(section)
        assert stages, f"{section.split(chr(10), 1)[0]} names no stages"
        lit, feeds = stages
        assert set(lit) | set(feeds) <= known


def test_builder_gives_each_section_a_tab(tmp_path):
    builder = _builder()
    builder.OUT = tmp_path / "agents.html"
    page = builder.build().read_text()
    assert page.count('class="tab"') == len(_sections())
    assert '<span class="chip on" data-stage="orchestrate">' in page
    assert '<span class="chip on" data-stage="editor">' in page
    assert '<span class="chip feeds" data-stage="recall">' in page


def test_tandem_pairs_are_drawn_as_one_step(tmp_path):
    builder = _builder()
    page = builder.pipeline(([], []))
    steps = re.findall(r'<li class="step( tandem)?"[^>]*>(.*?)</li>', page)
    pairs = [re.findall(r'data-stage="(\w+)"', inner) for tandem, inner in steps if tandem]
    assert pairs == [["ei", "context"], ["editor", "write"]]
    assert "<agent>" not in page
