"""Episode quality, measured by category (PROBLEMS.md §309).

The writing is the product ([writing-is-the-problem]), and until this file
nothing measured it: a prompt change, a new example or a different writer was
judged by ear, one episode at a time. This keeps one row per written episode
and scores six things the owner named - **information quality, story flow,
information depth, repetition, the hook and the storytelling** - three ways,
because no single way is trustworthy for all six:

* **In code, on every episode, for nothing** (`measure`). Proxies, said as
  proxies: names in the script the evidence never mentions, how often a
  sentence follows from the last ("because", "so", "but") rather than being
  added to it ("and then", "also"), how many sentences carry a particular,
  sentences that say the same thing twice, whether the first two sentences
  are about what was asked, and whether the ending recaps, teases or asks.
  Each comes back with the sentences that cost it points, so a low score can
  be read rather than trusted.
* **By a model, after the episode is written, never in front of a listener**
  (`grade_script`). A stronger model than the writer, on a 1-5 scale with
  every level described, and every score must quote the sentence it rests on
  - a grade with no quote, or a quote not in the script, is thrown away. Off
  until the owner sets a budget (`QUALITY_GRADE`), capped per day.
* **By what listeners did** (`note_listen`): how far each listener got. An
  exit in the first `EARLY_EXIT_SECONDS` is the hook's real measure, and
  `FINISHED_SHARE` heard is the storytelling's.

**A grade is not trusted until it agrees with a person.** The owner's own
scores (`label`) are compared with the grader's; a metric's grades count
toward a node's standing only once they agree on `CALIBRATION_MIN` episodes
(`calibration`). Until then the page shows them, marked, and ranks on code.
And every metric is checked against listening (`validity`): one that does not
predict finishing is a metric to reword, not a number to optimise.

**Filed by the category tree** ([one-category]). A row keeps the writer's
`<<CATEGORY:>>` words and is placed against the tree as it is *when the
report is read* - the rule `app._episode_category` keeps - so a tree that
grows later still places an older episode. Every node's score is its own
episodes plus everything beneath it; a node with fewer than
`QUALITY_MIN_EPISODES` borrows its parent's; and a node is compared with its
own heading, never the whole product, because a sports recap and a health
explainer will never flow alike. `report` sorts by episodes times shortfall:
where weak writing costs the most listening comes first.

Nothing here is on the path to a first word: `note` runs where a finished
script is cached, after the listener is already hearing it, and the grader
is a task of its own. Every entry point never raises - a measurement never
costs an episode.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import threading
import time
from contextlib import closing
from typing import Callable, Iterable, Optional

import db as store_db
from config import settings
from paths import data_path

log = logging.getLogger(__name__)

DAY = 86400.0

#: The six, in the order the owner named them, with the words a page shows.
METRICS = ("information", "flow", "depth", "repetition", "hook", "storytelling")
LABELS = {
    "information": "Information quality",
    "flow": "Story flow",
    "depth": "Information depth",
    "repetition": "Repetition",
    "hook": "Hook",
    "storytelling": "Storytelling",
}

#: Episodes written before anybody tapped: the only ones `QUALITY_GRADE=ahead`
#: grades, and the only ones a later rewrite pass could ever touch.
AHEAD_ORIGINS = frozenset({"prefetch", "dailyfam", "trending", "startup"})

#: Rows are kept this long, so a change can be compared with months before it.
KEEP_DAYS = 180
#: The evidence an episode was written from is kept only until it is graded,
#: or this long: it is the largest thing in a row and only the grader reads it.
EVIDENCE_DAYS = 14
#: What of the evidence the grader is shown. A five-minute packet is ~36 KB.
EVIDENCE_CHARS = 40_000

#: Out before this many seconds is an exit on the hook.
EARLY_EXIT_SECONDS = 20.0
#: Heard this share of the episode counts as finished - the friend profile's
#: own line ([op-friend-profile]).
FINISHED_SHARE = 0.85

#: Owner-scored episodes a metric needs before its grades are trusted, and
#: how often the grade must land within one point of the owner's.
CALIBRATION_MIN = 20
CALIBRATION_WITHIN_ONE = 0.8
#: Episodes with listening a metric needs before its validity is reported.
VALIDITY_MIN = 10

#: Any two sentences this alike are the same thing said twice (content
#: words, Jaccard).
REPEAT_SIMILARITY = 0.6
#: Sentences with fewer content words than this are never called repeats -
#: "It did." twice is not repetition.
REPEAT_MIN_WORDS = 4
#: The share of joins that should carry a because/so/but for full marks on
#: flow. A target for a proxy, to be checked against the grader and listeners.
FLOW_TARGET = 0.3

# --- measured in code --------------------------------------------------------

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’-]*")

_STOPWORDS = frozenset("""
a an and are as at be been but by can could did do does for from had has have
he her his how i if in into is it its just like more most much no not of on
one or our out over she so some than that the their them then there these they
this those to too up very was we were what when where which while who why will
with would you your about after again also all any because before being both
down each few here him itself me my now off once only other own same should
such through under until upon us whom yet
""".split())

#: Openings the house rules ban by name (`script_generator.SYSTEM_PROMPT`).
_BANNED_OPENERS = re.compile(
    r"\b(picture this|imagine\b|it was a (?:cold|warm|dark|bright|quiet)\b|"
    r"here'?s what i can tell you|let'?s talk about|this is a fascinating|"
    r"there'?s a lot to unpack|as you may know|have you ever wondered|"
    r"in today'?s episode|welcome to)", re.I)
#: Signposts: the writer talking about the piece instead of telling it.
_SIGNPOSTS = re.compile(
    r"\b(here'?s the (?:thing|mechanism|machinery|twist|kicker)|"
    r"here is the (?:machinery|mechanism)|let'?s (?:dig|dive|break|look|start)|"
    r"so what does (?:this|that|it) mean|the short answer is|"
    r"the answer is simple)", re.I)
#: A recap or a summary at the end ([speak-from-inside]).
_RECAP = re.compile(
    r"\b(to sum up|in summary|in conclusion|the bottom line|all in all|"
    r"in short\b|so there you have it|and that'?s the story|to recap|"
    r"long story short)", re.I)
#: A tease or a forecast at the end ([endings-do-not-tease]).
_TEASE = re.compile(
    r"\b(remains to be seen|wait and see|watch this space|only time will tell|"
    r"time will tell|raises another question|stay tuned|we'?ll have to see|"
    r"the question now is|what happens next is anyone'?s|next time)", re.I)

#: A join that makes the next fact follow from the last.
_CAUSAL = re.compile(
    r"\b(because|so|therefore|but|yet|instead|which is why|that'?s why|"
    r"as a result|which meant|that meant|meaning|until|since|which made|"
    r"forcing|leaving|so that|thanks to|despite|although|though)\b", re.I)
#: A join that only adds the next fact to the pile.
_ADDITIVE = re.compile(
    r"^\s*(and then|also|another|in addition|additionally|meanwhile|next|"
    r"on top of that|plus|furthermore|moreover|elsewhere)\b", re.I)

_NUMBER_WORDS = frozenset("""
one two three four five six seven eight nine ten eleven twelve thirteen
fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty
sixty seventy eighty ninety hundred thousand million billion trillion percent
half quarter first second third fourth fifth sixth seventh eighth ninth tenth
dozen
""".split())
_MONTHS_AND_DAYS = frozenset("""
january february march april may june july august september october november
december monday tuesday wednesday thursday friday saturday sunday
""".split())
#: Capitalised for reasons other than being a name.
_NOT_NAMES = frozenset({"i", "i'm", "i've", "i'd", "i'll", "ok", "okay"})


def _tokens(sentence: str) -> list[str]:
    return _WORD.findall(sentence or "")


def _content(sentence: str) -> set:
    return {t.lower().strip("'’-") for t in _tokens(sentence)
            if t.lower() not in _STOPWORDS and len(t) > 2}


def _names(sentence: str) -> list[str]:
    """Capitalised words that are not the sentence's first - names, places,
    organisations, months - possessives stripped."""
    out = []
    for token in _tokens(sentence)[1:]:
        if not token[:1].isupper():
            continue
        word = re.sub(r"['’]s$", "", token).strip("'’-")
        if len(word) < 2 or word.lower() in _NOT_NAMES:
            continue
        out.append(word)
    return out


def _has_particular(sentence: str) -> bool:
    """A name, a place, a figure or a date: something only this story has."""
    if re.search(r"\d", sentence or ""):
        return True
    words = {t.lower().strip("'’-") for t in _tokens(sentence)}
    for word in words:
        if word in _NUMBER_WORDS or any(part in _NUMBER_WORDS
                                        for part in word.split("-")):
            return True
    return bool(_names(sentence))


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 3)


def measure(sentences: Iterable[str], query: str = "", evidence: str = "",
            word_budget: int = 0) -> dict:
    """The code half of an episode's quality. Never raises.

    `{"scores": {metric: 0-1 or None}, "checks": {...}, "words": n,
    "opener": first four words}`. A score is None where code has nothing to
    go on (no evidence to ground names against, no names to ground). Every
    check lists the sentences that cost it points.
    """
    try:
        return _measure([" ".join(str(s).split()) for s in sentences if s],
                        query or "", evidence or "", int(word_budget or 0))
    except Exception:  # noqa: BLE001 - a measurement never costs an episode
        log.exception("could not measure an episode")
        return {"scores": {m: None for m in METRICS}, "checks": {},
                "words": 0, "opener": ""}


def _measure(sentences: list[str], query: str, evidence: str,
             word_budget: int) -> dict:
    sentences = [s for s in sentences if s]
    n = len(sentences)
    words = sum(len(_tokens(s)) for s in sentences)
    scores: dict = {m: None for m in METRICS}
    checks: dict = {}
    if not n:
        return {"scores": scores, "checks": checks, "words": 0, "opener": ""}

    # Information quality: names the evidence never mentions.
    if evidence.strip():
        haystack = evidence.lower()
        names = []
        for s in sentences:
            for name in _names(s):
                if name.lower() in _MONTHS_AND_DAYS:
                    continue
                names.append(name)
        unique = sorted(set(names), key=names.index)
        missing = [name for name in unique
                   if not re.search(r"\b" + re.escape(name.lower()) + r"\b",
                                    haystack)]
        checks["unsupported_names"] = missing
        if unique:
            scores["information"] = 1 - len(missing) / len(unique)

    # Story flow: joins that follow rather than add.
    causal = [s for s in sentences[1:] if _CAUSAL.search(s)]
    additive = [s for s in sentences[1:] if _ADDITIVE.search(s)]
    joins = max(1, n - 1)
    checks["causal_joins"] = len(causal)
    checks["additive_joins"] = additive
    if n > 1:
        scores["flow"] = (min(1.0, (len(causal) / joins) / FLOW_TARGET)
                          * (1 - len(additive) / joins))

    # Information depth: sentences that carry a particular, and whether the
    # time was used (a ceiling, never a quota - only reported).
    empty = [s for s in sentences if not _has_particular(s)]
    density = 1 - len(empty) / n
    checks["no_particular"] = empty
    if word_budget:
        used = words / word_budget
        checks["budget_share"] = round(used, 3)
        scores["depth"] = (density + min(1.0, used / 0.85)) / 2
    else:
        scores["depth"] = density

    # Repetition: any two sentences saying the same thing.
    sets = [_content(s) for s in sentences]
    repeated: list = []
    for j in range(n):
        if len(sets[j]) < REPEAT_MIN_WORDS:
            continue
        for i in range(j):
            if len(sets[i]) >= REPEAT_MIN_WORDS and \
                    _jaccard(sets[i], sets[j]) >= REPEAT_SIMILARITY:
                repeated.append(sentences[j])
                break
    checks["repeats"] = repeated
    scores["repetition"] = 1 - len(repeated) / n

    # The hook: the first two sentences, about what was asked, situated, and
    # not one of the banned openings.
    head = " ".join(sentences[:2])
    asked = _content(query)
    on_question = (not asked) or bool(asked & _content(head))
    situated = any(_has_particular(s) for s in sentences[:2])
    banned = bool(_BANNED_OPENERS.search(head))
    checks["hook"] = {"on_question": on_question, "situated": situated,
                      "banned_opener": banned}
    scores["hook"] = (int(on_question) + int(situated) + int(not banned)) / 3

    # Storytelling: how it ends, and whether it talks about itself.
    tail = " ".join(sentences[-2:])
    last = sentences[-1]
    ending = {"question": last.rstrip().rstrip("\"'”’)").endswith("?"),
              "recap": bool(_RECAP.search(tail)),
              "tease": bool(_TEASE.search(tail))}
    signposts = [s for s in sentences if _SIGNPOSTS.search(s)
                 or _BANNED_OPENERS.search(s)]
    checks["ending"] = ending
    checks["signposts"] = signposts
    scores["storytelling"] = (
        (1 - sum(ending.values()) / 3) * 0.7
        + (1 - min(1.0, len(signposts) / max(1, n) * 4)) * 0.3)

    opener = " ".join(t.lower() for t in _tokens(sentences[0])[:4])
    return {"scores": {m: _round(v) for m, v in scores.items()},
            "checks": checks, "words": words, "opener": opener}


# --- the store ---------------------------------------------------------------


class QualityStore:
    """`episodes` (one row per written episode), `listens` (how far each
    listener got) and `labels` (the owner's own scores)."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = data_path("EPISODE_QUALITY_DB", "episode_quality.db", path)
        self._lock = threading.Lock()
        with closing(self._connect()) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS episodes (
                              key           TEXT NOT NULL,
                              sourced       INTEGER NOT NULL,
                              at            REAL NOT NULL,
                              query         TEXT NOT NULL DEFAULT '',
                              minutes       INTEGER NOT NULL DEFAULT 0,
                              origin        TEXT NOT NULL DEFAULT '',
                              structure     TEXT NOT NULL DEFAULT '',
                              category      TEXT NOT NULL DEFAULT '',
                              title         TEXT NOT NULL DEFAULT '',
                              writer_model  TEXT NOT NULL DEFAULT '',
                              prompt        TEXT NOT NULL DEFAULT '',
                              words         INTEGER NOT NULL DEFAULT 0,
                              word_budget   INTEGER NOT NULL DEFAULT 0,
                              sentences     TEXT NOT NULL DEFAULT '[]',
                              evidence      TEXT NOT NULL DEFAULT '',
                              measures      TEXT NOT NULL DEFAULT '{}',
                              grade         TEXT NOT NULL DEFAULT '',
                              grade_model   TEXT NOT NULL DEFAULT '',
                              grade_dollars REAL NOT NULL DEFAULT 0,
                              graded_at     REAL NOT NULL DEFAULT 0,
                              PRIMARY KEY (key, sourced))""")
            db.execute("CREATE INDEX IF NOT EXISTS episodes_at ON episodes (at)")
            db.execute("""CREATE TABLE IF NOT EXISTS listens (
                              key      TEXT NOT NULL,
                              sourced  INTEGER NOT NULL,
                              listener TEXT NOT NULL,
                              heard    REAL NOT NULL DEFAULT 0,
                              duration REAL NOT NULL DEFAULT 0,
                              minutes  INTEGER NOT NULL DEFAULT 0,
                              at       REAL NOT NULL,
                              PRIMARY KEY (key, sourced, listener))""")
            db.execute("CREATE INDEX IF NOT EXISTS listens_key ON listens (key)")
            db.execute("""CREATE TABLE IF NOT EXISTS labels (
                              key     TEXT NOT NULL,
                              sourced INTEGER NOT NULL,
                              metric  TEXT NOT NULL,
                              score   INTEGER NOT NULL,
                              by_whom TEXT NOT NULL DEFAULT '',
                              at      REAL NOT NULL,
                              PRIMARY KEY (key, sourced, metric))""")

    def _connect(self):
        db = store_db.connect(self.path, timeout=5, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def _rows(self, sql: str, args: tuple = ()) -> list:
        with closing(self._connect()) as db:
            cursor = db.execute(sql, args)
            names = [c[0] for c in cursor.description]
            return [dict(zip(names, values)) for values in cursor]

    def record(self, row: dict, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        columns = ("key", "sourced", "at", "query", "minutes", "origin",
                   "structure", "category", "title", "writer_model", "prompt",
                   "words", "word_budget", "sentences", "evidence", "measures")
        with self._lock, closing(self._connect()) as db:
            # A re-written key under the same sourced stamp is the same
            # episode written again: the newer text is the one heard.
            db.execute(
                f"INSERT INTO episodes ({', '.join(columns)}) VALUES "
                f"({', '.join('?' for _ in columns)}) "
                "ON CONFLICT(key, sourced) DO UPDATE SET "
                + ", ".join(f"{c} = excluded.{c}" for c in columns[2:])
                + ", grade = '', grade_model = '', grade_dollars = 0, "
                "graded_at = 0",
                tuple(row.get(c) for c in columns))
            db.execute("DELETE FROM episodes WHERE at < ?",
                       (now - KEEP_DAYS * DAY,))
            db.execute("UPDATE episodes SET evidence = '' WHERE evidence != '' "
                       "AND at < ?", (now - EVIDENCE_DAYS * DAY,))
            db.execute("DELETE FROM listens WHERE at < ?",
                       (now - KEEP_DAYS * DAY,))

    def get(self, key: str, sourced: int) -> Optional[dict]:
        rows = self._rows("SELECT * FROM episodes WHERE key = ? AND sourced = ?",
                          (key, int(sourced)))
        return rows[0] if rows else None

    def set_grade(self, key: str, sourced: int, grade: dict, model: str,
                  dollars: float, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        with self._lock, closing(self._connect()) as db:
            # The evidence has done its only job once graded.
            db.execute("UPDATE episodes SET grade = ?, grade_model = ?, "
                       "grade_dollars = ?, graded_at = ?, evidence = '' "
                       "WHERE key = ? AND sourced = ?",
                       (json.dumps(grade), model, float(dollars or 0), now,
                        key, int(sourced)))

    def pending(self, limit: int = 20, origins: Optional[Iterable] = None) -> list:
        """Ungraded episodes, newest first."""
        rows = self._rows("SELECT * FROM episodes WHERE grade = '' "
                          "ORDER BY at DESC LIMIT ?", (int(limit) * 5,))
        if origins is not None:
            wanted = set(origins)
            rows = [r for r in rows if r["origin"] in wanted]
        return rows[:int(limit)]

    def graded_since(self, at: float) -> int:
        rows = self._rows("SELECT COUNT(*) AS n FROM episodes WHERE graded_at >= ?",
                          (at,))
        return int(rows[0]["n"]) if rows else 0

    def spent_since(self, at: float) -> float:
        rows = self._rows("SELECT COALESCE(SUM(grade_dollars), 0) AS d FROM "
                          "episodes WHERE graded_at >= ?", (at,))
        return float(rows[0]["d"]) if rows else 0.0

    def episodes_since(self, at: float) -> list:
        return self._rows("SELECT key, sourced, at, query, minutes, origin, "
                          "structure, category, title, writer_model, prompt, "
                          "words, word_budget, measures, grade, grade_model "
                          "FROM episodes WHERE at >= ? ORDER BY at DESC", (at,))

    def note_listen(self, key: str, sourced: int, listener: str, heard: float,
                    duration: float, minutes: int,
                    now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        with self._lock, closing(self._connect()) as db:
            db.execute(
                "INSERT INTO listens (key, sourced, listener, heard, duration, "
                "minutes, at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(key, sourced, listener) DO UPDATE SET "
                "heard = CASE WHEN excluded.heard > listens.heard "
                "             THEN excluded.heard ELSE listens.heard END, "
                "duration = CASE WHEN excluded.duration > 0 "
                "                THEN excluded.duration ELSE listens.duration END, "
                "at = excluded.at",
                (key, int(sourced), listener, float(heard), float(duration),
                 int(minutes), now))

    def listens_for(self, keys: Iterable[str]) -> list:
        keys = sorted(set(keys))
        out: list = []
        for start in range(0, len(keys), 400):
            chunk = keys[start:start + 400]
            out.extend(self._rows(
                "SELECT key, sourced, heard, duration, minutes FROM listens "
                f"WHERE key IN ({', '.join('?' for _ in chunk)})", tuple(chunk)))
        return out

    def label(self, key: str, sourced: int, metric: str, score: int,
              by_whom: str = "", now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        with self._lock, closing(self._connect()) as db:
            db.execute(
                "INSERT INTO labels (key, sourced, metric, score, by_whom, at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(key, sourced, metric) "
                "DO UPDATE SET score = excluded.score, by_whom = excluded.by_whom, "
                "at = excluded.at",
                (key, int(sourced), metric, int(score), by_whom, now))

    def unlabelled(self, limit: int = 20, origins: Optional[Iterable] = None) -> list:
        """Episodes the owner has not scored yet, newest first - the list a
        person works through so the grader can be checked (§310)."""
        rows = self._rows(
            "SELECT e.key, e.sourced, e.at, e.query, e.title, e.origin, "
            "e.minutes, e.grade != '' AS graded FROM episodes e WHERE NOT EXISTS "
            "(SELECT 1 FROM labels l WHERE l.key = e.key AND l.sourced = e.sourced) "
            "ORDER BY e.at DESC LIMIT ?", (int(limit) * 10,))
        if origins is not None:
            wanted = set(origins)
            rows = [r for r in rows if r["origin"] in wanted]
        return rows[:int(limit)]

    def labels(self) -> list:
        return self._rows("SELECT key, sourced, metric, score, by_whom, at "
                          "FROM labels")

    def forget(self, user_id: str) -> int:
        with self._lock, closing(self._connect()) as db:
            cursor = db.execute("DELETE FROM listens WHERE listener = ?",
                                (user_id,))
            return int(cursor.rowcount or 0)

    def clear(self) -> int:
        with self._lock, closing(self._connect()) as db:
            n = db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
            db.execute("DELETE FROM episodes")
            db.execute("DELETE FROM listens")
            db.execute("DELETE FROM labels")
            return int(n)


_STORE: Optional[QualityStore] = None
_STORE_LOCK = threading.Lock()


def store() -> QualityStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = QualityStore()
        return _STORE


def reset(quality_store: Optional[QualityStore] = None) -> None:
    """For tests: use this store (or open a fresh one on next use)."""
    global _STORE
    with _STORE_LOCK:
        _STORE = quality_store
    _PROMPT_VERSION.clear()


def exists() -> bool:
    """Whether there is anything to report. Reading never creates the store:
    it is created by the first written episode, like the routing rows."""
    if _STORE is not None:
        return True
    return store_db.has_tables(data_path("EPISODE_QUALITY_DB", "episode_quality.db"))


_PROMPT_VERSION: dict = {}


def prompt_version() -> str:
    """Twelve hex digits naming the writer's instructions as they stand -
    house rules and examples - so a report can compare one version with
    the next."""
    if "v" not in _PROMPT_VERSION:
        try:
            import script_generator

            text = script_generator.system_prompt()
        except Exception:  # noqa: BLE001
            text = ""
        _PROMPT_VERSION["v"] = hashlib.sha256(text.encode()).hexdigest()[:12]
    return _PROMPT_VERSION["v"]


def note(key: str, sourced, query: str, minutes: int, origin: str,
         sentences: list, notes=None) -> None:
    """Measure one written episode and keep it, where its script is cached.

    Called beside `category_audit.note` at all four places a script is
    written to the shared cache (a tap, prefetch, DailyFAM, Trending), so
    what is measured is exactly what is kept - never an attachment's
    episode, which is never cached ([op-attachments]), and never a private
    search's (origin `private`), which keeps nothing ([private-search]).
    Never raises."""
    try:
        if not key or not sentences:
            return
        import cache

        # A private search keeps nothing of the listener's past the episode
        # itself ([private-search]): no row here, as no history and no author.
        if (origin or "") == cache.PRIVATE_ORIGIN:
            return
        sourced = int(sourced or time.time())
        evidence = str(getattr(notes, "written_from", "") or "")
        budget = int(getattr(notes, "word_budget", 0) or 0)
        measures = measure(sentences, query, evidence, budget)
        store().record({
            "key": key, "sourced": sourced, "at": time.time(),
            "query": " ".join(str(query or "").split())[:300],
            "minutes": int(minutes or 0), "origin": str(origin or "")[:20],
            "structure": str(getattr(notes, "structure", "") or "")[:40],
            "category": str(getattr(notes, "category", "") or "")[:80],
            "title": str(getattr(notes, "title", "") or "")[:160],
            "writer_model": str(getattr(notes, "writer_model", "") or "")[:60],
            "prompt": prompt_version(),
            "words": measures["words"], "word_budget": budget,
            "sentences": json.dumps([str(s) for s in sentences]),
            "evidence": evidence[:EVIDENCE_CHARS],
            "measures": json.dumps(measures),
        })
        _grade_soon(key, sourced, origin)
    except Exception:  # noqa: BLE001 - a measurement never costs an episode
        log.exception("could not keep the quality of %r", query)


def note_listen(episode: str, listener: str, seconds: float, duration: float,
                minutes: int) -> None:
    """How far one listener got through one episode (`cache.episode_id`).

    Only for a listener whose listening is remembered (`app._remembers`);
    the caller checks. Keeps the furthest point heard. Never raises."""
    try:
        import cache

        parsed = cache.parse_episode_id(episode or "")
        if parsed is None or not listener:
            return
        key, sourced = parsed
        store().note_listen(key, sourced, listener, max(0.0, float(seconds or 0)),
                            max(0.0, float(duration or 0)), int(minutes or 0))
    except Exception:  # noqa: BLE001 - a measurement never costs playback
        log.exception("could not keep a listen")


def label(episode: str, metric: str, score: int, by_whom: str = "owner") -> bool:
    """The owner's own score for one metric of one episode, 1-5. What the
    grader is calibrated against. False for anything malformed."""
    import cache

    parsed = cache.parse_episode_id(episode or "")
    if parsed is None or metric not in METRICS:
        return False
    try:
        score = int(score)
    except (TypeError, ValueError):
        return False
    if not 1 <= score <= 5:
        return False
    store().label(parsed[0], parsed[1], metric, score, by_whom)
    return True


def forget(user_id: str) -> int:
    """A deleted account's listening ([op-identity]). Never creates the store."""
    if not user_id or not exists():
        return 0
    return store().forget(user_id)


def clear() -> int:
    """For a wipe (`demo_data`): every row goes. Never creates the store."""
    if not exists():
        return 0
    return store().clear()


# --- graded by a model ---------------------------------------------------------

#: Each level described, so a 3 means the same thing on every call. Written
#: from the house rules (`script_generator.SYSTEM_PROMPT`, docs/claude/product.md).
RUBRIC = {
    "information": (
        "Is every fact true to the evidence? 5: every name, figure, date and "
        "result is in the evidence or settled common knowledge, and the "
        "better-supported source wins any disagreement. 4: one minor detail "
        "is not in the evidence but is not wrong. 3: a detail stated more "
        "confidently than the evidence allows. 2: a claim the evidence does "
        "not support, or a result for something not reported as finished. "
        "1: an invented statistic, quote, name or result."),
    "flow": (
        "Does each beat follow from the one before? 5: because / therefore / "
        "but throughout; nothing could be reordered without breaking it. "
        "4: one stretch that is a list. 3: about half causal, half 'and "
        "then'. 2: mostly facts in time order or source by source. 1: a "
        "list of facts with no connection."),
    "depth": (
        "For its length, how far under the stock answer does it go? Judge "
        "against what the minutes are for. 5: the mechanism or the turn "
        "that explains it, with concrete particulars, and nothing generic. "
        "4: the mechanism, thinly evidenced. 3: correct but the stock "
        "answer. 2: skims several threads without explaining any. "
        "1: generalities that would fit a different topic."),
    "repetition": (
        "Is anything said twice? 5: every sentence adds something new. "
        "4: one restatement. 3: a point made twice in different words. "
        "2: several restatements, or the ending repeats the opening. "
        "1: the piece circles the same two or three facts."),
    "hook": (
        "The first two sentences. 5: concrete, already in motion, situates "
        "who/what/when in particulars, opens a small 'wait, why?', and could "
        "not open an episode about anything else. 4: specific and situated "
        "but opens no question. 3: on topic but generic, or states the "
        "conclusion at once. 2: background, history from years back, or "
        "scene-setting before the subject. 1: throat-clearing, a banned "
        "opener ('picture this', 'let's talk about'), or not about what "
        "was asked."),
    "storytelling": (
        "Is it a story made of the facts, and does it end right? 5: tension "
        "then release, one concrete anchor, a point of view, and the last "
        "line is the most concrete thing in it - then it stops. 4: a story, "
        "with a soft ending. 3: a briefing with some shape. 2: a briefing, "
        "or decoration ('imagine', atmosphere). 1: ends on a recap, a tease, "
        "a forecast or a question to the listener."),
}

GRADE_SYSTEM = (
    "You grade FAM episodes: short spoken pieces that answer what somebody "
    "asked, told as a story made of the facts. You are an editor, not the "
    "writer, and you are strict - most competent pieces are 3s and 4s; a 5 "
    "is a piece you would hold up as the house standard.\n\n"
    "Score each of six things from 1 to 5 using exactly these levels:\n\n"
    + "\n\n".join(f"{name}: {text}" for name, text in RUBRIC.items())
    + "\n\nFor every score, `quote` is the one sentence of the script, copied "
    "exactly, that the score most rests on, and `reason` is one plain "
    "sentence. `unsupported_claims` lists every claim in the script the "
    "evidence does not support, each in a few words; empty when there are "
    "none. When there is no evidence, grade information on internal "
    "consistency and plausibility alone and say so in its reason."
)


def _schema() -> dict:
    metric = {"type": "object",
              "properties": {"score": {"type": "integer"},
                             "quote": {"type": "string"},
                             "reason": {"type": "string"}},
              "required": ["score", "quote", "reason"],
              "additionalProperties": False}
    properties = {m: metric for m in METRICS}
    properties["unsupported_claims"] = {"type": "array",
                                        "items": {"type": "string"}}
    return {"type": "object", "properties": properties,
            "required": list(METRICS) + ["unsupported_claims"],
            "additionalProperties": False}


GRADE_SCHEMA = _schema()


def grade_prompt(query: str, minutes: int, structure: str, evidence: str,
                 sentences: list) -> str:
    script = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(sentences))
    return (f"They asked: {query}\nLength: {minutes} minute"
            f"{'s' if minutes != 1 else ''}"
            + (f"\nStory shape the brief chose: {structure.replace('_', ' ')}"
               if structure else "")
            + "\n\nThe evidence the writer was given:\n"
            + (evidence[:EVIDENCE_CHARS].strip() or "(none)")
            + "\n\nThe script, one sentence per line:\n" + script)


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def read_grade(data: dict, sentences: list) -> dict:
    """The grader's answer, held to what a grade may say: a score from 1 to
    5 and a quote that is really in the script, or that metric is dropped."""
    script = _squash(" ".join(sentences))
    out: dict = {}
    for metric in METRICS:
        part = (data or {}).get(metric)
        if not isinstance(part, dict):
            continue
        try:
            score = int(part.get("score"))
        except (TypeError, ValueError):
            continue
        quote = _squash(str(part.get("quote") or ""))
        if not 1 <= score <= 5 or not quote or quote not in script:
            continue
        out[metric] = {"score": score, "quote": str(part.get("quote"))[:400],
                       "reason": str(part.get("reason") or "")[:400]}
    claims = (data or {}).get("unsupported_claims")
    out["unsupported_claims"] = ([str(c)[:200] for c in claims][:20]
                                 if isinstance(claims, list) else [])
    return out


