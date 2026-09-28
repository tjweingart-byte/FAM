"""Tile pictures: one per branch of the category tree (§160, THUMBNAILS.md).

What these pin, in the order it would hurt to lose them:

* a tile gets the **deepest** branch's picture, and a branch still waiting for
  a person falls back to its parent - never to nothing, never to itself;
* the read path never opens a database and never calls anything;
* a picture the checker flags is never stored as live, however many times it
  is painted, and an unchecked picture waits for a person;
* people are switched off at the model, and a flagged subject's name never
  reaches the image prompt.
"""
from __future__ import annotations

import asyncio
import base64
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import categories as C  # noqa: E402
import thumbnails as th  # noqa: E402
import topics as T  # noqa: E402
import dataclasses  # noqa: E402

import config  # noqa: E402


def _png(color=(200, 120, 80), size=(1024, 768)) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "PNG")
    return out.getvalue()


def _set(monkeypatch, **values):
    monkeypatch.setattr(config, "settings",
                        dataclasses.replace(config.settings, **values))


CLEAN = {"logo": False, "text": False, "person": False,
         "identifiable_product": False, "matches_subject": True, "notes": ""}


@pytest.fixture
def tree():
    t = T.category_tree()
    C.apply_seed(t)
    return t


def _put(node, status=th.STATUS_APPROVED, facet="sports"):
    th.store().put(th.Thumb(node, status, facet=facet, mime="image/png"),
                   b"img-" + node.encode())


def _writer(flagged=()):
    async def write(paths):
        return [th.Scene(p.split(" > ")[-1],
                         f"a quiet scene about the subject number {i}",
                         p.split(" > ")[-1] in flagged)
                for i, p in enumerate(paths)]
    return write


def _painter(results):
    """Returns each queued result in turn; an exception is raised."""
    queue = list(results)
    calls = []

    async def paint(prompt):
        calls.append(prompt)
        item = queue.pop(0) if queue else th.Painting(_png(), "image/png")
        if isinstance(item, Exception):
            raise item
        return item
    paint.calls = calls
    return paint


def _checker(answers):
    queue = list(answers)

    async def check(image, mime, subject):
        item = queue.pop(0) if queue else CLEAN
        if isinstance(item, Exception):
            raise item
        return dict(item), 0.001
    return check


# --- the read path ---------------------------------------------------------


def test_no_store_means_no_picture_and_no_file(tree):
    assert th.pick("how college football money changed", ("sports",)) is None
    assert not os.path.exists(os.environ["THUMBNAILS_DB"])


def test_the_deepest_branch_with_a_picture_wins(tree):
    _put("sports")
    _put("american football")
    _put("college football")
    got = th.pick("how college football money changed", ("sports",))
    assert got["node"] == "college football"
    assert got["facet"] == "sports"
    assert got["url"].startswith("/api/thumb/college%20football?v=")


def test_a_branch_waiting_for_review_falls_back_to_its_parent(tree):
    _put("sports")
    _put("american football")
    _put("college football", status=th.STATUS_REVIEW)
    assert th.pick("how college football money changed",
                   ("sports",))["node"] == "american football"


def test_nothing_in_the_tree_falls_back_to_the_declared_facet(tree):
    _put("tech", facet="tech")
    got = th.pick("a question the tree knows nothing about", ("tech", "culture"))
    assert got == {"node": "tech", "facet": "tech",
                   "url": got["url"]}


def test_a_rejected_picture_leaves_tiles_at_once(tree):
    _put("college football")
    assert th.pick("college football", ())["node"] == "college football"
    th.store().set_status("college football", th.STATUS_REJECTED)
    assert th.pick("college football", ()) is None


def test_a_repainted_picture_is_a_new_url(tree):
    _put("college football")
    first = th.pick("college football", ())["url"]
    th.store().put(th.Thumb("college football", th.STATUS_APPROVED,
                            facet="sports", mime="image/png",
                            updated_at=th.store().get("college football").updated_at + 5),
                   b"new")
    assert th.pick("college football", ())["url"] != first


def test_a_tile_carries_its_picture_and_the_facet_word(tree):
    topic = next(t for t in T.TOPIC_BANK if "sports" in t.tags)
    assert topic.as_dict()["thumb"] == ""
    _put("sports")
    d = topic.as_dict()
    assert d["thumb"].startswith("/api/thumb/")
    assert d["thumb_facet"]


