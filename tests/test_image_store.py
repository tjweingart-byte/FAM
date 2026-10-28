"""Pictures in the bucket, not the databases (§330, image_store.py)."""
from __future__ import annotations

import base64
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audio_store  # noqa: E402
import image_store  # noqa: E402
import messages as MSG  # noqa: E402
import mixes as M  # noqa: E402
import social as S  # noqa: E402
import thumbnails as th  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + b"face" * 50
OTHER = b"\xff\xd8\xff\xe0" + b"else" * 50


def data_url(raw: bytes = JPEG) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode()


@pytest.fixture
def bucket():
    store = audio_store.MemoryAudioStore()
    audio_store.set_store(store)
    yield store
    audio_store.set_store(None)


@pytest.fixture
def no_bucket(monkeypatch):
    monkeypatch.setattr(image_store, "_store", lambda: None)


def images(bucket) -> list[str]:
    return sorted(n for n in bucket.objects if n.startswith(image_store.PREFIX))


# --- the module ---------------------------------------------------------------

def test_keep_writes_the_bytes_and_returns_a_path(bucket):
    kept = image_store.keep("avatar", data_url())
    assert kept.startswith("/api/image/avatar/") and kept.endswith(".jpg")
    name = image_store.name_from_url(kept)
    assert bucket.objects[name][0] == JPEG
    assert image_store.read(name) == (JPEG, "image/jpeg")


def test_without_a_bucket_the_picture_stays_inline(no_bucket):
    assert image_store.keep("avatar", data_url()) == data_url()


def test_a_failed_write_keeps_the_picture_inline(bucket, monkeypatch):
    def refuse(*a, **k):
        raise audio_store.AudioStoreError("down")
    monkeypatch.setattr(bucket, "put", refuse)
    assert image_store.keep("avatar", data_url()) == data_url()


def test_only_our_own_names_are_paths():
    assert not image_store.is_url("/api/image/avatar/../../recent/x.jpg")
    assert not image_store.is_url("https://example.com/a.jpg")
    assert not image_store.is_url("/api/image/audio/" + "a" * 64 + ".jpg")
    assert image_store.is_url("/api/image/cover/" + "a" * 64 + ".png")


def test_pictures_are_not_under_a_prefix_the_bucket_expires():
    import json
    rules = json.load(open(os.path.join(os.path.dirname(__file__), "..",
                                        "deploy", "r2-lifecycle.json")))["rules"]
    for rule in rules:
        assert not image_store.PREFIX.startswith(rule["conditions"]["prefix"])


# --- profiles -------------------------------------------------------------------

def test_a_profile_photo_goes_to_the_bucket_and_the_old_one_is_deleted(bucket, tmp_path):
    store = S.SocialStore(str(tmp_path / "s.db"))
    first = store.set_person("u", "Ann", "ann", avatar=data_url())["avatar"]
    assert first.startswith("/api/image/avatar/")
    second = store.set_person("u", "Ann", "ann", avatar=data_url(OTHER))["avatar"]
    assert second != first
    assert images(bucket) == [image_store.name_from_url(second)]
    # Renaming without a picture leaves it alone; sending its path back keeps it.
    assert store.set_person("u", "Ann B", "ann")["avatar"] == second
    assert store.set_person("u", "Ann B", "ann", avatar=second)["avatar"] == second


def test_a_shared_photo_survives_one_owner_leaving(bucket, tmp_path):
    store = S.SocialStore(str(tmp_path / "s.db"))
    store.set_person("a", "A", "aaa", avatar=data_url())
    store.set_person("b", "B", "bbb", avatar=data_url())
    store.set_person("a", "A", "aaa", avatar="")
    assert len(images(bucket)) == 1
    store.forget("b")
    assert images(bucket) == []


def test_inline_pictures_move_to_the_bucket(tmp_path, monkeypatch):
    with monkeypatch.context() as inline:
        inline.setattr(image_store, "_store", lambda: None)
        social = S.SocialStore(str(tmp_path / "s.db"))
        social.set_person("u", "Ann", "ann", avatar=data_url())
        mixes = M.MixStore(str(tmp_path / "m.db"))
        mix = mixes.create("u", "Gym", ["training-load"], cover=data_url(OTHER))
        msgs = MSG.MessageStore(str(tmp_path / "g.db"))
        group = msgs.create_group("u", ["v", "w"], "G")
        msgs.set_group_photo("u", group["id"], data_url())
        assert social.person("u")["avatar"].startswith("data:")
    bucket = audio_store.MemoryAudioStore()
    audio_store.set_store(bucket)
    try:
        moved = image_store.migrate(50, {"profiles": social, "mixes": mixes,
                                         "groups": msgs,
                                         "thumbnails": _Nothing()})
        assert moved == {"profiles": 1, "groups": 1, "mixes": 1, "thumbnails": 0}
        assert social.person("u")["avatar"].startswith("/api/image/avatar/")
        assert mixes.get("u", mix.id).cover.startswith("/api/image/cover/")
        assert msgs.group(group["id"])["avatar"].startswith("/api/image/group/")
        # A second pass finds nothing.
        assert not any(image_store.migrate(50, {"profiles": social, "mixes": mixes,
                                                "groups": msgs,
                                                "thumbnails": _Nothing()}).values())
    finally:
        audio_store.set_store(None)


