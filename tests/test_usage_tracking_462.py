"""Keith's first fixes, piece 1a: what `provider_usage` counts (§462).

The audit (decided item 7, keith.md "always" 12) found X counted and never
shown; Claude, Google's image model and the R2 bucket never counted; no limit
a number, so nothing could say "80% of it"; a monthly limit never measured
over its month; a failed write only a log line; and on Postgres `exists()`
looking for a SQLite file, so after every deploy the page read no usage.
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


# ------------------------------------------------------------ provider_usage
def test_x_claude_the_image_model_and_r2_are_reported():
    shown = rows()
    for provider, label in (("x", "X"), ("anthropic", "Claude (Anthropic)"),
                            ("gemini", "Google image model"),
                            ("r2", "Cloudflare R2")):
        assert shown[provider]["label"] == label
        assert shown[provider]["limit"] and shown[provider]["next"]
    assert set(provider_usage.PROVIDERS) == set(provider_usage.LABELS), \
        "a provider counted but not reported is the X bug again"


def test_every_limit_is_a_number_or_said_to_be_none(tmp_path, monkeypatch):
    usage = provider_usage.UsageStore(str(tmp_path / "u.db"))
    provider_usage.reset(usage)
    setting(monkeypatch, gnews_daily_requests=100)
    for _ in range(80):
        usage.record("gnews", now=NOON)
    shown = rows()
    gnews = shown["gnews"]["allowance"]
    assert gnews == {"count": 100, "period": "day", "unit": "requests",
                     "kind": "quota", "used": 80, "share": 0.8}
    # A pace is a rate: daily counts cannot measure it, so it has no share.
    bdl = shown["balldontlie"]["allowance"]
    assert bdl["kind"] == "rate" and bdl["period"] == "minute" and "share" not in bdl
    # No limit is None - said, never zero.
    assert shown["polymarket"]["allowance"] is None
    assert shown["nws"]["allowance"] is None


def test_a_monthly_limit_is_measured_over_the_month(tmp_path, monkeypatch):
    usage = provider_usage.UsageStore(str(tmp_path / "u.db"))
    provider_usage.reset(usage)
    setting(monkeypatch, wikimedia_monthly_requests=1000)
    # NOON is the 21st: the 1st and the 20th are this month, the 31st of
    # the month before is not.
    first = NOON - 20 * DAY
    for at, n in ((first, 300), (NOON - DAY, 200), (NOON, 100),
                  (first - DAY, 999)):
        for _ in range(n):
            usage.record("wikimedia_enterprise", now=at)
    w = rows()["wikimedia_enterprise"]
    assert w["allowance"]["period"] == "month"
    assert w["allowance"]["used"] == 600 and w["allowance"]["share"] == 0.6
    # The 7-day average still covers the last seven days only.
    assert w["average"] == round(300 / 7, 1)


def test_x_is_measured_in_posts_from_its_own_count(monkeypatch):
    import x_threads

    setting(monkeypatch, x_threads_daily_reads=600)
    monkeypatch.setattr(x_threads, "_exists", lambda: True)
    monkeypatch.setattr(x_threads, "store",
                        lambda: SimpleNamespace(reads_since=lambda since: 450))
    x = rows()["x"]["allowance"]
    assert x["unit"] == "posts" and x["used"] == 450 and x["share"] == 0.75


def test_exists_asks_the_database_not_for_a_file(tmp_path, monkeypatch):
    """On Postgres there is no file: the page read empty after every deploy."""
    seen = []
    monkeypatch.setattr(provider_usage.store_db, "has_tables",
                        lambda path: seen.append(path) or True)
    assert provider_usage.exists()
    assert seen and seen[0].endswith("provider_usage.db")


def test_a_failed_write_is_said_on_the_page_and_kept(tmp_path, monkeypatch):
    usage = provider_usage.UsageStore(str(tmp_path / "u.db"))
    provider_usage.reset(usage)
    real = usage.add

    def broken(counts, spent=None):
        raise OSError("disk full")

    monkeypatch.setattr(usage, "add", broken)
    provider_usage.record("gnews")
    provider_usage.record("gnews")
    provider_usage.flush()
    state = provider_usage.status()
    assert state["behind"] and state["waiting"] == 2 and state["failures"] >= 1
    assert "disk full" in state["last_failure"]
    shown = rows(now=None)
    assert "not written yet" in shown["gnews"]["alert"]
    assert "disk full" in shown["gnews"]["alert"]
    assert "alert" not in shown["tiingo"]
    # Writable again: nothing was lost, and the alert goes.
    monkeypatch.setattr(usage, "add", real)
    shown = rows(now=None)
    assert shown["gnews"]["today"] == 2 and "alert" not in shown["gnews"]
    assert not provider_usage.status()["behind"]


def test_a_store_that_will_not_open_is_said_too(monkeypatch):
    def refuse():
        raise OSError("no database")

    monkeypatch.setattr(provider_usage, "store", refuse)
    provider_usage.record("tiingo")
    provider_usage.flush()
    state = provider_usage.status()
    assert state["behind"] and "could not open" in state["last_failure"]


def test_every_claude_request_is_counted_at_the_client():
    client = anthropic_client.build_http_client()
    assert anthropic_client._count_response in client.event_hooks["response"]

    def answered(code, path):
        request = httpx.Request("POST", f"https://api.anthropic.com{path}")
        return httpx.Response(code, request=request)

    for code, path in ((200, "/v1/messages"), (429, "/v1/messages"),
                       (200, "/v1/messages/batches")):
        asyncio.run(anthropic_client._count_response(answered(code, path)))
    claude = rows(now=None)["anthropic"]
    assert claude["today"] == 3 and claude["failed_today"] == 1
    assert {"why": "HTTP 429", "count": 1} in claude["failure_reasons"]
    parts = {b["league"]: b["today"] for b in claude["breakdown"]}
    assert parts == {"messages": 2, "batches": 1}


def test_r2_requests_are_counted_by_operation_class():
    def answer(request):
        if request.method == "GET" and request.url.path.endswith("gone"):
            return httpx.Response(404)
        return httpx.Response(200, content=b"x")

    bucket = audio_store.R2AudioStore("acct", "b", "k", "s",
                                      transport=httpx.MockTransport(answer))
    bucket.put("a.ogg", b"x")
    bucket.get("a.ogg")
    bucket.head("a.ogg")
    assert bucket.get("gone") is None
    bucket.delete("a.ogg")
    r2 = rows(now=None)["r2"]
    assert r2["today"] == 5 and r2["failed_today"] == 0   # a 404 is an answer
    parts = {b["league"]: b for b in r2["breakdown"]}
    assert parts["class A"]["today"] == 1 and parts["class B"]["today"] == 3
    assert parts["delete (free)"]["today"] == 1
    assert parts["class A"]["allowance"]["count"] == 1_000_000
    assert parts["class B"]["allowance"]["share"] == round(3 / 10_000_000, 4)


def test_image_requests_are_counted(monkeypatch):
    import thumbnails

    real = httpx.AsyncClient

    def mocked(*a, **kw):
        kw["transport"] = httpx.MockTransport(lambda r: httpx.Response(503))
        return real(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", mocked)
    resp = asyncio.run(thumbnails._google_post("gemini-test-image", "a lighthouse", "k"))
    assert resp.status_code == 503
    gemini = rows(now=None)["gemini"]
    assert gemini["today"] == 1 and gemini["failed_today"] == 1
    assert [b["league"] for b in gemini["breakdown"]] == ["images"]


# -------------------------------------------------------------------- health
# -------------------------------------------------------------------- health
def test_health_says_how_the_tracking_is_kept():
    from fastapi.testclient import TestClient

    import app

    body = TestClient(app.app).get("/api/health").json()
    assert body["tracking"]["provider_usage"]["behind"] is False
    assert body["tracking"]["provider_usage"]["failures"] == 0




@pytest.mark.skipif(not db.enabled(), reason="no DATABASE_URL")
def test_after_a_deploy_postgres_usage_is_still_there():
    """The bug itself: a fresh process on Postgres has no file, and used to
    read no usage until the first request was counted."""
    usage = provider_usage.UsageStore()
    assert not os.path.exists(usage.path)
    usage.record("gnews", now=NOON)
    provider_usage.reset(None)          # a new process: nothing in memory
    assert provider_usage.exists()
    assert rows()["gnews"]["today"] == 1
