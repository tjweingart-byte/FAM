"""Kept episodes move to staging whole, without their author (§172).

Staging cannot write a real episode, so replaying production's is how real
content gets onto it. The round trip must keep everything that makes the
episode play - script, title, audio - and drop the one field about a listener.
"""
from __future__ import annotations

import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import spend_guard  # noqa: E402
from cache import SqliteScriptCache  # noqa: E402

TOKEN = "replay-admin-token"


def _seed(cache: SqliteScriptCache) -> str:
    key = "tides-key"
    cache.put(key, ["Tides follow the moon.", "Twice a day."], 3600,
              query="how do tides work", minutes=2,
              author="listener-123", title="Why the sea breathes twice a day")
    cache.put_audio(key, "voice-a", 24000, b"\x01\x02" * 4000,
                    ["Tides follow the moon.", "Twice a day."], [0.0, 1.5])
    return key


def test_an_episode_survives_the_round_trip_without_its_author(tmp_path):
    source = SqliteScriptCache(str(tmp_path / "prod.db"))
    target = SqliteScriptCache(str(tmp_path / "staging.db"))
    key = _seed(source)
    data = source.export_episode(key)
    assert "author" not in data["script"]
    assert "listener-123" not in repr(data)
    target.import_episode(data, keep_until=time.time() + 3600)
    assert target.get(key) == source.get(key)
    assert target.title(key) == "Why the sea breathes twice a day"
    assert target.get_audio(key, "voice-a", 24000) is not None
    assert target.export_keys(5)[0]["audio"] is True
    assert target.export_episode(key)["script"].get("author", "") == ""


def test_an_import_keeps_the_episode_but_not_its_currency(tmp_path):
    """Kept long enough to test with; never made to look newer than it is."""
    source = SqliteScriptCache(str(tmp_path / "prod.db"))
    target = SqliteScriptCache(str(tmp_path / "staging.db"))
    key = _seed(source)
    data = source.export_episode(key)
    later = time.time() + 30 * 86400
    target.import_episode(data, keep_until=later)
    moved = target.export_episode(key)["script"]
    assert moved["expires"] >= later
    assert moved["sourced_at"] == data["script"]["sourced_at"]
    assert moved["fresh_until"] == data["script"]["fresh_until"]


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "ADMIN_TOKEN", TOKEN)
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", SqliteScriptCache(str(tmp_path / "c.db")))
    try:
        yield TestClient(appmod.app)
    finally:
        spend_guard.reset()


def test_the_endpoints_are_admin_only(client):
    assert client.get("/api/admin/episodes").status_code == 404
    assert client.get("/api/admin/episodes", headers={"X-Admin-Token": "wrong"}).status_code == 404


def test_production_refuses_an_import(client):
    """Production's cache is written by listening; only staging takes imports."""
    exported = {"script": {"key": "k", "sentences": "[]", "expires": 0, "created": 0}}
    response = client.post("/api/admin/episodes", json=exported,
                           headers={"X-Admin-Token": TOKEN})
    assert response.status_code == 409
    assert "zero-spend" in response.json()["error"]


def test_staging_takes_an_export_and_lists_it(client, tmp_path, monkeypatch):
    source = SqliteScriptCache(str(tmp_path / "prod.db"))
    key = _seed(source)
    monkeypatch.setitem(spend_guard._STATE, "enabled", True)
    headers = {"X-Admin-Token": TOKEN}
    response = client.post("/api/admin/episodes", json=source.export_episode(key),
                           headers=headers)
    assert response.status_code == 200 and response.json() == {"imported": key, "audio": 1}
    listed = client.get("/api/admin/episodes", headers=headers).json()["episodes"]
    assert [e["key"] for e in listed] == [key]
    assert client.get(f"/api/admin/episodes/{key}", headers=headers).json()["script"]["key"] == key
