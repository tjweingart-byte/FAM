"""Keith's checks that each agent keeps to its rule book, item 4 (§491).

Pinned, each from a signed rule book and run on what the agent left behind:
Sonic connecting as the archive's writer is a breach; Consuela deleting
inside the 30-day grace is one; a Mclovin verdict outside his named checks
and results is one; a C3P0 title that states a result is one; a node Nando
filed under that the tree lacks is one - each urgent, naming the agent, so
`hold_agent` applies - and none when the agent is off. Mclovin's unchecked
share and Shakespeare's late / second_failed share rising are incidents;
the trial's episodes and listens per arm are reported. Reads only.
"""
from __future__ import annotations

import asyncio
import dataclasses

import pytest

import agent_checks as AC
import incidents as INC
import keith_fixes as KF
import pipeline_errors as PE
import provider_usage

NOW = 1_790_000_000.0


@pytest.fixture
def stores(tmp_path):
    PE.reset(PE.ErrorStore(str(tmp_path / "e.db")))
    INC.reset(INC.IncidentStore(str(tmp_path / "e.db")))
    provider_usage.reset(provider_usage.UsageStore(str(tmp_path / "u.db")))
    AC.reset()
    yield
    AC.reset()
    INC.reset()


def use(monkeypatch, **changes):
    import config

    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, **changes))


def causes(found):
    return [f["cause"] for f in found]


# ---------------------------------------------------------------------- Sonic
def test_sonic_as_the_archive_s_writer_is_a_breach(monkeypatch):
    import credentials

    use(monkeypatch, agents=True, retriever=True, archive_backend="atlas")
    uris = {"MONGODB_URI": "mongodb+srv://fam:pw@c.mongodb.net/",
            "MONGODB_READ_URI": "mongodb+srv://fam:pw2@c.mongodb.net/"}
    monkeypatch.setattr(credentials, "active", lambda name: uris.get(name, ""))
    [breach] = AC.sonic_never_writes()
    assert breach["cause"] == "breach:retriever:writer-user"
    assert breach["severity"] == "urgent" and breach["detail"]["role"] == "retriever"
    uris["MONGODB_READ_URI"] = "mongodb+srv://sonic:pw@c.mongodb.net/"
    assert AC.sonic_never_writes() == []
    use(monkeypatch, agents=True, retriever=False, archive_backend="atlas")
    uris["MONGODB_READ_URI"] = uris["MONGODB_URI"]
    assert AC.sonic_never_writes() == []                   # off: nothing to check


# -------------------------------------------------------------------- Consuela
def test_consuela_deleting_inside_the_grace_is_a_breach(monkeypatch):
    use(monkeypatch, agents=True, housekeeper=True, housekeeper_superseded_days=7.0)
    [breach] = AC.consuela_keeps_grace()
    assert breach["cause"] == "breach:housekeeper:grace" and "7 days" in breach["title"]
    use(monkeypatch, agents=True, housekeeper=True, housekeeper_superseded_days=30.0)
    assert AC.consuela_keeps_grace() == []


# --------------------------------------------------------------------- Mclovin
@pytest.fixture
def mclovin(tmp_path):
    from agents import supervisor_training as ST

    ST.reset(ST.SupervisorTrainingStore(str(tmp_path / "agents.db")))
    yield ST.store()
    ST.reset()


def flag(store, key, check, result, at, n=1):
    rows = [{"sentence": i, "check": check, "result": result, "enforced": True,
             "why": "", "text": "s"} for i in range(n)]
    store.put_episode(key, int(at), 40, rows, now=at)


def test_a_verdict_outside_mclovin_s_named_failures_is_a_breach(mclovin):
    flag(mclovin, "a", "quote", "fail", NOW - 60)
    assert AC.mclovin_names_his_failures(NOW) == []
    flag(mclovin, "b", "vibes", "fail", NOW - 60)
    [breach] = AC.mclovin_names_his_failures(NOW)
    assert breach["cause"] == "breach:supervisor:unnamed"
    assert "vibes/fail" in breach["detail"]["examples"]


