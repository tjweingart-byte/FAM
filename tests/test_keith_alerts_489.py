"""Keith's alerts, piece 3c: the pop-up on every admin page, "I fixed it",
and urgent email (§489; the design the owner approved 2026-11-06).

Pinned: only an admin reads or answers the alerts; "I fixed it" is checked,
never believed - a state closes only when a re-read no longer finds it, an
error kind only after half an hour with none of it, and said when it comes
back; an urgent incident is emailed once to IT_ALERT_EMAIL (again only if it
reopens), never without the address or a working sender; every admin page
includes the script; and the pop-up, run in a browser, shows the alerts with
their plans, re-checks on "I fixed it" and closes for the page view only.
"""
from __future__ import annotations

import dataclasses
import json
import pathlib

import pytest
from fastapi.testclient import TestClient

import incidents as INC
import mail
import pipeline_errors as PE
import provider_usage
import usage_monitors as UM

ROOT = pathlib.Path(__file__).resolve().parent.parent
T0 = 1_790_000_000.0
ADMIN = {"X-Admin-Token": "sekrit"}


@pytest.fixture
def stores(tmp_path):
    PE.reset(PE.ErrorStore(str(tmp_path / "e.db")))
    INC.reset(INC.IncidentStore(str(tmp_path / "e.db")))
    provider_usage.reset(provider_usage.UsageStore(str(tmp_path / "u.db")))
    yield
    INC.reset()


def errors(n, at, kind="timeout"):
    PE.store().add([PE.row("brief", kind, "slow", "q", PE.ERROR, at=at - i) for i in range(n)],
                   now=at)


def disk(monkeypatch, level):
    monkeypatch.setattr(UM, "report", lambda *a, **k: {"services": [{
        "service": "render", "ok": True, "figures": [] if level == "ok" else [{
            "name": "fam-db storage", "value": 0.97, "unit": "GB", "limit": 1.0,
            "share": 0.97, "projected": None, "level": level,
            "limit_source": "its disk", "next": "grow the disk"}]}]})


def email_to(monkeypatch, address="admin@familiarize.net", sends=True):
    monkeypatch.setattr(INC, "_settings", lambda: dataclasses.replace(
        __import__("config").settings, it_alert_email=address))
    monkeypatch.setattr(mail, "status", lambda: {"available": True, "reason": ""})
    sent = []
    monkeypatch.setattr(mail, "send", lambda to, subject, body: sent.append(
        (to, subject, body)) or sends)
    return sent


# ------------------------------------------------------------- "I fixed it"
def test_a_state_closes_only_when_the_re_check_no_longer_finds_it(stores, monkeypatch):
    disk(monkeypatch, "urgent")
    INC.watch(T0)
    said = INC.owner_fixed("usage:render:fam-db storage", T0 + 60)
    assert said["state"] == "still_open" and "still there" in said["message"]
    row = INC.store().get("usage:render:fam-db storage")
    assert row["closed_at"] is None and "after you said" in row["detail"]["after_your_fix"]
    disk(monkeypatch, "ok")
    said = INC.owner_fixed("usage:render:fam-db storage", T0 + 120)
    assert said["state"] == "closed"
    assert "re-check no longer finds it" in INC.store().get("usage:render:fam-db storage")["closed_why"]


def test_an_error_kind_said_fixed_closes_after_half_an_hour_with_none(stores):
    errors(4, T0)
    INC.watch(T0)
    assert INC.owner_fixed("errors:brief:timeout", T0 + 60)["state"] == "watching"
    assert INC.watch(T0 + 600)["closed"] == []                  # ten minutes: not yet
    done = INC.watch(T0 + 60 + INC.CLAIM_QUIET_SECONDS)
    assert done["closed"] == ["errors:brief:timeout"]
    assert "saw none" in INC.store().get("errors:brief:timeout")["closed_why"]


