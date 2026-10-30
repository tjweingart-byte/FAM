"""The roster, the typed hand-offs and the task table (§344).

The roster is what Netenyahu knows about every agent; the hand-offs are the
only shapes agents pass along; the task table is how background work runs
once across two servers, survives a restart and stops when it repeats with
nothing new.
"""
from __future__ import annotations

import pathlib
import re
import threading

import pytest

from agents import handoffs, names, roster, tasks
from config import settings

ROOT = pathlib.Path(__file__).resolve().parent.parent
GUIDE = (ROOT / "docs" / "FAM_AGENTS.md").read_text(encoding="utf-8")


# ------------------------------------------------------------------ the roster
def test_the_roster_is_the_eight_agents_the_guide_names():
    assert set(roster.ROSTER) == set(names.AGENTS)
    for role, agent in roster.ROSTER.items():
        assert f"**{agent.name}**" in GUIDE or agent.name in GUIDE, role
        assert (ROOT / "docs" / "agent_rules" / agent.rule_book).exists(), role


def test_every_stage_is_one_the_guide_draws():
    drawn = set(re.findall(r"<!-- stages: ([^|>]+)", GUIDE)[0].split())
    for agent in roster.ROSTER.values():
        assert agent.stage in drawn, (agent.role, agent.stage)


def test_every_ceiling_names_a_real_setting_with_a_positive_value():
    for agent in roster.ROSTER.values():
        assert agent.ceilings, agent.role
        assert agent.calls_per_task == 1 and agent.retries == 1, agent.role
        for surface, timeout, size in agent.ceilings:
            assert surface in agent.surfaces, (agent.role, surface)
            if timeout:
                assert getattr(settings, timeout) > 0, (agent.role, timeout)
                assert size and getattr(settings, size) > 0, (agent.role, size)
            seconds, tokens = agent.ceiling(surface)
            assert (seconds is None) == (not timeout)


def test_the_brief_partners_share_the_briefs_own_ceilings():
    """C3P0 and Nando work inside the brief's call (§341): C3P0's ceiling is
    the brief's, never a second clock beside it."""
    c3p0 = roster.get("contextualizer")
    assert c3p0.ceiling("search") == (settings.ei_timeout_seconds, settings.ei_max_tokens)
    assert c3p0.ceiling("ahead") == (settings.ei_ahead_timeout_seconds, settings.ei_max_tokens)


def test_code_only_agents_make_no_model_call_where_none_is_decided():
    assert roster.get("retriever").ceiling("search") == (None, None)
    assert roster.get("overlord").ceiling("background") == (None, None)
    assert settings.retriever_model == "" and settings.overlord_planner_model == ""


def test_agents_that_never_see_a_listener_are_background_only():
    for agent in roster.ROSTER.values():
        if not agent.sees_listener_words and agent.role != "overlord":
            assert agent.surfaces == (roster.BACKGROUND,), agent.role


def test_every_agent_is_off_and_held_today():
    for role in roster.ROSTER:
        state = roster.status(role)
        assert state["on"] is False and state["may_run"] is False
        assert state["why_not"] == "AGENTS=0"


def test_no_name_reaches_a_listener():
    """The names are internal (docs/FAM_AGENTS.md): nothing a listener sees
    may carry one."""
    surfaces = [ROOT / "static" / "index.html", ROOT / "static" / "app.js",
                ROOT / "static" / "listen.html", ROOT / "static" / "waitlist.html"]
    for agent in roster.ROSTER.values():
        for path in surfaces:
            if path.exists():
                assert agent.name not in path.read_text(encoding="utf-8"), (agent.name, path)


# ------------------------------------------------------------------ hand-offs
def test_every_hand_off_forbids_extra_fields_and_has_a_version():
    for name, model in handoffs.HANDOFFS.items():
        assert model.model_config.get("extra") == "forbid", name
        assert model.model_config.get("frozen") is True, name
        assert "schema_version" in model.model_fields, name
    for name in handoffs.CHAIN:
        assert name in handoffs.HANDOFFS


def test_a_hand_off_that_is_not_whole_is_refused_never_raised():
    plan, why = handoffs.validate("ResearchPlan", {"entity": "Eagles", "ambiguous": True,
                                                   "readings": ["the team", "the bird"]})
    assert plan is not None and not why and plan.ambiguous
    for kind, data in [
        ("ResearchPlan", {"entity": "Eagles", "spoken_result": "24-17"}),   # extra field
        ("Verdict", {"sentence": 3, "result": "fail"}),                     # no failure named
        ("Verdict", {"sentence": 3, "result": "pass", "failure": "advice"}),
        ("Verdict", {"sentence": 3, "result": "fail", "failure": "too dull"}),  # not a named failure
        ("ArchiveRequest", {"node_id": "nfl", "lookup": "write_a_query"}),  # not a lookup
        ("SentenceRef", {"packet": "memory", "source": 0, "sentence": 0}),  # not a packet
        ("Nonsense", {}),
        ("ResearchPlan", "not even an object"),
    ]:
        value, why = handoffs.validate(kind, data)
        assert value is None and why, (kind, data)


def test_an_archive_packet_is_history_in_date_order():
    source = {"url": "https://x.example/a", "sentences": ["One."]}
    item = lambda date: {"date": date, "node_id": "nfl", "source": source, "kind": "result"}
    ok, _ = handoffs.validate("ArchivePacket", {"items": [item("2024-01-01"), item("2025-01-01")]})
    assert ok is not None
    bad, why = handoffs.validate("ArchivePacket", {"items": [item("2025-01-01"), item("2024-01-01")]})
    assert bad is None and why