async def grade_script(client, query: str, minutes: int, structure: str,
                       evidence: str, sentences: list) -> tuple:
    """`(grade, model, dollars, failed)` for one script: `grade` is None when
    there is none, and `failed` says why. Raises nothing but cancellation."""
    import metering

    model = settings.quality_grader_model
    try:
        response = await client.messages.create(
            model=model, max_tokens=6000, system=GRADE_SYSTEM,
            output_config={"effort": settings.quality_grader_effort,
                           "format": {"type": "json_schema",
                                      "schema": GRADE_SCHEMA}},
            messages=[{"role": "user", "content": grade_prompt(
                query, minutes, structure, evidence, sentences)}])
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("quality grade failed for %r: %s", query, exc)
        return None, model, 0.0, f"{type(exc).__name__}: {exc}"
    usage = metering.Usage()
    usage.add_model_call(model, getattr(response, "usage", None))
    dollars = metering.price_of(usage).total
    if getattr(response, "stop_reason", "") in ("refusal", "max_tokens"):
        return None, model, dollars, f"the grader stopped: {response.stop_reason}"
    try:
        text = next(b.text for b in response.content if b.type == "text")
        data = json.loads(text)
    except (StopIteration, AttributeError, ValueError, TypeError) as exc:
        return None, model, dollars, f"the grader returned nothing readable: {exc}"
    return read_grade(data, sentences), model, dollars, ""


