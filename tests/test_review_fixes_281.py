"""§281: the review's fixes to the reference look and the retired backend."""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import wikimedia  # noqa: E402
from test_reference_278 import _brief, _enterprise, fake, parallel  # noqa: E402,F401


def test_a_retired_vendor_in_the_dashboard_still_boots(monkeypatch):
    monkeypatch.setenv("RESEARCH_BACKEND", "exa")
    monkeypatch.setenv("RESEARCH_SECOND_LOOK", "exa")
    settings = config.Settings()
    assert settings.research_backend == config.DEFAULT_RESEARCH_BACKEND
    assert settings.research_backend_replaced == "exa"
    assert settings.research_second_look == "same"


def test_an_empty_packet_does_not_wait_for_the_reference(fake, parallel):
    import research

    fake.delay = 3.0
    parallel["results"] = []
    parallel["delay"] = 0.0

    async def timed():
        started = time.perf_counter()
        packet = await research.retrieve("jalen hurts", brief=_brief(),
                                         reference_name="Jalen Hurts")
        return packet, time.perf_counter() - started

    packet, seconds = asyncio.run(timed())
    assert packet.reference == "no web sources"
    assert seconds < 0.2, "the grace is never spent in front of an empty rung"


def test_the_look_runs_in_its_own_pool(fake, parallel):
    import research

    seen = []
    real = wikimedia.lookup

    def lookup(*args, **kwargs):
        import threading

        seen.append(threading.current_thread().name)
        return real(*args, **kwargs)

    wikimedia.lookup = lookup
    try:
        asyncio.run(research.retrieve("jalen hurts", brief=_brief(),
                                      reference_name="Jalen Hurts"))
    finally:
        wikimedia.lookup = real
    assert seen and seen[0].startswith("wikimedia")


def test_a_network_failure_holds_the_provider(fake):
    def down(*_args, **_kwargs):
        raise TimeoutError("read timed out")

    wikimedia._transport = down
    assert wikimedia.lookup("Jalen Hurts") is None
    assert wikimedia.report()["backing_off_seconds"].get("wikidata", 0) > 0


def test_a_backoff_spends_nothing_from_the_month(fake, monkeypatch):
    _enterprise(monkeypatch)
    host = wikimedia.ENTERPRISE_LOGIN.split("//", 1)[-1].split("/", 1)[0]
    import provider_usage

    with wikimedia._BACKOFF_LOCK:
        wikimedia._BACKOFF[provider_usage.provider_for_host(host)] = (
            time.monotonic() + 60)
    before = wikimedia.store().count(wikimedia._month())
    try:
        wikimedia._token()
    except wikimedia.WikimediaError:
        pass
    assert wikimedia.store().count(wikimedia._month()) == before


def test_a_refused_login_is_not_tried_again(fake, monkeypatch):
    _enterprise(monkeypatch)
    fake.status = {wikimedia.ENTERPRISE_LOGIN: 401}
    for _ in range(3):
        try:
            wikimedia._token()
        except wikimedia.WikimediaError:
            pass
    assert len(fake.asked(wikimedia.ENTERPRISE_LOGIN)) == 1