def test_evidence_is_only_ever_a_pointer_to_a_sources_sentence():
    """Every agent: never pass on your own wording as evidence. The outline
    has no field a model could write a fact into but a beat's label."""
    outline = handoffs.Outline.model_fields
    assert set(outline) == {"schema_version", "summary", "beats", "opening", "landing",
                            "thin", "missing"}
    assert handoffs.Beat.model_fields["purpose"].metadata  # bounded


def test_the_named_failures_are_mclovins():
    book = " ".join((ROOT / "docs" / "agent_rules" / "mclovin.md").read_text().split())
    assert "quote" in book and "website named" in book
    assert len(handoffs.FAILURES) == len(set(handoffs.FAILURES)) == 12


def test_the_lookups_are_sonics_closed_list():
    assert handoffs.LOOKUPS == ("background", "result_between", "value_on_day", "series",
                                "by_meaning", "corroboration")


# ------------------------------------------------------------------ the task table
@pytest.fixture
def store(tmp_path):
    return tasks.TaskStore(str(tmp_path / "agents.db"))


def test_a_task_is_claimed_by_exactly_one_of_two_servers(tmp_path):
    path = str(tmp_path / "agents.db")
    one, two = tasks.TaskStore(path), tasks.TaskStore(path)   # two servers, one store
    assert one.add("housekeeper", "consolidate", "ep1", {"packet": 1}) == tasks.ADDED
    won = []
    def race(s):
        won.append(s.claim("housekeeper:consolidate:ep1", now=100.0))
    threads = [threading.Thread(target=race, args=(s,)) for s in (one, two, one, two)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert won.count(True) == 1


def test_a_live_claim_is_never_taken_a_crashed_one_is(store):
    store.add("housekeeper", "dedupe", "nightly", {"day": "2026-10-30"})
    task = "housekeeper:dedupe:nightly"
    assert store.claim(task, now=1000.0)
    assert not store.claim(task, now=1000.0 + tasks.STALE_SECONDS - 1)
    store.touch(task, now=1000.0 + tasks.STALE_SECONDS - 1)
    assert not store.claim(task, now=1000.0 + tasks.STALE_SECONDS + 5)
    assert store.claim(task, now=1000.0 + 2 * tasks.STALE_SECONDS + 5)


def test_one_retry_then_held(store):
    store.add("housekeeper", "consolidate", "ep2", "x")
    task = "housekeeper:consolidate:ep2"
    assert store.claim(task, now=1.0)
    assert store.fail(task, "timeout", now=2.0) == tasks.FAILED
    assert store.claim(task, now=3.0)              # the one retry
    assert store.fail(task, "timeout again", now=4.0) == tasks.HELD
    assert not store.claim(task, now=5.0)
    assert store.get(task)["status"] == tasks.HELD
    assert store.add("housekeeper", "consolidate", "ep2", "y") == tasks.NOTHING_NEW


def test_the_same_task_with_nothing_new_is_a_loop_and_is_refused(store):
    task = "housekeeper:consolidate:ep3"
    assert store.add("housekeeper", "consolidate", "ep3", {"packet": "A"}) == tasks.ADDED
    assert store.add("housekeeper", "consolidate", "ep3", {"packet": "A"}) == tasks.ALREADY
    assert store.claim(task, now=1.0)
    store.finish(task, "12 records", now=2.0)
    assert store.add("housekeeper", "consolidate", "ep3", {"packet": "A"}) == tasks.NOTHING_NEW
    assert store.add("housekeeper", "consolidate", "ep3", {"packet": "B"}) == tasks.ADDED


def test_due_lists_only_what_may_be_claimed_oldest_first(store):
    store.add("housekeeper", "consolidate", "a", 1, now=10.0)
    store.add("housekeeper", "consolidate", "b", 1, now=20.0)
    store.claim("housekeeper:consolidate:a", now=30.0)
    assert store.due(10, now=31.0) == ["housekeeper:consolidate:b"]
    assert store.due(1, now=31.0 + tasks.STALE_SECONDS) == ["housekeeper:consolidate:a"]


def test_a_task_survives_a_restart(tmp_path):
    path = str(tmp_path / "agents.db")
    tasks.TaskStore(path).add("housekeeper", "comb", "2026-10", "x")
    assert tasks.TaskStore(path).get("housekeeper:comb:2026-10")["status"] == tasks.QUEUED


def test_holds_are_recorded_once_and_released_only_by_asking(store):
    store.hold("housekeeper", "failed twice", now=1.0)
    store.hold("housekeeper", "failed again", now=2.0)
    assert store.held() == {"housekeeper": ("failed twice", 1.0)}
    assert store.release("housekeeper", now=3.0) == 1
    assert store.held() == {}


def test_the_store_holds_no_listener_and_no_material(store):
    with store._connect() as db:
        columns = {r[1] for t in ("tasks", "holds")
                   for r in db.execute(f"PRAGMA table_info({t})")}
    assert not columns & {"user_id", "listener", "query", "text", "material", "payload"}


def test_a_wipe_forgets_the_queue(store):
    store.add("housekeeper", "consolidate", "ep9", "x")
    assert store.forget_tasks() == 1 and store.counts() == {}
