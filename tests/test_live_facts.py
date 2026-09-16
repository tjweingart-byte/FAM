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
    """A two-step source: resolve to an entity, then fetch its state.

    `calls` counts *fetches that actually went out*, which is how the entity
    cache is tested - a shared result must not be a second provider request.
    """

    name = "test scoreboard"
    domain = "sports"
    cost_per_call = 0.004

    def __init__(self, facts=None, ready=True, explode=False, resolves=True,
                 hang=False, name=None, delayed_seconds=0.0):
        self._facts, self._ready, self._explode = facts, ready, explode
        self._resolves, self._hang = resolves, hang
        self.delayed_seconds = delayed_seconds
        self.calls = 0
        self.resolutions = 0
        if name:
            self.name = name

    def diagnose(self):
        return (True, "ready") if self._ready else (False, "no credential")

    async def resolve(self, brief):
        self.resolutions += 1
        if not self._resolves:
            return None
        return live_facts.Entity(domain="sports", provider=self.name,
                                 id="game-1", label="49ers at Rams")

    async def fetch(self, entity):
        self.calls += 1
        if self._hang:
            await asyncio.sleep(5)
        if self._explode:
            raise RuntimeError("the provider fell over")
        return self._facts


def facts(**kw):
    return live_facts.LiveFacts(
        domain=kw.get("domain", "sports"),
        source=kw.get("source", "test scoreboard"),
        as_of=kw.get("as_of", NOW - timedelta(seconds=20)),
        facts=kw.get("facts", ["The 49ers lead the Rams 24-17."]),
        status=kw.get("status", live_facts.IN_PROGRESS),
        delayed_seconds=kw.get("delayed_seconds", 0.0),
        kind=kw.get("kind", ""))


def fresh(**kw):
    """Facts stamped *now*, so freshness rules do not reject them by age."""
    kw.setdefault("as_of", datetime.now(timezone.utc))
    return facts(**kw)


@pytest.fixture(autouse=True)
def clean_caches():
    """Entity and fact caches are process-global; a leak between tests would
    make one test's provider call satisfy the next one's assertion."""
    live_facts.clear_caches()
    yield
    live_facts.clear_caches()


# --------------------------------------------------------------------------
# routing
# --------------------------------------------------------------------------
def test_a_question_that_turns_on_a_score_reaches_the_sports_source(registry):
    live_facts.register(Scoreboard(fresh()))
    got = asyncio.run(live_facts.lookup(ei.Brief(query="who won",
                                                 live_domain="sports")))
    assert got.outcome == live_facts.FACTS
    assert "24-17" in got.facts.facts[0]


def test_a_question_that_turns_on_nothing_live_asks_nobody(registry):
    """`None` means only one thing now: the question does not turn on a live
    state. Every other case has to come back saying what happened."""
    live_facts.register(Scoreboard(fresh()))
    assert asyncio.run(live_facts.lookup(
        ei.Brief(query="how does a heat pump work", live_domain=""))) is None


def test_a_source_is_never_consulted_outside_its_own_domain(registry):
    live_facts.register(Scoreboard(fresh()))
    got = asyncio.run(live_facts.lookup(
        ei.Brief(query="the nasdaq", live_domain="markets")))
    assert got.outcome == live_facts.NOT_CONFIGURED


def test_registering_a_source_for_a_domain_that_does_not_exist_is_refused():
    """It would never be consulted, so it would look registered and do
    nothing - the shape of absence this module exists to prevent."""
    class Wrong(live_facts.LiveSource):
        name, domain = "wrong", "weather"

    with pytest.raises(ValueError):
        live_facts.register(Wrong())


def test_a_registered_source_is_tried_before_the_placeholder(registry):
    live_facts.register(Scoreboard(fresh()))
    assert live_facts.sources_for("sports")[0].name == "test scoreboard"


