"""Keith's first fixes, piece 1b: the day's model spend (§471).

Only a listener's episodes and the agents reached `metering`'s ledger:
editions, prefetch, the story composer, the category placer, the thumbnails
and the admin page's questions spent on Claude, and no daily figure held it.
Now every answer is counted once in `provider_usage`'s `spend` table, by
what spent it, priced at `metering.PRICES`, an unpriced model never at $0.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic_client  # noqa: E402
import audio_store  # noqa: E402
import config  # noqa: E402
import db  # noqa: E402
import metering  # noqa: E402
import pipeline_errors  # noqa: E402
import provider_usage  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
NOON = 1_790_000_000.0          # 2026-09-21 12:53 UTC
DAY = 86400


def setting(monkeypatch, **changes):
    """Settings are frozen: the report reads a copy with these changes."""
    monkeypatch.setattr(config, "settings",
                        dataclasses.replace(config.settings, **changes))


def rows(now=NOON):
    return {r["provider"]: r for r in provider_usage.report(now)}


class _Usage:
    def __init__(self, i=0, o=0, r=0, w=0):
        self.input_tokens, self.output_tokens = i, o
        self.cache_read_input_tokens, self.cache_creation_input_tokens = r, w


# ------------------------------------------------------------------ metering
def test_every_model_answer_reaches_the_days_spend():
    metering.Usage().add_model_call("claude-haiku-5-5", _Usage(i=1_000_000, o=200_000))
    spent = provider_usage.spend_report()
    assert spent["today"] == pytest.approx(0.10 + 0.10)
    assert spent["by_source"][0]["source"] == "episodes"
    assert spent["calls_today"] == 1 and spent["unpriced_today"] == 0


def test_an_unpriced_model_is_counted_unpriced_never_at_nothing():
    metering.Usage().add_model_call("claude-unknown-9", _Usage(i=10, o=10))
    spent = provider_usage.spend_report()
    assert spent["today"] == 0 and spent["unpriced_today"] == 1


def test_a_batched_call_is_counted_at_the_batch_price():
    metering.Usage().add_model_call("claude-haiku-5-5", _Usage(i=1_000_000),
                                    batched=True)
    assert provider_usage.spend_report()["today"] == pytest.approx(0.05)


def test_folding_an_answer_in_twice_counts_it_once():
    metering.Usage().add_model_call("claude-haiku-5-5", _Usage(i=1_000_000),
                                    count=False)
    assert provider_usage.spend_report() is None or \
        provider_usage.spend_report()["calls_today"] == 0


def test_spend_is_attributed_to_the_job_that_spent_it():
    async def edition():
        metering.attribute_task("edition:trending")
        metering.Usage().add_model_call("claude-haiku-5-5", _Usage(i=1_000_000))

    asyncio.run(edition())
    with metering.spend_source("prefetch"):
        metering.Usage().add_research(3, 0.003)
    metering.Usage().add_model_call("claude-haiku-5-5", _Usage(o=1_000_000))
    by = {g["source"]: g for g in provider_usage.spend_report()["by_source"]}
    assert by["edition:trending"]["today"] == pytest.approx(0.10)
    assert by["prefetch"]["today"] == pytest.approx(0.003)
    assert by["episodes"]["today"] == pytest.approx(0.50)
    # Claude's row on the page says what was spent.
    assert "spent today $0.60" in rows(now=None)["anthropic"]["limit"]


def test_an_agent_call_with_notes_is_counted_once(monkeypatch, tmp_path):
    from tests.test_agents_consent import FakeAdapter, housekeeper
    from agents import adapters, models, rulebooks
    from tests.test_agents_model_layer import use

    fake = FakeAdapter()
    fake.vendor = "anthropic"
    monkeypatch.setattr(models.adapters, "adapter_for", lambda vendor: fake)
    use(monkeypatch, agents=True)
    use(monkeypatch, housekeeper=True)
    use(monkeypatch, housekeeper_model="anthropic:claude-haiku-5-5")
    monkeypatch.setattr(models.rulebooks, "check",
                        lambda book: rulebooks.Lock(ok=True, version="1", sha="s"))
    monkeypatch.setattr(models.spend_guard, "enabled", lambda: False)
    models.reset(metering.MeterStore(str(tmp_path / "m.db")))
    try:
        notes = SimpleNamespace(usage=metering.Usage())
        result = asyncio.run(models.ask(housekeeper(words=False), notes=notes))
    finally:
        models.reset(None)
    assert result.status == models.OK, result.reason
    assert notes.usage.model_calls == 1
    spent = provider_usage.spend_report()
    assert spent["calls_today"] == 1
    assert spent["by_source"][0]["source"] == "agent:housekeeper"


#: Where an answer is folded in by its caller, not beside the request.
_FOLDED_ELSEWHERE = {
    "claude_batch.py": "each answer is folded in by script_generator (batched=True)",
    "agents/adapters/anthropic_adapter.py": "agents/models.py folds the answer in",
    "compare_models.py": "an operator's tool, run by hand",
    "diagnose_api.py": "an operator's tool, run by hand",
}
_CALL = re.compile(r"\.messages\.(create|stream)\(|\.batches\.create\(")


def test_every_claude_call_in_fam_reaches_the_days_spend():
    """Derived from the code, not listed by hand: every request to Claude in
    FAM's modules is followed, in the same function, by the line that counts
    its answer. A new call without one fails here."""
    missing = []
    files = sorted(ROOT.glob("*.py")) + sorted((ROOT / "agents").rglob("*.py"))
    for path in files:
        name = str(path.relative_to(ROOT))
        if name in _FOLDED_ELSEWHERE:
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if not _CALL.search(line) or line.lstrip().startswith("#"):
                continue
            after = "\n".join(lines[i:i + 60])
            if "add_model_call" not in after and "count_call" not in after:
                missing.append(f"{name}:{i + 1}")
    assert not missing, f"Claude calls the day's spend never sees: {missing}"



@pytest.mark.skipif(not db.enabled(), reason="no DATABASE_URL")
def test_the_spend_table_on_postgres(tmp_path):
    # The deployment's own path: `exists` must find it with no file there.
    usage = provider_usage.UsageStore()
    assert not os.path.exists(usage.path)
    provider_usage.reset(usage)
    metering.Usage().add_model_call("claude-haiku-5-5", _Usage(i=1_000_000))
    metering.Usage().add_model_call("claude-haiku-5-5", _Usage(i=1_000_000))
    provider_usage.record("anthropic")
    assert provider_usage.spend_report()["today"] == pytest.approx(0.20)
    provider_usage.reset(None)
    assert provider_usage.exists()
