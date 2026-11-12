"""Optional surveys: what the beta testers (and later everyone) think of FAM.

Three surveys, decided at the 11.11 beta meeting (PROBLEMS.md §552):

* **episode** - "How was that episode?", one tap on the What's next card, two
  if something was off. Offered after a listener's first finished episode,
  then every `EPISODE_EVERY`th, never twice in `ONCE_A_DAY`.
* **day3** - the check-in: who they are, what frustrated them, and the 1-10
  "part of your daily routine" question. Offered on an app open once the
  account is `DAY3_DAYS` old.
* **wrap** - the two-week wrap-up: the "how disappointed" question, the 1-10
  again, price, and one thing to change. Offered from `WRAP_DAYS`; somebody
  who never answered the check-in by then is offered this one instead.

Every question is multiple choice with an optional "Other: ___" box (the
format the team agreed), or a 1-10 scale, or one free line.

Rules this module keeps:

**Optional means optional.** Every survey can be put off ("Not now") and is
offered again a day later, at most `MAX_DISMISSALS` times, then never. Nothing
in the app waits on one.

**Accounts only.** A guest's events are never logged (`app._remembers`), and
an answer is the most personal event there is.

**An answer is not a taste signal.** Nothing here feeds a ranker; the answers
are read by people, on /admin.

**The words stay here.** Free text is kept in this store and shown on /admin;
nothing sends it to a model (`consent.py` decides that, and nothing asks).

**Deleting an account anonymises its answers** (as `feedback.py` does): the
answer stays in the totals, the link to the person goes.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import sqlite3
import threading
import time
import uuid
from typing import Optional

import db as store_db
from paths import data_path

log = logging.getLogger(__name__)

DAY = 86400.0
#: How old an account is (from `accounts.created`) before each app survey.
DAY3_DAYS = 3
WRAP_DAYS = 13
#: The episode check: after the first finished episode, then every Nth.
EPISODE_EVERY = 5
#: No survey is offered twice inside this, answered or not. Twenty hours,
#: not twenty-four, so a listener who opens FAM at the same time every
#: morning is not pushed a day later each time.
ONCE_A_DAY = 20 * 3600.0
#: "Not now" this many times and the survey is never offered again.
MAX_DISMISSALS = 3
#: The longest free line kept, per question.
MAX_TEXT = 500
#: Answers one listener may send in an hour - a person sends three surveys
#: in two weeks; this only stops a loop.
PER_HOUR = 20


def _opts(*pairs) -> list[dict]:
    return [{"id": a, "label": b} for a, b in pairs]


#: Where people keep up now, and which part of FAM they used: asked in both
#: app surveys, so one list each.
_FEATURES = _opts(("search", "Search"), ("myfam", "myFAM"),
                  ("mixes", "DailyFAM mixes"), ("friends", "Friends & messages"),
                  ("none", "Haven't really used it yet"))
_ROUTINE = {"id": "routine", "kind": "scale", "min": 1, "max": 10,
            "low": "Not likely", "high": "Every day",
            "text": "How likely is FAM to become part of your daily routine?"}

SURVEYS: dict[str, dict] = {
    "episode": {
        "id": "episode",
        "title": "How was that episode?",
        "intro": "",
        "questions": [
            {"id": "rating", "kind": "single", "text": "How was that episode?",
             "options": _opts(("loved", "Loved it"), ("good", "Good"),
                              ("meh", "Meh"), ("bad", "Didn't work"))},
            {"id": "off", "kind": "multi", "text": "What was off?",
             "show_if": {"question": "rating", "in": ["good", "meh", "bad"]},
             "options": _opts(("unanswered", "Didn't answer what I asked"),
                              ("facts", "Facts felt wrong or out of date"),
                              ("long", "Too long"), ("short", "Too short"),
                              ("vague", "Vague or boring"),
                              ("name", "Mispronounced a name"),
                              ("slow", "Took too long to start")),
             # The option whose box asks a question of its own.
             "detail": {"name": "Which name?"},
             "other": True},
        ],
    },
    "day3": {
        "id": "day3",
        "title": "Got 60 seconds?",
        "intro": "Seven quick questions about your first few days. Skip any you like.",
        "questions": [
            {"id": "age", "kind": "single", "text": "Your age",
             # Not asked when the account already has a date of birth.
             "skip_if_known": "birth_date",
             "options": _opts(("u18", "Under 18"), ("18_24", "18-24"),
                              ("25_34", "25-34"), ("35_54", "35-54"),
                              ("55", "55+"))},
            {"id": "role", "kind": "single", "text": "Which describes you?",
             "options": _opts(("student", "Student"), ("working", "Working"),
                              ("parent", "Parent"), ("retired", "Retired")),
             "other": True},
            {"id": "sources", "kind": "multi",
             "text": "Where do you keep up with things now?",
             "options": _opts(("social", "Instagram / TikTok"),
                              ("groupchats", "Group chats"),
                              ("podcasts", "Podcasts"),
                              ("news", "News apps or sites"), ("tv", "TV"),
                              ("facebook", "Facebook groups"),
                              ("reddit_x", "Reddit / X")),
             "other": True},
            {"id": "used", "kind": "multi", "text": "What have you used most?",
             "options": _FEATURES},
            {"id": "frustrations", "kind": "multi", "text": "What's frustrated you?",
             "options": _opts(("waiting", "Waiting for episodes to start"),
                              ("lost", "Didn't know where to tap"),
                              ("wrong", "Episode wasn't what I asked"),
                              ("facts", "Facts felt off"),
                              ("voice", "The voice"),
                              ("share", "Hard to share"),
                              ("friends", "Couldn't find friends"),
                              ("nothing", "Nothing yet")),
             "other": True},
            dict(_ROUTINE),
            {"id": "when", "kind": "multi", "text": "When would you listen?",
             "options": _opts(("morning", "Morning"), ("commute", "Commute"),
                              ("gym", "Gym"), ("waiting", "Waiting in line"),
                              ("bed", "Before bed")),
             "other": True},
        ],
    },
    "wrap": {
        "id": "wrap",
        "title": "Two weeks of FAM",
        "intro": "Six questions. Your answers decide what we build next.",
        "questions": [
            {"id": "disappointed", "kind": "single",
             "text": "How would you feel if you could no longer use FAM?",
             "options": _opts(("very", "Very disappointed"),
                              ("somewhat", "Somewhat disappointed"),
                              ("not", "Not disappointed"))},
            dict(_ROUTINE),
            {"id": "talked", "kind": "single",
             "text": "Did FAM give you something to bring up with people?",
             "options": _opts(("yes", "Yes, I shared or mentioned an episode"),
                              ("not_yet", "Not yet"), ("no", "No"))},
            {"id": "miss", "kind": "single", "text": "What would you miss most?",
             "options": _FEATURES},
            {"id": "price", "kind": "single",
             "text": "What would you pay per month?",
             "options": _opts(("free", "Only free"), ("2.99", "$2.99"),
                              ("4.99", "$4.99"), ("7.99", "$7.99"),
                              ("9.99", "$9.99+"))},
            {"id": "change", "kind": "text", "text": "One thing you'd change",
             "placeholder": "Anything at all"},
        ],
    },
}

#: The order an app open looks for one: the wrap-up first, so somebody past
#: two weeks who skipped the check-in is asked the later one, not both.
APP_SURVEYS = ("wrap", "day3")
MOMENTS = ("open", "episode_end")


class SurveyError(ValueError):
    """An answer that cannot be kept, with the sentence that says why."""


def definition(survey_id: str, *, known: Optional[dict] = None) -> Optional[dict]:
    """The survey as the client draws it, minus questions whose answer the
    account already holds (`skip_if_known`)."""
    survey = SURVEYS.get(survey_id)
    if survey is None:
        return None
    known = known or {}
    out = dict(survey)
    out["questions"] = [dict(q) for q in survey["questions"]
                        if not known.get(q.get("skip_if_known", ""))]
    for q in out["questions"]:
        q.pop("skip_if_known", None)
    return out


def _visible(question: dict, answers: dict) -> bool:
    cond = question.get("show_if")
    if not cond:
        return True
    pick = (answers.get(cond["question"]) or {}).get("pick")
    return pick in cond.get("in", [])


def clean_answers(survey_id: str, raw: object) -> dict:
    """Validate what a client sent. Raises `SurveyError` with a sentence.

    The shape is `{question_id: {"pick": ..., "text": ...}}`: `pick` is an
    option id (single), a list of them (multi) or a whole number (scale);
    `text` is the "Other" box, the option's own box (`detail`), or the answer
    to a `text` question. Unknown questions and options are refused rather
    than dropped - a client sending them is a client drawing a different
    survey, and that should be loud.
    """
    survey = SURVEYS.get(survey_id)
    if survey is None:
        raise SurveyError("There is no such survey.")
    if not isinstance(raw, dict):
        raise SurveyError("The answers were not readable.")
    by_id = {q["id"]: q for q in survey["questions"]}
    out: dict[str, dict] = {}
    for qid, value in raw.items():
        q = by_id.get(qid)
        if q is None:
            raise SurveyError(f"{qid!r} is not a question on this survey.")
        if not isinstance(value, dict):
            raise SurveyError(f"The answer to {qid!r} was not readable.")
        pick = value.get("pick")
        raw_text = value.get("text")
        if raw_text is not None and not isinstance(raw_text, str):
            raise SurveyError(f"The words for {qid!r} were not readable.")
        text = (raw_text or "").strip()[:MAX_TEXT]
        ids = {o["id"] for o in q.get("options", [])}
        kind = q["kind"]
        if kind == "single":
            if pick is not None and (not isinstance(pick, str) or pick not in ids):
                raise SurveyError(f"{pick!r} is not an answer to {qid!r}.")
        elif kind == "multi":
            if pick is None:
                pick = []
            if not isinstance(pick, list) or any(
                    not isinstance(p, str) or p not in ids for p in pick):
                raise SurveyError(f"One of the answers to {qid!r} is not on the list.")
            pick = list(dict.fromkeys(pick))
        elif kind == "scale":
            if pick is not None:
                if isinstance(pick, bool) or not isinstance(pick, int) \
                        or not q["min"] <= pick <= q["max"]:
                    raise SurveyError(
                        f"The answer to {qid!r} is a number from {q['min']} to {q['max']}.")
        else:  # text
            pick = None
        if text and kind != "text" and not (q.get("other") or q.get("detail")):
            text = ""
        if pick in (None, []) and not text:
            continue
        entry: dict = {}
        if pick not in (None, []):
            entry["pick"] = pick
        if text:
            entry["text"] = text
        out[qid] = entry
    # A follow-up the client should not have shown is not kept.
    out = {qid: v for qid, v in out.items() if _visible(by_id[qid], out)}
    if not out:
        raise SurveyError("Pick at least one answer, or tap Not now.")
    return out


class SurveyStore:
    def __init__(self, path: str | None = None) -> None:
        self.path = data_path("SURVEYS_DB", "surveys.db", path)
        self._local = threading.local()
        self._stamps: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS responses (
                       id            TEXT PRIMARY KEY,
                       user_id       TEXT NOT NULL DEFAULT '',
                       survey        TEXT NOT NULL,
                       created       REAL NOT NULL,
                       answers       TEXT NOT NULL,
                       episode_id    TEXT NOT NULL DEFAULT '',
                       episode_title TEXT NOT NULL DEFAULT '',
                       build         TEXT NOT NULL DEFAULT ''
                   )""")
            conn.execute("CREATE INDEX IF NOT EXISTS responses_survey"
                         " ON responses(survey, created)")
            # One row per listener and survey: when it was last offered, how
            # often it was put off, when it was answered. `ended` counts the
            # finished episodes the episode check is spaced over.
            conn.execute(
                """CREATE TABLE IF NOT EXISTS offers (
                       user_id     TEXT NOT NULL,
                       survey      TEXT NOT NULL,
                       offered     INTEGER NOT NULL DEFAULT 0,
                       last_offer  REAL NOT NULL DEFAULT 0,
                       dismissed   INTEGER NOT NULL DEFAULT 0,
                       answered_at REAL NOT NULL DEFAULT 0,
                       ended       INTEGER NOT NULL DEFAULT 0,
                       PRIMARY KEY (user_id, survey)
                   )""")

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = store_db.connect(self.path, timeout=5.0, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    # --- offers -----------------------------------------------------------

    def _state(self, user_id: str, survey: str) -> dict:
        row = self._conn().execute(
            "SELECT offered, last_offer, dismissed, answered_at, ended FROM offers"
            " WHERE user_id = ? AND survey = ?", (user_id, survey)).fetchone()
        keys = ("offered", "last_offer", "dismissed", "answered_at", "ended")
        return dict(zip(keys, row)) if row else dict.fromkeys(keys, 0)

    def _ensure(self, user_id: str, survey: str) -> None:
        self._conn().execute(
            "INSERT OR IGNORE INTO offers (user_id, survey) VALUES (?, ?)",
            (user_id, survey))

    def _open(self, state: dict, now: float) -> bool:
        """Whether a survey in this state may be offered now."""
        return (not state["answered_at"]
                and state["dismissed"] < MAX_DISMISSALS
                and now - state["last_offer"] >= ONCE_A_DAY)

    def _mark_offered(self, user_id: str, survey: str, now: float) -> None:
        self._ensure(user_id, survey)
        self._conn().execute(
            "UPDATE offers SET offered = offered + 1, last_offer = ?"
            " WHERE user_id = ? AND survey = ?", (now, user_id, survey))

    def offer(self, user_id: str, moment: str, *, account_created: Optional[float],
              now: Optional[float] = None) -> Optional[str]:
        """The survey to show now, or None - and, when one is returned, a
        record that it was offered (so it is not offered again for a day).

        `moment` is `open` (the app came to the front, nothing playing) or
        `episode_end` (the What's next card is up).
        """
        if not user_id or moment not in MOMENTS:
            return None
        at = time.time() if now is None else now
        if moment == "episode_end":
            self._ensure(user_id, "episode")
            self._conn().execute(
                "UPDATE offers SET ended = ended + 1 WHERE user_id = ? AND survey = 'episode'",
                (user_id,))
            state = self._state(user_id, "episode")
            # Answered once is not done: this one is asked again and again,
            # spaced out, so `answered_at` does not close it.
            due = state["ended"] == 1 or state["ended"] % EPISODE_EVERY == 0
            if (due and state["dismissed"] < MAX_DISMISSALS
                    and at - state["last_offer"] >= ONCE_A_DAY):
                self._mark_offered(user_id, "episode", at)
                return "episode"
            return None
        if account_created is None:
            return None  # an account with no creation date is never guessed at
        age_days = (at - float(account_created)) / DAY
        for survey, days in (("wrap", WRAP_DAYS), ("day3", DAY3_DAYS)):
            if age_days < days:
                continue
            state = self._state(user_id, survey)
            if self._open(state, at):
                self._mark_offered(user_id, survey, at)
                return survey
            # Past two weeks, the check-in is moot: either the wrap-up is
            # offered, or it was today, or it is done - never both surveys.
            return None
        return None

    def dismiss(self, user_id: str, survey: str) -> bool:
        """'Not now'. False for a survey that does not exist."""
        if not user_id or survey not in SURVEYS:
            return False
        self._ensure(user_id, survey)
        self._conn().execute(
            "UPDATE offers SET dismissed = dismissed + 1"
            " WHERE user_id = ? AND survey = ?", (user_id, survey))
        return True

    # --- answers ----------------------------------------------------------

    def _admit(self, key: str, at: float) -> bool:
        with self._lock:
            kept = [t for t in self._stamps.get(key, []) if at - t < 3600]
            if len(kept) >= PER_HOUR:
                self._stamps[key] = kept
                return False
            kept.append(at)
            self._stamps[key] = kept
            if len(self._stamps) > 1024:
                for stale in [k for k, v in self._stamps.items()
                              if not v or at - v[-1] >= 3600]:
                    del self._stamps[stale]
            return True

    def answer(self, user_id: str, survey: str, answers: object, *,
               episode_id: str = "", episode_title: str = "", build: str = "",
               now: Optional[float] = None) -> dict:
        """Keep one response. Raises `SurveyError` with a sentence."""
        if not user_id:
            raise SurveyError("Surveys are for accounts.")
        cleaned = clean_answers(survey, answers)
        at = time.time() if now is None else now
        if not self._admit(user_id, at):
            raise SurveyError("That is a lot of answers in an hour - try again later.")
        rid = uuid.uuid4().hex[:12]
        if survey != "episode":
            episode_id = episode_title = ""
        self._conn().execute(
            "INSERT INTO responses (id, user_id, survey, created, answers,"
            " episode_id, episode_title, build) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, user_id, survey, at, json.dumps(cleaned),
             str(episode_id or "")[:200], str(episode_title or "")[:200],
             str(build or "")[:200]))
        self._ensure(user_id, survey)
        self._conn().execute(
            "UPDATE offers SET answered_at = ? WHERE user_id = ? AND survey = ?",
            (at, user_id, survey))
        return {"id": rid, "survey": survey, "answers": cleaned, "created": at}

    def responses(self, survey: str, limit: int = 5000) -> list[dict]:
        rows = self._conn().execute(
            "SELECT id, user_id, created, answers, episode_id, episode_title, build"
            " FROM responses WHERE survey = ? ORDER BY created DESC LIMIT ?",
            (survey, max(1, int(limit)))).fetchall()
        out = []
        for r in rows:
            try:
                answers = json.loads(r[3])
            except ValueError:
                answers = {}
            out.append({"id": r[0], "user_id": r[1], "created": r[2],
                        "answers": answers if isinstance(answers, dict) else {},
                        "episode_id": r[4], "episode_title": r[5], "build": r[6]})
        return out

    def summary(self) -> list[dict]:
        """Each survey with a tally per question, for /admin."""
        out = []
        for sid, survey in SURVEYS.items():
            rows = self.responses(sid)
            offered, dismissed = self._conn().execute(
                "SELECT COALESCE(SUM(offered), 0), COALESCE(SUM(dismissed), 0)"
                " FROM offers WHERE survey = ?", (sid,)).fetchone()
            questions = []
            for q in survey["questions"]:
                counts = {o["id"]: 0 for o in q.get("options", [])}
                texts, scores = [], []
                answered = 0
                for r in rows:
                    a = r["answers"].get(q["id"])
                    if not a:
                        continue
                    answered += 1
                    pick = a.get("pick")
                    if q["kind"] == "scale" and isinstance(pick, int):
                        scores.append(pick)
                    elif isinstance(pick, list):
                        for p in pick:
                            counts[p] = counts.get(p, 0) + 1
                    elif pick is not None:
                        counts[pick] = counts.get(pick, 0) + 1
                    if a.get("text"):
                        texts.append({"text": a["text"], "created": r["created"],
                                      "episode_title": r["episode_title"]})
                entry = {"id": q["id"], "text": q["text"], "kind": q["kind"],
                         "answered": answered, "texts": texts[:200]}
                if q.get("options"):
                    entry["options"] = [{"id": o["id"], "label": o["label"],
                                         "count": counts.get(o["id"], 0)}
                                        for o in q["options"]]
                if q["kind"] == "scale":
                    entry["mean"] = round(sum(scores) / len(scores), 1) if scores else None
                    entry["spread"] = {str(n): scores.count(n)
                                       for n in range(q["min"], q["max"] + 1)}
                questions.append(entry)
            out.append({"id": sid, "title": survey["title"], "responses": len(rows),
                        "offered": int(offered), "dismissed": int(dismissed),
                        "questions": questions})
        return out

    @staticmethod
    def _cell(value: str) -> str:
        """A typed line as a spreadsheet will read it: one starting with
        = + - @ (or a tab or return) is a formula to Sheets and Excel, so it
        is kept as text with a leading apostrophe."""
        value = str(value or "")
        return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value

    def csv(self, survey: str) -> str:
        """One row per response, one column per question (and its box), with
        the listener id so a row can be joined to what that listener did."""
        spec = SURVEYS.get(survey)
        if spec is None:
            raise SurveyError("There is no such survey.")
        buf = io.StringIO()
        out = csv.writer(buf)
        head = ["response_id", "created_utc", "user_id", "episode_id", "episode_title", "build"]
        for q in spec["questions"]:
            head.append(q["id"])
            if q["kind"] != "text" and (q.get("other") or q.get("detail")):
                head.append(q["id"] + "_text")
        out.writerow(head)
        for r in reversed(self.responses(survey)):
            row = [r["id"], time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(r["created"])),
                   r["user_id"], r["episode_id"], self._cell(r["episode_title"]), r["build"]]
            for q in spec["questions"]:
                a = r["answers"].get(q["id"]) or {}
                pick = a.get("pick")
                if q["kind"] == "text":
                    row.append(self._cell(a.get("text", "")))
                    continue
                row.append(";".join(pick) if isinstance(pick, list)
                           else ("" if pick is None else str(pick)))
                if q.get("other") or q.get("detail"):
                    row.append(self._cell(a.get("text", "")))
            out.writerow(row)
        return buf.getvalue()

    def forget(self, user_id: str) -> int:
        """An account deleted: its answers stay in the totals, unnamed, and
        its offer record goes."""
        if not user_id:
            return 0
        cur = self._conn().execute(
            "UPDATE responses SET user_id = '' WHERE user_id = ?", (user_id,))
        self._conn().execute("DELETE FROM offers WHERE user_id = ?", (user_id,))
        return cur.rowcount or 0
