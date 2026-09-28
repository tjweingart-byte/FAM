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
import json
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

    paint = _painter([th.StopRun("Imagen answered 403: disabled")] * 3)
    result = _run(only=["golf", "tennis"], writer=_writer(), painter=paint,
                  checker=_checker([]))
    # A key problem is about the deployment, not the picture: the run stops
    # at the first attempt and neither node is marked failed.
    assert "403" in result["stopped"]
    assert len(paint.calls) == 1
    assert th.store().get("golf") is None and th.store().get("tennis") is None
    # ...and is kept for the review page, since it left no node and no spend
    # to show anything happened (§164).
    last = th.store().report()["last_run"]
    assert "403" in last["stopped"] and last["at"] > 0


def test_a_refused_prompt_fails_its_node_and_the_run_goes_on(tree):
    # A 400 is about one prompt. Stopping the run on it would leave that node
    # first in the queue forever and nothing after it would ever be painted.
    paint = _painter([th.GenerationError("Imagen answered 400: blocked")])
    result = _run(only=["golf", "tennis"], writer=_writer(), painter=paint,
                  checker=_checker([]))
    assert th.store().get("golf").status == th.STATUS_FAILED
    assert th.store().get("tennis").status == th.STATUS_APPROVED
    assert "stopped" not in result


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


def test_imagen_is_asked_for_adults_only(monkeypatch):
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
    _set(monkeypatch, gemini_api_key="k",
         thumbnails_image_model="imagen-4.0-generate-001")
    monkeypatch.setattr(th, "_RESOLVED_MODEL", None)
    got = asyncio.run(th.imagen_painter("a scene"))
    assert got.image == b"png"
    # The house style has small anonymous figures since §166; never children.
    assert sent["body"]["parameters"]["personGeneration"] == "allow_adult"
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
    _set(monkeypatch, gemini_api_key="k",
         thumbnails_image_model="imagen-4.0-generate-001")
    monkeypatch.setattr(th, "_RESOLVED_MODEL", None)
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


def test_a_held_repaint_waits_beside_the_live_picture(tree):
    _run(only=["formula one"], writer=_writer(), painter=_painter([]),
         checker=_checker([]))
    th.store().set_status("formula one", th.STATUS_APPROVED)
    url = th.pick("formula one season", ())["url"]
    result = _run(only=["formula one"], regenerate=True,
                  writer=_writer(flagged={"formula one"}),
                  painter=_painter([th.Painting(_png((10, 200, 10)), "image/png")]),
                  checker=_checker([]))
    assert result["review"] == 1
    row = th.store().get("formula one")
    assert row.status == th.STATUS_APPROVED and row.pending
    # Tiles keep the live picture, at the same URL, while the new one waits.
    assert th.pick("formula one season", ())["url"] == url
    assert th.store().image("formula one", pending=True) is not None
    assert th.store().report()["counts"]["pending"] == 1


def test_approving_a_held_repaint_puts_it_live(tree):
    test_a_held_repaint_waits_beside_the_live_picture(tree)
    before = th.store().image("formula one")[0]
    pending = th.store().image("formula one", pending=True)[0]
    assert th.store().promote_pending("formula one")
    assert th.store().image("formula one")[0] == pending != before
    assert not th.store().get("formula one").pending


def test_rejecting_a_held_repaint_keeps_the_live_one(tree):
    test_a_held_repaint_waits_beside_the_live_picture(tree)
    before = th.store().image("formula one")[0]
    assert th.store().drop_pending("formula one")
    row = th.store().get("formula one")
    assert row.status == th.STATUS_APPROVED and not row.pending
    assert th.store().image("formula one")[0] == before


def test_a_failed_repaint_is_counted_as_failed(tree):
    _run(only=["golf"], writer=_writer(), painter=_painter([]),
         checker=_checker([]))
    result = _run(only=["golf"], regenerate=True, writer=_writer(),
                  painter=_painter([]),
                  checker=_checker([dict(CLEAN, logo=True)] * 3))
    assert result["failed"] == 1 and result["approved"] == 0