# --------------------------------------------------------------------------
# every way it comes up empty is a different way
# --------------------------------------------------------------------------
def test_a_source_that_fails_is_reported_as_a_failure_not_as_an_absence(registry):
    """A provider that broke says nothing whatever about the world, and the
    writer has to be told which of the two it is looking at. Collapsing them
    into `None` is what let an episode treat our silence as the event's."""
    live_facts.register(Scoreboard(fresh(), explode=True))
    got = asyncio.run(live_facts.lookup(
        ei.Brief(query="who won", live_domain="sports")))
    assert got.outcome == live_facts.PROVIDER_FAILED
    assert "fell over" in got.detail
    assert got.status == live_facts.UNKNOWN


def test_a_provider_with_no_matching_entity_is_not_a_provider_that_failed(registry):
    live_facts.register(Scoreboard(fresh(), resolves=False))
    got = asyncio.run(live_facts.lookup(
        ei.Brief(query="who won", live_domain="sports")))
    assert got.outcome == live_facts.NO_ENTITY


def test_a_provider_that_knows_it_and_says_nothing_is_its_own_outcome(registry):
    live_facts.register(Scoreboard(None))
    got = asyncio.run(live_facts.lookup(
        ei.Brief(query="who won", live_domain="sports")))
    assert got.outcome == live_facts.NO_FACTS


def test_a_source_that_cannot_serve_is_passed_over_for_one_that_can(registry):
    live_facts.register(Scoreboard(fresh()))
    live_facts.register(Scoreboard(fresh(), ready=False, name="unconfigured one"))
    got = asyncio.run(live_facts.lookup(ei.Brief(query="who won",
                                                 live_domain="sports")))
    assert got.outcome == live_facts.FACTS


def test_nothing_configured_says_so_and_never_says_there_is_no_such_thing(registry):
    """The state FAM actually ships in, and the sentence that matters: "we have
    no provider" is not "the world has no score"."""
    got = asyncio.run(live_facts.lookup(
        ei.Brief(query="who won", live_domain="sports")))
    assert got.outcome == live_facts.NOT_CONFIGURED
    assert got.facts is None
    assert got.status == live_facts.UNKNOWN


def test_a_failure_outranks_a_missing_provider_in_what_gets_reported(registry):
    """Two ways of having nothing, and one of them is a fault we can fix. The
    more informative one is what reaches the writer and the log."""
    live_facts.register(Scoreboard(fresh(), explode=True))
    got = asyncio.run(live_facts.lookup(
        ei.Brief(query="who won", live_domain="sports")))
    assert got.outcome == live_facts.PROVIDER_FAILED
    assert any(a[1] == live_facts.NOT_CONFIGURED for a in got.attempts), (
        "the placeholder was consulted and its verdict should be on the record")


# --------------------------------------------------------------------------
# freshness is a constraint, not a label
# --------------------------------------------------------------------------
def test_data_too_old_for_its_domain_is_withheld_rather_than_relabelled(registry):
    """Stale live data is more dangerous than none: it arrives with a
    timestamp and outranks the packet. It is refused, not annotated."""
    old = facts(as_of=datetime.now(timezone.utc) - timedelta(minutes=30))
    live_facts.register(Scoreboard(old))
    got = asyncio.run(live_facts.lookup(
        ei.Brief(query="who won", live_domain="sports")))
    assert got.outcome == live_facts.STALE
    assert got.facts is None, "stale facts must not reach the writer"
    assert got.status == live_facts.UNKNOWN


def test_each_domain_has_its_own_clock():
    """A score is stale in two minutes; a vote count published in hourly
    batches is not. One universal threshold would be wrong for two of three."""
    assert (live_facts.MAX_AGE_SECONDS["sports"]
            < live_facts.MAX_AGE_SECONDS["markets"]
            < live_facts.MAX_AGE_SECONDS["elections"])
    ten_minutes = datetime.now(timezone.utc) - timedelta(minutes=10)
    assert not facts(domain="sports", as_of=ten_minutes).is_fresh()
    assert facts(domain="elections", as_of=ten_minutes).is_fresh()


