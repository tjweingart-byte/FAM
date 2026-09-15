"""One episode, one drawing - the part that decides when and keeps the result.

Everything else in this feature is a step; this is the thing that runs the
steps, remembers what happened, and refuses to run them twice.

    request() -> queued -> generating -> processing -> validating -> ready
                                    \\-> failed

Five constraints shape the whole module, and each of them is somewhere this
project has been bitten before:

* **Nothing here may ever be in front of a listener.** `request` starts a
  background task and returns immediately; every CPU-bound step runs in a
  thread, because audio is streaming on the same event loop and the
  skeletoniser is heavy enough to be *heard* if it were not. There is no code
  path on which an episode waits for a picture.
* **One episode is one job.** A tap, a re-tap, a retry, a duplicate event and a
  server restart must produce one illustration between them. The key is the
  idempotency token, the row is the claim, and a claim older than
  `STALE_CLAIM_SECONDS` is assumed to have died with whatever process made it.
* **The key does not include the length, the voice or the model.** A
  three-minute episode and a ten-minute one about the same question are
  different scripts and the same subject, so they share a drawing - the same
  reasoning that keeps voice out of the script cache key. It *does* include the
  style version, so changing the house style retires every old picture rather
  than leaving a feed that is half one style and half another.
* **A layer that adds quality must not subtract availability.** No key, a
  refused request, unreadable art, a validator that says no - every one of them
  ends as `failed` on a record, and the episode plays exactly as it did before
  this feature existed. The blank ivory square is a designed state, not an
  error state.
* **Nothing personal is drawn.** An episode with attachments is that
  listener's alone and never gets a shared illustration, for the same reason
  the script cache refuses it: a picture in a shared store is a picture another
  listener can be shown.

**exploreFAM is excluded here, not only in the interface.** A replay-only
request is refused by `eligible`, so even a client that asked for a visual on
Explore would not get one and would not cause one to be drawn.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

import line_processor
import understanding
import visual_director
import visual_provider
import visual_style
import visual_validator
from cache import is_shareable, normalize_query
from config import settings
from paths import PROJECT_ROOT, data_path

log = logging.getLogger(__name__)

#: Bumped when what an illustration *is* changes - the style, the vector
#: contract, the way the path is ordered. Part of the key, so a bump retires
#: every stored drawing instead of mixing two generations of them in one feed.
VISUAL_VERSION = 1

#: The states a drawing can be in. `unconfigured` is deliberately distinct from
#: `failed`: nothing is broken, nothing was tried, and the two want different
#: sentences on a health page and different behaviour from a client (one is
#: worth polling, the other is not).
STATES = ("none", "queued", "generating", "processing", "validating", "ready",
          "failed", "unconfigured")
#: States a client should keep polling through.
PENDING_STATES = ("queued", "generating", "processing", "validating")

#: A claim older than this belonged to a process that is gone. Long enough that
#: a slow provider is not robbed of its job mid-flight; short enough that a
#: restart does not leave an episode illustrated by nobody.
STALE_CLAIM_SECONDS = 600.0

#: What each attempt changes, in words, for the log and the stored record. The
#: prompt text itself lives in `visual_style` - these are labels, not the
#: instruction - and the third rung's history is documented there, at
#: `CONNECTED_RICHNESS_INSISTENCE`.
RETRY_LADDER = (
    "standard FAM direction",
    "stronger continuous-line instructions",
    "the same scene, composed so its parts touch",
)


def _rung(attempt: int) -> str:
    """Which rung an attempt is on, in words. Attempts past the last rung stay
    on the last rung - `VISUAL_MAX_RETRIES` is settable and the ladder is
    not."""
    return RETRY_LADDER[min(attempt, len(RETRY_LADDER)) - 1]


# --------------------------------------------------------------------------
# The key
# --------------------------------------------------------------------------
def key_for(query: str, context: str = "") -> str:
    """Where this episode's drawing lives.

    Module-level for the same reason `pipeline.key_for` is: prefetching a
    drawing before the tap and looking one up on the tap are two pieces of code
    that must compute the same string, and two implementations that agree today
    drift the first time one of them gains a field. If you add something that
    changes *what the picture is*, it goes in here and nowhere else.

    Returns "" for an episode nobody else may see.
    """
    query = (query or "").strip()
    if not query or not is_shareable(query):
        return ""
    payload = json.dumps(
        {
            "q": normalize_query(query),
            # A follow-up is a different episode, and deserves its own picture.
            "ctx": (context or "").strip(),
            "style": visual_style.STYLE.name,
            "style_version": visual_style.STYLE.version,
            "v": VISUAL_VERSION,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


#: Surfaces that get a drawing. Named rather than inferred, so that adding a
#: surface is a decision somebody wrote down - and so `explore` being absent is
#: visible in the source rather than being the consequence of a flag somewhere.
ELIGIBLE_SURFACES = ("search", "myfam", "dailyfam", "godeeper", "saved",
                     "album", "warm")
#: exploreFAM, and nothing else. Kept as a constant so the exclusion can be
#: asserted by a test rather than described in a comment.
EXCLUDED_SURFACES = ("explore",)


def eligible(query: str, *, surface: str = "search", cached_only: bool = False,
             attachments=()) -> bool:
    """Whether this episode may have a drawing at all.

    Three separate refusals, each of which is a settled constraint elsewhere:

    * **Explore never generates anything.** It replays finished episodes and is
      built on the promise that it cannot spend. A picture is a spend.
    * **An attached episode is personal.** `pipeline` refuses to cache it, so
      this refuses to draw it - a shared illustration is reachable by another
      listener by definition.
    * **An unshareable question is unshareable in both media.** The script
      cache's own rule, applied to the picture, so the two can never disagree
      about whether an episode is private.
    """
    if cached_only or surface in EXCLUDED_SURFACES:
        return False
    if attachments:
        return False
    return bool(key_for(query))


# --------------------------------------------------------------------------
# The record
# --------------------------------------------------------------------------
@dataclass
class VisualRecord:
    id: str = ""
    status: str = "none"
    version: int = VISUAL_VERSION
    query: str = ""
    context: str = ""
    surface: str = ""
    #: Why this episode was thought worth drawing, in words. Survives to the
    #: report, because the only way to judge what is worth drawing ahead of a
    #: tap is to see which kinds of guess got looked at.
    reason: str = ""
    provider: str = ""
    model: str = ""
    brief: dict = field(default_factory=dict)
    d: str = ""
    view_box: str = f"0 0 {visual_style.VIEWBOX} {visual_style.VIEWBOX}"
    background: str = visual_style.PAPER
    stroke: str = visual_style.INK
    stroke_width: float = visual_style.STROKE_WIDTH
    metrics: dict = field(default_factory=dict)
    attempts: int = 0
    error: str = ""
    cost_usd: float = 0.0
    created: float = 0.0
    updated: float = 0.0
    claimed: float = 0.0
    total_latency_ms: int = 0
    provider_latency_ms: int = 0
    processing_latency_ms: int = 0

    def as_dict(self) -> dict:
        return asdict(self)

    def public(self) -> dict:
        """What a client is told.

        The path is sent inline when it is ready rather than as a URL to fetch,
        because the player wants the geometry and a second round trip in front
        of a picture that is already late is a round trip for nothing. The URLs
        are sent too: an iOS client, a share card and an `<img>` all want a
        file, and only the web player wants the raw `d`.
        """
        payload = {
            "status": self.status,
            "version": self.version,
            "id": self.id,
            "background_color": self.background,
            "stroke_color": self.stroke,
            "stroke_width": self.stroke_width,
            "provider": self.provider,
            # A placeholder must never be mistaken for the real thing - the
            # rule this project keeps paying to relearn. The client labels it.
            "placeholder": self.provider == "synthetic",
        }
        if self.status == "ready":
            payload.update({
                "d": self.d,
                "view_box": self.view_box,
                "vector_url": f"/api/visual/{self.id}.svg",
                "thumbnail_url": f"/api/visual/{self.id}.png",
                "subject": self.brief.get("subject", ""),
            })
        elif self.status in ("failed", "unconfigured"):
            payload["reason"] = self.error
        return payload


# --------------------------------------------------------------------------
# The store
# --------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS visuals (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    version INTEGER NOT NULL,
    query TEXT NOT NULL DEFAULT '',
    context TEXT NOT NULL DEFAULT '',
    surface TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    brief TEXT NOT NULL DEFAULT '{}',
    d TEXT NOT NULL DEFAULT '',
    view_box TEXT NOT NULL DEFAULT '',
    background TEXT NOT NULL DEFAULT '',
    stroke TEXT NOT NULL DEFAULT '',
    stroke_width REAL NOT NULL DEFAULT 1.4,
    metrics TEXT NOT NULL DEFAULT '{}',
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    cost_usd REAL NOT NULL DEFAULT 0,
    created REAL NOT NULL DEFAULT 0,
    updated REAL NOT NULL DEFAULT 0,
    claimed REAL NOT NULL DEFAULT 0,
    total_latency_ms INTEGER NOT NULL DEFAULT 0,
    provider_latency_ms INTEGER NOT NULL DEFAULT 0,
    processing_latency_ms INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS visuals_status ON visuals(status);
CREATE INDEX IF NOT EXISTS visuals_updated ON visuals(updated);
"""


