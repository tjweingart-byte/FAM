"""Episode thumbnails: one picture per branch of the category tree (§160).

## What this is

A browse tile used to carry one of nine line drawings, chosen by a hand-set
`icon` key - so the tile about short-form video showed a music note under
TECH. This replaces the drawing with a generated picture, and the unit that
gets a picture is **a node of the category tree, never an episode**:

    sports -> american football -> college football

has three pictures, and every episode the tree places under college football
shows the third. The cost therefore grows with the size of the vocabulary
(188 nodes at seed, `categories.MAX_NODES` at most) and not with episodes or
listeners - the same "one shared inventory" argument the script cache makes.

The word on the tile is the node's **facet** - SPORTS, TECH, MONEY - never
the node itself, so a picture of college football is still labelled SPORTS.

## How a picture is made

Three calls per attempt, and two of them are Claude:

1. **A scene, with no names in it.** Claude rewrites the node's path into one
   concrete scene of objects and places. No team, league, company, product,
   person or place names reach the image model, because naming a brand is
   what makes an image model draw its logo; this is the largest single lever
   on logos and it costs a fraction of a cent. Batched, one call for many
   nodes.
2. **Imagen 4** paints it, with `personGeneration: "dont_allow"` - the one
   hard off switch for people any of the candidate models has.
3. **Claude looks at the result** and answers four yes/no questions: a logo
   or emblem, readable text, a person or a face, an identifiable real
   product. Any yes and the picture is thrown away and painted again, up to
   `THUMBNAILS_ATTEMPTS` times. This is the only step that *catches* a logo
   rather than making one less likely.

A node whose subject is a real named thing (a team, a company) is also held
for a person to approve in `/admin/thumbnails` rather than going live on the
checker's word alone - `THUMBNAILS_REVIEW=flagged`, the default.

## Rules that are load-bearing

**Nothing here runs on a read path.** Generation happens in the category
sweep's background task or from `tools/thumbnails.py`; a browse page only
ever reads an in-process map of which nodes have an approved picture.

**A layer that adds quality must not subtract availability**
(`episode_intelligence`'s rule). No key, a timeout, a refusal, a filtered
image, unreadable JSON: every one leaves the tile drawing exactly what it
drew before, and says so in the row's `reason` and on `/api/health`.

**Opened lazily.** A browse page asks this module about every tile, and a
read must not be what creates a database on a machine that never generated a
picture (the trending bank's rule, §139).
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Iterable, Optional
from urllib.parse import quote

from paths import data_path

log = logging.getLogger(__name__)

STATUS_APPROVED = "approved"
STATUS_REVIEW = "review"
STATUS_REJECTED = "rejected"
STATUS_FAILED = "failed"
STATUSES = (STATUS_APPROVED, STATUS_REVIEW, STATUS_REJECTED, STATUS_FAILED)

#: What a tile shows the picture at, and what is stored. A rail card is 150px
#: wide; 480 covers a 3x phone screen with nothing to spare, and a 4:3 WebP at
#: this size is ~20-40 KB - so a full tree is well under 100 MB.
STORED_WIDTH, STORED_HEIGHT = 480, 360
#: What the checker is shown. Larger than what is stored, because a logo small
#: enough to miss at 480px is still a logo.
CHECK_WIDTH = 768

#: The look every picture shares. Imagen 4 takes no negative prompt and no
#: style reference, so this preamble is the whole of the consistency between
#: two hundred pictures - change it and regenerate, never edit it per node.
#: Exclusions are written as what the picture *is* ("plain unmarked
#: surfaces") rather than what it is not, because a model with no negative
#: prompt tends to draw the thing a sentence says to leave out.
HOUSE_STYLE = (
    "Editorial illustration for a podcast episode cover, in a warm, "
    "consistent house style: soft painterly shapes with gentle grain, calm "
    "diffuse light, a muted palette of cream (#EDE6D6), deep plum (#2A2530), "
    "warm gold (#E0B563), coral (#E2694F) and sage (#8FAE9A). One clear "
    "subject in the middle of the frame, seen from a slight distance, with "
    "quiet empty space along the bottom edge. Objects, places and landscapes "
    "only, with plain unmarked surfaces throughout: blank walls, unbranded "
    "equipment, wordless and symbol-free."
)

WRITER_SYSTEM = (
    "You write scene descriptions for an image model that paints podcast "
    "episode covers. Each input is a topic path from broad to specific, like "
    "'sports > american football > college football'. For each one, write a "
    "single concrete visual scene (at most 40 words) that someone would "
    "recognise as that topic.\n\n"
    "Hard rules, because the image model draws whatever is named:\n"
    "- Name no team, league, club, company, brand, product, publication, "
    "person, character or country. Describe a generic equivalent instead: "
    "'an American football stadium at dusk' rather than any team's, 'a "
    "smartphone' rather than any maker's.\n"
    "- No people, faces, hands, silhouettes, crowds or body parts. Use the "
    "objects and places of the topic.\n"
    "- No text, signs, scoreboards with writing, labels, flags, emblems, "
    "jerseys, uniforms or logos.\n"
    "- Make sibling topics in the batch look different from one another.\n\n"
    "Set names_real_entity to true when the topic itself is a specific real "
    "organisation, brand, product, person, team or league (e.g. 'nfl', "
    "'formula one', 'cincinnati bengals'), false for a generic subject "
    "(e.g. 'college football', 'cloud computing')."
)

WRITER_SCHEMA = {
    "type": "object",
    "properties": {
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "node": {"type": "string"},
                    "scene": {"type": "string"},
                    "names_real_entity": {"type": "boolean"},
                },
                "required": ["node", "scene", "names_real_entity"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["scenes"],
    "additionalProperties": False,
}

CHECKER_SYSTEM = (
    "You inspect generated cover illustrations before they are published. "
    "Answer strictly. Anything that could be read as a logo, crest, emblem, "
    "wordmark or trademark counts as a logo, even if distorted. Any letters, "
    "numbers or pseudo-writing count as text. Any human figure, face, "
    "silhouette, hand or body part counts as a person. A recognisable "
    "specific commercial product design (a particular phone, car model, "
    "console, shoe) counts as an identifiable product."
)

CHECKER_SCHEMA = {
    "type": "object",
    "properties": {
        "logo": {"type": "boolean"},
        "text": {"type": "boolean"},
        "person": {"type": "boolean"},
        "identifiable_product": {"type": "boolean"},
        "matches_subject": {"type": "boolean"},
        "notes": {"type": "string"},
    },
    "required": ["logo", "text", "person", "identifiable_product",
                 "matches_subject", "notes"],
    "additionalProperties": False,
}

#: Which checker answers fail a picture. `matches_subject` is here too: a
#: clean picture of the wrong thing is worse on a tile than the drawing it
#: replaces.
_FAILS_ON = ("logo", "text", "person", "identifiable_product")


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------


@dataclass
class Thumb:
    node_id: str
    status: str
    facet: str = ""
    mime: str = ""
    scene: str = ""
    prompt: str = ""
    check: dict = field(default_factory=dict)
    attempts: int = 0
    cost_usd: float = 0.0
    flagged: bool = False
    reason: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    size: int = 0
    #: A repainted picture waiting for a person while the live one stays on
    #: tiles. Approving promotes it; rejecting drops it and keeps the live one.
    pending: bool = False

    def as_dict(self) -> dict:
        return {"node": self.node_id, "status": self.status,
                "pending": self.pending,
                "facet": self.facet, "mime": self.mime, "scene": self.scene,
                "check": self.check, "attempts": self.attempts,
                "cost_usd": round(self.cost_usd, 4), "flagged": self.flagged,
                "reason": self.reason, "updated_at": self.updated_at,
                "bytes": self.size, "url": url_for(self.node_id,
                                                   self.updated_at)}


class ThumbnailStore:
    """One row per node, holding the stored picture and how it was made.

    Beside it, `spend`: one row per image the model was asked for, which is
    what the daily ceiling counts - a count of *rows* would let a node that
    took three attempts cost one.
    """

    def __init__(self, path: str | None = None) -> None:
        self.path = data_path("THUMBNAILS_DB", "thumbnails.db", path)
        self._local = threading.local()
        self._approved: dict[str, tuple[float, str]] = {}
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS thumbnails (
                       node_id    TEXT PRIMARY KEY,
                       status     TEXT NOT NULL,
                       facet      TEXT NOT NULL DEFAULT '',
                       image      BLOB,
                       mime       TEXT NOT NULL DEFAULT '',
                       scene      TEXT NOT NULL DEFAULT '',
                       prompt     TEXT NOT NULL DEFAULT '',
                       check_json TEXT NOT NULL DEFAULT '{}',
                       attempts   INTEGER NOT NULL DEFAULT 0,
                       cost_usd   REAL NOT NULL DEFAULT 0,
                       flagged    INTEGER NOT NULL DEFAULT 0,
                       reason     TEXT NOT NULL DEFAULT '',
                       created_at REAL NOT NULL,
                       updated_at REAL NOT NULL
                   )""")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS spend (
                       at       REAL NOT NULL,
                       node_id  TEXT NOT NULL,
                       images   INTEGER NOT NULL DEFAULT 0,
                       cost_usd REAL NOT NULL DEFAULT 0
                   )""")
            conn.execute("CREATE INDEX IF NOT EXISTS spend_at ON spend(at)")
            have = {r[1] for r in conn.execute(
                "PRAGMA table_info(thumbnails)")}
            for column, kind in (("pending_image", "BLOB"),
                                 ("pending_mime", "TEXT NOT NULL DEFAULT ''"),
                                 ("pending_scene", "TEXT NOT NULL DEFAULT ''"),
                                 ("pending_check", "TEXT NOT NULL DEFAULT '{}'")):
                if column not in have:
                    conn.execute(
                        f"ALTER TABLE thumbnails ADD COLUMN {column} {kind}")
        self._data_version = -1
        self._checked_at = 0.0
        self.reload()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=5.0,
                                   isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    def reload(self) -> None:
        """Rebuild the approved map. Called after every write, never on a
        read - a browse page reads the map and nothing else."""
        try:
            rows = self._conn().execute(
                "SELECT node_id, updated_at, facet FROM thumbnails"
                " WHERE status = ? AND image IS NOT NULL",
                (STATUS_APPROVED,)).fetchall()
        except Exception:
            log.exception("thumbnails: could not read the store")
            return
        self._approved = {r[0]: (float(r[1]), r[2] or "") for r in rows}
        try:
            self._data_version = int(self._conn().execute(
                "PRAGMA data_version").fetchone()[0])
        except Exception:  # noqa: BLE001
            pass
        _bump_generation()

    def approved(self) -> dict[str, tuple[float, str]]:
        return self._approved

    def refresh_if_changed(self, now: Optional[float] = None) -> None:
        """Pick up a write another process made - `tools/thumbnails.py`
        painting or approving against the same database - at most once every
        `REFRESH_SECONDS`. One `PRAGMA data_version` per window, which reads
        a counter and no rows; the map is rebuilt only when it moved."""
        now = time.time() if now is None else now
        if now - self._checked_at < REFRESH_SECONDS:
            return
        self._checked_at = now
        try:
            version = int(self._conn().execute(
                "PRAGMA data_version").fetchone()[0])
        except Exception:  # noqa: BLE001 - a stale map beats a failed tile
            return
        if version != self._data_version:
            self.reload()

    def _row(self, r) -> Thumb:
        try:
            check = json.loads(r[7] or "{}")
        except ValueError:
            check = {}
        return Thumb(node_id=r[0], status=r[1], facet=r[2] or "",
                     mime=r[4] or "", scene=r[5] or "", prompt=r[6] or "",
                     check=check, attempts=int(r[8]), cost_usd=float(r[9]),
                     flagged=bool(r[10]), reason=r[11] or "",
                     created_at=float(r[12]), updated_at=float(r[13]),
                     size=int(r[14] or 0), pending=bool(r[15]))

    _COLUMNS = ("node_id, status, facet, NULL, mime, scene, prompt,"
                " check_json, attempts, cost_usd, flagged, reason,"
                " created_at, updated_at, length(image),"
                " pending_image IS NOT NULL")

    def get(self, node_id: str) -> Optional[Thumb]:
        r = self._conn().execute(
            f"SELECT {self._COLUMNS} FROM thumbnails WHERE node_id = ?",
            (node_id,)).fetchone()
        return self._row(r) if r else None

    def image(self, node_id: str, *, any_status: bool = False,
              pending: bool = False) -> Optional[tuple[bytes, str]]:
        if pending:
            r = self._conn().execute(
                "SELECT pending_image, pending_mime FROM thumbnails"
                " WHERE node_id = ?", (node_id,)).fetchone()
            if not r or r[0] is None:
                return None
            return bytes(r[0]), r[1] or "image/png"
        sql = "SELECT image, mime FROM thumbnails WHERE node_id = ?"
        args: tuple = (node_id,)
        if not any_status:
            sql += " AND status = ?"
            args += (STATUS_APPROVED,)
        r = self._conn().execute(sql, args).fetchone()
        if not r or r[0] is None:
            return None
        return bytes(r[0]), r[1] or "image/png"

    def all(self, status: str = "") -> list[Thumb]:
        sql = f"SELECT {self._COLUMNS} FROM thumbnails"
        args: tuple = ()
        if status:
            sql += " WHERE status = ?"
            args = (status,)
        sql += " ORDER BY updated_at DESC"
        return [self._row(r) for r in self._conn().execute(sql, args)]

    def put(self, thumb: Thumb, image: Optional[bytes]) -> None:
        now = thumb.updated_at or time.time()
        existing = self.get(thumb.node_id)
        created = existing.created_at if existing else now
        self._conn().execute(
            """INSERT INTO thumbnails (node_id, status, facet, image, mime,
                   scene, prompt, check_json, attempts, cost_usd, flagged,
                   reason, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(node_id) DO UPDATE SET
                   status=excluded.status, facet=excluded.facet,
                   image=excluded.image, mime=excluded.mime,
                   scene=excluded.scene, prompt=excluded.prompt,
                   check_json=excluded.check_json,
                   attempts=thumbnails.attempts + excluded.attempts,
                   cost_usd=thumbnails.cost_usd + excluded.cost_usd,
                   flagged=excluded.flagged, reason=excluded.reason,
                   updated_at=excluded.updated_at,
                   pending_image=NULL, pending_mime='', pending_scene='',
                   pending_check='{}'""",
            (thumb.node_id, thumb.status, thumb.facet, image, thumb.mime,
             thumb.scene, thumb.prompt, json.dumps(thumb.check),
             thumb.attempts, thumb.cost_usd, int(thumb.flagged), thumb.reason,
             created, now))
        self.reload()

    def set_status(self, node_id: str, status: str, reason: str = "") -> bool:
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}")
        cur = self._conn().execute(
            "UPDATE thumbnails SET status = ?, reason = ?, updated_at = ?"
            " WHERE node_id = ?", (status, reason, time.time(), node_id))
        self.reload()
        return cur.rowcount > 0

    def put_pending(self, node_id: str, image: bytes, mime: str, scene: str,
                    check: dict, attempts: int, cost_usd: float,
                    reason: str) -> None:
        """Hold a repainted picture for a person without touching the live
        one: its bytes, status and version (so its URL) stay as they were."""
        self._conn().execute(
            "UPDATE thumbnails SET pending_image = ?, pending_mime = ?,"
            " pending_scene = ?, pending_check = ?,"
            " attempts = attempts + ?, cost_usd = cost_usd + ?, reason = ?"
            " WHERE node_id = ?",
            (image, mime, scene, json.dumps(check), attempts, cost_usd,
             reason, node_id))

    def promote_pending(self, node_id: str) -> bool:
        """Put the held picture live, as a new version."""
        cur = self._conn().execute(
            "UPDATE thumbnails SET image = pending_image,"
            " mime = pending_mime, scene = pending_scene,"
            " check_json = pending_check, status = ?, reason = '',"
            " updated_at = ?, pending_image = NULL, pending_mime = '',"
            " pending_scene = '', pending_check = '{}'"
            " WHERE node_id = ? AND pending_image IS NOT NULL",
            (STATUS_APPROVED, time.time(), node_id))
        self.reload()
        return cur.rowcount > 0

    def drop_pending(self, node_id: str) -> bool:
        cur = self._conn().execute(
            "UPDATE thumbnails SET pending_image = NULL, pending_mime = '',"
            " pending_scene = '', pending_check = '{}',"
            " reason = 'new picture rejected; the live one stays'"
            " WHERE node_id = ? AND pending_image IS NOT NULL", (node_id,))
        return cur.rowcount > 0

    def note_failed_repaint(self, node_id: str, attempts: int,
                            cost_usd: float, reason: str) -> None:
        """Count a repaint that produced nothing, and leave the picture, its
        status and its version (so its URL) exactly as they were."""
        self._conn().execute(
            "UPDATE thumbnails SET attempts = attempts + ?,"
            " cost_usd = cost_usd + ?, reason = ? WHERE node_id = ?",
            (attempts, cost_usd, reason, node_id))

    def record_spend(self, node_id: str, images: int, cost_usd: float,
                     at: Optional[float] = None) -> None:
        self._conn().execute(
            "INSERT INTO spend (at, node_id, images, cost_usd) VALUES (?,?,?,?)",
            (time.time() if at is None else at, node_id, images, cost_usd))

    def images_since(self, since: float) -> int:
        r = self._conn().execute(
            "SELECT COALESCE(SUM(images), 0) FROM spend WHERE at >= ?",
            (since,)).fetchone()
        return int(r[0] or 0)

    def report(self) -> dict:
        counts = {s: 0 for s in STATUSES}
        for status, n in self._conn().execute(
                "SELECT status, COUNT(*) FROM thumbnails GROUP BY status"):
            counts[status] = int(n)
        # Repaints of live pictures waiting for a person, counted apart
        # because their row still says approved.
        counts["pending"] = int(self._conn().execute(
            "SELECT COUNT(*) FROM thumbnails WHERE pending_image IS NOT NULL"
        ).fetchone()[0])
        spent = self._conn().execute(
            "SELECT COALESCE(SUM(images),0), COALESCE(SUM(cost_usd),0)"
            " FROM spend").fetchone()
        day = self.images_since(time.time() - 86400)
        return {"counts": counts, "images_generated": int(spent[0]),
                "spend_usd": round(float(spent[1]), 4),
                "images_last_24h": day}


#: How often a read checks whether another process changed the store.
REFRESH_SECONDS = 30.0

_STORE: Optional[ThumbnailStore] = None
_STORE_LOCK = threading.Lock()


def store() -> ThumbnailStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = ThumbnailStore()
        return _STORE


def reset(thumb_store: Optional[ThumbnailStore] = None) -> None:
    """Forget the handle (tests), or install one."""
    global _STORE
    _STORE = thumb_store
    _bump_generation()


def _exists() -> bool:
    """Whether there is a store to read, without creating one."""
    if _STORE is not None:
        return True
    try:
        return os.path.exists(data_path("THUMBNAILS_DB", "thumbnails.db"))
    except Exception:  # noqa: BLE001
        return False


def url_for(node_id: str, version: float = 0.0) -> str:
    """Where a tile fetches a node's picture. The version is the row's
    `updated_at`, so a regenerated picture is a new URL and a year-long
    `Cache-Control` can never serve the old one."""
    base = "/api/thumb/" + quote(node_id, safe="")
    return base + (f"?v={int(version)}" if version else "")


# --------------------------------------------------------------------------
# Choosing a picture for a tile (the read path)
# --------------------------------------------------------------------------

_MEMO: dict[tuple, tuple[str, str, str]] = {}
_MEMO_KEY: tuple = ()
_GENERATION = 0
MAX_MEMO = 4000


def _bump_generation() -> None:
    global _GENERATION
    _GENERATION += 1


def _facet_of_node(tree, node_id: str, facets: frozenset) -> str:
    if node_id in facets:
        return node_id
    chain = tree.ancestors(node_id) if hasattr(tree, "ancestors") else []
    for parent in reversed(chain):
        if parent in facets:
            return parent
    return ""


def pick(text: str, tags: Iterable[str] = ()) -> Optional[dict]:
    """The picture for a tile about `text`, or None to keep the drawing.

    The deepest node the tree finds in the text that has an approved
    picture; failing that, the tile's own declared facet. `tree.match`
    already returns every ancestor of what it matched, so "the deepest one
    with a picture" *is* the walk up the branch - a node whose picture is
    still in review falls back to its parent's without a second rule.

    Never raises and never creates a database: with no store on disk it
    returns None, and the tile draws what it always drew. When one exists,
    the first call in a process opens it, and after that a call reads the
    in-process map plus, at most once every `REFRESH_SECONDS`, one
    `PRAGMA data_version` - which is how a picture painted or approved from
    `tools/thumbnails.py` reaches a running server without a restart.
    """
    try:
        if not _exists():
            return None
        held = store()
        held.refresh_if_changed()
        approved = held.approved()
        if not approved:
            return None
        import topics

        tree = topics.category_tree()
        gen = (getattr(tree, "_loaded_at", 0.0), _GENERATION)
        global _MEMO, _MEMO_KEY
        if gen != _MEMO_KEY:
            _MEMO = {}
            _MEMO_KEY = gen
        tags = tuple(tags or ())
        key = (text or "", tags)
        hit = _MEMO.get(key)
        if hit is None:
            facets = topics.FACETS
            candidates = [n for n in (tree.match(text or "") if text else ())
                          if n in approved]
            candidates.sort(key=lambda n: (-_depth(tree, n, facets), n))
            node = candidates[0] if candidates else ""
            if not node:
                declared = topics.facet_of(tags[0]) if tags else ""
                node = declared if declared in approved else ""
            facet = ""
            if node:
                facet = approved[node][1] or _facet_of_node(tree, node, facets)
            hit = (node, facet, url_for(node, approved[node][0]) if node else "")
            if len(_MEMO) < MAX_MEMO:
                _MEMO[key] = hit
        if not hit[0]:
            return None
        return {"node": hit[0], "facet": hit[1], "url": hit[2]}
    except Exception:  # noqa: BLE001 - a picture is never worth a tile
        log.exception("thumbnails: could not pick a picture")
        return None


def _depth(tree, node_id: str, facets: frozenset) -> int:
    # A facet is depth -1 so any real node under it wins; the tree's own
    # roots are depth 0.
    if node_id in facets:
        return -1
    return tree.depth_of(node_id) if hasattr(tree, "depth_of") else 0


# --------------------------------------------------------------------------
# Generation (background only)
# --------------------------------------------------------------------------


@dataclass
class Scene:
    node: str
    scene: str
    flagged: bool


@dataclass
class Painting:
    image: bytes
    mime: str


class GenerationError(Exception):
    """One attempt failed in a way worth recording."""


class StopRun(Exception):
    """A failure about the deployment, not the picture - a rejected key, a
    billing account, an API that is switched off. Stops the whole run and
    records nothing against the node, because marking two hundred nodes
    failed over one missing setting would make every one of them wait for
    somebody to retry it by hand."""


Writer = Callable[[list[str]], Awaitable[list[Scene]]]
Painter = Callable[[str], Awaitable[Painting]]
Checker = Callable[[bytes, str, str], Awaitable[tuple[dict, float]]]


def path_text(tree, node_id: str) -> str:
    """'sports > american football > college football'."""
    chain = list(reversed(tree.ancestors(node_id))) if node_id not in _facets() \
        else []
    return " > ".join(chain + [node_id])


def _facets() -> frozenset:
    import topics

    return topics.FACETS


def build_prompt(scene: str) -> str:
    return f"{scene.strip().rstrip('.')}. {HOUSE_STYLE}"


_NAMEISH = re.compile(r"[a-z0-9]+")


def names_leak(node_id: str, scene: str, flagged: bool) -> bool:
    """Whether a scene repeats the name of a flagged subject.

    The writer is told not to, and this is the cheap code-side belt to its
    braces: "nfl" in the scene for the node "nfl" is exactly the prompt that
    paints a shield. Only for flagged nodes - "college football" in a scene
    about college football is the point.
    """
    if not flagged:
        return False
    words = _NAMEISH.findall(scene.lower())
    target = _NAMEISH.findall(node_id.lower())
    if not target:
        return False
    n = len(target)
    return any(words[i:i + n] == target for i in range(len(words) - n + 1))


def _price_tokens(usage) -> float:
    """What one Claude call cost, priced from its usage. Labelled priced, not
    billed, the way `metering` says it."""
    from config import settings

    try:
        return (int(getattr(usage, "input_tokens", 0) or 0)
                * settings.thumbnails_claude_input_per_mtok / 1e6
                + int(getattr(usage, "output_tokens", 0) or 0)
                * settings.thumbnails_claude_output_per_mtok / 1e6)
    except Exception:  # noqa: BLE001
        return 0.0


async def claude_writer(paths: list[str]) -> list[Scene]:
    """Ask Claude for a scene per path, in one call."""
    import credentials
    from anthropic_client import build_async_client
    from config import settings

    client = build_async_client(credentials.active("ANTHROPIC_API_KEY"))
    listing = "\n".join(f"- {p}" for p in paths)
    response = await asyncio.wait_for(
        client.messages.create(
            model=settings.thumbnails_model,
            max_tokens=8000,
            system=WRITER_SYSTEM,
            output_config={"effort": "low",
                           "format": {"type": "json_schema",
                                      "schema": WRITER_SCHEMA}},
            messages=[{"role": "user", "content":
                       "Write one scene for each topic path. Use the last "
                       "part of each path, exactly as written, as `node`.\n\n"
                       + listing}],
        ),
        timeout=float(settings.thumbnails_timeout_seconds))
    if getattr(response, "stop_reason", "") == "refusal":
        raise GenerationError("the scene writer declined")
    text = next(b.text for b in response.content if b.type == "text")
    rows = (json.loads(text) or {}).get("scenes") or []
    return [Scene(str(r.get("node", "")).strip().lower(),
                  str(r.get("scene", "")).strip(),
                  bool(r.get("names_real_entity")))
            for r in rows if r.get("scene")]


async def imagen_painter(prompt: str) -> Painting:
    """One Imagen 4 image, people switched off at the model."""
    import httpx

    from config import settings

    key = settings.gemini_api_key
    if not key:
        raise StopRun("GEMINI_API_KEY is not set")
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"{settings.thumbnails_image_model}:predict")
    body = {"instances": [{"prompt": prompt}],
            "parameters": {"sampleCount": 1, "aspectRatio": "4:3",
                           "personGeneration": "dont_allow"}}
    async with httpx.AsyncClient(
            timeout=settings.thumbnails_timeout_seconds) as http:
        resp = await http.post(url, json=body,
                               headers={"x-goog-api-key": key})
    if resp.status_code != 200:
        # The body names the problem (a disabled API, a billing account, a
        # bad key) and never contains the key itself.
        detail = f"Imagen answered {resp.status_code}: {resp.text[:300]}"
        if resp.status_code == 400:
            # A 400 is about this prompt - Imagen refusing it, or a field it
            # did not like - and is recorded against the node, so one bad
            # prompt cannot sit first in the queue and stop every run.
            raise GenerationError(detail)
        # A key, a billing account, a quota, an outage: about the
        # deployment, so the run stops and no node is blamed.
        raise StopRun(detail)
    predictions = (resp.json() or {}).get("predictions") or []
    for p in predictions:
        data = p.get("bytesBase64Encoded")
        if data:
            return Painting(base64.b64decode(data),
                            p.get("mimeType") or "image/png")
    # Imagen returns no image, rather than an error, when its own safety
    # filter drops the result.
    raise GenerationError("Imagen returned no image (filtered)")


async def claude_checker(image: bytes, mime: str, subject: str
                         ) -> tuple[dict, float]:
    """Claude's four yes/no answers about a picture, and what it cost."""
    import credentials
    from anthropic_client import build_async_client
    from config import settings

    client = build_async_client(credentials.active("ANTHROPIC_API_KEY"))
    response = await asyncio.wait_for(
        client.messages.create(
            model=settings.thumbnails_model,
            max_tokens=1000,
            system=CHECKER_SYSTEM,
            output_config={"effort": "low",
                           "format": {"type": "json_schema",
                                      "schema": CHECKER_SCHEMA}},
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": mime,
                    "data": base64.standard_b64encode(image).decode()}},
                {"type": "text", "text":
                    f"This is meant to be a cover for the topic '{subject}'. "
                    "Does it contain a logo, text, a person, or an "
                    "identifiable real product? Does it read as that topic?"},
            ]}],
        ),
        timeout=float(settings.thumbnails_timeout_seconds))
    cost = _price_tokens(getattr(response, "usage", None))
    if getattr(response, "stop_reason", "") == "refusal":
        raise GenerationError("the checker declined")
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text), cost


