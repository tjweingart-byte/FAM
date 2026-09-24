"""The trending bank: Trending as an edition, built twice a day for everybody.

What it is (§139, at the owner's direction)
-------------------------------------------
Trending used to be read straight off the live story pool, which a GDELT
sweep refilled every fifteen minutes. From Render that sweep timed out on
every run - about thirty-two requests to a free service that asks for one
every five seconds, inside a forty-five second ceiling - so the rail said
"The live sources didn't answer in time" to everybody who opened myFAM.

So Trending is an **edition** now:

* **Built on a clock, not on a page load.** At 05:00 and 17:00 on the East
  Coast (`TRENDING_BANK_TIMEZONE`, `TRENDING_BANK_HOURS`), a named zone so the
  hour survives daylight saving.
* **From GNews and nothing else** (`gnews.py`), a keyed feed built for
  exactly this question. **There is no fallback source**, at the owner's
  direction: if GNews cannot answer, the plan is changed, not the source.
  A build that fails leaves the last edition on the row (for up to
  `TRENDING_BANK_MAX_AGE_HOURS`), is retried after
  `TRENDING_BANK_RETRY_SECONDS`, and says so on `/api/health`. And the live
  story pool never reaches this row - that inventory is Made for you's.
* **Ten stories** (`TRENDING_BANK_SIZE`), chosen by how widely the press is
  running them, and **ten episodes written ahead of the tap**, into the shared
  script cache under the exact key a myFAM tap computes (`pipeline.key_for`).
  A tap on a Trending tile is an ordinary cache hit, and once it has been
  played in a production voice its audio is kept too (§132), so the second
  listener costs nothing at all.
* **Shared by every listener.** One build, one set of episodes. What differs
  per listener is only what the rail already did: their country moves stories
  up, and a story they have heard comes back as a follow-up or not at all.

Where it departs from a rule, stated rather than buried
-------------------------------------------------------
**A bank episode keeps until the next edition**, however fresh its evidence
had to be. `cache.ttl_for` would give a news episode fifteen minutes
(`CACHE_TTL_VOLATILE`), so ten episodes written at 5am would be gone before
anybody woke up. The owner asked for a bank that regenerates every twelve
hours, and an edition is a snapshot with a time on it - the same bargain a
morning paper makes. Two parts of §89 still hold: a script about a game in
progress is never kept (`live_status == "in_progress"`), and a question whose
answer is a *result* is never written ahead (`outcome_dependent`) - its tile
is still offered and the tap writes it, exactly as prefetch behaves.

The rest is the house style
---------------------------
* **Nothing on a request path builds anything.** The rail reads the last
  edition from SQLite (`topics`), memoised; a build is only ever the
  background loop's (`run_forever`) or the operator's (`tools/trending_bank.py`).
* **One builder per slot**, across workers and restarts: a slot is *claimed*
  in the database before anything is spent, and a claim that went quiet for
  twenty minutes is a crashed builder and may be taken over.
* **A new deployment does not wait for 5pm.** On boot, a slot with no edition
  is built at once, and a slot that failed for want of a key is retried as
  soon as there is one - which is what adding `GNEWS_KEY` on Render and
  redeploying amounts to.
* **An empty rail says which of its nothings it is** (`empty_reason`) - no
  key, a failed build, the first edition still being written - and never that
  nothing is happening in the world.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Optional

from config import settings
from paths import data_path

log = logging.getLogger(__name__)

GNEWS = "GNews"

#: Build states in the database.
BUILDING = "building"
READY = "ready"
FAILED = "failed"

#: A claim this old with nothing finished is a crashed builder.
STALE_CLAIM_SECONDS = 20 * 60
#: How long a bank episode outlives the slot after its own, so the rail and
#: the cache hand over without a gap while the next edition is written.
EPISODE_GRACE_SECONDS = 3600
#: How often a running server re-reads the edition, so a build finished by
#: another worker (or by `tools/trending_bank.py`) reaches every rail.
RELOAD_SECONDS = 60.0
#: At most this many stories from one GNews category in an edition, so a
#: busy day in one section cannot be the whole row. A cap on what is
#: available, never a quota: an edition short of variety is topped back up.
MAX_PER_SECTION = 3


# --------------------------------------------------------------------------
# The clock
# --------------------------------------------------------------------------
def zone():
    """The zone editions are scheduled in. UTC, loudly, if it cannot be read."""
    name = settings.trending_bank_timezone or "America/New_York"
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception as exc:  # noqa: BLE001 - a clock must never stop a boot
        log.error("trending bank: time zone %r is unavailable (%s); scheduling "
                  "in UTC instead. Install tzdata.", name, exc)
        return timezone.utc


def hours() -> list:
    """The build hours, sorted, deduplicated, and each a real hour."""
    out = set()
    for part in (settings.trending_bank_hours or "").split(","):
        try:
            hour = int(part.strip())
        except ValueError:
            continue
        if 0 <= hour <= 23:
            out.add(hour)
    return sorted(out) or [5, 17]


def _slots_around(now: float) -> list:
    tz = zone()
    local = datetime.fromtimestamp(now, tz)
    out = []
    for offset in (-1, 0, 1):
        day = (local + timedelta(days=offset)).date()
        for hour in hours():
            out.append(datetime(day.year, day.month, day.day, hour, tzinfo=tz))
    return sorted(out, key=lambda d: d.timestamp())


def last_slot(now: Optional[float] = None) -> datetime:
    """The most recent build time at or before `now`."""
    now = time.time() if now is None else now
    return [s for s in _slots_around(now) if s.timestamp() <= now][-1]


def next_slot(now: Optional[float] = None) -> datetime:
    """The next build time after `now`."""
    now = time.time() if now is None else now
    return [s for s in _slots_around(now) if s.timestamp() > now][0]


def slot_id(slot: datetime) -> str:
    return slot.isoformat(timespec="minutes")


# --------------------------------------------------------------------------
# The edition
# --------------------------------------------------------------------------
@dataclass
class Edition:
    """One build: the stories, the episodes, and how it was made."""

    slot: str
    built_at: float
    source: str
    stories: list = field(default_factory=list)
    #: story id -> {"status", "key", "dollars", "detail"}
    episodes: dict = field(default_factory=dict)
    detail: str = ""
    requests: int = 0
    minutes: int = 0

    def expired(self, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        return now - self.built_at > settings.trending_bank_max_age_hours * 3600

    def written(self) -> int:
        return sum(1 for e in self.episodes.values()
                   if e.get("status") in ("written", "cached"))

    def dollars(self) -> float:
        return round(sum(float(e.get("dollars") or 0.0)
                         for e in self.episodes.values()), 4)

    def to_json(self) -> str:
        return json.dumps({
            "slot": self.slot, "built_at": self.built_at, "source": self.source,
            "stories": [asdict(s) for s in self.stories],
            "episodes": self.episodes, "detail": self.detail, "requests": self.requests,
            "minutes": self.minutes,
        })

    @classmethod
    def from_json(cls, text: str) -> "Edition":
        import stories

        data = json.loads(text)
        return cls(
            slot=data["slot"], built_at=float(data["built_at"]),
            source=data.get("source", ""),
            stories=[_story(row, stories.Story) for row in data.get("stories", [])],
            episodes=data.get("episodes", {}) or {},
            detail=data.get("detail", ""),
            requests=int(data.get("requests", 0) or 0),
            minutes=int(data.get("minutes", 0) or 0),
        )

    def as_dict(self) -> dict:
        return {
            "slot": self.slot, "built_at": self.built_at, "source": self.source,
            "detail": self.detail,
            "stories": len(self.stories), "episodes_written": self.written(),
            "episodes": self.episodes, "dollars": self.dollars(),
            "requests": self.requests, "minutes": self.minutes,
            "titles": [s.title for s in self.stories],
        }


def _story(row: dict, cls):
    """A stored story back into a `stories.Story`, tuples and all."""
    known = {f for f in cls.__dataclass_fields__}
    row = {k: v for k, v in row.items() if k in known}
    for name in ("tags", "keywords"):
        if name in row:
            row[name] = tuple(row[name] or ())
    if "countries" in row:
        row["countries"] = tuple(tuple(pair) for pair in row["countries"] or ())
    return cls(**row)


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------
class BankStore:
    """Editions and the GNews request ledger, on the mounted disk."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = data_path("TRENDING_BANK_DB", "trending_bank.db", path)
        self._lock = threading.Lock()
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS editions (
                slot TEXT PRIMARY KEY, status TEXT NOT NULL,
                claimed_at REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                built_at REAL, source TEXT, detail TEXT, payload TEXT)""")
            db.execute("""CREATE TABLE IF NOT EXISTS spend (
                day TEXT NOT NULL, provider TEXT NOT NULL,
                n INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (day, provider))""")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def claim(self, slot: str, now: float, force: bool = False) -> bool:
        """Take the right to build `slot`. True for exactly one caller.

        A ready slot is never re-claimed unless `force`; a building one only
        once it has gone quiet (`STALE_CLAIM_SECONDS`); a failed one only
        after `TRENDING_BANK_RETRY_SECONDS`.
        """
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT status, claimed_at FROM editions "
                                 "WHERE slot = ?", (slot,)).fetchone()
                if row is None:
                    db.execute("INSERT INTO editions (slot, status, claimed_at, "
                               "attempts) VALUES (?, ?, ?, 1)",
                               (slot, BUILDING, now))
                    db.execute("COMMIT")
                    return True
                status, claimed_at = row
                quiet = now - float(claimed_at or 0.0)
                allowed = force or (
                    (status == BUILDING and quiet >= STALE_CLAIM_SECONDS)
                    or (status == FAILED
                        and quiet >= settings.trending_bank_retry_seconds))
                if not allowed:
                    db.execute("ROLLBACK")
                    return False
                db.execute("UPDATE editions SET status = ?, claimed_at = ?, "
                           "attempts = attempts + 1 WHERE slot = ?",
                           (BUILDING, now, slot))
                db.execute("COMMIT")
                return True
            except Exception:
                db.execute("ROLLBACK")
                raise

    def finish(self, edition: Edition) -> None:
        with self._lock, self._connect() as db:
            db.execute("UPDATE editions SET status = ?, built_at = ?, source = ?, "
                       "detail = ?, payload = ? WHERE slot = ?",
                       (READY, edition.built_at, edition.source, edition.detail,
                        edition.to_json(), edition.slot))

    def fail(self, slot: str, detail: str) -> None:
        """Record a failed build. A slot that already has an edition keeps it:
        a forced rebuild that failed (`tools/trending_bank.py --build`) must
        not take down what the rail was showing."""
        with self._lock, self._connect() as db:
            db.execute("UPDATE editions SET status = CASE WHEN payload IS NULL "
                       "THEN ? ELSE ? END, detail = ? WHERE slot = ?",
                       (FAILED, READY, detail[:500], slot))

    def status(self, slot: str) -> Optional[dict]:
        with self._connect() as db:
            row = db.execute("SELECT status, claimed_at, attempts, built_at, "
                             "source, detail FROM editions WHERE slot = ?",
                             (slot,)).fetchone()
        if row is None:
            return None
        keys = ("status", "claimed_at", "attempts", "built_at", "source", "detail")
        return dict(zip(keys, row))

    def latest(self) -> Optional[Edition]:
        with self._connect() as db:
            row = db.execute("SELECT payload FROM editions WHERE status = ? "
                             "ORDER BY built_at DESC LIMIT 1", (READY,)).fetchone()
        if row is None or not row[0]:
            return None
        try:
            return Edition.from_json(row[0])
        except Exception as exc:  # noqa: BLE001 - a bad row is not an outage
            log.error("trending bank: the latest edition could not be read: %s",
                      exc)
            return None

    def latest_built_at(self) -> float:
        with self._connect() as db:
            row = db.execute("SELECT MAX(built_at) FROM editions WHERE status = ?",
                             (READY,)).fetchone()
        return float(row[0] or 0.0) if row else 0.0

    def spend(self, provider: str, limit: int, now: Optional[float] = None) -> bool:
        """Count one request against today's ceiling. False once it is spent."""
        day = _utc_day(now)
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT n FROM spend WHERE day = ? AND provider = ?",
                             (day, provider)).fetchone()
            used = int(row[0]) if row else 0
            if limit and used >= limit:
                db.execute("ROLLBACK")
                return False
            db.execute("INSERT INTO spend (day, provider, n) VALUES (?, ?, 1) "
                       "ON CONFLICT(day, provider) DO UPDATE SET n = n + 1",
                       (day, provider))
            db.execute("COMMIT")
            return True

    def spent(self, provider: str, now: Optional[float] = None) -> int:
        with self._connect() as db:
            row = db.execute("SELECT n FROM spend WHERE day = ? AND provider = ?",
                             (_utc_day(now), provider)).fetchone()
        return int(row[0]) if row else 0

    def clear(self) -> int:
        with self._lock, self._connect() as db:
            n = db.execute("SELECT COUNT(*) FROM editions").fetchone()[0]
            db.execute("DELETE FROM editions")
        return int(n)


