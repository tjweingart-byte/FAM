"""tools/compare_backends.py (§356): Parallel against Perplexity, one brief.

No key and no network: the generator is a stand-in that records which
backend the shared settings named while it searched, so what is tested is the
harness - one brief for both, the index pinned for each run, the settings put
back, and the blind files and the report written.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import compare_backends as cb  # noqa: E402
import episode_quality  # noqa: E402
from config import settings  # noqa: E402


def _packet(sources: int, dated: str = "2026-10-28") -> str:
    parts = []
    for n in range(1, sources + 1):
        parts += [f"SOURCE {n}", f"Title: story {n}",
                  f"Published: {dated if n % 2 else 'unknown'} (two days ago)",
                  "Source type: major outlet", "Key evidence:",
                  "The council voted seven to two on Tuesday.", ""]
    return "\n".join(parts)


@dataclasses.dataclass
class _Brief:
    retrieval: str = "council vote"
    degraded: bool = False


class FakeGenerator:
    """Records what the shared settings said at the moment each search ran."""

    def __init__(self):
        self.client = None
        self.understood = 0
        self.searched = []

    async def understand(self, plan, notes=None):
        self.understood += 1
        return dataclasses.replace(plan, brief=_Brief())

    async def prepare(self, plan, notes):
        backend = settings.research_backend
        self.searched.append((backend, settings.research_second_look,
                              settings.archive_recall, id(plan.brief)))
        sources = 4 if backend == "parallel" else 2
        notes.research = {"backend": backend, "rungs": [backend],
                          "fell_back_from": "", "retried": backend == "perplexity",
                          "searches": 1, "seconds": 0.4, "cost": 0.001,
                          "results_returned": 10, "sources": ["a.com", "b.org"],
                          "missing": [] if backend == "parallel" else ["the vote count"]}
        return dataclasses.replace(plan, evidence=_packet(sources),
                                   thin_on=tuple(notes.research["missing"]))

    async def stream_sentences(self, plan, notes):
        for sentence in ("The council voted seven to two on Tuesday.",
                         "The new rule starts in March."):
            yield sentence


def _args(**over):
    base = dict(write=True, grade=True, minutes=3.0)
    base.update(over)
    return argparse.Namespace(**base)


def test_one_brief_both_indexes_and_the_settings_put_back(tmp_path, monkeypatch):
    async def fake_grade(*_a, **_k):
        return ({m: {"score": 4, "reason": "ok"} for m in episode_quality.METRICS},
                "grader", 0.002, "")

    monkeypatch.setattr(episode_quality, "grade_script", fake_grade)
    fake = FakeGenerator()
    monkeypatch.setattr(cb, "ScriptGenerator", lambda: fake)
    before = (settings.research_backend, settings.research_second_look,
              settings.archive_recall)

    out = tmp_path / "run"
    code = asyncio.run(cb._run(["what did the council decide"], _args(), str(out), 7, None))

    assert code == 0
    # EI once; both searches from that one brief, each index pinned, the
    # second look kept on it, the archive's recall off.
    assert fake.understood == 1
    assert [s[:3] for s in fake.searched] == [("parallel", "same", False),
                                              ("perplexity", "same", False)]
    assert fake.searched[0][3] == fake.searched[1][3]
    assert (settings.research_backend, settings.research_second_look,
            settings.archive_recall) == before

    key = json.loads((out / "key.json").read_text())
    assert sorted((key["q01"]["A"], key["q01"]["B"])) == sorted(cb.BACKENDS)
    blind = (out / "blind" / "q01-A.txt").read_text()
    assert "seven to two" in blind and "parallel" not in blind and "perplexity" not in blind
    assert (out / "packets" / "q01-perplexity.txt").exists()
    report = (out / "report.md").read_text()
    assert "parallel 1, perplexity 0, tied 0" in report
    assert "grade: information (mean, 1-5) | 4 | 4" in report
    assert "results.csv" in os.listdir(out)


def test_settings_are_put_back_when_a_run_raises():
    before = settings.research_backend
    try:
        with cb.configured(research_backend="perplexity"):
            assert settings.research_backend == "perplexity"
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert settings.research_backend == before


def test_packet_measures_reads_the_packet_shape():
    from datetime import datetime, timezone

    measured = cb.packet_measures(_packet(3, "2026-10-27"),
                                  datetime(2026, 10, 30, tzinfo=timezone.utc))
    assert measured["sources"] == 3
    assert measured["dated"] == 2  # every other source is "unknown"
    assert measured["median_age_days"] == 3.0
    assert measured["types"] == {"major outlet": 3}


def test_the_default_questions_load_and_the_dry_run_spends_nothing(capsys, monkeypatch):
    questions = cb.load_questions(cb.QUESTIONS_FILE)
    assert len(questions) >= 20 and not any(q.startswith("#") for q in questions)
    monkeypatch.setattr(sys, "argv", ["compare_backends.py", "--dry-run", "--limit", "2"])
    assert cb.main() == 0
    assert "2 questions" in capsys.readouterr().out
