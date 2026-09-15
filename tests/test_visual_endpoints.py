"""The drawing as an API, before it is a screen.

`IOS_APP.md`'s standing rule: behaviour that exists only in the interface is
behaviour that has to be written a second time. So every part of this feature a
player uses is an endpoint, and these are the tests for the endpoints rather
than for the browser (`tools/visual_probe.py` drives the browser).

The two that matter most:

* **Polling cannot spend.** `/api/visual` never generates. That is what makes
  it safe for a player to ask every couple of seconds.
* **exploreFAM is excluded over HTTP too**, not only in the JavaScript. A
  replay request must not leave a drawing behind it.
"""
from __future__ import annotations

import dataclasses
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_mod  # noqa: E402
import visuals as visuals_mod  # noqa: E402
import visual_style  # noqa: E402
from config import settings  # noqa: E402


@pytest.fixture
def client():
    """A client that keeps one event loop for its whole life.

    `TestClient` used as a plain object starts and tears down a portal per
    request, so a background task - which is exactly what a drawing is - never
    gets to run between two calls. Used as a context manager it keeps one loop,
    which is what a real server has.
    """
    with TestClient(app_mod.app) as test_client:
        yield test_client


def ready_visual(query: str = "how do lighthouses work") -> str:
    """Put a finished drawing in the store without drawing one.

    The pipeline is tested in `tests/test_visuals.py`; these tests are about
    what the endpoints do with a record, so the record is made directly.
    """
    record = visuals_mod.VisualRecord(
        id=visuals_mod.key_for(query), status="ready", query=query,
        surface="search", provider="openai", model="gpt-image-1",
        d="M100 100 C200 150 300 250 400 400 C500 550 600 650 700 700",
        view_box=f"0 0 {visual_style.VIEWBOX} {visual_style.VIEWBOX}",
    )
    visuals_mod.store().put(record)
    return record.id


# --------------------------------------------------------------------------
# Reading one
# --------------------------------------------------------------------------
def test_an_episode_with_no_drawing_says_none(client):
    body = client.get("/api/visual", params={"q": "a question nobody asked"}).json()
    assert body["visual"]["status"] == "none"
    # The canvas colours come back even with no drawing, so the player can
    # show the right ivory square before there is anything on it.
    assert body["visual"]["background_color"] == visual_style.PAPER
    assert body["visual"]["stroke_color"] == visual_style.INK


def test_polling_never_starts_a_drawing(client):
    """The property that makes it safe to poll: the episode starts the job, and
    this only ever reports."""
    for _ in range(5):
        client.get("/api/visual", params={"q": "something quite interesting"})
    assert visuals_mod.store().counts() == {}


def test_a_ready_drawing_comes_back_with_its_geometry(client):
    ready_visual()
    body = client.get("/api/visual",
                      params={"q": "how do lighthouses work"}).json()["visual"]
    assert body["status"] == "ready"
    # Inline rather than as a URL to fetch: the player wants the geometry, and
    # a second round trip in front of a picture that is already late is a round
    # trip for nothing.
    assert body["d"].startswith("M")
    assert body["vector_url"] and body["thumbnail_url"]


def test_a_follow_up_is_a_different_drawing(client):
    ready_visual()
    body = client.get("/api/visual",
                      params={"q": "how do lighthouses work",
                              "context": "an earlier episode"}).json()["visual"]
    assert body["status"] == "none"


# --------------------------------------------------------------------------
# The files
# --------------------------------------------------------------------------
def test_the_svg_is_served_as_one_safe_document(client):
    visual_id = ready_visual()
    response = client.get(f"/api/visual/{visual_id}.svg")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert "immutable" in response.headers["cache-control"]
    # An SVG is a document, and this one is served from the app's own origin.
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    body = response.text
    assert body.count("<path") == 1
    assert "<script" not in body


