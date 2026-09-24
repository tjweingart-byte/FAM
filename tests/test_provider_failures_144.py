"""§144: the Render failures of 24/09 - Finnhub, GDELT and a leaked key.

Each test here is one line of those logs made impossible:

* `finnhub.io/api/v1/search?q=Startup and venture capital industry news` ->
  422, logged with a traceback, with the token in the URL.
* `gdelt retrieval failed for '...': ` - a timeout, with no message at all.
* `stories: GDELT failed: all 9 GDELT requests failed` - dozens of requests
  from one shared address in the same minute.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import time

import httpx
import pytest

import gdelt
import live_sources
import log_redaction


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# Finnhub: a topic is not a ticker
# --------------------------------------------------------------------------
@pytest.mark.parametrize("subject", [
    "Startup and venture capital industry news",
    "Business and finance news of the last 24 hours (September 23-24, 2026)",
    "US stock market and economy news",
    "Startups",
    "what is happening with the markets today?",
])
def test_the_subjects_from_the_logs_are_topics(subject):
    assert not live_sources.looks_like_a_listing(subject)


@pytest.mark.parametrize("subject", [
    "Apple", "Nvidia", "NVDA", "Taiwan Semiconductor Manufacturing",
    "S&P 500", "Berkshire Hathaway", "Global Payments",
])
def test_names_are_still_looked_up(subject):
    assert live_sources.looks_like_a_listing(subject)


class _Brief:
    def __init__(self, subject):
        self.subject = subject
        self.query = subject


def test_a_topic_never_reaches_finnhub(monkeypatch):
    asked = []

    async def _json(url, headers, params, timeout):
        asked.append(params)
        return {"result": []}

    monkeypatch.setattr(live_sources, "_json", _json)
    got = run(live_sources.FinnhubSource().resolve(
        _Brief("Startup and venture capital industry news")))
    assert got is None and asked == []


def test_a_422_on_lookup_is_no_match_not_a_failure(monkeypatch):
    async def _json(url, headers, params, timeout):
        raise live_sources.ProviderHTTPError(422, "finnhub.io/api/v1/search")

    monkeypatch.setattr(live_sources, "_json", _json)
    assert run(live_sources.FinnhubSource().resolve(_Brief("Nvidia"))) is None


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_a_key_or_plan_problem_is_still_a_failure(monkeypatch, status):
    async def _json(url, headers, params, timeout):
        raise live_sources.ProviderHTTPError(status, "finnhub.io/api/v1/search")

    monkeypatch.setattr(live_sources, "_json", _json)
    with pytest.raises(live_sources.ProviderHTTPError):
        run(live_sources.FinnhubSource().resolve(_Brief("Nvidia")))


# --------------------------------------------------------------------------
# The key never reaches a log
# --------------------------------------------------------------------------
def _mock_client(monkeypatch, status, body="Unprocessable"):
    real = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(
            lambda request: httpx.Response(status, text=body))
        return real(*args, **kwargs)

    monkeypatch.setattr(live_sources.httpx, "AsyncClient", client)


def test_an_http_error_carries_no_url_and_no_token(monkeypatch):
    _mock_client(monkeypatch, 422)
    with pytest.raises(live_sources.ProviderHTTPError) as caught:
        run(live_sources._json("https://finnhub.io/api/v1/search", {},
                               {"q": "x", "token": "SECRET123"}, 1.0))
    text = str(caught.value)
    assert "SECRET123" not in text and "token" not in text
    assert "422" in text and "finnhub.io/api/v1/search" in text
    # Not chained: a chained HTTPStatusError would print the URL again.
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


def test_a_redirect_is_still_an_error(monkeypatch):
    """`raise_for_status` refused a 3xx; the helper that replaced it must
    too, or a redirect falls through to a confusing JSON error."""
    _mock_client(monkeypatch, 302, body="")
    with pytest.raises(live_sources.ProviderHTTPError, match="302"):
        run(live_sources._json("https://finnhub.io/api/v1/quote", {}, {}, 1.0))


def test_a_body_that_echoes_the_key_is_redacted(monkeypatch):
    _mock_client(monkeypatch, 400, body="bad request for token=SECRET123")
    with pytest.raises(live_sources.ProviderHTTPError) as caught:
        run(live_sources._json("https://finnhub.io/api/v1/quote", {},
                               {"token": "SECRET123"}, 1.0))
    assert "SECRET123" not in str(caught.value)


@pytest.mark.parametrize("url", [
    "https://finnhub.io/api/v1/search?q=x&token=SECRET123",
    "https://gnews.io/api/v4/top-headlines?apikey=SECRET123&max=10",
])
def test_the_httpx_request_line_is_redacted(url):
    record = logging.LogRecord("httpx", logging.INFO, __file__, 1,
                               'HTTP Request: GET %s "HTTP/1.1 422"', (url,), None)
    for f in logging.getLogger("httpx").filters:
        f.filter(record)
    assert "SECRET123" not in record.getMessage()
    assert "[redacted]" in record.getMessage()


def test_redaction_leaves_ordinary_parameters_alone():
    assert log_redaction.redact("q=nvidia&max=10") == "q=nvidia&max=10"


# --------------------------------------------------------------------------
# GDELT: one request at a time, and an episode never queues behind a sweep
# --------------------------------------------------------------------------
@pytest.fixture
def paced(monkeypatch):
    patched = dataclasses.replace(gdelt.settings, gdelt=True,
                                  gdelt_request_gap_seconds=0.2,
                                  gdelt_episode_wait_seconds=0.3)
    monkeypatch.setattr(gdelt, "settings", patched)
    gdelt.PACER.reset()
    yield patched
    gdelt.PACER.reset()


def test_background_requests_are_spaced(paced):
    async def go():
        starts = []

        async def one():
            await gdelt.PACER.slot()
            starts.append(time.monotonic())

        await asyncio.gather(*(one() for _ in range(4)))
        return sorted(starts)

    starts = run(go())
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert all(g >= 0.18 for g in gaps), gaps


def test_an_episode_is_not_queued_behind_a_whole_sweep(paced):
    """Ten background requests queued; an episode still gets a slot within
    about one gap, because background books one slot at a time."""
    async def go():
        sweep = [asyncio.ensure_future(gdelt.PACER.slot()) for _ in range(10)]
        await asyncio.sleep(0.01)
        began = time.monotonic()
        await gdelt.PACER.slot(max_wait=0.3)
        waited = time.monotonic() - began
        for task in sweep:
            task.cancel()
        await asyncio.gather(*sweep, return_exceptions=True)
        return waited

    assert run(go()) < 0.35


def test_an_episode_does_without_gdelt_rather_than_wait(paced):
    async def go():
        await gdelt.PACER.slot(max_wait=1.0)
        await gdelt.PACER.slot(max_wait=1.0)
        await gdelt.PACER.slot(max_wait=0.05)

    with pytest.raises(gdelt.GdeltBusy):
        run(go())


def test_a_busy_pacer_is_an_empty_retrieval_not_a_warning(paced, monkeypatch,
                                                          caplog):
    async def _get(params, timeout, max_wait=None):
        raise gdelt.GdeltBusy("next slot is 30s away")

    monkeypatch.setattr(gdelt, "_get", _get)
    with caplog.at_level(logging.INFO, logger="gdelt"):
        assert run(gdelt.retrieve("fed rates")) == []
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_a_timeout_is_named_in_the_log(paced, monkeypatch, caplog):
    """The 24/09 logs read `gdelt retrieval failed for '...': ` and stopped,
    because a timeout's message is empty."""
    async def _get(params, timeout, max_wait=None):
        raise httpx.ReadTimeout("")

    monkeypatch.setattr(gdelt, "_get", _get)
    with caplog.at_level(logging.WARNING, logger="gdelt"):
        run(gdelt.retrieve("fed rates"))
    assert "ReadTimeout" in caplog.text


