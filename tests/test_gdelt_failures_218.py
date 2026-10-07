"""§218: why a third of GDELT's downloads failed, and what stops it.

The admin page counted 314 GDELT requests in a day, 101 failed, where a
healthy day is about 200 with none. Every download goes through `sync`, and
it had three ways to turn GDELT's ordinary behaviour into failures:

* the poll slept a fixed period *after* each sync, so it drifted by the
  sync's own length and walked through the minutes in which
  `lastupdate.txt` names a file the server does not serve yet - a 404, a
  failed sync, the file asked again as a backfill (and written off for good
  on a second 404);
* one failed download stopped the sync, so the files after it waited a
  whole period;
* a timeout, a dropped connection or a passing 5xx was never asked again.

What these pin: polls land on GDELT's clock; a named file is waited for,
never written off; a passing failure is asked once more; one bad file does
not stop the rest; and every failure is counted with its reason.
"""
from __future__ import annotations

import asyncio
import dataclasses
import pathlib
from datetime import datetime, timedelta, timezone

import httpx
import pytest

import config
import gdelt
import provider_usage
from tests.test_gdelt_exports_211 import upstream_with_fed

ROOT = pathlib.Path(__file__).resolve().parent.parent


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def on(monkeypatch):
    patched = dataclasses.replace(config.settings, gdelt=True,
                                  gdelt_export_backfill_files=2,
                                  gdelt_export_keep_hours=24.0,
                                  gdelt_export_retry_seconds=0.0,
                                  gdelt_export_poll_seconds=900.0,
                                  gdelt_export_poll_offset_seconds=450.0,
                                  gdelt_export_wait_seconds=120.0)
    monkeypatch.setattr(gdelt, "settings", patched)
    return patched


SKIPPED = "GKG file: HTTP 404, never published"


def reasons(skipped: bool = False) -> dict:
    """GDELT's failure reasons; the upstream fixture's one deliberately
    skipped file left out unless asked for."""
    return {r["why"]: r["count"] for r in provider_usage.failure_reasons("gdelt")
            if skipped or r["why"] != SKIPPED}


# --------------------------------------------------------------------------
# A named file is waited for
# --------------------------------------------------------------------------
def test_a_named_file_not_served_yet_is_waited_for_not_written_off(on):
    """The old sync wrote a named file off on its second 404 - once it was
    behind the newest - and its fifteen minutes of news were never read."""
    up = upstream_with_fed()
    late = gdelt.name_for(up.newest)
    blob = up.files.pop(late)

    async def go():
        async with up.client() as client:
            first = await gdelt.sync(client=client)
            # GDELT names the next file; the late one still is not served.
            up.newest += timedelta(minutes=15)
            up.files[gdelt.name_for(up.newest)] = blob
            second = await gdelt.sync(client=client)
            up.files[late] = blob
            third = await gdelt.sync(client=client)
            return first, second, third

    first, second, third = run(go())
    assert first["waiting"] == [late] and "error" not in first
    assert second["waiting"] == [late], second
    assert third.get("fetched") == 1 and "waiting" not in third
    held = gdelt.store()
    assert held.has_file(late)
    assert gdelt.STATE.failures_in_a_row == 0
    assert reasons() == {"GKG file: HTTP 404, named but not served yet": 2}


def test_a_file_never_named_is_written_off_on_one_404(on):
    up = upstream_with_fed()
    never = gdelt.name_for(up.newest - timedelta(minutes=30))

    async def go():
        async with up.client() as client:
            return await gdelt.sync(client=client)

    result = run(go())
    assert "error" not in result and "waiting" not in result
    assert reasons(skipped=True) == {SKIPPED: 1}
    up.asked.clear()

    async def again():
        async with up.client() as client:
            await gdelt.sync(client=client)

    run(again())
    assert not any(never in u for u in up.asked)


def test_a_named_file_missing_for_an_hour_is_written_off(on):
    up = upstream_with_fed()
    late = gdelt.name_for(up.newest)
    up.files.pop(late)
    start = up.newest.timestamp() + 60

    async def go():
        async with up.client() as client:
            await gdelt.sync(client=client, now=start)
            return await gdelt.sync(
                client=client, now=start + gdelt.NAMED_GRACE_SECONDS + 1)

    result = run(go())
    assert "waiting" not in result
    assert gdelt.store().has_file(late)  # noted as never published
    assert gdelt.store().newest() < up.newest.timestamp()


