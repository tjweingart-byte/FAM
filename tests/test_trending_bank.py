"""The trending bank (§139): Trending as an edition, built twice a day.

What is pinned here, in the order it would hurt to lose:

* the ten episodes land in the shared cache **under the key a tap computes**,
  and live until the next edition rather than for fifteen minutes;
* GNews builds it and nothing else does - no fallback source, and never
  the live pool, which is Made for you's;
* the schedule is 05:00 and 17:00 *Eastern* through daylight saving;
* one builder per slot, and a failed rebuild never takes down an edition;
* the rail reads the bank, and is empty with a reason when there is none;
* the key never reaches a log line.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import sys
import time
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gnews  # noqa: E402
import stories  # noqa: E402
import topics as T  # noqa: E402
import trending_bank as TB  # noqa: E402
from cache import MemoryScriptCache  # noqa: E402
import config  # noqa: E402


def configure(monkeypatch, **changes):
    """Settings with `changes`, everywhere the bank's path reads them."""
    import gdelt

    new = dataclasses.replace(TB.settings, **changes)
    for module in (config, TB, gnews, stories, gdelt):
        monkeypatch.setattr(module, "settings", new)
    return new


class _Settings:
    """`settings.x` reads whatever the bank currently sees."""

    def __getattr__(self, name):
        return getattr(TB.settings, name)


settings = _Settings()


def et(*args) -> float:
    """A wall-clock time in New York, as a timestamp."""
    from zoneinfo import ZoneInfo

    return datetime(*args, tzinfo=ZoneInfo("America/New_York")).timestamp()


@pytest.fixture
def bank(tmp_path, monkeypatch):
    configure(monkeypatch, trending_bank=True)
    configure(monkeypatch, stories_compose=False)
    configure(monkeypatch, gnews_request_gap_seconds=0.0)
    configure(monkeypatch, gnews_key="")
    configure(monkeypatch, gdelt=False)
    store = TB.BankStore(str(tmp_path / "bank" / "trending_bank.db"))
    TB.reset(store)
    yield store
    TB.reset()


# --------------------------------------------------------------------------
# The clock
# --------------------------------------------------------------------------
def test_editions_are_at_five_and_five_eastern():
    assert TB.slot_id(TB.last_slot(et(2026, 9, 23, 4, 59))) == "2026-09-22T17:00-04:00"
    assert TB.slot_id(TB.last_slot(et(2026, 9, 23, 5, 0))) == "2026-09-23T05:00-04:00"
    assert TB.slot_id(TB.next_slot(et(2026, 9, 23, 5, 0))) == "2026-09-23T17:00-04:00"
    assert TB.slot_id(TB.next_slot(et(2026, 9, 23, 18, 0))) == "2026-09-24T05:00-04:00"


def test_five_am_stays_five_am_across_daylight_saving():
    # Summer: 05:00 EDT is 09:00 UTC. Winter: 05:00 EST is 10:00 UTC.
    summer = TB.next_slot(et(2026, 7, 1, 1, 0))
    winter = TB.next_slot(et(2026, 12, 1, 1, 0))
    assert summer.hour == winter.hour == 5
    assert datetime.utcfromtimestamp(summer.timestamp()).hour == 9
    assert datetime.utcfromtimestamp(winter.timestamp()).hour == 10
    # The night the clocks go back is still two editions, twelve hours apart
    # on the wall clock.
    first = TB.next_slot(et(2026, 11, 1, 0, 30))
    second = TB.next_slot(first.timestamp())
    assert (first.hour, second.hour) == (5, 17)


def test_the_hours_are_a_setting(monkeypatch):
    configure(monkeypatch, trending_bank_hours="6, 18, nonsense, 99")
    assert TB.hours() == [6, 18]
    configure(monkeypatch, trending_bank_hours="")
    assert TB.hours() == [5, 17]