def may_grade(origin: str) -> bool:
    """Whether `QUALITY_GRADE` covers an episode from `origin`, and whether
    anything may be spent at all (staging never: [zero-spend-staging])."""
    mode = (settings.quality_grade or "off").strip().lower()
    if mode not in ("ahead", "all"):
        return False
    try:
        import spend_guard

        if spend_guard.enabled():
            return False
    except Exception:  # noqa: BLE001
        return False
    return mode == "all" or origin in AHEAD_ORIGINS


async def grade_stored(client, key: str, sourced: int,
                       now: Optional[float] = None) -> Optional[dict]:
    """Grade one kept episode and keep the grade, inside the daily ceiling.
    The failure is on /admin's errors ([failures-visible])."""
    now = time.time() if now is None else now
    quality = store()
    if quality.graded_since(now - DAY) >= settings.quality_grade_daily:
        return None
    row = quality.get(key, sourced)
    if row is None or row.get("grade"):
        return None
    sentences = json.loads(row.get("sentences") or "[]")
    grade, model, dollars, failed = await grade_script(
        client, row["query"], row["minutes"], row["structure"],
        row.get("evidence") or "", sentences)
    if grade is None:
        import pipeline_errors

        pipeline_errors.record("episode", "quality grade failed", failed,
                               row["query"], pipeline_errors.NOTICE)
        return None
    quality.set_grade(key, sourced, grade, model, dollars, now)
    return grade