# --------------------------------------------------------------------------
# A passing failure is asked once more; one failure stops nothing else
# --------------------------------------------------------------------------
def _flaky(up, name, statuses):
    """GDELT's server answering `name` with each of `statuses` in turn."""
    real = up.handler
    left = list(statuses)

    def handler(request):
        if request.url.path.endswith(name) and left:
            up.asked.append(str(request.url))
            status = left.pop(0)
            if status == "drop":
                raise httpx.ReadTimeout("timed out", request=request)
            return httpx.Response(status)
        return real(request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_a_passing_5xx_is_asked_once_more(on):
    up = upstream_with_fed()
    newest = gdelt.name_for(up.newest)

    async def go():
        async with _flaky(up, newest, [503]) as client:
            return await gdelt.sync(client=client)

    result = run(go())
    assert "error" not in result and gdelt.store().has_file(newest)
    assert reasons() == {"GKG file: HTTP 503": 1}


def test_a_timeout_is_asked_once_more(on):
    up = upstream_with_fed()

    async def go():
        async with _flaky(up, "lastupdate.txt", ["drop"]) as client:
            return await gdelt.sync(client=client)

    result = run(go())
    assert "error" not in result and result["fetched"] == 2
    assert reasons() == {"lastupdate.txt: ReadTimeout": 1}


def test_a_second_failure_is_real_and_counted_twice(on):
    up = upstream_with_fed()
    newest = gdelt.name_for(up.newest)

    async def go():
        async with _flaky(up, newest, [502, 502]) as client:
            return await gdelt.sync(client=client)

    result = run(go())
    assert "502" in result["error"]
    assert reasons() == {"GKG file: HTTP 502": 2}
    assert gdelt.STATE.failures_in_a_row == 1


def test_a_404_on_lastupdate_is_not_retried(on):
    up = upstream_with_fed()

    async def go():
        async with _flaky(up, "lastupdate.txt", [404, 404]) as client:
            return await gdelt.sync(client=client)

    result = run(go())
    assert "error" in result
    assert reasons() == {"lastupdate.txt: HTTP 404": 1}


def test_one_bad_file_does_not_stop_the_newest(on):
    """The old loop raised on the first failure, oldest first, so a backfill
    file that would not download kept the newest file out for a period."""
    up = upstream_with_fed()
    earlier = gdelt.name_for(up.newest - timedelta(minutes=15))

    async def go():
        async with _flaky(up, earlier, [500, 500]) as client:
            return await gdelt.sync(client=client)

    result = run(go())
    assert "error" in result and earlier in result["error"]
    assert gdelt.store().has_file(gdelt.name_for(up.newest))
    assert result["fetched"] == 1


# --------------------------------------------------------------------------
# Polls on GDELT's clock
# --------------------------------------------------------------------------
def _at(minute: int, second: int = 0) -> float:
    return datetime(2026, 10, 7, 14, minute, second,
                    tzinfo=timezone.utc).timestamp()


def test_polls_land_at_the_same_place_in_each_quarter_hour(on):
    # The offset is 7.5 minutes: 14:07:30, 14:22:30, ...
    assert gdelt.next_poll_in(_at(0)) == 450
    # A sync that started at 14:07:30 and took 40s does not push the next.
    assert gdelt.next_poll_in(_at(8, 10)) == 900 - 40
    assert gdelt.next_poll_in(_at(20)) == 150


def test_a_poll_never_comes_within_a_minute_of_the_last(on):
    assert gdelt.next_poll_in(_at(7)) == 30 + 900


def test_a_waiting_file_is_looked_for_again_soon(on):
    assert gdelt.next_poll_in(_at(8, 10), waiting=True) == 120
    # ...unless the next poll is sooner anyway.
    assert gdelt.next_poll_in(_at(21), waiting=True) == 90


def test_the_loop_reads_the_clock_rather_than_sleeping_a_period():
    source = (ROOT / "gdelt.py").read_text()
    loop = source[source.index("async def run_forever"):]
    loop = loop[:loop.index("\n\n\n")]
    assert "next_poll_in" in loop
    assert "gdelt_export_poll_seconds" not in loop


# --------------------------------------------------------------------------
# Every failure says why
# --------------------------------------------------------------------------
def test_the_admin_row_says_why_gdelt_failed(on):
    up = upstream_with_fed()
    newest = gdelt.name_for(up.newest)

    async def go():
        async with _flaky(up, newest, [503]) as client:
            await gdelt.sync(client=client)

    run(go())
    row = {r["provider"]: r for r in provider_usage.report()}["gdelt"]
    assert row["failed_today"] == 2  # the 503 and the never-published file
    assert {r["why"] for r in row["failure_reasons"]} == {
        "GKG file: HTTP 503", "GKG file: HTTP 404, never published"}
    assert gdelt.report()["failure_reasons"] == row["failure_reasons"]


def test_a_reason_never_carries_a_url():
    provider_usage.record("gdelt", ok=False, why="GKG file: HTTP 500")
    for row in provider_usage.failure_reasons("gdelt"):
        assert "http" not in row["why"].lower().replace("http 5", "")


def test_reasons_are_bounded():
    for n in range(provider_usage.WHY_KINDS + 5):
        provider_usage.record("gdelt", ok=False, why=f"kind {n}")
    kinds = {r["why"] for r in provider_usage.failure_reasons("gdelt")}
    assert len(kinds) == provider_usage.WHY_KINDS + 1 and "other" in kinds


def test_the_tracker_draws_the_reasons():
    page = (ROOT / "admin_ui" / "tracker.html").read_text()
    assert "failure_reasons" in page


def test_finnhub_and_local_feeds_say_why_too():
    """The same admin page shows Finnhub and the local feeds failing; their
    reasons come from the two functions every request of theirs goes through,
    and Finnhub's names the path, never the query that carries its key."""
    live = (ROOT / "live_sources.py").read_text()
    body = live[live.index("async def _json"):]
    body = body[:body.index("\n\n\n")]
    assert body.count("why=") == 2 and "response.url.path" in body
    assert "response.url}" not in body and "str(response.url)" not in body
    local = (ROOT / "local_news.py").read_text()
    body = local[local.index("async def _get"):]
    body = body[:body.index("\n\n\n")]
    assert body.count("why=") == 2
