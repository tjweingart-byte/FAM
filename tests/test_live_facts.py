"""The seam for facts an article index cannot be fresh enough to hold.

Exa retrieves *writing about* the world. A game ends and the scoreboard knows
instantly; the recap saying so is written, published and indexed later, and in
between a search returns the preview. An episode built on that describes a
finished match as though it is still coming - which is not a writing bug, the
evidence really did say that.

Nothing is configured here yet, and the tests that matter most are about that:
an absent capability must be *named* rather than silently missing, and must
never be able to stop an episode that is otherwise answerable.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import episode_intelligence as ei  # noqa: E402
import live_facts  # noqa: E402

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def registry(monkeypatch):
    """A clean registry, so a test's source cannot leak into the next test."""
    original = list(live_facts._SOURCES)
    monkeypatch.setattr(live_facts, "_SOURCES", list(original))
    return live_facts._SOURCES


class Scoreboard(live_facts.LiveSource):
    name = "test scoreboard"
    domain = "sports"

    def __init__(self, facts=None, ready=True, explode=False):
        self._facts, self._ready, self._explode = facts, ready, explode

    def diagnose(self):
        return (True, "ready") if self._ready else (False, "no credential")

    async def fetch(self, brief):
        if self._explode:
            raise RuntimeError("the provider fell over")
        return self._facts


def facts(**kw):
    return live_facts.LiveFacts(
        domain=kw.get("domain", "sports"),
        source=kw.get("source", "test scoreboard"),
        as_of=kw.get("as_of", NOW - timedelta(minutes=4)),
        facts=kw.get("facts", ["The 49ers beat the Rams 24-17."]),
        status=kw.get("status", "completed"))


# --------------------------------------------------------------------------
# routing
# --------------------------------------------------------------------------
def test_a_question_that_turns_on_a_score_reaches_the_sports_source(registry):
    live_facts.register(Scoreboard(facts()))
    got = asyncio.run(live_facts.lookup(ei.Brief(query="who won",
                                                 live_domain="sports")))
    assert got and "24-17" in got.facts[0]


def test_a_question_that_turns_on_nothing_live_asks_nobody(registry):
    live_facts.register(Scoreboard(facts()))
    assert asyncio.run(live_facts.lookup(
        ei.Brief(query="how does a heat pump work", live_domain=""))) is None


def test_a_source_is_never_consulted_outside_its_own_domain(registry):
    live_facts.register(Scoreboard(facts()))
    assert asyncio.run(live_facts.lookup(
        ei.Brief(query="the nasdaq", live_domain="markets"))) is None


def test_registering_a_source_for_a_domain_that_does_not_exist_is_refused():
    """It would never be consulted, so it would look registered and do
    nothing - the shape of absence this module exists to prevent."""
    class Wrong(live_facts.LiveSource):
        name, domain = "wrong", "weather"

    with pytest.raises(ValueError):
        live_facts.register(Wrong())


def test_a_registered_source_is_tried_before_the_placeholder(registry):
    live_facts.register(Scoreboard(facts()))
    assert live_facts.sources_for("sports")[0].name == "test scoreboard"


# --------------------------------------------------------------------------
# it cannot stop an episode
# --------------------------------------------------------------------------
def test_a_source_that_fails_is_skipped_rather_than_ending_the_episode(registry):
    """A live-fact provider improves an episode that is already answerable. It
    must never be able to prevent one."""
    live_facts.register(Scoreboard(explode=True))
    assert asyncio.run(live_facts.lookup(
        ei.Brief(query="who won", live_domain="sports"))) is None


def test_a_source_that_cannot_serve_is_passed_over_for_one_that_can(registry):
    live_facts.register(Scoreboard(facts()))
    live_facts.register(Scoreboard(ready=False))
    got = asyncio.run(live_facts.lookup(ei.Brief(query="who won",
                                                 live_domain="sports")))
    assert got is not None


def test_nothing_configured_means_nothing_returned_and_no_pretending(registry):
    """The state FAM actually ships in. It must answer "no", not "here is an
    empty scoreboard"."""
    assert asyncio.run(live_facts.lookup(
        ei.Brief(query="who won", live_domain="sports"))) is None


# --------------------------------------------------------------------------
# the absence is named
# --------------------------------------------------------------------------
def test_health_names_every_domain_with_no_provider_and_what_it_needs():
    """A capability that is absent and says so can be fixed; one that is absent
    and quiet gets shipped. A scoreboard question answered from an article
    index is wrong in a way nobody sees until a listener hears it."""
    report = live_facts.report()
    assert set(report["domains"]) == set(live_facts.LIVE_DOMAINS)
    assert report["ready"] == [], "a source claims to be ready that is not"
    for domain in live_facts.LIVE_DOMAINS:
        entries = report["sources"][domain]
        assert entries, f"{domain} is not even named as missing"
        assert all(not e["ready"] for e in entries)
        assert all("Needs:" in e["detail"] for e in entries), (
            "an unconfigured source says it cannot serve without saying what "
            "it would take to make it serve")


# --------------------------------------------------------------------------
# what the writer is told
# --------------------------------------------------------------------------
def test_a_live_fact_says_when_it_was_true():
    """The entire reason this module exists. The listener is told "last night"
    or "two days ago" and the difference has to come from a timestamp."""
    block = facts().as_prompt_block(NOW)
    assert "4 minutes ago" in block
    assert "24-17" in block


def test_a_live_fact_outranks_the_evidence_packet_and_says_so():
    """It reports a state; the packet reports articles about that state. Where
    the two disagree there is no contest, and the writer has to be told which
    way round it is."""
    block = facts().as_prompt_block(NOW)
    assert "outranks everything else" in block


def test_event_status_travels_with_the_fact():
    """The single most valuable field here: it is what stops a future event
    being narrated in the past tense."""
    assert "Status: not started." in facts(status="not started").as_prompt_block(NOW)


def test_a_source_with_nothing_to_say_produces_no_block():
    assert live_facts.LiveFacts("sports", "s", NOW, []).as_prompt_block(NOW) == ""


@pytest.mark.parametrize("delta, phrase", [
    (timedelta(seconds=30), "just now"),
    (timedelta(minutes=30), "30 minutes ago"),
    (timedelta(hours=5), "5 hours ago"),
    (timedelta(days=3), "3 days ago"),
])
def test_age_is_phrased_the_way_the_episode_would_say_it(delta, phrase):
    assert facts(as_of=NOW - delta).age_phrase(NOW) == phrase