class VisualStore:
    """Metadata in sqlite, assets on disk.

    The `d` attribute lives in the database rather than only in the file
    because it is the thing every request wants and it is a few kilobytes of
    text; the files exist so that a drawing is a *thing on disk* - inspectable,
    copyable, servable - rather than something that only exists inside a
    process. `metadata.json` beside them is what makes a folder of these
    readable a year later without this code.
    """

    def __init__(self, path: str | None = None, assets: str | None = None) -> None:
        self.path = data_path("FAM_VISUAL_DB", "fam-visuals.db", path)
        self.assets = Path(assets or os.environ.get("FAM_VISUAL_DIR", "")
                           or (PROJECT_ROOT / "episode-visuals"))
        self.assets.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    def get(self, visual_id: str):
        if not visual_id:
            return None
        row = self._conn().execute(
            "SELECT * FROM visuals WHERE id = ?", (visual_id,)).fetchone()
        return _from_row(row) if row else None

    def put(self, record: VisualRecord) -> None:
        record.updated = time.time()
        payload = record.as_dict()
        payload["brief"] = json.dumps(record.brief, default=str)
        payload["metrics"] = json.dumps(record.metrics, default=str)
        columns = ", ".join(payload)
        marks = ", ".join(f":{name}" for name in payload)
        with self._conn() as conn:
            conn.execute(
                f"INSERT INTO visuals ({columns}) VALUES ({marks}) "
                f"ON CONFLICT(id) DO UPDATE SET "
                + ", ".join(f"{name}=excluded.{name}" for name in payload
                            if name != "id"),
                payload)

    def claim(self, record: VisualRecord) -> bool:
        """Take the job, or find that somebody else already has it.

        One statement, so two processes racing on the same episode cannot both
        win: the `WHERE` is the check and the `UPDATE` is the claim, and sqlite
        does them together.
        """
        now = time.time()
        with self._conn() as conn:
            existing = conn.execute("SELECT status, claimed, attempts FROM visuals "
                                    "WHERE id = ?", (record.id,)).fetchone()
            if existing is None:
                record.created = record.created or now
                record.claimed = now
                record.status = "generating"
                self.put(record)
                return True
            if existing["status"] == "ready":
                return False
            if (existing["status"] in PENDING_STATES
                    and now - (existing["claimed"] or 0) < STALE_CLAIM_SECONDS):
                return False
            cursor = conn.execute(
                "UPDATE visuals SET status='generating', claimed=?, updated=? "
                "WHERE id = ? AND claimed = ?",
                (now, now, record.id, existing["claimed"]))
            if cursor.rowcount != 1:
                return False
        record.attempts = existing["attempts"]
        return True

    def mark(self, record: VisualRecord, status: str, error: str = "") -> None:
        record.status = status
        if error:
            record.error = error
        self.put(record)

    def recent(self, limit: int = 40) -> list:
        rows = self._conn().execute(
            "SELECT * FROM visuals ORDER BY updated DESC LIMIT ?",
            (limit,)).fetchall()
        return [_from_row(row) for row in rows]

    def counts(self) -> dict:
        rows = self._conn().execute(
            "SELECT status, COUNT(*) AS n FROM visuals GROUP BY status").fetchall()
        return {row["status"]: row["n"] for row in rows}

    def spent_since(self, since: float) -> float:
        row = self._conn().execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM visuals "
            "WHERE updated >= ?", (since,)).fetchone()
        return float(row["total"] or 0.0)

    def folder(self, visual_id: str) -> Path:
        path = self.assets / visual_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_assets(self, record: VisualRecord, *, source: bytes, svg: str,
                     thumbnail: bytes) -> None:
        folder = self.folder(record.id)
        try:
            if source:
                (folder / "source.png").write_bytes(source)
            (folder / "visual.svg").write_text(svg, encoding="utf-8")
            (folder / "thumbnail.png").write_bytes(thumbnail)
            (folder / "metadata.json").write_text(
                json.dumps({**record.as_dict(), "d": record.d[:120] + "…"},
                           indent=2, default=str), encoding="utf-8")
        except OSError as exc:
            # The database already holds the path and the metrics, so a
            # read-only asset directory costs the files and not the picture.
            log.warning("could not write visual assets for %s: %s", record.id, exc)

    def thumbnail(self, visual_id: str) -> bytes:
        path = self.assets / visual_id / "thumbnail.png"
        try:
            return path.read_bytes()
        except OSError:
            return b""