# --- painting --------------------------------------------------------------


def _run(**kw):
    return asyncio.run(th.backfill(kw.pop("limit", 50), **kw))


def test_a_clean_picture_goes_live_and_is_stored_small(tree):
    result = _run(only=["college football"], writer=_writer(),
                  painter=_painter([]), checker=_checker([]))
    assert result["approved"] == 1
    row = th.store().get("college football")
    assert row.status == th.STATUS_APPROVED and row.facet == "sports"
    data, mime = th.store().image("college football")
    assert mime == "image/webp"
    from PIL import Image
    assert Image.open(io.BytesIO(data)).size == (th.STORED_WIDTH, th.STORED_HEIGHT)


def test_a_real_named_subject_waits_for_a_person(tree):
    _run(only=["formula one"], writer=_writer(flagged={"formula one"}),
         painter=_painter([]), checker=_checker([]))
    assert th.store().get("formula one").status == th.STATUS_REVIEW
    assert th.pick("formula one season", ()) is None or \
        th.pick("formula one season", ())["node"] != "formula one"


def test_a_logo_is_painted_again_and_never_stored_live(tree, monkeypatch):
    _set(monkeypatch, thumbnails_attempts=3)
    dirty = dict(CLEAN, logo=True)
    paint = _painter([])
    _run(only=["college football"], writer=_writer(), painter=paint,
         checker=_checker([dirty, dirty, dirty]))
    row = th.store().get("college football")
    assert row.status == th.STATUS_FAILED
    assert "logo" in row.reason
    assert len(paint.calls) == 3
    assert th.store().image("college football", any_status=True) is None


def test_a_second_attempt_can_succeed(tree):
    _run(only=["college football"], writer=_writer(), painter=_painter([]),
         checker=_checker([dict(CLEAN, person=True), CLEAN]))
    row = th.store().get("college football")
    assert row.status == th.STATUS_APPROVED and row.attempts == 2


def test_an_unchecked_picture_is_held_rather_than_published(tree):
    _run(only=["college football"], writer=_writer(), painter=_painter([]),
         checker=_checker([RuntimeError("checker down")]))
    assert th.store().get("college football").status == th.STATUS_REVIEW


def test_a_filtered_result_is_retried_and_a_key_problem_is_not(tree):
    paint = _painter([th.GenerationError("Imagen returned no image (filtered)")])
    _run(only=["college football"], writer=_writer(), painter=paint,
         checker=_checker([]))
    assert th.store().get("college football").status == th.STATUS_APPROVED
    assert len(paint.calls) == 2

    paint = _painter([th.GenerationError("Imagen answered 403: disabled")] * 3)
    result = _run(only=["golf", "tennis"], writer=_writer(), painter=paint,
                  checker=_checker([]))
    # A key problem is about the deployment, not the picture: the run stops
    # at the first attempt and neither node is marked failed.
    assert "403" in result["stopped"]
    assert len(paint.calls) == 1
    assert th.store().get("golf") is None and th.store().get("tennis") is None


def test_a_scene_naming_a_flagged_subject_is_never_painted(tree):
    async def write(paths):
        return [th.Scene("formula one", "a formula one car on a grid", True)]
    paint = _painter([])
    _run(only=["formula one"], writer=write, painter=paint, checker=_checker([]))
    assert paint.calls == []
    assert th.store().get("formula one").status == th.STATUS_FAILED


def test_the_prompt_carries_the_house_style(tree):
    paint = _painter([])
    _run(only=["golf"], writer=_writer(), painter=paint, checker=_checker([]))
    assert th.HOUSE_STYLE in paint.calls[0]


def test_the_daily_ceiling_stops_a_run(tree, monkeypatch):
    _set(monkeypatch, thumbnails_daily_images=2)
    result = _run(limit=10, writer=_writer(), painter=_painter([]),
                  checker=_checker([]))
    painted = result["approved"] + result["review"] + result["failed"]
    assert painted <= 2
    assert th.store().images_since(0) <= 2


def test_broad_branches_are_painted_first(tree):
    order = th.wanted(tree, th.store())
    assert set(order[:8]) == set(T.FACETS)
    assert order.index("american football") < order.index("college football")