async def grade_pending(client, limit: int = 20,
                        origins: Optional[Iterable] = None) -> int:
    """Grade what has not been graded yet, newest first: `tools/quality_report.py
    --grade`, for an eval run or a backlog. Returns how many were graded."""
    graded = 0
    for row in store().pending(limit, origins):
        if await grade_stored(client, row["key"], row["sourced"]) is not None:
            graded += 1
    return graded


_TASKS: set = set()
_CLIENT: list = []


def _client():
    if not _CLIENT:
        import credentials
        from anthropic_client import build_async_client

        # The key in force, as the thumbnail calls read it.
        _CLIENT.append(build_async_client(credentials.active("ANTHROPIC_API_KEY")))
    return _CLIENT[0]


def _grade_soon(key: str, sourced: int, origin: str) -> None:
    """A task of its own on the running loop, when grading covers this
    episode. Nothing awaits it."""
    if not may_grade(origin):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return   # no loop to run on: `tools/quality_report.py --grade` will
    task = loop.create_task(_grade_quietly(key, sourced))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def _grade_quietly(key: str, sourced: int) -> None:
    try:
        await grade_stored(_client(), key, sourced)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - a grade never costs anything else
        log.exception("could not grade %s", key)


# --- calibration and validity -----------------------------------------------


def _grades_by_episode(rows: list) -> dict:
    out = {}
    for row in rows:
        if row.get("grade"):
            try:
                out[(row["key"], int(row["sourced"]))] = json.loads(row["grade"])
            except (TypeError, ValueError):
                continue
    return out