def _utc_day(now: Optional[float] = None) -> str:
    return datetime.fromtimestamp(time.time() if now is None else now,
                                  timezone.utc).strftime("%Y-%m-%d")


_STORE: Optional[BankStore] = None
_CURRENT: Optional[Edition] = None
_LOADED_AT = 0.0
_LOADED_BUILT_AT = -1.0
_BUILDING = False
#: What the last build attempt in this process said, for `empty_reason`.
_LAST_ATTEMPT: dict = {}


def store() -> BankStore:
    global _STORE
    if _STORE is None:
        _STORE = BankStore()
    return _STORE


def reset(bank_store: Optional[BankStore] = None) -> None:
    """Forget everything in memory. For tests, and after a wipe."""
    global _STORE, _CURRENT, _LOADED_AT, _LOADED_BUILT_AT, _BUILDING
    _STORE = bank_store
    _CURRENT = None
    _LOADED_AT = 0.0
    _LOADED_BUILT_AT = -1.0
    _BUILDING = False
    _LAST_ATTEMPT.clear()


def _exists() -> bool:
    """Whether a bank database is there to read, without creating one.

    The rail asks on every myFAM draw, and a read must not be what creates a
    database - in a test, a preview build or a laptop that never built an
    edition, that would be a stray file and a lie about there being a bank.
    """
    if _STORE is not None:
        return True
    try:
        return os.path.exists(data_path("TRENDING_BANK_DB", "trending_bank.db"))
    except Exception:  # noqa: BLE001
        return False


