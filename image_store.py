"""Pictures in the R2 bucket, not in the databases (§330).

Four stores used to keep pictures inside their rows: profile photos
(`social.people.avatar`) and group photos (`messages.groups.avatar`) as
`data:` URLs up to 96 KB, mix covers (`mixes.cover`) up to 200 KB, and the
category pictures (`thumbnails.image`) as bytes. That is the largest thing a
listener adds to a database, and it rode every response that carried a face.

**The bucket keeps the bytes; the row keeps a path.** With a bucket
(`AUDIO_STORE=r2`, the same one kept audio uses), `keep()` writes the picture
to `images/<kind>/<sha256>.<ext>` and returns `/api/image/<kind>/<sha256>.<ext>`,
which is what the row stores and what a client puts in `<img src>` exactly
where it put the `data:` URL. `/api/image/` reads the object back. No bucket
(staging, tests, a fresh clone) or a failed write and the `data:` URL is
stored as before: a layer that saves space must not cost a picture.

* **Under `images/`**, which no rule in `deploy/r2-lifecycle.json` touches:
  a picture is deleted by FAM when no row names it, never by age.
* **Named by its bytes, per kind.** The same picture twice is one object, and
  a mix copied from somebody else's shares its cover. Because the name is per
  kind, whether anything still uses it is a question for one table
  (`release`'s `still_used`), never a reach across stores.
* **A thumbnail row keeps a pointer in its BLOB** (`POINTER + name`), so
  "has a picture" stays `image IS NOT NULL` everywhere it is asked.
* `migrate()` moves what is already inline (`tools/images_to_r2.py`, and
  once per boot in the background).
"""
from __future__ import annotations

import base64
import hashlib
import logging
import re
from typing import Callable, Optional

import audio_store

log = logging.getLogger(__name__)

PREFIX = "images/"
URL_PREFIX = "/api/image/"
#: What a thumbnail's BLOB starts with when the bytes are in the bucket.
POINTER = b"r2:"

KINDS = ("avatar", "group", "cover", "thumb")
_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp",
          "image/gif": "gif"}
_MIMES = {ext: mime for mime, ext in _TYPES.items()}
_NAME = re.compile(r"^(avatar|group|cover|thumb)/[0-9a-f]{64}\.(jpg|png|webp|gif)$")


def _store():
    return audio_store.get_store()


def enabled() -> bool:
    return _store() is not None


def name_for(kind: str, data: bytes, mime: str) -> str:
    """`images/<kind>/<sha256>.<ext>`."""
    ext = _TYPES.get(mime, "jpg")
    return f"{PREFIX}{kind}/{hashlib.sha256(data).hexdigest()}.{ext}"


def put(kind: str, data: bytes, mime: str) -> Optional[str]:
    """Write the bytes; the object's name, or None with no bucket or on a
    failure (the caller keeps the bytes where they were)."""
    if kind not in KINDS:
        raise ValueError(f"unknown picture kind {kind!r}")
    store = _store()
    if store is None or not data:
        return None
    name = name_for(kind, data, mime)
    try:
        store.put(name, data, content_type=mime if mime in _TYPES else "image/jpeg")
    except Exception as exc:  # noqa: BLE001 - never worth losing a picture
        log.warning("image store: could not keep %s (%s); kept inline", name, exc)
        return None
    return name


def url_for(name: str) -> str:
    return URL_PREFIX + name[len(PREFIX):]


def name_from_url(value: str) -> Optional[str]:
    """The object a `/api/image/...` path names, or None for anything else."""
    value = str(value or "")
    if not value.startswith(URL_PREFIX):
        return None
    rest = value[len(URL_PREFIX):]
    return PREFIX + rest if _NAME.match(rest) else None


def is_url(value: str) -> bool:
    return name_from_url(value) is not None


def _split(data_url: str) -> Optional[tuple[str, bytes]]:
    head, _, body = str(data_url or "").partition(",")
    if not head.startswith("data:") or ";base64" not in head:
        return None
    mime = head[5:].split(";", 1)[0]
    try:
        return mime, base64.b64decode(body, validate=True)
    except Exception:  # noqa: BLE001
        return None


