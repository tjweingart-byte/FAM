"""Hermes, the outreach agent (§576): `/admin/groups`' reader, as a full agent.

Pinned here: his call goes through `agents/models.py` and nowhere else; he
waits for the owner's signature without holding any other agent; he never
reads about a person, and code sends back an answer that names or reaches
one; whenever he cannot answer, the card is composed in code and says why.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import communities as C  # noqa: E402
import config  # noqa: E402
import metering  # noqa: E402
from agents import models, names, roster, rulebooks  # noqa: E402

PROPOSAL = ROOT / "docs" / "agent_rule_proposals" / "hermes.md"
RULES = ROOT / "docs" / "agent_rules"


def lead(**kw):
    base = dict(platform="facebook", ident="eaglesfans", name="Eagles Fans Nation",
                url="u", contact_url="c", subject="Philadelphia Eagles",
                description=("Game threads and trade talk. 48K members. Admin: Maria Lopez. "
                             "Questions to hello@eaglesfans.org or @birdmod, or call 215-555-0134."),
                members=48000, admin_hint=["Maria Lopez"])
    base.update(kw)
    return C.Lead(**base)


def reading(**kw):
    base = dict(fit=4, fit_why="A busy fan group.", unsure=False,
                message="Hi, I run FAM. Eagles Fans Nation looks like a fit. Would you like early access?",
                pitches=[C.Pitch(title=f"Idea {w}", angle="a", why="w") for w in "abc"])
    base.update(kw)
    return C.Reading(**base)


def answers(*values, status="ok"):
    """A fake `models.ask`: each call returns the next value, recording the
    request it was given."""
    seen = []

    async def ask(request):
        seen.append(request)
        value = values[min(len(seen) - 1, len(values) - 1)]
        return SimpleNamespace(ok=status == "ok", status=status, reason="why",
                               value=value, cost_usd=0.002, model="claude-haiku-5-5")
    return ask, seen


# ------------------------------------------------------------- the call

def test_his_answer_is_used_and_said_to_be_his():
    ask, seen = answers(reading())
    out, dollars = asyncio.run(C.read(lead(), ask))
    assert out.fit == 4 and [p["title"] for p in out.pitches] == ["Idea a", "Idea b", "Idea c"]
    assert out.drafted_by.startswith("Hermes") and dollars == 0.002
    req = seen[0]
    assert (req.agent, req.switch_setting, req.model_setting, req.rule_book) == \
        ("outreach", "outreach_agent", "outreach_model", "hermes.md")
    assert req.surface == "background" and req.timeout > 0 and req.max_tokens > 0


def test_the_material_never_reaches_a_person():
    material = C.material(lead())
    text = " ".join(t for _tag, t in material)
    for leaked in ("Maria", "Lopez", "hello@eaglesfans.org", "@birdmod", "555"):
        assert leaked not in text, leaked
    assert C.PERSON in text and C.ADDRESS in text
    # The community, not a listener: never the consent-checked `request` tag.
    assert {tag for tag, _ in material} <= {"evidence", "archive"}


def test_an_answer_naming_a_person_is_sent_back_once_then_code():
    bad = reading(message="Hi Maria Lopez, would you like early access?")
    ask, seen = answers(bad, reading())
    out, _ = asyncio.run(C.read(lead(), ask))
    assert len(seen) == 2 and out.drafted_by.startswith("Hermes")
    ask, seen = answers(bad, bad)
    out, _ = asyncio.run(C.read(lead(), ask))
    assert len(seen) == 2 and out.drafted_by == "code"
    assert "sent back twice" in out.fit_why and "name a person" in out.fit_why


def test_a_way_to_reach_someone_or_a_stray_figure_is_sent_back():
    l = lead()
    assert "reach a person" in C.refused(l, reading(message="Email me at a@b.com?"))
    assert "reach a person" in C.refused(l, reading(fit_why="Ring 215-555-0134"))
    assert "material does not" in C.refused(l, reading(message="Your 90,000 members?"))
    # 48K is on the page: saying it is no invention.
    assert C.refused(l, reading(message="All 48 of your threads?")) == ""
    assert "pitches" in C.refused(l, reading(pitches=[]))


def test_unsure_or_unanswered_is_composed_in_code_and_says_why():
    ask, _ = answers(reading(unsure=True))
    out, _ = asyncio.run(C.read(lead(), ask))
    assert out.drafted_by == "code" and "unsure" in out.fit_why
    ask, _ = answers(None, status="held")
    out, _ = asyncio.run(C.read(lead(), ask))
    assert out.drafted_by == "code" and "held" in out.fit_why
    assert len(out.pitches) == C.PITCHES and out.message


def test_today_the_layer_refuses_him_and_the_card_is_codes():
    """Unsigned, he is not in `agents.names.AGENTS`: the real layer refuses
    the call before anything leaves the machine."""
    out, dollars = asyncio.run(C.read(lead()))
    assert out.drafted_by == "code" and dollars == 0.0
    assert "malformed" in out.fit_why and "not an agent" in out.fit_why


def test_nothing_but_the_model_layer_calls_a_model():
    src = (ROOT / "communities.py").read_text()
    for word in ("anthropic_client", "messages.create", "ANTHROPIC_API_KEY"):
        assert word not in src, word


# ------------------------------------------------------------- waiting to be signed

def test_he_waits_without_holding_any_other_agent():
    assert "outreach" in names.PROPOSED and "outreach" not in names.AGENTS
    assert "outreach" in roster.PROPOSED and "outreach" not in roster.ROSTER
    # His book is not in the signed folder: there it would hold every agent.
    assert not (RULES / "hermes.md").exists()
    assert PROPOSAL.exists()


def test_the_pending_manifest_adds_his_book_and_changes_nothing_else():
    signed = json.loads((RULES / rulebooks.MANIFEST).read_text())
    pending = json.loads((RULES / rulebooks.PENDING).read_text())
    assert pending["version"] == signed["version"] + 1
    assert set(pending["books"]) - set(signed["books"]) == {"hermes.md"}
    for book, sha in signed["books"].items():
        assert pending["books"][book] == sha, book
    assert pending["books"]["hermes.md"] == rulebooks.sha256(PROPOSAL.read_bytes())
    # And it is exactly what the lock will compute once the book is in place.
    paths = rulebooks.book_paths(RULES)
    paths["hermes.md"] = PROPOSAL
    assert rulebooks.manifest_for(paths, pending["version"]) == \
        (RULES / rulebooks.PENDING).read_bytes()


def test_his_book_reads_like_every_other():
    text = PROPOSAL.read_text()
    assert "governing copy outside Claude" in text.split("\n", 4)[2]
    assert "## You must always" in text and "## You must never" in text
    assert "every-agent.md" in text


def test_once_registered_his_request_is_his_own_and_his_settings_are_sound(monkeypatch):
    monkeypatch.setattr(names, "AGENTS", {**names.AGENTS, **names.PROPOSED})
    assert models._own_settings(C.request(lead())) == ""
    assert models._malformed(C.request(lead())) == ""
    assert config.settings.outreach_agent is False
    spec, why = models.parse_model(config.settings.outreach_model)
    assert spec is not None and not why and spec.price_key in metering.PRICES
    agent = roster.PROPOSED["outreach"]
    timeout, size = agent.ceiling(roster.BACKGROUND)
    assert timeout > 0 and size > 0
    assert agent.surfaces == (roster.BACKGROUND,) and not agent.sees_listener_words
