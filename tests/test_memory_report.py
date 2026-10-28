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
