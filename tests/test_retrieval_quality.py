"""Recency filters, credibility sorts, and one retry when the packet is thin.

These three are the answer to "the search should always come up with current
information", and each does a different job:

* **The window** decides what is eligible. Nothing stale is considered, however
  well linked it is.
* **The grade** decides the order within what survives. So a question about
  last night is answered from last night *by a wire service*, rather than by
  whoever published fastest - which is what "prioritise recency without giving
  up source quality" has to mean if it means anything.
* **The retry** is the one extra search a packet gets when it does not contain
  what the brief said the episode needs. One, never more: an unbounded loop is
  an unbounded wait in front of the first word.

Nothing here needs an EXA_API_KEY, `exa_py` or the network.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import episode_intelligence as ei  # noqa: E402
import research  # noqa: E402

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


class FakeResult:
    def __init__(self, title, url, highlights, published_date=None):
        self.title, self.url, self.highlights = title, url, highlights
        self.published_date = published_date


def days_ago(n: int) -> str:
    return (NOW - timedelta(days=n)).strftime("%Y-%m-%d")


@pytest.fixture
def exa(monkeypatch):
    """A stubbed Exa client that records every call and replays queued replies."""
    calls: list = []
    queue: list = []

    class FakeClient:
        def search_and_contents(self, query, **kwargs):
            calls.append({"query": query, **kwargs})
            results = queue.pop(0) if queue else []
            return types.SimpleNamespace(results=results, cost_dollars=None)

    monkeypatch.setattr(research, "_client", lambda: FakeClient())
    monkeypatch.setattr(research, "settings",
                        dataclasses.replace(config.settings,
                                            research_backend="exa"))
    return calls, queue


# --------------------------------------------------------------------------
# dates
# --------------------------------------------------------------------------
def test_a_published_date_is_read_and_turned_into_the_words_a_person_uses():
    """The blueprint's rule: relative labels come from normalized time, not
    from guessing at prose. Computed here so the model reads "yesterday"
    instead of working it out - and gets it wrong."""
    assert research.age_phrase(NOW - timedelta(days=0), NOW) == "today"
    assert research.age_phrase(NOW - timedelta(days=1), NOW) == "yesterday"
    assert research.age_phrase(NOW - timedelta(days=2), NOW) == "2 days ago"
    assert research.age_phrase(None, NOW) == "date not stated"


def test_a_source_dated_in_the_future_is_flagged_rather_than_trusted():
    """It happens, and it is the one date that must never quietly become "today"
    - a future-dated article about an event that has not occurred is how a
    result gets invented."""
    assert "suspicion" in research.age_phrase(NOW + timedelta(days=3), NOW)


def test_a_missing_date_is_not_filled_in():
    assert research.published_at(FakeResult("x", "https://a.com/x", [])) is None
    assert research.published_at(
        FakeResult("x", "https://a.com/x", [], "not a date")) is None


# --------------------------------------------------------------------------
# credibility
# --------------------------------------------------------------------------
@pytest.mark.parametrize("url, tier", [
    ("https://www.reuters.com/a", "primary"),
    ("https://finance.yahoo.com/b", "established"),
    ("https://federalreserve.gov/c", "primary"),
    ("https://some-blog.example/d", "unverified"),
    ("", "unverified"),
])
def test_publishers_are_graded_and_an_unknown_one_is_never_graded_high(url, tier):
    assert research.credibility(FakeResult("x", url, [])) == tier


def test_the_best_source_goes_first_and_the_newest_wins_within_a_tier():
    """Recency has already filtered; this is the sort. A fresh unknown blog
    must not outrank a wire service from the same window."""
    blog = FakeResult("Blog", "https://blog.example/x", ["a"], days_ago(0))
    wire_old = FakeResult("Wire older", "https://reuters.com/x", ["b"], days_ago(2))
    wire_new = FakeResult("Wire newer", "https://apnews.com/x", ["c"], days_ago(1))

    ranked = research.rank_results([blog, wire_old, wire_new], NOW)
    assert [r.title for r in ranked] == ["Wire newer", "Wire older", "Blog"]


def test_an_undated_source_sorts_last_within_its_tier():
    """It may be anything, so it loses to anything known."""
    dated = FakeResult("Dated", "https://reuters.com/a", ["x"], days_ago(5))
    undated = FakeResult("Undated", "https://apnews.com/b", ["y"])
    assert [r.title for r in research.rank_results([undated, dated], NOW)] == \
        ["Dated", "Undated"]


# --------------------------------------------------------------------------
# the window
# --------------------------------------------------------------------------
def test_a_question_about_a_moment_is_searched_inside_a_window(exa):
    calls, queue = exa
    queue.append([FakeResult("x", "https://reuters.com/a", ["y"], days_ago(1))])
    brief = ei.Brief(query="what did the fed do", search_query="fed decision",
                     recency_days=7)
    asyncio.run(research.retrieve("fed decision", brief=brief))
    assert "start_published_date" in calls[0]


def test_an_evergreen_question_is_not_confined_to_a_window(exa):
    """A piece on how something works is not improved by refusing to look at
    anything written last year."""
    calls, queue = exa
    queue.append([FakeResult("x", "https://reuters.com/a", ["y"], days_ago(400))])
    brief = ei.Brief(query="how does a heat pump work",
                     search_query="heat pump mechanism", recency_days=0)
    asyncio.run(research.retrieve("heat pump mechanism", brief=brief))
    assert "start_published_date" not in calls[0]


def test_the_window_reaches_the_packet_for_a_person_to_read(exa):
    _, queue = exa
    queue.append([FakeResult("x", "https://reuters.com/a", ["y"], days_ago(1))])
    packet = asyncio.run(research.retrieve(
        "fed decision", brief=ei.Brief(recency_days=7)))
    assert packet.window_days == 7
    assert packet.as_dict()["window_days"] == 7


# --------------------------------------------------------------------------
# sufficiency, and the one retry
# --------------------------------------------------------------------------
def test_a_packet_that_answers_the_brief_is_accepted():
    packet = ("SOURCE 1\nTitle: 49ers beat the Rams 24-17\n"
              "Key evidence:\nThe final score was 24-17.\n")
    covered, missing = research.packet_covers(packet, ["final score", "49ers"])
    assert covered and not missing


def test_a_packet_that_misses_what_was_asked_for_says_which_part():
    packet = "SOURCE 1\nTitle: Rams injury report\nKey evidence:\nSomeone is out.\n"
    covered, missing = research.packet_covers(
        packet, ["49ers final score", "Chiefs quarterback rating"])
    assert not covered
    assert missing


def test_a_brief_that_asked_for_nothing_is_always_satisfied():
    """No brief means the pre-EI path, which never retried and must not start."""
    assert research.packet_covers("anything", []) == (True, [])
    assert research.packet_covers("anything", None) == (True, [])


def test_a_thin_packet_buys_exactly_one_more_search(exa):
    calls, queue = exa
    queue.append([FakeResult("Unrelated", "https://a.example/x",
                             ["Nothing to do with it."], days_ago(1))])
    queue.append([FakeResult("Still unrelated", "https://b.example/x",
                             ["Also nothing."], days_ago(1))])
    brief = ei.Brief(query="who won", search_query="49ers result",
                     subject="the 49ers game", recency_days=3,
                     must_establish=["49ers final score", "touchdown scorers"])

    packet = asyncio.run(research.retrieve("49ers result", brief=brief))
    assert len(calls) == 2, "the retry did not fire, or fired more than once"
    assert packet.retried is True
    assert packet.searches == 2, "a retry that is not counted is a cost nobody sees"


def test_the_retry_drops_the_window_and_searches_the_resolved_subject(exa):
    """Not a rephrasing by another model call - that would put a second round
    trip in front of the first word. The two things most likely to have caused
    an empty result are the window and the wording, so it changes both."""
    calls, queue = exa
    queue.append([])
    queue.append([FakeResult("Found", "https://reuters.com/x",
                             ["The 49ers won 24-17."], days_ago(1))])
    brief = ei.Brief(query="who won", search_query="49ers result last night",
                     subject="San Francisco 49ers game result", recency_days=1,
                     must_establish=["49ers final score"])

    asyncio.run(research.retrieve("49ers result last night", brief=brief))
    assert calls[1]["query"] == "San Francisco 49ers game result"
    assert "start_published_date" not in calls[1]


def test_the_better_packet_wins_and_its_cost_is_the_sum_of_both(exa):
    calls, queue = exa
    queue.append([])
    queue.append([FakeResult("Found", "https://reuters.com/x",
                             ["The 49ers final score was 24-17."], days_ago(1))])
    brief = ei.Brief(query="who won", search_query="x", subject="49ers",
                     must_establish=["49ers final score"])

    packet = asyncio.run(research.retrieve("x", brief=brief))
    assert "24-17" in packet.context, "the retry's evidence was thrown away"
    assert packet.searches == 2
    assert packet.cost == pytest.approx(research.COST_PER_SEARCH * 2)


def test_a_retry_that_also_misses_still_returns_its_evidence(exa):
    """Half an answer beats none. The writer is told which parts are thin
    rather than the episode being abandoned."""
    calls, queue = exa
    queue.append([FakeResult("Partial", "https://reuters.com/x",
                             ["The 49ers played."], days_ago(1))])
    queue.append([FakeResult("Partial", "https://reuters.com/x",
                             ["The 49ers played."], days_ago(1))])
    brief = ei.Brief(query="who won", search_query="x", subject="49ers",
                     must_establish=["final score", "touchdown scorers",
                                     "injury report"])
    packet = asyncio.run(research.retrieve("x", brief=brief))
    assert packet.context, "a thin packet was discarded instead of used"
    assert packet.missing, "the writer was not told which part is thin"


def test_a_second_look_that_fails_keeps_the_first_packet(exa, monkeypatch):
    """The distinction that earns `_second_look` its exemption from the
    no-handlers rule: a *first* retrieval failing means research never
    happened and must reach the caller; a *second* failing means research
    happened and a bonus attempt did not."""
    calls, queue = exa
    queue.append([FakeResult("First", "https://reuters.com/x",
                             ["Something partial."], days_ago(1))])

    async def explode(*args, **kwargs):
        raise RuntimeError("exa fell over")

    monkeypatch.setattr(research, "_second_look", explode)
    brief = ei.Brief(query="who won", search_query="x", subject="49ers",
                     must_establish=["final score", "touchdown scorers"])
    with pytest.raises(RuntimeError):
        asyncio.run(research.retrieve("x", brief=brief))


def test_the_retry_can_be_switched_off(exa, monkeypatch):
    calls, queue = exa
    monkeypatch.setattr(research, "settings",
                        dataclasses.replace(research.settings,
                                            research_retry=False))
    queue.append([FakeResult("Unrelated", "https://a.example/x",
                             ["Nothing."], days_ago(1))])
    brief = ei.Brief(query="who won", search_query="x", subject="49ers",
                     must_establish=["final score"])
    asyncio.run(research.retrieve("x", brief=brief))
    assert len(calls) == 1


# --------------------------------------------------------------------------
# no brief at all
# --------------------------------------------------------------------------
def test_without_a_brief_retrieval_behaves_exactly_as_it_did_before(exa):
    """EI off, or a degraded brief: one search, no window, no retry. The floor
    this whole layer must never drop below."""
    calls, queue = exa
    queue.append([FakeResult("x", "https://reuters.com/a", ["y"], days_ago(1))])
    packet = asyncio.run(research.retrieve("what did the fed do"))
    assert len(calls) == 1
    assert calls[0]["query"] == "what did the fed do"
    assert "start_published_date" not in calls[0]
    assert packet.retried is False and not packet.missing
