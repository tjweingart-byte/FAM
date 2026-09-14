"""Continuous-line artwork for an episode: found, validated, and described.

The product idea is one line. A single uninterrupted stroke is revealed as the
episode plays, and the completed stroke is that episode's thumbnail. "A single
line connecting us all."

**This module generates nothing.** Artwork is produced somewhere else - today
by hand, later by the visual service sketched in
`docs/FAM_CONTINUOUS_LINE_VISUALS.md` - and dropped into a folder. All that
happens here is finding the file for a query, checking it is actually one
drawable line, and describing it in a way both the web app and a future iOS
client can consume without knowing any of that.

Three decisions are load-bearing, and each is the same decision this project
has made elsewhere:

* **Keyed on the subject, not the episode.** `visual_id` hashes the normalised
  *query* and nothing else. Length is not part of it, for exactly the reason
  voice is not part of the script cache key: a three minute and a ten minute
  episode about the same thing are the same subject, and the drawing
  illustrates the subject. That also makes a feed tile resolvable, which it
  would not be if the id needed a length nobody has chosen yet.

* **Per-machine state lives in `~/.fam/`.** Artwork is like voice models: tens
  of kilobytes that change far less often than the code, and a new copy of the
  app must find them already there. `FAM_VISUALS_DIR` overrides, the same way
  `FAM_VOICES_DIR` does.

* **A wrong asset says so.** Multi-path SVGs are the failure the visual
  language cannot survive - the pen would leave the page - so an asset that is
  not one drawable path is reported `failed` with the reason, never rendered
  as a silent multi-stroke reveal. Announcing is the rule this project has
  paid for most often.

Status is one of:

    none        no asset, and none expected
    processing  an asset is coming; show the empty square
    ready       an asset is here and passed validation
    failed      an asset is here and is not usable; `reason` says why

Nothing here is on the audio path. Every call is a file stat against a cached
manifest, and every failure resolves to a status rather than an exception.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from cache import normalize_query

log = logging.getLogger(__name__)

#: Per-user default, beside the voice models and for the same reason.
DEFAULT_DIR = Path.home() / ".fam" / "visuals" if Path.home() else Path("visuals")

#: The manifest, if there is one. Optional: a bare `<visual_id>.svg` in the
#: folder is a complete, valid installation on its own.
INDEX_NAME = "index.json"

#: The house palette, used when an asset does not name its own. These are the
#: numbers in the visual spec: warm ivory ground, near-black ink, a hairline.
DEFAULT_BACKGROUND = "#F8F4EA"
DEFAULT_STROKE = "#171820"
DEFAULT_STROKE_WIDTH = 1.4

#: What the folder may serve. A vector and, optionally, a raster of the
#: finished frame. Anything else is not an episode visual.
VECTOR_SUFFIXES = (".svg",)
THUMBNAIL_SUFFIXES = (".png", ".webp", ".jpg", ".jpeg")

#: A servable filename. Strict rather than sanitised: the set of names this
#: folder can hold is one we chose, so anything outside it is a mistake or an
#: attack and both deserve the same answer.
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")

#: Where the asset routes live. One place, so the interface, the iOS client
#: and the tests cannot disagree about the shape of a URL.
ASSET_ROUTE = "/api/visual/asset/"

_SVG_NS = "{http://www.w3.org/2000/svg}"
#: Everything SVG can draw. Exactly one of these may be present, and it must be
#: a `path`: a circle or a polyline in the file means the pen left the page.
_DRAWABLE = ("path", "circle", "ellipse", "rect", "line", "polyline", "polygon")

#: Counted rather than sampled: at this volume a count is the whole truth, and
#: it is reported on /api/health so a deploy can be asked what its artwork is
#: doing without reading a log.
_counters: dict[str, int] = {
    "lookups": 0, "ready": 0, "processing": 0, "failed": 0, "none": 0,
    "assets_served": 0, "assets_refused": 0,
}
_counter_lock = threading.Lock()


def _count(name: str) -> None:
    with _counter_lock:
        _counters[name] = _counters.get(name, 0) + 1


def counters() -> dict:
    with _counter_lock:
        return dict(_counters)


def enabled() -> bool:
    """Whether this server offers episode visuals at all.

    Read at call time rather than captured at import, so a test that sets the
    variable does not have to reload the module. Default on: with no artwork in
    the folder every lookup is `none` and nothing renders, which is exactly
    today's behaviour, so the flag exists to switch the feature *off* in a
    hurry rather than to opt into it.
    """
    return os.environ.get("EPISODE_VISUALS", "1") not in ("0", "false", "False", "")


def visuals_dir(create: bool = False) -> Path:
    """The folder episode artwork is read from."""
    override = os.environ.get("FAM_VISUALS_DIR") or os.environ.get("VISUALS_DIR")
    directory = Path(override).expanduser() if override else DEFAULT_DIR
    if create:
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.warning("could not create %s: %s", directory, exc)
    return directory


def visual_id(query: str) -> str:
    """Stable id for the *subject* of an episode.

    The same hash for "NFL week 5 recap" and "recap: week 5, NFL", because
    `normalize_query` already decided those are one topic and the drawing of
    one topic is one drawing.
    """
    key = normalize_query(query) or query.strip().lower()
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- manifest

_index_cache: dict = {"path": None, "mtime": None, "data": {}}
_index_lock = threading.Lock()


def _load_index(directory: Path) -> dict:
    """The manifest, re-read only when it has changed on disk.

    Keys may be a visual id or a raw query; a query is normalised on load, so
    whoever writes the file can use whichever they have to hand.
    """
    path = directory / INDEX_NAME
    try:
        mtime = path.stat().st_mtime
    except OSError:
        with _index_lock:
            _index_cache.update({"path": str(path), "mtime": None, "data": {}})
        return {}

    with _index_lock:
        if _index_cache["path"] == str(path) and _index_cache["mtime"] == mtime:
            return _index_cache["data"]

    data: dict = {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            for key, value in raw.items():
                if not isinstance(value, dict):
                    continue
                data[str(key).strip()] = value
                # Also reachable by the id its query hashes to, so a manifest
                # written in either vocabulary resolves the same way.
                data.setdefault(visual_id(str(key)), value)
    except (OSError, ValueError) as exc:
        # A broken manifest must not take the folder with it: the bare-file
        # convention still works, and the log says what to fix.
        log.warning("episode visuals: %s is not readable JSON (%s)", path, exc)
        data = {}

    with _index_lock:
        _index_cache.update({"path": str(path), "mtime": mtime, "data": data})
    return data


# -------------------------------------------------------------- validation


def validate_svg(text: str) -> tuple[bool, str]:
    """Is this one continuous drawable line? Returns (ok, reason).

    The rules are the visual language written down. One `<path>`, no other
    drawable element, and no fill - a filled shape is a silhouette, not a line,
    and reveals as a growing blob rather than a travelling pen.

    Deliberately not a repair. Repairing upstream is Phase 2's job and doing it
    here would mean the app quietly shipping a drawing nobody approved.
    """
    if not text.strip():
        return False, "the file is empty"
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return False, f"it is not parseable SVG ({exc})"

    tag = root.tag.replace(_SVG_NS, "")
    if tag != "svg":
        return False, f"the root element is <{tag}>, not <svg>"

    drawables: list[ET.Element] = []
    for element in root.iter():
        name = element.tag.replace(_SVG_NS, "") if isinstance(element.tag, str) else ""
        if name in _DRAWABLE:
            if name == "path" and not (element.get("d") or "").strip():
                continue          # an empty path draws nothing; ignore it
            drawables.append(element)

    if not drawables:
        return False, "it contains no drawable path"
    if len(drawables) > 1:
        names = ", ".join(sorted({d.tag.replace(_SVG_NS, "") for d in drawables}))
        return False, (
            f"it contains {len(drawables)} drawable elements ({names}); a FAM "
            "visual is exactly one continuous path"
        )

    only = drawables[0]
    name = only.tag.replace(_SVG_NS, "")
    if name != "path":
        return False, f"its one drawable element is a <{name}>, not a <path>"

    fill = (only.get("fill") or "").strip().lower()
    if fill and fill not in ("none", "transparent"):
        return False, f'the path is filled (fill="{fill}"); a FAM visual is stroke only'

    return True, ""


def _view_box(text: str) -> str:
    """The asset's own viewBox, so the client can size it without guessing."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return ""
    return (root.get("viewBox") or "").strip()