def keep(kind: str, value: str) -> str:
    """What a row should store for a cleaned picture: its `/api/image/` path
    when the bucket took it, else `value` unchanged ("" and paths included)."""
    parts = _split(value)
    if parts is None:
        return value
    name = put(kind, parts[1], parts[0])
    return url_for(name) if name else value


def read(name: str) -> Optional[tuple[bytes, str]]:
    """(bytes, media type) for an object name, or None."""
    store = _store()
    if store is None:
        return None
    try:
        data = store.get(name)
    except Exception as exc:  # noqa: BLE001
        log.warning("image store: could not read %s (%s)", name, exc)
        return None
    if data is None:
        return None
    return data, _MIMES.get(name.rsplit(".", 1)[-1], "image/jpeg")


def release(value, still_used: Callable[[], bool]) -> bool:
    """Delete the object `value` names (a path, or a thumbnail pointer) once
    `still_used()` - asked of the one table that may hold it - says nothing
    does. True if it was deleted. Never raises: a picture left behind costs
    a fraction of a cent, a failed save costs a listener's change."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        name = pointer_name(bytes(value))
    else:
        name = name_from_url(value)
    store = _store()
    if not name or store is None:
        return False
    try:
        if still_used():
            return False
        store.delete(name)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("image store: could not release %s (%s)", name, exc)
        return False


# --- thumbnails keep a pointer in their BLOB ---------------------------------

def pointer(name: str) -> bytes:
    return POINTER + name.encode("ascii")


def pointer_name(blob: Optional[bytes]) -> Optional[str]:
    if not blob or not bytes(blob[:len(POINTER)]) == POINTER:
        return None
    name = bytes(blob[len(POINTER):]).decode("ascii", "replace")
    return name if name.startswith(PREFIX) and _NAME.match(name[len(PREFIX):]) else None


def keep_bytes(kind: str, data: Optional[bytes], mime: str) -> Optional[bytes]:
    """A thumbnail's BLOB: a pointer when the bucket took the bytes, else the
    bytes themselves."""
    if not data or pointer_name(data):
        return data
    name = put(kind, bytes(data), mime)
    return pointer(name) if name else data


def resolve_bytes(blob: Optional[bytes]) -> Optional[bytes]:
    """A thumbnail BLOB's picture: the bucket's bytes for a pointer, else the
    BLOB. None when a pointer's object cannot be read."""
    name = pointer_name(blob)
    if name is None:
        return blob
    found = read(name)
    return found[0] if found else None


# --- moving what is already inline -------------------------------------------

def migrate(limit: int = 500, stores: Optional[dict] = None) -> dict:
    """Move up to `limit` inline pictures per store into the bucket. Each
    store does its own (`move_pictures_to_bucket`), the way each erases its
    own on account deletion. `stores` maps a label to an open store (the
    server's own); missing ones are opened here. Counts moved per store;
    nothing without a bucket."""
    if not enabled():
        return {"why": "no bucket (AUDIO_STORE=r2)"}
    out: dict = {}
    for label, opener in _MIGRATORS:
        try:
            store = (stores or {}).get(label) or opener()
            out[label] = store.move_pictures_to_bucket(limit)
        except Exception as exc:  # noqa: BLE001 - one store never stops the rest
            log.exception("image store: moving %s failed", label)
            out[label] = f"failed: {exc.__class__.__name__}"
    return out


def _social():
    import social
    return social.SocialStore()


def _messages():
    import messages
    return messages.MessageStore()


def _mixes():
    import mixes
    return mixes.MixStore()


def _thumbs():
    import thumbnails
    return thumbnails.store()


_MIGRATORS = (("profiles", _social), ("groups", _messages), ("mixes", _mixes),
              ("thumbnails", _thumbs))


def describe() -> dict:
    return {"store": "bucket" if enabled() else "inline", "prefix": PREFIX}