def current(now: Optional[float] = None) -> Optional[Edition]:
    """The edition the rail shows, or None. Never builds; cheap to call."""
    global _CURRENT, _LOADED_AT, _LOADED_BUILT_AT
    if not settings.trending_bank:
        return None
    now = time.time() if now is None else now
    if now - _LOADED_AT >= RELOAD_SECONDS:
        if not _exists():
            return None
        try:
            built_at = store().latest_built_at()
            if built_at != _LOADED_BUILT_AT:
                _CURRENT = store().latest()
                _LOADED_BUILT_AT = built_at
        except Exception as exc:  # noqa: BLE001 - a read never breaks a page
            log.warning("trending bank: could not read the edition: %s", exc)
        _LOADED_AT = now
    if _CURRENT is None or _CURRENT.expired(now):
        return None
    return _CURRENT


def stories_now(now: Optional[float] = None) -> list:
    """The current edition's stories, unexpired. `[]` means use the pool."""
    edition = current(now)
    if edition is None:
        return []
    now = time.time() if now is None else now
    return [s for s in edition.stories if not s.expired(now)]


def empty_reason() -> str:
    """What Trending says when there is no edition to show."""
    import gnews

    ok, _why = gnews.available()
    attempt = _LAST_ATTEMPT.get("detail", "")
    if _BUILDING:
        return "Today's trending stories are being put together now."
    if not ok:
        return "FAM isn't connected to a live news source yet."
    if attempt:
        return "Couldn't reach the news sources for this edition. Trying again shortly."
    return "Today's trending stories are being put together now."


