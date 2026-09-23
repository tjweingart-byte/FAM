"""The trending bank (§137): Trending as an edition, built twice a day.

What is pinned here, in the order it would hurt to lose:

* the ten episodes land in the shared cache **under the key a tap computes**,
  and live until the next edition rather than for fifteen minutes;
* GNews builds it, GDELT is asked only when GNews cannot, and the edition
  says which;
* the schedule is 05:00 and 17:00 *Eastern* through daylight saving;
* one builder per slot, and a failed rebuild never takes down an edition;
* the rail reads the bank when there is one and the pool when there is not;
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
    bank.finish(TB.Edition(slot="s", built_at=1000.0, source=TB.GDELT))
    assert bank.claim("s", 2000.0, force=True)
    bank.fail("s", "GNews failed and so did GDELT")
    assert bank.status("s")["status"] == TB.READY
    assert bank.latest().source == TB.GDELT


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
    assert edition.source == TB.GNEWS and not edition.fell_back_from
    assert 1 <= len(edition.stories) <= settings.trending_bank_size
    assert edition.written() == len(edition.stories)
    for story in edition.stories:
        tap = asyncio.run(key_for(plan_episode(story.query,
                                               settings.trending_bank_minutes)))
        assert edition.episodes[story.id]["key"] == tap
        assert cache.get(tap), "a tap would not find the bank's episode"
        # Kept until the next edition and its grace hour - not fifteen minutes.
        expires = cache._data[tap][0]
        assert expires >= TB.next_slot(now).timestamp() + TB.EPISODE_GRACE_SECONDS - 5


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


def test_gdelt_is_the_crutch_and_the_edition_says_so(bank, monkeypatch):
    async def gdelt(now, size):
        return [stories.Signal(subject="Crutch story about the Senate budget",
                               observation="12 outlets", source=TB.GDELT,
                               coverage=12)]
    monkeypatch.setattr(TB, "gdelt_signals", gdelt)
    edition = _build(bank, monkeypatch, fail=True)
    assert edition.source == TB.GDELT
    assert edition.fell_back_from == TB.GNEWS
    assert "GNews failed" in edition.detail


def test_with_no_source_there_is_no_edition_and_the_rail_says_why(bank, monkeypatch):
    configure(monkeypatch, gnews_key="")
    assert asyncio.run(TB.build(now=time.time())) is None
    assert TB.empty_reason() == "FAM isn't connected to a live news source yet."


def test_a_crutch_edition_is_rebuilt_the_moment_a_key_is_added(bank, monkeypatch):
    now = time.time()
    sid = TB.slot_id(TB.last_slot(now))
    bank.claim(sid, now - 60)
    bank.finish(TB.Edition(slot=sid, built_at=now, source=TB.GDELT,
                           detail="GNEWS_KEY is not set; GDELT: 8 stories"))
    configure(monkeypatch, gnews_key="")
    assert not TB.due(now)
    configure(monkeypatch, gnews_key="k")
    assert TB.due(now)


def test_a_gnews_that_failed_is_not_retried_in_a_loop(bank, monkeypatch):
    now = time.time()
    sid = TB.slot_id(TB.last_slot(now))
    bank.claim(sid, now - 60)
    bank.finish(TB.Edition(slot=sid, built_at=now, source=TB.GDELT,
                           detail="GNews failed: GNEWS_KEY was refused"))
    configure(monkeypatch, gnews_key="k")
    assert not TB.due(now)
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
    assert [t["title"] for t in before["world_trending"]["topics"]] == \
        ["A pool story about something else entirely"]

    edition = _build(bank, monkeypatch, now=time.time())
    after = {s["key"]: s for s in T.build_feed(store, "u")["sections"]}
    shown = [t["id"] for t in after["world_trending"]["topics"]]
    assert shown and set(shown) <= {s.id for s in edition.stories}

    section = T.build_section(store, "u", "world_trending")
    assert {t["id"] for t in section["topics"]} <= {s.id for s in edition.stories}
    assert len(section["topics"]) == len(edition.stories)
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


def test_the_gdelt_crutch_really_runs_and_is_gentle(bank, monkeypatch):
    """The crutch itself, not a stand-in: regional reads, one at a time."""
    import gdelt

    configure(monkeypatch, gdelt=True)
    monkeypatch.setattr(TB, "GDELT_GAP_SECONDS", 0.0)
    asked, in_flight, most = [], [0], [0]

    async def artlist(query, limit, hours, timeout):
        in_flight[0] += 1
        most[0] = max(most[0], in_flight[0])
        await asyncio.sleep(0)
        in_flight[0] -= 1
        asked.append(query)
        return [SimpleNamespace(title=t, url=f"https://o{i}.example/{len(asked)}",
                                country="United States", published_date="")
                for i, t in enumerate(STORIES["big"])]

    monkeypatch.setattr(gdelt, "artlist", artlist)
    signals = asyncio.run(TB.gdelt_signals(time.time(), 10))
    assert signals and signals[0].source == "GDELT"
    assert len(asked) == len(TB.GDELT_REGIONS) and most[0] == 1
