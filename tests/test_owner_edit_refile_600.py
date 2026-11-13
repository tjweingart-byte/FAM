"""§600: after the owner's first review of the tree, Keith raised "19 nodes
Nando filed 39 episodes under are not in the tree - Nando may still be
naming nodes the tree does not hold". He was not: the owner had removed
those nodes, and the edit left their episodes behind. An accepted remove,
merge or rename now carries its episodes; a boot repair moves any left
from before; and Keith reads a node the owner took out as the tree moving,
never as Nando misbehaving, unless something was filed there after."""
from __future__ import annotations

import dataclasses
import time

import pytest

import categories as C
import category_review as R
import topics
import cache as cache_mod


@pytest.fixture
def tree(tmp_path, monkeypatch):
    store = C.CategoryStore(str(tmp_path / "categories.db"))
    C.apply_seed(store)
    store.mint("company", parent_id="business", source=C.SOURCE_SEARCH, listeners=3)
    store.mint("stocks", parent_id="money", source=C.SOURCE_SEARCH, listeners=3)
    monkeypatch.setattr(topics, "_CATEGORIES", store)
    yield store


@pytest.fixture(params=["memory", "sqlite"])
def cache(request, tmp_path):
    c = (cache_mod.MemoryScriptCache() if request.param == "memory"
         else cache_mod.SqliteScriptCache(str(tmp_path / "s.db")))
    c.put("k1", ["One."], 3600, "anthropic news", title="Anthropic's Step",
          category_node="company")
    c.put("k2", ["Two."], 3600, "stock market", category_node="stocks")
    c.put("k3", ["Three."], 3600, "bengals", category_node="bengals")
    return c


def test_a_merge_takes_its_episodes_to_the_node_it_became(tree, cache):
    out = R.apply(tree, "merge", "stocks", "stock market", "same subject", cache=cache)
    assert "1 episode re-filed" in out["said"]
    assert cache.keys_with_node("stock market") == ["k2"]
    assert cache.keys_with_node("stocks") == []


def test_a_removal_unfiles_its_episodes_for_nando(tree, cache):
    out = R.apply(tree, "remove", "company", "", "not a subject", cache=cache)
    assert "left for Nando to re-file" in out["said"]
    assert cache.keys_with_node("company") == []
    assert "k1" in [r["key"] for r in cache.unfiled()], "Nando's re-file finds it"


def test_a_rename_takes_its_episodes(tree, cache):
    R.apply(tree, "rename", "company", "companies", "", cache=cache)
    assert cache.keys_with_node("companies") == ["k1"]


def test_a_move_or_an_add_touches_no_episode(tree, cache):
    R.apply(tree, "move", "company", "tech", "", cache=cache)
    assert cache.keys_with_node("company") == ["k1"]
    assert "refiled" not in R.apply(tree, "add", "boutiques", "business", "", cache=cache)


def test_the_boot_repair_moves_what_earlier_edits_left(tree, cache):
    # Edits accepted before §600: no cache, so the episodes stayed behind.
    R.apply(tree, "merge", "stocks", "stock market")
    R.apply(tree, "remove", "company")
    assert cache.keys_with_node("stocks") == ["k2"]
    assert R.refile(cache, tree) == {"moved": 1, "unfiled": 1}
    assert R.refile(cache, tree) == {"moved": 0, "unfiled": 0}, "idempotent"
    assert cache.keys_with_node("bengals") == ["k3"], "the rest is untouched"


def _keith(monkeypatch, rows):
    import agent_checks as AC
    import config

    monkeypatch.setattr(config, "settings",
                        dataclasses.replace(config.settings, agents=True, categorizer=True))
    monkeypatch.setattr(AC, "_NANDO", {"after": "", "seen": set(), "last_pass": set()})

    class Cache:
        def tagged(self, after="", limit=500):
            return [r for r in rows if r[0] > after][:limit]

    return AC.nando_nodes_exist(Cache())


def test_keith_reads_an_owner_removal_as_the_tree_moving(tree, monkeypatch):
    filed_at = time.time() - 3600
    R.apply(tree, "remove", "company")
    [breach] = _keith(monkeypatch, [("k1", "company", filed_at, [])])
    assert breach["detail"]["still_filing"] is False
    words = str(breach)
    assert "your edits to the tree" in words
    assert "Hold Nando" not in words


def test_keith_still_flags_a_filing_after_the_removal(tree, monkeypatch):
    R.apply(tree, "remove", "company")
    [breach] = _keith(monkeypatch, [("k1", "company", time.time() + 5, [])])
    assert breach["detail"]["still_filing"] is True


def test_keith_still_flags_a_node_nobody_removed(tree, monkeypatch):
    [breach] = _keith(monkeypatch, [("k1", "not a node", time.time() - 60, [])])
    assert breach["detail"]["still_filing"] is True
