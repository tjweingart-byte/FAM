"""Where a shared link lands, at the HTTP level.

`/s/<id>` used to redirect into the web app, which handed a stranger the whole
product - search, myFAM, Explore, an account - when what they had been sent was
one episode. These tests are about the page that replaced it, and the one rule
it exists to keep: **the only control that works is play, and everything else
is a door to the App Store.**

The other half is the trace. There is no episode id in this product, and this
did not add one: a share row holds the question and the length, which are what
`pipeline.key_for` builds the cache key from, so following a link resolves the
sharer's own script out of the shared cache. `test_sharing.py` pins the key
itself; these pin the endpoints around it.
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import sharing  # noqa: E402


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "SHARES",
                        sharing.ShareStore(str(tmp_path / "shares.db")))
    return TestClient(appmod.app)


@pytest.fixture
def shared(client):
    """One shared episode, and its id."""
    response = client.post("/api/share", json={
        "query": "why is semiconductor manufacturing concentrated",
        "minutes": 3, "title": "Who Makes the Chips"})
    assert response.status_code == 200
    return response.json()["share"]["id"]


def _public(monkeypatch, base="https://fam.audio", store=""):
    import dataclasses

    import config

    monkeypatch.setattr(appmod, "settings", dataclasses.replace(
        config.settings, public_base_url=base, app_store_url=store))


# --- the page -------------------------------------------------------------

def test_a_shared_link_serves_a_page_rather_than_the_whole_app(client, shared):
    """The change this file is about. A redirect into `/` gave somebody who
    was sent one episode a search box, a browse page and a sign-up."""
    response = client.get(f"/s/{shared}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Who Makes the Chips" in response.text
    # Not a bounce into the app under any name.
    assert response.history == []


def test_the_page_can_play_and_says_what_it_is(client, shared):
    """A landing page that cannot play the episode is a poster for one."""
    page = client.get(f"/s/{shared}").text
    assert "fam-audio.js" in page
    assert "semiconductor manufacturing" in page
    assert "window.FAM_SHARE" in page


def test_a_dead_link_lands_on_a_page_that_says_so(client):
    """Rather than the front door of an app the person did not ask for. A
    crawler following a stale preview should get markup, not a bounce."""
    response = client.get("/s/no-such-share")
    assert response.status_code == 404
    assert "expired" in response.text.lower()


def test_serving_the_page_does_not_count_an_open(client, shared):
    """Facebook and LinkedIn *fetch* a shared link to build their preview
    card. Counting the serve would make the open count - the only number
    sharing produces - mostly crawlers."""
    client.get(f"/s/{shared}")
    client.get(f"/s/{shared}")
    assert appmod.SHARES.get(shared)["opens"] == 0


def test_the_page_reports_the_open_itself(client, shared):
    """What a crawler does not do is run the script. That is the whole
    mechanism, and it needs no list of user agents to maintain."""
    assert client.post(f"/api/share/{shared}/open").status_code == 200
    assert appmod.SHARES.get(shared)["opens"] == 1


def test_an_open_on_a_share_that_does_not_exist_is_a_404(client):
    assert client.post("/api/share/nope/open").status_code == 404


# --- the API behind it ----------------------------------------------------

def test_the_share_reads_back_without_an_account(client, shared):
    """A share link is public by construction - that is the point of sending
    one - so putting a sign-up in front of it would defeat the feature."""
    body = client.get(f"/api/share/{shared}").json()
    assert body["question"] == "why is semiconductor manufacturing concentrated"
    assert body["minutes"] == 3
    assert body["title"] == "Who Makes the Chips"


def test_reading_a_share_never_hands_back_a_listener_id(client, shared):
    """The endpoint version of the rule. The row has `user_id` sitting right
    next to the question, and this response goes to strangers."""
    body = client.get(f"/api/share/{shared}").json()
    assert "user_id" not in body
    assert "user" not in " ".join(body.keys())


def test_the_share_api_is_reachable_on_the_versioned_prefix(client, shared):
    """Every feature is an API before it is a screen: a universal link opening
    the iOS app has to resolve the same share the web page resolves."""
    assert client.get(f"/api/v1/share/{shared}").status_code == 200


def test_an_unknown_share_is_a_404_and_not_an_empty_episode(client):
    assert client.get("/api/share/no-such-share").status_code == 404


# --- what a recipient may do besides listen -------------------------------

def test_with_an_app_store_link_the_page_offers_it(client, shared, monkeypatch):
    _public(monkeypatch, store="https://apps.apple.com/app/fam/id1")
    body = client.get(f"/api/share/{shared}").json()
    assert body["has_app"] is True
    assert body["app_store"] == "https://apps.apple.com/app/fam/id1"


def test_with_no_app_store_link_there_is_nothing_to_press(client, shared,
                                                          monkeypatch):
    """Nothing invents a store URL, the same way nothing invents a host. A
    control with nothing behind it is worse than no control, and a stranger
    arriving from LinkedIn is the worst possible audience for one that 404s."""
    _public(monkeypatch, store="")
    body = client.get(f"/api/share/{shared}").json()
    assert body["has_app"] is False
    assert body["app_store"] == ""


# --- the preview other platforms build ------------------------------------

def test_the_page_carries_the_preview_facebook_and_linkedin_read(
        client, shared, monkeypatch):
    """Both drop everything except the URL and read the page itself. Until
    this existed, every FAM episode posted anywhere previewed identically."""
    _public(monkeypatch)
    page = client.get(f"/s/{shared}").text
    assert 'property="og:title"' in page
    assert "Who Makes the Chips" in page
    assert 'property="og:image"' in page
    assert "/api/share/card" in page


def test_no_public_host_means_no_image_is_promised(client, shared, monkeypatch):
    """A relative Open Graph image is fetched by a crawler on somebody else's
    server and resolves to nothing there."""
    _public(monkeypatch, base="")
    # Loopback, because that is now the only host this server refuses to name
    # - everywhere else it reads its own address off the request it was asked
    # through. See `app._public_base`.
    page = client.get(f"/s/{shared}", headers={"host": "localhost:8000"}).text
    assert 'property="og:image"' not in page


def test_health_says_whether_a_share_can_leave_this_machine(client):
    """Both settings fail in ways nobody sees from inside the app: a link that
    names no host is posted to LinkedIn as `/s/abc`, and a landing page with
    no store link draws no way to get FAM at all."""
    body = client.get("/api/health").json()["sharing"]
    assert set(body) >= {"public_base_url", "app_store_url", "landing_doors",
                         "targets"}
    assert len(body["targets"]) == len(sharing.TARGETS)