def _from_row(row) -> VisualRecord:
    data = dict(row)
    data["brief"] = _loads(data.get("brief"))
    data["metrics"] = _loads(data.get("metrics"))
    return VisualRecord(**data)


def _loads(text):
    try:
        return json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}


_STORE: VisualStore | None = None
_STORE_LOCK = threading.Lock()


def store() -> VisualStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = VisualStore()
        return _STORE


def reset(path: str | None = None, assets: str | None = None) -> None:
    """Point the store somewhere else, and forget everything in this process.

    Tests, and nothing else - but *everything*, which is the part worth
    stating. The in-flight set, the counters, the latency totals and the
    "somebody is listening right now" clock are all module state, and a reset
    that left any of them behind would leak one test into the next: a key
    still in `_INFLIGHT` makes the next identical request a silent no-op, and a
    stale live-generation clock makes every warm stand aside forever.
    """
    global _STORE, _INFLIGHT, _COUNTERS, _LATENCY, _LIVE_AT
    with _STORE_LOCK:
        _STORE = VisualStore(path, assets) if (path or assets) else None
    _INFLIGHT = set()
    _COUNTERS = dict.fromkeys(TELEMETRY, 0)
    _LATENCY = {}
    _LIVE_AT = 0.0


# --------------------------------------------------------------------------
# Telemetry
# --------------------------------------------------------------------------
#: Every event this feature emits. A list rather than ad-hoc log lines so the
#: counters can be reported, and so a name cannot be invented in one place and
#: looked for under a different spelling in another.
TELEMETRY = (
    "visual_job_queued", "visual_generation_started",
    "visual_generation_completed", "visual_generation_failed",
    "visual_processing_started", "visual_processing_failed",
    "visual_validation_failed", "visual_retry", "visual_ready",
    "visual_skipped", "visual_asset_loaded", "visual_asset_cache_hit",
    "visual_asset_failed", "visual_parse_failed",
)
_COUNTERS: dict = dict.fromkeys(TELEMETRY, 0)
#: Latencies, kept as running totals and counts so an average is available
#: without storing every sample.
_LATENCY: dict = {}


