"""The 10.11 tweaks (PROBLEMS.md §251): light mode's white page and cream
tiles, YourFAM's View Your FAM pill, a group chat's photo, and the tab bar."""

from __future__ import annotations

import base64
import pathlib
import re
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import app as appmod  # noqa: E402
import messages as messages_mod  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text()
PHOTO = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff" + b"0" * 40).decode()


def _fn(name):
    return INDEX.split("function " + name + "(", 1)[1].split("\n  }\n", 1)[0]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    with TestClient(appmod.app) as c:
        yield c


def signed_in(client, email, name, handle):
    client.post("/api/auth/signup", json={"email": email, "password": "password12"})
    client.post("/api/me", json={"name": name, "handle": handle})
    return client.get("/api/auth/me").json()["user_id"]


# --- light mode ----------------------------------------------------------------

def test_light_mode_is_a_white_page_with_cream_tiles():
    light = re.search(r'html\[data-theme="light"\]\{[^}]*\}', INDEX).group(0)
    assert "--bg:#FFFFFF" in light and "--surface:#F7F3EA" in light
    assert "--bg-rgb:255,255,255" in light


# --- YourFAM -------------------------------------------------------------------

def test_yourfam_has_no_faces_row():
    # The pill went with the §341 hero: the friends number opens the same
    # Friends page, and the faces row stays DailyFAM's.
    body = _fn("renderProfile")
    assert "circleRowHTML()" not in body
    assert 'class="yf-stat" onclick="openFriends()"' in body
    assert "famPillHTML" not in INDEX


# --- a group's photo -----------------------------------------------------------

def test_a_member_sets_and_removes_a_group_photo(client):
    signed_in(client, "gp1@b.com", "Gil", "gil1")
    others, ids = [], []
    for n in (2, 3):
        c = TestClient(appmod.app)
        c.__enter__()
        ids.append(signed_in(c, f"gp{n}@b.com", f"G{n}", f"gg{n}"))
        others.append(c)
    for uid in ids:
        client.post("/api/friends/follow", json={"user_id": uid})
    gid = client.post("/api/messages/groups",
                      json={"user_ids": ids}).json()["group"]["user_id"]
    assert client.get("/api/messages/thread", params={"with": gid}).json()["with"]["avatar"] == ""

    # A remote picture is refused, as a profile picture's is.
    bad = client.post(f"/api/messages/groups/{gid}/photo",
                      json={"avatar": "https://example.com/x.jpg"})
    assert bad.status_code == 400

    made = client.post(f"/api/messages/groups/{gid}/photo", json={"avatar": PHOTO})
    assert made.status_code == 200, made.text
    assert made.json()["group"]["avatar"] == PHOTO
    # Every member sees it, in the chat and in their list, and is told.
    seen = others[0].get("/api/messages/thread", params={"with": gid}).json()
    assert seen["with"]["avatar"] == PHOTO
    assert seen["messages"][-1]["kind"] == "system"
    assert "photo" in seen["messages"][-1]["text"]
    row = [t for t in others[1].get("/api/messages").json()["threads"] if t["with"] == gid][0]
    assert row["avatar"] == PHOTO

    # Somebody not in it cannot.
    outsider = TestClient(appmod.app)
    outsider.__enter__()
    signed_in(outsider, "gp9@b.com", "Out", "out9")
    assert outsider.post(f"/api/messages/groups/{gid}/photo",
                         json={"avatar": PHOTO}).status_code == 404
    outsider.__exit__(None, None, None)

    gone = others[0].post(f"/api/messages/groups/{gid}/photo", json={"avatar": ""})
    assert gone.status_code == 200 and gone.json()["group"]["avatar"] == ""
    for c in others:
        c.__exit__(None, None, None)


@pytest.mark.sqlite_file
def test_an_old_groups_table_is_widened(tmp_path):
    import sqlite3
    path = str(tmp_path / "messages.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE groups (id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '',"
                 " created_by TEXT NOT NULL, at REAL NOT NULL)")
    conn.execute("INSERT INTO groups VALUES ('g:old', 'Old', 'a', 0)")
    conn.commit()
    conn.close()
    mail = messages_mod.MessageStore(path)
    assert mail.group("g:old")["avatar"] == ""


def test_the_chat_offers_library_camera_and_file():
    assert 'id="threadPhotoBtn" hidden onclick="openGroupPhotoMenu()"' in INDEX
    assert 'id="groupPhotoCamera" hidden accept="image/*" capture="environment"' in INDEX
    menu = _fn("openGroupPhotoMenu")
    for label in ("Choose from library", "Take a photo", "Choose a file", "Remove photo"):
        assert label in menu
    assert "photoBtn.hidden = !person.group" in _fn("paintThreadHead")
    assert "/photo" in _fn("saveGroupPhoto")


# --- the tab bar ---------------------------------------------------------------

def test_messages_is_capitalised_on_every_tab_bar():
    assert '<div class="lbl">messages</div>' not in INDEX
    assert INDEX.count('<div class="lbl">Messages</div>') == INDEX.count('<div class="tabbar">')


def test_the_tab_icons_are_toned_and_spring():
    assert ".tabbar .tab .dot .tone{" in INDEX
    assert "@keyframes tabPop" in INDEX
    # One tint per tab, two on YourFAM's (head and shoulders), on every copy.
    assert INDEX.count('class="tone"') == 6 * INDEX.count('<div class="tabbar">')


def test_a_reviewer_takes_a_reported_group_photo_down(client, monkeypatch):
    """A group's photo is content everybody in it sees, so "removed" on a
    reported group takes it down; a group with no photo is still not
    content to remove."""
    monkeypatch.setattr(appmod, "_require_admin", lambda request: None)
    signed_in(client, "rg1@b.com", "Rae", "rae1")
    others, ids = [], []
    for n in (2, 3):
        c = TestClient(appmod.app)
        c.__enter__()
        ids.append(signed_in(c, f"rg{n}@b.com", f"R{n}", f"rr{n}"))
        others.append(c)
    for uid in ids:
        client.post("/api/friends/follow", json={"user_id": uid})
    gid = client.post("/api/messages/groups",
                      json={"user_ids": ids}).json()["group"]["user_id"]

    def report():
        return others[0].post("/api/report", json={
            "kind": "group", "target": gid, "reason": "hate"}).json()["id"]

    bare = report()
    assert client.post(f"/api/admin/reports/{bare}/resolve",
                       json={"action": "removed"}).status_code == 400

    client.post(f"/api/messages/groups/{gid}/photo", json={"avatar": PHOTO})
    rid = report()
    r = client.post(f"/api/admin/reports/{rid}/resolve", json={"action": "removed"})
    assert r.status_code == 200, r.text
    assert others[1].get("/api/messages/thread",
                         params={"with": gid}).json()["with"]["avatar"] == ""
    for c in others:
        c.__exit__(None, None, None)


def test_a_refused_photo_shows_the_servers_sentence():
    assert "d.error || d.detail" in _fn("saveGroupPhoto")
