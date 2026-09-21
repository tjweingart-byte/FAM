"""Where the voice is, as reported by the voice - not as typed into a dashboard.

`REMOTE_VOICE_URL` is a fact about the world written down in a second place. It
is correct until RunPod moves the pod, and then it is wrong in the one way that
cannot be seen from the app: the address still resolves, something still
answers, and what comes back is a 404 that reads exactly like a missing route
(PROBLEMS.md §78, §112).

So the worker says where it is. On boot, and every
`VOICE_REGISTER_INTERVAL` seconds after that, `voice_worker/register.py` posts
its own address, mode, port, contract version and sample rate to this app. A
pod that is replaced registers its new address within one heartbeat, and
nobody edits anything.

## Three rules, each of which is the reason for a column

**A registration expires.** `last_seen` is the whole mechanism: a worker that
has stopped heartbeating stops being a candidate after `VOICE_REGISTRY_TTL`,
without anything having to notice that it died. A registry that remembered
forever would hand the app a dead address with more confidence than the
environment variable it replaced.

**A registration is authenticated or it does not happen.** `VOICE_REGISTRY_TOKEN`
unset means registration is refused, not open - an endpoint that accepts "the
voice is at this URL" from anybody is an endpoint that redirects every script
FAM writes to somebody else's server, and it would look like the feature
working. The token is checked in `app.py`, before anything reaches here.

**A registration is a claim, never a promotion.** Nothing here decides what
speaks. `voice_control.py` verifies a registered worker with a real call before
a listener is sent to it, exactly as it does with a configured one - a worker
that registers and then fails its health check is a candidate that loses, not
an outage.

It is durable because a redeploy of the app must not cost the address of a pod
that is up and working; the pod would re-register within a minute, but a minute
is several episodes. And it is per-instance: one Render disk is not shared
between replicas, so on a multi-replica deployment each learns the address from
whichever heartbeat the load balancer handed it, within one interval.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from paths import data_path

log = logging.getLogger(__name__)

#: A registration older than this is not offered as a candidate. Longer than a
#: heartbeat by enough that one dropped POST is not an outage, short enough
#: that a dead pod leaves the list before anybody is sent to it.
DEFAULT_TTL = 300.0

#: Registrations kept at all. There is one voice worker in the usual case and
#: a handful during a migration; a registry that grows without bound is a
#: table full of addresses that stopped existing months ago.
MAX_ROWS = 20


class RegistryError(ValueError):
    """A registration that cannot be accepted, phrased for the worker's log."""


@dataclass(frozen=True)
class Registration:
    """One worker's own account of itself.

    Every field is something only the worker knows. `image` and `commit` are
    the §77 rule applied across the split: "the fix is pushed" and "the fix is
    running on the card" are the same sentence from the app's side, and the
    difference is a day.
    """

    url: str
    mode: str = "http"
    port: int = 0
    contract: int = 0
    sample_rate: int = 0
    engine: str = ""
    image: str = ""
    commit: str = ""
    detail: str = ""
    ready: bool = True
    first_seen: float = 0.0
    last_seen: float = 0.0

    @property
    def age(self) -> float:
        return max(0.0, time.time() - self.last_seen)

    def as_dict(self) -> dict:
        return {
            "url": self.url,
            "mode": self.mode,
            "port": self.port or None,
            "contract": self.contract or None,
            "sample_rate": self.sample_rate or None,
            "engine": self.engine or None,
            "image": self.image or None,
            "commit": self.commit or None,
            "detail": self.detail or None,
            "ready": self.ready,
            "age_seconds": round(self.age, 1),
        }


def _plain_http_allowed() -> bool:
    """Whether a worker may register a plain-HTTP address.

    Asked of `voice_control` rather than of the environment, so the registry
    and the ladder cannot disagree: an address one of them accepts and the
    other refuses is a worker that registers successfully and is never used.
    """
    try:
        import voice_control

        return voice_control.allow_plain_http()
    except Exception:  # pragma: no cover - the ladder is optional here
        return False


def clean_url(raw: str) -> str:
    """The base URL of a worker, or a sentence saying why it is not one.

    Refuses everything that is not an origin: no path, no query, and https
    unless it is a loopback address, because a plain-HTTP worker on the open
    internet carries the bearer token and every sentence of the script in
    clear. `/synth` on the end is trimmed rather than refused - the worker is
    naming itself, and an operator reading the log will paste the URL they
    tested with.
    """
    url = (raw or "").strip().rstrip("/")
    if not url:
        raise RegistryError("a registration needs a url")
    if url.endswith("/synth"):
        url = url[: -len("/synth")]
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise RegistryError(f"{raw!r} is not an http(s) origin")
    local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
    if parsed.scheme != "https" and not (local or _plain_http_allowed()):
        raise RegistryError(
            f"{url} is not https; a worker on the open internet carries the "
            "bearer token and the whole script in clear. A pod's direct TCP "
            "port has no certificate and is the one address that does not go "
            "through a proxy edge, so VOICE_ALLOW_PLAIN_HTTP=1 permits it "
            "deliberately - see REMOTE_VOICE.md and PROBLEMS.md §117")
    if parsed.path not in ("", "/") or parsed.query:
        raise RegistryError(f"{url} has a path; register the worker's origin")
    return f"{parsed.scheme}://{parsed.netloc}"


