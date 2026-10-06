"""§207: quotas enforced in production, with the doors an owner needs.

`ENFORCE_QUOTAS=1` is set on the production service in `render.yaml` and
nowhere else, so the worst listener is bounded at the free tier's daily
ceiling rather than ~$2 a minute. There is still no checkout, so two things
come with it: admin accounts are `unlimited` by derivation (never by anything
the client says), and `POST /api/admin/plan` moves any other account between
plans. The plans sheet words itself from `/api/plans` - "everything is free"
is false the day limits are on.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import accounts as accounts_mod  # noqa: E402
import app as appmod  # noqa: E402


def _service(name: str) -> dict:
    services = yaml.safe_load((ROOT / "render.yaml").read_text())["services"]
    return next(s for s in services if s["name"] == name)


def _env(service: dict) -> dict:
    return {e["key"]: e.get("value") for e in service.get("envVars", [])}


def test_production_enforces_and_staging_does_not():
    """Enforced where strangers can reach it; the code default (and so local
    runs, tests and staging) stays off."""
    assert _env(_service("fam")).get("ENFORCE_QUOTAS") == "1"
    assert "ENFORCE_QUOTAS" not in _env(_service("fam-staging"))


def _request(listener):
    return SimpleNamespace(state=SimpleNamespace(listener=listener))


def test_an_admin_account_is_unlimited(monkeypatch):
    monkeypatch.setenv("FAM_ADMIN_ACCOUNTS", "owner@example.com")
    admin = accounts_mod.Listener("u-owner", email="owner@example.com",
                                  tier="free", has_account=True)
    other = accounts_mod.Listener("u-other", email="other@example.com",
                                  tier="free", has_account=True)
    assert appmod._tier(_request(admin)) == "unlimited"
    assert appmod._tier(_request(other)) == "free"


def test_a_guest_session_named_like_an_admin_is_not_one(monkeypatch):
    """Admin is decided against an account, so a bare session whose id
    happens to be listed - with no credentials behind it - gets nothing."""
    monkeypatch.setenv("FAM_ADMIN_ACCOUNTS", "u-guest")
    guest = accounts_mod.Listener("u-guest")
    assert appmod._tier(_request(guest)) == "free"
    assert appmod._tier(_request(None)) == "free"


@pytest.fixture
def admin_client(monkeypatch, tmp_path):
    store = accounts_mod.AccountStore(str(tmp_path / "accounts.db"))
    store.sign_up("u-tester", "tester@example.com", "correct horse battery")
    monkeypatch.setattr(appmod, "ACCOUNTS", store)
    monkeypatch.setattr(appmod, "ADMIN_TOKEN", "admin-secret")
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)
    with TestClient(appmod.app) as c:
        yield c, store


def _set(c, who, plan, token="admin-secret"):
    headers = {"x-admin-token": token} if token else {}
    return c.post("/api/admin/plan", json={"who": who, "plan": plan}, headers=headers)


def test_an_admin_moves_an_account_by_email_or_id(admin_client):
    c, store = admin_client
    r = _set(c, "Tester@Example.com", "plus")
    assert r.status_code == 200, r.text
    assert r.json() == {"user_id": "u-tester", "plan": "plus"}
    assert store.account("u-tester")["plan"] == "plus"
    assert _set(c, "u-tester", "unlimited").json()["plan"] == "unlimited"


def test_the_plan_endpoint_refuses_what_it_cannot_do(admin_client):
    c, _store = admin_client
    assert _set(c, "nobody@example.com", "plus").status_code == 404
    assert _set(c, "u-tester", "platinum").status_code == 400
    # Not an admin: the endpoint does not exist, as every admin endpoint.
    assert _set(c, "u-tester", "plus", token="").status_code == 404
    assert _set(c, "u-tester", "plus", token="wrong").status_code == 404


def test_the_plans_sheet_is_told_whether_limits_are_live(admin_client, monkeypatch):
    c, _store = admin_client
    body = c.get("/api/plans").json()
    assert body["checkout"] is False
    assert body["enforced"] is bool(appmod.settings.enforce_quotas)


def test_the_plans_note_is_worded_from_the_server():
    html = (ROOT / "static" / "index.html").read_text()
    assert "data.enforced === true" in html
    assert "Your daily allowance comes back every day." in html
    assert "Everything is free for now." in html
