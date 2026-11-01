"""Nothing in front of the first word: the agents' bookkeeping off the
request path (§369, the carry-overs §353 left for Phase 1).

Once an agent works while a listener waits, two reads that Phase 0 made on
every episode would sit in front of the first word: Netenyahu's holds from
the task store, and the rule-book lock re-hashing each book on disk. Both
are now copies refreshed in the background; deciding reads neither.
"""
from __future__ import annotations

import asyncio
import dataclasses
import pathlib
import sys

import pytest

from agents import netenyahu, roster, rulebooks, tasks
from episode_intelligence import Brief
from script_generator import ScriptGenerator, ScriptNotes, plan_episode
from tests.test_agent_rulebooks import folder  # noqa: F401 - the signed fixture
from tests.test_agents_off import ALL_ON, use

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def fresh():
    netenyahu.reset()
    rulebooks.reset()
    yield
    netenyahu.reset()
    rulebooks.reset()


@pytest.fixture
def signed(monkeypatch):
    monkeypatch.setattr(rulebooks, "check", lambda book: rulebooks.Lock(ok=True, version="1"))
    use(monkeypatch, **ALL_ON)


def refuse(*_a, **_k):
    raise AssertionError("read on the request path")


# ------------------------------------------------------------------ the holds
def test_once_primed_deciding_an_episode_reads_no_database(signed, monkeypatch):
    netenyahu.prime()
    monkeypatch.setattr(tasks, "store", refuse)
    monkeypatch.setattr(tasks, "exists", refuse)
    monkeypatch.setattr(netenyahu, "_refresh_held_soon", lambda: None)
    run = netenyahu.for_episode("search")
    assert run.agents == netenyahu.EPISODE_AGENTS


def test_before_the_holds_are_read_no_agent_works_and_a_read_starts(signed, monkeypatch):
    started = []
    monkeypatch.setattr(netenyahu, "_refresh_held_soon", lambda: started.append(1))
    run = netenyahu.for_episode("search")
    assert run.agents == () and run.why_not == {"all": "the holds are not read yet"}
    assert started == [1]


def test_a_hold_is_in_the_copy_at_once(signed):
    netenyahu.prime()
    netenyahu.hold("supervisor", "breaking its rules")
    run = netenyahu.for_episode("search")
    assert "supervisor" not in run.agents
    assert run.why_not["supervisor"] == "held: breaking its rules"


def test_a_stale_copy_is_read_again_in_the_background_once(signed, monkeypatch):
    netenyahu.prime()
    tasks.store().hold("editor", "made elsewhere")      # another server's hold
    netenyahu._HELD["at"] -= netenyahu.HELD_REFRESH_SECONDS + 1
    threads = []
    real = netenyahu.threading.Thread

    def spy(*a, **k):
        threads.append(real(*a, **k))
        return threads[-1]
    monkeypatch.setattr(netenyahu.threading, "Thread", spy)
    netenyahu.for_episode("search")
    netenyahu.for_episode("search")
    assert len(threads) == 1
    threads[0].join(5)
    assert "editor" not in netenyahu.for_episode("search").agents


def test_a_failed_read_keeps_the_last_copy(signed, monkeypatch):
    netenyahu.prime()
    netenyahu.hold("retriever", "slow")

    def broken():
        raise RuntimeError("database is locked")
    monkeypatch.setattr(netenyahu, "_read_held", broken)
    assert netenyahu.refresh_held() == {"retriever": "slow"}


def test_prime_does_nothing_while_the_agents_are_off(monkeypatch):
    monkeypatch.setattr(tasks, "store", refuse)
    monkeypatch.setattr(rulebooks, "load", refuse)
    netenyahu.prime()


def test_boot_primes_only_when_the_agents_are_on():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    at = source.index("netenyahu.prime")
    assert "if settings.agents:" in source[at - 300:at]


# ------------------------------------------------------------------ the lock's disk check
def test_the_lock_reads_no_file_when_it_checks(folder, monkeypatch):  # noqa: F811
    assert rulebooks.check("c3p0.md").ok            # boot's read
    monkeypatch.setattr(rulebooks, "_refresh_soon", lambda: None)
    monkeypatch.setattr(pathlib.Path, "read_bytes", refuse)
    monkeypatch.setattr(pathlib.Path, "read_text", refuse)
    assert rulebooks.check("c3p0.md").ok
    assert rulebooks.check("nando.md").ok