def track(event: str, **fields) -> None:
    """One log line and one counter, for everything worth counting.

    Goes through FAM's own logging rather than anywhere new: a feature that
    brings its own telemetry system is a feature whose numbers nobody sees
    beside everything else's.
    """
    if event not in _COUNTERS:
        _COUNTERS[event] = 0
    _COUNTERS[event] += 1
    if fields:
        log.info("%s %s", event, " ".join(f"{k}={v!r}" for k, v in fields.items()))
    else:
        log.info("%s", event)


def track_latency(name: str, milliseconds: int) -> None:
    total, count = _LATENCY.get(name, (0, 0))
    _LATENCY[name] = (total + int(milliseconds), count + 1)


def latencies() -> dict:
    """Averages, or `None` where there is no data.

    `None` rather than `0`: zero out of zero reads as "instant" or "broken",
    and it is actually silence. The same rule prefetch reports its hit rate
    under, for the same reason.
    """
    out = {}
    for name in ("visual_total_latency_ms", "visual_provider_latency_ms",
                 "visual_processing_latency_ms"):
        total, count = _LATENCY.get(name, (0, 0))
        out[name] = round(total / count) if count else None
    return out


# --------------------------------------------------------------------------
# Running one
# --------------------------------------------------------------------------
#: When set, every drawing writes its intermediate stages into this directory:
#: the director's brief, the exact prompt sent, the artwork as it came back,
#: the ink mask, the skeleton, the route before smoothing, the finished line and
#: an overlay of the two. `None` in production and nothing is written.
#:
#: A module global rather than an argument threaded through `request`, because
#: every caller on the audio path would have to pass `None` to a parameter that
#: exists for one diagnostic tool. `tools/visual_trace.py` sets it, and it is
#: the only thing that does.
TRACE_DIR = None

_INFLIGHT: set = set()
_SEMAPHORE: asyncio.Semaphore | None = None
_SEMAPHORE_LOOP = None
_LIVE_AT: float = 0.0


def note_live_generation() -> None:
    """A listener is waiting on an episode right now.

    Warming a drawing nobody asked for while somebody is waiting on one they
    did is the same inversion prefetch guards against, so warm-ahead work
    stands aside for a moment. A drawing for the episode being generated is
    *not* warm-ahead work and never stands aside.
    """
    global _LIVE_AT
    _LIVE_AT = time.monotonic()


def _quiet() -> bool:
    if not _LIVE_AT:
        return True
    return (time.monotonic() - _LIVE_AT) >= settings.visual_quiet_seconds


def _semaphore() -> asyncio.Semaphore:
    """One semaphore per event loop.

    Rebuilt when the loop changes because an asyncio primitive bound to a dead
    loop raises at the worst possible moment - and every test client makes a
    new loop.
    """
    global _SEMAPHORE, _SEMAPHORE_LOOP
    loop = asyncio.get_running_loop()
    if _SEMAPHORE is None or _SEMAPHORE_LOOP is not loop:
        _SEMAPHORE = asyncio.Semaphore(max(1, settings.visual_concurrency))
        _SEMAPHORE_LOOP = loop
    return _SEMAPHORE


