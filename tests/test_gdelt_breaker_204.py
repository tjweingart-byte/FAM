"""§204: GDELT stops being asked when it refuses everything, and can be
sent through a proxy of FAM's own.

On 1/10 GDELT failed 384 of 384 requests from Render's shared outbound address
(§191). Each failure cost a paced slot, and an episode whose Exa search came
back empty waited up to six seconds for it. The breaker pauses GDELT after a
run of failures and says so on health; `GDELT_PROXY_URL` sends GDELT's
requests - only GDELT's - from a static address.
"""
from __future__ import annotations

import asyncio
import dataclasses

import httpx
import pytest

import gdelt
import spend_guard


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def wired(monkeypatch):
    """GDELT on, no pacing, and every request answered by `status`."""
    state = {"status": 429, "calls": 0, "proxy": "unset"}

    def handler(request):
        state["calls"] += 1
        if state["status"] == 200:
            return httpx.Response(200, json={"articles": []})
        return httpx.Response(state["status"], text="refused")

    real = httpx.AsyncClient

    def client(*args, **kwargs):
        state["proxy"] = kwargs.pop("proxy", None)
        return real(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(gdelt.httpx, "AsyncClient", client)
    monkeypatch.setattr(gdelt, "settings", dataclasses.replace(
        gdelt.settings, gdelt=True, gdelt_request_gap_seconds=0.0,
        gdelt_breaker_failures=3, gdelt_breaker_seconds=600.0,
        gdelt_proxy_url=""))
    return state


def test_a_run_of_refusals_pauses_gdelt(wired):
    for _ in range(3):
        assert run(gdelt.retrieve("fed rates")) == []
    assert wired["calls"] == 3
    # Paused: the next episode does not spend a request or a wait on it.
    assert run(gdelt.retrieve("fed rates")) == []
    assert wired["calls"] == 3
    report = gdelt.report()
    assert report["failures_in_a_row"] == 3
    assert report["paused_until"] is not None
    assert "429" in report["last_error"]


def test_background_work_is_refused_too(wired):
    for _ in range(3):
        run(gdelt.retrieve("fed rates"))
    with pytest.raises(gdelt.GdeltPaused):
        run(gdelt.artlist("theme:SPORTS", 5, 12, 1.0))


def test_a_success_closes_it(wired):
    run(gdelt.retrieve("fed rates"))
    run(gdelt.retrieve("fed rates"))
    wired["status"] = 200
    run(gdelt.retrieve("fed rates"))
    assert gdelt.report()["failures_in_a_row"] == 0
    assert gdelt.report()["paused_until"] is None


def test_the_pause_ends_with_one_request_let_through(wired):
    for _ in range(3):
        run(gdelt.retrieve("fed rates"))
    gdelt.BREAKER.paused_until = 1.0  # the pause is over
    wired["status"] = 200
    run(gdelt.retrieve("fed rates"))
    assert wired["calls"] == 4
    assert gdelt.report()["paused_until"] is None


def test_zero_never_pauses(wired, monkeypatch):
    monkeypatch.setattr(gdelt, "settings", dataclasses.replace(
        gdelt.settings, gdelt_breaker_failures=0))
    for _ in range(6):
        run(gdelt.retrieve("fed rates"))
    assert wired["calls"] == 6


def test_requests_go_through_the_proxy_when_one_is_set(wired, monkeypatch):
    run(gdelt.retrieve("fed rates"))
    assert wired["proxy"] is None
    assert gdelt.report()["via_proxy"] is False
    monkeypatch.setattr(gdelt, "settings", dataclasses.replace(
        gdelt.settings, gdelt_proxy_url="http://user:pass@proxy.example:9293"))
    run(gdelt.retrieve("fed rates"))
    assert wired["proxy"] == "http://user:pass@proxy.example:9293"
    report = gdelt.report()
    assert report["via_proxy"] is True
    # The URL carries a credential: health says whether, never what.
    assert "pass" not in repr(report)


def test_staging_never_holds_the_proxy():
    assert "GDELT_PROXY_URL" in spend_guard.PAID_CREDENTIALS
