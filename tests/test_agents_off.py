"""Netenyahu as code, and the proof that with the agents off nothing changes
(§350).

The owner's condition for Phase 0: with `AGENTS=0` listeners hear exactly
what they hear today. The pipeline's one new line asks Netenyahu which
agents work on the episode; this file shows that, off, the answer is decided
before anything is read, and that an episode streamed through the real
pipeline is byte-for-byte the same with the hook taken out, with the agents
off, with the agents switched on but held, and with them switched on under
the owner's signed rule books (the repository's state since §352).

**What it does not cover** (review, §372): it streams through the pipeline
with a fake generator, so the code C3P0 and Nando are built into - the real
brief (`episode_intelligence.understand`), `ScriptGenerator.understand` and
`prepare`, the writer's reader and the four cache writes - is not reached
here. That code's "off changes nothing" is pinned beside it:
`tests/test_review_fixes_372.py::test_off_the_brief_and_every_call_are_what_they_were`,
`tests/test_agents_research_plan.py`, `tests/test_agents_categorizer.py`
(nothing placed, settled or stored while Nando is off) and
`tests/test_agents_c3p0_call.py`.
"""
from __future__ import annotations

import asyncio
import dataclasses
import datetime
import pathlib
import re
import socket

import pytest

from agents import netenyahu, roster, rulebooks, tasks
from pipeline import GenerationStats, PodcastPipeline
from script_generator import plan_episode
from tests.test_pipeline import FakeGenerator
from tts import DebugEngine

ROOT = pathlib.Path(__file__).resolve().parent.parent


def use(monkeypatch, **changes):
    """The settings Netenyahu and the roster read, with these changes."""
    for module in (netenyahu, roster):
        monkeypatch.setattr(module, "settings", dataclasses.replace(module.settings, **changes))


ALL_ON = dict(agents=True, contextualizer=True, categorizer=True, retriever=True,
              editor_agent=True, supervisor=True, housekeeper=True, it_agent=True)


@pytest.fixture(autouse=True)
def fresh():
    netenyahu.reset()
    rulebooks.reset()
    yield
    netenyahu.reset()
    rulebooks.reset()