def _resize(image: bytes, width: int, height: Optional[int], fmt: str
            ) -> tuple[bytes, str]:
    """Crop to 4:3 and scale. Without Pillow the original is returned as it
    came, which is larger and still correct."""
    try:
        from PIL import Image
    except ImportError:
        log.warning("thumbnails: Pillow is not installed, so the picture is "
                    "stored uncropped at full size (pip install Pillow)")
        return image, "image/png"
    with Image.open(io.BytesIO(image)) as im:
        im = im.convert("RGB")
        w, h = im.size
        target = 4 / 3
        if w / h > target:
            cut = int(h * target)
            im = im.crop(((w - cut) // 2, 0, (w - cut) // 2 + cut, h))
        elif w / h < target:
            cut = int(w / target)
            im = im.crop((0, (h - cut) // 2, w, (h - cut) // 2 + cut))
        height = height or int(width * 3 / 4)
        im = im.resize((width, height), Image.LANCZOS)
        out = io.BytesIO()
        if fmt == "webp":
            im.save(out, "WEBP", quality=80, method=6)
            return out.getvalue(), "image/webp"
        im.save(out, "JPEG", quality=88)
        return out.getvalue(), "image/jpeg"


def failed_checks(check: dict) -> list[str]:
    bad = [k for k in _FAILS_ON if check.get(k)]
    if not check.get("matches_subject", True):
        bad.append("off_subject")
    return bad


def _hold(thumb_store: ThumbnailStore, live: bool, node_id: str, facet: str,
          scene: Scene, prompt: str, image: bytes, mime: str, check: dict,
          tries: int, spent: float, reason: str) -> Thumb:
    """Store a picture that waits for a person. Beside the live picture when
    there is one, so tiles keep it until the new one is approved; as the
    node's row when there is not."""
    if live:
        reason += "; the live picture stays until this one is approved"
        thumb_store.put_pending(node_id, image, mime, scene.scene, check,
                                tries, spent, reason)
        return Thumb(node_id, STATUS_REVIEW, facet=facet, mime=mime,
                     scene=scene.scene, prompt=prompt, check=check,
                     attempts=tries, cost_usd=spent, flagged=scene.flagged,
                     reason=reason, updated_at=time.time(), pending=True)
    thumb = Thumb(node_id, STATUS_REVIEW, facet=facet, mime=mime,
                  scene=scene.scene, prompt=prompt, check=check,
                  attempts=tries, cost_usd=spent, flagged=scene.flagged,
                  reason=reason, updated_at=time.time())
    thumb_store.put(thumb, image)
    return thumb


async def make_one(node_id: str, scene: Scene, facet: str, *,
                   painter: Painter, checker: Checker,
                   attempts: int, review: str,
                   thumb_store: ThumbnailStore,
                   room: Optional[Callable[[], bool]] = None) -> Thumb:
    """Paint, check, and repaint until clean or out of attempts, and store
    the outcome. Raises only `StopRun` - a problem with the deployment, or
    the daily ceiling reached before an attempt - and then stores nothing.

    **A node that already has a live picture keeps it** unless the new one
    goes straight to live: a repaint waiting for a person is held beside it
    (`put_pending`), and a repaint that produced nothing only counts its
    attempts. The returned `Thumb` says what happened to *this* run."""
    from config import settings

    previous = thumb_store.get(node_id)
    live = (previous is not None and previous.status == STATUS_APPROVED
            and thumb_store.image(node_id) is not None)
    subject = node_id
    prompt = build_prompt(scene.scene)
    spent = 0.0
    tries = 0
    last_reason = ""
    last_check: dict = {}
    leaked = names_leak(node_id, scene.scene, scene.flagged)
    if leaked:
        last_reason = "the scene named the subject; not painted"
    for _ in range(0 if leaked else max(1, attempts)):
        if room is not None and not room():
            # Checked per image rather than per node, so a node that needs
            # three attempts cannot take the day two images past its ceiling.
            raise StopRun("the daily image ceiling is reached")
        tries += 1
        image_cost = settings.thumbnails_image_price
        try:
            painting = await painter(prompt)
        except StopRun:
            raise
        except GenerationError as exc:
            last_reason = str(exc)
            # Counted against the daily ceiling either way (it was a
            # request). A filtered result returns no image, so it is priced
            # at nothing and painted again; a refused prompt will be refused
            # again, so the node fails now.
            thumb_store.record_spend(node_id, 1, 0.0)
            if "filtered" in last_reason:
                continue
            break
        except Exception as exc:  # noqa: BLE001 - the network, not the node
            raise StopRun(f"painter failed: {exc}") from exc
        spent += image_cost
        thumb_store.record_spend(node_id, 1, image_cost)
        try:
            # Off the event loop: a Lanczos resize and a WebP encode are tens
            # of milliseconds each, and this runs inside a server.
            check_copy, check_mime = await asyncio.to_thread(
                _resize, painting.image, CHECK_WIDTH, None, "jpeg")
            stored, stored_mime = await asyncio.to_thread(
                _resize, painting.image, STORED_WIDTH, STORED_HEIGHT, "webp")
        except Exception as exc:  # noqa: BLE001 - an unreadable image
            last_reason = f"unreadable image: {exc}"
            continue
        try:
            check, check_cost = await checker(check_copy, check_mime, subject)
            spent += check_cost
        except Exception as exc:  # noqa: BLE001
            # **Unchecked is not clean.** A picture nobody inspected never
            # goes live on its own; it waits for a person.
            return _hold(thumb_store, live, node_id, facet, scene, prompt,
                         stored, stored_mime, {}, tries, spent,
                         f"not checked ({exc}); needs a person")
        last_check = check
        bad = failed_checks(check)
        if bad:
            last_reason = "checker found: " + ", ".join(bad)
            continue
        hold = review == "all" or (review == "flagged" and scene.flagged)
        if hold:
            return _hold(thumb_store, live, node_id, facet, scene, prompt,
                         stored, stored_mime, check, tries, spent,
                         "names a real entity; waiting for approval"
                         if scene.flagged else "waiting for approval")
        thumb = Thumb(node_id, STATUS_APPROVED, facet=facet, mime=stored_mime,
                      scene=scene.scene, prompt=prompt, check=check,
                      attempts=tries, cost_usd=spent, flagged=scene.flagged,
                      updated_at=time.time())
        thumb_store.put(thumb, stored)
        return thumb
    reason = last_reason or "no clean picture"
    if live:
        # **A failed repaint keeps the picture that was live.** Repainting is
        # asking for a better one; getting none must not take the one that
        # was already on tiles away and drop them back to the parent's.
        reason = "repaint failed, kept the live picture: " + reason
        thumb_store.note_failed_repaint(node_id, tries, spent, reason)
        return Thumb(node_id, STATUS_FAILED, facet=facet, scene=scene.scene,
                     attempts=tries, cost_usd=spent, flagged=scene.flagged,
                     reason=reason, updated_at=time.time())
    thumb = Thumb(node_id, STATUS_FAILED, facet=facet, scene=scene.scene,
                  prompt=prompt, check=last_check, attempts=tries,
                  cost_usd=spent, flagged=scene.flagged, reason=reason,
                  updated_at=time.time())
    thumb_store.put(thumb, None)
    return thumb


def wanted(tree, thumb_store: ThumbnailStore, *, regenerate: bool = False,
           retry_failed: bool = False, only: Iterable[str] = ()) -> list[str]:
    """Nodes that need a picture, broadest first.

    By default, only nodes with no row at all: a failed node is not retried
    on its own (it failed for a reason, and a sweep that retried it forever
    would spend the daily ceiling on one subject). `retry_failed` takes those
    back; `regenerate` repaints whatever is named, or everything.

    Facets first, then depth, then most listeners: a broad picture covers
    every tile under it while its children wait, so painting top-down is the
    order that covers the most tiles soonest.
    """
    only = [o.strip().lower() for o in only if o and o.strip()]
    facets = _facets()
    nodes = tree.nodes() if hasattr(tree, "nodes") else {}
    if only:
        pool = [o for o in only if o in nodes or o in facets]
    else:
        pool = sorted(facets) + list(nodes)
    have = {t.node_id: t.status for t in thumb_store.all()}
    out = []
    for node_id in pool:
        status = have.get(node_id)
        if status is None or regenerate:
            out.append(node_id)
        elif status == STATUS_FAILED and (retry_failed or only):
            out.append(node_id)

    def order(n: str):
        if n in facets:
            return (-1, 0, n)
        node = nodes.get(n)
        return (node.depth if node else 0, -(node.listeners if node else 0), n)

    return sorted(dict.fromkeys(out), key=order)


def daily_room(thumb_store: ThumbnailStore, now: Optional[float] = None) -> int:
    from config import settings

    now = time.time() if now is None else now
    used = thumb_store.images_since(now - 86400)
    return max(0, settings.thumbnails_daily_images - used)


def configured() -> tuple[bool, str]:
    """Whether generation can run here, and if not, why - one sentence."""
    from config import settings

    import credentials

    if not settings.gemini_api_key:
        return False, "GEMINI_API_KEY is not set"
    if not credentials.active("ANTHROPIC_API_KEY") and not os.environ.get(
            "ANTHROPIC_AUTH_TOKEN"):
        return False, "no Anthropic key for the scene writer and checker"
    return True, ""


_RUNNING = threading.Lock()


async def backfill(limit: int, *, only: Iterable[str] = (),
                   regenerate: bool = False, retry_failed: bool = False,
                   ignore_daily_cap: bool = False,
                   writer: Optional[Writer] = None,
                   painter: Optional[Painter] = None,
                   checker: Optional[Checker] = None,
                   thumb_store: Optional[ThumbnailStore] = None,
                   tree=None) -> dict:
    """Paint up to `limit` nodes that need a picture. Never raises.

    One at a time across the deployment (a second call while one runs
    returns at once), inside the daily image ceiling, and batched through the
    scene writer so a sweep of forty nodes is two Claude calls for scenes
    rather than forty.
    """
    from config import settings

    if not _RUNNING.acquire(blocking=False):
        return {"skipped": "a run is already in progress"}
    try:
        writer = writer or claude_writer
        painter = painter or imagen_painter
        checker = checker or claude_checker
        if writer is claude_writer or painter is imagen_painter:
            ok, why = configured()
            if not ok:
                return {"skipped": why}
        thumb_store = thumb_store or store()
        if tree is None:
            import topics

            tree = topics.category_tree()
        room = limit
        if not ignore_daily_cap:
            # Each node can cost up to `attempts` images; budget for the
            # expected one and a half, and let the ceiling stop the rest.
            room = min(limit, daily_room(thumb_store))
        todo = wanted(tree, thumb_store, regenerate=regenerate,
                      retry_failed=retry_failed, only=only)[:max(0, room)]
        result = {"wanted": len(todo), "approved": 0, "review": 0,
                  "failed": 0, "spend_usd": 0.0, "nodes": []}
        if not todo:
            return result
        facets = _facets()
        batch = max(1, settings.thumbnails_writer_batch)
        for start in range(0, len(todo), batch):
            chunk = todo[start:start + batch]
            paths = {n: path_text(tree, n) for n in chunk}
            try:
                scenes = await writer(list(paths.values()))
            except Exception as exc:  # noqa: BLE001
                log.warning("thumbnails: the scene writer failed (%s); "
                            "%d nodes wait for the next run", exc, len(chunk))
                result.setdefault("errors", []).append(f"writer: {exc}")
                continue
            by_node = {s.node: s for s in scenes}
            for node_id in chunk:
                scene = by_node.get(node_id)
                if scene is None or not scene.scene:
                    result.setdefault("errors", []).append(
                        f"no scene for {node_id!r}")
                    continue
                if not ignore_daily_cap and daily_room(thumb_store) <= 0:
                    result["capped"] = True
                    return result
                facet = node_id if node_id in facets else \
                    _facet_of_node(tree, node_id, facets)
                try:
                    thumb = await make_one(
                        node_id, scene, facet, painter=painter,
                        checker=checker,
                        attempts=settings.thumbnails_attempts,
                        review=settings.thumbnails_review,
                        thumb_store=thumb_store,
                        room=None if ignore_daily_cap
                        else (lambda: daily_room(thumb_store) > 0))
                except StopRun as exc:
                    if "ceiling" in str(exc):
                        result["capped"] = True
                        return result
                    log.warning("thumbnails: stopping the run - %s", exc)
                    result["stopped"] = str(exc)
                    return result
                key = {STATUS_APPROVED: "approved", STATUS_REVIEW: "review"
                       }.get(thumb.status, "failed")
                result[key] += 1
                result["spend_usd"] = round(result["spend_usd"]
                                            + thumb.cost_usd, 4)
                result["nodes"].append({"node": node_id,
                                        "status": thumb.status,
                                        "reason": thumb.reason})
        return result
    except Exception as exc:  # noqa: BLE001 - never worth a failed sweep
        log.exception("thumbnails: the backfill failed")
        return {"failed_run": str(exc)}
    finally:
        _RUNNING.release()


def estimate(nodes: int) -> dict:
    """What painting `nodes` pictures should cost, before spending anything."""
    from config import settings

    expected_images = nodes * settings.thumbnails_expected_attempts
    image = expected_images * settings.thumbnails_image_price
    # A check is ~1.5k input tokens (the image) and ~100 out; a scene is a
    # share of one batched call.
    check = expected_images * (
        1600 * settings.thumbnails_claude_input_per_mtok / 1e6
        + 120 * settings.thumbnails_claude_output_per_mtok / 1e6)
    scenes = nodes * (60 * settings.thumbnails_claude_input_per_mtok / 1e6
                      + 70 * settings.thumbnails_claude_output_per_mtok / 1e6)
    return {"nodes": nodes, "expected_images": round(expected_images, 1),
            "images_usd": round(image, 2),
            "claude_usd": round(check + scenes, 2),
            "total_usd": round(image + check + scenes, 2),
            "basis": "priced from list prices, not billed"}


def health() -> dict:
    """What `/api/health` says. Opens nothing that does not exist."""
    from config import settings

    ok, why = configured()
    out = {"generation": settings.thumbnails,
           "configured": ok, "image_model": settings.thumbnails_image_model,
           "review": settings.thumbnails_review}
    if not ok:
        out["reason"] = why
    if _exists():
        try:
            out.update(store().report())
        except Exception as exc:  # noqa: BLE001
            out["error"] = str(exc)
    else:
        out["counts"] = {s: 0 for s in STATUSES}
    return out