def test_a_whole_prompt_is_reduced_to_searchable_words():
    prompt = ("The latest on Anthropic (Startups) as of Thursday, September 24, "
              "2026. Cover only Anthropic, not Startups in general: what happened")
    cleaned = gdelt.clean_query(prompt)
    assert "(" not in cleaned and "," not in cleaned and ":" not in cleaned
    assert len(cleaned.split()) <= gdelt.MAX_QUERY_WORDS
    assert cleaned.startswith("The latest on Anthropic Startups")


def test_theme_volumes_are_shared_between_callers(paced, monkeypatch):
    calls = []

    async def measure(theme, timeout):
        calls.append(theme)
        await asyncio.sleep(0.01)
        return 42.0

    monkeypatch.setattr(gdelt, "_measure_volume", measure)
    monkeypatch.setattr(gdelt, "_VOLUMES", {})

    async def go():
        first = await asyncio.gather(gdelt.volume_for("ECON", 1.0),
                                     gdelt.volume_for("ECON", 1.0))
        again = await gdelt.volume_for("ECON", 1.0)
        return first, again

    first, again = run(go())
    assert first == [42.0, 42.0] and again == 42.0
    assert calls == ["ECON"]


def test_a_failed_volume_is_asked_again(paced, monkeypatch):
    calls = []

    async def measure(theme, timeout):
        calls.append(theme)
        if len(calls) == 1:
            raise httpx.ReadTimeout("")
        return 7.0

    monkeypatch.setattr(gdelt, "_measure_volume", measure)
    monkeypatch.setattr(gdelt, "_VOLUMES", {})

    async def go():
        with pytest.raises(httpx.ReadTimeout):
            await gdelt.volume_for("SPORTS", 1.0)
        return await gdelt.volume_for("SPORTS", 1.0)

    assert run(go()) == 7.0 and len(calls) == 2


def test_the_paced_sweeps_have_room_to_finish():
    """A ceiling sized for unpaced requests is a sweep that always times out."""
    import config
    import story_sources

    gap = config.settings.gdelt_request_gap_seconds
    # Fifteen theme volumes, eight hot themes, nine regions.
    assert story_sources.GdeltSignals.timeout_seconds > 32 * gap
    assert story_sources.TrendingRegistrySignals.timeout_seconds > 15 * gap
    assert gdelt.GdeltTrendingSource.timeout_seconds > 15 * gap


# --------------------------------------------------------------------------
# Boot: three background jobs do not start in the same second
# --------------------------------------------------------------------------
def test_boot_staggers_the_editions():
    import pathlib

    source = pathlib.Path(__file__).resolve().parent.parent.joinpath(
        "app.py").read_text()
    assert "initial_delay=settings.boot_stagger_seconds" in source
    assert "initial_delay=2 * settings.boot_stagger_seconds" in source