def calibration(rows: list, labels: list) -> dict:
    """Per metric, how the grader's scores compare with the owner's:
    `n`, the mean gap, the share within one point, and `trusted`."""
    grades = _grades_by_episode(rows)
    out = {}
    for metric in METRICS:
        pairs = []
        for item in labels:
            if item["metric"] != metric:
                continue
            graded = grades.get((item["key"], int(item["sourced"])), {}).get(metric)
            if graded:
                pairs.append((int(item["score"]), int(graded["score"])))
        n = len(pairs)
        gaps = [abs(a - b) for a, b in pairs]
        within = sum(1 for g in gaps if g <= 1) / n if n else None
        out[metric] = {
            "n": n, "labelled": sum(1 for i in labels if i["metric"] == metric),
            "mean_gap": round(sum(gaps) / n, 2) if n else None,
            "within_one": None if within is None else round(within, 3),
            "trusted": bool(n >= CALIBRATION_MIN
                            and within is not None
                            and within >= CALIBRATION_WITHIN_ONE)}
    return out


def _ranks(values: list) -> list:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs: list, ys: list) -> Optional[float]:
    """Rank correlation, ties averaged. None when either side is constant."""
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    vy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if not vx or not vy:
        return None
    return round(cov / (vx * vy), 3)


# --- the report ----------------------------------------------------------------


