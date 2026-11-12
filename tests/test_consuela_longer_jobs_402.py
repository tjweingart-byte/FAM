"""Phase 2, piece 5: Consuela's longer jobs (§402).

Re-filing on tree changes, the 90-day pass and the full comb, against a
fake tree and fake models (no key, no network); and an article two nodes
claim, handed to Nando through Netenyahu's task table.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import sys
from contextlib import closing
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as store_db  # noqa: E402  (a store's file, or its schema in Postgres, §243)
import history_archive as ha  # noqa: E402
from agents import dedupe, housekeeper as hk, housekeeping as hj, models  # noqa: E402

DAY = 86400.0
NOW = 1_800_000_000.0

#: child -> parent; a node absent here is not in the tree.
TREE = {"sports": "", "nfl": "sports", "eagles": "nfl", "giants": "nfl"}


def _tree(parents):
    def ancestors(node):
        out, seen = [], {node}
        while parents.get(node):
            node = parents[node]
            if node in seen:
                break
            seen.add(node)
            out.append(node)
        return out
    return SimpleNamespace(get=lambda node: object() if node in parents else None,
                           ancestors=ancestors)


def _settings(monkeypatch, **values):
    changed = dataclasses.replace(ha.settings, archive=True, archive_backend="sqlite",
                                  **values)
    for module in (ha, hk, hj, dedupe):
        monkeypatch.setattr(module, "settings", changed)
    return changed


@pytest.fixture
def archive(monkeypatch, tmp_path):
    import topics

    ha.reset()
    _settings(monkeypatch, housekeeper_dry_run=False)
    monkeypatch.setattr(ha, "_embed", lambda text: ([1.0] + [0.0] * (ha.DIMS - 1),
                                                    f"onnx:{ha.DIMS}"))
    monkeypatch.setattr(ha, "consuela_runs", lambda: True)
    parents = dict(TREE)
    monkeypatch.setattr(topics, "category_tree", lambda: _tree(parents))
    store = ha.SQLiteBackend(str(tmp_path / "archive.db"))
    store.parents = parents
    ha.use(store)
    hk.reset(hk.ReportStore(str(tmp_path / "housekeeper.db")))
    yield store
    ha.reset()
    hk.reset()


def _fact(text, node="eagles", kind="sentence", url=None, created=NOW):
    fact = ha.Fact(url=url or f"https://x.com/{abs(hash(text))}", text=text,
                   title="Eagles top Giants", host="x.com", grade="established",
                   published="2026-10-19", kind=kind, subject="Eagles", node_id=node,
                   node_path=ha.node_path(node) if node else [])
    return fact, created


def _add(store, fact, created):
    return store.add_fact(fact, [1.0] + [0.0] * (ha.DIMS - 1), f"onnx:{ha.DIMS}", created)


def _rows(store, sql, *args):
    with closing(store_db.connect(store.path)) as db:
        return db.execute(sql, args).fetchall()


# --- re-filing ----------------------------------------------------------------

def test_the_nearest_surviving_ancestor_is_read_from_the_fact_s_own_path(archive):
    tree = _tree({"sports": "", "nfl": "sports"})
    assert hj.surviving(["sports", "nfl", "eagles"], tree) == "nfl"
    assert hj.surviving(["gone", "also gone"], tree) == ""
    assert hj.where_now({"node_id": "eagles", "node_path": ["sports", "nfl", "eagles"]},
                        tree) == ("nfl", ["sports", "nfl"])


def test_a_pruned_node_s_facts_intake_and_readings_move_to_its_parent(archive):
    fid = _add(archive, *_fact("The Eagles won."))
    archive.intake([ha.Finding(url="https://x.com/a", passages=("One.",),
                               provider="perplexity")], "Eagles", "nfl", NOW, node="eagles")
    archive.add_reading(ha.Reading(subject="Eagles vs Giants", metric="score",
                                   as_of=ha.datetime.fromtimestamp(NOW, ha.timezone.utc),
                                   text="7-0", node_id="eagles"), NOW)
    del archive.parents["eagles"]
    summary = hj.refile("eagles", now=NOW)
    assert summary["refiled"] == 1 and summary["moved"] == {"eagles": "nfl"}
    assert _rows(archive, "SELECT node_id, node_path FROM facts WHERE id = ?", fid) == [
        ("nfl", json.dumps(["sports", "nfl"]))]
    assert _rows(archive, "SELECT nodes FROM intake") == [('["nfl"]',)]
    assert _rows(archive, "SELECT node_id FROM live_series") == [("nfl",)]
    refile, = hk.store().refiles()
    assert (refile["from_node"], refile["to_node"]) == ("eagles", "nfl")
    assert refile["from_path"] == ["sports", "nfl", "eagles"]


def test_a_moved_node_keeps_its_facts_with_a_new_path(archive):
    fid = _add(archive, *_fact("The Eagles won."))
    archive.parents["nfl"] = "football"
    archive.parents["football"] = "sports"
    hj.refile("nfl", now=NOW)
    assert _rows(archive, "SELECT node_id, node_path FROM facts WHERE id = ?", fid) == [
        ("eagles", json.dumps(["sports", "football", "nfl", "eagles"]))]


def test_a_dry_run_re_file_reports_and_changes_nothing(archive, monkeypatch):
    _settings(monkeypatch, housekeeper_dry_run=True)
    fid = _add(archive, *_fact("The Eagles won."))
    del archive.parents["eagles"]
    assert hj.refile("eagles", now=NOW)["refiled"] == 1
    assert _rows(archive, "SELECT node_id FROM facts WHERE id = ?", fid) == [("eagles",)]
    assert hk.store().refiles()[0]["mode"] == "dry_run"


def test_a_fact_whose_branch_is_gone_falls_to_its_facet_never_a_dead_node(archive):
    """§555: a facet is a root and never goes, so it is the nearest
    surviving ancestor of any fact whose branch was pruned - before, the
    facet failed the same test and the fact was unfiled."""
    fid = _add(archive, *_fact("The Eagles won."))
    for node in ("eagles", "nfl", "sports"):
        del archive.parents[node]
    assert hj.refile("eagles", now=NOW)["unfiled"] == 0
    assert _rows(archive, "SELECT node_id FROM facts WHERE id = ?", fid) == [("sports",)]


def test_with_no_surviving_ancestor_a_fact_is_unfiled_never_left_on_a_dead_node(archive):
    archive.parents["club"] = ""
    archive.parents["squad"] = "club"
    fid = _add(archive, *_fact("The squad won.", node="squad"))
    for node in ("squad", "club"):
        del archive.parents[node]
    assert hj.refile("squad", now=NOW)["unfiled"] == 1
    assert _rows(archive, "SELECT node_id, node_path FROM facts WHERE id = ?", fid) == [
        ("", "[]")]


# --- the 90-day pass ------------------------------------------------------------

def test_old_plain_sentences_fold_into_one_link_per_article(archive):
    old = NOW - 91 * DAY
    a = _add(archive, *_fact("The Eagles won.", url="https://x.com/a", created=old))
    b = _add(archive, *_fact("Hurts threw three touchdowns.", url="https://x.com/a",
                             created=old))
    figure = _add(archive, *_fact("Tickets cost $250.", kind="figure", url="https://x.com/a",
                                  created=old))
    quote = _add(archive, *_fact('"We won," he said.', kind="quote", url="https://x.com/a",
                                 created=old))
    young = _add(archive, *_fact("The Eagles trained.", url="https://x.com/b",
                                 created=NOW - 10 * DAY))
    summary = hj.second_pass(now=NOW)
    assert (summary["sentences"], summary["articles"], summary["folded"]) == (2, 1, 2)
    active = dict(_rows(archive, "SELECT id, active FROM facts"))
    assert active[a] == active[b] == 0
    assert active[figure] == active[quote] == active[young] == 1
    link_id = ha.url_id("https://x.com/a\nlink")
    (kind, text, space, embedding, links, node), = _rows(
        archive, "SELECT kind, text, space, embedding, corroborations, node_id FROM facts "
        "WHERE id = ?", link_id)
    assert (kind, text, node) == ("link", "Eagles top Giants", "eagles")
    # No vector: the recall never offers a headline as evidence; and no
    # corroboration of itself.
    assert embedding is None and space == "" and links == "[]"
    # Reversible, like every mark.
    assert archive.restore(a) and dict(_rows(archive, "SELECT id, active FROM facts"))[a] == 1


def test_the_90_day_pass_in_a_dry_run_changes_nothing(archive, monkeypatch):
    _settings(monkeypatch, housekeeper_dry_run=True)
    _add(archive, *_fact("The Eagles won.", created=NOW - 91 * DAY))
    summary = hj.second_pass(now=NOW)
    assert summary["sentences"] == 1 and summary["folded"] == 0
    assert _rows(archive, "SELECT kind, active FROM facts") == [("sentence", 1)]


def test_the_recall_never_returns_a_link(archive):
    _add(archive, *_fact("The Eagles won.", created=NOW - 91 * DAY))
    hj.second_pass(now=NOW)
    found = ha.recall("eagles", window_days=1,
                      now=ha.datetime.fromtimestamp(NOW + 400 * DAY, ha.timezone.utc))
    assert found == []


# --- the full comb ---------------------------------------------------------------

def test_the_comb_runs_a_page_a_night_until_through_then_waits(archive, monkeypatch):
    monkeypatch.setattr(hj, "COMB_PAGE", 2)
    monkeypatch.setattr(models, "ask", lambda *a, **k: None)
    for i in range(3):
        _add(archive, *_fact(f"Fact number {['one', 'two', 'three'][i]}."))
    first = asyncio.run(hj.comb(now=NOW))
    assert (first["due"], first["facts"], first["finished"]) == (True, 2, False)
    second = asyncio.run(hj.comb(now=NOW + DAY))
    assert (second["facts"], second["finished"]) == (1, True)
    assert asyncio.run(hj.comb(now=NOW + 2 * DAY)) == {"due": False}
    assert asyncio.run(hj.comb(now=NOW + 8 * DAY))["due"] is True


def test_the_comb_finds_repeats_the_nightly_run_never_looks_at(archive, monkeypatch):
    # Both kept long ago: the nightly two-day look would miss them.
    old = NOW - 400 * DAY
    _add(archive, *_fact("The council approved the budget.", url="https://a", created=old))
    _add(archive, *_fact("the council approved the budget", url="https://b", created=old))
    assert asyncio.run(dedupe.run(now=NOW))["pairs"] == 0
    result = asyncio.run(hj.comb(now=NOW))
    assert result["dedupe"]["same"] == 1 and result["dedupe"]["mode"] == "dry_run"


def test_the_comb_re_files_what_a_lost_task_left_behind(archive):
    fid = _add(archive, *_fact("The Eagles won."))
    del archive.parents["eagles"]
    result = asyncio.run(hj.comb(now=NOW))
    assert result["refiled"] == 1
    assert _rows(archive, "SELECT node_id FROM facts WHERE id = ?", fid) == [("nfl",)]


# --- Nando, and Netenyahu's schedule ------------------------------------------------

def test_an_article_two_nodes_claim_is_filed_under_both(archive, monkeypatch):
    """§413, the owner: a Bengals-Giants game is filed under both teams -
    never collapsed to their parent, never held for Nando."""
    monkeypatch.setattr(models, "ask", _Model_picks())
    finding = ha.Finding(url="https://x.com/a", title="T", published="2026-10-19",
                         passages=("The Eagles beat the Giants on Sunday at home.",),
                         provider="perplexity")
    archive.intake([finding], "Eagles", "nfl", NOW, node="eagles")
    archive.intake([finding], "Giants", "nfl", NOW, node="giants")
    summary = asyncio.run(hk.run())
    assert (summary["chosen"], summary["held"]) == (1, 0)
    (node, also), = _rows(archive, "SELECT node_id, also_nodes FROM facts")
    assert (node, json.loads(also)) == ("eagles", ["giants"])
    assert "filed under eagles, giants" in hk.store().recent()[0]["why"]
    # Sonic finds it from either team.
    from agents import retriever

    reader = retriever.ReadOnlySQL(archive.path)
    later = ha.datetime.fromtimestamp(NOW + 400 * DAY, ha.timezone.utc)
    assert [f.text for f in reader.node_facts(["giants"], later, 6)] == [
        "The Eagles beat the Giants on Sunday at home."]
    # And a re-file of the second node follows it.
    del archive.parents["giants"]
    archive.replace_node("giants", "nfl")
    assert json.loads(_rows(archive, "SELECT also_nodes FROM facts")[0][0]) == ["nfl"]


def _Model_picks():
    async def ask(request, **_kw):
        return models.AgentResult(status=models.OK, agent=request.agent, task=request.task,
                                  value=hk.Picks(articles=[hk.ArticlePick(
                                      article=1, key=[1], quotes=[], figures=[])]))
    return ask


def test_her_longer_jobs_are_registered_and_scheduled():
    from agents import netenyahu

    kinds = ("consolidate", "dedupe", "refile", "second", "comb")
    try:
        hk.register()
        assert all(("housekeeper", k) in netenyahu.HANDLERS for k in kinds)
    finally:
        for kind in kinds:
            netenyahu.HANDLERS.pop(("housekeeper", kind), None)
    hours = {kind: hour for agent, kind, hour, _s in netenyahu.SCHEDULE
             if agent == "housekeeper"}
    assert hours == {"consolidate": 1, "dedupe": 2, "comb": 3, "second": 4,
                     "totals": 0}


def test_a_refile_task_s_key_is_its_node(archive, monkeypatch):
    seen = []
    monkeypatch.setattr(hj, "refile", lambda node, now=None: seen.append(node) or {})
    asyncio.run(hj.refile_handler("housekeeper:refile:new york giants"))
    assert seen == ["new york giants"]
