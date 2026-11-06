"""Keith's first fixes, piece 1c: `pipeline_errors` keeps its own record (§472).

Past 2,000 waiting rows the oldest were dropped with no trace, a failed
write was a log line, and the `agents` and `audio` stages had no
explanation.
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


# ----------------------------------------------------------- pipeline_errors
def test_rows_dropped_while_the_store_is_unwritable_are_counted(monkeypatch):
    monkeypatch.setattr(pipeline_errors, "MAX_PENDING", 3)

    def refuse():
        raise OSError("database unreachable")

    monkeypatch.setattr(pipeline_errors, "store", refuse)
    for n in range(5):
        pipeline_errors.record("brief", f"timeout {n}")
    pipeline_errors.flush()
    state = pipeline_errors.status()
    assert state["behind"] and state["dropped"]["errors"] == 2
    assert state["waiting"] == 3
    # The kind of error, never its message: health is public (the review).
    assert state["last_failure"] == "could not open the store: OSError"
    report = pipeline_errors.report()
    assert report["tracking"]["dropped_total"] == 2
    top = report["kinds"][0]
    assert (top["stage"], top["kind"]) == ("tracking", "not written")
    assert "2 dropped" in top["last_detail"]
    assert "no explanation" not in top["explain"]["what"]


def test_a_failed_write_keeps_the_rows_and_says_so(tmp_path, monkeypatch):
    store = pipeline_errors.ErrorStore(str(tmp_path / "e.db"))
    pipeline_errors.reset(store)
    real = store.add

    def broken(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(store, "add", broken)
    pipeline_errors.record("writer", "refused")
    pipeline_errors.flush()
    assert pipeline_errors.status()["behind"]
    monkeypatch.setattr(store, "add", real)
    report = pipeline_errors.report()
    assert report["errors"]["day"] == 1 and not report["tracking"]["behind"]
    assert all(k["stage"] != "tracking" for k in report["kinds"])


@pytest.mark.parametrize("stage,kind", [
    ("agents", "contextualizer: held"), ("agents", "editor: failed"),
    ("agents", "supervisor: wrong_model"), ("agents", "housekeeper: unpriced"),
    ("audio", "upload failed")])
def test_the_agents_and_audio_stages_are_explained(stage, kind):
    assert stage in pipeline_errors.STAGES
    said = pipeline_errors.explain(stage, kind)
    assert "no explanation is written" not in said["what"]



def test_health_says_how_the_error_rows_are_kept():
    from fastapi.testclient import TestClient

    import app

    body = TestClient(app.app).get("/api/health").json()
    assert set(body["tracking"]) == {"provider_usage", "pipeline_errors"}
    assert body["tracking"]["pipeline_errors"]["dropped_total"] == 0
    assert body["tracking"]["pipeline_errors"]["behind"] is False