def _listening(listens: list, episodes: list) -> dict:
    """Per episode `(key, sourced)`: listens, early exits, finishes and the
    mean share heard. A listen is matched to its episode by key and the
    nearest sourced stamp within a few seconds."""
    by_key: dict = {}
    for row in episodes:
        by_key.setdefault(row["key"], []).append(int(row["sourced"]))
    out: dict = {}
    for item in listens:
        stamps = by_key.get(item["key"])
        if not stamps:
            continue
        sourced = int(item["sourced"] or 0)
        if sourced:
            near = min(stamps, key=lambda s: abs(s - sourced))
            if abs(near - sourced) > 5:
                continue
        else:
            near = max(stamps)
        length = float(item["duration"] or 0) or float(item["minutes"] or 0) * 60
        heard = float(item["heard"] or 0)
        share = min(1.0, heard / length) if length > 0 else 0.0
        stats = out.setdefault((item["key"], near),
                               {"listens": 0, "early": 0, "finished": 0,
                                "heard": 0.0})
        stats["listens"] += 1
        stats["early"] += int(heard < EARLY_EXIT_SECONDS)
        stats["finished"] += int(share >= FINISHED_SHARE)
        stats["heard"] += share
    return out


def _default_place() -> tuple:
    import stories
    import topics

    tree = topics.category_tree()

    def resolve(words: str) -> str:
        return stories.resolve_category(words, near=True) if words else ""

    def ancestors(node: str) -> list:
        return tree.ancestors(node)

    def label_of(node: str) -> str:
        found = tree.get(node)
        if found is not None:
            return found.label
        return topics.TAG_LABELS.get(node, node)

    return resolve, ancestors, label_of


