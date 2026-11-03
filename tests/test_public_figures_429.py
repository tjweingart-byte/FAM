"""Public figures only (decided item 16; c3p0.md never 6; §429).

When C3P0 says a request is about a private person, the reference look asks
Wikidata for the name before anything is searched; a name Wikidata does not
know is refused after the search unless the reporting names them. A look
that did not answer establishes nothing. With C3P0 off nothing changes.
Built on stubs: this container cannot reach Wikimedia.
"""
from __future__ import annotations

import asyncio
import dataclasses

import pytest

import episode_intelligence as ei
import research
import script_generator as sg
import wikimedia
from agents import contextualizer


class Runs:
    def __init__(self, *roles):
        self.roles = set(roles)

    def runs(self, role):
        return role in self.roles


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    contextualizer.reset()
    wikimedia.reset_store()
    monkeypatch.setattr(wikimedia, "ENABLED_OVERRIDE", True)
    wikimedia._STORE[0] = wikimedia.ReferenceStore(str(tmp_path / "w.db"))
    yield
    contextualizer.reset()
    wikimedia.reset_store()


def wikidata(monkeypatch, hits, fail=False):
    asked = []

    def fake(params):
        asked.append(params)
        if fail:
            raise wikimedia.WikimediaError("unreachable")
        return {"search": hits}
    monkeypatch.setattr(wikimedia, "_wikidata", fake)
    return asked


def notes_for(*roles):
    notes = sg.ScriptNotes()
    notes.agents = Runs(*roles)
    return notes


def plan_about(name, private=True):
    plan = sg.plan_episode(f"what is {name} up to", 2, search=True)
    brief = ei.Brief(query=plan.query, subject=name,
                     contextualized={"entity": name, "private_person": private})
    return dataclasses.replace(plan, brief=brief)


# ------------------------------------------------------------------ the look
def test_an_exact_label_or_alias_is_known(monkeypatch):
    wikidata(monkeypatch, [{"id": "Q1", "label": "Jane Doe"}])
    assert wikimedia.known("Jane Doe") == "known"
    wikimedia.reset_store()
    wikidata(monkeypatch, [{"id": "Q2", "label": "Janet Doe", "match": {"text": "jane doe"}}])
    assert wikimedia.known("Jane  Doe") == "known"


def test_a_near_name_is_not_known(monkeypatch):
    wikidata(monkeypatch, [{"id": "Q1", "label": "Jane Doe-Smith"}])
    assert wikimedia.known("Jane Doe") == "unknown"


def test_a_look_that_fails_or_is_off_is_unchecked(monkeypatch):
    wikidata(monkeypatch, [], fail=True)
    assert wikimedia.known("Jane Doe") == "unchecked"
    monkeypatch.setattr(wikimedia, "ENABLED_OVERRIDE", False)
    assert wikimedia.known("Jane Doe") == "unchecked"


def test_the_answer_is_kept_so_a_second_ask_sends_nothing(monkeypatch):
    asked = wikidata(monkeypatch, [])
    assert wikimedia.known("Dave Smith") == "unknown"
    assert wikimedia.known("dave smith") == "unknown"
    assert len(asked) == 1


# ------------------------------------------------------------------ the gate
def test_known_to_wikidata_goes_ahead(monkeypatch):
    wikidata(monkeypatch, [{"id": "Q1", "label": "Jane Doe"}])
    notes = notes_for("contextualizer")
    plan = plan_about("Jane Doe")
    asyncio.run(sg._public_look(plan, notes))
    assert notes.public_check == "known"
    sg._refuse_private(dataclasses.replace(plan, evidence=""), notes)    # no raise


def test_unknown_and_not_in_the_reporting_is_refused(monkeypatch):
    wikidata(monkeypatch, [])
    notes = notes_for("contextualizer")
    plan = plan_about("Dave Smith")
    asyncio.run(sg._public_look(plan, notes))
    with pytest.raises(sg.PrivatePerson) as refused:
        sg._refuse_private(dataclasses.replace(
            plan, evidence="SOURCE 1\nA story about someone else entirely."), notes)
    assert isinstance(refused.value, research.NoEvidence)    # every path declines it
    assert "Dave Smith" not in str(refused.value)            # names nobody
    assert contextualizer.report()["since_boot"]["private_refused"] == 1


def test_unknown_but_named_in_the_reporting_goes_ahead(monkeypatch):
    """The owner (2026-11-03): reporting that names them in a public role -
    a business owner quoted in the news - makes them public."""
    wikidata(monkeypatch, [])
    notes = notes_for("contextualizer")
    plan = plan_about("Dave Smith")
    asyncio.run(sg._public_look(plan, notes))
    sg._refuse_private(dataclasses.replace(
        plan, evidence="SOURCE 1\nBakery owner Dave Smith told the council..."), notes)
    assert contextualizer.report()["since_boot"]["private_named_in_reporting"] == 1


def test_a_look_that_did_not_answer_never_refuses(monkeypatch):
    wikidata(monkeypatch, [], fail=True)
    notes = notes_for("contextualizer")
    plan = plan_about("Dave Smith")
    asyncio.run(sg._public_look(plan, notes))
    assert notes.public_check == "unchecked"
    sg._refuse_private(dataclasses.replace(plan, evidence=""), notes)


@pytest.mark.parametrize("roles,private", [((), True), (("contextualizer",), False)])
def test_with_c3p0_off_or_not_private_nothing_is_asked(monkeypatch, roles, private):
    asked = wikidata(monkeypatch, [])
    notes = notes_for(*roles)
    asyncio.run(sg._public_look(plan_about("Dave Smith", private), notes))
    assert asked == [] and notes.public_check == ""
    sg._refuse_private(plan_about("Dave Smith"), notes)


def test_the_gate_sits_before_the_search_and_the_refusal_after_it():
    src = open(sg.__file__, encoding="utf-8").read()
    i = src.index("async def prepare(")
    look = src.index("await _public_look(plan, notes)", i)
    route = src.index("live_route.apply(routed, plan.query)", i)
    refuse = src.index("_refuse_private(plan, notes)", i)
    ready = src.index('_mark(notes, "evidence_ready")', i)
    assert look < route and refuse > ready
