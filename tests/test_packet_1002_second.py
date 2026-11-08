"""The 10.2 implementations packet (PROBLEMS.md §195): friends at the top of
the rails screen, the chevron as the icon, Explore turning the whole page,
"Interested?", and Explore from search."""

from __future__ import annotations

import json
import pathlib
import re
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import app as appmod  # noqa: E402
import topics as T  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
INDEX = (STATIC / "index.html").read_text()


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


# --- 1. the friends row at the top of the rails screen ----------------------

def test_a_guest_gets_no_faces(client):
    assert client.get("/api/circle").json() == {"circle": []}


def test_the_circle_is_the_profiles_row(client):
    me = signed_in(client, "ian2@b.com", "Ian", "ian2")
    other = TestClient(appmod.app)
    with other:
        them = signed_in(other, "di2@b.com", "Di", "di2")
        other.post("/api/friends/follow", json={"user_id": me})
        other.post("/api/vibe", json={"query": "why bonds move", "minutes": 3,
                                      "title": "Bonds"})
    client.post("/api/friends/follow", json={"user_id": them})
    row = client.get("/api/circle").json()["circle"]
    assert [p["handle"] for p in row] == ["di2"]
    assert row[0]["stories"], "a vibe did not come with the face"
    assert row == client.get("/api/profile").json()["circle"]


def test_the_rails_screen_draws_the_same_row_first():
    screen = INDEX.split('id="screen-myfam"', 1)[1].split("</section>", 1)[0]
    scroll = screen.split('<div class="scroll">', 1)[1]
    assert scroll.index('id="myfamCircle"') < scroll.index('id="goDeeperBlock"')
    # YourFAM gave its copy up (10.11 packet; its pill went in §341).
    assert "circleRowHTML()" not in _fn("renderProfile")
    assert "circleRowHTML()" in _fn("paintMyFamCircle")
    # Asked beside the rails, not before them.
    feed = _fn("loadMyFamFeed")
    assert feed.index("loadMyFamCircle()") < feed.index('fetch(withInterests("/api/myfam')


def test_the_viewer_reads_a_face_by_its_index_not_its_place():
    """Two rows on the page: the k-th button is not the k-th person."""
    assert 'data-i="' in _fn("circleRowHTML")
    assert 'getAttribute("data-i")' in _fn("closeStory")


# --- 2. the chevron is the icon ---------------------------------------------

def test_the_icon_is_the_wordmarks_chevron():
    svg = (STATIC / "icon.svg").read_text()
    mark = INDEX.split('<span class="wm-a"', 1)[1].split("</span>", 1)[0]
    for path in re.findall(r'<path d="([^"]+)"', mark):
        assert f'd="{path}"' in svg
    for name in ("favicon.ico", "apple-touch-icon.png", "icon-192.png", "icon-512.png"):
        assert (STATIC / name).stat().st_size > 0, name
    manifest = json.loads((STATIC / "manifest.webmanifest").read_text())
    assert {i["src"] for i in manifest["icons"]} >= {"/icon-192.png", "/icon-512.png"}


@pytest.mark.parametrize("page", ["index.html", "waitlist.html", "listen.html"])
def test_every_page_names_the_icon(page):
    head = (STATIC / page).read_text().split("</head>", 1)[0]
    assert '<link rel="icon" href="/icon.svg"' in head
    assert '<link rel="apple-touch-icon" href="/apple-touch-icon.png">' in head


def test_the_icon_is_served(client):
    for path in ("/favicon.ico", "/icon.svg", "/apple-touch-icon.png",
                 "/manifest.webmanifest"):
        assert client.get(path).status_code == 200, path


# --- 3. Explore turns the whole page ----------------------------------------

# --- 4. Interested? ---------------------------------------------------------

# --- 5. Explore from search -------------------------------------------------
