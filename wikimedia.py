"""The reference look: Wikipedia and Wikidata, for who someone is (§278).

A web search answers what happened; it is a poor way to learn that a
footballer was born in 1998 or that a company's chief executive is who it is.
Those are settled facts, and an encyclopedia is where they are kept. This
module asks Wikimedia for them, beside the main search, and `research` adds
what comes back to the packet as **one extra source after the web sources** -
background, never a current state, and never a replacement for anything the
search found.

Three rules, each the same as one the live providers already keep:

* **The name is resolved from the catalogue, never guessed** - the
  `balldontlie.pick_player` rule. Wikidata's own search is asked, and a match
  is accepted only when its English label (or the alias it matched on) is the
  asked name, ignoring case, accents and punctuation. Two exact matches are
  told apart by the question's own words against their descriptions, and if
  that does not leave exactly one, there is no answer: ambiguity is no answer.
* **Facts are computed in code.** An age is subtracted from a date of birth on
  the listener's clock, a year-only date gives no age, and a position is
  "listed as" current only when Wikidata gives it no end date. Every sentence
  says "Wikidata lists ...", so nothing here is ever presented as news.
* **Everything is counted** (`provider_usage`), every request carries a
  User-Agent with a contact (`places.user_agent`, which Wikimedia's policy
  asks for), at most three are in flight at once, and a 429 is honoured by
  backing off for its `Retry-After` - never by retrying inside the request.

The summary comes from **Wikimedia Enterprise** when this deployment has an
account (its free plan is 50,000 requests a month; FAM stops at
`WIKIMEDIA_MONTHLY_REQUESTS`, counted in this module's own store so a restart
does not reset it), and from Wikipedia's keyless REST summary otherwise. The
Enterprise reply's infoboxes are not used: they are the article's free text,
and the facts here come from Wikidata's statements, where a date is a date.

Each name's answer - including "no match" - is kept for a day
(`CACHE_SECONDS`), so a second episode about the same person asks nothing.
What is kept is the raw material, not the sentences: an age is computed again
on every read, on that listener's clock.

**Unverified against the real services**: the build container's egress blocks
en.wikipedia.org, www.wikidata.org and *.enterprise.wikimedia.com, so every
request shape here was read from the published API documentation and the
`wme-sdk-python` SDK, and is exercised only against stubs.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
import unicodedata
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Optional
from urllib.parse import quote

import db as store_db
from config import settings
from paths import data_path

log = logging.getLogger("wikimedia")

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/"
ARTICLE_URL = "https://en.wikipedia.org/wiki/"
ENTITY_URL = "https://www.wikidata.org/wiki/"
ENTERPRISE_LOGIN = "https://auth.enterprise.wikimedia.com/v1/login"
ENTERPRISE_REFRESH = "https://auth.enterprise.wikimedia.com/v1/token-refresh"
ENTERPRISE_ARTICLE = "https://api.enterprise.wikimedia.com/v2/structured-contents/"

#: How long one name's answer is kept, "no match" included.
CACHE_SECONDS = 86400
#: How much of an article's lead reaches the packet.
SUMMARY_CHARS = 700
#: Requests in flight at once, across every episode (Wikimedia asks for few).
CONCURRENCY = 3
#: How long to stay away after a 429 that names no `Retry-After`.
DEFAULT_BACKOFF_SECONDS = 60.0
#: Enterprise's tokens: an access token lasts about a day, a refresh token
#: about ninety. Both are renewed a little early.
ACCESS_SECONDS = 23 * 3600
REFRESH_SECONDS = 89 * 86400
#: The most ids one `wbgetentities` call may name.
MAX_IDS = 50

#: Wikidata properties read, by name.
P_INSTANCE, HUMAN = "P31", "Q5"
P_BORN, P_DIED = "P569", "P570"
P_CITIZEN, P_OCCUPATION = "P27", "P106"
P_POSITION, P_TEAM, P_EMPLOYER = "P39", "P54", "P108"
P_START, P_END = "P580", "P582"
P_INCEPTION, P_HQ, P_CEO, P_FOUNDER = "P571", "P159", "P169", "P112"

#: For tests: `None` follows `WIKIPEDIA`; the suite sets False so no test
#: reaches Wikimedia unless it turns this on (tests/conftest.py).
ENABLED_OVERRIDE: Optional[bool] = None


def enabled() -> bool:
    if ENABLED_OVERRIDE is not None:
        return bool(ENABLED_OVERRIDE)
    return bool(getattr(settings, "wikipedia", False))


@dataclass
class Reference:
    """One encyclopedia entry, as the packet and the sources panel read it."""

    title: str = ""
    url: str = ""
    description: str = ""
    summary: str = ""
    facts: list = field(default_factory=list)
    revised: Optional[datetime] = None
    qid: str = ""
    via: str = ""          # "enterprise" | "public"

    def as_dict(self) -> dict:
        out = asdict(self)
        out["revised"] = self.revised.isoformat() if self.revised else ""
        return out


class WikimediaError(RuntimeError):
    """A request failed. Carries the host, path and status - never a token."""


class BackingOff(WikimediaError):
    """A 429 asked us to wait, and the wait has not passed."""


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
_SLOTS = threading.BoundedSemaphore(CONCURRENCY)
#: provider -> monotonic time before which nothing is sent to it.
_BACKOFF: dict = {}
_BACKOFF_LOCK = threading.Lock()


def _transport(method: str, url: str, *, params: Optional[dict] = None,
               body: Any = None, headers: Optional[dict] = None,
               timeout: float = 2.0) -> tuple:
    """One HTTP request: (status, headers, parsed JSON or None). Raises on a
    network failure. The seam the tests replace; nothing else sends."""
    import httpx

    with httpx.Client(timeout=timeout) as client:
        reply = client.request(method, url, params=params, json=body,
                               headers=headers)
    try:
        parsed = reply.json()
    except ValueError:
        parsed = None
    return reply.status_code, dict(reply.headers), parsed


def _send(method: str, url: str, *, params: Optional[dict] = None,
          body: Any = None, headers: Optional[dict] = None) -> tuple:
    """A counted, identified, rate-respecting request: (status, json)."""
    import places
    import provider_usage

    host = url.split("//", 1)[-1].split("/", 1)[0]
    path = "/" + url.split("//", 1)[-1].split("/", 1)[-1].split("?", 1)[0]
    provider = provider_usage.provider_for_host(host)
    with _BACKOFF_LOCK:
        until = _BACKOFF.get(provider, 0.0)
    if until > time.monotonic():
        raise BackingOff(f"{host}: backing off for "
                         f"{until - time.monotonic():.0f}s after a 429")
    sent = {"User-Agent": places.user_agent() + " (reference look)",
            "Accept": "application/json"}
    sent.update(headers or {})
    with _SLOTS:
        try:
            status, reply_headers, parsed = _transport(
                method, url, params=params, body=body, headers=sent,
                timeout=float(getattr(settings, "wikipedia_timeout_seconds", 2.0)))
        except Exception as exc:
            provider_usage.record(provider, ok=False, why=type(exc).__name__)
            raise WikimediaError(f"{host}{path}: {type(exc).__name__}") from None
    ok = 200 <= int(status) < 300
    # The path, never the query or a body: a token never reaches a log.
    provider_usage.record(provider, ok=ok, why=f"HTTP {status} {path[:60]}")
    if status == 429:
        wait = _retry_after(reply_headers)
        with _BACKOFF_LOCK:
            _BACKOFF[provider] = time.monotonic() + wait
        log.warning("wikimedia: %s asked us to slow down; nothing more is "
                    "sent to it for %.0fs", host, wait)
    return int(status), parsed


def _retry_after(headers: dict) -> float:
    for name, value in (headers or {}).items():
        if str(name).lower() == "retry-after":
            try:
                return max(1.0, float(str(value).strip()))
            except ValueError:
                return DEFAULT_BACKOFF_SECONDS
    return DEFAULT_BACKOFF_SECONDS


def _wikidata(params: dict) -> dict:
    status, parsed = _send("GET", WIKIDATA_API, params={**params, "format": "json"})
    if status != 200 or not isinstance(parsed, dict):
        raise WikimediaError(f"wikidata answered HTTP {status}")
    return parsed


# --------------------------------------------------------------------------
# The store: the day's answers, and Enterprise's monthly count
# --------------------------------------------------------------------------
class ReferenceStore:
    """`answers(key, at, body)` and `enterprise(month, requests)`."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = data_path("WIKIMEDIA_DB", "wikimedia.db", path)
        self._lock = threading.Lock()
        with closing(self._connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS answers ("
                       " key TEXT PRIMARY KEY, at REAL NOT NULL, body TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS enterprise ("
                       " month TEXT PRIMARY KEY,"
                       " requests INTEGER NOT NULL DEFAULT 0)")

    def _connect(self) -> sqlite3.Connection:
        return store_db.connect(self.path, timeout=10)

    def get(self, key: str, now: Optional[float] = None):
        """(found, body): body is "" for a remembered no-match."""
        now = time.time() if now is None else now
        with closing(self._connect()) as db:
            row = db.execute("SELECT at, body FROM answers WHERE key = ?",
                             (key,)).fetchone()
        if row is None or now - float(row[0]) > CACHE_SECONDS:
            return False, ""
        return True, row[1] or ""

    def put(self, key: str, body: str, now: Optional[float] = None) -> None:
        with self._lock, closing(self._connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO answers (key, at, body) "
                       "VALUES (?, ?, ?)",
                       (key, time.time() if now is None else now, body))

    def count(self, month: str) -> int:
        with closing(self._connect()) as db:
            row = db.execute("SELECT requests FROM enterprise WHERE month = ?",
                             (month,)).fetchone()
        return int(row[0]) if row else 0

    def spend(self, month: str) -> int:
        """Count one Enterprise request; returns the month's total."""
        with self._lock, closing(self._connect()) as db, db:
            db.execute("INSERT INTO enterprise (month, requests) VALUES (?, 1) "
                       "ON CONFLICT(month) DO UPDATE SET "
                       "requests = enterprise.requests + 1", (month,))
        return self.count(month)


_STORE: list = [None]


def store() -> ReferenceStore:
    if _STORE[0] is None:
        _STORE[0] = ReferenceStore()
    return _STORE[0]


def reset_store() -> None:
    """Forget the open store, the tokens and any backoff (tests)."""
    _STORE[0] = None
    _TOKENS.clear()
    with _BACKOFF_LOCK:
        _BACKOFF.clear()


def exists() -> bool:
    return store_db.has_tables(data_path("WIKIMEDIA_DB", "wikimedia.db"))


def _month(now: Optional[float] = None) -> str:
    return time.strftime("%Y-%m", time.gmtime(time.time() if now is None else now))


# --------------------------------------------------------------------------
# Names
# --------------------------------------------------------------------------
def normalise(text: str) -> str:
    """Lower case, no accents, no punctuation, single spaces."""
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


_STOP = frozenset("""a an and the of in on at for to with by from is are was
    were be what who how why when where which about latest news new his her
    their its this that does did do""".split())


def _words(text: str) -> set:
    return {w for w in normalise(text).split() if len(w) > 2 and w not in _STOP}


def pick(hits: list, name: str, context: str = "") -> tuple:
    """(qid or "", ambiguous) from `wbsearchentities`' hits.

    Exact matches only - the label, or the alias the search matched on. More
    than one is told apart by the question's own words against each one's
    description; anything but exactly one survivor is no answer.
    """
    target = normalise(name)
    if not target:
        return "", False
    exact: dict = {}
    for hit in hits or []:
        if not isinstance(hit, dict) or not hit.get("id"):
            continue
        label = normalise(hit.get("label", ""))
        matched = normalise((hit.get("match") or {}).get("text", ""))
        if target in (label, matched):
            exact.setdefault(hit["id"], hit)
    if len(exact) == 1:
        return next(iter(exact)), False
    if not exact:
        return "", False
    wanted = _words(context) - _words(name)
    fitting = [qid for qid, hit in exact.items()
               if wanted & _words(hit.get("description", ""))]
    return (fitting[0] if len(fitting) == 1 else ""), True


# --------------------------------------------------------------------------
# Wikidata statements
# --------------------------------------------------------------------------
def _statements(entity: dict, prop: str, best: bool = True) -> list:
    """`prop`'s statements, deprecated ones dropped; with `best`, only the
    preferred ones when there are any."""
    claims = [c for c in ((entity.get("claims") or {}).get(prop) or [])
              if isinstance(c, dict) and c.get("rank") != "deprecated"]
    if best:
        preferred = [c for c in claims if c.get("rank") == "preferred"]
        if preferred:
            return preferred
    return claims


def _value(snak: dict):
    if not isinstance(snak, dict) or snak.get("snaktype") != "value":
        return None
    return (snak.get("datavalue") or {}).get("value")


def _item(snak: dict) -> str:
    value = _value(snak)
    return str(value.get("id", "")) if isinstance(value, dict) else ""


def _time(snak: dict) -> Optional[dict]:
    """A Wikidata time as {"year", "month", "day", "precision"}, or None.
    Precision 11 is a day, 10 a month, 9 a year; anything coarser is None."""
    value = _value(snak)
    if not isinstance(value, dict):
        return None
    match = re.match(r"([+-])(\d+)-(\d\d)-(\d\d)", str(value.get("time", "")))
    precision = int(value.get("precision", 0) or 0)
    if not match or precision < 9:
        return None
    year = int(match.group(2)) * (-1 if match.group(1) == "-" else 1)
    return {"year": year, "month": int(match.group(3)),
            "day": int(match.group(4)), "precision": min(precision, 11)}


def _qualifier_time(claim: dict, prop: str) -> Optional[dict]:
    for snak in ((claim.get("qualifiers") or {}).get(prop) or []):
        found = _time(snak)
        if found:
            return found
    return None


def _ended(claim: dict) -> bool:
    """Whether a statement has an end time - any, known or not."""
    return bool((claim.get("qualifiers") or {}).get(P_END))


def raw_facts(entity: dict) -> tuple:
    """(facts, item ids whose labels are needed) - the cacheable material."""
    facts: dict = {"human": any(_item(c.get("mainsnak")) == HUMAN
                                for c in _statements(entity, P_INSTANCE, False))}
    ids: list = []

    def items(prop: str, limit: int = 3) -> list:
        out = []
        for claim in _statements(entity, prop):
            qid = _item(claim.get("mainsnak"))
            if qid and qid not in out:
                out.append(qid)
        out = out[:limit]
        ids.extend(out)
        return out

    def current(prop: str, limit: int = 3) -> list:
        out = []
        for claim in _statements(entity, prop, best=False):
            qid = _item(claim.get("mainsnak"))
            if not qid or _ended(claim) or any(r["id"] == qid for r in out):
                continue
            out.append({"id": qid, "since": _qualifier_time(claim, P_START)})
        out = out[:limit]
        ids.extend(r["id"] for r in out)
        return out

    def first_time(prop: str) -> Optional[dict]:
        for claim in _statements(entity, prop):
            found = _time(claim.get("mainsnak"))
            if found:
                return found
        return None

    if facts["human"]:
        facts["born"] = first_time(P_BORN)
        facts["died"] = first_time(P_DIED)
        facts["citizenship"] = items(P_CITIZEN, 2)
        facts["occupations"] = items(P_OCCUPATION, 3)
        facts["positions"] = current(P_POSITION)
        facts["teams"] = current(P_TEAM, 2)
        facts["employers"] = current(P_EMPLOYER, 2)
    else:
        facts["inception"] = first_time(P_INCEPTION)
        facts["headquarters"] = items(P_HQ, 1)
        facts["chief_executive"] = current(P_CEO, 1)
        facts["founders"] = items(P_FOUNDER, 3)
    return facts, list(dict.fromkeys(ids))


_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")


def say_date(when: Optional[dict]) -> str:
    if not when:
        return ""
    year = when["year"]
    year_text = f"{-year} BC" if year < 0 else str(year)
    if when["precision"] >= 11 and 1 <= when["month"] <= 12:
        return f"{_MONTHS[when['month'] - 1]} {when['day']}, {year_text}"
    if when["precision"] == 10 and 1 <= when["month"] <= 12:
        return f"{_MONTHS[when['month'] - 1]} {year_text}"
    return year_text


def age(born: Optional[dict], on: date) -> Optional[int]:
    """Whole years from `born` to `on`; None unless `born` is to the day."""
    if not born or born["precision"] < 11 or born["year"] < 1:
        return None
    try:
        birthday = date(born["year"], born["month"], born["day"])
    except ValueError:
        return None
    years = on.year - birthday.year - (
        (on.month, on.day) < (birthday.month, birthday.day))
    return years if years >= 0 else None


def _join(words: list) -> str:
    words = [w for w in words if w]
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def sentences(name: str, facts: dict, labels: dict,
              today: Optional[date] = None) -> list:
    """The fact lines the writer reads. Every one says "Wikidata lists"."""
    if today is None:
        import listener_clock

        today = listener_clock.now().date()
    out: list = []

    def named(ids: list) -> list:
        return [labels[i] for i in ids if labels.get(i)]

    def held(rows: list) -> list:
        parts = []
        for row in rows or []:
            label = labels.get(row["id"])
            if not label:
                continue
            since = say_date(row.get("since"))
            parts.append(f"{label} (since {since})" if since else label)
        return parts

    if facts.get("human"):
        born, died = facts.get("born"), facts.get("died")
        if born and died:
            line = (f"Wikidata lists {name} as born {say_date(born)} and as "
                    f"having died {say_date(died)}")
            if died["precision"] >= 11:
                at = age(born, date(died["year"], died["month"], died["day"])) \
                    if died["year"] > 0 else None
                if at is not None:
                    line += f", aged {at}"
            out.append(line + ".")
        elif born:
            years = age(born, today)
            line = f"Wikidata lists {name} as born {say_date(born)}"
            if years is not None:
                line += (f", which makes them {years} as of "
                         f"{_MONTHS[today.month - 1]} {today.day}, {today.year}")
            out.append(line + ".")
        elif died:
            out.append(f"Wikidata lists {name} as having died {say_date(died)}.")
        if named(facts.get("citizenship", [])):
            out.append(f"Wikidata lists {name}'s citizenship as "
                       f"{_join(named(facts['citizenship']))}.")
        if named(facts.get("occupations", [])):
            out.append(f"Wikidata lists {name}'s occupation as "
                       f"{_join(named(facts['occupations']))}.")
        current_word = "" if died else "currently "
        for key, verb in (("positions", "holding the position"),
                          ("teams", "a member of"),
                          ("employers", "employed by")):
            parts = held(facts.get(key, []))
            if not parts:
                continue
            if key == "positions":
                out.append(f"Wikidata lists {name} as {current_word}{verb} of "
                           f"{_join(parts)}, with no end date recorded.")
            else:
                out.append(f"Wikidata lists {name} as {current_word}{verb} "
                           f"{_join(parts)}, with no end date recorded.")
    else:
        if facts.get("inception"):
            out.append(f"Wikidata lists {name} as founded "
                       f"{say_date(facts['inception'])}.")
        if named(facts.get("founders", [])):
            out.append(f"Wikidata lists {name}'s founders as "
                       f"{_join(named(facts['founders']))}.")
        if named(facts.get("headquarters", [])):
            out.append(f"Wikidata lists {name}'s headquarters as "
                       f"{_join(named(facts['headquarters']))}.")
        parts = held(facts.get("chief_executive", []))
        if parts:
            out.append(f"Wikidata lists {name}'s chief executive as "
                       f"{_join(parts)}, with no end date recorded.")
    return out


# --------------------------------------------------------------------------
# Summaries: Enterprise first, the public REST summary second
# --------------------------------------------------------------------------
_TOKENS: dict = {}
_TOKEN_LOCK = threading.Lock()


def enterprise_configured() -> bool:
    return bool((getattr(settings, "wikimedia_enterprise_username", "") or "").strip()
                and (getattr(settings, "wikimedia_enterprise_password", "") or ""))


def enterprise_room(now: Optional[float] = None) -> bool:
    """Whether this month's Enterprise count is under the cap."""
    cap = int(getattr(settings, "wikimedia_monthly_requests", 45000))
    try:
        return store().count(_month(now)) < cap
    except Exception:  # noqa: BLE001 - an unreadable count spends nothing
        log.warning("wikimedia: the Enterprise count could not be read; "
                    "using the public summary", exc_info=True)
        return False


def _enterprise_post(url: str, body: dict, token: str = "") -> tuple:
    """One counted Enterprise request. Refused when the month is spent."""
    if not enterprise_room():
        raise WikimediaError("the month's Enterprise requests are spent")
    store().spend(_month())
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return _send("POST", url, body=body, headers=headers)


def _token(force: bool = False) -> str:
    """An Enterprise access token: kept, refreshed, or logged in for."""
    username = (settings.wikimedia_enterprise_username or "").strip()
    password = settings.wikimedia_enterprise_password or ""
    now = time.time()
    with _TOKEN_LOCK:
        if not force and _TOKENS.get("access") and _TOKENS.get("access_until", 0) > now:
            return _TOKENS["access"]
        refresh = _TOKENS.get("refresh") if _TOKENS.get("refresh_until", 0) > now else ""
    if refresh:
        status, parsed = _enterprise_post(
            ENTERPRISE_REFRESH, {"username": username, "refresh_token": refresh})
        if status == 200 and isinstance(parsed, dict) and parsed.get("access_token"):
            with _TOKEN_LOCK:
                _TOKENS["access"] = parsed["access_token"]
                _TOKENS["access_until"] = now + ACCESS_SECONDS
            return parsed["access_token"]
    status, parsed = _enterprise_post(
        ENTERPRISE_LOGIN, {"username": username, "password": password})
    if status != 200 or not isinstance(parsed, dict) or not parsed.get("access_token"):
        raise WikimediaError(f"Enterprise login answered HTTP {status}")
    with _TOKEN_LOCK:
        _TOKENS["access"] = parsed["access_token"]
        _TOKENS["access_until"] = now + ACCESS_SECONDS
        if parsed.get("refresh_token"):
            _TOKENS["refresh"] = parsed["refresh_token"]
            _TOKENS["refresh_until"] = now + REFRESH_SECONDS
    return parsed["access_token"]


def _parse_time(text: str) -> Optional[datetime]:
    try:
        when = datetime.fromisoformat(str(text or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def clip(text: str, limit: int = SUMMARY_CHARS) -> str:
    """`text` cut at a sentence end at or before `limit` characters."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit + 1]
    ends = [m.end() for m in re.finditer(r"[.!?](?=\s|$)", cut)
            if m.end() <= limit]
    return cut[:ends[-1]] if ends else text[:limit].rsplit(" ", 1)[0] + "..."


def parse_enterprise(reply: Any, qid: str) -> Optional[dict]:
    """The summary fields of a structured-contents reply, or None.

    A reply whose main entity is another item is the wrong article and is not
    used. Infoboxes are ignored on purpose (see the module docstring).
    """
    rows = reply if isinstance(reply, list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        entity = str((row.get("main_entity") or {}).get("identifier", "") or "")
        if qid and entity and entity != qid:
            continue
        summary = clip(row.get("abstract", ""))
        if not summary:
            continue
        return {"title": str(row.get("name", "") or ""),
                "url": str(row.get("url", "") or ""),
                "description": str(row.get("description", "") or ""),
                "summary": summary,
                "revised": row.get("date_modified", "") or ""}
    return None


def parse_public(reply: Any, qid: str) -> Optional[dict]:
    """The REST summary's fields, or None for a disambiguation page or a
    page about some other item."""
    if not isinstance(reply, dict) or reply.get("type") == "disambiguation":
        return None
    item = str(reply.get("wikibase_item", "") or "")
    if qid and item and item != qid:
        return None
    summary = clip(reply.get("extract", ""))
    if not summary:
        return None
    return {"title": str(reply.get("title", "") or ""),
            "url": str(((reply.get("content_urls") or {}).get("desktop") or {})
                       .get("page", "") or ""),
            "description": str(reply.get("description", "") or ""),
            "summary": summary,
            "revised": reply.get("timestamp", "") or ""}


def _from_enterprise(title: str, qid: str) -> Optional[dict]:
    body = {"filters": [{"field": "is_part_of.identifier", "value": "enwiki"}],
            "limit": 1}
    url = ENTERPRISE_ARTICLE + quote(title.replace(" ", "_"), safe="")
    status, parsed = _enterprise_post(url, body, _token())
    if status == 401:
        # An access token that lapsed early: log in again, once.
        status, parsed = _enterprise_post(url, body, _token(force=True))
    if status != 200:
        raise WikimediaError(f"Enterprise answered HTTP {status}")
    return parse_enterprise(parsed, qid)


def _from_public(title: str, qid: str) -> Optional[dict]:
    url = SUMMARY_URL + quote(title.replace(" ", "_"), safe="")
    status, parsed = _send("GET", url)
    if status != 200:
        raise WikimediaError(f"the Wikipedia summary answered HTTP {status}")
    return parse_public(parsed, qid)


def summary_for(title: str, qid: str) -> tuple:
    """(fields or None, via). Enterprise while it can serve, else public."""
    if enterprise_configured() and enterprise_room():
        try:
            found = _from_enterprise(title, qid)
            if found:
                return found, "enterprise"
        except Exception as exc:  # noqa: BLE001 - the public route is next
            log.warning("wikimedia: Enterprise could not serve %r (%s); using "
                        "the public summary", title, exc)
    return _from_public(title, qid), "public"


# --------------------------------------------------------------------------
# The look
# --------------------------------------------------------------------------
def _labels(ids: list) -> dict:
    labels: dict = {}
    for start in range(0, len(ids), MAX_IDS):
        chunk = ids[start:start + MAX_IDS]
        data = _wikidata({"action": "wbgetentities", "ids": "|".join(chunk),
                          "props": "labels", "languages": "en"})
        for qid, entity in (data.get("entities") or {}).items():
            label = ((entity.get("labels") or {}).get("en") or {}).get("value")
            if label:
                labels[qid] = label
    return labels


def _fetch(name: str, context: str) -> tuple:
    """(body or None, ambiguous): every request a cache miss makes."""
    found = _wikidata({"action": "wbsearchentities", "search": name,
                       "language": "en", "type": "item", "limit": "5"})
    qid, ambiguous = pick(found.get("search") or [], name, context)
    if not qid:
        return None, ambiguous
    data = _wikidata({"action": "wbgetentities", "ids": qid,
                      "props": "labels|descriptions|claims|sitelinks",
                      "languages": "en", "sitefilter": "enwiki"})
    entity = (data.get("entities") or {}).get(qid) or {}
    title = ((entity.get("sitelinks") or {}).get("enwiki") or {}).get("title", "")
    if not title:
        return None, ambiguous
    label = ((entity.get("labels") or {}).get("en") or {}).get("value") or title
    facts, ids = raw_facts(entity)
    labels = _labels(ids) if ids else {}
    fields, via = summary_for(title, qid)
    if not fields:
        return None, ambiguous
    return {"qid": qid, "label": label, "facts": facts, "labels": labels,
            "via": via, **fields,
            "title": fields.get("title") or title,
            "url": fields.get("url") or ARTICLE_URL + quote(title.replace(" ", "_"))
            }, ambiguous


def _reference(body: dict, today: Optional[date] = None) -> Reference:
    return Reference(
        title=body.get("title", ""), url=body.get("url", ""),
        description=body.get("description", ""), summary=body.get("summary", ""),
        facts=sentences(body.get("label") or body.get("title", ""),
                        body.get("facts") or {}, body.get("labels") or {}, today),
        revised=_parse_time(body.get("revised", "")),
        qid=body.get("qid", ""), via=body.get("via", ""))


def _context_key(name: str, context: str) -> str:
    return normalise(name) + "|" + " ".join(sorted(_words(context) - _words(name)))


def lookup(name: str, context: str = "",
           today: Optional[date] = None) -> Optional[Reference]:
    """The entry for `name`, or None. Never raises.

    `context` is the question's own words, used only to tell two exact
    matches apart. A name's answer is kept for a day; an answer that needed
    the context to decide is kept under the name and those words.
    """
    name = " ".join(str(name or "").split())
    if not name or not enabled():
        return None
    try:
        held = store()
        for key in (normalise(name), _context_key(name, context)):
            found, body = held.get(key)
            if found:
                return _reference(json.loads(body), today) if body else None
    except Exception:  # noqa: BLE001 - a broken cache is a miss
        log.warning("wikimedia: the cache could not be read", exc_info=True)
        held = None
    try:
        body, ambiguous = _fetch(name, context)
    except Exception as exc:  # noqa: BLE001 - a reference is only ever extra
        log.warning("wikimedia: no reference for %r (%s)", name, exc)
        return None
    if held is not None:
        try:
            key = _context_key(name, context) if ambiguous else normalise(name)
            held.put(key, json.dumps(body) if body else "")
        except Exception:  # noqa: BLE001
            log.warning("wikimedia: the answer could not be kept", exc_info=True)
    return _reference(body, today) if body else None


def report() -> dict:
    """For `/api/health`. Never creates the store, never sends anything."""
    cap = int(getattr(settings, "wikimedia_monthly_requests", 45000))
    count = 0
    try:
        if exists():
            count = store().count(_month())
    except Exception:  # noqa: BLE001 - a report is never load-bearing
        count = -1
    configured = enterprise_configured()
    if not enabled():
        route = "off"
    elif configured and 0 <= count < cap:
        route = "enterprise"
    else:
        route = "public"
    with _BACKOFF_LOCK:
        backing = {p: round(u - time.monotonic())
                   for p, u in _BACKOFF.items() if u > time.monotonic()}
    return {"enabled": enabled(), "route": route,
            "enterprise_configured": configured,
            "month": _month(), "enterprise_requests_this_month": count,
            "monthly_cap": cap, "backing_off_seconds": backing,
            "grace_seconds": float(getattr(settings, "wikipedia_grace_seconds", 0.5))}