# ----------------------------------------------------------------- records


def _find(directory: Path, vid: str, suffixes: tuple[str, ...]) -> Optional[Path]:
    for suffix in suffixes:
        candidate = directory / f"{vid}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def _named(directory: Path, name: str) -> Optional[Path]:
    """A file the manifest named explicitly, if it is one this folder may hold."""
    name = str(name).strip()
    if not SAFE_NAME.match(name):
        return None
    candidate = directory / name
    return candidate if candidate.is_file() else None


def _asset_url(path: Path) -> str:
    return ASSET_ROUTE + path.name


def _blank(vid: str, status: str, reason: str = "", entry: Optional[dict] = None) -> dict:
    entry = entry or {}
    return {
        "id": vid,
        "status": status,
        "version": int(entry.get("version", 1) or 1),
        "vector_url": "",
        "thumbnail_url": "",
        "view_box": "",
        "background_color": str(entry.get("background", DEFAULT_BACKGROUND)),
        "stroke_color": str(entry.get("stroke", DEFAULT_STROKE)),
        "stroke_width": float(entry.get("stroke_width", DEFAULT_STROKE_WIDTH)),
        "reason": reason,
    }


def record_for(query: str, directory: Optional[Path] = None) -> dict:
    """Everything a player needs to draw this episode's line, or not to.

    Never raises. A folder that cannot be read, a manifest that is nonsense, a
    file that vanished between the stat and the open - all of them resolve to a
    status the player can act on, because the one thing this must never do is
    interfere with audio.
    """
    _count("lookups")
    vid = visual_id(query)

    if not enabled():
        _count("none")
        return _blank(vid, "none", "episode visuals are switched off on this server")

    directory = directory or visuals_dir()
    if not directory.is_dir():
        # The overwhelmingly common case on a machine nobody has put artwork
        # on, and the one a feed request hits twenty-four times. One stat
        # rather than a hundred and twenty.
        _count("none")
        return _blank(vid, "none", "")

    entry = _load_index(directory).get(vid) or _load_index(directory).get(
        normalize_query(query)) or {}

    declared = str(entry.get("status", "")).strip().lower()
    if declared == "processing":
        _count("processing")
        return _blank(vid, "processing", "the artwork is still being made", entry)
    if declared == "failed":
        _count("failed")
        return _blank(vid, "failed",
                      str(entry.get("reason", "the visual service could not make one")),
                      entry)

    vector = _named(directory, entry["vector"]) if entry.get("vector") else None
    if vector is None:
        vector = _find(directory, vid, VECTOR_SUFFIXES)
    if vector is None:
        # No file and nothing claimed. Not an error: most episodes have no
        # artwork, and the player shows no square at all for this state.
        _count("none")
        return _blank(vid, "none", "", entry)

    try:
        text = vector.read_text(encoding="utf-8")
    except OSError as exc:
        _count("failed")
        log.warning("episode visuals: could not read %s (%s)", vector, exc)
        return _blank(vid, "failed", "the artwork file could not be read", entry)

    ok, reason = validate_svg(text)
    if not ok:
        _count("failed")
        log.warning("episode visuals: %s rejected - %s", vector.name, reason)
        return _blank(vid, "failed", reason, entry)

    thumbnail = _named(directory, entry["thumbnail"]) if entry.get("thumbnail") else None
    if thumbnail is None:
        thumbnail = _find(directory, vid, THUMBNAIL_SUFFIXES)

    record = _blank(vid, "ready", "", entry)
    record["vector_url"] = _asset_url(vector)
    # The completed line *is* the thumbnail, so the vector stands in when no
    # raster was supplied. A feed tile renders it as a still <img>: one file,
    # two jobs, and no second thing to keep in step with the first.
    record["thumbnail_url"] = _asset_url(thumbnail) if thumbnail else _asset_url(vector)
    record["view_box"] = _view_box(text)
    _count("ready")
    return record