def test_an_error_after_you_said_fixed_is_said_and_keeps_it_open(stores):
    errors(4, T0)
    INC.watch(T0)
    INC.owner_fixed("errors:brief:timeout", T0 + 60)
    errors(1, T0 + 300)
    INC.watch(T0 + 300)
    row = INC.store().get("errors:brief:timeout")
    assert row["closed_at"] is None and row["fixed_claim_at"] is None
    assert "still happening after you said" in row["detail"]["after_your_fix"]
    assert INC.watch(T0 + 60 + INC.CLAIM_QUIET_SECONDS)["closed"] == []


# ------------------------------------------------------------------- email
def test_an_urgent_incident_is_emailed_once_with_its_plan(stores, monkeypatch):
    sent = email_to(monkeypatch)
    disk(monkeypatch, "urgent")
    assert INC.watch(T0)["emailed"] == ["usage:render:fam-db storage"]
    INC.watch(T0 + 300)
    assert len(sent) == 1
    to, subject, body = sent[0]
    assert to == "admin@familiarize.net" and subject.startswith("[FAM urgent]")
    assert "Recommended: Move up before the limit: grow the disk" in body
    assert "Needed from you:" in body
    assert INC.store().get("usage:render:fam-db storage")["emailed_at"] == T0


def test_a_reopened_incident_is_emailed_again_and_a_warning_never(stores, monkeypatch):
    sent = email_to(monkeypatch)
    disk(monkeypatch, "urgent")
    INC.watch(T0)
    disk(monkeypatch, "ok")
    INC.watch(T0 + 300)
    disk(monkeypatch, "urgent")
    INC.watch(T0 + 600)
    assert len(sent) == 2
    disk(monkeypatch, "warn")
    INC.store().clear()
    INC.watch(T0 + 900)
    assert len(sent) == 2


def test_no_address_or_no_sender_sends_nothing_and_a_failed_send_tries_again(stores, monkeypatch):
    sent = email_to(monkeypatch, address="")
    disk(monkeypatch, "urgent")
    INC.watch(T0)
    assert sent == [] and INC.email_ready()["why"] == "IT_ALERT_EMAIL is not set"
    INC.store().clear()
    sent = email_to(monkeypatch, sends=False)
    INC.watch(T0)
    INC.watch(T0 + 300)
    assert len(sent) == 2                                      # failed, tried again
    assert INC.store().get("usage:render:fam-db storage")["emailed_at"] is None
    monkeypatch.setattr(mail, "status", lambda: {"available": False,
                                                 "reason": "This is the staging server"})
    assert INC.email_ready()["ready"] is False


# --------------------------------------------------------------- the routes
@pytest.fixture
def admin(monkeypatch, stores):
    import app as appmod

    monkeypatch.setattr(appmod, "ADMIN_TOKEN", "sekrit")
    return TestClient(appmod.app)


def test_only_an_admin_reads_or_answers_the_alerts(admin):
    assert admin.get("/api/admin/incidents").status_code == 404
    assert admin.post("/api/admin/incidents/fixed", json={"cause": "x"}).status_code == 404
    body = admin.get("/api/admin/incidents", headers=ADMIN).json()
    assert body["open"] == [] and "ready" in body["email"]
    said = admin.post("/api/admin/incidents/fixed", json={"cause": "x"}, headers=ADMIN).json()
    assert said["state"] == "closed"


def test_every_admin_page_includes_the_script_and_it_is_served(admin):
    # `/admin/x` went with the X agent; `/admin/groups` replaced it (§578).
    for page in ("tracker", "thumbnails", "waitlist", "groups"):
        html = (ROOT / "admin_ui" / f"{page}.html").read_text()
        assert '<script src="/admin/keith-alerts.js" defer></script>' in html
    served = admin.get("/admin/keith-alerts.js")
    assert served.status_code == 200
    assert served.headers["content-type"] == "application/javascript; charset=utf-8"
    assert "/api/admin/incidents" in served.text


