"""The archive's intake records which search supplied each passage (§358).

Perplexity's terms let FAM keep what it returns; Parallel's forbid reusing it
for another listener (docs/PROVIDER_TERMS.md). A record that does not say
where its words came from cannot be kept or dropped per provider, so every
finding carries its provider and intake keeps each provider's passages apart.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing

import pytest

import history_archive as ha
import research


def _results():
    return [research.SearchResult(url="https://apnews.com/z", title="AP",
                                  published_date="2026-10-22",
                                  highlights=["It happened."])]


def test_each_web_search_names_itself_on_its_findings(monkeypatch):
    monkeypatch.setattr(research, "_search_perplexity",
                        lambda *a, **k: (_results(), 0.001))
    monkeypatch.setattr(research, "_search_parallel",
                        lambda *a, **k: (_results(), 0.001))
    for backend in ("perplexity", "parallel"):
        packet = research._retrieve_blocking("q", 10, 6, 2, backend=backend)
        assert [f.provider for f in packet.findings] == [backend]


# Runs on both backends: read through the store's own connection, so in
# CI's Postgres job (§243) this checks the by_provider column there too. That
# database is shared by the whole run, so the URL is this test's own and the
# counts are compared before and after rather than read as totals.
def test_one_article_from_two_providers_keeps_their_passages_apart(tmp_path):
    store = ha.SQLiteBackend(str(tmp_path / "archive.db"))
    url = f"https://www.reuters.com/a/{tmp_path.name}"
    before = store.counts()["intake_by_provider"]
    store.intake([ha.Finding(url=url, passages=("From P.", "Shared."),
                             provider="perplexity")], "Eagles", "nfl", 1.0)
    store.intake([ha.Finding(url=url, passages=("From X.", "Shared."),
                             provider="parallel")], "Eagles", "nfl", 2.0)
    with closing(store._connect()) as db:
        passages, by_provider = db.execute(
            "SELECT passages, by_provider FROM intake WHERE url = ?", (url,)).fetchone()
    # What every reader reads is unchanged: the union.
    assert json.loads(passages) == ["From P.", "Shared.", "From X."]
    assert json.loads(by_provider) == {"perplexity": ["From P.", "Shared."],
                                       "parallel": ["From X.", "Shared."]}
    after = store.counts()["intake_by_provider"]
    assert after["perplexity"] - before["perplexity"] == 1
    assert after["parallel"] - before["parallel"] == 1
    assert after["unknown"] == before["unknown"]


# Reads the SQLite file directly, so it tests the SQLite backend only;
# skipped in CI's Postgres job (§243), where the store is not that file.
@pytest.mark.sqlite_file
def test_an_archive_from_before_is_widened_and_its_rows_read_as_unknown(tmp_path):
    path = str(tmp_path / "archive.db")
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE intake (id TEXT PRIMARY KEY, url TEXT NOT NULL,"
                   " title TEXT NOT NULL, host TEXT NOT NULL, grade TEXT NOT NULL,"
                   " published TEXT NOT NULL, passages TEXT NOT NULL,"
                   " subjects TEXT NOT NULL, categories TEXT NOT NULL,"
                   " first_seen REAL NOT NULL, last_seen REAL NOT NULL,"
                   " seen INTEGER NOT NULL, digested INTEGER NOT NULL DEFAULT 0)")
        db.execute("INSERT INTO intake VALUES (?, 'https://x.com/1', '', '', '',"
                   " '', '[\"Old words.\"]', '[]', '[]', 0, 0, 1, 0)",
                   (ha.url_id("https://x.com/1"),))
    store = ha.SQLiteBackend(path)
    assert store.counts()["intake_by_provider"]["unknown"] == 1
    # And a later sighting of the same article starts recording its provider.
    store.intake([ha.Finding(url="https://x.com/1", passages=("New words.",),
                             provider="perplexity")], "", "", 1.0)
    with closing(sqlite3.connect(path)) as db:
        rows = db.execute("SELECT passages, by_provider FROM intake").fetchall()
    assert len(rows) == 1
    # The old words stay unattributed; only the new ones carry a provider.
    assert json.loads(rows[0][0]) == ["Old words.", "New words."]
    assert json.loads(rows[0][1]) == {"perplexity": ["New words."]}


class _Collection:
    def __init__(self):
        self.updates = []

    def update_one(self, query, update, upsert=False):
        self.updates.append(update)


def test_atlas_adds_each_provider_s_passages_under_its_name():
    store = ha.AtlasBackend.__new__(ha.AtlasBackend)
    store.intake_c = _Collection()
    store.ensure_indexes = lambda: None
    store.intake([ha.Finding(url="https://a.com/1", passages=("Words.",),
                             provider="perplexity"),
                  ha.Finding(url="https://a.com/2", passages=("More.",))], "", "", 1.0)
    first, second = store.intake_c.updates
    assert first["$addToSet"]["by_provider.perplexity"] == {"$each": ["Words."]}
    # A finding with no provider adds none, rather than an empty key.
    assert not any(k.startswith("by_provider") for k in second["$addToSet"])


def test_nothing_parallel_returned_is_kept(tmp_path, monkeypatch):
    """§366: Parallel is the backup search and its terms forbid keeping its
    output for other listeners, so the archive keeps none of it - not its
    passages in intake, not its links beside the episode."""
    store = ha.SQLiteBackend(str(tmp_path / "archive.db"))
    monkeypatch.setattr(ha, "backend", lambda: store)
    monkeypatch.setattr(ha, "_backing_off", lambda: False)
    findings = [
        ha.Finding(url="https://a.com/1", passages=("From Parallel.",),
                   provider="parallel"),
        ha.Finding(url="https://a.com/1", passages=("From Perplexity.",),
                   provider="perplexity"),
        ha.Finding(url="https://b.com/2", passages=("Only Parallel.",),
                   provider="parallel"),
    ]
    ha._write(findings, "subject", "", "episode-1", "key", 1.0)
    with closing(sqlite3.connect(store.path)) as db:
        rows = db.execute("SELECT url, passages, by_provider FROM intake").fetchall()
    assert [r[0] for r in rows] == ["https://a.com/1"]
    assert json.loads(rows[0][1]) == ["From Perplexity."]
    assert json.loads(rows[0][2]) == {"perplexity": ["From Perplexity."]}
    assert [link["url"] for link in store.episode_links("episode-1")] == [
        "https://a.com/1"]


def test_an_episode_from_parallel_alone_keeps_nothing(tmp_path, monkeypatch):
    store = ha.SQLiteBackend(str(tmp_path / "archive.db"))
    monkeypatch.setattr(ha, "backend", lambda: store)
    monkeypatch.setattr(ha, "_backing_off", lambda: False)
    ha._write([ha.Finding(url="https://b.com/2", passages=("Words.",),
                          provider="parallel")], "s", "", "episode-2", "key", 1.0)
    assert store.counts()["intake"] == 0
    assert store.episode_links("episode-2") == []
