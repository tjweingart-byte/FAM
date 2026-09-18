"""Saving and sharing, through the API."""
from __future__ import annotations

import json
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    with TestClient(appmod.app) as c:
        yield c


@pytest.fixture
def account(client):
    client.post("/api/auth/signup", json={"email": "a@b.com", "password": "password12"})
    return client.get("/api/auth/me").json()["user_id"]


# --- saving ---------------------------------------------------------------

def test_saving_says_it_is_saved_and_nothing_about_downloading(client, account):
    """Saving used to answer with the offline shelf's capacity, because it
    raised a popup asking whether to download the episode too. Pressing save
    saves; that is the whole of it."""
    body = client.post("/api/saved", json={
        "query": "why bonds move", "minutes": 3, "title": "Bonds"}).json()
    assert body["saved"] is True
    assert body["item"]["query"] == "why bonds move"
    assert "downloads" not in body
    assert "downloaded" not in body["item"]


def test_the_shelf_needs_an_account(client):
    """The settled boundary: an account gates what is *kept*. A saved episode
    is the definition of kept."""
    assert client.get("/api/saved").status_code == 401
    assert client.post("/api/saved", json={"query": "q", "minutes": 3}).status_code == 401


def test_folders_can_be_made_and_filtered_by(client, account):
    folder = client.post("/api/saved/folders", json={"name": "Commute"}).json()["folder"]
    client.post("/api/saved", json={"query": "in the folder", "minutes": 3,
                                    "folder_id": folder["id"]})
    client.post("/api/saved", json={"query": "unfiled", "minutes": 3})
    filtered = client.get(f"/api/saved?folder_id={folder['id']}").json()
    assert [i["query"] for i in filtered["items"]] == ["in the folder"]


def test_deleting_a_folder_keeps_its_episodes(client, account):
    folder = client.post("/api/saved/folders", json={"name": "Commute"}).json()["folder"]
    client.post("/api/saved", json={"query": "in the folder", "minutes": 3,
                                    "folder_id": folder["id"]})
    client.request("DELETE", f"/api/saved/folders/{folder['id']}")
    assert len(client.get("/api/saved").json()["items"]) == 1


# --- the toggle -----------------------------------------------------------

def test_the_save_control_can_ask_whether_it_is_lit(client, account):
    """A control that lights up has to be able to ask whether it is lit
    without pulling the whole shelf down to find out. Same shape as the vibe
    control's question, and for the same reason."""
    before = client.get("/api/saved?q=why+bonds+move&minutes=3").json()
    assert before == {"saved": False}
    client.post("/api/saved", json={"query": "why bonds move", "minutes": 3})
    after = client.get("/api/saved?q=why+bonds+move&minutes=3").json()
    assert after == {"saved": True}


def test_unsaving_takes_the_question_and_the_length_not_a_row_id(client, account):
    """The player knows the pair that is the script cache's key. Making it
    fetch a row id before it could un-press a button would put a round trip
    in front of the second tap that the first tap did not pay."""
    client.post("/api/saved", json={"query": "why bonds move", "minutes": 3})
    off = client.request("DELETE", "/api/saved?q=why+bonds+move&minutes=3")
    assert off.status_code == 200 and off.json() == {"ok": True, "saved": False}
    assert client.get("/api/saved").json()["items"] == []


def test_a_length_the_listener_did_not_press_save_on_stays_saved(client, account):
    client.post("/api/saved", json={"query": "q", "minutes": 3})
    client.post("/api/saved", json={"query": "q", "minutes": 10})
    client.request("DELETE", "/api/saved?q=q&minutes=3")
    assert [i["minutes"] for i in client.get("/api/saved").json()["items"]] == [10]


def test_the_download_endpoints_are_gone(client, account):
    """Removed rather than switched off, on the Piper reasoning: a route left
    behind is an invitation to draw a control for it again.

    Read off the route table rather than by calling them, because "what does
    a request to this path do" has several right answers (404, 405, whatever
    a catch-all decides) and only one of them is the question being asked.
    """
    paths = {getattr(route, "path", "") for route in appmod.app.routes}
    assert not [p for p in paths if "download" in p], \
        "a download route is still registered"


# --- sharing --------------------------------------------------------------

def test_sharing_works_without_an_account(client):
    """A share link is the cheapest route FAM has to a listener who does not
    have it yet. Putting a sign-up in front of the act of recommending it
    would be a strange way to grow, and nothing durable is being kept."""
    made = client.post("/api/share", json={
        "query": "why bonds move", "minutes": 3, "title": "Bonds"})
    assert made.status_code == 200, made.text
    assert made.json()["url"]


def test_a_share_carries_wording_for_every_destination(client):
    body = client.post("/api/share", json={
        "query": "why bonds move", "minutes": 3, "title": "Bonds"}).json()
    for key in ("sms", "email", "x", "facebook", "linkedin",
                "instagram_story", "snapchat_story"):
        assert body["targets"][key]["text"], key


def test_a_share_says_when_its_link_is_not_public_yet(client):
    """Without PUBLIC_BASE_URL the link works inside the app and nowhere else.
    A share posted to LinkedIn that resolves to localhost is the quiet failure
    this project keeps a rule about."""
    body = client.post("/api/share", json={"query": "q", "minutes": 3}).json()
    assert body["public"] is False
    assert body["url"].startswith("/s/")


def test_a_public_base_url_makes_the_link_absolute(client, monkeypatch):
    import dataclasses

    import config

    monkeypatch.setattr(appmod, "settings", dataclasses.replace(
        config.settings, public_base_url="https://fam.audio"))
    body = client.post("/api/share", json={"query": "q", "minutes": 3}).json()
    assert body["url"].startswith("https://fam.audio/s/")
    assert body["public"] is True


def test_the_story_card_is_an_image(client):
    """Instagram and Snapchat stories cannot carry a link as text. Without a
    card the listener shares a screenshot of a player UI."""
    body = client.post("/api/share", json={
        "query": "q", "minutes": 3, "title": "Bonds"}).json()
    card = client.get(body["card"])
    assert card.status_code == 200
    assert card.headers["content-type"].startswith("image/svg+xml")


def test_following_a_share_link_lands_on_the_episode_and_is_counted(client):
    body = client.post("/api/share", json={
        "query": "why bonds move", "minutes": 3}).json()
    hop = client.get(body["url"], follow_redirects=False)
    assert hop.status_code == 302
    assert "q=why" in hop.headers["location"]
    assert appmod.SHARES.get(body["share"]["id"])["opens"] == 1


def test_an_unknown_share_link_lands_in_the_app_rather_than_on_an_error(client):
    """Somebody followed a link a friend sent them. A 404 is a worse first
    impression of FAM than the home screen."""
    assert client.get("/s/nope", follow_redirects=False).status_code == 302


def test_the_targets_catalogue_says_which_need_a_picture(client):
    targets = {t["key"]: t for t in client.get("/api/share/targets").json()["targets"]}
    assert targets["instagram_story"]["needs_image"] is True
    assert targets["linkedin"]["needs_image"] is False


# --- deletion -------------------------------------------------------------

def test_deleting_an_account_clears_the_shelf_and_the_shares(client, account):
    client.post("/api/saved", json={"query": "q", "minutes": 3})
    client.post("/api/share", json={"query": "q", "minutes": 3})
    removed = client.request("DELETE", "/api/account").json()["removed"]
    assert removed["saved"] >= 1
    assert removed["shares"] >= 1
