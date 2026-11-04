"""Re-file the episodes written before Nando (decided item 20; nando.md always
10, §428): a list for the owner first, applied only with its SHA-256, through
`category_tag.retag` as a first filing.
"""
from __future__ import annotations

import json

import pytest

import cache as cache_mod
import categories as C
import category_tag
import refile_old
import topics
from agents import categorizer


@pytest.fixture(autouse=True)
def tree():
    t = topics.category_tree()
    C.apply_seed(t)
    category_tag.reset()
    return t


def store(*rows):
    c = cache_mod.MemoryScriptCache()
    for key, query, title, words in rows:
        c.put(key, ["One sentence."], 3600, query, "", 2, "", "", "", title,
              category=words)
    return c


ROWS = (("k-eagles", "how did the Philadelphia Eagles do on Sunday", "Eagles on Sunday",
         "sports"),
        ("k-money", "Nvidia earnings this quarter", "Nvidia's quarter", "money"),
        ("k-filed", "how do tides work", "Tides", "science"))


@pytest.fixture
def cache(monkeypatch):
    c = store(*ROWS)
    c.set_category_node("k-filed", "science")     # Nando filed it: not a pre-Nando row
    return c


def picks(monkeypatch, answers: dict):
    asked = []

    async def ask_tie(query, candidates, notes=None, ahead=False):
        asked.append((query, tuple(candidates), ahead))
        node = answers.get(query, "")
        return node if node in candidates else ""
    monkeypatch.setattr(categorizer, "ask_tie", ask_tie)
    return asked


def test_only_episodes_nando_never_filed_are_read(cache):
    assert [r["key"] for r in cache.unfiled()] == ["k-eagles", "k-money"]
    assert [r["key"] for r in cache.unfiled(after="k-eagles")] == ["k-money"]
    row = cache.unfiled()[0]
    assert row["query"].startswith("how did the Philadelphia Eagles")
    assert row["title"] == "Eagles on Sunday" and row["category"] == "sports"


def test_the_sqlite_store_reads_them_too(tmp_path):
    c = cache_mod.SqliteScriptCache(str(tmp_path / "s.db"))
    for key, query, title, words in ROWS:
        c.put(key, ["One sentence."], 3600, query, "", 2, "", "", "", title,
              category=words)
    c.set_category_node("k-filed", "science")
    assert [r["key"] for r in c.unfiled()] == ["k-eagles", "k-money"]
    assert c.unfiled()[1]["category"] == "money"


def test_his_call_files_what_code_could_not(cache, monkeypatch):
    asked = picks(monkeypatch, {ROWS[0][1]: "philadelphia eagles"})
    proposal = refile_old.propose(cache)
    assert all(ahead for _q, _c, ahead in asked)          # nobody waits: ahead
    [change] = proposal["changes"]
    assert change["key"] == "k-eagles"
    assert change["current"] == "sports" and change["proposed"] == "philadelphia eagles"
    assert change["his_call"] == "philadelphia eagles" and change["tree_has_it"]
    assert proposal["episodes"] == 2 and proposal["model"] is True
    assert proposal["calls"]["answered"] == 1


def test_without_the_model_nothing_is_asked(cache, monkeypatch):
    asked = picks(monkeypatch, {ROWS[0][1]: "philadelphia eagles"})
    proposal = refile_old.propose(cache, model=False)
    assert asked == [] and proposal["calls"]["asked"] == 0
    assert all(c["proposed"] != "philadelphia eagles" for c in proposal["changes"])


def test_a_name_that_means_two_things_is_never_asked(monkeypatch):
    c = store(("k-united", "how did United do", "United", "sports"))
    asked = picks(monkeypatch, {"how did United do": "airlines"})
    proposal = refile_old.propose(c)
    assert asked == [] and proposal["changes"] == []


def test_the_dry_run_changes_nothing(cache, monkeypatch, tmp_path):
    picks(monkeypatch, {ROWS[0][1]: "philadelphia eagles"})
    digest = refile_old.write_report(refile_old.propose(cache), tmp_path / "r.json")
    assert cache.category_node("k-eagles") == ""
    assert len(digest) == 64
    md = (tmp_path / "r.md").read_text()
    assert "1 would change" in md and digest in md and "Philadelphia Eagles" in md


def test_apply_refuses_any_other_approval(cache, monkeypatch, tmp_path):
    picks(monkeypatch, {ROWS[0][1]: "philadelphia eagles"})
    path = tmp_path / "r.json"
    digest = refile_old.write_report(refile_old.propose(cache), path)
    # The last character changed - to one it is not (§442: "+ '0'" was the
    # real digest one run in sixteen, and the test failed by chance).
    flipped = digest[:-1] + ("1" if digest[-1] == "0" else "0")
    for wrong in ("", "yes", flipped):
        out = refile_old.apply(cache, path, wrong)
        assert out["applied"] == 0 and "nothing was changed" in out["refused"]
    # A list edited after the review is a different list.
    data = json.loads(path.read_text())
    data["changes"][0]["proposed"] = "nfl"
    path.write_text(json.dumps(data))
    assert refile_old.apply(cache, path, digest)["refused"]
    assert cache.category_node("k-eagles") == ""


def test_apply_with_the_approval_files_it_through_retag(cache, monkeypatch, tmp_path):
    picks(monkeypatch, {ROWS[0][1]: "philadelphia eagles"})
    path = tmp_path / "r.json"
    digest = refile_old.write_report(refile_old.propose(cache), path)
    seen = []
    real = category_tag.retag

    def spy(*a, **k):
        seen.append(k)
        return real(*a, **k)
    monkeypatch.setattr(category_tag, "retag", spy)
    out = refile_old.apply(cache, path, digest.upper())
    assert out["applied"] == 1 and not out["failed"]
    assert cache.category_node("k-eagles") == "philadelphia eagles"
    assert seen[0]["first_filing"] is True and seen[0]["was"] == "sports"
    # Applied twice is applied once: the row is filed now.
    again = refile_old.apply(cache, path, digest)
    assert again["applied"] == 0 and "filed meanwhile" in again["skipped"][0]["why"]


def test_a_row_that_changed_since_the_review_is_skipped(cache, monkeypatch, tmp_path):
    picks(monkeypatch, {ROWS[0][1]: "philadelphia eagles"})
    path = tmp_path / "r.json"
    digest = refile_old.write_report(refile_old.propose(cache), path)
    cache.set_category_node("k-eagles", "nfl")
    out = refile_old.apply(cache, path, digest)
    assert out["applied"] == 0 and cache.category_node("k-eagles") == "nfl"


def test_retag_leaves_an_unfiled_episode_alone_unless_it_is_a_first_filing(cache):
    out = category_tag.retag(cache, "k-eagles", "philadelphia eagles")
    assert "never filed" in out["skipped"] and cache.category_node("k-eagles") == ""
    out = category_tag.retag(cache, "k-eagles", "philadelphia eagles",
                             first_filing=True, was="sports")
    assert out["first_filing"] and out["old"] == "sports" and out["script"]
    assert cache.category_node("k-eagles") == "philadelphia eagles"