def within_budget() -> tuple[bool, str]:
    """Whether today's drawing budget has anything left in it.

    A ceiling and not a quota: it stops a runaway, it does not ration
    listeners. Priced from the provider's published rate, which is what
    `metering` calls the difference between a number that is billed and a
    number that is assumed.
    """
    ceiling = float(settings.visual_daily_budget_usd)
    if ceiling <= 0:
        return True, ""
    spent = store().spent_since(time.time() - 86400)
    if spent >= ceiling:
        return False, (f"today's illustration budget is spent "
                       f"(${spent:.2f} of ${ceiling:.2f}); "
                       "raise VISUAL_DAILY_BUDGET_USD or wait for it to roll over")
    return True, ""


def request(query: str, *, context: str = "", surface: str = "search",
            reason: str = "", topic: str = "",
            brief=None, evidence: str = "", cached_only: bool = False,
            attachments=(), live: bool = True, notes=None) -> str:
    """Ask for this episode's drawing. Returns immediately, always.

    The one thing this function guarantees is that it does not wait. It decides
    whether a job is wanted, starts it on the event loop if so, and returns the
    key - so the caller can be on the audio path without knowing or caring that
    it is.

    Returns "" when nothing was started, which covers every refusal: not
    eligible, the feature is off, no provider, already drawn, already running,
    out of budget, or a browse-surface warm that is standing aside for a live
    listener.
    """
    if not settings.visuals:
        return ""
    if not eligible(query, surface=surface, cached_only=cached_only,
                    attachments=attachments):
        return ""
    visual_id = key_for(query, context)
    if not visual_id:
        return ""
    if visual_id in _INFLIGHT:
        return visual_id
    existing = store().get(visual_id)
    if existing is not None:
        if existing.status == "ready":
            return visual_id
        if (existing.status in PENDING_STATES
                and time.time() - (existing.claimed or 0) < STALE_CLAIM_SECONDS):
            return visual_id
        if existing.status in ("failed", "unconfigured"):
            if existing.attempts >= settings.visual_max_retries:
                return ""
    if not live and not _quiet():
        track("visual_skipped", why="a listener is waiting on an episode",
              query=query)
        return ""
    ready, why = within_budget()
    if not ready:
        track("visual_skipped", why=why, query=query)
        return ""
    if live:
        note_live_generation()

    record = existing or VisualRecord(
        id=visual_id, query=query, context=context, surface=surface,
        reason=reason, created=time.time())
    record.surface = record.surface or surface
    record.reason = record.reason or reason

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Called from a thread with no loop - `write.py`, a management command.
        # There is nowhere to run it, and saying so beats pretending.
        log.debug("no event loop; not drawing %r", query)
        return ""

    _INFLIGHT.add(visual_id)
    track("visual_job_queued", id=visual_id, surface=surface, query=query,
          reason=reason)
    task = loop.create_task(_run(record, brief=brief, evidence=evidence,
                                 topic=topic, notes=notes))
    # Held so the loop does not garbage-collect a task nobody awaits, and
    # discarded on completion so the set is not a slow leak.
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return visual_id


_TASKS: set = set()


async def _run(record: VisualRecord, *, brief=None, evidence: str = "",
               topic: str = "", notes=None,
               force: bool = False) -> None:
    """The whole job, off the audio path.

    Wrapped so that nothing in here can escape into the event loop: a drawing
    that fails must leave an episode that plays.
    """
    started = time.monotonic()
    try:
        async with _semaphore():
            await _draw(record, brief=brief, evidence=evidence, topic=topic,
                        notes=notes, force=force)
    except Exception as exc:  # noqa: BLE001 - availability outranks diagnosis
        log.exception("drawing %r failed unexpectedly", record.query)
        record.error = str(exc)
        store().mark(record, "failed", str(exc))
        track("visual_generation_failed", id=record.id, error=str(exc))
    finally:
        _INFLIGHT.discard(record.id)
        elapsed = int((time.monotonic() - started) * 1000)
        record.total_latency_ms = elapsed
        track_latency("visual_total_latency_ms", elapsed)


def _trace_for(record: VisualRecord, attempt: int, direction):
    """This attempt's trace directory, or None when tracing is off.

    Per attempt, because the retry ladder is the interesting part when art
    keeps failing: attempt 1 and attempt 3 are different prompts and different
    pictures, and one folder holding whichever ran last answers nothing.
    """
    if TRACE_DIR is None:
        return None
    from pathlib import Path

    folder = Path(TRACE_DIR) / f"attempt-{attempt}"
    folder.mkdir(parents=True, exist_ok=True)
    _write_trace(folder, "director-brief.json",
                 json.dumps(direction.as_dict(), indent=2, default=str))
    return folder


def _write_trace(folder, name: str, text: str) -> None:
    try:
        (folder / name).write_text(text, encoding="utf-8")
    except OSError as exc:  # noqa: BLE001 - a trace never breaks a drawing
        log.warning("could not write trace %s: %s", name, exc)


