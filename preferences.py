"""What a listener *chose*, as opposed to what they did.

`topics.py` infers taste from an append-only log of behaviour, and is
deliberately good at it: a profile there is a query over the log rather than a
stored object, so it cannot drift out of step with what actually happened.

This module is the other half, and it exists because three of the things the
interface now needs cannot be inferred at all:

* **Interests**, chosen in the intro before there is any behaviour to read.
  A brand-new listener's "Made for you" shelf was honestly empty; six chosen
  facets give the ranker something on the very first open. `topics.taste`
  folds them in at roughly the weight of one play, so real listening overtakes
  a declared interest within an evening rather than being fought by it.
* **Language**, which is stored and not yet acted on - see LANGUAGES.
* **Whether they want a weekly digest.** Stored, and read by nothing: the
  weekly recap popup that used to read it is gone, replaced by myFAM's "What
  you missed last week" rail, which needs no preference because a rail is not
  an interruption. The column stays for the same reason `language` does -
  dropping one is a migration with no benefit, and it is what a scheduled
  digest would read on the day there is one to schedule.

Being *stored* is the whole reason this is gated on having an account. Nothing
here works for an anonymous listener, by decision: a preference the server
keeps for you is precisely the class of thing an account is for. An anonymous
listener still sees the intro - their answers stay in their own browser, are
passed to the ranker for that request only, and the interface says so plainly
rather than implying they were saved.

**`recap_week` and `week_start` are kept for the same reason.** They were the
answer to "show it the first time they open on or after Sunday", which cannot
be a boolean because nothing clears it. Nothing asks the question now. They
are a stored date and a pure function of the clock, and both are what a
scheduled digest would be built on.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Iterable, Optional

import topics
from paths import data_path

log = logging.getLogger(__name__)

#: The facets an interest can be. Deliberately the *tag* vocabulary from
#: topics.py and not the 28-topic bank: a tag is what `taste` scores and what
#: `tags_for_text` maps a free search onto, so choosing tags feeds the ranker
#: directly. Choosing six of the twenty-eight topics would seed six tiles and
#: teach the feed nothing.
INTERESTS = tuple(topics.TAG_LABELS)

#: The vocabulary a stored language is validated against. **Nothing in the app
#: offers a choice from it any more** (§100): the picker was a page of the
#: first run, it was never wired to generation, and every episode was written
#: and spoken in English whatever was chosen - so the only honest thing on that
#: screen was a note saying it would not be acted on. A question in front of
#: the product that answers to nothing is worse than no question.
#:
#: The *field* stays: it is still stored, still accepted on `/api/preferences`,
#: and still validated against this list. Deleting a column is a migration with
#: a real cost and no benefit, and this is the vocabulary that per-language
#: generation will read on the day it exists. What went is the screen.
LANGUAGES = (
    {"code": "en", "label": "English", "endonym": "English"},
    {"code": "es", "label": "Spanish", "endonym": "Español"},
    {"code": "fr", "label": "French", "endonym": "Français"},
    {"code": "de", "label": "German", "endonym": "Deutsch"},
    {"code": "pt", "label": "Portuguese", "endonym": "Português"},
    {"code": "it", "label": "Italian", "endonym": "Italiano"},
    {"code": "hi", "label": "Hindi", "endonym": "हिन्दी"},
    {"code": "ar", "label": "Arabic", "endonym": "العربية"},
    {"code": "zh", "label": "Chinese", "endonym": "中文"},
    {"code": "ja", "label": "Japanese", "endonym": "日本語"},
)

LANGUAGE_CODES = frozenset(lang["code"] for lang in LANGUAGES)
DEFAULT_LANGUAGE = "en"

#: True once per-language generation actually exists. Still served on
#: `/api/preferences`, and no longer printed anywhere, because the picker it
#: used to caveat is gone - it is now a fact about the API rather than a
#: sentence under a control.
LANGUAGE_ACTIVE = False


class PreferenceError(ValueError):
    """A choice that cannot be stored, phrased so a listener can act on it."""


def week_start(now: Optional[float] = None) -> str:
    """The Sunday that began the week `now` falls in, as YYYY-MM-DD (UTC).

    UTC rather than local time, because the server has no idea where the
    listener is and something that arrives a few hours early is a smaller
    wrong than something that arrives twice.
    """
    now = time.time() if now is None else now
    stamp = time.gmtime(now)
    # tm_wday is Monday=0 .. Sunday=6; a Sunday-started week needs Sunday=0.
    since_sunday = (stamp.tm_wday + 1) % 7
    return time.strftime("%Y-%m-%d", time.gmtime(now - since_sunday * 86400))


def clean_interests(values: Iterable[str]) -> tuple[str, ...]:
    """Known facets, de-duplicated, order preserved.

    **There is no cap, and there is nothing to add one back to** (§99). It used
    to be six, disabled in the interface at the sixth chip and refused here at
    the seventh - and the number was never doing anything a listener wanted. It
    made somebody with seven interests choose which to lie about, and the
    ranker is perfectly happy to weigh eight.

    Unbounded input is still bounded, by the only thing that was ever load
    bearing here: every value has to be one of the facets, and duplicates
    collapse. Eight is therefore the most this can return, and that is a fact
    about the vocabulary rather than a rule anybody is told.
    """
    seen: list[str] = []
    for raw in values or ():
        tag = str(raw).strip().lower()
        if not tag:
            continue
        if tag not in topics.TAG_LABELS:
            raise PreferenceError(f"{tag!r} is not one of the interests on offer.")
        if tag not in seen:
            seen.append(tag)
    return tuple(seen)


def clean_language(code: str) -> str:
    lang = (code or "").strip().lower()
    if not lang:
        return DEFAULT_LANGUAGE
    if lang not in LANGUAGE_CODES:
        raise PreferenceError(f"{code!r} is not a language this app offers.")
    return lang


@dataclass(frozen=True)
class Preferences:
    """One listener's declared settings. Absent rows read as the defaults."""

    user_id: str
    interests: tuple[str, ...] = ()
    language: str = DEFAULT_LANGUAGE
    weekly_recap: bool = True
    #: The Sunday of the week whose recap they have already been shown.
    recap_week: str = ""
    intro_done: bool = False

    def as_dict(self) -> dict:
        return {
            "interests": list(self.interests),
            "language": self.language,
            "weekly_recap": self.weekly_recap,
            "recap_week": self.recap_week,
            "intro_done": self.intro_done,
        }