def test_a_picture_written_by_another_process_reaches_tiles(tree, monkeypatch):
    held = th.store()
    assert th.pick("golf", ()) is None
    # A second connection - `tools/thumbnails.py` in another process.
    other = th.ThumbnailStore(held.path)
    other.put(th.Thumb("golf", th.STATUS_APPROVED, facet="sports",
                       mime="image/png"), b"x")
    monkeypatch.setattr(th, "REFRESH_SECONDS", 0.0)
    assert th.pick("golf", ())["node"] == "golf"
    other.set_status("golf", th.STATUS_REJECTED)
    assert th.pick("golf", ()) is None


def test_the_ceiling_is_checked_per_image_not_per_node(tree, monkeypatch):
    _set(monkeypatch, thumbnails_daily_images=2, thumbnails_attempts=3)
    dirty = dict(CLEAN, logo=True)
    result = _run(only=["golf"], writer=_writer(), painter=_painter([]),
                  checker=_checker([dirty] * 3))
    assert th.store().images_since(0) == 2
    assert result.get("capped")
    # Stopped by the ceiling, not failed: the node is tried again tomorrow.
    assert th.store().get("golf") is None


def test_the_admin_run_says_when_there_is_nothing_to_paint(tree, client,
                                                           monkeypatch):
    monkeypatch.setattr(th, "configured", lambda: (True, ""))
    r = client.post("/api/admin/thumbnails/run",
                    json={"limit": 1, "nodes": ["not a node"],
                          "regenerate": True},
                    headers={"X-Admin-Token": "secret"})
    assert r.status_code == 409 and "no longer in the category tree" in r.json()["error"]


def test_the_admin_decides_on_a_held_repaint(tree, client):
    test_a_held_repaint_waits_beside_the_live_picture(tree)
    headers = {"X-Admin-Token": "secret"}
    assert client.get("/api/admin/thumbnails/formula%20one/image?pending=1",
                      headers=headers).status_code == 200
    r = client.post("/api/admin/thumbnails/decide",
                    json={"node": "formula one", "action": "reject"},
                    headers=headers)
    assert r.json()["kept_live"] is True
    assert client.get("/api/thumb/formula%20one").status_code == 200


def test_a_scene_writer_failure_is_kept_for_the_review_page(tree):
    async def broken(paths):
        raise RuntimeError("model not found")
    result = _run(only=["golf"], writer=broken, painter=_painter([]),
                  checker=_checker([]))
    assert result["wanted"] == 1 and result["approved"] == 0
    last = th.store().report()["last_run"]
    assert "model not found" in last["errors"][0]
    assert "nodes" not in last


class _Google:
    """A fake of Google's API: `answers` maps a model to (status, body), and
    `models` is what the listing returns."""

    def __init__(self, answers, models=()):
        self.answers, self.models, self.posts = answers, list(models), []

    def client(self):
        fake = self

        class Resp:
            def __init__(self, status, body):
                self.status_code, self._body = status, body
                self.text = json.dumps(body)

            def json(self):
                return self._body

        class Client:
            def __init__(self, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, json=None, headers=None):
                fake.posts.append((url, json))
                model = url.rsplit("/", 1)[-1].split(":")[0]
                return Resp(*fake.answers.get(model, (404, {"error": {}})))

            async def get(self, url, params=None, headers=None):
                return Resp(200, {"models": fake.models})

        return Client


def _gemini_image(data=b"png"):
    return {"candidates": [{"content": {"parts": [
        {"text": "here"},
        {"inlineData": {"mimeType": "image/png",
                        "data": base64.b64encode(data).decode()}}]}}]}