def test_mclovin_s_unchecked_share_rising_is_an_incident(mclovin):
    for i in range(7):                                      # a week at 1 in 40
        flag(mclovin, f"w{i}", "figures", "unchecked", NOW - (2 + i) * 86400)
    found, figures = AC.mclovin_unchecked(NOW)
    assert found == [] and figures["today_sentences"] == 0
    flag(mclovin, "t1", "figures", "unchecked", NOW - 600, n=6)   # 6 of 40 today
    flag(mclovin, "t2", "quote", "fail", NOW - 500)               # 40 more sentences
    found, figures = AC.mclovin_unchecked(NOW)
    assert figures["today"] == 6 and figures["today_sentences"] == 80
    [incident] = found
    assert incident["cause"] == "watch:supervisor:unchecked"
    assert "7.5% of sentences unchecked today" in incident["title"]


# ------------------------------------------------------------------------ C3P0
class Cache:
    def __init__(self, titles=(), tagged=()):
        self.titles, self.rows = list(titles), list(tagged)

    def recent(self, limit=40, **kw):
        return [{"key": str(i), "title": t} for i, t in enumerate(self.titles)][:limit]

    def tagged(self, after="", limit=500):
        return [r for r in self.rows if r[0] > after][:limit]


def test_a_c3p0_title_stating_a_result_is_a_breach(monkeypatch, stores):
    """Read from his own titles, kept as steps (§495), never the episode's
    title - a writer's or an admin's is not his - and never the words."""
    import time

    now = time.time()                       # steps past 14 days go on write
    use(monkeypatch, agents=True, contextualizer=True, contextualizer_titles=True)
    PE.store().add([], steps=[{"at": now - 600, "step": "c3p0 title", "path": "search",
                               "outcome": "clean", "seconds": None}])
    assert AC.c3p0_titles_carry_no_results(now) == []
    PE.store().add([], steps=[{"at": now - 500, "step": "c3p0 title", "path": "search",
                               "outcome": "result", "seconds": None}])
    [breach] = AC.c3p0_titles_carry_no_results(now)
    assert breach["cause"] == "breach:contextualizer:title-result"
    assert "1 of C3P0's 2 titles" in breach["title"]
    assert breach["detail"]["stated"] == 1 and "examples" not in breach["detail"]
    use(monkeypatch, agents=True, contextualizer=True, contextualizer_titles=False)
    assert AC.c3p0_titles_carry_no_results(now) == []          # not his titles


def test_c3p0_s_title_is_kept_as_a_step_without_its_words(monkeypatch):
    import script_generator as SG

    use(monkeypatch, agents=True, contextualizer=True, contextualizer_titles=True)
    seen = []
    monkeypatch.setattr(SG, "_agent_runs", lambda notes, role: True)
    monkeypatch.setattr(SG, "settings", dataclasses.replace(SG.settings,
                                                            contextualizer_titles=True))
    monkeypatch.setattr(SG.pipeline_errors, "step", lambda *a, **k: seen.append(a))

    class Brief:
        contextualized = {"title": "Eagles beat Giants 24-17"}

    notes = SG.ScriptNotes()
    SG._take_c3p0_title(notes, Brief())
    assert notes.c3p0_title and seen and seen[0][0] == "c3p0 title"
    assert seen[0][2] == "result" and "Eagles" not in repr(seen)


# ----------------------------------------------------------------------- Nando
def test_a_node_nando_filed_under_that_the_tree_lacks_is_a_breach(monkeypatch):
    import topics

    use(monkeypatch, agents=True, categorizer=True)
    monkeypatch.setattr(topics, "category_tree",
                        lambda: {"sports/nfl": object(), "markets": object()})
    rows = [("a", "sports/nfl", 1, []), ("b", "sports/xfl", 1, []), ("c", "markets", 1, [])]
    [breach] = AC.nando_nodes_exist(Cache(tagged=rows))
    assert breach["cause"] == "breach:categorizer:dead-node"
    assert breach["detail"]["examples"] == ["sports/xfl"]