class PreferenceStore:
    """One row per listener. Small, boring, and read on nearly every screen."""

    def __init__(self, path: str | None = None) -> None:
        self.path = data_path("PREFS_DB", "preferences.db", path)
        self._local = threading.local()
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS preferences (
                       user_id      TEXT PRIMARY KEY,
                       interests    TEXT NOT NULL DEFAULT '',
                       language     TEXT NOT NULL DEFAULT 'en',
                       weekly_recap INTEGER NOT NULL DEFAULT 1,
                       recap_week   TEXT NOT NULL DEFAULT '',
                       intro_done   INTEGER NOT NULL DEFAULT 0,
                       updated      REAL NOT NULL
                   )"""
            )

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    def get(self, user_id: str) -> Preferences:
        """This listener's settings, or the defaults. Never raises."""
        if not user_id:
            return Preferences("")
        try:
            row = self._conn().execute(
                "SELECT interests, language, weekly_recap, recap_week, intro_done"
                " FROM preferences WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        except Exception:
            # A settings read must never be what stops an episode playing.
            log.exception("could not read preferences")
            return Preferences(user_id)
        if row is None:
            return Preferences(user_id)
        return Preferences(
            user_id=user_id,
            interests=tuple(t for t in row[0].split(",") if t),
            language=row[1] or DEFAULT_LANGUAGE,
            weekly_recap=bool(row[2]),
            recap_week=row[3] or "",
            intro_done=bool(row[4]),
        )

    def save(
        self,
        user_id: str,
        interests: Optional[Iterable[str]] = None,
        language: Optional[str] = None,
        weekly_recap: Optional[bool] = None,
        intro_done: Optional[bool] = None,
        recap_week: Optional[str] = None,
        at: float = 0.0,
    ) -> Preferences:
        """Write only the fields given. Raises PreferenceError on a bad value.

        Partial by design: the intro saves interests on one page and the
        language on the next, and the recap popup writes one flag from a screen
        that knows nothing about either.
        """
        if not user_id:
            raise PreferenceError("There is no listener to save this for.")
        current = self.get(user_id)
        merged = Preferences(
            user_id=user_id,
            interests=(clean_interests(interests) if interests is not None
                       else current.interests),
            language=(clean_language(language) if language is not None
                      else current.language),
            weekly_recap=(bool(weekly_recap) if weekly_recap is not None
                          else current.weekly_recap),
            recap_week=(recap_week if recap_week is not None else current.recap_week),
            intro_done=(bool(intro_done) if intro_done is not None
                        else current.intro_done),
        )
        self._conn().execute(
            """INSERT INTO preferences
                   (user_id, interests, language, weekly_recap, recap_week,
                    intro_done, updated)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   interests    = excluded.interests,
                   language     = excluded.language,
                   weekly_recap = excluded.weekly_recap,
                   recap_week   = excluded.recap_week,
                   intro_done   = excluded.intro_done,
                   updated      = excluded.updated""",
            (user_id, ",".join(merged.interests), merged.language,
             int(merged.weekly_recap), merged.recap_week, int(merged.intro_done),
             at or time.time()),
        )
        return merged

    def forget(self, user_id: str) -> int:
        """Erase everything this store holds for one listener.

        Part of account deletion, which App Store guideline 5.1.1(v) requires
        of any app that creates accounts. Each store implements its own rather
        than a central deleter reaching into six databases by table name: that
        deleter silently stops covering the seventh, and the failure is
        invisible until somebody audits it.

        Returns rows removed, so the endpoint can report what it did rather
        than that it tried.
        """
        removed = 0
        for table in ('preferences',):
            try:
                cur = self._conn().execute(
                    f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
                removed += cur.rowcount or 0
            except Exception:
                log.exception("could not erase %s for %r", table, user_id)
        return removed