# --------------------------------------------------------------------------
# One builder per slot
# --------------------------------------------------------------------------
def test_a_slot_is_claimed_once(bank):
    assert bank.claim("s", 1000.0) is True
    assert bank.claim("s", 1001.0) is False


def test_a_crashed_builder_is_taken_over(bank):
    assert bank.claim("s", 1000.0)
    assert not bank.claim("s", 1000.0 + TB.STALE_CLAIM_SECONDS - 1)
    assert bank.claim("s", 1000.0 + TB.STALE_CLAIM_SECONDS)


def test_a_failed_slot_waits_before_it_is_tried_again(bank):
    assert bank.claim("s", 1000.0)
    bank.fail("s", "no stories")
    assert not bank.claim("s", 1000.0 + settings.trending_bank_retry_seconds - 1)
    assert bank.claim("s", 1000.0 + settings.trending_bank_retry_seconds)


def test_a_failed_rebuild_keeps_the_edition_it_was_replacing(bank):
    bank.claim("s", 1000.0)
    bank.finish(TB.Edition(slot="s", built_at=1000.0, source=TB.GNEWS,
                           detail="the first build"))
    assert bank.claim("s", 2000.0, force=True)
    bank.fail("s", "GNews failed: GNews is rate-limiting this key")
    assert bank.status("s")["status"] == TB.READY
    assert bank.latest().detail == "the first build"


def test_the_daily_ceiling_holds(bank):
    assert [bank.spend("GNews", 3, 1000.0) for _ in range(4)] == [True, True, True, False]
    assert bank.spent("GNews", 1000.0) == 3
    # A new UTC day is a new allowance.
    assert bank.spend("GNews", 3, 1000.0 + 86400)


def test_reading_the_rail_never_creates_a_database(tmp_path, monkeypatch):
    path = tmp_path / "nowhere" / "trending_bank.db"
    monkeypatch.setenv("TRENDING_BANK_DB", str(path))
    TB.reset()
    assert TB.stories_now() == []
    assert not path.exists()


# --------------------------------------------------------------------------
# GNews
# --------------------------------------------------------------------------
def _article(title, url, country="us", source="Outlet"):
    return {"title": title, "description": "", "url": url,
            "publishedAt": "2026-09-23T08:00:00Z",
            "source": {"name": source, "url": url, "country": country}}


#: Three stories: one every feed leads with, one in two feeds, one in one.
STORIES = {
    "big": ["Senate Passes Budget Deal After Marathon Vote",
            "Budget Deal Passes Senate in Marathon Vote",
            "Senate Budget Deal Passes After Marathon Night Vote"],
    "mid": ["Hurricane Milton Makes Landfall Near Tampa Coast",
            "Hurricane Milton Landfall Tampa Coast Evacuations"],
    "small": ["Apple Unveils New Vision Headset Pricing"],
}


def _handler(calls: list, fail: bool = False, totals: dict | None = None):
    totals = totals or {"Senate": 900, "Milton": 400, "Apple": 20}

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if fail:
            return httpx.Response(500, json={"errors": ["boom"]})
        path = request.url.path
        if path.endswith("/search"):
            q = request.url.params.get("q", "")
            total = next((n for word, n in totals.items() if word in q), 5)
            return httpx.Response(200, json={"totalArticles": total, "articles": [
                _article(f"{q} coverage", f"https://s{i}.example/{q}", "gb")
                for i in range(2)]})
        feed = (request.url.params.get("category")
                or request.url.params.get("country"))
        rows = [_article(STORIES["big"][0], f"https://a.example/{feed}/big")]
        if feed in ("general", "world", "us"):
            rows.append(_article(STORIES["mid"][0], f"https://b.example/{feed}/mid"))
        if feed == "technology":
            rows.append(_article(STORIES["small"][0], f"https://c.example/small"))
        return httpx.Response(200, json={"totalArticles": len(rows), "articles": rows})

    return handle