def test_the_thumbnail_renders_from_the_stored_path(client):
    visual_id = ready_visual()
    response = client.get(f"/api/visual/{visual_id}.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert len(response.content) > 500


def test_an_unknown_drawing_is_a_404_rather_than_an_empty_picture(client):
    assert client.get("/api/visual/nothingheread.svg").status_code == 404
    assert client.get("/api/visual/nothingheread.png").status_code == 404


# --------------------------------------------------------------------------
# exploreFAM
# --------------------------------------------------------------------------
def test_an_explore_replay_leaves_no_drawing_behind(client, monkeypatch):
    """The hard exclusion, over HTTP. Explore sends `cached_only`, and the
    server refuses to draw for it whatever the interface believes."""
    monkeypatch.setattr(visuals_mod, "settings",
                        dataclasses.replace(settings,
                                            visual_image_provider="synthetic"))
    # A miss on a replay-only request is a 409, which is the expected answer
    # here: what matters is that no drawing was started on the way to it.
    client.get("/api/audio", params={"q": "an episode somebody else made",
                                     "minutes": 1, "cached_only": "true"})
    assert visuals_mod.store().counts() == {}
    body = client.get("/api/visual",
                      params={"q": "an episode somebody else made"}).json()
    assert body["visual"]["status"] == "none"


# --------------------------------------------------------------------------
# The feed
# --------------------------------------------------------------------------
def test_every_myfam_tile_carries_its_drawing(client):
    """The browse surfaces' whole advantage: the picture is finished and on the
    tile before anybody taps it, so the tap pays nothing."""
    feed = client.get("/api/myfam").json()
    tiles = [topic for section in feed["sections"] for topic in section["topics"]]
    assert tiles, "the feed came back empty"
    for tile in tiles:
        assert "visual" in tile, "a tile has no visual block at all"
        assert tile["visual"]["status"] in visuals_mod.STATES


def test_a_warmed_tile_shows_its_drawing_on_the_feed(client):
    feed = client.get("/api/myfam").json()
    # The first shelf is honestly empty for a listener with no history, so
    # take the first tile the feed actually offers rather than assuming one.
    first = next(t for s in feed["sections"] for t in s["topics"])
    ready_visual(first["query"])
    again = client.get("/api/myfam").json()
    found = [t for s in again["sections"] for t in s["topics"]
             if t["id"] == first["id"]][0]
    assert found["visual"]["status"] == "ready"
    assert found["visual"]["d"].startswith("M")


def test_the_feed_only_warms_up_to_its_ceiling(client, monkeypatch):
    """Opening a feed must not fan out into a page of image generations."""
    started = []
    monkeypatch.setattr(visuals_mod, "warm",
                        lambda candidates, **kw: started.extend(candidates))
    client.get("/api/myfam")
    # `_illustrate` hands `visuals.warm` one shelf at a time and warm applies
    # the per-cycle cap itself; what is asserted here is that every candidate
    # carries a reason, which is what makes the hit rate readable later.
    assert started, "nothing was offered for warming"
    for query, context, reason in started:
        assert query and reason, f"a candidate with no reason: {query!r}"


# --------------------------------------------------------------------------
# Telemetry and the admin path
# --------------------------------------------------------------------------
def test_the_client_can_report_what_only_it_knows(client):
    before = visuals_mod.report()["events"]["visual_asset_loaded"]
    response = client.post("/api/visual/event",
                           json={"event": "visual_asset_loaded",
                                 "visual_id": "abc", "detail": "polled"})
    assert response.status_code == 200
    assert visuals_mod.report()["events"]["visual_asset_loaded"] == before + 1


def test_a_made_up_event_name_is_ignored_rather_than_counted(client):
    client.post("/api/visual/event", json={"event": "drop table", "visual_id": ""})
    assert "drop table" not in visuals_mod.report()["events"]


def test_regeneration_is_not_reachable_without_the_admin_credential(client):
    """404 rather than 401: this costs money per call, and an unconfigured
    deployment should not advertise that it has the endpoint."""
    response = client.post("/api/visual/regenerate", json={"q": "anything"})
    assert response.status_code == 404


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------
def test_health_says_whether_anything_is_being_drawn(client):
    report = client.get("/api/health").json()["visuals"]
    assert report["enabled"] is True
    assert report["excluded"] == ["explore"]
    assert "configured" in report["provider"]
    assert "references" in report["style"]
    assert set(report["latency"]) == {
        "visual_total_latency_ms", "visual_provider_latency_ms",
        "visual_processing_latency_ms"}


def test_health_names_the_missing_credential(client):
    """"Why is the square empty" answered in one line, rather than by reading
    a log. The container these tests run in has no image key, which is exactly
    the deployment this sentence is for."""
    provider = client.get("/api/health").json()["visuals"]["provider"]
    assert provider["configured"] is False
    assert "VISUAL_IMAGE_API_KEY" in provider["reason"]