class _Nothing:
    def move_pictures_to_bucket(self, limit):
        return 0


# --- mixes ------------------------------------------------------------------------

def test_a_copy_keeps_its_cover_when_the_original_goes(bucket, tmp_path):
    store = M.MixStore(str(tmp_path / "m.db"))
    mix = store.create("a", "Gym", ["training-load"], cover=data_url())
    assert mix.cover.startswith("/api/image/cover/")
    copy = store.add_copy("b", mix, owner="A")
    assert copy.cover == mix.cover
    store.delete("a", mix.id)
    assert images(bucket) == [image_store.name_from_url(mix.cover)]
    store.forget("b")
    assert images(bucket) == []


def test_a_new_cover_replaces_the_old_object(bucket, tmp_path):
    store = M.MixStore(str(tmp_path / "m.db"))
    mix = store.create("a", "Gym", ["training-load"], cover=data_url())
    updated = store.update("a", mix.id, cover=data_url(OTHER))
    assert images(bucket) == [image_store.name_from_url(updated.cover)]
    # The path sent back unchanged is accepted and kept.
    assert store.update("a", mix.id, cover=updated.cover).cover == updated.cover


# --- group photos -------------------------------------------------------------------

def test_a_reviewer_taking_a_group_photo_down_deletes_it(bucket, tmp_path):
    msgs = MSG.MessageStore(str(tmp_path / "g.db"))
    group = msgs.create_group("u", ["v", "w"], "G")
    kept = msgs.set_group_photo("u", group["id"], data_url())["avatar"]
    assert kept.startswith("/api/image/group/")
    assert msgs.remove_group_photo(group["id"])
    assert images(bucket) == []


# --- thumbnails ---------------------------------------------------------------------

def test_a_thumbnail_keeps_a_pointer_and_reads_back(bucket, tmp_path):
    store = th.ThumbnailStore(str(tmp_path / "t.db"))
    store.put(th.Thumb("golf", th.STATUS_APPROVED, facet="sports",
                       mime="image/png"), JPEG)
    assert "golf" in store.approved()
    raw = store._blobs("golf")[0]
    assert image_store.pointer_name(raw)
    assert store.image("golf") == (JPEG, "image/png")
    # A repaint held for review, then promoted: the old object goes.
    store.put_pending("golf", OTHER, "image/png", "scene", {}, 1, 0.0, "")
    assert store.image("golf", pending=True)[0] == OTHER
    assert store.promote_pending("golf")
    assert store.image("golf")[0] == OTHER
    assert len(images(bucket)) == 1


def test_inline_thumbnails_move(tmp_path, monkeypatch):
    with monkeypatch.context() as inline:
        inline.setattr(image_store, "_store", lambda: None)
        store = th.ThumbnailStore(str(tmp_path / "t.db"))
        store.put(th.Thumb("golf", th.STATUS_APPROVED, facet="sports",
                           mime="image/png"), JPEG)
        store.put(th.Thumb("tennis", th.STATUS_APPROVED, facet="sports",
                           mime="image/png"), OTHER)
        assert store._blobs("golf")[0] == JPEG
    bucket = audio_store.MemoryAudioStore()
    audio_store.set_store(bucket)
    try:
        # One a batch: the second pass reaches the row the first did not.
        assert store.move_pictures_to_bucket(1) == 1
        assert store.move_pictures_to_bucket(1) == 1
        assert store.move_pictures_to_bucket(1) == 0
        assert image_store.pointer_name(store._blobs("golf")[0])
        assert store.image("golf") == (JPEG, "image/png")
        assert "golf" in store.approved()
    finally:
        audio_store.set_store(None)


# --- the endpoint -----------------------------------------------------------------

def test_the_endpoint_serves_a_kept_picture_and_nothing_else(bucket):
    from fastapi.testclient import TestClient
    import app as appmod

    path = image_store.keep("avatar", data_url())
    client = TestClient(appmod.app)
    got = client.get(path)
    assert got.status_code == 200 and got.content == JPEG
    assert got.headers["content-type"] == "image/jpeg"
    assert "immutable" in got.headers["cache-control"]
    assert client.get("/api/image/avatar/" + "0" * 64 + ".jpg").status_code == 404
    assert client.get("/api/image/../recent/x").status_code == 404
