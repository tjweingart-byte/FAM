"""§624: the post kit on /admin/groups - a link per community, its opens
counted, and the words that go with the photo or the clip."""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import communities as C  # noqa: E402
import sharing  # noqa: E402

EPISODE = {"query": "how do brain implants restore speech", "minutes": 2,
           "title": "Brain Implants Giving Paralyzed Patients a Voice", "match": 0.9}


def _lead(name: str) -> C.Lead:
    lead = C.Lead("facebook", name, name, C.community_url("facebook", name),
                  C.contact_url("facebook", name), "science", message="hi",
                  pitches=C.code_pitches(C.Lead("facebook", "e", "E", "u", "c", "science")))
    lead.episode = dict(EPISODE)
    return lead


@pytest.fixture
def admin(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_require_admin", lambda request: None)
    monkeypatch.setattr(appmod, "_admin_configured", lambda: True)
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "SHARES", sharing.ShareStore(str(tmp_path / "s.db")))
    monkeypatch.setattr(appmod, "_public_base", lambda request=None: "https://fam.audio")
    held = C.Store(str(tmp_path / "c.db"))
    C.reset(held)
    ids = []
    for name in ("scienceknowledge", "brainnews"):
        lead = _lead(name)
        held.add(lead, 1.0)
        ids.append(lead.id)
    return TestClient(appmod.app), held, ids


def _listen(client, lead_id: str) -> str:
    got = client.post("/api/admin/groups/listen", json={"id": lead_id})
    assert got.status_code == 200, got.text
    return got.json()["url"]


def test_two_communities_with_one_episode_get_two_links(admin):
    """One shared link counted every group together; a link each is what
    makes an open count mean something."""
    client, _held, (a, b) = admin
    first, second = _listen(client, a), _listen(client, b)
    assert first != second
    assert _listen(client, a) == first  # the same community keeps its link
    # Both play the same episode: the same question and length.
    rows = [appmod.SHARES.get(C.share_id_of(u)) for u in (first, second)]
    assert {(r["query"], r["minutes"]) for r in rows} == {(EPISODE["query"], 2)}


def test_opens_are_counted_per_community(admin):
    client, _held, (a, b) = admin
    first, _second = _listen(client, a), _listen(client, b)
    for _ in range(3):
        assert client.post(f"/api/share/{C.share_id_of(first)}/open").status_code == 200
    leads = {k["id"]: k for k in client.get("/api/admin/groups").json()["leads"]}
    assert leads[a]["opens"] == 3 and leads[a]["own_link"] is True
    assert leads[b]["opens"] == 0 and leads[b]["own_link"] is True


def test_a_link_made_before_this_says_it_is_shared(admin):
    client, held, (a, _b) = admin
    old = appmod.SHARES.create(C.SHARER, EPISODE["query"], 2, EPISODE["title"])
    held.listened(a, 5.0, f"https://fam.audio/s/{old['id']}")
    lead = next(k for k in client.get("/api/admin/groups").json()["leads"] if k["id"] == a)
    assert lead["own_link"] is False
    page = open(os.path.join(os.path.dirname(__file__), "..", "admin_ui", "groups.html")).read()
    assert "Listen again to give this community its own link" in page


def test_the_kit_has_the_link_the_photo_the_clip_and_the_words(admin, monkeypatch):
    client, _held, (a, _b) = admin

    async def hook(record):
        return "A chip reads the signals for speech."

    monkeypatch.setattr(appmod, "_share_hook", hook)
    assert client.get(f"/api/admin/groups/kit?id={a}").status_code == 409  # not heard yet
    link = _listen(client, a)
    kit = client.get(f"/api/admin/groups/kit?id={a}").json()
    share = C.share_id_of(link)
    assert kit["url"] == link and kit["own_link"] is True and kit["opens"] == 0
    assert kit["photo"].endswith(f"/api/share/{share}/post.jpg?download=1")
    assert kit["clip"] == f"/api/admin/share/{share}/clip.mp4"
    assert kit["first_comment"] == f"Listen to the full episode: {link}"
    assert kit["caption"] == (
        "Brain Implants Giving Paralyzed Patients a Voice\n\n"
        "A chip reads the signals for speech.\n\n"
        "A 2-minute listen from FAM. Link in the first comment.")
    # The caption never carries the link: a post that links out is held back.
    assert "http" not in kit["caption"]
    assert client.get("/api/admin/groups/kit?id=facebook:nobody").status_code == 404


def test_the_caption_is_composed_in_code():
    assert C.caption_for("Title", "", 1.5) == \
        "Title\n\nA 1.5-minute listen from FAM. Link in the first comment."
    assert C.share_id_of("https://fam.audio/s/abc-123_x?x=1") == "abc-123_x"
    assert C.share_id_of("") == ""
    assert C.sharer_for("facebook:x") != C.sharer_for("facebook:y")


def test_the_kit_is_admin_only(monkeypatch, tmp_path):
    client = TestClient(appmod.app)
    assert client.get("/api/admin/groups/kit?id=facebook:x").status_code in (401, 403, 404)


def test_the_card_has_the_kits_buttons():
    page = open(os.path.join(os.path.dirname(__file__), "..", "admin_ui", "groups.html")).read()
    for words in ("Copy caption", "Copy first comment", "/api/admin/groups/kit?id=",
                  "from this community’s own link"):
        assert words in page, words