def test_nando_s_filings_are_read_a_page_at_a_time_and_come_round(monkeypatch):
    """A breach stands until a whole pass is clean (§495): a clean page
    between two bad ones never closes and reopens it."""
    import topics

    use(monkeypatch, agents=True, categorizer=True)
    tree = {"n": object()}
    monkeypatch.setattr(topics, "category_tree", lambda: tree)
    monkeypatch.setattr(AC, "SAMPLE", 2)
    cache = Cache(tagged=[("a", "n", 1, []), ("b", "n", 1, []), ("c", "gone", 1, [])])
    assert AC.nando_nodes_exist(cache) == []                # a, b: nothing yet
    dead = ["breach:categorizer:dead-node"]
    assert causes(AC.nando_nodes_exist(cache)) == dead      # c: the pass ends
    assert causes(AC.nando_nodes_exist(cache)) == dead      # a, b: still stands
    assert causes(AC.nando_nodes_exist(cache)) == dead      # c again
    tree["gone"] = object()                                 # the node is back
    assert AC.nando_nodes_exist(cache) == []


# ---------------------------------------------------------------- Shakespeare
def test_the_tandem_s_late_share_rising_is_an_incident(stores):
    for i in range(70):                                     # a week, 1 late in 10
        PE.store().add([], steps=[{"at": NOW - 2 * 86400 - i * 3600, "step": "editor tandem",
                                   "path": "search", "outcome": "late" if i % 10 == 0 else "handed_over",
                                   "seconds": None}], now=NOW)
    for i in range(30):                                     # today, 1 in 3
        PE.store().add([], steps=[{"at": NOW - i * 600, "step": "editor tandem",
                                   "path": "search", "outcome": "late" if i % 3 == 0 else "handed_over",
                                   "seconds": None}], now=NOW)
    found, detail = AC.tandem_outcomes(NOW)
    assert causes(found) == ["watch:editor:late"]
    assert detail["today"] == {"late": 10, "handed_over": 20}
    assert "Watching only" in found[0]["plan"]["needs_from_owner"]


def test_the_tandem_outcome_is_kept_as_a_measured_step(stores):
    import script_generator

    script_generator._tandem_count("second_failed")
    PE.flush()
    [row] = [r for r in PE.store().steps_since(0) if r["step"] == "editor tandem"]
    assert row["outcome"] == "second_failed"


# -------------------------------------------------------------- in the round
def test_a_breach_is_an_incident_and_hold_agent_would_apply(stores, monkeypatch):
    use(monkeypatch, agents=True, housekeeper=True, housekeeper_superseded_days=1.0,
        it_fixes="")
    INC.watch(NOW)
    [row] = INC.report(NOW)["open"]
    assert row["cause"] == "breach:housekeeper:grace" and row["source"] == "agents"
    asyncio.run(KF.act(NOW))
    [fix] = INC.store().fixes_since(0)
    assert (fix["fix"], fix["outcome"]) == ("hold_agent", "would")
    # Mended: the next look finds nothing, and the incident closes.
    use(monkeypatch, agents=True, housekeeper=True, housekeeper_superseded_days=30.0)
    INC.watch(NOW + AC.EVERY_SECONDS + 1)
    assert INC.report(NOW)["open"] == []


def test_the_checks_run_at_most_hourly(stores, monkeypatch):
    use(monkeypatch, agents=True, housekeeper=True, housekeeper_superseded_days=1.0)
    assert causes(AC.findings(NOW)) == ["breach:housekeeper:grace"]
    use(monkeypatch, agents=True, housekeeper=True, housekeeper_superseded_days=30.0)
    assert causes(AC.findings(NOW + 60)) == ["breach:housekeeper:grace"]   # stands
    assert AC.findings(NOW + AC.EVERY_SECONDS) == []


def test_nothing_is_checked_while_the_agents_are_off(stores, monkeypatch):
    use(monkeypatch, agents=False, housekeeper=True, housekeeper_superseded_days=1.0)
    assert AC.findings(NOW, force=True) == []


def test_the_report_has_the_trial_and_the_watches(stores, monkeypatch):
    import episode_quality

    monkeypatch.setattr(episode_quality, "editor_trial", lambda: {"arms": {
        "with": {"episodes": 41, "listens": 120, "early_exit": 0.2},
        "without": {"episodes": 39, "listens": 101}}})
    out = AC.report(NOW)
    assert out["trial"] == {"with": {"episodes": 41, "listens": 120},
                            "without": {"episodes": 39, "listens": 101}}
    assert "tandem" in out and "mclovin_unchecked" in out