UNPLACED = "unplaced"


def report(days: float = 30.0, now: Optional[float] = None,
           resolve: Optional[Callable] = None,
           ancestors: Optional[Callable] = None,
           label_of: Optional[Callable] = None,
           quality: Optional[QualityStore] = None) -> dict:
    """Every node with episodes under it, worst-costing first, plus the
    grader's calibration, each metric's validity and what grading spent."""
    now = time.time() if now is None else now
    if quality is None:
        if not exists():
            return {"episodes": 0, "nodes": [], "metrics": list(METRICS),
                    "labels": LABELS, "calibration": {}, "validity": {},
                    "grading": grading_report(None, now), "days": days,
                    "origins": {}}
        quality = store()
    if resolve is None or ancestors is None or label_of is None:
        d_resolve, d_ancestors, d_label = _default_place()
        resolve = resolve or d_resolve
        ancestors = ancestors or d_ancestors
        label_of = label_of or d_label

    rows = quality.episodes_since(now - days * DAY)
    labels = quality.labels()
    trusted = {m: c["trusted"] for m, c in calibration(rows, labels).items()}
    listening = _listening(quality.listens_for(r["key"] for r in rows), rows)

    placed: dict = {}

    def place(words: str) -> list:
        if words not in placed:
            node = ""
            try:
                node = resolve(words) or ""
            except Exception:  # noqa: BLE001
                node = ""
            if not node:
                placed[words] = [UNPLACED]
            else:
                try:
                    up = list(ancestors(node))
                except Exception:  # noqa: BLE001
                    up = []
                placed[words] = [node] + [a for a in up if a != node]
        return placed[words]

    nodes: dict = {}
    episodes = []
    for row in rows:
        try:
            measures = json.loads(row.get("measures") or "{}")
        except ValueError:
            measures = {}
        try:
            grade = json.loads(row["grade"]) if row.get("grade") else {}
        except ValueError:
            grade = {}
        heard = listening.get((row["key"], int(row["sourced"])))
        values = {}
        for metric in METRICS:
            code = (measures.get("scores") or {}).get(metric)
            graded = grade.get(metric, {}).get("score") if grade else None
            values[metric] = {
                "code": code,
                "grade": None if graded is None else (graded - 1) / 4,
            }
        if heard:
            values["hook"]["listeners"] = 1 - heard["early"] / heard["listens"]
            values["storytelling"]["listeners"] = (heard["finished"]
                                                   / heard["listens"])
        path = place(row.get("category") or "")
        episodes.append({"row": row, "values": values, "heard": heard,
                         "path": path, "opener": measures.get("opener", "")})
        for depth, node in enumerate(path):
            entry = nodes.setdefault(node, {
                "node": node, "path": list(reversed(path[depth:])),
                "parent": path[depth + 1] if depth + 1 < len(path) else "",
                "own": 0, "episodes": [], })
            entry["episodes"].append(episodes[-1])
            if depth == 0:
                entry["own"] += 1

    def overall(values: dict) -> Optional[float]:
        picked = []
        for metric in METRICS:
            v = values[metric]
            use = v["grade"] if (trusted.get(metric) and v["grade"] is not None) \
                else v["code"]
            if use is not None:
                picked.append(use)
        return sum(picked) / len(picked) if picked else None

    def mean(items: list) -> Optional[float]:
        items = [x for x in items if x is not None]
        return round(sum(items) / len(items), 3) if items else None

    out_nodes = []
    for node, entry in nodes.items():
        eps = entry["episodes"]
        scores = {}
        for metric in METRICS:
            scores[metric] = {
                kind: mean([e["values"][metric].get(kind) for e in eps])
                for kind in ("code", "grade", "listeners")}
            scores[metric]["trusted"] = bool(trusted.get(metric))
        heard = [e["heard"] for e in eps if e["heard"]]
        listens = sum(h["listens"] for h in heard)
        openers = [e["opener"] for e in eps if e["opener"]]
        shared = max((openers.count(o) for o in set(openers)), default=0)
        out_nodes.append({
            "node": node, "label": label_of(node) if node != UNPLACED
            else "Not placed in the tree",
            "path": entry["path"], "parent": entry["parent"],
            "depth": len(entry["path"]) - 1,
            "episodes": len(eps), "own_episodes": entry["own"],
            "enough": len(eps) >= settings.quality_min_episodes,
            "scores": scores,
            "overall": mean([overall(e["values"]) for e in eps]),
            "listens": listens,
            "early_exit": round(sum(h["early"] for h in heard) / listens, 3)
            if listens else None,
            "finished": round(sum(h["finished"] for h in heard) / listens, 3)
            if listens else None,
            "heard": round(sum(h["heard"] for h in heard) / listens, 3)
            if listens else None,
            # The same first four words on more than one episode in a node
            # is a template, which every listener of that node hears.
            "shared_opener": shared if shared > 1 else 0,
            "structures": _count(e["row"]["structure"] for e in eps),
            "worst": _worst(eps, overall),
        })

    by_node = {n["node"]: n for n in out_nodes}
    for item in out_nodes:
        # A node with too few episodes borrows the nearest ancestor that has
        # enough; every node is held to its own heading's standard.
        borrowed = ""
        if not item["enough"]:
            for up in reversed(item["path"][:-1]):
                if by_node.get(up, {}).get("enough"):
                    borrowed = up
                    break
        item["borrowed_from"] = borrowed
        facet = by_node.get(item["path"][0]) if item["path"] else None
        facet_overall = facet["overall"] if facet else None
        item["facet_overall"] = facet_overall
        shortfall = None
        if item["overall"] is not None and facet_overall is not None:
            shortfall = round(facet_overall - item["overall"], 3)
        item["shortfall"] = shortfall
        item["priority"] = (round(item["episodes"] * max(0.0, shortfall), 3)
                            if (shortfall is not None and item["enough"]) else 0.0)
    out_nodes.sort(key=lambda n: (-n["priority"], -n["episodes"], n["node"]))

    return {"episodes": len(episodes), "days": days, "nodes": out_nodes,
            "metrics": list(METRICS), "labels": LABELS,
            "calibration": calibration(rows, labels),
            "validity": validity(episodes),
            "grading": grading_report(quality, now),
            "prompts": _count(r["prompt"] for r in rows),
            "writers": _count(r["writer_model"] for r in rows),
            # Each origin's means side by side: a tap, prefetch, the
            # editions (§310).
            "origins": _by_origin(episodes, overall)}


