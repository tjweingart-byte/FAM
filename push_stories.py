"""Big stories and followed finals, told to the phone (§552).

Two of the three notification kinds (`push.py` keeps the daily reminder and
the delivery). Both keep the rule the daily reminder keeps: **ready means
written**. Nothing is sent until the episode a tap plays is in the shared
cache under the key that tap computes (the story's query at
`BROWSE_MINUTES`), so a tap is a cache hit, never a wait. A story whose
episode cannot be written (no writer, the write failed) is never sent.

A massive story (`breaking`)
----------------------------
**Detected from GDELT's export copy, never its search API** (§211): every
`DETECT_SECONDS` the same clustering Trending uses (`story_sources.
GdeltSignals`) runs over the last `WINDOW_HOURS`, reading only files already
on disk - no model, no request. A story is a cluster of headlines and its
size is the number of **distinct outlets** running it.

Who is told is the owner's choice (§552, "mixed"):

* **Somebody who follows a subject the story names** is told automatically
  once `PUSH_BREAKING_FOLLOWED_OUTLETS` outlets run it. "Follows" means what
  they chose, never what they were inferred to like: a subject in one of their
  mixes (`f:nfl~Eagles` is "Eagles"; plain `f:nfl` is "NFL") or one of their
  chosen interests that names a subject (`topics._names_a_subject`), matched
  as a whole phrase against the story's headline and keywords.
* **Everybody** only once the owner approves it in `/admin/push`. The
  detector proposes the one story that is both past
  `PUSH_BREAKING_EVERYONE_OUTLETS` and `PUSH_BREAKING_RATIO` times the
  next-biggest; nothing goes to every phone without a person.

A followed final (`final`)
--------------------------
A game in a followed team's league that the sports sweep saw end
(`balldontlie_scores`, a `stories` story with `live_status == final`): the
people who follow a team in it are told "<score> · Final - the recap is
ready". It rides the sweep as it is (§191): sports are swept only while a
game is on and somebody drew myFAM recently, so a final nobody's sweep saw is
not told. Nothing here asks BALLDONTLIE anything.

Both kinds wait out the listener's quiet hours (`push.in_quiet_hours`) and go
in the morning if still current: a breaking story while the detector saw it
in the last `FRESH_SECONDS`, either kind for `LIVE_SECONDS` after it was first
seen. Each person is told about each story once (`push_log`).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

import db as store_db
from paths import data_path

log = logging.getLogger("fam.push.stories")

BREAKING = "breaking"
FINAL = "final"

#: A breaking story the followed bar caught: told to followers only.
WATCHING = "watching"
#: Past the everyone bar too: waiting for the owner in /admin/push.
PROPOSED = "proposed"
#: The owner said send it to everybody.
APPROVED = "approved"
#: The owner said no. Followers already told stay told; nobody else is.
DISMISSED = "dismissed"
STATUSES = (WATCHING, PROPOSED, APPROVED, DISMISSED)

#: How often the detector reads the export copy: GDELT's own clock (§211).
DETECT_SECONDS = 15 * 60
#: How far back a breaking story's headlines are read. Shorter than
#: Trending's twelve hours: a story that broke this morning, not this week.
WINDOW_HOURS = 3
#: How long after it was first seen a story may still be told.
LIVE_SECONDS = 12 * 3600
#: A breaking story counts as still big while the detector saw it this
#: recently. What decides whether one held by quiet hours still goes at 08:00.
FRESH_SECONDS = 3 * 3600
#: Keyword overlap at which two sweeps' clusters are the same story; the
#: leading headline changes between sweeps, the names in it do not.
SAME_STORY = 0.5
#: Names shorter than this never match (initials, "US").
MIN_NAME = 3

_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def _settings():
    from config import settings
    return settings


def _words(text: str) -> list:
    return _WORD.findall((text or "").lower())


def phrase_in(name: str, text: str) -> bool:
    """Whether every word of `name`, in order and whole, is in `text`."""
    want, have = _words(name), _words(text)
    if not want:
        return False
    n = len(want)
    return any(have[i:i + n] == want for i in range(len(have) - n + 1))


# --------------------------------------------------------------------------
# What somebody follows
# --------------------------------------------------------------------------
def names_of_item(item) -> list:
    """The names a mix item follows. `f:nfl~Eagles` follows "Eagles";
    `f:nfl` follows its subject's own name; a bank topic or a typed question
    follows nothing - a question is not a subject."""
    ident = str(getattr(item, "id", "") or "")
    if not ident.startswith("f:"):
        return []
    from urllib.parse import unquote
    base, _, focus = ident.partition("~")
    if focus:
        return [unquote(part).strip() for part in focus.split("|") if part.strip()]
    title = str(getattr(item, "title", "") or "").strip()
    return [title] if title else []


def names_of_interests(interests) -> list:
    """The chosen interests that name a subject, by their label. A field
    ("sports", "money") is not something a story can name."""
    import topics
    out = []
    try:
        tree = topics.category_tree()
    except Exception:  # noqa: BLE001
        return out
    for tag in interests or ():
        try:
            if not topics._names_a_subject(tag):  # noqa: SLF001
                continue
            node = tree.get(tag)
        except Exception:  # noqa: BLE001
            continue
        out.append(getattr(node, "label", "") or str(tag).replace("-", " "))
    return out


def usable_names(names) -> list:
    """Names worth matching: long enough, and not just a field's own words
    ("football", "markets" - every story in the field carries them)."""
    import topics
    try:
        generic = topics._field_words()  # noqa: SLF001
    except Exception:  # noqa: BLE001
        generic = frozenset()
    out, seen = [], set()
    for name in names or ():
        words = _words(name)
        key = " ".join(words)
        if (not words or key in seen or len(key) < MIN_NAME
                or all(w in generic for w in words)):
            continue
        seen.add(key)
        out.append(name.strip())
    return out


def follows(names, text: str) -> list:
    """Which of these followed names the text names."""
    return [n for n in names if phrase_in(n, text)]


# --------------------------------------------------------------------------
# The stories
# --------------------------------------------------------------------------
class StoryStore:
    """Each story the detector or the sweep raised, beside the mixes."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = data_path("MIXES_DB", "mixes.db", path)
        self._lock = threading.Lock()
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS push_stories (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, subject TEXT NOT NULL,
                query TEXT NOT NULL, keywords TEXT NOT NULL DEFAULT '[]',
                coverage INTEGER NOT NULL DEFAULT 0, peak INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL, first_seen REAL NOT NULL, last_seen REAL NOT NULL,
                decided_at REAL NOT NULL DEFAULT 0, written_at REAL NOT NULL DEFAULT 0,
                title TEXT NOT NULL DEFAULT '', line TEXT NOT NULL DEFAULT '',
                detail TEXT NOT NULL DEFAULT '')""")

    @contextmanager
    def _connect(self):
        db = store_db.connect(self.path, timeout=5.0)
        try:
            with db:
                yield db
        finally:
            db.close()

    _COLUMNS = ("id, kind, subject, query, keywords, coverage, peak, status, first_seen, "
                "last_seen, decided_at, written_at, title, line, detail")

    def _row(self, r) -> dict:
        names = ("id", "kind", "subject", "query", "keywords", "coverage", "peak", "status",
                 "first_seen", "last_seen", "decided_at", "written_at", "title", "line",
                 "detail")
        row = dict(zip(names, r))
        try:
            row["keywords"] = list(json.loads(row["keywords"] or "[]"))
        except ValueError:
            row["keywords"] = []
        return row

    def get(self, story_id: str) -> Optional[dict]:
        with self._connect() as db:
            r = db.execute(f"SELECT {self._COLUMNS} FROM push_stories WHERE id = ?",
                           (story_id,)).fetchone()
        return self._row(r) if r else None

    def recent(self, since: float, kind: str = "") -> list:
        with self._connect() as db:
            if kind:
                rows = db.execute(f"SELECT {self._COLUMNS} FROM push_stories WHERE "
                                  "first_seen >= ? AND kind = ? ORDER BY first_seen DESC",
                                  (since, kind)).fetchall()
            else:
                rows = db.execute(f"SELECT {self._COLUMNS} FROM push_stories WHERE "
                                  "first_seen >= ? ORDER BY first_seen DESC",
                                  (since,)).fetchall()
        return [self._row(r) for r in rows]

    def note_breaking(self, subject: str, query: str, keywords, coverage: int,
                      everyone: bool, now: float) -> dict:
        """A cluster past the followed bar: the same story as one already
        held when its names overlap, else a new one. Raised to PROPOSED when
        it passes the everyone bar; never approved here."""
        keywords = sorted({k.lower() for k in keywords or () if k})
        mine = set(keywords)
        match = None
        for row in self.recent(now - LIVE_SECONDS, BREAKING):
            theirs = set(row["keywords"])
            union = mine | theirs
            if union and len(mine & theirs) / len(union) >= SAME_STORY:
                match = row
                break
            if row["subject"] == subject:
                match = row
                break
        with self._lock, self._connect() as db:
            if match is None:
                ident = "b-" + hashlib.sha256(
                    f"{subject}|{int(now)}".encode("utf-8")).hexdigest()[:16]
                db.execute(f"INSERT INTO push_stories ({self._COLUMNS}) VALUES "
                           "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, '', '', '')",
                           (ident, BREAKING, subject[:300], query[:400],
                            json.dumps(keywords), int(coverage), int(coverage),
                            PROPOSED if everyone else WATCHING, now, now))
            else:
                ident = match["id"]
                status = match["status"]
                if everyone and status == WATCHING:
                    status = PROPOSED
                db.execute("UPDATE push_stories SET coverage = ?, peak = ?, last_seen = ?, "
                           "status = ? WHERE id = ?",
                           (int(coverage), max(int(coverage), int(match["peak"])), now,
                            status, ident))
        return self.get(ident) or {}

    def note_final(self, story_id: str, subject: str, query: str, line: str,
                   now: float) -> bool:
        """A final the sweep saw. True the first time."""
        with self._lock, self._connect() as db:
            cur = db.execute(f"INSERT OR IGNORE INTO push_stories ({self._COLUMNS}) VALUES "
                             "(?, ?, ?, ?, '[]', 0, 0, ?, ?, ?, 0, 0, '', ?, '')",
                             ("f-" + story_id[:60], FINAL, subject[:300], query[:400],
                              WATCHING, now, now, line[:200]))
            return cur.rowcount == 1

    def decide(self, story_id: str, status: str, now: float) -> Optional[dict]:
        if status not in (APPROVED, DISMISSED):
            raise ValueError("A story is approved or dismissed.")
        with self._lock, self._connect() as db:
            db.execute("UPDATE push_stories SET status = ?, decided_at = ? WHERE id = ? "
                       "AND kind = ?", (status, now, story_id, BREAKING))
        return self.get(story_id)

    def written(self, story_id: str, title: str, now: float) -> None:
        with self._lock, self._connect() as db:
            db.execute("UPDATE push_stories SET written_at = ?, title = ?, detail = '' "
                       "WHERE id = ?", (now, title[:300], story_id))

    def failed(self, story_id: str, detail: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("UPDATE push_stories SET detail = ? WHERE id = ?",
                       (detail[:500], story_id))


_STORE: Optional[StoryStore] = None


def store() -> StoryStore:
    global _STORE
    if _STORE is None:
        _STORE = StoryStore()
    return _STORE


def reset(story_store: Optional[StoryStore] = None) -> None:
    global _STORE, _LAST_DETECT
    _STORE = story_store
    _LAST_DETECT = 0.0


# --------------------------------------------------------------------------
# Finding them
# --------------------------------------------------------------------------
_LAST_DETECT = 0.0


def _source():
    import story_sources

    class Breaking(story_sources.GdeltSignals):
        WINDOW_HOURS = WINDOW_HOURS

    return Breaking()


def judge(signals: list, followed_bar: int, everyone_bar: int, ratio: float) -> list:
    """[(signal, everyone)] for every cluster past the followed bar. Only the
    biggest can be proposed for everybody, and only when it stands `ratio`
    times above the next one: a busy news day is not one massive story."""
    ranked = sorted(signals, key=lambda s: -int(getattr(s, "coverage", 0) or 0))
    second = int(getattr(ranked[1], "coverage", 0) or 0) if len(ranked) > 1 else 0
    out = []
    for i, signal in enumerate(ranked):
        coverage = int(getattr(signal, "coverage", 0) or 0)
        if coverage < followed_bar:
            break
        everyone = (i == 0 and coverage >= everyone_bar
                    and coverage >= ratio * max(second, 1))
        out.append((signal, everyone))
    return out


async def detect(now: Optional[float] = None, source=None) -> list:
    """One read of the export copy. Returns the stories noted."""
    global _LAST_DETECT
    s = _settings()
    now = time.time() if now is None else now
    if not s.push_breaking:
        return []
    if source is None:
        import gdelt
        ok, _why = gdelt.available()
        if not ok:
            return []
        source = _source()
        if source.idle(now):
            return []
    _LAST_DETECT = now
    signals = await source.collect(40)
    noted = []
    for signal, everyone in judge(signals, s.push_breaking_followed_outlets,
                                  s.push_breaking_everyone_outlets,
                                  s.push_breaking_ratio):
        noted.append(store().note_breaking(
            signal.subject, signal.suggested_query or signal.subject,
            list(signal.keywords or ()), int(signal.coverage or 0), everyone, now))
    return noted


def note_finals(now: Optional[float] = None, pool=None) -> int:
    """Every final the sports sweep put in the story pool, noted once."""
    import live_facts
    import stories
    if not _settings().push_finals:
        return 0
    now = time.time() if now is None else now
    try:
        pool = stories.pool() if pool is None else pool
        held = list(pool.stories)
    except Exception:  # noqa: BLE001 - an unreadable pool tells nobody
        return 0
    n = 0
    for story in held:
        if (story.domain != stories.SPORTS
                or story.live_status != live_facts.FINAL or not story.live_line):
            continue
        seen = float(story.status_at or story.live_as_of or story.last_seen or now)
        if now - seen > LIVE_SECONDS:
            continue
        if store().note_final(story.id, story.subject or story.title, story.query,
                              story.live_line, now):
            n += 1
    return n


# --------------------------------------------------------------------------
# Writing, then telling
# --------------------------------------------------------------------------
def text_of(row: dict) -> str:
    """What a followed name is matched against."""
    return " ".join([row.get("subject", ""), row.get("line", "")]
                    + list(row.get("keywords") or []))


def message_for(row: dict) -> dict:
    import config
    from urllib.parse import urlencode
    title = row.get("title") or row.get("subject") or ""
    url = "/?" + urlencode({"listen": row["query"], "title": title})
    minutes = config.BROWSE_MINUTES
    shown = f"{minutes:g}"
    if row["kind"] == FINAL:
        line = (row.get("line") or "").replace("Final · ", "").strip()
        return {"title": f"{line} · Final" if line else "Final",
                "body": "The recap is ready. Tap to listen.",
                "url": url, "tag": f"fam-{row['id']}"}
    return {"title": "Big story", "body": f"{title}. Hear it on FAM, {shown} min.",
            "url": url, "tag": f"fam-{row['id']}"}


async def write(row: dict, generator, cache, now: Optional[float] = None) -> Optional[str]:
    """The episode a tap on this story plays, into the shared cache. Its
    title, or None when it could not be written (and nothing is sent)."""
    import config
    import daily_edition
    now = time.time() if now is None else now
    if row.get("written_at"):
        return row.get("title") or row["subject"]
    if generator is None or cache is None:
        store().failed(row["id"], "no writer on this server; nothing is sent unwritten")
        return None
    sid = f"push@{row['id']}"
    slot = datetime.fromtimestamp(now, timezone.utc)
    import metering
    with metering.spend_source("edition:push"):
        report = await daily_edition.run_slot(
            sid, slot, now, lambda: [{"query": row["query"], "title": row["subject"]}],
            generator, cache, until=now + LIVE_SECONDS, length=config.BROWSE_MINUTES,
            origin="push", label="push story")
    if report is None:
        existing = daily_edition.store().status(sid) or {}
        report = existing.get("report") if existing.get("status") == daily_edition.READY \
            else None
    result = ((report or {}).get("episodes") or {}).get(row["query"]) or {}
    if result.get("status") not in ("written", "volatile", "cached"):
        store().failed(row["id"], f"not written: {result.get('status') or 'no report'}")
        return None
    title = (result.get("title") or row["subject"]).strip()
    store().written(row["id"], title, now)
    return title


def audience(row: dict, users: list, push_store, follows_of, now: float) -> list:
    """Who should be told about this story now."""
    import push
    out = []
    everyone = row["kind"] == BREAKING and row["status"] == APPROVED
    if row["kind"] == BREAKING and row["status"] == DISMISSED:
        everyone = False
    if row["kind"] == BREAKING and not everyone and now - row["last_seen"] > FRESH_SECONDS:
        return []  # no longer big; whoever quiet hours held misses it
    text = text_of(row)
    for user_id in users:
        prefs = push_store.prefs(user_id)
        if not prefs[push.SWITCH[row["kind"]]]:
            continue
        if push_store.was_sent(user_id, row["kind"], row["id"]):
            continue
        if not everyone and not follows(follows_of(user_id), text):
            continue
        if push.in_quiet_hours(prefs.get("tz") or "", now):
            continue  # asked again next tick; goes at 08:00 if still current
        out.append(user_id)
    return out


async def tick(push_store, follows_of, allowed=None, generator=None, cache=None,
               now: Optional[float] = None, source=None) -> list:
    """One pass: detect (on GDELT's clock), note finals, then tell whoever
    should hear each live story. Returns [(story id, user id, delivered)]."""
    import asyncio
    import push
    if not push.status()["available"]:
        return []
    now = time.time() if now is None else now
    if source is not None or now - _LAST_DETECT >= DETECT_SECONDS:
        try:
            await detect(now, source=source)
        except Exception:  # noqa: BLE001 - a bad read never stops the finals
            log.exception("push: breaking detector failed")
    note_finals(now)
    users = [u for u in push_store.subscribers() if allowed is None or allowed(u)]
    if not users:
        return []
    names: dict = {}

    def named(user_id: str) -> list:
        if user_id not in names:
            try:
                names[user_id] = usable_names(follows_of(user_id))
            except Exception:  # noqa: BLE001
                names[user_id] = []
        return names[user_id]

    sent = []
    for row in store().recent(now - LIVE_SECONDS):
        if row["kind"] == BREAKING and row["status"] == DISMISSED:
            continue
        who = audience(row, users, push_store, named, now)
        if not who:
            continue
        title = await write(row, generator, cache, now)
        if not title:
            continue
        row = store().get(row["id"]) or row
        payload = message_for(row)
        for user_id in who:
            delivered = await asyncio.to_thread(push.deliver, user_id, row["kind"],
                                                row["id"], payload, push_store)
            if delivered is not None:
                sent.append((row["id"], user_id, delivered))
    if sent:
        log.info("push: story notifications sent: %s", sent)
    return sent


def admin_list(push_store, now: Optional[float] = None) -> list:
    """Every story of the last day, newest first, with how many were told."""
    now = time.time() if now is None else now
    out = []
    for row in store().recent(now - 24 * 3600):
        row = dict(row)
        row["told"] = push_store.sent_count(row["kind"], row["id"])
        row["live"] = now - row["first_seen"] <= LIVE_SECONDS
        out.append(row)
    return out
