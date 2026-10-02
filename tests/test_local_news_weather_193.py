"""Local news from a town's own outlets, and weather for the place asked (§193).

At the owner's direction: a local question goes town, then county, then Exa
on the town's known outlets - never GDELT - and when the town has nothing the
episode opens with a sentence composed in code saying so, then the weather
there, then the nearest news. Weather is the National Weather Service first
for a US place, Open-Meteo everywhere else and when NWS fails.

None of the hosts involved is reachable from the build container, so every
provider here is a recorded reply or a stand-in.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
import time
import types
from datetime import datetime, timedelta, timezone

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cache  # noqa: E402
import episode_intelligence as ei  # noqa: E402
import live_facts  # noqa: E402
import local_news  # noqa: E402
import places  # noqa: E402
import research  # noqa: E402
import script_generator as sg  # noqa: E402
import spend_guard  # noqa: E402
import weather  # noqa: E402
from script_generator import ScriptNotes, plan_episode  # noqa: E402

SAN_ANSELMO = places.Place("San Anselmo", "Marin", "California", "US",
                           37.9746, -122.5616, "test")
NOW = time.time()


def rss(*items: tuple) -> bytes:
    body = "".join(
        f"<item><title>{t}</title><link>{u}</link>"
        f"<description>{d}</description>"
        f"<pubDate>{datetime.fromtimestamp(p, timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>"
        f"</item>" for t, u, d, p in items)
    return (f'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>'
            f"{body}</channel></rss>").encode()


def filed(*outlets) -> local_news.LocalNewsStore:
    store = local_news.store()
    for o in outlets:
        store.add_outlet(**o)
    return store


def keep(store, homepage: str, *items: local_news.Item) -> None:
    outlet = store.outlets("homepage = ?", (homepage,))[0]
    store.add_items(outlet.id, list(items))


# --------------------------------------------------------------------------
# Reading feeds
# --------------------------------------------------------------------------
def test_rss_and_atom_are_read_with_dates_and_full_text():
    feed = (b'<?xml version="1.0"?><rss version="2.0" '
            b'xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>'
            b"<item><title>Council approves budget</title>"
            b"<link>https://ross.example/a</link>"
            b"<description>Short teaser.</description>"
            b"<content:encoded><![CDATA[<p>" + b"The San Anselmo town council " * 30
            + b"</p>]]></content:encoded>"
            b"<pubDate>Wed, 01 Oct 2026 18:00:00 +0000</pubDate></item>"
            b"</channel></rss>")
    items = local_news.parse_feed(feed)
    assert len(items) == 1
    assert items[0].title == "Council approves budget"
    assert local_news.words(items[0].body) > 100
    assert items[0].published == datetime(2026, 10, 1, 18, tzinfo=timezone.utc).timestamp()

    atom = (b'<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
            b"<title>Road closure</title>"
            b'<link rel="alternate" href="https://x.example/b"/>'
            b"<summary>Sir Francis Drake closed</summary>"
            b"<updated>2026-10-01T10:00:00Z</updated></entry></feed>")
    entry = local_news.parse_feed(atom)[0]
    assert entry.url == "https://x.example/b" and entry.summary


def test_a_feed_that_declares_entities_is_refused():
    """The expansion attacks need declared entities; none is ever parsed."""
    bomb = (b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]>'
            b"<rss><channel><item><title>&a;</title></item></channel></rss>")
    with pytest.raises(ValueError):
        local_news.parse_feed(bomb)
    with pytest.raises(ValueError):
        local_news.parse_feed(b"<html><body>not a feed</body></html>")


def test_a_place_is_named_only_as_a_whole_phrase():
    assert local_news.names("Flooding in San Anselmo today", "San Anselmo")
    assert local_news.names("SAN  ANSELMO council", "san anselmo")
    assert not local_news.names("San Anselmo-Fairfax border", "Fairfax-x")
    assert not local_news.names("Sausalito news", "Sausal")


def test_feed_links_are_found_on_a_homepage_main_feed_first():
    html = ('<link rel="alternate" type="application/rss+xml" '
            'href="/comments/feed/"><link rel="alternate" '
            'type="application/rss+xml" href="/feed/">')
    assert local_news.feed_links(html, "https://ross.example/") == [
        "https://ross.example/feed/", "https://ross.example/comments/feed/"]


# --------------------------------------------------------------------------
# Polling, and a feed's health
# --------------------------------------------------------------------------
def poll_with(handler, outlet: local_news.Outlet) -> local_news.Outlet:
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                     follow_redirects=True) as client:
            return await local_news.poll(outlet, client)
    return asyncio.run(run())


def an_outlet(**extra) -> local_news.Outlet:
    store = filed({"name": "Ross Valley Reporter",
                   "homepage": "https://ross.example/", "town": "San Anselmo",
                   "county": "Marin", "region": "California", "country": "US",
                   **extra})
    return store.outlets("homepage = ?", ("https://ross.example/",))[0]


def test_a_healthy_feed_is_found_read_and_scheduled():
    feed = rss(("Council meets", "https://ross.example/1", "x " * 80, NOW - 3600),
               ("Library hours", "https://ross.example/2", "y " * 80, NOW - 90000))

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/":
            return httpx.Response(200, text='<link rel="alternate" '
                                  'type="application/rss+xml" href="/feed/">')
        if request.url.path == "/feed/":
            return httpx.Response(200, content=feed, headers={"etag": '"v1"'})
        return httpx.Response(404)

    outlet = poll_with(handler, an_outlet())
    assert outlet.state == local_news.HEALTHY, outlet.detail
    assert outlet.feed_url == "https://ross.example/feed/"
    assert outlet.etag == '"v1"' and outlet.full_text is True
    assert outlet.next_poll > time.time()
    assert len(local_news.store().items(0)) == 2


def test_a_refusing_site_is_blocked_and_a_failing_one_broken():
    def forbidden(request):
        return httpx.Response(404 if request.url.path == "/robots.txt" else 403)
    outlet = poll_with(forbidden, an_outlet(feed_url="https://ross.example/feed/"))
    assert outlet.state == local_news.BLOCKED
    assert outlet.next_poll - time.time() >= 86000

    def failing(request):
        return httpx.Response(404 if request.url.path == "/robots.txt" else 500)
    outlet.state = local_news.HEALTHY
    outlet = poll_with(failing, outlet)
    assert outlet.state == local_news.BROKEN and outlet.failures == 2
    assert "500" in outlet.detail


def test_robots_txt_is_obeyed():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /")
        raise AssertionError(f"read {request.url} against robots.txt")
    outlet = poll_with(handler, an_outlet(feed_url="https://ross.example/feed/"))
    assert outlet.state == local_news.DISALLOWED


def test_a_feed_broken_for_a_month_is_retired():
    outlet = an_outlet(feed_url="https://ross.example/feed/")
    outlet.state = local_news.BROKEN
    outlet.trouble_since = time.time() - local_news.RETIRE_AFTER - 10

    def failing(request):
        return httpx.Response(404 if request.url.path == "/robots.txt" else 500)
    assert poll_with(failing, outlet).state == local_news.RETIRED


def test_an_excluded_publisher_is_never_filed_again():
    store = local_news.store()
    store.add_outlet("Ross", "https://ross.example/", town="San Anselmo")
    store.exclude("ross.example", "the publisher asked")
    ross = store.outlets("homepage = ?", ("https://ross.example/",))[0]
    assert ross.state == local_news.EXCLUDED
    assert store.add_outlet("Ross", "https://www.ross.example/news") is None


def test_quiet_outlets_are_polled_less_often_than_busy_ones():
    now = time.time()
    busy = local_news.Outlet(1, "a", "https://a/", state=local_news.HEALTHY,
                             typical_gap=600)
    weekly = local_news.Outlet(2, "b", "https://b/", state=local_news.HEALTHY,
                               typical_gap=7 * 86400)
    assert local_news.next_poll(busy, now) < local_news.next_poll(weekly, now)
    assert local_news.next_poll(weekly, now) - now <= 6 * 3600


# --------------------------------------------------------------------------
# Matching a town, then its county
# --------------------------------------------------------------------------
def marin_store():
    store = filed(
        {"name": "Ross Valley Reporter", "homepage": "https://ross.example/",
         "town": "San Anselmo", "county": "Marin", "region": "California"},
        {"name": "Marin IJ", "homepage": "https://ij.example/",
         "town": "San Rafael", "county": "Marin", "region": "California",
         "scope": "county"},
        {"name": "Regional site", "homepage": "https://bay.example/",
         "town": "San Anselmo", "county": "Marin", "region": "California",
         "scope": "mention"})
    return store


def item(url, title, text="", age_days=1.0):
    return local_news.Item(url=url, title=title, summary=text,
                           published=NOW - age_days * 86400)


def test_town_items_are_the_towns_own_or_name_it():
    store = marin_store()
    keep(store, "https://ross.example/", item("https://ross.example/1", "Library reopens"))
    keep(store, "https://ij.example/",
         item("https://ij.example/1", "San Anselmo flood wall approved"),
         item("https://ij.example/2", "Novato school board"))
    keep(store, "https://bay.example/", item("https://bay.example/1", "Bay traffic"))
    found = {i.url for i in local_news.town_items(SAN_ANSELMO)}
    # The regional site is a MENTION outlet: its stories count only when they
    # name the town.
    assert found == {"https://ross.example/1", "https://ij.example/1"}


def test_county_items_leave_out_the_town_and_old_stories():
    store = marin_store()
    keep(store, "https://ij.example/",
         item("https://ij.example/2", "Novato school board"),
         item("https://ij.example/3", "Old story", age_days=60))
    keep(store, "https://bay.example/",
         item("https://bay.example/2", "Marin County fire season ends"),
         item("https://bay.example/3", "Oakland news"))
    found = {i.url for i in local_news.county_items(SAN_ANSELMO)}
    assert found == {"https://ij.example/2", "https://bay.example/2"}


def test_the_packet_never_carries_a_hostname():
    packet = local_news.build_packet(
        [item("https://ij.example/2", "Novato school board", "The board met.")],
        local_news.COUNTY, SAN_ANSELMO)
    assert "ij.example" not in packet
    assert "Marin County" in packet and "Published:" in packet


# --------------------------------------------------------------------------
# The ladder
# --------------------------------------------------------------------------
def ladder(exa=None):
    return asyncio.run(local_news.research_local(
        SAN_ANSELMO, types.SimpleNamespace(retrieval="San Anselmo news"),
        exa=exa))


def test_the_town_answers_first_and_nothing_else_is_asked():
    store = marin_store()
    keep(store, "https://ross.example/", item("https://ross.example/1", "Library reopens"))

    async def exa(*_):
        raise AssertionError("Exa was asked after the town answered")
    result = ladder(exa)
    assert result.scope == "town" and not result.gap
    assert "Library reopens" in result.packet.context


def test_an_empty_town_falls_to_its_county_and_says_so():
    store = marin_store()
    keep(store, "https://ij.example/", item("https://ij.example/2", "Novato school board"))

    async def exa(*_):
        raise AssertionError("Exa was asked after the county answered")
    result = ladder(exa)
    assert result.scope == "county" and result.gap
    assert "Novato" in result.packet.context


def test_exa_is_asked_only_on_known_outlets_and_must_name_the_town():
    marin_store()
    asked = {}

    async def exa(query, domains, must_name):
        asked.update(query=query, domains=domains, must_name=must_name)
        return research.Packet(context="SOURCE 1\nTitle: San Anselmo news",
                               backend="exa", sources=["ross.example"])
    result = ladder(exa)
    # The shipped seed files the Marin IJ for the county, so it is asked too.
    assert set(asked["domains"]) == {"ross.example", "ij.example",
                                     "bay.example", "marinij.com"}
    assert asked["must_name"] == "San Anselmo"
    # Every result names the town, so it *is* town news: no gap to announce.
    assert result.scope == "exa" and not result.gap


def test_nothing_anywhere_is_a_gap_with_no_packet():
    marin_store()

    async def exa(*_):
        return research.Packet(backend="exa")
    result = ladder(exa)
    assert result.gap and result.packet is None and result.scope == ""


def test_exa_results_that_do_not_name_the_place_are_dropped():
    results = [types.SimpleNamespace(title="Marin County fire", highlights=[]),
               types.SimpleNamespace(title="x", highlights=["In San Anselmo, ..."])]
    kept = research.names_place(results, "San Anselmo")
    assert kept == [results[1]]


# --------------------------------------------------------------------------
# The gap sentence: composed in code, about our search, never the world
# --------------------------------------------------------------------------
def test_the_gap_sentence_promises_only_what_follows():
    full = local_news.gap_line(SAN_ANSELMO, weather=True, county_news=True)
    assert full == ("We couldn't find any recent news reports out of San "
                    "Anselmo. Here's the weather there, and the closest news "
                    "we have, from across Marin County.")
    assert "news" not in local_news.gap_line(
        SAN_ANSELMO, weather=True, county_news=False).split(".", 1)[1]
    assert "weather" not in local_news.gap_line(
        SAN_ANSELMO, weather=False, county_news=True)
    for line in (full, local_news.gap_line(SAN_ANSELMO, True, False)):
        assert "nothing" not in line.lower() and "no news" not in line.lower()


def gap_plan(weather_facts=True, scope="county", evidence="SOURCE 1\nTitle: x"):
    local = local_news.LocalResult(place=SAN_ANSELMO, scope=scope, gap=True)
    if weather_facts:
        local.weather = om_facts()
    return dataclasses.replace(plan_episode("San Anselmo news", 2, search=True),
                               local=local, evidence=evidence)


def test_the_gap_sentence_is_spoken_first_and_the_writer_is_told():
    plan = gap_plan()
    reader = sg._ScriptReader(plan, None)
    opening = reader.opening()
    assert opening == [local_news.gap_line(SAN_ANSELMO, True, True)]
    prompt = sg.build_prompt(plan)
    assert "ALREADY HEARD" in prompt and opening[0] in prompt
    assert "never present any of it as\nnews from San Anselmo" in prompt
    # An ordinary episode opens with nothing of ours.
    assert sg._ScriptReader(plan_episode("x", 2), None).opening() == []


def test_weather_alone_is_a_short_honest_episode():
    plan = gap_plan(scope="", evidence="")
    prompt = sg.build_prompt(plan)
    assert "and then stop" in prompt
    assert sg._ScriptReader(plan, None).opening() == [
        local_news.gap_line(SAN_ANSELMO, weather=True, county_news=False)]


def test_nothing_at_all_is_refused_in_the_same_transparent_words():
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    plan = gap_plan(weather_facts=False, scope="", evidence="")
    with pytest.raises(research.NoEvidence) as refused:
        generator._refuse_without_evidence(plan)
    assert "couldn't find any recent news reports out of San Anselmo" in str(refused.value)
    # With the weather there is an episode, and it is not refused.
    plan = gap_plan(scope="", evidence="")
    plan = dataclasses.replace(plan, live=live_facts.LiveLookup(
        "weather", live_facts.FACTS, facts=plan.local.weather))
    generator._refuse_without_evidence(plan)


# --------------------------------------------------------------------------
# Routing in the writer's research step
# --------------------------------------------------------------------------
def test_a_local_question_takes_the_local_ladder_and_never_gdelt(monkeypatch):
    async def never(*a, **k):
        raise AssertionError("the national ladder ran on a local question")
    monkeypatch.setattr(research, "retrieve", never)

    async def resolve(text):
        return SAN_ANSELMO
    monkeypatch.setattr(places, "resolve", resolve)

    async def forecast(place):
        return om_facts(place), [("Open-Meteo", "facts", "")]
    monkeypatch.setattr(weather, "forecast_for", forecast)

    store = marin_store()
    keep(store, "https://ij.example/", item("https://ij.example/2", "Novato school board"))
    brief = types.SimpleNamespace(
        retrieval="San Anselmo news", broader="", subject="San Anselmo",
        recency_days=7, outcome_dependent=False, must_establish=[],
        degraded=False, live_domain="", place="San Anselmo, California, US")
    plan = dataclasses.replace(plan_episode("whats new in san anselmo", 2,
                                            search=True), brief=brief)
    notes = ScriptNotes()
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    done = asyncio.run(generator.research(plan, notes))
    assert done.local.scope == "county" and done.local.gap
    assert done.local.weather is not None
    assert "Novato" in done.evidence
    assert notes.research["local"]["scope"] == "county"


def test_a_weather_question_skips_the_article_index(monkeypatch):
    async def never(*a, **k):
        raise AssertionError("an index was searched for a forecast")
    monkeypatch.setattr(research, "retrieve", never)
    brief = types.SimpleNamespace(retrieval="weather", live_domain="weather",
                                  place="San Anselmo, California, US")
    plan = dataclasses.replace(plan_episode("weather in san anselmo", 2,
                                            search=True), brief=brief)
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    assert asyncio.run(generator.research(plan, ScriptNotes())).evidence == ""


def test_weather_is_never_a_result_to_wait_for():
    brief = ei.gate(ei.Brief(query="weather in san anselmo", intent="update",
                             search_query="San Anselmo weather forecast",
                             live_domain="weather",
                             place="San Anselmo, California, US"),
                    "weather in san anselmo")
    # `update` makes it outcome-dependent; the live domain alone does not.
    brief2 = ei.gate(ei.Brief(query="forecast", intent="explainer",
                              search_query="forecast", live_domain="weather"),
                     "forecast")
    assert brief2.outcome_dependent is False
    assert "place" in ei.BRIEF_SCHEMA["required"]
    assert "weather" in ei.BRIEF_SCHEMA["properties"]["live_domain"]["enum"]
    assert brief.place == "San Anselmo, California, US"


def test_an_episode_built_on_weather_is_current_for_an_hour():
    assert cache.ttl_for("anything", live_domain="weather") <= 3600
    assert cache.ttl_for("anything") > 3600


# --------------------------------------------------------------------------
# Weather
# --------------------------------------------------------------------------
NWS_FORECAST = {"properties": {
    "updateTime": "2026-10-01T15:00:00+00:00",
    "periods": [
        {"name": "This Morning", "isDaytime": True, "temperature": 60,
         "temperatureUnit": "F", "windSpeed": "5 mph",
         "startTime": "2026-10-01T06:00:00-07:00",
         "endTime": "2026-10-01T08:00:00-07:00",
         "shortForecast": "Patchy Fog",
         "probabilityOfPrecipitation": {"value": None}},
        {"name": "Tonight", "isDaytime": False, "temperature": 52,
         "temperatureUnit": "F", "windSpeed": "5 mph",
         "startTime": "2026-10-01T18:00:00-07:00",
         "endTime": "2026-10-02T06:00:00-07:00",
         "shortForecast": "Patchy Fog",
         "probabilityOfPrecipitation": {"value": None}},
        {"name": "Thursday", "isDaytime": True, "temperature": 74,
         "temperatureUnit": "F", "windSpeed": "5 to 10 mph",
         "startTime": "2026-10-02T06:00:00-07:00",
         "endTime": "2026-10-02T18:00:00-07:00",
         "shortForecast": "Sunny",
         "probabilityOfPrecipitation": {"value": 10}},
    ]}}
NWS_ALERTS = {"features": [
    {"properties": {"event": "Wind Advisory", "ends": "2026-10-02T01:00:00+00:00"}},
    {"properties": {"event": "Heat Advisory", "ends": "2026-10-01T12:00:00+00:00"}}]}
OPEN_METEO = {
    "timezone": "America/Los_Angeles",
    "current": {"time": "2026-10-01T09:00", "temperature_2m": 61.2,
                "weather_code": 2, "wind_speed_10m": 7.6},
    "daily": {"time": ["2026-10-01", "2026-10-02", "2026-10-03"],
              "weather_code": [2, 61, 0],
              "temperature_2m_max": [74.1, 66.0, 70.0],
              "temperature_2m_min": [51.0, 50.2, 49.0],
              "precipitation_probability_max": [0, 70, 5]}}
#: 09:30 in San Anselmo on 1 October.
ASKED = datetime(2026, 10, 1, 16, 30, tzinfo=timezone.utc)


def om_facts(place=SAN_ANSELMO, now=ASKED):
    snap = weather.open_meteo_snapshot(place, OPEN_METEO, now=now.timestamp())
    return weather.render(snap, place, now=now)


def test_nws_is_worded_as_a_forecast_with_its_official_warning_first():
    fresh = {"properties": {"timestamp": "2026-10-01T15:30:00+00:00",
                            "temperature": {"value": 15.0},
                            "textDescription": "Cloudy"}}
    snap = weather.nws_snapshot(NWS_FORECAST, NWS_ALERTS, fresh,
                                tz="America/Los_Angeles", now=ASKED.timestamp())
    facts = weather.render(snap, SAN_ANSELMO, now=ASKED)
    assert facts.source == weather.NWS and facts.kind == live_facts.WEATHER
    assert facts.facts[0].startswith("The National Weather Service has issued "
                                     "an official Wind Advisory until")
    # A warning that has already ended is gone, not read out.
    assert not any("Heat Advisory" in f for f in facts.facts)
    assert any("59 degrees Fahrenheit and cloudy" in f for f in facts.facts)
    # A period that is already over is never said.
    assert not any(f.startswith("This Morning") for f in facts.facts)
    assert any(f.startswith("Thursday: the forecast calls for sunny, high near "
                            "74 degrees Fahrenheit, 10 percent chance of rain")
               for f in facts.facts)
    assert facts.as_of == datetime(2026, 10, 1, 15, tzinfo=timezone.utc)


def test_an_old_observation_is_dropped_not_called_now():
    old = {"properties": {"timestamp": "2026-10-01T09:00:00+00:00",
                          "temperature": {"value": 15.0}}}
    snap = weather.nws_snapshot(NWS_FORECAST, {}, old, now=ASKED.timestamp())
    facts = weather.render(snap, SAN_ANSELMO, now=ASKED)
    assert not any("Observed" in f for f in facts.facts)


def test_open_meteo_is_the_days_high_low_sky_and_rain_in_local_units():
    us = om_facts()
    assert us.facts[0].startswith("Observed at ")
    assert "61 degrees Fahrenheit and partly cloudy, wind 8 miles an hour" in us.facts[0]
    assert us.facts[1] == ("Today: the forecast calls for partly cloudy, high "
                           "74 degrees Fahrenheit, low 51 degrees Fahrenheit.")
    assert us.facts[2].startswith("Tomorrow: the forecast calls for light rain")
    assert "70 percent chance of rain" in us.facts[2]
    paris = places.Place("Paris", "", "Ile-de-France", "FR", 48.85, 2.35)
    assert "Celsius" in om_facts(paris).facts[1]


def test_a_kept_forecast_said_twelve_hours_later_drops_what_is_over():
    later = om_facts(now=ASKED + timedelta(hours=16))   # 01:30 on the 2nd
    assert not any("Observed" in f for f in later.facts), \
        "a sixteen-hour-old reading was called now"
    assert later.facts[0].startswith("Today: the forecast calls for light rain")


def test_the_weather_block_forbids_turning_a_forecast_into_a_certainty():
    block = om_facts().as_prompt_block()
    assert "A forecast is not an outcome" in block
    assert "has FINISHED" not in block and "NOT STARTED" not in block


def test_sweep_slots_are_in_the_places_own_time():
    # 09:30 in San Anselmo: the last slot was 05:00 there, 12:00 UTC.
    assert weather.last_slot("America/Los_Angeles", ASKED.timestamp()) == \
        datetime(2026, 10, 1, 12, tzinfo=timezone.utc).timestamp()
    # 02:00 there: the last slot was 17:00 the day before.
    early = datetime(2026, 10, 1, 9, tzinfo=timezone.utc).timestamp()
    assert weather.last_slot("America/Los_Angeles", early) == \
        datetime(2026, 10, 1, 0, tzinfo=timezone.utc).timestamp()
    snap = weather.Snapshot(source="x", fetched_at=ASKED.timestamp() - 3600,
                            tz="America/Los_Angeles")
    assert weather.is_current(snap, ASKED.timestamp())
    snap.fetched_at = ASKED.timestamp() - 6 * 3600   # before 05:00 there
    assert not weather.is_current(snap, ASKED.timestamp())


def fake_providers(monkeypatch, nws=None, om=None):
    calls = {"nws": 0, "om": 0, "alerts": 0}

    async def from_nws(place):
        calls["nws"] += 1
        if isinstance(nws, Exception):
            raise nws
        return nws

    async def from_om(place):
        calls["om"] += 1
        return weather.open_meteo_snapshot(place, OPEN_METEO)

    async def alerts(place):
        calls["alerts"] += 1
        return [{"event": "Flood Warning", "ends": ""}]
    monkeypatch.setattr(weather, "from_nws", from_nws)
    monkeypatch.setattr(weather, "from_open_meteo", from_om)
    monkeypatch.setattr(weather, "live_alerts", alerts)
    monkeypatch.setattr(weather, "settings", dataclasses.replace(
        weather.settings, open_meteo_keyless=True))
    return calls


def test_nws_failing_falls_to_open_meteo_and_the_answer_is_kept(monkeypatch):
    calls = fake_providers(monkeypatch, nws=httpx.ConnectError("NWS is down"))
    facts, attempts = asyncio.run(weather.forecast_for(SAN_ANSELMO))
    assert facts.source == weather.OPEN_METEO
    assert [a[0] for a in attempts][:2] == [weather.NWS, weather.OPEN_METEO]
    assert attempts[0][1] == live_facts.PROVIDER_FAILED

    # Asked again before the next sweep slot: the kept forecast, no new
    # forecast call - but the warnings in force are asked for (severe weather).
    again, how = asyncio.run(weather.forecast_for(SAN_ANSELMO))
    assert how[0][0] == "kept"
    assert calls["nws"] == 1 and calls["om"] == 1
    assert calls["alerts"] >= 1
    assert again.facts[0].startswith("The National Weather Service has issued "
                                     "an official Flood Warning")


def test_the_sweep_refreshes_asked_places_whose_slot_came(monkeypatch):
    calls = fake_providers(monkeypatch, nws=httpx.ConnectError("down"))
    asyncio.run(weather.forecast_for(SAN_ANSELMO))
    assert asyncio.run(weather.sweep_due()) == 0, "swept a place already current"
    tomorrow = time.time() + 86400
    assert asyncio.run(weather.sweep_due(tomorrow)) == 1
    assert calls["om"] == 2
    # A place nobody asked about in thirty days is not swept.
    assert asyncio.run(weather.sweep_due(time.time() + 40 * 86400)) == 0


def test_outside_the_us_without_an_open_meteo_key_there_is_no_weather():
    """The free endpoint is non-commercial, so it is never used by default."""
    paris = places.Place("Paris", "", "Ile-de-France", "FR", 48.85, 2.35)
    facts, attempts = asyncio.run(weather.forecast_for(paris))
    assert facts is None
    assert attempts == [(weather.OPEN_METEO, live_facts.NOT_CONFIGURED,
                         "no OPEN_METEO_API_KEY")]
    assert places.geocoder_available()[0] is False


def test_weather_is_a_live_domain_with_its_own_source():
    import live_sources

    live_sources.install()
    names = [s.name for s in live_facts.sources_for("weather")]
    assert names[0] == "weather"


# --------------------------------------------------------------------------
# Places
# --------------------------------------------------------------------------
def test_a_typed_place_is_split_and_a_state_code_expanded():
    assert places.split_name("San Anselmo, CA") == ("San Anselmo", "California", "US")
    assert places.split_name("Paris") == ("Paris", "", "")
    assert SAN_ANSELMO.county_label == "Marin County"
    assert places.Place("X", "Marin County", country="US").county_label == "Marin County"


def test_the_registry_knows_a_towns_county_without_the_network(monkeypatch):
    marin_store()

    async def offline(*a, **k):
        return None
    monkeypatch.setattr(places, "geocode", offline)
    place = asyncio.run(places.resolve("San Anselmo, California"))
    assert place.county == "Marin" and place.region == "California"
    assert not place.located


def test_the_catalogue_picks_a_populated_place_in_the_named_region():
    results = [
        {"name": "San Anselmo", "feature_code": "STM", "admin1": "California",
         "country_code": "US", "population": 0},
        {"name": "San Anselmo", "feature_code": "PPL", "admin1": "California",
         "admin2": "Marin County", "country_code": "US", "population": 12500,
         "latitude": 37.97, "longitude": -122.56},
    ]
    best = places._pick(results, "California", "US")  # noqa: SLF001
    assert best["admin2"] == "Marin County"


# --------------------------------------------------------------------------
# Zero spend
# --------------------------------------------------------------------------
def test_staging_switches_off_the_collector_and_the_weather():
    assert spend_guard.FORCED["LOCAL_NEWS"] == "0"
    assert spend_guard.FORCED["WEATHER"] == "0"
    assert "OPEN_METEO_API_KEY" in spend_guard.PAID_CREDENTIALS