def test_a_failed_node_is_not_retried_unless_asked(tree):
    th.store().put(th.Thumb("golf", th.STATUS_FAILED), None)
    assert "golf" not in th.wanted(tree, th.store())
    assert "golf" in th.wanted(tree, th.store(), retry_failed=True)


def test_a_writer_failure_leaves_everything_as_it_was(tree):
    async def broken(paths):
        raise RuntimeError("no key")
    result = _run(writer=broken, painter=_painter([]), checker=_checker([]))
    assert result["approved"] == 0 and result.get("errors")
    assert th.store().all() == []


def test_the_real_painter_switches_people_off_at_the_model(monkeypatch):
    sent = {}

    class Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"predictions": [{"bytesBase64Encoded":
                                     base64.b64encode(b"png").decode(),
                                     "mimeType": "image/png"}]}

    class Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            sent.update(url=url, body=json, headers=headers)
            return Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    _set(monkeypatch, gemini_api_key="k")
    got = asyncio.run(th.imagen_painter("a scene"))
    assert got.image == b"png"
    assert sent["body"]["parameters"]["personGeneration"] == "dont_allow"
    assert sent["url"].endswith("imagen-4.0-generate-001:predict")
    assert sent["headers"] == {"x-goog-api-key": "k"}


def test_an_empty_imagen_answer_reads_as_filtered(monkeypatch):
    class Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"predictions": []}

    class Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    _set(monkeypatch, gemini_api_key="k")
    with pytest.raises(th.GenerationError, match="filtered"):
        asyncio.run(th.imagen_painter("a scene"))


def test_the_writer_is_told_to_name_nothing():
    for word in ("team", "company", "brand", "person", "logos"):
        assert word in th.WRITER_SYSTEM


# --- the endpoints ---------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    import app as appmod
    from fastapi.testclient import TestClient

    monkeypatch.setattr(appmod, "ADMIN_TOKEN", "secret")
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    return TestClient(appmod.app)


def test_an_approved_picture_is_served_and_cached_for_a_year(tree, client):
    _put("golf")
    r = client.get("/api/thumb/golf")
    assert r.status_code == 200 and r.content == b"img-golf"
    assert "immutable" in r.headers["cache-control"]
    assert client.get("/api/v1/thumb/golf").status_code == 200


def test_a_held_picture_is_not_served(tree, client):
    assert client.get("/api/thumb/golf").status_code == 404
    _put("golf", status=th.STATUS_REVIEW)
    assert client.get("/api/thumb/golf").status_code == 404


def test_the_admin_endpoints_are_admin_only(tree, client):
    assert client.get("/api/admin/thumbnails").status_code == 404
    assert client.post("/api/admin/thumbnails/decide",
                       json={"node": "golf", "action": "approve"}).status_code == 404
    ok = client.get("/api/admin/thumbnails", headers={"X-Admin-Token": "secret"})
    assert ok.status_code == 200
    assert ok.json()["missing"] == len(T.FACETS) + len(tree.nodes())


def test_a_person_approves_a_held_picture(tree, client):
    _put("golf", status=th.STATUS_REVIEW)
    r = client.post("/api/admin/thumbnails/decide",
                    json={"node": "golf", "action": "approve"},
                    headers={"X-Admin-Token": "secret"})
    assert r.status_code == 200
    assert client.get("/api/thumb/golf").status_code == 200


def test_health_reports_pictures_without_creating_a_store(client):
    body = client.get("/api/health").json()
    assert body["thumbnails"]["counts"]["approved"] == 0
    assert body["thumbnails"]["generation"] is False
    assert not os.path.exists(os.environ["THUMBNAILS_DB"])


def test_a_failed_repaint_keeps_the_live_picture(tree):
    _run(only=["golf"], writer=_writer(), painter=_painter([]),
         checker=_checker([]))
    live = th.store().get("golf")
    url = th.pick("golf", ())["url"]
    dirty = dict(CLEAN, logo=True)
    _run(only=["golf"], regenerate=True, writer=_writer(),
         painter=_painter([]), checker=_checker([dirty] * 3))
    row = th.store().get("golf")
    assert row.status == th.STATUS_APPROVED
    assert th.store().image("golf") is not None
    assert th.pick("golf", ())["url"] == url
    assert "kept the live picture" in row.reason
    assert row.attempts == live.attempts + 3
