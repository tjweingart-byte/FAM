"""The first run's tour (§586).

The tour itself is interface (`Tour` in `static/index.html`, driven by
`tools/smoke_preview.py`). What the server holds is one flag: finished or
skipped, so it is never offered again by itself on another device.
"""
from __future__ import annotations

import os
import pathlib
import sqlite3
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as appmod  # noqa: E402
import preferences as P  # noqa: E402
import topics as T  # noqa: E402

INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


@pytest.fixture
def store(tmp_path):
    return P.PreferenceStore(str(tmp_path / "prefs.db"))


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)
    monkeypatch.setattr(appmod, "EVENTS", T.EventStore(str(tmp_path / "e.db")))
    return TestClient(appmod.app)


def test_a_new_listener_has_not_taken_the_tour(store):
    assert store.get("nobody").tour_done is False
    assert store.get("nobody").as_dict()["tour_done"] is False


def test_the_flag_is_kept_and_other_writes_leave_it(store):
    store.save("u1", tour_done=True)
    store.save("u1", language="en", private_search=True)
    assert store.get("u1").tour_done is True
    store.save("u1", tour_done=False)
    assert store.get("u1").tour_done is False


def test_an_older_table_is_widened_not_recreated(tmp_path):
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE preferences (
        user_id TEXT PRIMARY KEY, interests TEXT NOT NULL DEFAULT '',
        language TEXT NOT NULL DEFAULT 'en', weekly_recap INTEGER NOT NULL DEFAULT 1,
        recap_week TEXT NOT NULL DEFAULT '', intro_done INTEGER NOT NULL DEFAULT 0,
        updated REAL NOT NULL)""")
    conn.execute("INSERT INTO preferences (user_id, interests, intro_done, updated)"
                 " VALUES ('old', 'sport', 1, 1)")
    conn.commit()
    conn.close()
    store = P.PreferenceStore(path)
    old = store.get("old")
    assert old.intro_done is True and old.tour_done is False


def test_the_api_stores_it_for_an_account(client):
    res = client.post("/api/auth/signup",
                      json={"email": "t@fam.test", "password": "a-long-enough-password"})
    assert res.status_code == 200, res.text
    assert client.get("/api/preferences").json()["tour_done"] is False
    saved = client.post("/api/preferences", json={"tour_done": True}).json()
    assert saved["tour_done"] is True
    assert client.get("/api/preferences").json()["tour_done"] is True


def test_the_interface_owes_it_at_the_end_of_sign_up_and_offers_a_replay():
    finish = INDEX[INDEX.index("function finishIntro(){"):]
    finish = finish[:finish.index("\n  }\n")]
    assert "Tour.owe()" in finish and "finishEntry()" in finish
    entry = INDEX[INDEX.index("function finishEntry(opts){"):]
    entry = entry[:entry.index("\n  }\n")]
    assert "Tour.offerOnce()" in entry


def test_an_account_from_before_the_tour_is_offered_it_once():
    """§587: offered once on the next open or sign-in when the account says
    it has not been taken; the account's "done" is kept on the device."""
    once = INDEX[INDEX.index("function offerOnce(){"):]
    once = once[:once.index("\n    }\n")]
    assert '"/api/preferences"' in once and "d.tour_done" in once
    route = INDEX[INDEX.index("function routeFirstScreen(){"):]
    route = route[:route.index("\n  }\n")]
    assert "Tour.offerOnce()" in route
    assert 'settingsRow("Take the tour"' in INDEX
    # Skippable from every card: one Skip in the card's head, and Escape.
    assert 'id="tourSkip"' in INDEX and 'e.key === "Escape"' in INDEX