def test_a_delayed_feed_is_never_described_as_current():
    """Fifteen-minute-delayed market data presented as "now" is a lie with a
    timestamp attached, which is the most convincing kind."""
    block = fresh(delayed_seconds=900).as_prompt_block()
    assert "DELAYED" in block
    assert "never call it the current one" in block


# --------------------------------------------------------------------------
# timeouts and isolation
# --------------------------------------------------------------------------
def test_a_provider_that_hangs_costs_a_bounded_slice_and_not_the_episode(
        registry, monkeypatch):
    import dataclasses as dc
    monkeypatch.setattr(live_facts, "settings", dc.replace(
        live_facts.settings, live_timeout_seconds=0.05,
        live_total_timeout_seconds=0.2))
    live_facts.register(Scoreboard(fresh(), hang=True))
    got = asyncio.run(live_facts.lookup(
        ei.Brief(query="who won", live_domain="sports")))
    assert got.outcome == live_facts.TIMEOUT


def test_one_provider_failing_does_not_stop_the_next_one(registry):
    good = Scoreboard(fresh())
    live_facts.register(good)
    live_facts.register(Scoreboard(fresh(), explode=True, name="broken one"))
    got = asyncio.run(live_facts.lookup(
        ei.Brief(query="who won", live_domain="sports")))
    assert got.outcome == live_facts.FACTS
    assert good.calls == 1


# --------------------------------------------------------------------------
# the status vocabulary is closed
# --------------------------------------------------------------------------
@pytest.mark.parametrize("given, expected", [
    ("in_progress", live_facts.IN_PROGRESS),
    ("In Progress", live_facts.IN_PROGRESS),
    ("in-progress", live_facts.IN_PROGRESS),
    ("final", live_facts.FINAL),
    ("scheduled", live_facts.SCHEDULED),
    ("", live_facts.UNKNOWN),
    ("1H", live_facts.UNKNOWN),
    ("completed", live_facts.UNKNOWN),
    (None, live_facts.UNKNOWN),
])
def test_a_status_is_mapped_at_the_boundary_or_becomes_unknown(given, expected):
    """A provider's own vocabulary is its own business, and must be mapped
    where it arrives. An unmapped string silently missing every comparison
    downstream is the failure this closes - and `unknown` is not "probably
    fine", it is the state in which no result may be spoken."""
    assert live_facts.normalise_status(given) == expected
    assert facts(status=given).status == expected


def test_entity_level_caching_shares_one_fetch_between_worded_questions(registry):
    """Three people asking the same thing three ways is one game, so it must
    be one provider request - cost per event, not cost per listener."""
    source = Scoreboard(fresh())
    live_facts.register(source)
    for query in ("how are the chiefs doing",
                  "what's happening in the chiefs game",
                  "chiefs score"):
        got = asyncio.run(live_facts.lookup(
            ei.Brief(query=query, subject="the Chiefs game",
                     live_domain="sports")))
        assert got.outcome == live_facts.FACTS
    assert source.calls == 1, "one entity, one fetch"


def test_an_unknown_status_is_never_cached(registry):
    """Caching a state nobody established would make one listener's ignorance
    into everybody's for the next ten seconds."""
    source = Scoreboard(fresh(status="something else"))
    live_facts.register(source)
    for _ in range(2):
        asyncio.run(live_facts.lookup(
            ei.Brief(query="who won", subject="the Chiefs game",
                     live_domain="sports")))
    assert source.calls == 2


def test_a_cache_hit_is_not_metered_as_a_provider_request(registry):
    """§73's rule: spend is recorded at the moment it is spent. Counting a
    cache hit would report a cost that was never incurred."""
    from script_generator import ScriptNotes

    live_facts.register(Scoreboard(fresh()))
    notes = ScriptNotes()
    for _ in range(3):
        asyncio.run(live_facts.lookup(
            ei.Brief(query="who won", subject="the Chiefs game",
                     live_domain="sports"), notes))
    assert notes.usage.live_calls == 1
    assert notes.usage.live_cost == pytest.approx(0.004)


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
    assert "elections" in report["domains"], (
        "elections is a live domain with its own kinds of claim, and must be "
        "named as missing rather than quietly absent")
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
    block = facts(as_of=NOW - timedelta(minutes=4)).as_prompt_block(NOW)
    assert "4 minutes ago" in block
    assert "24-17" in block


