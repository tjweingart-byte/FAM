"""The 29/09 packet (PROBLEMS.md §175): instant feedback, iMessage, Join FAM.

* **Instant feedback.** A button under the phone on the demo page files a bug
  report; `/admin` is the inbox, where each is resolved or opened again.
* **iMessage.** The share put a sentence in front of the link, so adding your
  own words meant scrolling back through it. The body is the link alone now,
  which Messages draws as the preview card with an empty typing line.
* **Join FAM.** A shared episode's page offers "Want to hear more? Join FAM
  for free" whether or not the app is out.
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import feedback  # noqa: E402
import sharing  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
ADMIN = {"X-Admin-Token": "secret"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "ADMIN_TOKEN", "secret")
    return TestClient(appmod.app)


# --- instant feedback: the store ------------------------------------------

def test_a_report_is_kept_open_until_resolved(tmp_path):
    store = feedback.FeedbackStore(str(tmp_path / "f.db"))
    report = store.add("Refresh showed the same tiles", screen="myfam")
    assert report["state"] == "open" and report["screen"] == "myfam"
    assert [r["id"] for r in store.list("open")] == [report["id"]]

    done = store.resolve(report["id"])
    assert done["state"] == "resolved" and done["resolved_at"] > 0
    assert store.list("open") == []
    # Resolving is a toggle, never a delete: the report is still there.
    assert [r["id"] for r in store.list("resolved")] == [report["id"]]
    assert store.counts() == {"open": 0, "resolved": 1}

    again = store.resolve(report["id"], False)
    assert again["state"] == "open" and again["resolved_at"] == 0


def test_an_empty_or_huge_report_is_refused_with_a_sentence(tmp_path):
    store = feedback.FeedbackStore(str(tmp_path / "f.db"))
    with pytest.raises(feedback.FeedbackError, match="empty"):
        store.add("   ")
    with pytest.raises(feedback.FeedbackError, match="longer"):
        store.add("x" * (feedback.MAX_TEXT + 1))


def test_one_listener_is_paced(tmp_path):
    store = feedback.FeedbackStore(str(tmp_path / "f.db"))
    for i in range(feedback.PER_LISTENER):
        store.add(f"bug {i}", throttle_key="anon_x", now=1000.0 + i)
    with pytest.raises(feedback.FeedbackError, match="a lot of reports"):
        store.add("one more", throttle_key="anon_x", now=1100.0)
    # Somebody else is not held up, and the window ends.
    store.add("theirs", throttle_key="anon_y", now=1100.0)
    store.add("later", throttle_key="anon_x",
              now=1000.0 + feedback.WINDOW_SECONDS + 60)


def test_everybody_together_is_capped_on_disk(tmp_path, monkeypatch):
    """The per-session pace is keyed on a cookie a script can drop; the
    ceiling counted from the table is the bound that holds."""
    monkeypatch.setattr(feedback, "GLOBAL_PER_WINDOW", 3)
    store = feedback.FeedbackStore(str(tmp_path / "f.db"))
    for i in range(3):
        store.add(f"bug {i}", throttle_key=f"anon_{i}", now=1000.0 + i)
    with pytest.raises(feedback.FeedbackError, match="no more reports this hour"):
        store.add("fourth", throttle_key="anon_new", now=1010.0)
    store.add("next hour", throttle_key="anon_new",
              now=1000.0 + feedback.WINDOW_SECONDS + 5)


def test_forgotten_sessions_do_not_pile_up_in_memory(tmp_path):
    store = feedback.FeedbackStore(str(tmp_path / "f.db"))
    for i in range(600):
        store._admit(f"anon_{i}", 1000.0)
    store._admit("late", 1000.0 + feedback.WINDOW_SECONDS + 1)
    assert len(store._stamps) < 600


def test_deleting_an_account_keeps_its_reports_without_the_id(tmp_path):
    store = feedback.FeedbackStore(str(tmp_path / "f.db"))
    report = store.add("The mic stayed on", user_id="acct_1")
    assert store.forget("acct_1") == 1
    kept = store.get(report["id"])
    assert kept is not None and kept["user_id"] == ""


# --- instant feedback: the endpoints --------------------------------------

def test_anybody_can_file_and_only_an_admin_can_read(client):
    sent = client.post("/api/feedback", json={
        "text": "Search now fired twice", "screen": "search",
        "build": "web/live", "viewport": "1280x800"})
    assert sent.status_code == 200, sent.text
    rid = sent.json()["id"]

    assert client.get("/api/admin/feedback").status_code == 404
    inbox = client.get("/api/admin/feedback", headers=ADMIN).json()
    assert [r["id"] for r in inbox["reports"]] == [rid]
    report = inbox["reports"][0]
    assert report["screen"] == "search"
    # The page's client label, and the code that answered it.
    assert report["build"].startswith("web/live @ ")
    # A guest is paced by session but never recorded (`app._remembers`).
    assert report["user_id"] == ""
    assert inbox["counts"] == {"open": 1, "resolved": 0}


def test_an_admin_resolves_and_reopens_from_the_inbox(client):
    rid = client.post("/api/feedback", json={"text": "Tile art missing"}).json()["id"]
    path = f"/api/admin/feedback/{rid}/resolve"
    assert client.post(path, json={}).status_code == 404

    done = client.post(path, json={"resolved": True}, headers=ADMIN).json()
    assert done["report"]["state"] == "resolved"
    assert done["counts"] == {"open": 0, "resolved": 1}
    assert client.get("/api/admin/feedback", headers=ADMIN).json()["reports"] == []
    resolved = client.get("/api/admin/feedback?state=resolved", headers=ADMIN).json()
    assert [r["id"] for r in resolved["reports"]] == [rid]

    back = client.post(path, json={"resolved": False}, headers=ADMIN).json()
    assert back["report"]["state"] == "open"
    assert client.post("/api/admin/feedback/nope/resolve", json={},
                       headers=ADMIN).status_code == 404


def test_an_empty_report_is_a_400_with_the_reason(client):
    r = client.post("/api/feedback", json={"text": "  "})
    assert r.status_code == 400 and "empty" in r.json()["error"]


def test_the_demo_page_draws_the_button_under_the_phone():
    page = (ROOT / "static" / "index.html").read_text()
    phone_end = page.index('<div class="demo-feedback">')
    assert page.index('<div class="phone">') < phone_end
    assert 'id="feedbackOpen"' in page and "Instant feedback" in page
    assert '"/api/feedback"' in page


def test_the_admin_page_has_the_inbox():
    page = (ROOT / "admin_ui" / "tracker.html").read_text()
    assert 'id="inboxList"' in page
    assert "/api/admin/feedback" in page and "/resolve" in page


# --- iMessage: the link and nothing else ----------------------------------

EPISODE = dict(title="Life's Possible Address on Enceladus",
               question="is there life on enceladus", minutes=2,
               url="https://fam.audio/s/LTPqFSU7lKMo")


def test_imessage_sends_only_the_link():
    """So Messages draws the preview card and leaves the typing line empty
    for the sender's own words, rather than a sentence with the cursor parked
    after a URL."""
    rendered = sharing.render("sms", **EPISODE)
    assert rendered["text"] == EPISODE["url"]
    assert rendered["destination"] == "sms:&body=" + \
        "https%3A%2F%2Ffam.audio%2Fs%2FLTPqFSU7lKMo"


def test_the_card_still_says_what_the_sentence_said():
    """What the old sentence carried - the title and the length - is on the
    card Messages builds from the page."""
    head = sharing.landing_head({"title": EPISODE["title"],
                                 "question": EPISODE["question"],
                                 "minutes": 2, "url": EPISODE["url"]})
    assert EPISODE["title"].replace("'", "&#x27;") in head
    assert "2-minute FAM episode" in head


# --- Join FAM for free ----------------------------------------------------

SHARE_ROW = {"id": "abc123", "query": "why bonds move", "minutes": 3,
             "title": "Bonds"}


def test_join_goes_to_the_app_store_when_there_is_one():
    payload = sharing.landing_payload(
        SHARE_ROW, url="/s/abc123", app_store="https://apps.apple.com/app/fam/id1")
    assert payload["join"] == "https://apps.apple.com/app/fam/id1"


def test_join_goes_to_the_front_door_when_there_is_no_app():
    """The front door opens on sign-up for anybody not signed in, on the host
    that served the landing page - a real page, never a dead control."""
    payload = sharing.landing_payload(SHARE_ROW, url="/s/abc123")
    assert payload["has_app"] is False
    assert payload["join"] == sharing.JOIN_PATH == "/"


def test_the_landing_page_offers_join_whether_or_not_the_app_is_out():
    page = (ROOT / "static" / "listen.html").read_text()
    assert "Want to hear more?" in page
    assert "Join FAM for free" in page
    assert 'data-door="join"' in page
    assert "share.join" in page
