"""Error tracking, the lint gate and gated deploys (§202).

Each promise is tested the way it would break: a DSN that reaches staging, a
listener's cookie or a provider key inside an event, a check added to the
local loop and not the gate, a deploy that no longer waits for CI.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import error_tracking  # noqa: E402
import spend_guard  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_state():
    saved = dict(error_tracking._STATE)
    yield
    error_tracking._STATE.clear()
    error_tracking._STATE.update(saved)


def test_off_without_a_dsn_and_says_why(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.setattr(spend_guard, "enabled", lambda: False)
    out = error_tracking.init()
    assert out["enabled"] is False and "SENTRY_DSN" in out["reason"]


def test_zero_spend_never_sends_even_with_a_dsn(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://public@o0.ingest.sentry.io/0")
    monkeypatch.setattr(spend_guard, "enabled", lambda: True)
    out = error_tracking.init()
    assert out["enabled"] is False and "zero spend" in out["reason"]


def test_staging_removes_the_dsn_before_anything_reads_it():
    assert "SENTRY_DSN" in spend_guard.PAID_CREDENTIALS


def test_an_event_carries_no_listener_and_no_credential():
    event = {
        "user": {"id": "listener-123", "ip_address": "198.51.100.4"},
        "request": {
            "url": "https://fam.example/api/x?apikey=SECRET1",
            "query_string": "token=SECRET2&q=eagles",
            "cookies": {"fam_session": "abc"},
            "data": {"password": "hunter2"},
            "headers": {"Authorization": "Bearer SECRET3", "Cookie": "fam=1",
                        "X-Forwarded-For": "198.51.100.4",
                        "X-FAM-Client": "web/1.0"},
        },
        "exception": {"values": [{
            "value": "422 for https://finnhub.io/api?token=SECRET4",
            "stacktrace": {"frames": [{"vars": {"listener": "listener-123"}}]},
        }]},
        "logentry": {"message": "GET https://gnews.io/?apikey=SECRET5",
                     "params": [{"listener_id": "listener-123"}]},
        "extra": {"listener": "listener-123", "query": "eagles"},
        "breadcrumbs": {"values": [
            {"message": "HTTP Request: GET https://x/?api_key=SECRET6",
             "data": {"url": "https://x/?key=SECRET7"}}]},
    }
    out = error_tracking.scrub(event)
    text = repr(out)
    for secret in ("SECRET1", "SECRET2", "SECRET3", "SECRET4", "SECRET5",
                   "SECRET6", "SECRET7", "listener-123", "198.51.100.4",
                   "hunter2", "fam_session"):
        assert secret not in text, secret
    assert out["request"]["headers"] == {"X-FAM-Client": "web/1.0"}
    assert "q=eagles" in out["request"]["query_string"]
    assert out["extra"]["query"] == "eagles"


def test_health_reports_it():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert '"error_tracking": error_tracking.report()' in source
    # Before the app object exists, so its request errors are instrumented.
    assert source.index("error_tracking.init()") < source.index("app = FastAPI(")


def _service(name: str) -> str:
    text = (ROOT / "render.yaml").read_text(encoding="utf-8")
    for block in re.split(r"\n  - type: web\n", text)[1:]:
        if re.search(rf"^    name: {re.escape(name)}$", block, re.M):
            return block
    raise AssertionError(name)


@pytest.mark.parametrize("service", ["fam", "fam-staging"])
def test_no_deploy_goes_out_before_ci_passes(service):
    block = _service(service)
    assert re.search(r"^    autoDeployTrigger: checksPass$", block, re.M)
    assert not re.search(r"^    autoDeploy:", block, re.M)


def test_only_production_is_given_a_dsn():
    assert re.search(r"- key: SENTRY_DSN\n\s+sync: false", _service("fam"))
    assert "SENTRY_DSN" not in _service("fam-staging")


def test_the_lint_gate_runs_in_ci_and_in_dev_sh():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    dev = (ROOT / "dev.sh").read_text(encoding="utf-8")
    assert "ruff check ." in ci and "ruff check ." in dev
    assert (ROOT / "ruff.toml").exists()