def test_a_gemini_image_model_is_asked_through_generate_content(monkeypatch):
    import httpx
    google = _Google({"gemini-3.1-flash-image": (200, _gemini_image())})
    monkeypatch.setattr(httpx, "AsyncClient", google.client())
    _set(monkeypatch, gemini_api_key="k",
         thumbnails_image_model="gemini-3.1-flash-image")
    monkeypatch.setattr(th, "_RESOLVED_MODEL", None)
    got = asyncio.run(th.imagen_painter("a scene"))
    assert got.image == b"png" and got.mime == "image/png"
    url, body = google.posts[0]
    assert url.endswith("gemini-3.1-flash-image:generateContent")
    assert body["generationConfig"]["responseModalities"] == ["IMAGE"]
    assert body["generationConfig"]["imageConfig"] == {"aspectRatio": "4:3"}


def test_a_draft_image_from_thinking_is_not_the_picture(monkeypatch):
    import httpx
    answer = {"candidates": [{"content": {"parts": [
        {"thought": True, "inlineData": {
            "mimeType": "image/png",
            "data": base64.b64encode(b"draft").decode()}},
        {"inlineData": {"mimeType": "image/png",
                        "data": base64.b64encode(b"final").decode()}}]}}]}
    google = _Google({"gemini-3.1-flash-image": (200, answer)})
    monkeypatch.setattr(httpx, "AsyncClient", google.client())
    _set(monkeypatch, gemini_api_key="k",
         thumbnails_image_model="gemini-3.1-flash-image")
    monkeypatch.setattr(th, "_RESOLVED_MODEL", None)
    assert asyncio.run(th.imagen_painter("a scene")).image == b"final"


def test_a_retired_model_is_replaced_by_one_the_key_can_call(monkeypatch):
    # Imagen 4 was shut down on 2026-08-17 and answered 404 (§164).
    import httpx
    google = _Google(
        {"gemini-3.1-flash-image-preview": (200, _gemini_image())},
        models=[
            {"name": "models/gemini-3-pro-image-preview",
             "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-3.1-flash-image-preview",
             "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-3.1-flash",
             "supportedGenerationMethods": ["generateContent"]},
        ])
    monkeypatch.setattr(httpx, "AsyncClient", google.client())
    _set(monkeypatch, gemini_api_key="k",
         thumbnails_image_model="imagen-4.0-generate-001")
    monkeypatch.setattr(th, "_RESOLVED_MODEL", None)
    got = asyncio.run(th.imagen_painter("a scene"))
    assert got.image == b"png"
    assert google.posts[-1][0].endswith(
        "gemini-3.1-flash-image-preview:generateContent")
    # Remembered, so the next picture does not pay for the 404 again.
    asyncio.run(th.imagen_painter("another"))
    assert len(google.posts) == 3


def test_no_image_model_at_all_stops_the_run_and_says_so(monkeypatch):
    import httpx
    google = _Google({}, models=[])
    monkeypatch.setattr(httpx, "AsyncClient", google.client())
    _set(monkeypatch, gemini_api_key="k",
         thumbnails_image_model="imagen-4.0-generate-001")
    monkeypatch.setattr(th, "_RESOLVED_MODEL", None)
    with pytest.raises(th.StopRun, match="THUMBNAILS_IMAGE_MODEL"):
        asyncio.run(th.imagen_painter("a scene"))


# --- the house style's reference pictures (§166) ---------------------------


def test_the_shipped_references_are_found():
    refs = th.style_references()
    assert 1 <= len(refs) <= th.MAX_STYLE_REFERENCES
    assert all(mime.startswith("image/") and data for mime, data in refs)


def test_every_gemini_request_carries_the_references(monkeypatch):
    import httpx
    google = _Google({"gemini-3.1-flash-image": (200, _gemini_image())})
    monkeypatch.setattr(httpx, "AsyncClient", google.client())
    _set(monkeypatch, gemini_api_key="k",
         thumbnails_image_model="gemini-3.1-flash-image")
    monkeypatch.setattr(th, "_RESOLVED_MODEL", None)
    asyncio.run(th.imagen_painter("a clay tennis court"))
    parts = google.posts[0][1]["contents"][0]["parts"]
    pictures = [p for p in parts if "inlineData" in p]
    assert len(pictures) == len(th.style_references()) >= 1
    # Pictures first, then what to take from them, then the scene.
    assert parts[-1]["text"].startswith(th.STYLE_REFERENCE_NOTE)
    assert parts[-1]["text"].endswith("a clay tennis court")
    assert parts.index(pictures[-1]) < len(parts) - 1


