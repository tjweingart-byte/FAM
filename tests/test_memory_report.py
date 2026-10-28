"""The admin memory report (§331, memory_report.py)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import memory_report  # noqa: E402

#: A global big enough to be listed, held by a module of this project.
memory_report._TEST_BALLAST = [bytes(4096) for _ in range(600)]


def test_the_report_measures_and_trims():
    out = memory_report.report(trim_first=True, census=False)
    assert out["rss_mb"] and out["rss_mb"] > 0
    assert out["trim"]["rss_after_mb"] is not None
    assert "holders" not in out


def test_the_census_names_a_large_global():
    found = memory_report.holders(top=50)
    names = {h["name"]: h["mb"] for h in found["largest"]}
    assert names.get("memory_report._TEST_BALLAST", 0) >= 2
    assert found["objects_walked"] > 0


def test_the_census_stops_at_its_bound():
    found = memory_report.holders(max_objects=50)
    assert found["complete"] is False and found["objects_walked"] <= 50


def test_only_an_admin_may_ask(monkeypatch):
    from fastapi.testclient import TestClient
    import app as appmod

    monkeypatch.setattr(appmod, "ADMIN_TOKEN", "secret")
    client = TestClient(appmod.app)
    assert client.get("/api/admin/memory").status_code == 404
    got = client.get("/api/admin/memory", headers={"x-admin-token": "secret"})
    assert got.status_code == 200 and "trim" in got.json()


def test_the_report_counts_every_arena_and_names_threads():
    out = memory_report.report(trim_first=False)
    assert "arenas" in out["all_arenas"] and out["all_arenas"]["arenas"] >= 1
    assert any(t["name"] == "MainThread" for t in out["thread_names"])


def test_the_watch_reports_each_line_once(monkeypatch):
    monkeypatch.setattr(memory_report, "_CROSSED", set())
    monkeypatch.setattr(memory_report, "SNAPSHOTS", [])
    monkeypatch.setattr(memory_report, "holders", lambda: {"largest": []})
    assert memory_report.watch_step(now=1, rss=500) is None
    first = memory_report.watch_step(now=2, rss=900)
    assert first and first["crossed_mb"] == 800
    assert memory_report.watch_step(now=3, rss=950) is None
    # A jump past two lines reports once, at the higher one.
    assert memory_report.watch_step(now=4, rss=1700)["crossed_mb"] == 1600
    assert memory_report.watch_state()["crossed_mb"] == [800, 1200, 1600]
    assert len(memory_report.watch_state()["snapshots"]) == 2


def test_the_image_sets_two_arenas():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert "ENV MALLOC_ARENA_MAX=2" in open(os.path.join(here, "Dockerfile")).read()


def test_a_jump_names_what_was_running(monkeypatch):
    import time as _t
    monkeypatch.setattr(memory_report, "JUMPS", memory_report.collections.deque(maxlen=10))
    monkeypatch.setattr(memory_report, "IN_FLIGHT", {})
    monkeypatch.setattr(memory_report, "FINISHED", memory_report.collections.deque(maxlen=300))
    since = _t.time() - 1
    memory_report.request_started(1, "GET /api/myfam")
    memory_report.request_started(2, "GET /api/explore")
    memory_report.request_ended(2)
    tasks = [{"task": "_sweep_audio_forever", "count": 1}]
    assert memory_report.jump_step(400.0, 450.0, since, tasks) is None
    jump = memory_report.jump_step(400.0, 1900.0, since, tasks)
    assert jump["to_mb"] == 1900.0
    assert [r["request"] for r in jump["in_flight"]] == ["GET /api/myfam"]
    assert [r["request"] for r in jump["finished"]] == ["GET /api/explore"]
    assert jump["tasks"] == tasks
    assert memory_report.watch_state()["jumps"][-1] is jump


def test_every_request_is_tracked_and_released(monkeypatch):
    from fastapi.testclient import TestClient
    import app as appmod

    monkeypatch.setattr(memory_report, "IN_FLIGHT", {})
    monkeypatch.setattr(memory_report, "FINISHED", memory_report.collections.deque(maxlen=300))
    TestClient(appmod.app).get("/api/health")
    assert memory_report.IN_FLIGHT == {}
    assert any(f[1] == "GET /api/health" for f in memory_report.FINISHED)
