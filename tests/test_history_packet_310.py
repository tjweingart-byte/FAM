"""The second packet: what came before, from the history store (§310).

The seam the MongoDB store plugs into. Pinned here, because each is a way a
second packet could cost an episode something:

* no store, a store that is off, slow, failing or empty: the episode is
  exactly what it was before the store existed, and nothing raises;
* a listener waits no more than `HISTORY_GRACE_SECONDS` for it once the
  evidence is in; nobody waiting, it gets its whole ceiling;
* undated entries are dropped, and entries inside the brief's window are
  dropped when the search found evidence for that stretch;
* more minutes buy more entries, under a character ceiling;
* the writer reads dates computed in code and never a URL or a hostname;
* the query carries the category path and never a listener;
* a remote store is not asked on staging.

No key, no network, no database: the store is a stub.
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
import history  # noqa: E402
import provenance  # noqa: E402
import script_generator as sg  # noqa: E402

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
PACKET = "SOURCE 1\nTitle: t\nKey evidence:\nThe Eagles won on Sunday.\n"


def _entry(days_ago, title="t", text=None, **kw):
    return history.HistoryEntry(
        title=title, text=text or f"Something {days_ago} days ago.",
        when=None if days_ago is None else NOW - timedelta(days=days_ago), **kw)


def _brief(**kw):
    base = dict(query="eagles", subject="the Philadelphia Eagles",
                live_entity="Philadelphia Eagles", recency_days=7,
                category="NFL teams",
                category_path=["sports", "american football", "nfl",
                               "philadelphia eagles"])
    base.update(kw)
    return ei.Brief(**base)


def _plan(minutes=1, evidence="", **kw):
    plan = sg.plan_episode("how are the eagles doing", minutes)
    return dataclasses.replace(plan, brief=_brief(**kw), evidence=evidence)


@pytest.fixture(autouse=True)
def _no_store():
    history.unregister()
    yield
    history.unregister()


def _store(entries=(), delay=0.0, raises=None, remote=False, seen=None):
    async def fetch(query):
        if seen is not None:
            seen.append(query)
        if delay:
            await asyncio.sleep(delay)
        if raises is not None:
            raise raises
        return list(entries)

    history.register(fetch, "test store", remote=remote)


# -- asking ------------------------------------------------------------------
def test_no_store_is_a_no_op():
    packet = asyncio.run(history.lookup(_plan()))
    assert packet.outcome == "no_source" and not packet
    assert history.consolidate(packet, _plan()) is None
    assert "<background>" not in sg.build_prompt(_plan())


def test_off_is_off(monkeypatch):
    _store([_entry(30)])
    monkeypatch.setattr(history, "settings", dataclasses.replace(
        config.settings, history_packet=False))
    assert asyncio.run(history.lookup(_plan())).outcome == "off"


def test_failures_become_outcomes_never_exceptions(monkeypatch):
    _store(raises=RuntimeError("mongo down"))
    assert asyncio.run(history.lookup(_plan())).outcome == "failed"
    monkeypatch.setattr(history, "settings", dataclasses.replace(
        config.settings, history_timeout_seconds=0.01))
    _store([_entry(30)], delay=0.5)
    assert asyncio.run(history.lookup(_plan())).outcome == "timeout"
    _store([])
    assert asyncio.run(history.lookup(_plan())).outcome == "empty"


def test_a_remote_store_is_not_asked_on_staging(monkeypatch):
    seen = []
    _store([_entry(30)], remote=True, seen=seen)
    import spend_guard

    monkeypatch.setattr(spend_guard, "enabled", lambda: True)
    assert asyncio.run(history.lookup(_plan())).outcome == "staging"
    assert seen == []


def test_the_query_is_the_brief_contextualised_and_never_personal():
    seen = []
    _store([_entry(30)], seen=seen)
    asyncio.run(history.lookup(_plan(minutes=2)))
    query = seen[0]
    assert query.category == "sports"
    assert query.subcategory == "philadelphia eagles"
    assert query.entities == ("Philadelphia Eagles",)
    assert query.limit == history.limit_for(2)
    fields = {f.name for f in dataclasses.fields(history.HistoryQuery)}
    assert not fields & {"user", "user_id", "listener", "listener_id"}


def test_a_degraded_brief_does_not_ask():
    seen = []
    _store([_entry(30)], seen=seen)
    plan = dataclasses.replace(_plan(), brief=ei.fallback_brief("x", "test"))
    assert asyncio.run(history.lookup(plan)).outcome == "off" and seen == []


# -- consolidating -----------------------------------------------------------
def test_undated_and_empty_entries_are_dropped():
    packet = history.HistoryPacket("found", [_entry(None), _entry(40, text=" "),
                                             _entry(40)])
    kept = history.consolidate(packet, _plan(), now=NOW)
    assert len(kept.entries) == 1
    assert kept.dropped == {"undated": 1, "empty": 1}


def test_the_fresh_packet_speaks_for_its_own_window():
    packet = history.HistoryPacket("found", [_entry(2), _entry(30)])
    with_evidence = history.consolidate(packet, _plan(evidence=PACKET), now=NOW)
    assert [e.when for e in with_evidence.entries] == [NOW - timedelta(days=30)]
    assert with_evidence.dropped == {"inside_window": 1}
    # With nothing found, the store's last week is all there is of it.
    without = history.consolidate(packet, _plan(), now=NOW)
    assert len(without.entries) == 2
    # An evergreen question has no window to speak for.
    evergreen = history.consolidate(
        packet, _plan(evidence=PACKET, recency_days=0), now=NOW)
    assert len(evergreen.entries) == 2


def test_newest_first_more_minutes_buy_more_and_a_ceiling_holds(monkeypatch):
    entries = [_entry(d, title=f"d{d}") for d in range(10, 40)]
    one = history.consolidate(history.HistoryPacket("found", entries), _plan(1),
                              now=NOW)
    five = history.consolidate(history.HistoryPacket("found", entries), _plan(5),
                               now=NOW)
    assert one.entries[0].title == "d10"
    assert len(one.entries) == history.limit_for(1) < len(five.entries)
    assert len(five.entries) <= config.settings.history_max_entries
    monkeypatch.setattr(history, "settings", dataclasses.replace(
        config.settings, history_max_chars=40))
    tight = history.consolidate(history.HistoryPacket("found", entries),
                                _plan(5), now=NOW)
    assert len(tight.entries) == 1 and tight.dropped["over_budget"] >= 1


def test_repeats_are_dropped():
    packet = history.HistoryPacket("found", [
        _entry(30, text="Same."), _entry(31, text="Same."),
        _entry(32, ref="a"), _entry(33, ref="a")])
    kept = history.consolidate(packet, _plan(5), now=NOW)
    assert len(kept.entries) == 2 and kept.dropped == {"repeat": 2}


# -- what the writer reads ---------------------------------------------------
def test_the_block_dates_in_code_and_never_names_where_it_came_from():
    entry = _entry(400, title="Last season's collapse",
                   text="The Eagles lost five straight in December.",
                   kind="episode", ref="https://example.com/x")
    packet = history.HistoryPacket("found", [entry], source="mongo")
    block = history.as_prompt_block(packet, now=NOW)
    assert "<background>" in block and "RECORD 1" in block
    assert entry.when.strftime("%d %B %Y") in block
    assert history.KINDS["episode"] in block
    assert "example.com" not in block and "mongo" not in block
    assert history.as_prompt_block(None) == ""


def test_the_archive_is_credited_as_background():
    packet = history.HistoryPacket("found", [_entry(30)], source="FAM archive")
    credit = history.attribution(packet)
    assert credit.kind == provenance.ARCHIVE in provenance.KINDS
    assert "background" in credit.tier
    assert history.attribution(None) is None


# -- in the pipeline ---------------------------------------------------------
class _Gen(sg.ScriptGenerator):
    def __init__(self):
        pass

    async def understand(self, plan, notes=None):
        return dataclasses.replace(plan, brief=_brief())

    async def live_lookup(self, plan, notes=None):
        return plan

    async def forecast_lookup(self, plan, notes=None):
        return types.SimpleNamespace(forecast=None)

    async def players_lookup(self, plan, notes=None):
        return plan

    async def research(self, plan, notes=None):
        return dataclasses.replace(plan, evidence=PACKET)


def _prepare(notes=None):
    plan = dataclasses.replace(sg.plan_episode("how are the eagles doing", 1),
                               search=True)
    return asyncio.run(_Gen().prepare(plan, notes))


def test_prepare_hands_the_writer_both_packets():
    _store([_entry(60, title="Last year", text="They won the Super Bowl.")])
    notes = sg.ScriptNotes()
    plan = _prepare(notes)
    assert plan.history is not None and len(plan.history.entries) == 1
    prompt = sg.build_prompt(plan)
    assert prompt.index("<evidence>") < prompt.index("<background>")
    assert "They won the Super Bowl." in prompt
    assert notes.research["history"]["kept"] == 1
    assert any(i.kind == provenance.ARCHIVE for i in notes.provenance.items)


def test_a_slow_store_never_holds_the_first_word(monkeypatch):
    monkeypatch.setattr(sg, "settings", dataclasses.replace(
        config.settings, history_grace_seconds=0.05))
    _store([_entry(60)], delay=2.0)
    import time

    started = time.monotonic()
    plan = _prepare()
    assert time.monotonic() - started < 1.0
    assert plan.history is None
    assert "<background>" not in sg.build_prompt(plan)


def test_no_store_leaves_the_prompt_as_it_was():
    plan = _prepare()
    assert plan.history is None
    assert sg.build_prompt(plan) == sg.build_prompt(
        dataclasses.replace(plan, history=None))
    assert "<background>" not in sg.build_prompt(plan)


# -- contextualising ---------------------------------------------------------
def test_place_category_walks_the_tree_broadest_first(monkeypatch):
    import stories
    import topics

    tree = types.SimpleNamespace(
        ancestors=lambda node: ["nfl", "american football", "sports"])
    monkeypatch.setattr(stories, "resolve_category", lambda text: (
        "philadelphia eagles" if "Eagles" in text else ""))
    monkeypatch.setattr(topics, "category_tree", lambda: tree)
    brief = ei.Brief(query="eagles", subject="the Philadelphia Eagles",
                     category="NFL team")
    placed = ei.place_category(brief)
    assert placed.category_path == ["sports", "american football", "nfl",
                                    "philadelphia eagles"]
    assert placed.subcategory == "philadelphia eagles"
    assert brief.category_path == []  # a copy: a warmed brief is shared


def test_place_category_never_raises(monkeypatch):
    import stories

    def boom(text):
        raise RuntimeError("no tree")

    monkeypatch.setattr(stories, "resolve_category", boom)
    brief = ei.Brief(query="x", category="y")
    assert ei.place_category(brief) is brief
    assert ei.place_category(ei.fallback_brief("x", "t")).category_path == []


def test_a_figure_from_a_dated_record_is_material():
    import figure_guard

    plan = dataclasses.replace(_plan(), history=history.HistoryPacket(
        "found", [_entry(400, text="The shares fell 31.5 percent in 2022.")]))
    assert "31.5 percent" in figure_guard._material(plan)
