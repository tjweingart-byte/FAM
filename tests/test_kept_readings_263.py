"""§263: market readings are kept, recency is enforced, the local ladder asks
what the question is about, and an older article cannot move the listener's
date.

From four bad episodes on 10.10-10.13: a 7pm "latest on Nasdaq" told it had
no reading, a March 2025 article in a "last 24 hours" packet, Meta's move on
13 October "corrected" to June 3, and "no airshow over San Francisco Bay"
during Fleet Week, sourced to a Valkyries fan story.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
import types
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import episode_intelligence as ei  # noqa: E402
import live_facts  # noqa: E402
import live_sources as ls  # noqa: E402
import local_news  # noqa: E402
import places  # noqa: E402
import quote_book  # noqa: E402
import research  # noqa: E402
import script_generator as sg  # noqa: E402
import story_sources  # noqa: E402
from live_facts import Entity, LiveFacts, LiveSource  # noqa: E402

# 13 October 2026, 16:00 Eastern (20:00 UTC): a close.
CLOSE_13 = datetime(2026, 10, 13, 20, 0, tzinfo=timezone.utc).timestamp()
# 12 October 2026, 14:10 Eastern: a mid-session reading.
MID_12 = datetime(2026, 10, 12, 18, 10, tzinfo=timezone.utc).timestamp()


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# Recency, enforced in code
# --------------------------------------------------------------------------
def result(days_old, title="x"):
    when = (datetime.now(timezone.utc) - timedelta(days=days_old)).strftime("%Y-%m-%d")
    return types.SimpleNamespace(title=title, url="https://x.example/a",
                                 highlights=["y"], published_date=when)


def test_a_dated_result_before_the_window_is_dropped_and_an_undated_one_kept():
    old = result(570, "Nasdaq-100 in correction")
    fresh = result(0, "Nasdaq today")
    undated = types.SimpleNamespace(title="Quotes page", url="https://q.example",
                                    highlights=["y"], published_date="")
    kept = research.within_window([old, fresh, undated], 2)
    assert old not in kept, "a March 2025 article reached a last-24-hours packet"
    assert fresh in kept and undated in kept


def test_no_window_keeps_everything():
    old = result(900)
    assert research.within_window([old], 0) == [old]


def test_the_retry_widens_the_window_and_never_drops_it():
    assert research.retry_window(0) == 0, "an evergreen question has no window"
    assert research.retry_window(2) >= 7
    assert research.retry_window(30) == 90


# --------------------------------------------------------------------------
# Markets: the latest reading is never discarded
# --------------------------------------------------------------------------
class Quotes(LiveSource):
    name = "Quotes"
    domain = "markets"

    def __init__(self, facts):
        self.facts = facts

    def diagnose(self):
        return True, "test"

    async def resolve(self, brief):
        return Entity(domain="markets", provider=self.name, id="QQQ", label="QQQ")

    async def fetch(self, entity):
        return self.facts


def test_a_closing_quote_hours_old_is_kept_not_withheld(monkeypatch):
    monkeypatch.setattr(live_facts, "_SOURCES", [])
    live_facts.clear_caches()
    yesterday = datetime.now(timezone.utc) - timedelta(hours=27)
    live_facts.register(Quotes(LiveFacts(
        domain="markets", source="Quotes", as_of=yesterday,
        facts=["QQQ closed at 512.30 on Tuesday, October 13."],
        delayed_seconds=1200)))
    got = run(live_facts.lookup(ei.Brief(query="nasdaq", subject="Nasdaq",
                                         live_domain="markets")))
    assert got.outcome == live_facts.FACTS, "the latest reading was withheld"
    block = got.as_prompt_block()
    assert "MOST RECENT reading" in block
    assert "never say there is no reading" in block
    assert "closed at 512.30" in block
    assert got.status == live_facts.UNKNOWN, "a price never establishes an outcome"


def test_a_stale_score_is_still_withheld(monkeypatch):
    """Only markets keep old readings: a score is a claim about now."""
    assert "sports" not in live_facts.KEPT_WHEN_OLD


def test_a_delayed_quote_can_be_fresh_mid_session():
    """At 300s a feed twenty minutes behind by design was never fresh."""
    ten = datetime.now(timezone.utc) - timedelta(minutes=21)
    facts = LiveFacts(domain="markets", source="F", as_of=ten, facts=["x"],
                      delayed_seconds=1200)
    assert facts.is_fresh()


def entity(label="Oracle Corporation", symbol="ORCL"):
    return Entity(domain="markets", provider="Finnhub", id=symbol, label=label)


def test_a_close_is_said_with_its_day_previous_close_and_range():
    facts = ls.FinnhubSource().to_facts(
        {"c": 245.1, "d": 7.6, "dp": 3.2, "pc": 237.5, "o": 238.0, "h": 246.0,
         "l": 237.9, "t": CLOSE_13}, entity(), is_open=False)
    said = " ".join(facts.facts)
    assert "closed at 245.10 on Tuesday, October 13" in said
    assert "up about 3.2 percent" in said and "previous close of 237.50" in said
    assert "237.90 to 246.00" in said


def test_a_mid_session_reading_is_never_called_a_close():
    facts = ls.FinnhubSource().to_facts(
        {"c": 240.0, "dp": 0.5, "t": MID_12}, entity(), is_open=False)
    said = facts.facts[0]
    assert "closed" not in said
    assert "2:10 PM Eastern on Monday, October 12" in said


def test_earlier_sessions_from_the_book_are_given_with_their_days():
    history = [
        {"session": "2026-10-13", "t": CLOSE_13, "c": 245.1, "dp": 3.2, "pc": 237.5},
        {"session": "2026-10-12", "t": MID_12, "c": 240.0, "dp": 0.5, "pc": 238.8},
    ]
    facts = ls.FinnhubSource().to_facts(
        {"c": 246.0, "dp": 0.4, "t": CLOSE_13 + 86400}, entity(),
        is_open=False, history=history)
    said = " ".join(facts.facts)
    assert "On Tuesday, October 13, ORCL closed at 245.10, up about 3.2 percent" in said
    assert "On Monday, October 12, ORCL was last read at 2:10 PM Eastern at 240.00" in said


def test_an_index_resolves_to_its_fund_never_to_nasdaq_inc(monkeypatch):
    async def no_search(*_a, **_k):
        raise AssertionError("an index was looked up in Finnhub's search")

    monkeypatch.setattr(ls, "_json", no_search)
    for subject in ("Nasdaq", "Nasdaq (Stocks & Economy)", "the Nasdaq Composite",
                    "S&P 500", "Dow Jones"):
        got = run(ls.FinnhubSource().resolve(types.SimpleNamespace(subject=subject)))
        assert got is not None and got.id in ("QQQ", "SPY", "DIA"), subject
    facts = ls.FinnhubSource().to_facts(
        {"c": 512.3, "dp": -1.1, "t": CLOSE_13},
        run(ls.FinnhubSource().resolve(types.SimpleNamespace(subject="Nasdaq"))),
        is_open=False)
    assert "not the index itself" in " ".join(facts.facts)
    assert ls.index_proxy("Nasdaq Inc") is None


def test_the_book_keeps_the_latest_reading_of_each_session():
    book = quote_book.book()
    assert book.record("orcl", {"c": 240.0, "dp": 0.5, "t": MID_12})
    assert book.record("ORCL", {"c": 241.0, "dp": 0.9, "t": MID_12 + 3600})
    assert not book.record("ORCL", {"c": 1.0, "t": MID_12}), "an older reading won"
    assert book.record("ORCL", {"c": 245.1, "dp": 3.2, "t": CLOSE_13})
    assert not book.record("ORCL", {"c": 0, "t": CLOSE_13}), "no price is no reading"
    rows = book.sessions("ORCL")
    assert [r["session"] for r in rows] == ["2026-10-13", "2026-10-12"]
    assert rows[1]["c"] == 241.0
    assert book.recent_symbols() == ["ORCL"]


def with_settings(monkeypatch, **kw):
    monkeypatch.setattr(story_sources, "settings",
                        dataclasses.replace(story_sources.settings, **kw))


def test_the_sweep_keeps_every_quote_and_follows_what_episodes_asked(monkeypatch):
    """The threshold decides what becomes a tile, never what FAM knows."""
    quote_book.record("ORCL", {"c": 245.1, "dp": 3.2, "t": CLOSE_13})
    asked = []

    async def _json(url, headers, params, timeout):
        asked.append(params.get("symbol"))
        return {"c": 100.0, "dp": 0.2 if params["symbol"] == "AAPL" else 9.0,
                "t": CLOSE_13 + 86400}

    monkeypatch.setattr(story_sources, "_json", _json)
    with_settings(monkeypatch, finnhub_key="k", finnhub_watchlist="AAPL",
                  stories_market_move_percent=3.0)
    rows = run(story_sources.FinnhubSignals().collect(8))
    assert set(asked) == {"AAPL", "ORCL"}, "a followed symbol was not swept"
    assert rows == [], "a followed symbol became a tile, or a quiet one did"
    assert quote_book.sessions("AAPL"), "a quote under the threshold was thrown away"
    assert len(quote_book.sessions("ORCL")) == 2


# --------------------------------------------------------------------------
# The local ladder asks what the question is about
# --------------------------------------------------------------------------
SF = places.Place("San Francisco", "San Francisco", "California", "US",
                  37.77, -122.42, "test")
AIRSHOW = types.SimpleNamespace(
    retrieval="Blue Angels airshow San Francisco Bay Fleet Week",
    must_establish=["whether the Blue Angels airshow is flying over the bay today"])
VALKYRIES = research.Packet(
    context="SOURCE 1\nTitle: Berkeley Amputee Becomes Sparkling Heart of "
            "Valkyries' Ballhalla\nKey evidence:\nFans at Chase Center in San "
            "Francisco cheered.", backend="parallel", sources=["hoodline.com"])


def test_a_story_that_only_names_the_place_does_not_answer_the_question():
    assert not local_news.relevant(VALKYRIES, AIRSHOW, SF)
    flyover = research.Packet(context="Blue Angels airshow thrills Fleet Week "
                                      "crowds over the bay in San Francisco today")
    assert local_news.relevant(flyover, AIRSHOW, SF)


def test_a_general_town_question_is_answered_by_any_town_story():
    general = types.SimpleNamespace(must_establish=["what's happening in San Francisco today"])
    assert local_news.relevant(VALKYRIES, general, SF)
    assert local_news.relevant(VALKYRIES, types.SimpleNamespace(), SF)


def test_the_web_rung_never_narrows_to_a_learned_outlet_and_drops_what_misses():
    store = local_news.store()
    store.add_outlet("hoodline.com", "https://hoodline.com/", town="San Francisco",
                     county="San Francisco", region="California",
                     scope=local_news.MENTION, source="learned")
    asked = {}

    async def exa(query, domains, must_name):
        asked.update(domains=domains, must_name=must_name)
        return VALKYRIES

    got = run(local_news.research_local(SF, AIRSHOW, exa=exa))
    assert "hoodline.com" not in asked["domains"], \
        "a site an earlier search found narrowed every later search to itself"
    assert asked["must_name"] == "San Francisco"
    assert got.packet is None and got.gap, "an unrelated story was passed as the town's news"


def test_ei_sends_a_specific_happening_to_the_general_search():
    """The prompt line that put "its events" on the local ladder."""
    said = " ".join(ei.build_ei_prompt("Airshow in San Francisco bay today", 2).split())
    assert "airshow in San Francisco Bay today" in said
    assert "that is a news question, searched everywhere" in said


# --------------------------------------------------------------------------
# The writer
# --------------------------------------------------------------------------
def test_an_older_article_cannot_move_the_listener_s_date_or_disprove_an_event():
    plan = dataclasses.replace(
        sg.plan_episode("Why did Meta's stock rise 3.2 percent on 13 October", 2,
                        search=True),
        evidence="SOURCE 1\nTitle: Meta up 3.2%\nPublished: 2026-06-03",
        thin_on=["whether the airshow is on today"])
    prompt = " ".join(sg.build_prompt(plan).split())
    assert "An article cannot speak for a day it was written before" in prompt
    assert "never move their date to the day an older article describes" in prompt.lower() \
        or "Never move their date to the day an older article describes" in prompt
    assert "never say it is not happening" in prompt


def test_the_local_block_never_calls_what_was_found_all_there_is():
    plan = dataclasses.replace(
        sg.plan_episode("airshow in San Francisco", 2, search=True),
        local=local_news.LocalResult(place=SF))
    block = " ".join(sg.build_local_block(plan).split())
    assert "not all that is happening there" in block
    assert "is about San Francisco itself" not in block
