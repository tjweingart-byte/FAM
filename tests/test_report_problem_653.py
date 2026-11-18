"""§653: "Report a problem" on the player, carrying the phone player's log.

The owner, 11.18, after the first sentence was heard twice again: "Yes, add
Report a problem to the player menu." The iPhone player kept a detailed log
(connects, hold and release, stalls, refills, reconnects, format fallbacks)
that never left the phone. A report from the player now carries it into
/admin's feedback inbox beside the episode's title, sources and transcript.
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as appmod  # noqa: E402
import feedback  # noqa: E402

ADMIN = {"X-Admin-Token": "secret"}
LOG = "\n".join(f"12:00:{i:02d} [fg] line {i}" for i in range(40))


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "ADMIN_TOKEN", "secret")
    return TestClient(appmod.app)


def _report(client, rid):
    return [r for r in client.get("/api/admin/feedback", headers=ADMIN).json()["reports"]
            if r["id"] == rid][0]


def test_a_report_from_the_player_keeps_the_players_log(client):
    rid = client.post("/api/v1/feedback", json={
        "text": "The first sentence was said twice.", "screen": "player",
        "build": "ios/1.0", "page": "ios", "viewport": "390x844",
        "episode": {"q": "did the eagles win", "minutes": 1.5, "id": ""},
        "playback": "phase playing · at 4s of 9s arrived\n" + LOG}).json()["id"]
    got = _report(client, rid)
    assert got["playback"].startswith("phase playing")
    assert "line 39" in got["playback"]
    assert got["episode"]["query"] == "did the eagles win"


def test_a_report_without_a_log_is_as_before(client):
    rid = client.post("/api/feedback", json={"text": "Tabs overlap"}).json()["id"]
    assert _report(client, rid)["playback"] == ""


def test_a_long_log_keeps_its_newest_lines_whole():
    long = "\n".join(f"line {i:06d} " + "x" * 60 for i in range(2000))
    kept = feedback.playback_tail(long)
    assert len(kept) <= feedback.MAX_PLAYBACK + 40
    assert kept.startswith("[... earlier lines cut]\nline ")
    assert kept.rstrip().endswith("line 001999 " + "x" * 60)
    assert feedback.playback_tail("short") == "short"


@pytest.mark.sqlite_file
def test_an_old_inbox_gains_the_playback_column(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE reports (id TEXT PRIMARY KEY, user_id TEXT NOT NULL DEFAULT '',
        text TEXT NOT NULL, screen TEXT NOT NULL DEFAULT '', build TEXT NOT NULL DEFAULT '',
        page TEXT NOT NULL DEFAULT '', viewport TEXT NOT NULL DEFAULT '',
        agent TEXT NOT NULL DEFAULT '', created REAL NOT NULL,
        resolved_at REAL NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '',
        episode TEXT NOT NULL DEFAULT '')""")
    conn.execute("INSERT INTO reports (id, text, created) VALUES ('a', 'old', 1)")
    conn.commit()
    conn.close()
    store = feedback.FeedbackStore(str(path))
    assert store.get("a")["playback"] == ""
    new = store.add("new", playback="one\ntwo")
    assert store.get(new["id"])["playback"] == "one\ntwo"


def test_the_inbox_draws_the_log_behind_a_button():
    page = (ROOT / "admin_ui" / "tracker.html").read_text()
    assert 'data-playback="' in page and "Playback log" in page
    assert "esc(r.playback)" in page, "the log is escaped, never drawn as HTML"


def test_both_players_offer_it_beside_report():
    web = (ROOT / "static" / "index.html").read_text()
    assert 'menuLabel("problem", "Report a problem")' in web
    sheets = (ROOT / "ios" / "FAM" / "FAM" / "Views" / "PlayerSheets.swift").read_text()
    assert 'row("Report a problem", PlayerIcons.alert) { open(.problem) }' in sheets
    assert "player.lines" in sheets, "the iPhone sends its player's log"
    api = (ROOT / "ios" / "FAM" / "FAM" / "API.swift").read_text()
    assert '"api/v1/feedback"' in api and '"playback": playback' in api