# ------------------------------------------------------------ in a browser
OPEN = {"open": [
    {"cause": "usage:render:fam-db storage", "source": "usage", "severity": "urgent",
     "title": "render: fam-db storage at 97% of 1.0 GB", "opened_at": T0, "last_seen": T0,
     "seen": 12, "reopened": 0, "emailed_at": T0, "fixed_claim_at": None, "detail": {},
     "plan": {"problem": "fam-db storage is 0.97 GB against 1.0 GB",
              "recommended": "grow the disk", "needs_from_owner": "your decision"}},
    {"cause": "errors:brief:timeout", "source": "errors", "severity": "warn",
     "title": "The brief took too long: 5 in the last hour", "opened_at": T0,
     "last_seen": T0, "seen": 3, "reopened": 0, "emailed_at": None,
     "fixed_claim_at": None, "detail": {}, "plan": {"problem": "slow"}}],
    "closed": [], "counts": {"urgent": 1, "warn": 1},
    "email": {"ready": True, "to": "admin@familiarize.net", "why": ""},
    # §490: the fix report beside the alerts.
    "fix_report": {"enabled": [], "available": ["hold_agent", "rerun_job", "reverify_voice"],
                   "fixes": [{"at": T0, "cause": "errors:voice:no worker", "opened_at": T0,
                              "fix": "reverify_voice", "mode": "observe",
                              "outcome": "would", "how": "would re-verify the voice ladder now"}]}}


def test_the_pop_up_in_a_browser():
    playwright = pytest.importorskip("playwright.sync_api")
    script = (ROOT / "admin_ui" / "keith_alerts.js").read_text()
    posted = []
    with playwright.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:  # pragma: no cover - no browser here
            pytest.skip(f"no browser: {exc}")
        page = browser.new_page()

        def route(r):
            url = r.request.url
            if url.endswith("/admin"):
                r.fulfill(content_type="text/html; charset=utf-8",
                          body="<html><head><meta charset='utf-8'></head><body><h1>Live tracker</h1>"
                               "<script src='/admin/keith-alerts.js'></script></body></html>")
            elif url.endswith("/admin/keith-alerts.js"):
                r.fulfill(content_type="application/javascript; charset=utf-8", body=script)
            elif url.endswith("/api/admin/incidents/fixed"):
                posted.append(json.loads(r.request.post_data))
                r.fulfill(content_type="application/json",
                          body=json.dumps({"state": "still_open",
                                           "message": "Keith re-checked: it is still there."}))
            elif url.endswith("/api/admin/incidents"):
                r.fulfill(content_type="application/json", body=json.dumps(OPEN))
            else:
                r.fulfill(status=404, body="")

        page.route("**/*", route)
        page.goto("http://fam.test/admin")
        page.wait_for_selector("#keith-alerts")
        assert page.inner_text("#keith-title") == "Keith: 2 open alerts"
        items = page.query_selector_all(".keith-item")
        assert [i.get_attribute("data-cause") for i in items] == [
            "usage:render:fam-db storage", "errors:brief:timeout"]
        # The urgent one's plan is open; the warning's is behind a button.
        plans = page.query_selector_all(".keith-plan")
        assert plans[0].is_visible() and not plans[1].is_visible()
        assert "grow the disk" in plans[0].inner_text()
        assert "emailed to admin@familiarize.net" in page.inner_text("#keith-alerts")
        # "I fixed it" asks Keith, and says what he found.
        items[0].query_selector(".keith-fixed").click()
        page.wait_for_function("document.querySelector('.keith-said').textContent"
                               ".includes('still there')")
        assert posted == [{"cause": "usage:render:fam-db storage"}]
        # The fix report (§490): behind its button, saying what is off.
        assert not page.is_visible(".keith-fix-report")
        page.click(".keith-fix-btn")
        report = page.inner_text(".keith-fix-report")
        assert "off - would" in report and "none - Keith observes only" in report
        # Closing hides it for this page view; the badge stays, and brings it back.
        page.click(".keith-close")
        assert page.query_selector("#keith-alerts") is None
        assert page.inner_text("#keith-badge") == "⚑ 2 open alerts"
        page.click("#keith-badge")
        page.wait_for_selector("#keith-alerts")
        browser.close()