def asset_path(name: str, directory: Optional[Path] = None) -> Optional[Path]:
    """Resolve a servable asset name to a file inside the visuals folder.

    Two gates, because one of them is a rule about names and the other is a
    rule about locations, and a name can pass the first while a symlink fails
    the second.
    """
    if not enabled() or not SAFE_NAME.match(str(name or "")):
        _count("assets_refused")
        return None
    directory = (directory or visuals_dir()).resolve()
    candidate = (directory / name)
    try:
        resolved = candidate.resolve()
        resolved.relative_to(directory)
    except (OSError, ValueError):
        _count("assets_refused")
        return None
    if not resolved.is_file():
        _count("assets_refused")
        return None
    if resolved.suffix.lower() not in VECTOR_SUFFIXES + THUMBNAIL_SUFFIXES:
        _count("assets_refused")
        return None
    _count("assets_served")
    return resolved


MEDIA_TYPES = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def report() -> dict:
    """What /api/health says about artwork on this machine."""
    directory = visuals_dir()
    try:
        vectors = sum(1 for p in directory.glob("*.svg") if p.is_file())
    except OSError:
        vectors = 0
    return {
        "enabled": enabled(),
        "dir": str(directory),
        "dir_exists": directory.is_dir(),
        "vectors": vectors,
        "manifest": (directory / INDEX_NAME).is_file(),
        "counters": counters(),
    }