# --------------------------------------------------------------------------
# Collecting: GNews first
# --------------------------------------------------------------------------
def _country_of(article) -> str:
    """The publisher country, else the country feed that found it."""
    import stories

    code = (getattr(article, "country", "") or "").lower()
    if not code and str(getattr(article, "feed", "")).startswith("country:"):
        code = article.feed.split(":", 1)[1]
    return stories.ISO_COUNTRIES.get(code, code)


_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-]*")


def search_words(headline: str, limit: int = 4) -> str:
    """The words to search GNews for, to count how widely a story runs.

    Names first - capitalised words that are not the headline's first word -
    because a story's people and places are what every outlet shares, then
    the longest remaining words. In a Title Case headline every word is
    capitalised and says nothing, so there it is longest first throughout.
    Plain words only: GNews reads punctuation as syntax, and a stray quote is
    a 400.
    """
    import news_clusters

    words = _WORD.findall(headline or "")
    stop = news_clusters.STOPWORDS
    seen: set = set()
    kept = []
    for i, word in enumerate(words):
        word = word.strip("'-")
        low = word.lower()
        if len(low) < 3 or low in stop or low in seen or low.isdigit():
            continue
        seen.add(low)
        kept.append((i, word))
    later = [w for i, w in kept if i > 0]
    title_case = len(later) >= 3 and all(w[0].isupper() for w in later)
    names = [] if title_case else [w for i, w in kept if i > 0 and w[0].isupper()]
    rest = sorted((w for _i, w in kept if w not in names), key=len, reverse=True)
    return " ".join((names + rest)[:limit])