def _client(calls, store, **kw):
    return gnews.Client(
        spend=lambda _e: store.spend(TB.GNEWS, settings.gnews_daily_requests),
        transport=httpx.MockTransport(_handler(calls, **kw)), gap=0.0)


def test_parse_reads_a_v4_response():
    result = gnews.parse({"totalArticles": 812, "articles": [
        _article("One", "https://x.example/1", "US"), {"title": "no url"}, "junk"]},
        "category:world")
    assert result.total == 812
    [only] = result.articles
    assert (only.title, only.country, only.feed, only.rank) == (
        "One", "us", "category:world", 0)


def test_the_key_never_reaches_a_log_line(caplog):
    record = logging.LogRecord("httpx", logging.INFO, __file__, 1,
                               'HTTP Request: GET %s "HTTP/1.1 200 OK"',
                               ("https://gnews.io/api/v4/search?q=x&apikey=SECRET123&max=1",),
                               None)
    for f in logging.getLogger("httpx").filters:
        f.filter(record)
    assert "SECRET123" not in record.getMessage()
    assert "apikey=[redacted]" in record.getMessage()


def test_a_refusal_names_the_fix_and_not_the_url(bank, monkeypatch):
    configure(monkeypatch, gnews_key="SECRET123")
    client = gnews.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(401, json={"errors": ["bad key"]})), gap=0.0)
    with pytest.raises(gnews.GNewsError) as err:
        asyncio.run(client.top_headlines(category="world"))
    assert "GNEWS_KEY was refused" in str(err.value)
    assert "SECRET123" not in str(err.value)


def test_the_ceiling_refuses_before_a_request_is_made(bank, monkeypatch):
    configure(monkeypatch, gnews_key="k")
    calls = []
    client = gnews.Client(spend=lambda _e: False,
                          transport=httpx.MockTransport(_handler(calls)), gap=0.0)
    with pytest.raises(gnews.QuotaSpent):
        asyncio.run(client.top_headlines(category="world"))
    assert calls == []


def test_search_words_are_plain_and_lead_with_names():
    words = TB.search_words('Hurricane Milton makes "landfall" near Tampa: what now?')
    assert '"' not in words and ":" not in words
    assert words.split()[:2] == ["Milton", "Tampa"]
    # Title Case capitalises everything, so there the longest words lead -
    # and the subject is not dropped for being the first word.
    assert "Senate" in TB.search_words("Senate Passes Budget Deal After Marathon Vote")


def test_gnews_ranks_by_how_widely_a_story_runs(bank, monkeypatch):
    configure(monkeypatch, gnews_key="k")
    calls = []
    signals = asyncio.run(TB.gnews_signals(_client(calls, bank), time.time(), 10))
    subjects = [s.subject for s in signals]
    assert subjects[0].startswith("Senate") and "Milton" in subjects[1]
    assert signals[0].coverage == 900 and signals[0].source == TB.GNEWS
    assert all(s.observation and "What they are saying" in s.observation
               for s in signals)
    # Fourteen feeds plus one search per candidate, every one counted.
    assert bank.spent(TB.GNEWS) == len(calls)
    assert len(calls) <= 14 + settings.gnews_corroborate


# --------------------------------------------------------------------------
# Building an edition
# --------------------------------------------------------------------------
class FakeGenerator:
    """Stands in for `ScriptGenerator`: a brief, then sentences."""

    client = None

    def __init__(self, outcome_dependent=(), live=()):
        self.outcome_dependent = set(outcome_dependent)
        self.live = set(live)
        self.written = []

    async def understand(self, plan, notes):
        brief = SimpleNamespace(
            outcome_dependent=any(w in plan.query for w in self.outcome_dependent),
            degraded=False)
        return dataclasses.replace(plan, brief=brief) if hasattr(plan, "brief") \
            else SimpleNamespace(**{**plan.__dict__, "brief": brief})

    async def stream_sentences(self, plan, notes):
        self.written.append(plan.query)
        notes.title = f"Title for {plan.query[:20]}"
        if any(w in plan.query for w in self.live):
            notes.live_status = "in_progress"
        for i in range(3):
            yield f"Sentence {i} about {plan.query[:20]}."