def test_a_live_fact_outranks_the_evidence_packet_and_says_so():
    """It reports a state; the packet reports articles about that state. Where
    the two disagree there is no contest, and the writer has to be told which
    way round it is."""
    assert "most authoritative thing" in facts().as_prompt_block(NOW)


@pytest.mark.parametrize("status, must_say", [
    (live_facts.IN_PROGRESS, "UNDER WAY"),
    (live_facts.FINAL, "FINISHED"),
    (live_facts.SCHEDULED, "NOT STARTED"),
    (live_facts.UNKNOWN, "you do not know that it has"),
])
def test_each_status_carries_its_rule_rather_than_just_its_name(status, must_say):
    """The word alone was left to be interpreted, and "in progress" got read as
    a recap anyway. What the writer may *do* with each state is spelled out."""
    assert must_say in facts(status=status).as_prompt_block(NOW)


def test_an_event_under_way_is_told_it_has_no_result():
    block = facts(status=live_facts.IN_PROGRESS).as_prompt_block(NOW)
    assert "no result, no winner and no final score" in block
    assert "Do not project how it ends" in block


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


# --------------------------------------------------------------------------
# every outcome reaches the writer
# --------------------------------------------------------------------------
@pytest.mark.parametrize("outcome", [o for o in live_facts.OUTCOMES
                                     if o != live_facts.FACTS])
def test_every_empty_outcome_tells_the_writer_what_it_does_not_know(outcome):
    """An absence the writer is not told about is an absence the writer fills
    in. That is the whole of §88, so none of these may render blank."""
    block = live_facts.LiveLookup("sports", outcome).as_prompt_block()
    assert block.strip(), f"{outcome} renders nothing"
    assert "Not knowing is not the same as nothing having happened" in block
    flat = " ".join(block.split())
    assert "do not tell the listener that nothing has been reported" in flat


def test_a_failed_lookup_never_reports_an_event_status():
    """The cache and the story shape both switch on this, so a lookup that
    established nothing must say `unknown` rather than inherit a status from
    facts it did not get."""
    for outcome in live_facts.OUTCOMES:
        if outcome == live_facts.FACTS:
            continue
        assert live_facts.LiveLookup("sports", outcome).status == live_facts.UNKNOWN


# --------------------------------------------------------------------------
# elections: four kinds of claim, and only two of them are results
# --------------------------------------------------------------------------
def test_elections_is_its_own_domain_rather_than_sports_with_a_new_word():
    """A vote count is not a scoreline. It has its own clock - counts are
    published in batches an hour apart, where a score changes every few
    seconds - and its own kinds of claim."""
    assert "elections" in live_facts.LIVE_DOMAINS
    assert (live_facts.MAX_AGE_SECONDS["elections"]
            > live_facts.MAX_AGE_SECONDS["sports"])


def test_a_prediction_market_price_is_never_rendered_as_a_result():
    """The distinction this domain exists to keep. A market price is what
    people are betting; a reported count is what was counted; a certified
    result is what stands. Collapsing the first into the last would be
    inventing an outcome out of an opinion."""
    odds = live_facts.LiveFacts(
        domain="elections", source="a prediction market",
        as_of=datetime.now(timezone.utc),
        facts=["The contract is trading at sixty-two cents."],
        status=live_facts.IN_PROGRESS, kind="prediction-market")
    block = odds.as_prompt_block()
    assert "These are prediction-market figures." in block
    # And the status rule still forbids a result, which is what stops a price
    # being read out as a winner.
    assert "no result, no winner" in block
