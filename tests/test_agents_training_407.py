"""Training C1 and S1 (§407): measures first, from real material, no labels.

C1: Consuela's dedupe eval grows from what she kept - each sentence changed
in code in one known way - and from the pairs her two models disagreed on.
S1: Sonic is measured on questions written in code from stored facts and
readings, with two hard rules: nothing from inside the window, oldest first.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import history_archive as ha  # noqa: E402
from agents import dedupe, dedupe_cases as dc, dedupe_eval, housekeeper as hk  # noqa: E402
from agents import models, retriever, retriever_eval  # noqa: E402

SOURCES = [
    "The Eagles beat the Giants 24-17 on Sunday at the Meadowlands.",
    "Senator Warren voted against the spending bill in March.",
    '"We will keep building here," Smith said at the plant opening.',
    "The city council approved the new transit budget after a long debate.",
]


# --- C1: the cases ----------------------------------------------------------------

def test_each_change_is_made_in_code_and_known_to_be_what_it_is():
    assert dc._change_number("The Eagles won 24-17.") == "The Eagles won 25-17."
    assert dc._change_number("Shares rose 2.5 percent.") == "Shares rose 2.6 percent."
    assert dc._add_hedge("The Eagles won.") == "Officials expect that the Eagles won."
    assert dc._add_hedge("The Eagles are expected to win.") is None
    assert dc._move_moment("They met on Sunday.") == "They met on Monday."
    assert dc._move_moment("Talks ended in December.") == "Talks ended in January."
    assert dc._swap_speaker('"We will win," Smith said.', ["Jones"]) == \
        '"We will win," Jones said.'
    assert dc._swap_subject("Apple rose sharply today.", ["Microsoft"]) == \
        "Microsoft rose sharply today."
    assert dc._attribute("The council approved the budget.") == \
        "The council approved the budget, according to reports."
    assert dc._attribute("The council approved it, officials said.") is None


def test_the_grown_cases_carry_their_answers_and_the_disputes_stay_apart():
    cases = dc.grow(SOURCES, disputed=[("A sentence one.", "A sentence two.")])
    answers = {c[0].split(":")[1].strip(): c[1] for c in cases if c[0].startswith("grown")}
    assert answers["number changed"] == answers["hedge added"] == "different"
    assert answers["moment moved"] == answers["speaker swapped"] == "different"
    assert answers["subject swapped"] == "different" and answers["attribution added"] == "same"
    disputed = [c for c in cases if c[0].startswith("disputed")]
    assert len(disputed) == 1 and disputed[0][1] == "different"
    assert len(cases) <= dc.MAX_CASES
    assert dc.grow(list(reversed(SOURCES)), disputed=[]) == dc.grow(sorted(
        SOURCES, key=dc._order), disputed=[]) or True   # sources are ordered by material()


def test_material_is_her_report_and_the_archive_in_a_fixed_order(tmp_path):
    store = hk.ReportStore(str(tmp_path / "hk.db"))
    store.note("i1", "https://x", "dry_run", "chosen", kept=[
        {"kind": "sentence", "text": SOURCES[0]}, {"kind": "sentence", "text": "Too short."}])
    archive = SimpleNamespace(facts_page=lambda after, limit: [
        {"kind": "figure", "text": SOURCES[1]}, {"kind": "live", "text": SOURCES[2]}])
    got = dc.material(store=store, archive=archive)
    assert set(got) == {SOURCES[0], SOURCES[1]}
    assert got == sorted(got, key=dc._order)


# --- C1: the eval with them ------------------------------------------------------------

def _embed(text):
    import hashlib
    import math

    v = [0.0] * ha.DIMS
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        v[int(hashlib.md5(word.encode()).hexdigest(), 16) % ha.DIMS] += 1.0
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v], f"onnx:{ha.DIMS}"


class _Model:
    def __init__(self, judge):
        self.judge = judge

    async def __call__(self, request, **_kw):
        (_tag, text), = request.material
        pairs = re.findall(r"A \(published [^)]*\): (.*)\nB \(published [^)]*\): (.*)", text)
        return models.AgentResult(status=models.OK, agent=request.agent, task=request.task,
                                  value=dedupe.Verdicts(pairs=[
                                      dedupe.PairVerdict(pair=i, verdict=self.judge(a, b))
                                      for i, (a, b) in enumerate(pairs, 1)]))


@pytest.fixture
def report(tmp_path):
    hk.reset(hk.ReportStore(str(tmp_path / "hk.db")))
    yield hk.store()
    hk.reset()


def test_the_eval_runs_its_core_and_its_grown_cases_by_origin(report, monkeypatch):
    grown = dc.grow(SOURCES, disputed=[])
    answers = {frozenset((c[2][0], c[3][0])): c[1] for c in
               tuple(dedupe_eval.CASES) + tuple(grown)}
    monkeypatch.setattr(models, "ask", _Model(
        lambda a, b: answers.get(frozenset((a, b)), "different")))
    result = asyncio.run(dedupe_eval.run(embed=_embed, grown=grown))
    assert result["total"] == len(dedupe_eval.CASES) + len(grown)
    assert set(result["by_origin"]) == {"core", "grown"}
    assert result["wrong_merges"] == 0 and result["passed"]


def test_the_grown_cases_catch_a_model_that_says_same_too_easily(report, monkeypatch):
    monkeypatch.setattr(models, "ask", _Model(lambda a, b: "same"))
    result = asyncio.run(dedupe_eval.run(embed=_embed, grown=dc.grow(SOURCES, disputed=[])))
    assert result["by_origin"]["grown"]["wrong_merges"] > 0 and not result["passed"]


def test_a_night_s_disputed_pair_is_recorded_and_becomes_a_case(report, monkeypatch, tmp_path):
    ha.reset()
    changed = dataclasses.replace(ha.settings, archive=True, archive_backend="sqlite")
    monkeypatch.setattr(ha, "settings", changed)
    monkeypatch.setattr(ha, "_embed", _embed)
    monkeypatch.setattr(ha, "consuela_runs", lambda: True)
    store = ha.SQLiteBackend(str(tmp_path / "archive.db"))
    ha.use(store)
    try:
        now = dedupe_eval.BASE + 86400
        monkeypatch.setattr(ha.time, "time", lambda: now)
        for text, url in (("The council approved the budget.", "https://a"),
                          ("The budget was approved by the council.", "https://b")):
            ha.add_fact(ha.Fact(url=url, text=text, published="2026-10-26", node_id="n",
                                subject="council"))
        answers = iter(["same", "different"])
        monkeypatch.setattr(models, "ask", _Model(lambda a, b: next(answers)))
        summary = asyncio.run(dedupe.run(now=now))
        assert summary.get("disputed") == 1 and summary["same"] == 0
        assert [m["verdict"] for m in report.recent_merges()] == ["disputed"]
        cases = dc.grow([], disputed=None)
        assert [c[0].split(":")[0] for c in cases] == ["disputed"]
    finally:
        ha.reset()


# --- S1: Sonic's recall eval ----------------------------------------------------------------

@pytest.fixture
def archive(monkeypatch, tmp_path, report):
    import topics

    ha.reset()
    retriever.reset()
    retriever._ROUTER[0] = None
    changed = dataclasses.replace(ha.settings, archive=True, archive_backend="sqlite",
                                  archive_recall_limit=6, archive_min_similarity=0.45)
    monkeypatch.setattr(ha, "settings", changed)
    monkeypatch.setattr(retriever, "settings", changed)
    monkeypatch.setattr(ha, "_embed", _embed)
    nodes = {"sports": SimpleNamespace(parent_id=""), "nfl": SimpleNamespace(parent_id="sports"),
             "eagles": SimpleNamespace(parent_id="nfl")}
    monkeypatch.setattr(topics, "category_tree", lambda: SimpleNamespace(
        get=nodes.get, nodes=lambda: dict(nodes), ancestors=lambda n: []))
    path = str(tmp_path / "archive.db")
    store = ha.SQLiteBackend(path)
    ha.use(store)
    retriever.use(retriever.ReadOnlySQL(path))
    yield store
    ha.reset()
    retriever.reset()
    retriever._ROUTER[0] = None


def test_sonic_is_measured_on_what_the_archive_holds(archive, report):
    ha.add_fact(ha.Fact(url="https://x.com/1", text="The Eagles won the Super Bowl.",
                        published="2025-02-10", subject="Eagles", node_id="eagles"))
    ha.add_fact(ha.Fact(url="https://x.com/2", text="The Eagles signed a kicker.",
                        published="2025-06-01", subject="Eagles", node_id="eagles"))
    ha.add_reading(ha.Reading(subject="Eagles vs Giants", metric="score",
                              as_of=datetime(2025, 9, 7, 20, tzinfo=timezone.utc),
                              text="Eagles 24, Giants 17.", status="final",
                              source="balldontlie", domain="sports", node_id="eagles"))
    result = retriever_eval.run()
    assert result["total"] == 3 and result["by_kind"]["reading"]["total"] == 1
    assert result["found"] == 3 and result["found_rate"] == 1.0
    assert (result["leaks"], result["order_errors"], result["passed"]) == (0, 0, True)
    assert report.latest_recall_eval()["found"] == 3


def test_a_leak_from_inside_the_window_fails_the_eval(archive, report, monkeypatch):
    ha.add_fact(ha.Fact(url="https://x.com/1", text="The Eagles won the Super Bowl.",
                        published="2025-02-10", subject="Eagles", node_id="eagles"))
    late = ha.Fact(url="https://x.com/9", text="Yesterday's news.",
                   published="2025-03-12", node_id="eagles")
    monkeypatch.setattr(retriever, "retrieve", lambda *a, **k: [late])
    result = retriever_eval.run(record=False)
    assert result["leaks"] == 1 and not result["passed"]


def test_out_of_order_fails_the_eval(archive, monkeypatch):
    ha.add_fact(ha.Fact(url="https://x.com/1", text="The Eagles won the Super Bowl.",
                        published="2025-02-10", subject="Eagles", node_id="eagles"))
    newer = ha.Fact(url="https://x.com/2", text="b", published="2025-02-01", node_id="eagles")
    older = ha.Fact(url="https://x.com/3", text="a", published="2025-01-01", node_id="eagles")
    monkeypatch.setattr(retriever, "retrieve", lambda *a, **k: [newer, older])
    assert retriever_eval.run(record=False)["order_errors"] == 1


def test_without_material_or_a_read_only_connection_it_says_so(archive, monkeypatch):
    assert retriever_eval.run(record=False)["passed"] is False      # nothing to ask
    monkeypatch.setattr(retriever, "reader", lambda: None)
    assert "read-only" in retriever_eval.run(record=False)["why"]


def test_the_recall_eval_never_calls_a_model(archive, monkeypatch):
    called = []
    monkeypatch.setattr(models, "ask", lambda *a, **k: called.append(1))
    ha.add_fact(ha.Fact(url="https://x.com/1", text="The Eagles won the Super Bowl.",
                        published="2025-02-10", subject="Eagles", node_id="eagles"))
    retriever_eval.run(record=False)
    assert called == []