async def _prepare(record: VisualRecord, *, brief, evidence: str,
                   topic: str, notes, force: bool) -> tuple | None:
    """Everything that happens once, before the first attempt.

    Returns `(provider, direction, references)`, or `None` when there is
    nothing to draw - already claimed, or no provider configured. Split out of
    `_draw` because it runs once and the loop runs up to three times, and a
    reader chasing a retry should not have to step past the claim, the
    understanding and the art direction to find it.
    """
    if force:
        # A deliberate redo. The claim check exists to stop *accidental*
        # duplicates, and refusing an administrator who asked for one by name
        # would be the check doing the opposite of its job.
        record.claimed = time.time()
        store().mark(record, "generating")
    elif not store().claim(record):
        log.debug("%s is already being drawn", record.id)
        return None

    provider = visual_provider.build_provider()
    ready, why = provider.configured()
    if not ready:
        store().mark(record, "unconfigured", why)
        track("visual_generation_failed", id=record.id, error=why)
        return None
    record.provider = provider.name
    record.model = getattr(provider, "model", "")

    # --- the same understanding the writer is given -----------------------
    # Waited for, not re-derived. On search the script path is running EI at
    # this moment and publishes the result; arriving a few seconds later with
    # the real brief costs the picture nothing, because nobody is waiting on
    # it, and buys an illustration that is about the same episode the words
    # are about. A timeout is not a failure: the director falls back to the
    # query, which is what a browse-surface warm uses anyway.
    if brief is None:
        shared = await understanding.wait(
            record.id, timeout=settings.visual_understanding_wait_seconds)
        if shared is not None:
            brief = shared.brief
            evidence = evidence or shared.evidence
            log.info("visual for %r is using the episode's own understanding",
                     record.query)

    direction = await visual_director.direct(
        record.query, context=record.context, brief=brief, evidence=evidence,
        topic=topic, surface=record.surface, notes=notes)
    record.brief = direction.as_dict()
    store().put(record)

    references = visual_style.references() if settings.visual_use_references else []
    if references:
        log.info("drawing %r in the style of %s", record.query,
                 ", ".join(ref.name for ref in references))
    elif settings.visual_use_references:
        # Worth a line every time rather than only on a health page. A
        # deployment that lost `visual_references/` still draws, still reports
        # healthy, and quietly stops looking like FAM.
        log.warning("no approved references in %s - the house style is being "
                    "described in words only", visual_style.REFERENCE_DIR)
    return provider, direction, references


