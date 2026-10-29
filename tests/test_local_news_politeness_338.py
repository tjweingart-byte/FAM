"""Local news stops knocking when a site says no (§338)."""
from __future__ import annotations

import asyncio
import os
import sys
import time

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import local_news  # noqa: E402


def outlet_at(homepage: str, **extra) -> local_news.Outlet:
    store = local_news.store()
    store.add_outlet(name="Test Outlet", homepage=homepage, town="San Anselmo",
                     county="Marin", region="California", country="US", **extra)
    return store.outlets("homepage = ?", (homepage,))[0]


def poll_counting(handler, outlet):
    asked = []

    def counted(request):
        asked.append(request.url.path)
        return handler(request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(counted),
                                     follow_redirects=True) as client:
            return await local_news.poll(outlet, client)
    return asyncio.run(run()), asked


def test_a_429_on_the_homepage_stops_the_guessing():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(429)
    outlet, asked = poll_counting(handler, outlet_at("https://busy-429.example/"))
    assert outlet.state == local_news.BLOCKED
    assert "429" in outlet.detail
    assert asked == ["/robots.txt", "/"]          # not the seven guesses after it
    assert outlet.next_poll - time.time() >= 86000


def test_a_refusal_partway_through_the_guesses_stops_them():
    def handler(request):
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(404)
        if path == "/":
            return httpx.Response(200, text="<html>no feed links</html>")
        if path == "/feed/":
            return httpx.Response(404)
        return httpx.Response(429)
    outlet, asked = poll_counting(handler, outlet_at("https://half-429.example/"))
    assert outlet.state == local_news.BLOCKED
    assert asked == ["/robots.txt", "/", "/feed/", "/rss"]


def test_robots_txt_is_asked_before_the_homepage_and_the_guesses():
    def handler(request):
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        return httpx.Response(200, text="<html></html>")
    outlet, asked = poll_counting(handler, outlet_at("https://private.example/"))
    assert outlet.state == local_news.DISALLOWED
    assert asked == ["/robots.txt"]


def test_a_guess_robots_forbids_is_skipped_not_asked():
    def handler(request):
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /rss\n")
        return httpx.Response(404 if path != "/" else 200, text="<html></html>")
    outlet, asked = poll_counting(handler, outlet_at("https://partly.example/"))
    assert outlet.state == local_news.NO_FEED
    assert "/rss" not in asked and "/rss.xml" not in asked
    assert "/feed/" in asked


def test_a_site_with_no_feed_is_asked_again_in_days_not_hours():
    o = local_news.Outlet.__new__(local_news.Outlet)
    o.state, o.failures, o.typical_gap = local_news.NO_FEED, 1, 0.0
    now = time.time()
    assert local_news.next_poll(o, now) - now == 86400.0
    o.failures = 9
    assert local_news.next_poll(o, now) - now == 7 * 86400.0


def test_a_remote_voice_is_not_warmed_at_boot(monkeypatch):
    """§338: the boot-time "Ready." went to the serverless endpoint and
    cold-started a GPU nobody was waiting for."""
    import tts

    spoken = []

    class Remote:
        name = "remote"

        async def synth(self, text, wpm):
            spoken.append(text)

    monkeypatch.setattr(tts, "build_engine", lambda: Remote())
    asyncio.run(tts.warm_up())
    assert spoken == []