def _build(bank, monkeypatch, generator=None, cache=None, now=None, **kw):
    configure(monkeypatch, gnews_key="k")
    calls = []
    return asyncio.run(TB.build(
        now=now or et(2026, 9, 23, 5, 0, 5), generator=generator, cache=cache,
        client=_client(calls, bank, **kw)))


def test_an_edition_writes_its_episodes_under_the_key_a_tap_computes(bank, monkeypatch):
    from pipeline import key_for
    from script_generator import plan_episode

    cache, generator = MemoryScriptCache(), FakeGenerator()
    now = time.time()
    edition = _build(bank, monkeypatch, generator, cache, now=now)
    assert edition.source == TB.GNEWS
    assert 1 <= len(edition.stories) <= settings.trending_bank_size
    assert edition.written() == len(edition.stories)
    for story in edition.stories:
        tap = asyncio.run(key_for(plan_episode(story.query,
                                               settings.trending_bank_minutes)))
        assert edition.episodes[story.id]["key"] == tap
        assert cache.get(tap), "a tap would not find the bank's episode"
        # Kept for as long as the edition can be shown - not fifteen minutes,
        # and not only until the next edition, which might fail to build.
        expires = cache._data[tap][0]
        assert expires >= now + settings.trending_bank_max_age_hours * 3600 - 5


def test_the_size_is_a_setting(bank, monkeypatch):
    configure(monkeypatch, trending_bank_size=1)
    edition = _build(bank, monkeypatch)
    assert len(edition.stories) == 1


def test_a_result_is_never_written_ahead_and_a_live_score_is_never_kept(bank, monkeypatch):
    cache = MemoryScriptCache()
    generator = FakeGenerator(outcome_dependent={"Senate"}, live={"Milton"})
    edition = _build(bank, monkeypatch, generator, cache)
    by_subject = {s.subject[:6]: edition.episodes[s.id] for s in edition.stories}
    assert by_subject["Senate"]["status"] == "volatile"
    assert by_subject["Hurric"]["status"] == "volatile"
    assert not cache.get(by_subject["Hurric"]["key"])
    assert not any("Senate" in q for q in generator.written)


def test_an_edition_is_built_once_per_slot(bank, monkeypatch):
    now = et(2026, 9, 23, 5, 0, 5)
    assert _build(bank, monkeypatch, now=now) is not None
    assert _build(bank, monkeypatch, now=now + 60) is None
    assert _build(bank, monkeypatch, now=et(2026, 9, 23, 17, 0, 5)) is not None


def test_gnews_is_the_only_source_and_there_is_no_fallback(bank, monkeypatch):
    """GNews failing leaves no edition - not a GDELT one - even with GDELT on."""
    import gdelt

    configure(monkeypatch, gdelt=True)
    asked = []

    async def artlist(*a, **k):
        asked.append(a)
        raise AssertionError("the trending bank asked GDELT")

    monkeypatch.setattr(gdelt, "artlist", artlist)
    assert _build(bank, monkeypatch, fail=True) is None
    assert asked == []
    row = bank.status(TB.slot_id(TB.last_slot(et(2026, 9, 23, 5, 0, 5))))
    assert row["status"] == TB.FAILED and "GNews failed" in row["detail"]
    assert not hasattr(TB, "gdelt_signals")


def test_a_failed_build_leaves_the_last_edition_up(bank, monkeypatch):
    morning = _build(bank, monkeypatch, now=et(2026, 9, 23, 5, 0, 5))
    assert _build(bank, monkeypatch, now=et(2026, 9, 23, 17, 0, 5), fail=True) is None
    TB.reset(bank)
    evening = et(2026, 9, 23, 17, 30)
    assert [s.id for s in TB.stories_now(evening)] == [s.id for s in morning.stories]
    # And not forever: past TRENDING_BANK_MAX_AGE_HOURS the row empties.
    TB.reset(bank)
    late = et(2026, 9, 23, 5, 0, 5) + (settings.trending_bank_max_age_hours + 1) * 3600
    assert TB.stories_now(late) == []