async def _draw(record: VisualRecord, *, brief=None, evidence: str = "",
                topic: str = "", notes=None,
                force: bool = False) -> None:
    """One episode's drawing, attempt by attempt.

    The loop is the retry ladder: generate, screen the art, vectorise, judge,
    and either store it or go round again with a stronger instruction. Every
    exit leaves a state on the record - `ready`, `failed` or `unconfigured` -
    because a picture must never be able to take an episode down with it.
    """
    prepared = await _prepare(record, brief=brief, evidence=evidence,
                              topic=topic, notes=notes, force=force)
    if prepared is None:
        return
    provider, direction, references = prepared

    last_error = ""
    for attempt in range(record.attempts + 1,
                         settings.visual_max_retries + 1):
        record.attempts = attempt
        if attempt > 1:
            track("visual_retry", id=record.id, attempt=attempt,
                  ladder=_rung(attempt))
        store().mark(record, "generating")
        track("visual_generation_started", id=record.id, attempt=attempt,
              provider=provider.name, model=record.model)
        trace = _trace_for(record, attempt, direction)
        try:
            image = await provider.generate(direction, attempt=attempt,
                                            references=references)
        except visual_provider.VisualProviderUnconfigured as exc:
            store().mark(record, "unconfigured", str(exc))
            track("visual_generation_failed", id=record.id, error=str(exc))
            return
        except visual_provider.VisualProviderError as exc:
            last_error = str(exc)
            track("visual_generation_failed", id=record.id, attempt=attempt,
                  error=last_error)
            continue
        record.cost_usd += image.cost_usd
        record.provider_latency_ms = image.latency_ms
        track_latency("visual_provider_latency_ms", image.latency_ms)
        track("visual_generation_completed", id=record.id, attempt=attempt,
              ms=image.latency_ms, cost=round(image.cost_usd, 4))

        # Is the artwork worth vectorising? Asked before any of the work,
        # because the answer to art that is an icon, cluttered or coloured is
        # a different picture - and because a pipeline good enough to rescue
        # it would quietly fill the feed with pictograms. Art first.
        screening = visual_validator.screen_source(image.data)
        if trace is not None:
            _write_trace(trace, "source-screening.json",
                         json.dumps(screening.as_dict(), indent=2))
        if not screening.ok:
            last_error = "; ".join(screening.reasons)
            track("visual_source_rejected", id=record.id, attempt=attempt,
                  reasons=screening.reasons, metrics=screening.metrics)
            record.metrics = {"source_screening": screening.as_dict()}
            store().put(record)
            continue
        if screening.advisories:
            # Noticed and drawn anyway. A richness heuristic is a rejection aid
            # and not a definition of good FAM art - a spare, sophisticated
            # composition scores low on every one of them - so this is a line
            # in the log and a field on the record, never a refusal.
            log.info("drawing %r despite: %s", record.query,
                     "; ".join(screening.advisories))
            track("visual_source_advisory", id=record.id, attempt=attempt,
                  advisories=screening.advisories, metrics=screening.metrics)

        if trace is not None:
            _write_trace(trace, "prompt.txt", image.prompt)

        store().mark(record, "processing")
        track("visual_processing_started", id=record.id, attempt=attempt)
        processing_started = time.monotonic()
        try:
            # In a thread, without exception. Skeletonising a megapixel is
            # tenths of a second of solid CPU, and this loop is streaming
            # somebody's episode.
            art = await asyncio.to_thread(line_processor.process, image.data,
                                          trace)
        except line_processor.LineProcessingError as exc:
            last_error = str(exc)
            track("visual_processing_failed", id=record.id, attempt=attempt,
                  reason=exc.reason, error=last_error)
            continue
        except Exception as exc:  # noqa: BLE001
            last_error = f"the line processor failed: {exc}"
            log.exception("line processing failed for %s", record.id)
            track("visual_processing_failed", id=record.id, attempt=attempt,
                  error=last_error)
            continue
        processing_ms = int((time.monotonic() - processing_started) * 1000)
        record.processing_latency_ms = processing_ms
        track_latency("visual_processing_latency_ms", processing_ms)

        store().mark(record, "validating")
        svg = line_processor.svg_document(art.d, art.view_box)
        thumbnail = await asyncio.to_thread(
            line_processor.render_png, art.points, settings.visual_thumbnail_pixels)
        verdict = visual_validator.validate(svg, art, thumbnail)
        if verdict.advisories:
            log.info("%s: %s", record.id, "; ".join(verdict.advisories))
            track("visual_advisory", id=record.id, attempt=attempt,
                  advisories=verdict.advisories)
        if not verdict.ok:
            last_error = "; ".join(verdict.reasons)
            track("visual_validation_failed", id=record.id, attempt=attempt,
                  reasons=verdict.reasons)
            record.metrics = {**art.as_dict(), **verdict.as_dict()}
            store().put(record)
            continue

        record.d = art.d
        record.view_box = art.view_box
        record.metrics = {**art.as_dict(), "validation": verdict.metrics,
                          "source_screening": screening.metrics,
                          "provider": image.as_dict(),
                          "ladder": _rung(attempt)}
        store().write_assets(record, source=image.data, svg=svg,
                             thumbnail=thumbnail)
        store().mark(record, "ready")
        track("visual_ready", id=record.id, attempt=attempt, query=record.query,
              curves=art.curves, retraced=round(art.retraced, 3),
              bridged=art.bridged, fidelity=round(art.fidelity, 3),
              detail=art.detail, stall=round(art.longest_stall, 3))
        return

    store().mark(record, "failed",
                 last_error or "the artwork could not be drawn as one line")
    track("visual_generation_failed", id=record.id, final=True, error=last_error)


# --------------------------------------------------------------------------
# Reading one
# --------------------------------------------------------------------------
def describe(query: str, context: str = "") -> dict:
    """What a client is told about this episode's drawing, without starting one.

    `none` is a real answer and the commonest one: the episode has no drawing
    and nobody has asked for one. A client shows the blank ivory square and
    stops asking.
    """
    if not settings.visuals:
        return {"status": "none", "version": VISUAL_VERSION,
                "background_color": visual_style.PAPER,
                "stroke_color": visual_style.INK,
                "stroke_width": visual_style.STROKE_WIDTH}
    visual_id = key_for(query, context)
    record = store().get(visual_id) if visual_id else None
    if record is None:
        return {"status": "none", "version": VISUAL_VERSION, "id": visual_id,
                "background_color": visual_style.PAPER,
                "stroke_color": visual_style.INK,
                "stroke_width": visual_style.STROKE_WIDTH}
    return record.public()


def svg(visual_id: str) -> str:
    record = store().get(visual_id)
    if record is None or record.status != "ready" or not record.d:
        return ""
    return line_processor.svg_document(record.d, record.view_box,
                                       record.stroke, record.stroke_width,
                                       record.background)


def thumbnail(visual_id: str) -> bytes:
    """The rendered square, from the vector.

    Re-rendered from the stored path when the file is missing rather than
    reported absent: the path is the canonical asset and the PNG is a
    convenience, so a wiped asset directory costs a few milliseconds and not a
    picture.
    """
    data = store().thumbnail(visual_id)
    if data:
        return data
    record = store().get(visual_id)
    if record is None or record.status != "ready" or not record.d:
        return b""
    art = line_processor.LineArt(d=record.d, view_box=record.view_box)
    points = line_processor.flatten(_parse_beziers(record.d))
    if not points:
        return b""
    rendered = line_processor.render_png(points, settings.visual_thumbnail_pixels)
    try:
        (store().folder(visual_id) / "thumbnail.png").write_bytes(rendered)
    except OSError:
        pass
    del art
    return rendered