class VoiceRegistry:
    """Self-registered workers, newest heartbeat first."""

    def __init__(self, path: str | None = None) -> None:
        self.path = data_path("VOICE_REGISTRY_DB", "voice_registry.db", path)
        self._local = threading.local()
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS workers (
                       url         TEXT PRIMARY KEY,
                       mode        TEXT NOT NULL DEFAULT 'http',
                       port        INTEGER NOT NULL DEFAULT 0,
                       contract    INTEGER NOT NULL DEFAULT 0,
                       sample_rate INTEGER NOT NULL DEFAULT 0,
                       engine      TEXT NOT NULL DEFAULT '',
                       image       TEXT NOT NULL DEFAULT '',
                       commit_sha  TEXT NOT NULL DEFAULT '',
                       detail      TEXT NOT NULL DEFAULT '',
                       ready       INTEGER NOT NULL DEFAULT 1,
                       first_seen  REAL NOT NULL,
                       last_seen   REAL NOT NULL
                   )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS workers_seen"
                         " ON workers(last_seen)")

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    # -- writing -----------------------------------------------------------

    def register(self, payload: dict, at: float = 0.0) -> Registration:
        """Record one heartbeat. Raises `RegistryError` on anything unusable.

        An upsert rather than an insert, keyed on the URL: a worker heartbeats
        every minute for as long as it lives, and each one is the same worker
        saying it is still there. `first_seen` is kept from the original row so
        "this pod has been up for three hours" survives the heartbeats.
        """
        if not isinstance(payload, dict):
            raise RegistryError(f"expected an object, got {type(payload).__name__}")
        url = clean_url(str(payload.get("url") or ""))
        now = at or time.time()
        row = Registration(
            url=url,
            mode=str(payload.get("mode") or "http")[:32],
            port=_as_int(payload.get("port")),
            contract=_as_int(payload.get("contract")),
            sample_rate=_as_int(payload.get("sample_rate")),
            engine=str(payload.get("engine") or "")[:64],
            image=str(payload.get("image") or "")[:200],
            commit=str(payload.get("commit") or "")[:64],
            detail=str(payload.get("detail") or "")[:300],
            ready=bool(payload.get("ready", True)),
            first_seen=now,
            last_seen=now,
        )
        conn = self._conn()
        conn.execute(
            """INSERT INTO workers (url, mode, port, contract, sample_rate,
                                    engine, image, commit_sha, detail, ready,
                                    first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(url) DO UPDATE SET
                   mode = excluded.mode,
                   port = excluded.port,
                   contract = excluded.contract,
                   sample_rate = excluded.sample_rate,
                   engine = excluded.engine,
                   image = excluded.image,
                   commit_sha = excluded.commit_sha,
                   detail = excluded.detail,
                   ready = excluded.ready,
                   last_seen = excluded.last_seen""",
            (row.url, row.mode, row.port, row.contract, row.sample_rate,
             row.engine, row.image, row.commit, row.detail, int(row.ready),
             row.first_seen, row.last_seen))
        self._trim()
        return self.get(url) or row

    def forget(self, url: str) -> None:
        """Drop one worker. A pod that is being torn down says so on its way out."""
        try:
            self._conn().execute("DELETE FROM workers WHERE url = ?",
                                 (clean_url(url),))
        except RegistryError:
            return
        except Exception:  # pragma: no cover - a tidy-up that cannot cost audio
            log.exception("could not forget a voice worker")

    def _trim(self) -> None:
        self._conn().execute(
            "DELETE FROM workers WHERE url NOT IN ("
            " SELECT url FROM workers ORDER BY last_seen DESC LIMIT ?)",
            (MAX_ROWS,))

    # -- reading -----------------------------------------------------------

    def get(self, url: str) -> Optional[Registration]:
        row = self._conn().execute(
            f"SELECT {_COLUMNS} FROM workers WHERE url = ?", (url,)).fetchone()
        return _row(row) if row else None

    def live(self, ttl: float = DEFAULT_TTL, at: float = 0.0) -> list[Registration]:
        """Workers that have heartbeated inside the window, newest first.

        Not "workers that exist" - workers that said so recently. The TTL is
        the only thing standing between a pod that was destroyed and an app
        that keeps sending it episodes.
        """
        now = at or time.time()
        try:
            rows = self._conn().execute(
                f"SELECT {_COLUMNS} FROM workers WHERE last_seen >= ?"
                " ORDER BY last_seen DESC", (now - max(1.0, ttl),)).fetchall()
        except Exception:  # pragma: no cover - a registry that cannot be read
            log.exception("could not read the voice registry")
            return []
        return [_row(r) for r in rows]

    def all(self) -> list[Registration]:
        """Everything, expired included. For the doctor, which has to be able
        to say "it registered forty minutes ago and then stopped"."""
        rows = self._conn().execute(
            f"SELECT {_COLUMNS} FROM workers ORDER BY last_seen DESC").fetchall()
        return [_row(r) for r in rows]


_COLUMNS = ("url, mode, port, contract, sample_rate, engine, image, "
            "commit_sha, detail, ready, first_seen, last_seen")


def _row(row) -> Registration:
    return Registration(
        url=row[0], mode=row[1], port=row[2], contract=row[3],
        sample_rate=row[4], engine=row[5], image=row[6], commit=row[7],
        detail=row[8], ready=bool(row[9]), first_seen=row[10], last_seen=row[11])


def _as_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


_REGISTRY: VoiceRegistry | None = None
_LOCK = threading.Lock()


def opened() -> VoiceRegistry | None:
    """The registry if this process has one, without opening it.

    `/api/health` reports the stores it holds, and a store listed as missing on
    every machine that never switched registration on is a health page
    teaching people to ignore it.
    """
    return _REGISTRY


def registry() -> VoiceRegistry:
    """The process's registry, opened on first use.

    Lazy because most deployments never register anything: the in-process card
    and a configured URL both leave this file uncreated, and a store that is
    opened at import is a file created on every machine that imports the app.
    """
    global _REGISTRY
    with _LOCK:
        if _REGISTRY is None:
            _REGISTRY = VoiceRegistry()
        return _REGISTRY