def test_with_no_key_nothing_is_attempted_and_the_rail_says_why(bank, monkeypatch):
    configure(monkeypatch, gnews_key="")
    assert not TB.due()
    assert asyncio.run(TB.build(now=time.time())) is None
    assert TB.empty_reason() == "FAM isn't connected to a live news source yet."


def test_a_slot_that_failed_for_want_of_a_key_is_built_once_there_is_one(bank,
                                                                        monkeypatch):
    now = time.time()
    configure(monkeypatch, gnews_key="")
    asyncio.run(TB.build(now=now))       # the operator's --build, keyless
    configure(monkeypatch, gnews_key="k")
    assert TB.due(now + 60)
    row = bank.status(TB.slot_id(TB.last_slot(now)))
    assert TB._retry_at_once(row)


def test_a_gnews_that_failed_is_not_retried_in_a_loop(bank, monkeypatch):
    now = et(2026, 9, 23, 5, 0, 5)
    _build(bank, monkeypatch, now=now, fail=True)
    assert not TB.due(now + 60)
    assert TB.due(now + settings.trending_bank_retry_seconds)


def test_an_edition_survives_a_restart(bank, monkeypatch):
    edition = _build(bank, monkeypatch, now=time.time())
    TB.reset(bank)
    again = TB.current()
    assert again is not None and [s.id for s in again.stories] == \
        [s.id for s in edition.stories]
    assert isinstance(again.stories[0].countries, tuple)


def test_an_old_edition_is_not_shown(bank, monkeypatch):
    built = time.time() - (settings.trending_bank_max_age_hours + 1) * 3600
    _build(bank, monkeypatch, now=built)
    TB.reset(bank)
    assert TB.stories_now() == []


# --------------------------------------------------------------------------
# The rail
# --------------------------------------------------------------------------
def _pool_story(subject):
    now = time.time()
    return stories.Story(subject=subject, title=subject, angle="a",
                         query=f"{subject}?", first_seen=now, last_seen=now,
                         strength=0.9, coverage=5)


def test_trending_reads_the_bank_when_there_is_an_edition(bank, monkeypatch, tmp_path):
    stories.reset()
    stories.seed([_pool_story("A pool story about something else entirely")])
    store = T.EventStore(str(tmp_path / "myfam.db"))

    before = {s["key"]: s for s in T.build_feed(store, "u")["sections"]}
    assert before["world_trending"]["topics"] == []

    edition = _build(bank, monkeypatch, now=time.time())
    after = {s["key"]: s for s in T.build_feed(store, "u")["sections"]}
    shown = [t["id"] for t in after["world_trending"]["topics"]]
    assert shown and set(shown) <= {s.id for s in edition.stories}

    section = T.build_section(store, "u", "world_trending")
    assert {t["id"] for t in section["topics"]} <= {s.id for s in edition.stories}
    assert len(section["topics"]) == len(edition.stories)
    stories.reset()


def test_trending_never_reads_the_live_pool_while_the_bank_is_on(bank, monkeypatch,
                                                              tmp_path):
    """The owner's rule: Trending is GNews, and the pool is Made for you's.
    With no edition the row is empty and says why - never the pool."""
    configure(monkeypatch, gnews_key="")
    stories.reset()
    stories.seed([_pool_story("A pool story from GDELT or API-Sports")])
    store = T.EventStore(str(tmp_path / "myfam.db"))
    sections = {s["key"]: s for s in T.build_feed(store, "u")["sections"]}
    trending = sections["world_trending"]
    assert trending["topics"] == []
    assert trending["empty_reason"] == TB.empty_reason()
    assert "Made for you" not in trending["empty_reason"]
    view_more = T.build_section(store, "u", "world_trending")
    assert view_more["topics"] == []
    # And the pool story is still offered - by Made for you, where it
    # belongs. (It used to reach the page through What you missed's top-up,
    # which no longer reads the pool at all.)
    offered = [t.id for t in T.browse_inventory(T.live_topics(), False)]
    assert stories.story_id("A pool story from GDELT or API-Sports") in offered
    stories.reset()