def test_references_switched_off_send_the_scene_alone(monkeypatch):
    import httpx
    google = _Google({"gemini-3.1-flash-image": (200, _gemini_image())})
    monkeypatch.setattr(httpx, "AsyncClient", google.client())
    _set(monkeypatch, gemini_api_key="k", thumbnails_style_dir="0",
         thumbnails_image_model="gemini-3.1-flash-image")
    monkeypatch.setattr(th, "_RESOLVED_MODEL", None)
    asyncio.run(th.imagen_painter("a clay tennis court"))
    assert google.posts[0][1]["contents"][0]["parts"] == [
        {"text": "a clay tennis court"}]
    assert th.health()["style_references"] == 0
    assert "style_warning" not in th.health()


def test_a_missing_reference_folder_is_said_not_raised(monkeypatch, tmp_path):
    _set(monkeypatch, thumbnails_style_dir=str(tmp_path / "nowhere"))
    assert th.style_references() == []
    assert "no reference pictures" in th.health()["style_warning"]


def test_the_house_style_is_the_owners_watercolour():
    for word in ("watercolour", "mid-century", "anonymous", "wordless"):
        assert word in th.HOUSE_STYLE
    # Figures are allowed; faces, real people and team kit are not.
    assert "anonymous figures" in th.WRITER_SYSTEM
    assert "real or famous person" in th.WRITER_SYSTEM
    assert "do not" in th.CHECKER_SYSTEM and "real person" in th.CHECKER_SYSTEM


# --- §167: full-bleed, deeper colour, modern equipment ---------------------


def _bordered(size, fill, border=0, border_fill=(250, 248, 244)):
    from PIL import Image
    im = Image.new("RGB", size, border_fill if border else fill)
    if border:
        im.paste(Image.new("RGB", (size[0] - 2 * border, size[1] - 2 * border),
                           fill), (border, border))
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


def _open(data):
    from PIL import Image
    return Image.open(io.BytesIO(data)).convert("RGB")


def test_a_white_paper_border_is_cut_off():
    pytest.importorskip("PIL")
    data, _ = th._resize(_bordered((800, 600), (40, 110, 70), border=60),
                         400, 300, "jpeg")
    im = _open(data)
    for xy in ((1, 1), (398, 1), (1, 298), (398, 298), (200, 1), (1, 150)):
        r, g, b = im.getpixel(xy)
        assert g < 170 and r < 120, (xy, im.getpixel(xy))


def test_a_pale_blue_sky_is_not_mistaken_for_paper():
    pytest.importorskip("PIL")
    from PIL import Image
    im = Image.new("RGB", (800, 600), (40, 110, 70))
    im.paste(Image.new("RGB", (800, 150), (175, 215, 230)), (0, 0))
    assert th._trim_pale_edges(im).size == (800, 600)


def test_a_border_or_obsolete_equipment_fails_the_picture():
    assert th.failed_checks(dict(CLEAN, border=True)) == ["border"]
    assert th.failed_checks(dict(CLEAN, obsolete=True)) == ["obsolete"]
    assert {"border", "obsolete"} <= set(th.CHECKER_SCHEMA["required"])


def test_the_look_is_full_bleed_deeper_and_modern():
    for words in ("Full-bleed", "no border", "vignette", "deep tones",
                  "modern"):
        assert words in th.HOUSE_STYLE
    assert "pastel" in th.HOUSE_STYLE
    for words in ("TODAY", "old radios", "obsolete"):
        assert words in th.WRITER_SYSTEM
    assert "today's equipment" in th.STYLE_REFERENCE_NOTE