def _parse_beziers(d: str) -> list:
    """The stored `d` back into curve segments.

    Only the two commands this project emits - `M` and `C` - are understood,
    and anything else raises the path to nothing rather than being guessed at.
    The parser exists for one job: re-rendering a thumbnail from a record whose
    file has gone.
    """
    import re

    tokens = re.findall(r"[MC]|-?\d+(?:\.\d+)?", d or "")
    at = 0
    start = None
    out = []
    while at < len(tokens):
        token = tokens[at]
        if token == "M":
            start = (float(tokens[at + 1]), float(tokens[at + 2]))
            at += 3
        elif token == "C":
            if start is None:
                return []
            values = [float(v) for v in tokens[at + 1:at + 7]]
            if len(values) < 6:
                return []
            c1 = (values[0], values[1])
            c2 = (values[2], values[3])
            end = (values[4], values[5])
            out.append((start, c1, c2, end))
            start = end
            at += 7
        else:
            at += 1
    return out


async def regenerate(query: str, context: str = "", *, surface: str = "search",
                     reason: str = "manual regeneration") -> dict:
    """Draw it again, keeping the current picture until the new one works.

    The record is *not* cleared first, which is the whole point: a regeneration
    that fails must leave the episode illustrated exactly as it was. The new
    attempt writes over the old one only on the `ready` branch of `_draw`.
    """
    visual_id = key_for(query, context)
    if not visual_id:
        return {"ok": False, "error": "that episode cannot have an illustration"}
    existing = store().get(visual_id)
    scratch = VisualRecord(
        id=visual_id, query=query, context=context, surface=surface,
        reason=reason, created=time.time(), attempts=0, claimed=0.0,
        status="queued",
        cost_usd=existing.cost_usd if existing is not None else 0.0)
    _INFLIGHT.discard(visual_id)
    # Deliberately not written to the store first. The redo walks the same row
    # through generating/processing/validating, and only the `ready` branch
    # writes a path or an asset - so if it fails, the good drawing is put back
    # exactly as it was, files included. An administrator asking for a better
    # picture must never be able to end up with no picture.
    await _run(scratch, force=True)
    record = store().get(visual_id)
    if (record is None or record.status != "ready") and existing is not None \
            and existing.status == "ready":
        store().put(existing)
        log.info("regeneration of %s failed; the previous drawing stands",
                 visual_id)
        return {"ok": False, "error": record.error if record else "",
                "visual": existing.public()}
    return {"ok": bool(record and record.status == "ready"),
            "visual": record.public() if record else {}}


def warm(candidates, *, surface: str = "warm") -> list:
    """Draw ahead of the tap, for the browse surfaces.

    The whole reason myFAM and DailyFAM pay nothing for this feature: what
    somebody might tap is known before they tap it, so the picture is finished
    and sitting on the tile. Bounded per call, and it stands aside while a
    listener is waiting on a live episode.

    Each candidate is `(query, context, reason)`. The reason survives to the
    record and to the report, because the only way to judge what is worth
    drawing ahead is to see which kinds of guess got looked at.
    """
    started = []
    for query, context, reason in candidates[:settings.visual_warm_per_cycle]:
        visual_id = request(query, context=context, surface=surface,
                            reason=reason, live=False)
        if visual_id:
            started.append(visual_id)
    return started


def report() -> dict:
    """What `/api/health` says about the drawings.

    Says which state a deploy is in rather than only whether the feature
    exists: an unconfigured provider and a working one look identical from
    outside until somebody opens a player and stares at a blank square.
    """
    counts = store().counts() if settings.visuals else {}
    spent = store().spent_since(time.time() - 86400) if settings.visuals else 0.0
    return {
        "enabled": settings.visuals,
        "version": VISUAL_VERSION,
        "surfaces": list(ELIGIBLE_SURFACES),
        "excluded": list(EXCLUDED_SURFACES),
        "provider": visual_provider.report(),
        "director": visual_director.report(),
        "style": visual_style.report(),
        "validator": visual_validator.report(),
        "counts": counts,
        "events": dict(_COUNTERS),
        "latency": latencies(),
        "spend_24h_usd": round(spent, 4),
        "daily_budget_usd": settings.visual_daily_budget_usd,
        "understanding_wait_seconds": settings.visual_understanding_wait_seconds,
        "note": "" if settings.visuals else
                "VISUALS=0: no episode has an illustration and the player "
                "shows the ivory square throughout",
    }
