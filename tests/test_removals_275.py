"""§275: the old search vendor's names are gone from the metering ledger."""
from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metering  # noqa: E402


def test_a_ledger_with_the_old_column_names_is_renamed_and_keeps_its_rows(tmp_path):
    path = tmp_path / "metering.db"
    old = {"searches": "exa_searches", "cost": "exa_cost", "usd": "exa_usd"}
    with sqlite3.connect(path) as db:
        db.execute(f"""CREATE TABLE usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
            user_id TEXT NOT NULL, plan TEXT NOT NULL DEFAULT 'free',
            surface TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
            minutes INTEGER NOT NULL DEFAULT 0, model_calls INTEGER NOT NULL DEFAULT 0,
            input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens INTEGER NOT NULL DEFAULT 0,
            cache_write_tokens INTEGER NOT NULL DEFAULT 0,
            {old['searches']} INTEGER NOT NULL DEFAULT 0,
            {old['cost']} REAL NOT NULL DEFAULT 0,
            audio_seconds REAL NOT NULL DEFAULT 0, cache_hit INTEGER NOT NULL DEFAULT 0,
            cost_usd REAL NOT NULL DEFAULT 0, claude_usd REAL NOT NULL DEFAULT 0,
            {old['usd']} REAL NOT NULL DEFAULT 0, gpu_usd REAL NOT NULL DEFAULT 0,
            priced INTEGER NOT NULL DEFAULT 1)""")
        db.execute(f"INSERT INTO usage (at, user_id, {old['searches']}, "
                   f"{old['cost']}, {old['usd']}) VALUES (1, 'u', 2, 0.002, 0.002)")
    store = metering.MeterStore(str(path))
    with sqlite3.connect(path) as db:
        columns = {r[1] for r in db.execute("PRAGMA table_info(usage)")}
        row = db.execute("SELECT web_searches, web_search_cost, web_search_usd "
                         "FROM usage").fetchone()
    assert {"web_searches", "web_search_cost", "web_search_usd"} <= columns
    assert not columns & set(old.values())
    assert row == (2, 0.002, 0.002)
    assert store is not None
