"""Tiingo's credit, spoken and shown, whenever its data went in (§552).

At the owner's direction: an episode that used Tiingo's market data ends
"Market data powered by Tiingo." - the very last sentence, composed in code,
never by the writer - and Tiingo is listed in the player's sources, among
the three marks the corner shows. Both are read from one record (the
episode's provenance), so the voice and the panel cannot disagree.
"""
from __future__ import annotations

import asyncio
import dataclasses
from datetime import datetime, timezone

import live_facts
import provenance as P
import script_generator as sg
from script_generator import ScriptNotes, plan_episode

from tests.test_edition_batch_179 import SCRIPT, message
from tests.test_editor_tandem_456 import FIRST, EVIDENCE, generator


def _tiingo_lookup():
    facts = live_facts.LiveFacts(domain="markets", source="Tiingo",
                                 as_of=datetime.now(timezone.utc))
    return live_facts.LiveLookup(domain="markets", outcome=live_facts.FACTS,
                                 facts=facts)


def _notes(*labels, kind=P.LIVE):
    notes = ScriptNotes()
    notes.provenance = P.Provenance()
    for label in labels:
        notes.provenance.add(P.Attribution(label=label, kind=kind))
    return notes


def test_the_credit_is_owed_only_when_tiingo_is_on_the_record():
    assert P.credits_tiingo(_notes("Tiingo").provenance)
    # Recalled from FAM's archive, credited by provider name (§313).
    assert P.credits_tiingo(_notes("Tiingo").provenance.as_dict())
    assert not P.credits_tiingo(_notes("BALLDONTLIE", "reuters.com").provenance)
    assert not P.credits_tiingo(P.Provenance())
    assert not P.credits_tiingo(None)


def test_a_failed_tiingo_lookup_is_not_credited():
    plan = plan_episode("how is apple stock doing", 1.5, search=False)
    failed = live_facts.LiveLookup(domain="markets",
                                   outcome=live_facts.PROVIDER_FAILED)
    plan = dataclasses.replace(plan, live=failed)
    assert sg.market_credit(plan, None) == ""
    assert sg.market_credit(dataclasses.replace(plan, live=_tiingo_lookup()), None) \
        == P.TIINGO_CREDIT


def test_a_streamed_episode_ends_with_the_credit():
    gen, _asked, _finals = generator(texts=(FIRST,))
    plan = dataclasses.replace(plan_episode("how are markets doing", 1.5),
                               search=False, evidence=EVIDENCE)

    async def go(notes):
        return [s async for s in gen.stream_prepared(plan, notes)]

    out = asyncio.run(go(_notes("Tiingo", "reuters.com")))
    assert out[-1] == "Market data powered by Tiingo."
    assert out.count(P.TIINGO_CREDIT) == 1


def test_no_tiingo_no_credit():
    gen, _asked, _finals = generator(texts=(FIRST,))
    plan = dataclasses.replace(plan_episode("what did the fed decide", 1.5),
                               search=False, evidence=EVIDENCE)
    out = asyncio.run(_collect(gen, plan, _notes("reuters.com", kind=P.ARTICLE)))
    assert P.TIINGO_CREDIT not in out


async def _collect(gen, plan, notes):
    return [s async for s in gen.stream_prepared(plan, notes)]


def test_a_batched_episode_ends_with_the_credit_too():
    plan = plan_episode("did the fed hold rates", 2, search=False)
    gen = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    out = gen.sentences_from_message(plan, _notes("Tiingo"), message(SCRIPT))
    assert out[-1] == P.TIINGO_CREDIT and len(out) == 4
    plain = gen.sentences_from_message(plan, ScriptNotes(), message(SCRIPT))
    assert P.TIINGO_CREDIT not in plain


def test_tiingo_leads_the_sources_so_the_corner_shows_it():
    prov = P.Provenance()
    for host in ("reuters.com", "apnews.com", "bloomberg.com", "ft.com"):
        prov.add(P.Attribution(label=host))
    prov.add(P.Attribution(label="Tiingo", kind=P.LIVE, tier="live feed"))
    served = P.Provenance.from_json(prov.to_json()).as_dict()
    assert served["items"][0]["label"] == "Tiingo"
    # Everything else keeps the order it was found in.
    assert [i["label"] for i in served["items"][1:]] == \
        ["reuters.com", "apnews.com", "bloomberg.com", "ft.com"]