def test_the_live_pool_never_spends_gnews(bank, monkeypatch):
    """GNews requests are the bank's alone. A pool sweep with every source
    installed makes none, and nothing but the bank imports the client."""
    import pathlib

    configure(monkeypatch, gnews_key="k", stories=True)
    calls = []

    async def refused(self, endpoint, params):
        calls.append(endpoint)
        raise AssertionError("the live pool asked GNews")

    monkeypatch.setattr(gnews.Client, "_get", refused)
    stories.reset()
    stories.install()
    asyncio.run(stories.refresh())
    assert calls == [] and bank.spent(TB.GNEWS) == 0
    assert not any("gnews" in s.name.lower() for s in stories.sources())

    root = pathlib.Path(__file__).resolve().parent.parent
    importers = sorted(p.name for p in root.glob("*.py")
                       if p.name != "gnews.py"
                       and ("import gnews" in p.read_text()
                            or "from gnews" in p.read_text()))
    assert importers == ["trending_bank.py"], importers
    stories.reset()


def test_with_the_bank_switched_off_trending_is_the_pool(bank, monkeypatch, tmp_path):
    _build(bank, monkeypatch, now=time.time())
    configure(monkeypatch, trending_bank=False)
    stories.reset()
    stories.seed([_pool_story("Only the pool story")])
    store = T.EventStore(str(tmp_path / "myfam.db"))
    sections = {s["key"]: s for s in T.build_feed(store, "u")["sections"]}
    assert [t["title"] for t in sections["world_trending"]["topics"]] == \
        ["Only the pool story"]
    stories.reset()


def test_the_report_says_where_the_edition_came_from(bank, monkeypatch):
    _build(bank, monkeypatch, now=time.time())
    report = TB.report()
    assert report["edition"]["source"] == TB.GNEWS
    assert report["schedule"]["hours"] == [5, 17]
    assert report["gnews"]["requests_today"] > 0
    json.dumps(report)



# --------------------------------------------------------------------------
# What the pre-merge review found
# --------------------------------------------------------------------------
def test_a_forced_rebuild_keeps_the_edition_on_the_row_while_it_runs(bank, monkeypatch):
    now = et(2026, 9, 23, 5, 0, 5)
    first = _build(bank, monkeypatch, now=now)
    sid = TB.slot_id(TB.last_slot(now))
    assert bank.claim(sid, now + 60, force=True)        # the operator's --build
    assert bank.status(sid)["status"] == TB.BUILDING
    TB.reset(bank)
    assert [s.id for s in TB.stories_now(now + 120)] == [s.id for s in first.stories]


def test_force_never_overrides_a_live_builder(bank):
    assert bank.claim("s", 1000.0)
    assert not bank.claim("s", 1060.0, force=True)
    assert bank.claim("s", 1000.0 + TB.STALE_CLAIM_SECONDS, force=True)


def test_a_long_build_keeps_its_claim_alive(bank):
    assert bank.claim("s", 1000.0)
    bank.touch("s", 1000.0 + TB.STALE_CLAIM_SECONDS - 1)
    assert not bank.claim("s", 1000.0 + TB.STALE_CLAIM_SECONDS)


def test_a_refused_key_stops_after_one_request(bank, monkeypatch):
    configure(monkeypatch, gnews_key="bad")
    calls = []

    def refuse(request):
        calls.append(request)
        return httpx.Response(401, json={"errors": ["bad key"]})

    client = gnews.Client(
        spend=lambda _e: bank.spend(TB.GNEWS, settings.gnews_daily_requests),
        transport=httpx.MockTransport(refuse), gap=0.0)
    with pytest.raises(gnews.GNewsError):
        asyncio.run(TB.gnews_signals(client, time.time(), 10))
    assert len(calls) == 1 and bank.spent(TB.GNEWS) == 1


