"""Which live provider each written episode was routed to (§264).

An episode with no scoreboard had one of four causes - not routed, not routed
because EI degraded, routed with no answer, or answered - and only
`live_facts.lookup` knew which. These pin that each is recorded as itself,
that a not-routed live-looking question is flagged without a request, and that
`prepare` records one row and never waits on the disk to do it.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
import time
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import admin_tracker  # noqa: E402
import live_facts  # noqa: E402
import live_routing as LR  # noqa: E402
import live_sources  # noqa: E402
import script_generator as sg  # noqa: E402


def brief(**overrides):
    base = dict(subject="", query="", live_domain="", degraded=False,
                notes=[], intent="", outcome_dependent=False, place="")
    base.update(overrides)
    return types.SimpleNamespace(**base)


def lookup(domain, outcome, attempts=()):
    return live_facts.LiveLookup(domain, outcome, detail="d",
                                 attempts=list(attempts))


def test_each_cause_is_its_own_verdict():
    answered = LR.row_for("chiefs score", brief(live_domain="sports"),
                          lookup("sports", live_facts.FACTS,
                                 [("API-Sports", "facts", "API-Sports")]))
    assert LR.verdict(answered["domain"], answered["outcome"],
                      answered["degraded"]) == LR.ANSWERED
    assert answered["answered_by"] == "API-Sports"

    missed = LR.row_for("chiefs score", brief(live_domain="sports"),
                        lookup("sports", live_facts.NO_ENTITY,
                               [("API-Sports", "no_entity", "nothing matching")]))
    assert LR.verdict(missed["domain"], missed["outcome"],
                      missed["degraded"]) == LR.NO_ANSWER

    plain = LR.row_for("how do tides work", brief(), None)
    assert LR.verdict(plain["domain"], plain["outcome"],
                      plain["degraded"]) == LR.NOT_ROUTED

    fell = LR.row_for("chiefs score",
                      brief(degraded=True, notes=["timed out"]), None)
    assert LR.verdict(fell["domain"], fell["outcome"],
                      fell["degraded"]) == LR.DEGRADED
    assert fell["degraded_why"] == "timed out"


def test_no_brief_at_all_reads_as_degraded():
    row = LR.row_for("anything", None, None)
    assert LR.verdict(row["domain"], row["outcome"], row["degraded"]) == LR.DEGRADED


def test_the_local_ladders_weather_counts_as_answered():
    """`prepare` puts the local ladder's weather in `plan.live` when the
    brief named no domain; that is a weather answer, not 'not routed'."""
    row = LR.row_for("what's on in san anselmo", brief(),
                     lookup("weather", live_facts.FACTS))
    assert row["domain"] == "weather"
    assert LR.verdict(row["domain"], row["outcome"], row["degraded"]) == LR.ANSWERED


def test_a_routed_brief_with_no_lookup_says_so():
    row = LR.row_for("chiefs score", brief(live_domain="sports"), None)
    assert row["outcome"] == "not_asked"
    assert LR.verdict(row["domain"], row["outcome"], row["degraded"]) == LR.NO_ANSWER


def test_suspect_reads_a_held_catalogue_and_spends_nothing(monkeypatch):
    monkeypatch.setitem(live_sources.TEAMS, "american-football",
                        (time.time(), [{"name": "Kansas City Chiefs",
                                        "city": "Kansas City"}]))

    def no_requests(*a, **k):
        raise AssertionError("a suspect check must never make a request")

    monkeypatch.setattr(live_sources, "_json", no_requests)
    row = LR.row_for("how are the kansas city chiefs looking", brief(), None)
    assert row["suspect"].startswith("sports: names Kansas City Chiefs")
    # By full name only (§265): a nickname alone is too often a word.
    assert LR.row_for("how are the chiefs looking", brief(), None)["suspect"] == ""


def test_suspect_flags_a_named_slot_and_market_words():
    assert LR.suspect("sunday night football recap").startswith("sports:")
    assert LR.suspect("why are nvidia shares falling").startswith("markets:")
    assert LR.suspect("how do tides work") == ""


def test_a_routed_row_is_never_suspect():
    row = LR.row_for("sunday night football recap", brief(live_domain="sports"),
                     lookup("sports", live_facts.TIMEOUT))
    assert row["suspect"] == ""


def test_record_flush_report_round_trip():
    LR.record("chiefs score", brief(live_domain="sports"),
              lookup("sports", live_facts.TIMEOUT,
                     [("API-Sports", "timeout", "API-Sports timed out")]))
    LR.record("how do tides work", brief(), None)
    LR.record("nvidia shares", brief(), None)
    LR.flush()
    report = LR.report()
    assert report["episodes"] == 3
    assert report["totals"][LR.NO_ANSWER] == 1
    assert report["totals"][LR.NOT_ROUTED] == 2
    assert report["domains"] == {"sports": {"routed": 1, "answered": 0}}
    assert report["no_answer_reasons"][0]["why"] == \
        "sports - timeout: API-Sports timed out"
    assert report["suspects"] == 1
    assert report["recent"][0]["query"] == "nvidia shares"


def test_rows_past_the_keep_window_are_deleted(tmp_path):
    store = LR.RoutingStore(str(tmp_path / "r.db"))
    old = LR.row_for("old", brief(), None)
    old["at"] = time.time() - (LR.KEEP_DAYS + 1) * LR.DAY
    store.add([old])
    store.add([LR.row_for("new", brief(), None)])
    assert [r["query"] for r in store.since(0)] == ["new"]


def test_report_never_creates_the_store():
    assert not LR.exists()
    assert LR.report()["episodes"] == 0
    assert not LR.exists()


def test_record_never_raises(monkeypatch):
    monkeypatch.setattr(LR, "row_for", lambda *a: 1 / 0)
    LR.record("q", None, None)


def test_prepare_records_one_row(monkeypatch):
    async def research(self, plan, notes=None):
        return dataclasses.replace(plan, evidence="SOURCE 1\nTitle: x")

    async def understand(self, plan, notes=None):
        return plan

    async def asked(b, notes=None, **_):
        return lookup("sports", live_facts.NO_ENTITY,
                      [("API-Sports", "no_entity", "nothing matching")])

    monkeypatch.setattr(live_facts, "lookup", asked)
    monkeypatch.setattr(sg.ScriptGenerator, "research", research)
    monkeypatch.setattr(sg.ScriptGenerator, "understand", understand)
    plan = dataclasses.replace(sg.plan_episode("chiefs score", 3),
                               brief=brief(subject="Chiefs", live_domain="sports"))
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    asyncio.run(generator.prepare(plan, sg.ScriptNotes()))
    report = LR.report()
    assert report["episodes"] == 1
    assert report["recent"][0]["verdict"] == LR.NO_ANSWER
    assert report["recent"][0]["attempts"][0][:2] == ["API-Sports", "no_entity"]


def test_the_admin_snapshot_carries_routing():
    LR.record("how do tides work", brief(), None)
    assert admin_tracker.snapshot()["routing"]["episodes"] == 1