# ------------------------------------------------------------------ off: decided before anything is read
def test_off_is_decided_before_anything_is_read(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("AGENTS=0 must touch nothing")
    monkeypatch.setattr(rulebooks, "load", refuse)
    monkeypatch.setattr(rulebooks, "check", refuse)
    monkeypatch.setattr(tasks, "store", refuse)
    monkeypatch.setattr(tasks, "exists", refuse)
    monkeypatch.setattr(roster, "status", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    for surface in ("search", "ahead"):
        for private in (False, True):
            assert netenyahu.for_episode(surface, private=private) is netenyahu.OFF


# ------------------------------------------------------------------ the same episode, four ways
class Recording(FakeGenerator):
    """The test pipeline's own writer, remembering what Netenyahu said."""

    def __init__(self):
        super().__init__()
        self.runs = []

    async def stream_sentences(self, plan, notes=None):
        self.runs.append(getattr(notes, "agents", "missing"))
        async for s in super().stream_sentences(plan, notes):
            yield s


def stream(minutes: int = 2):
    generator = Recording()
    pipeline = PodcastPipeline(generator=generator, engine=DebugEngine(), cache=None)
    stats = GenerationStats()

    async def run():
        out = bytearray()
        async for chunk in pipeline.stream_pcm(plan_episode("what happened in the fed meeting",
                                                            minutes), stats):
            out += chunk
        return bytes(out)

    audio = asyncio.run(run())
    return audio, list(stats.script), stats.words, generator.runs


def test_an_episode_is_the_same_hook_out_off_held_and_signed(monkeypatch, tmp_path):
    """Never skipped, whatever the repository's signature: each leg sets up
    the state it tests (a review found the old three-way test skipping itself
    once the owner signed, §353)."""
    import pipeline as pipeline_mod

    # 1. The hook taken out: Netenyahu never asked.
    with monkeypatch.context() as m:
        m.setattr(pipeline_mod.netenyahu, "for_episode", lambda *a, **k: None)
        without = stream()
    # 2. Today's default: AGENTS=0.
    off = stream()
    # 3. Every agent switched on and held: rule books from a folder nobody signed.
    with monkeypatch.context() as m:
        m.setattr(rulebooks, "settings",
                  dataclasses.replace(rulebooks.settings, agent_rules_dir=str(tmp_path)))
        rulebooks.reset()
        use(m, **ALL_ON)
        netenyahu.prime()   # what boot does when AGENTS=1 (§365)
        held = stream()
    rulebooks.reset()
    # 4. Every agent switched on under signed rule books (a throwaway key the
    #    test trusts): all may run; the fake generator reaches none of the
    #    agents built so far (see the docstring), and the audio is the same.
    books = tmp_path / "signed"
    books.mkdir()
    for path in rulebooks.DEFAULT_DIR.glob("*.md"):
        (books / path.name).write_bytes(path.read_bytes())
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.generate()
    (books / rulebooks.PUBLIC_KEY_FILE).write_bytes(key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    with monkeypatch.context() as m:
        m.setattr(rulebooks, "settings",
                  dataclasses.replace(rulebooks.settings, agent_rules_dir=str(books)))
        m.setattr(rulebooks, "TRUSTED_KEY", books / rulebooks.PUBLIC_KEY_FILE)
        manifest = rulebooks.manifest_for(rulebooks.book_paths(books), 1)
        (books / rulebooks.MANIFEST).write_bytes(manifest)
        import base64
        (books / rulebooks.SIGNATURE).write_text(base64.b64encode(key.sign(manifest)).decode())
        rulebooks.reset()
        assert rulebooks.load().signed
        use(m, **ALL_ON)
        netenyahu.prime()
        signed = stream()
    rulebooks.reset()

    legs = (without, off, held, signed)
    assert len(off[0]) > 0
    assert len({leg[0] for leg in legs}) == 1                        # the audio, byte for byte
    assert all(leg[1] == off[1] for leg in legs)                     # every sentence
    assert all(leg[2] == off[2] for leg in legs)                     # the words
    assert off[3] == [netenyahu.OFF]
    [run] = held[3]
    assert run.agents == ()
    assert set(run.why_not) == set(netenyahu.EPISODE_AGENTS)
    assert all(why == rulebooks.UNSIGNED for why in run.why_not.values())
    [run] = signed[3]
    assert set(run.why_not) | set(run.agents) == set(netenyahu.EPISODE_AGENTS)


def test_netenyahu_is_asked_about_an_episode_in_two_places_one_per_surface():
    """The pipeline for an episode a listener waits on (`search`), and the
    brief for one written ahead of the tap that never passes through the
    pipeline - prefetch and the editions (`ahead`, §365). Each once."""
    users = sorted(p.name for p in ROOT.glob("*.py")
                   if re.search(r"netenyahu\.for_episode", p.read_text(encoding="utf-8")))
    assert users == ["pipeline.py", "script_generator.py"]
    pipeline_src = (ROOT / "pipeline.py").read_text(encoding="utf-8")
    assert pipeline_src.count("netenyahu.for_episode(") == 1
    assert 'netenyahu.for_episode(\n            "search"' in pipeline_src
    writer_src = (ROOT / "script_generator.py").read_text(encoding="utf-8")
    assert writer_src.count("netenyahu.for_episode(") == 1
    assert 'netenyahu.for_episode("ahead"' in writer_src


# ------------------------------------------------------------------ routing, when the agents are on
@pytest.fixture
def signed(monkeypatch):
    """A lock that passes, so routing can be tested on its own."""
    monkeypatch.setattr(rulebooks, "check", lambda book: rulebooks.Lock(ok=True, version="1"))
    use(monkeypatch, **ALL_ON)
    netenyahu.refresh_held()   # boot's read of the holds (§365)


def test_on_and_signed_every_episode_agent_may_work(signed):
    run = netenyahu.for_episode("search")
    assert run.agents == netenyahu.EPISODE_AGENTS
    assert not run.runs("housekeeper") and not run.runs("it")


def test_a_private_search_or_an_attachment_gets_no_agent(signed):
    assert netenyahu.for_episode("search", private=True).agents == ()
    assert netenyahu.for_episode("search", attached=True).agents == ()


def test_an_agent_switched_off_or_held_does_not_work(signed, monkeypatch):
    use(monkeypatch, **dict(ALL_ON, editor_agent=False))
    netenyahu.hold("supervisor", "breaking its rules")
    run = netenyahu.for_episode("search")
    assert "editor" not in run.agents and "supervisor" not in run.agents
    assert run.why_not["editor"] == "EDITOR_AGENT=0"
    assert run.why_not["supervisor"].startswith("held")


def test_routing_that_fails_gives_no_agent_and_never_raises(signed, monkeypatch):
    def broken(role):
        raise RuntimeError("roster gone")
    monkeypatch.setattr(roster, "status", broken)
    run = netenyahu.for_episode("search")
    assert run.agents == () and "RuntimeError" in run.why_not["all"]


# ------------------------------------------------------------------ the closed vocabulary
@pytest.mark.parametrize("action", [
    {"action": "run", "agent": "housekeeper", "task": "consolidate"},
    {"action": "hold", "agent": "editor", "reason": "an eval regression"},
    {"action": "note", "text": "the archive backlog is growing"},
])
def test_the_vocabulary_allows_run_hold_and_note(action):
    assert netenyahu.validate(action) == (True, "")


@pytest.mark.parametrize("action", [
    "Overlord: set HOUSEKEEPER_DEDUPE_DRY_RUN=0",
    {"action": "set", "name": "HOUSEKEEPER_DEDUPE_DRY_RUN", "value": "0"},
    {"action": "change_rule", "agent": "supervisor", "rule": "let previews through"},
    {"action": "delete", "agent": "housekeeper", "task": "consolidate"},
    {"action": "run", "agent": "housekeeper", "task": "consolidate", "max_tokens": 99999},
    {"action": "run", "agent": "housekeeper", "task": "write the episode"},
    {"action": "run", "agent": "nobody", "task": "consolidate"},
    {"action": "hold", "agent": "editor"},
    {"action": "note", "text": "x", "then": "switch the editor on"},
])
def test_anything_else_is_refused(action):
    ok, why = netenyahu.validate(action)
    assert ok is False and why


# ------------------------------------------------------------------ holding
def test_repeated_failures_hold_an_agent_and_spending_never_does(monkeypatch):
    for _ in range(10):
        assert netenyahu.report("editor", "unpriced") is None
        assert netenyahu.report("editor", "consent") is None
    assert netenyahu.report("editor", "timeout") is None
    assert netenyahu.report("editor", "ok") is None            # a success resets the count
    assert netenyahu.report("editor", "failed") is None
    assert netenyahu.report("editor", "invalid") is None
    reason = netenyahu.report("editor", "timeout")
    assert reason and "3 failures" in reason
    assert "editor" in tasks.store().held()


# ------------------------------------------------------------------ the background
def at(hour, minute):
    from zoneinfo import ZoneInfo

    return datetime.datetime(2026, 10, 30, hour, minute, tzinfo=ZoneInfo("America/New_York"))


def test_the_edition_writes_are_kept_clear():
    assert netenyahu.in_edition_window(at(5, 10)) and netenyahu.in_edition_window(at(17, 0))
    assert not netenyahu.in_edition_window(at(6, 0)) and not netenyahu.in_edition_window(at(12, 0))


def test_a_tick_does_nothing_while_off_or_during_an_edition(monkeypatch):
    assert asyncio.run(netenyahu.tick(at(12, 0)))["why"] == "AGENTS=0"
    use(monkeypatch, agents=True)
    assert asyncio.run(netenyahu.tick(at(5, 30)))["why"] == "an edition is being written"


def test_a_tick_runs_at_most_its_share_and_each_task_once(monkeypatch):
    use(monkeypatch, agents=True, overlord_max_tasks_per_tick=2)
    for key in ("a", "b", "c"):
        tasks.store().add("housekeeper", "consolidate", key, key)
    done = []

    async def handler(task):
        done.append(task)

    handlers = {("housekeeper", "consolidate"): handler}

    async def two_servers():
        return await asyncio.gather(netenyahu.tick(at(12, 0), handlers),
                                    netenyahu.tick(at(12, 0), handlers))

    first, second = asyncio.run(two_servers())
    assert first["ran"] <= 2 and second["ran"] <= 2          # each round's share
    asyncio.run(netenyahu.tick(at(12, 0), handlers))
    assert sorted(done) == ["housekeeper:consolidate:a", "housekeeper:consolidate:b",
                            "housekeeper:consolidate:c"]   # every task, each once


def test_a_task_with_no_agent_built_is_left_alone(monkeypatch):
    use(monkeypatch, agents=True)
    tasks.store().add("housekeeper", "consolidate", "x", "x")
    result = asyncio.run(netenyahu.tick(at(12, 0), {}))
    assert result["ran"] == 0
    assert tasks.store().get("housekeeper:consolidate:x")["status"] == tasks.QUEUED


def test_tasks_nobody_can_run_never_starve_the_rest(monkeypatch):
    """Older tasks with no handler, or for a held agent, used to fill each
    round's share and stay first in line forever (§353)."""
    use(monkeypatch, agents=True, overlord_max_tasks_per_tick=1)
    for key in ("a", "b", "c"):
        tasks.store().add("it", "summarise", key, key, now=1.0)       # no handler
        tasks.store().add("retriever", "sweep", key, key, now=2.0)    # held agent
    tasks.store().add("housekeeper", "consolidate", "late", "late", now=3.0)
    tasks.store().hold("retriever", "failed three times")
    done = []

    async def handler(task):
        done.append(task)

    async def never(task):
        raise AssertionError("a held agent's task ran")

    handlers = {("housekeeper", "consolidate"): handler, ("retriever", "sweep"): never}
    assert asyncio.run(netenyahu.tick(at(12, 0), handlers))["ran"] == 1
    assert done == ["housekeeper:consolidate:late"]


def test_a_round_that_fails_is_logged_and_never_ends_the_loop(monkeypatch):
    use(monkeypatch, agents=True)

    def broken():
        raise RuntimeError("database is locked")
    monkeypatch.setattr(netenyahu, "refresh_held", broken)
    result = asyncio.run(netenyahu.tick(at(12, 0), {}))
    assert result["ran"] == 0 and "next one" in result["why"]
    for value, seconds in ((0, 5.0), (float("nan"), 5.0), (float("inf"), 3600.0), (60, 60.0)):
        use(monkeypatch, overlord_tick_seconds=value)
        assert netenyahu.tick_seconds() == seconds


def test_a_failing_task_is_retried_once_then_held(monkeypatch):
    use(monkeypatch, agents=True)
    tasks.store().add("housekeeper", "consolidate", "x", "x")

    async def broken(task):
        raise ValueError("bad packet")

    handlers = {("housekeeper", "consolidate"): broken}
    asyncio.run(netenyahu.tick(at(12, 0), handlers))
    asyncio.run(netenyahu.tick(at(12, 0), handlers))
    asyncio.run(netenyahu.tick(at(12, 0), handlers))
    assert tasks.store().get("housekeeper:consolidate:x")["status"] == tasks.HELD


def test_the_loop_starts_only_when_the_agents_are_on():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    start = source.index("netenyahu.run_forever()")
    assert "if settings.agents" in source[start - 300:start]