def _by_origin(episodes: list, overall: Callable) -> dict:
    out: dict = {}
    for e in episodes:
        origin = e["row"].get("origin") or "tap"
        item = out.setdefault(origin, {"episodes": 0, "_overall": [],
                                       "_code": {m: [] for m in METRICS},
                                       "_grade": {m: [] for m in METRICS}})
        item["episodes"] += 1
        value = overall(e["values"])
        if value is not None:
            item["_overall"].append(value)
        for metric in METRICS:
            for kind in ("code", "grade"):
                v = e["values"][metric].get(kind)
                if v is not None:
                    item[f"_{kind}"][metric].append(v)

    def mean(xs):
        return round(sum(xs) / len(xs), 3) if xs else None

    for item in out.values():
        item["overall"] = mean(item.pop("_overall"))
        item["code"] = {m: mean(v) for m, v in item.pop("_code").items()}
        item["grade"] = {m: mean(v) for m, v in item.pop("_grade").items()}
    return out


def _count(items: Iterable) -> dict:
    out: dict = {}
    for item in items:
        if item:
            out[item] = out.get(item, 0) + 1
    return out


def _worst(eps: list, overall: Callable) -> list:
    """The node's three weakest episodes, to read rather than average."""
    scored = [(overall(e["values"]), e) for e in eps]
    scored = [s for s in scored if s[0] is not None]
    scored.sort(key=lambda s: s[0])
    out = []
    for value, e in scored[:3]:
        row = e["row"]
        out.append({"episode": f"{row['key']}.{int(row['sourced'])}",
                    "query": row["query"], "title": row["title"],
                    "overall": round(value, 3)})
    return out


def validity(episodes: list) -> dict:
    """Per metric and per kind of measure, whether it predicts how much of
    an episode its listeners heard (Spearman, `VALIDITY_MIN` episodes)."""
    out = {}
    for metric in METRICS:
        out[metric] = {}
        for kind in ("code", "grade"):
            xs, ys = [], []
            for e in episodes:
                value = e["values"][metric].get(kind)
                if value is None or not e["heard"]:
                    continue
                xs.append(value)
                ys.append(e["heard"]["heard"] / e["heard"]["listens"])
            out[metric][kind] = {
                "n": len(xs),
                "predicts_heard": spearman(xs, ys) if len(xs) >= VALIDITY_MIN
                else None}
    return out


def grading_report(quality: Optional[QualityStore], now: float) -> dict:
    out = {"mode": settings.quality_grade, "model": settings.quality_grader_model,
           "effort": settings.quality_grader_effort,
           "daily_ceiling": settings.quality_grade_daily,
           "graded_today": None, "spent_today": None}
    if quality is not None:
        out["graded_today"] = quality.graded_since(now - DAY)
        out["spent_today"] = round(quality.spent_since(now - DAY), 4)
    return out


# --- printing, for write.py and the report tool ----------------------------


def format_measures(measures: dict) -> str:
    """The code half, as `write.py` prints it under the script."""
    scores = measures.get("scores") or {}
    checks = measures.get("checks") or {}
    lines = ["quality (code proxies, 0-1; §309):"]
    for metric in METRICS:
        value = scores.get(metric)
        lines.append(f"  {LABELS[metric]:<20} "
                     + ("-" if value is None else f"{value:.2f}"))
    hook = checks.get("hook") or {}
    if hook and not all((hook.get("on_question"), hook.get("situated"),
                         not hook.get("banned_opener"))):
        lines.append(f"  hook: {hook}")
    ending = checks.get("ending") or {}
    if any(ending.values()):
        lines.append(f"  ending: {', '.join(k for k, v in ending.items() if v)}")
    for name, title in (("unsupported_names", "not in the evidence"),
                        ("repeats", "said twice"),
                        ("additive_joins", "'and then' joins"),
                        ("signposts", "signposts"),
                        ("no_particular", "no particular")):
        found = checks.get(name) or []
        if found:
            shown = found if name == "unsupported_names" else found[:3]
            lines.append(f"  {title}: " + " | ".join(str(f)[:90] for f in shown))
    return "\n".join(lines)


def format_grade(grade: Optional[dict]) -> str:
    if not grade:
        return "graded: no grade (see the log)"
    lines = ["graded (1-5):"]
    for metric in METRICS:
        part = grade.get(metric)
        if part:
            lines.append(f"  {LABELS[metric]:<20} {part['score']}  "
                         f"{part['reason']}")
        else:
            lines.append(f"  {LABELS[metric]:<20} -  (dropped: no score or no "
                         "quote from the script)")
    claims = grade.get("unsupported_claims") or []
    if claims:
        lines.append("  unsupported: " + "; ".join(claims))
    return "\n".join(lines)