@dataclass
class _Candidate:
    group: object
    feeds: set
    best_rank: int
    sections: set
    coverage: int = 0
    countries: list = field(default_factory=list)

    def score(self) -> float:
        """Popularity: how widely it runs, how many feeds lead with it, how high.

        Log-scaled coverage so one story every outlet is running does not
        flatten the rest; feeds and position break the ties a count cannot.
        """
        return (math.log1p(max(self.coverage, self.group.outlets))
                + 0.5 * len(self.feeds)
                + 0.5 / (1 + self.best_rank))


async def gnews_signals(client, now: float, size: int) -> list:
    """What the press is leading with, as signals. Raises if GNews is down."""
    import gnews
    import news_clusters
    import stories

    articles: list = []
    asked = failed = 0
    last_error = ""
    feeds = ([("category", c) for c in gnews.categories()]
             + [("country", c) for c in gnews.countries()])
    for kind, value in feeds:
        asked += 1
        try:
            if kind == "category":
                articles += await client.top_headlines(category=value)
            else:
                articles += await client.top_headlines(country=value)
        except gnews.QuotaSpent as exc:
            last_error = str(exc)
            failed += 1
            break
        except gnews.GNewsError as exc:
            failed += 1
            last_error = str(exc)
            log.warning("trending bank: %s %s failed: %s", kind, value, exc)
    if asked and failed == asked:
        raise gnews.GNewsError(last_error or "every GNews request failed")
    if not articles:
        return []

    by_url = {a.url: a for a in articles}
    groups = news_clusters.cluster(articles, scope_of=lambda a: a.feed,
                                   min_outlets=1)
    candidates = []
    for group in groups:
        members = [by_url[u] for u in group.urls if u in by_url]
        feeds_in = {a.feed for a in members}
        candidates.append(_Candidate(
            group=group, feeds=feeds_in,
            best_rank=min((a.rank for a in members), default=99),
            sections={f.split(":", 1)[1] for f in feeds_in
                      if f.startswith("category:")} or {"general"},
            countries=[_country_of(a) for a in members if _country_of(a)],
        ))
    candidates.sort(key=lambda c: (-c.score(), c.group.headline()))

    # How widely the leaders are really running: one search each, counted.
    since = datetime.fromtimestamp(now - 12 * 3600, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    for candidate in candidates[:max(0, settings.gnews_corroborate)]:
        words = search_words(candidate.group.headline())
        if not words:
            continue
        try:
            found = await client.search(words, since_iso=since,
                                        limit=settings.gnews_max_articles)
        except gnews.QuotaSpent:
            break
        except gnews.GNewsError as exc:
            log.info("trending bank: could not count coverage for %r: %s",
                     words, exc)
            continue
        candidate.coverage = found.total
        candidate.countries += [_country_of(a) for a in found.articles
                                if _country_of(a)]
    candidates.sort(key=lambda c: (-c.score(), c.group.headline()))

    chosen = _varied(candidates, size)
    peak = max((c.score() for c in chosen), default=1.0) or 1.0
    at = datetime.fromtimestamp(now, timezone.utc)
    out = []
    for candidate in chosen:
        group = candidate.group
        headline = group.headline()
        titles = [headline] + [t for t in group.titles if t != headline]
        covered = max(candidate.coverage, group.outlets)
        said = (f"{covered} articles in the last 12 hours"
                + (f" across {len(candidate.feeds)} of the press's top-story "
                   "feeds" if len(candidate.feeds) > 1 else "")
                + ". What they are saying: "
                + "; ".join(t[:120] for t in titles[:4]))
        subject = headline[:140]
        out.append(stories.Signal(
            subject=subject, observation=said, domain=stories.ATTENTION,
            source=GNEWS, tags=_tags(" ".join(titles[:3])),
            strength=round(max(0.3, candidate.score() / peak), 3), as_of=at,
            url=group.urls[0] if group.urls else "",
            countries=stories.country_shares(candidate.countries),
            keywords=group.keywords(), coverage=covered,
            suggested_query=f"{subject}: what is happening and why it matters",
            suggested_angle=f"Leading the news, in {covered} articles today",
        ))
    return out


def _varied(candidates: list, size: int) -> list:
    """The top `size`, at most `MAX_PER_SECTION` from one section, topped up."""
    chosen, per = [], {}
    for c in candidates:
        section = sorted(c.sections)[0]
        if per.get(section, 0) >= MAX_PER_SECTION:
            continue
        per[section] = per.get(section, 0) + 1
        chosen.append(c)
        if len(chosen) >= size:
            return chosen
    for c in candidates:
        if len(chosen) >= size:
            break
        if c not in chosen:
            chosen.append(c)
    return chosen


def _tags(text: str) -> tuple:
    try:
        import story_sources

        return story_sources._tags(text)
    except Exception:  # noqa: BLE001 - tags are ranking sugar, never required
        return ()


async def collect(now: float, size: int, client=None) -> tuple:
    """Signals for an edition: `(signals, detail, requests)`. Never raises.

    GNews and nothing else. `signals` is empty when GNews is not configured,
    failed, or found nothing, and `detail` says which.
    """
    import gnews

    ok, why = gnews.available()
    if not ok:
        return [], why, 0
    client = client or gnews.Client(
        spend=lambda _endpoint: store().spend(
            GNEWS, settings.gnews_daily_requests, now))
    try:
        signals = await gnews_signals(client, now, size)
    except Exception as exc:  # noqa: BLE001 - a failed build is reported, not raised
        log.warning("trending bank: GNews failed: %s", exc)
        return [], f"GNews failed: {exc}", getattr(client, "requests", 0)
    if not signals:
        return [], "GNews answered with no stories", client.requests
    return signals, f"GNews: {len(signals)} stories", client.requests


# --------------------------------------------------------------------------
# Writing the episodes
# --------------------------------------------------------------------------
async def write_episode(story, generator, cache, minutes: int,
                        expires_at: float) -> dict:
    """Write one story's episode into the shared cache. Never raises."""
    import prefetch
    import research
    from pipeline import bucket_for, key_for
    from script_generator import ScriptNotes, plan_episode

    try:
        plan = plan_episode(story.query, minutes)
        key = await key_for(plan, getattr(generator, "client", None))
        if not key:
            return {"status": "failed", "key": "", "detail": "no cache key"}
        if cache.get(key):
            return {"status": "cached", "key": key}
        notes = ScriptNotes()
        plan = await generator.understand(plan, notes)
        if getattr(plan.brief, "outcome_dependent", False):
            return {"status": "volatile", "key": key,
                    "dollars": prefetch._dollars(notes),
                    "detail": "the answer is a result; the tap writes it"}
        sentences = [s async for s in generator.stream_sentences(plan, notes)]
        dollars = prefetch._dollars(notes)
        if not sentences:
            return {"status": "failed", "key": key, "dollars": dollars,
                    "detail": "the writer returned nothing"}
        if (notes.live_status or "") == "in_progress":
            return {"status": "volatile", "key": key, "dollars": dollars,
                    "detail": "under way; a score is never kept"}
        ttl = max(60, int(expires_at - time.time()))
        sources = notes.provenance.to_json() if notes.provenance is not None else ""
        extra = {"summary": notes.summary} if notes.summary else {}
        # No author: a bank episode was nobody's tap, so it belongs to
        # everybody - the rule prefetch keeps, for the same reason.
        cache.put(key, sentences, ttl, story.query, notes.thread, minutes,
                  bucket_for(plan), sources, "", notes.title, **extra)
        return {"status": "written", "key": key, "dollars": dollars,
                "title": notes.title}
    except research.NoEvidence as exc:
        return {"status": "no_evidence", "key": "", "detail": str(exc)[:200]}
    except Exception as exc:  # noqa: BLE001 - one episode is not the edition
        log.warning("trending bank: could not write %r: %s", story.query, exc)
        return {"status": "failed", "key": "",
                "detail": f"{type(exc).__name__}: {exc}"[:200]}


# --------------------------------------------------------------------------
# Building
# --------------------------------------------------------------------------
async def build(now: Optional[float] = None, generator=None, cache=None,
                force: bool = False, client=None) -> Optional[Edition]:
    """Build the edition for the current slot, if this caller wins the claim.

    Returns the edition, or None when another builder has it, it is already
    built, or nothing could be collected. Never raises.
    """
    global _BUILDING, _CURRENT, _LOADED_AT
    import stories

    now = time.time() if now is None else now
    if not settings.trending_bank:
        return None
    slot = last_slot(now)
    sid = slot_id(slot)
    try:
        if not store().claim(sid, now, force=force):
            return None
    except Exception as exc:  # noqa: BLE001
        log.error("trending bank: could not claim %s: %s", sid, exc)
        return None

    _BUILDING = True
    try:
        size = max(1, settings.trending_bank_size)
        signals, detail, requests = await collect(now, size, client=client)
        if not signals:
            store().fail(sid, detail or "no stories")
            _LAST_ATTEMPT.update(slot=sid, detail=detail, at=now)
            log.error("trending bank: no edition for %s - %s", sid, detail)
            return None

        composed = await stories.compose(signals[:size], now)
        shelf = settings.trending_bank_max_age_hours * 3600.0
        edition_stories = [_placed(replace(s, first_seen=now, last_seen=now,
                                           shelf_life=shelf))
                           for s in composed]
        edition = Edition(slot=sid, built_at=now, source=GNEWS,
                          stories=edition_stories, detail=detail,
                          requests=requests,
                          minutes=settings.trending_bank_minutes)

        if settings.trending_bank_write and generator is not None and cache is not None:
            expires_at = next_slot(now).timestamp() + EPISODE_GRACE_SECONDS
            for story in edition_stories:
                edition.episodes[story.id] = await write_episode(
                    story, generator, cache, settings.trending_bank_minutes,
                    expires_at)
        else:
            reason = ("TRENDING_BANK_WRITE=0" if not settings.trending_bank_write
                      else "no writer on this server")
            edition.episodes = {s.id: {"status": "not_written", "detail": reason}
                                for s in edition_stories}

        store().finish(edition)
        _CURRENT, _LOADED_AT = edition, now
        _LAST_ATTEMPT.clear()
        log.info("trending bank: edition %s - %d stories, %d episodes "
                 "written, $%.4f, %d GNews request(s)",
                 sid, len(edition_stories), edition.written(), edition.dollars(),
                 requests)
        return edition
    except Exception as exc:  # noqa: BLE001 - a build never takes the server down
        log.exception("trending bank: the build for %s failed", sid)
        try:
            store().fail(sid, f"{type(exc).__name__}: {exc}")
        except Exception:  # noqa: BLE001
            pass
        _LAST_ATTEMPT.update(slot=sid, detail=str(exc), at=now)
        return None
    finally:
        _BUILDING = False


def _placed(story):
    """Where the story is trending, from its publishers' countries."""
    import geography

    scope, key, label = geography.scope_for(story.countries, story.region_hint)
    return replace(story, geo_scope=scope, geo_key=key, geo=label)


def due(now: Optional[float] = None) -> bool:
    """Whether the current slot still wants building.

    Never without a key: there is no other source to build from, and a build
    that can only fail would log a failure every half hour for nothing - the
    row already says "not connected". With a key, a slot is due when it has
    no edition, when a failed build has waited `TRENDING_BANK_RETRY_SECONDS`,
    when a claim has gone quiet (a crashed builder), or at once when the only
    thing that failed it was the missing key - which is what adding
    `GNEWS_KEY` on Render and redeploying has to mean.
    """
    import gnews

    if not settings.trending_bank or not gnews.available()[0]:
        return False
    now = time.time() if now is None else now
    row = store().status(slot_id(last_slot(now)))
    if row is None:
        return True
    if row["status"] == READY:
        return False
    if row["status"] == FAILED:
        return (_failed_for_want_of_a_key(row)
                or now - float(row["claimed_at"] or 0)
                >= settings.trending_bank_retry_seconds)
    return now - float(row["claimed_at"] or 0) >= STALE_CLAIM_SECONDS


def _failed_for_want_of_a_key(row: Optional[dict]) -> bool:
    return bool(row and row.get("status") == FAILED
                and "GNEWS_KEY is not set" in str(row.get("detail") or ""))


async def run_forever(generator=None, cache=None) -> None:
    """Build on the clock, catching up on boot. Runs for the server's life.

    Wakes at least once a minute, so a slot is never missed by more than that
    and a clock change is noticed. Never raises.
    """
    if not settings.trending_bank:
        log.info("trending bank: off (TRENDING_BANK=0)")
        return
    log.info("trending bank: editions at %s %s; next at %s",
             ", ".join(f"{h:02d}:00" for h in hours()),
             settings.trending_bank_timezone, slot_id(next_slot()))
    while True:
        try:
            if due():
                row = store().status(slot_id(last_slot()))
                await build(generator=generator, cache=cache,
                            force=_failed_for_want_of_a_key(row))
        except Exception:  # noqa: BLE001
            log.exception("trending bank: the scheduler tick failed")
        await asyncio.sleep(max(5.0, min(60.0, next_slot().timestamp() - time.time())))


def report(now: Optional[float] = None) -> dict:
    """For `/api/health`: the schedule, the edition, and the source."""
    import gnews

    now = time.time() if now is None else now
    ok, why = gnews.available()
    edition = current(now)
    out = {
        "enabled": bool(settings.trending_bank),
        "schedule": {"timezone": settings.trending_bank_timezone,
                     "hours": hours(), "next": slot_id(next_slot(now)),
                     "current_slot": slot_id(last_slot(now))},
        "size": settings.trending_bank_size,
        "minutes": settings.trending_bank_minutes,
        "writes_episodes": bool(settings.trending_bank_write),
        "gnews": {"ready": ok, "detail": why,
                  "daily_ceiling": settings.gnews_daily_requests,
                  "requests_today": store().spent(GNEWS, now) if _exists() else 0},
        "building": _BUILDING,
        "edition": edition.as_dict() if edition else None,
        "last_failure": dict(_LAST_ATTEMPT) or None,
        # From the database rather than this process, so a failure another
        # worker (or the last boot) hit is still visible here.
        "current_slot_status": (store().status(slot_id(last_slot(now)))
                                if _exists() else None),
    }
    return out