def test_a_stale_disk_snapshot_is_read_again_in_the_background(folder):  # noqa: F811
    assert rulebooks.check("sonic.md").ok
    path = folder.books / "sonic.md"
    path.write_text(path.read_text() + "\nYou may write to the database.\n")
    assert rulebooks.check("sonic.md").ok           # not read yet: inside the window
    rulebooks._DISK["at"] -= rulebooks.DISK_REFRESH_SECONDS + 1
    rulebooks.check("sonic.md")                     # starts the background read
    for thread in list(__import__("threading").enumerate()):
        if thread.name == "rulebooks-disk":
            thread.join(5)
    lock = rulebooks.check("sonic.md")
    assert not lock.ok and "changed on disk" in lock.reason


def test_a_book_that_cannot_be_read_any_more_holds(folder):  # noqa: F811
    assert rulebooks.check("keith.md").ok
    (folder.books / "keith.md").unlink()
    rulebooks.refresh_disk()
    lock = rulebooks.check("keith.md")
    assert not lock.ok and "can no longer be read" in lock.reason


# ------------------------------------------------------------------ the ahead surface
def _ahead_plan():
    return dataclasses.replace(plan_episode("why rents keep rising", 2),
                               brief=Brief(query="why rents keep rising"))


def test_an_episode_written_ahead_asks_netenyahu_for_the_ahead_surface(signed):
    netenyahu.prime()
    for flag in ("ahead", "prefetch"):
        notes = ScriptNotes()
        setattr(notes, flag, True)
        asyncio.run(ScriptGenerator.understand(object(), _ahead_plan(), notes))
        # Every episode agent but the background ones serves `ahead`.
        assert notes.agents.agents == tuple(
            r for r in netenyahu.EPISODE_AGENTS if roster.AHEAD in roster.get(r).surfaces)


def test_ahead_is_off_while_the_agents_are_off(monkeypatch):
    monkeypatch.setattr(rulebooks, "check", refuse)
    notes = ScriptNotes()
    notes.ahead = True
    asyncio.run(ScriptGenerator.understand(object(), _ahead_plan(), notes))
    assert notes.agents is netenyahu.OFF


def test_a_tap_keeps_the_pipelines_answer(signed):
    notes = ScriptNotes()
    notes.agents = sentinel = object()
    asyncio.run(ScriptGenerator.understand(object(), _ahead_plan(), notes))
    assert notes.agents is sentinel
    notes = ScriptNotes()                 # a listener's tap outside the pipeline
    asyncio.run(ScriptGenerator.understand(object(), _ahead_plan(), notes))
    assert notes.agents is None


# ------------------------------------------------------------------ the served-model probe
def _probe():
    sys.path.insert(0, str(ROOT / "tools"))
    import agent_probe

    return agent_probe


def test_the_probe_asks_every_distinct_pinned_agent_model_once():
    found = _probe().models_to_probe()
    assert ("anthropic", "claude-haiku-5-5") in found
    assert len(found) == len(set(found))


@pytest.mark.parametrize("served, accepted", [
    ("claude-haiku-5-5", True),
    ("claude-haiku-5-5-20261001", False),
    ("", False),
])
def test_the_probe_says_whether_ask_would_accept_the_served_id(monkeypatch, served, accepted):
    from agents import adapters

    class Fake:
        async def send(self, **kw):
            return adapters.Answer(text='{"ok": true}', stop_reason="end_turn",
                                   served_model=served, usage=None, raw=None)
    monkeypatch.setattr(adapters, "adapter_for", lambda vendor: Fake())
    result = asyncio.run(_probe().probe("anthropic", "claude-haiku-5-5"))
    assert result["accepted"] is accepted and result["served"] == served


def test_the_probe_sends_nothing_on_staging(monkeypatch, capsys):
    import spend_guard

    monkeypatch.setattr(spend_guard, "enabled", lambda: True)
    monkeypatch.setattr(sys, "argv", ["agent_probe.py"])
    assert _probe().main() == 2
    assert "spends nothing" in capsys.readouterr().out