def test_a_build_cut_short_by_a_shutdown_is_retried_at_once(bank, monkeypatch):
    configure(monkeypatch, gnews_key="k")
    now = time.time()

    async def slow(*_a, **_k):
        await asyncio.sleep(10)

    monkeypatch.setattr(TB, "collect", slow)

    async def run():
        task = asyncio.ensure_future(TB.build(now=now))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    row = bank.status(TB.slot_id(TB.last_slot(now)))
    assert row["status"] == TB.FAILED and TB._retry_at_once(row)
    assert TB.due(now + 60)


def test_the_empty_sentence_is_right_for_each_cause(bank, monkeypatch):
    now = et(2026, 9, 23, 5, 0, 5)
    configure(monkeypatch, gnews_key="k")
    assert TB.empty_reason(now) == "Today's trending stories are being put together now."
    _build(bank, monkeypatch, now=now, fail=True)
    TB.reset(bank)                      # a restart: nothing held in memory
    assert TB.empty_reason(now + 60).startswith("Couldn't reach the news source")
    _build(bank, monkeypatch, now=et(2026, 9, 23, 17, 0, 5))
    TB.reset(bank)
    # An edition exists and this listener has heard it all.
    reason = TB.empty_reason(et(2026, 9, 23, 18, 0))
    assert reason.startswith("You've heard everything trending") and "5 AM" in reason


def test_a_trending_play_counts_like_any_other_tile(bank, monkeypatch):
    edition = _build(bank, monkeypatch, now=time.time())
    story = edition.stories[0]
    assert story.id in T.known_topics()
    assert T.tags_for_id(story.id) == tuple(story.tags)


def test_health_does_not_create_the_database(tmp_path, monkeypatch):
    path = tmp_path / "never" / "trending_bank.db"
    monkeypatch.setenv("TRENDING_BANK_DB", str(path))
    configure(monkeypatch, trending_bank=True)
    TB.reset()
    TB.report()
    TB.empty_reason()
    assert not path.exists()


def test_health_reports_the_bank_database_once_it_exists(bank, monkeypatch):
    """Lazy, like the voice registry - but reported once a build made it."""
    from fastapi.testclient import TestClient

    import app as appmod

    monkeypatch.setattr(appmod, "settings",
                        dataclasses.replace(appmod.settings, trending_bank=True))
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    _build(bank, monkeypatch, now=time.time())
    body = TestClient(appmod.app).get("/api/health").json()
    entry = next(e for e in body["databases"] if e["env_var"] == "TRENDING_BANK_DB")
    assert entry["readable"] and entry["writable"]


def test_a_heard_edition_story_leaves_only_that_listeners_row(bank, monkeypatch,
                                                              tmp_path):
    """No repeats reaches the GNews edition too (§136 on §139's row): a story
    a listener has heard is gone from their Trending, rail and View more, and
    still on everybody else's."""
    now = time.time()
    _build(bank, monkeypatch, now=now)
    store = T.EventStore(str(tmp_path / "events.db"))

    def row(user):
        feed = T.build_feed(store, user, now=now + 60)
        return [t["id"] for s in feed["sections"] if s["key"] == "world_trending"
                for t in s["topics"]]

    before = row("heard")
    assert before, "the edition did not reach the Trending row"
    story = T.known_topics(now)[before[0]]
    store.record(T.Event("heard", "play", story.id, story.query, story.tags,
                         now))
    after = row("heard")
    assert story.id not in after and f"{story.id}-new" not in after
    section = T.build_section(store, "heard", "world_trending", now=now + 60)
    assert story.id not in {t["id"] for t in section["topics"]}
    assert story.id in row("other")
