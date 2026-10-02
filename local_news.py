"""Local news from the outlets that actually cover a town (§194).

**The hole this fills.** A question about a small town - "what's going on in
San Anselmo" - went to Exa and GDELT, national indexes that weight a town's
two or three outlets at nothing. They came back with Marin County or San
Francisco stories, nothing checked that those named the town, and the writer
bridged the gap from memory. That is the invention §88 and §109 forbid,
reached by a side door.

**The order, at the owner's direction** (`research_local`):

1. **Town items**: stories already collected from the town's own outlets, or
   from any outlet in its state that names it. A local database read - faster
   than an Exa call, and it follows [latency-start-earlier]: the collecting
   happens before anybody taps.
2. **County items**: the county's outlets, or stories naming the county. The
   writer is told plainly that this is county-level evidence, and the episode
   opens with a sentence composed in code that says the town itself had
   nothing (`gap_line`) - so county news is never presented as the town's.
3. **Exa, limited to the town's and county's known outlets** - or, for a
   place with no known outlet, an Exa search that must name the place.
   Avoided whenever 1 or 2 answered.
4. **Nothing**: the gap sentence and the weather there, said as exactly that.

**No GDELT on this path**, at the owner's direction. Everything a local
episode is written from names the place it is about: an item that does not
is not evidence about that place, whichever rung found it.

**The collector runs itself** (`poll_due`, `run_forever`): it reads each
outlet's feed with a conditional request, learns how often the outlet
publishes, and moves a feed through healthy, stale, broken (when it finds the
feed again from the homepage), blocked and retired on its own. It polls only
outlets for places somebody has asked about recently, so the work scales with
demand. It reads `robots.txt` and keeps a do-not-use list for publishers who
object. Outlets come from a seed file, `tools/local_outlets.py` (Wikidata) and
- automatically - from any site the Exa rung finds writing about the town.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from config import settings
from paths import PROJECT_ROOT, data_path

log = logging.getLogger("fam.local_news")

#: Outlet states. Each is a different thing to do next, which is why it is a
#: word and not a boolean.
NEW = "new"              # never polled
HEALTHY = "healthy"      # answered, and publishing on its usual rhythm
STALE = "stale"          # answered, but nothing new for far longer than usual
BROKEN = "broken"        # failing; the feed is looked for again from the homepage
NO_FEED = "no_feed"      # the homepage offers no feed we can find
BLOCKED = "blocked"      # the site refuses us (401/403/429, a bot challenge)
DISALLOWED = "disallowed"  # robots.txt says no
EXCLUDED = "excluded"    # the publisher asked; never polled again
RETIRED = "retired"      # broken or feedless for a month; no longer polled
STATES = (NEW, HEALTHY, STALE, BROKEN, NO_FEED, BLOCKED, DISALLOWED,
          EXCLUDED, RETIRED)
#: States that are polled at all.
POLLED = (NEW, HEALTHY, STALE, BROKEN, NO_FEED, BLOCKED)

TOWN = "town"
COUNTY = "county"
#: An outlet found by the Exa rung rather than filed by hand: a regional
#: site, usually, so its stories count for a place only when they name it.
MENTION = "mention"

#: The seed list shipped with the code. Small on purpose: Wikidata and the
#: Exa rung grow it; this only makes the first town work on a fresh disk.
SEED_FILE = PROJECT_ROOT / "local_outlets.json"

#: Paths a feed usually lives at, tried when a homepage does not say.
FEED_PATHS = ("/feed/", "/rss", "/feed", "/rss.xml", "/feed.xml",
              "/index.xml", "/atom.xml")

#: A feed or page bigger than this is not read.
MAX_BYTES = 3_000_000
#: How long one feed or page may take.
FETCH_TIMEOUT = 8.0
#: Articles fetched per outlet per poll, when a feed carries only teasers.
ARTICLES_PER_POLL = 4
#: An item with fewer words than this is a teaser and its article is fetched.
TEASER_WORDS = 60
#: An extracted article with fewer words than this was paywalled or blocked.
ARTICLE_MIN_WORDS = 80
#: How long somebody's question keeps a place's outlets polled.
DEMAND_SECONDS = 30 * 86400
#: How long a feed may sit broken or feedless before it is retired.
RETIRE_AFTER = 30 * 86400
#: How many items the writer is given, per rung.
PACKET_ITEMS = 4
#: Words of an item's text the writer is given.
TOWN_WORDS = 170
COUNTY_WORDS = 110

_PAYWALL = re.compile(
    r"\b(subscribe to (continue|read)|subscribers only|already a subscriber|"
    r"to continue reading|log in to continue|this content is for subscribers)\b",
    re.I)
_CHALLENGE = re.compile(r"(cf-chl|challenge-platform|just a moment\.\.\.|"
                        r"attention required! \| cloudflare)", re.I)


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------
@dataclass
class Outlet:
    id: int
    name: str
    homepage: str
    feed_url: str = ""
    town: str = ""
    county: str = ""
    region: str = ""
    country: str = ""
    scope: str = TOWN
    source: str = ""
    state: str = NEW
    etag: str = ""
    last_modified: str = ""
    last_poll: float = 0.0
    next_poll: float = 0.0
    last_item_at: float = 0.0
    typical_gap: float = 0.0
    failures: int = 0
    full_text: Optional[bool] = None
    detail: str = ""
    added_at: float = 0.0
    trouble_since: float = 0.0

    @property
    def host(self) -> str:
        return host_of(self.homepage or self.feed_url)


@dataclass
class Item:
    """One collected story. Shaped like a search result on purpose, so the
    research module's date and provenance helpers read it unchanged."""

    url: str
    title: str
    summary: str = ""
    body: str = ""
    published: float = 0.0
    outlet: str = ""
    outlet_scope: str = TOWN
    outlet_town: str = ""
    outlet_county: str = ""

    @property
    def published_date(self) -> str:
        if not self.published:
            return ""
        return datetime.fromtimestamp(self.published, timezone.utc).isoformat()

    @property
    def text(self) -> str:
        return self.body or self.summary

    @property
    def highlights(self) -> list:
        return [self.text] if self.text else []


def host_of(url: str) -> str:
    host = (urlparse(url or "").hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def words(text: str) -> int:
    return len((text or "").split())


def clip(text: str, limit: int) -> str:
    parts = (text or "").split()
    if len(parts) <= limit:
        return " ".join(parts)
    return " ".join(parts[:limit]) + " ..."


def names(text: str, name: str) -> bool:
    """Whether `text` names `name` as a whole phrase, any case."""
    name = " ".join((name or "").split())
    if len(name) < 3:
        return False
    pattern = r"(?<![\w-])" + r"\s+".join(map(re.escape, name.split())) + r"(?![\w-])"
    return re.search(pattern, text or "", re.I) is not None


# --------------------------------------------------------------------------
# The store
# --------------------------------------------------------------------------
_OUTLET_COLUMNS = ("id", "name", "homepage", "feed_url", "town", "county",
                   "region", "country", "scope", "source", "state", "etag",
                   "last_modified", "last_poll", "next_poll", "last_item_at",
                   "typical_gap", "failures", "full_text", "detail",
                   "added_at", "trouble_since")


class LocalNewsStore:
    """Outlets, their items, and which places were asked about."""

    def __init__(self, path: str | None = None) -> None:
        self.path = data_path("LOCAL_NEWS_DB", "local_news.db", path)
        self._lock = threading.Lock()
        with closing(self._connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS outlets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL, homepage TEXT NOT NULL UNIQUE,
                    feed_url TEXT DEFAULT '', town TEXT DEFAULT '',
                    county TEXT DEFAULT '', region TEXT DEFAULT '',
                    country TEXT DEFAULT '', scope TEXT DEFAULT 'town',
                    source TEXT DEFAULT '', state TEXT DEFAULT 'new',
                    etag TEXT DEFAULT '', last_modified TEXT DEFAULT '',
                    last_poll REAL DEFAULT 0, next_poll REAL DEFAULT 0,
                    last_item_at REAL DEFAULT 0, typical_gap REAL DEFAULT 0,
                    failures INTEGER DEFAULT 0, full_text INTEGER,
                    detail TEXT DEFAULT '', added_at REAL DEFAULT 0,
                    trouble_since REAL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    outlet_id INTEGER NOT NULL, url TEXT NOT NULL UNIQUE,
                    title TEXT DEFAULT '', summary TEXT DEFAULT '',
                    body TEXT DEFAULT '', published REAL DEFAULT 0,
                    fetched REAL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS items_by_outlet
                    ON items (outlet_id, published);
                CREATE TABLE IF NOT EXISTS demand (
                    key TEXT PRIMARY KEY, town TEXT, county TEXT,
                    region TEXT, asked_at REAL);
                CREATE TABLE IF NOT EXISTS excluded (
                    host TEXT PRIMARY KEY, reason TEXT, at REAL);
            """)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    # Outlets -------------------------------------------------------------
    def add_outlet(self, name: str, homepage: str, *, town: str = "",
                   county: str = "", region: str = "", country: str = "",
                   scope: str = TOWN, source: str = "", feed_url: str = ""
                   ) -> Optional[int]:
        """Add an outlet, or return the existing one's id. Never duplicates."""
        homepage = (homepage or "").strip()
        if not homepage.startswith(("http://", "https://")):
            return None
        if self.is_excluded(host_of(homepage)):
            return None
        with self._lock, closing(self._connect()) as db, db:
            row = db.execute("SELECT id FROM outlets WHERE homepage = ?",
                             (homepage,)).fetchone()
            if row:
                return int(row[0])
            cur = db.execute(
                "INSERT INTO outlets (name, homepage, feed_url, town, county,"
                " region, country, scope, source, added_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (name.strip() or host_of(homepage), homepage, feed_url, town,
                 county, region, country.upper(),
                 scope if scope in (TOWN, COUNTY, MENTION) else TOWN, source,
                 time.time()))
            return int(cur.lastrowid)

    def outlets(self, where: str = "", args: tuple = ()) -> list:
        sql = f"SELECT {', '.join(_OUTLET_COLUMNS)} FROM outlets"
        if where:
            sql += " WHERE " + where
        with closing(self._connect()) as db:
            rows = db.execute(sql, args).fetchall()
        out = []
        defaults = Outlet.__dataclass_fields__
        for row in rows:
            data = dict(zip(_OUTLET_COLUMNS, row))
            for key, value in data.items():
                if value is None and key != "full_text":
                    data[key] = defaults[key].default
            if data["full_text"] is not None:
                data["full_text"] = bool(data["full_text"])
            out.append(Outlet(**data))
        return out

    def outlet(self, outlet_id: int) -> Optional[Outlet]:
        found = self.outlets("id = ?", (outlet_id,))
        return found[0] if found else None

    def save(self, outlet: Outlet) -> None:
        values = [getattr(outlet, c) for c in _OUTLET_COLUMNS[1:]]
        values[_OUTLET_COLUMNS.index("full_text") - 1] = (
            None if outlet.full_text is None else int(outlet.full_text))
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                "UPDATE outlets SET "
                + ", ".join(f"{c} = ?" for c in _OUTLET_COLUMNS[1:])
                + " WHERE id = ?", (*values, outlet.id))

    def for_place(self, town: str, county: str, region: str) -> list:
        """Outlets covering this town or its county."""
        clauses, args = ["lower(town) = lower(?)"], [town]
        if county:
            clauses.append("lower(county) = lower(?)")
            args.append(_bare_county(county))
            clauses.append("lower(county) = lower(?)")
            args.append(county)
        found = self.outlets("(" + " OR ".join(clauses) + ")", tuple(args))
        if region:
            found = [o for o in found
                     if not o.region or o.region.lower() == region.lower()]
        return found

    # Items ---------------------------------------------------------------
    def add_items(self, outlet_id: int, items: list) -> int:
        """Keep new items; update an edited one in place. Returns how many new."""
        added = 0
        now = time.time()
        with self._lock, closing(self._connect()) as db, db:
            for it in items:
                if not it.url or not it.title:
                    continue
                row = db.execute("SELECT id, body FROM items WHERE url = ?",
                                 (it.url,)).fetchone()
                if row:
                    db.execute(
                        "UPDATE items SET title = ?, summary = ?,"
                        " body = CASE WHEN length(?) > length(body) THEN ?"
                        " ELSE body END WHERE id = ?",
                        (it.title, it.summary, it.body, it.body, row[0]))
                    continue
                if self._duplicate_title(db, outlet_id, it.title):
                    continue
                db.execute(
                    "INSERT INTO items (outlet_id, url, title, summary, body,"
                    " published, fetched) VALUES (?,?,?,?,?,?,?)",
                    (outlet_id, it.url, it.title, it.summary, it.body,
                     it.published or now, now))
                added += 1
        return added

    @staticmethod
    def _duplicate_title(db, outlet_id: int, title: str) -> bool:
        key = " ".join(re.findall(r"[a-z0-9]+", title.lower()))
        rows = db.execute(
            "SELECT title FROM items WHERE outlet_id = ? ORDER BY id DESC"
            " LIMIT 200", (outlet_id,)).fetchall()
        return any(" ".join(re.findall(r"[a-z0-9]+", r[0].lower())) == key
                   for r in rows)

    def set_body(self, url: str, body: str) -> None:
        with self._lock, closing(self._connect()) as db, db:
            db.execute("UPDATE items SET body = ? WHERE url = ?", (body, url))

    def items(self, since: float, region: str = "") -> list:
        """Every item published since `since`, newest first, with its outlet."""
        sql = ("SELECT i.url, i.title, i.summary, i.body, i.published,"
               " o.name, o.scope, o.town, o.county, o.region, o.state"
               " FROM items i JOIN outlets o ON o.id = i.outlet_id"
               " WHERE i.published >= ? AND o.state != ?")
        args: list = [since, EXCLUDED]
        if region:
            sql += " AND (o.region = '' OR lower(o.region) = lower(?))"
            args.append(region)
        sql += " ORDER BY i.published DESC LIMIT 2000"
        with closing(self._connect()) as db:
            rows = db.execute(sql, args).fetchall()
        return [Item(url=r[0], title=r[1], summary=r[2] or "", body=r[3] or "",
                     published=r[4] or 0.0, outlet=r[5], outlet_scope=r[6],
                     outlet_town=r[7] or "", outlet_county=r[8] or "")
                for r in rows]

    def latest_teasers(self, outlet_id: int, limit: int) -> list:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT url, title, summary FROM items WHERE outlet_id = ?"
                " AND body = '' ORDER BY published DESC LIMIT ?",
                (outlet_id, limit)).fetchall()
        return [Item(url=r[0], title=r[1], summary=r[2] or "") for r in rows
                if words(r[2] or "") < TEASER_WORDS]

    def item_times(self, outlet_id: int, limit: int = 20) -> list:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT published FROM items WHERE outlet_id = ?"
                " ORDER BY published DESC LIMIT ?", (outlet_id, limit)).fetchall()
        return [r[0] for r in rows if r[0]]

    # Demand --------------------------------------------------------------
    def asked(self, town: str, county: str, region: str) -> None:
        key = "|".join(x.lower() for x in (town, county, region))
        with self._lock, closing(self._connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO demand VALUES (?,?,?,?,?)",
                       (key, town, county, region, time.time()))

    def demanded(self, since: float) -> list:
        with closing(self._connect()) as db:
            return db.execute(
                "SELECT town, county, region FROM demand WHERE asked_at >= ?",
                (since,)).fetchall()

    # Exclusions ----------------------------------------------------------
    def exclude(self, host: str, reason: str) -> None:
        """Never read this publisher again (they asked, or we decided)."""
        host = host_of(host) if "//" in host else host.lower().removeprefix("www.")
        with self._lock, closing(self._connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO excluded VALUES (?,?,?)",
                       (host, reason, time.time()))
            db.execute("UPDATE outlets SET state = ?, detail = ?"
                       " WHERE homepage LIKE ? OR homepage LIKE ?",
                       (EXCLUDED, f"excluded: {reason}",
                        f"%://{host}%", f"%://www.{host}%"))

    def is_excluded(self, host: str) -> bool:
        with closing(self._connect()) as db:
            return db.execute("SELECT 1 FROM excluded WHERE host = ?",
                              (host,)).fetchone() is not None

    def counts(self) -> dict:
        with closing(self._connect()) as db:
            states = dict(db.execute(
                "SELECT state, count(*) FROM outlets GROUP BY state").fetchall())
            items = db.execute("SELECT count(*) FROM items").fetchone()[0]
            places = db.execute("SELECT count(*) FROM demand").fetchone()[0]
        return {"outlets": states, "items": int(items), "places_asked": int(places)}


_STORE: list = [None]
_SEEDED: list = [False]


def store() -> LocalNewsStore:
    if _STORE[0] is None:
        _STORE[0] = LocalNewsStore()
        if not _SEEDED[0]:
            _SEEDED[0] = True
            try:
                seed(_STORE[0])
            except Exception:  # noqa: BLE001 - a bad seed must not stop a lookup
                log.warning("local news: the seed list could not be read",
                            exc_info=True)
    return _STORE[0]


def exists() -> bool:
    """Whether the store has been created - read without creating it."""
    import os

    return os.path.exists(data_path("LOCAL_NEWS_DB", "local_news.db"))


def reset_store() -> None:
    _STORE[0] = None
    _SEEDED[0] = False
    _ROBOTS.clear()


def seed(target: LocalNewsStore, path: Path = SEED_FILE) -> int:
    """Add the shipped outlets. Idempotent: an outlet already held is left."""
    if not path.exists():
        return 0
    added = 0
    for entry in json.loads(path.read_text()).get("outlets", []):
        before = target.outlets("homepage = ?", (entry.get("homepage", ""),))
        target.add_outlet(
            entry.get("name", ""), entry.get("homepage", ""),
            town=entry.get("town", ""), county=entry.get("county", ""),
            region=entry.get("region", ""), country=entry.get("country", ""),
            scope=entry.get("scope", TOWN), source="seed",
            feed_url=entry.get("feed_url", ""))
        added += 0 if before else 1
    return added


def _bare_county(county: str) -> str:
    return re.sub(r"\s+(county|parish|borough)$", "", (county or "").strip(),
                  flags=re.I)


def registry_place(town: str, region: str = ""):
    """What the outlet registry knows about a town: its county and state."""
    import places

    try:
        found = [o for o in store().outlets("lower(town) = lower(?)", (town,))
                 if not region or not o.region
                 or o.region.lower() == region.lower()]
    except sqlite3.Error:
        return None
    for outlet in found:
        if outlet.county:
            return places.Place(name=outlet.town or town, county=outlet.county,
                                region=outlet.region or region,
                                country=outlet.country, source="registry")
    return None


# --------------------------------------------------------------------------
# Reading feeds and pages
# --------------------------------------------------------------------------
class _Text(HTMLParser):
    """Plain text from HTML: paragraphs only when asked, scripts never."""

    def __init__(self, paragraphs: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        self.paragraphs = paragraphs
        self.out: list = []
        self._skip = 0
        self._in_p = 0
        self._buf: list = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "nav", "footer", "aside",
                   "form", "header"):
            self._skip += 1
        elif tag == "p":
            self._in_p += 1
            self._buf = []

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "nav", "footer", "aside",
                   "form", "header"):
            self._skip = max(0, self._skip - 1)
        elif tag == "p" and self._in_p:
            self._in_p -= 1
            text = " ".join("".join(self._buf).split())
            if self.paragraphs and len(text) >= 40:
                self.out.append(text)

    def handle_data(self, data):
        if self._skip:
            return
        if self.paragraphs:
            if self._in_p:
                self._buf.append(data)
        else:
            self.out.append(data)


def html_text(html: str) -> str:
    parser = _Text()
    try:
        parser.feed(html or "")
    except Exception:  # noqa: BLE001 - malformed markup is common
        pass
    return " ".join(" ".join(parser.out).split())


def article_text(html: str) -> str:
    """The story on an article page: its paragraphs, chrome left out."""
    parser = _Text(paragraphs=True)
    try:
        parser.feed(html or "")
    except Exception:  # noqa: BLE001
        pass
    return clip("\n".join(parser.out), 1500)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _date(text: str) -> float:
    text = (text or "").strip()
    if not text:
        return 0.0
    try:
        return parsedate_to_datetime(text).timestamp()
    except (TypeError, ValueError, IndexError):
        pass
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return when.timestamp()
    except ValueError:
        return 0.0


def parse_feed(data: bytes, base: str = "") -> list:
    """RSS 2.0, RSS 1.0 or Atom -> Items. Raises ValueError on anything else.

    The standard library parser, guarded: a document that declares entities
    is refused outright (the expansion attacks need them), and size is capped
    before parsing.
    """
    if len(data) > MAX_BYTES:
        raise ValueError("feed is larger than the cap")
    head = data[:4096].lower()
    if b"<!entity" in data.lower() or (b"<!doctype" in head and b"[" in head):
        raise ValueError("feed declares entities; refused")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError(f"not XML: {exc}") from exc
    if _local(root.tag) not in ("rss", "feed", "rdf"):
        raise ValueError(f"not a feed: <{_local(root.tag)}>")
    out = []
    for node in root.iter():
        if _local(node.tag) not in ("item", "entry"):
            continue
        fields: dict = {}
        link = ""
        for child in node:
            name = _local(child.tag)
            if name == "link":
                href = child.get("href")
                rel = child.get("rel", "alternate")
                if href and rel == "alternate":
                    link = link or href
                elif (child.text or "").strip():
                    link = link or child.text.strip()
            elif name in ("encoded", "content"):
                fields["body"] = fields.get("body") or (child.text or "")
            elif name in ("description", "summary"):
                fields["summary"] = child.text or ""
            elif name == "title":
                fields["title"] = child.text or ""
            elif name in ("pubdate", "published", "date", "updated", "issued"):
                fields.setdefault("date", child.text or "")
            elif name == "guid" and not link and \
                    child.get("isPermaLink", "true") != "false":
                link = (child.text or "").strip()
        title = html_text(fields.get("title", ""))
        if not title or not link:
            continue
        summary = html_text(fields.get("summary", ""))
        body = html_text(fields.get("body", ""))
        if body and words(body) <= words(summary):
            body = ""
        out.append(Item(url=urljoin(base, link), title=title, summary=summary,
                        body=body, published=_date(fields.get("date", ""))))
    return out


class _FeedLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list = []

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        a = dict(attrs)
        if "alternate" in (a.get("rel") or "").lower() and (a.get("type") or "").lower() in (
                "application/rss+xml", "application/atom+xml",
                "application/rdf+xml"):
            if a.get("href"):
                self.found.append(a["href"])


def feed_links(html: str, base: str) -> list:
    """The feeds a page declares, in the order it declares them."""
    parser = _FeedLinks()
    try:
        parser.feed(html or "")
    except Exception:  # noqa: BLE001
        pass
    links = [urljoin(base, h) for h in parser.found]
    # A site's main feed before its comments feed.
    return sorted(dict.fromkeys(links), key=lambda u: "comment" in u.lower())


async def _get(client: httpx.AsyncClient, url: str, headers: dict | None = None):
    import places
    import provider_usage

    try:
        reply = await client.get(url, headers={
            "User-Agent": places.user_agent(), **(headers or {})})
    except Exception:
        provider_usage.record("local_feeds", ok=False)
        raise
    provider_usage.record("local_feeds",
                          ok=reply.is_success or reply.status_code == 304)
    return reply


_ROBOTS: dict = {}


async def allowed(client: httpx.AsyncClient, url: str) -> bool:
    """Whether robots.txt lets us read `url`. A missing robots.txt allows."""
    parts = urlparse(url)
    root = f"{parts.scheme}://{parts.netloc}"
    held = _ROBOTS.get(root)
    if held is None or held[0] < time.time():
        parser = RobotFileParser()
        try:
            reply = await _get(client, root + "/robots.txt")
            if reply.status_code in (401, 403):
                parser.disallow_all = True
            elif reply.is_success:
                parser.parse(reply.text.splitlines())
            else:
                parser.allow_all = True
        except Exception:  # noqa: BLE001 - unreachable robots.txt allows
            parser.allow_all = True
        held = (time.time() + 86400, parser)
        _ROBOTS[root] = held
    import places

    return held[1].can_fetch(places.user_agent(), url)


async def discover(client: httpx.AsyncClient, homepage: str) -> str:
    """Find an outlet's feed from its homepage. "" when there is none."""
    try:
        page = await _get(client, homepage)
        if page.is_success:
            for link in feed_links(page.text[:MAX_BYTES], str(page.url)):
                if await _is_feed(client, link):
                    return link
    except Exception as exc:  # noqa: BLE001
        log.info("local news: %s could not be read for a feed: %s", homepage, exc)
    for path in FEED_PATHS:
        candidate = urljoin(homepage, path)
        if await _is_feed(client, candidate):
            return candidate
    return ""


async def _is_feed(client: httpx.AsyncClient, url: str) -> bool:
    try:
        reply = await _get(client, url)
        return reply.is_success and bool(parse_feed(reply.content, url))
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------
# Polling, and the health of each feed
# --------------------------------------------------------------------------
def typical_gap(times: list) -> float:
    """The outlet's usual gap between items, from its own recent history."""
    times = sorted(t for t in times if t)
    if len(times) < 3:
        return 0.0
    gaps = sorted(b - a for a, b in zip(times, times[1:]) if b > a)
    return gaps[len(gaps) // 2] if gaps else 0.0


def next_poll(outlet: Outlet, now: float) -> float:
    """When to ask this outlet again. Busy outlets often, quiet ones rarely."""
    floor = max(5, int(settings.local_news_poll_minutes)) * 60.0
    if outlet.state == HEALTHY:
        gap = outlet.typical_gap or floor
        return now + min(max(floor, gap / 4), 6 * 3600.0)
    if outlet.state == STALE:
        return now + 6 * 3600.0
    if outlet.state in (BROKEN, NO_FEED):
        return now + min(3600.0 * (2 ** max(0, outlet.failures - 1)), 86400.0)
    if outlet.state == BLOCKED:
        return now + 86400.0
    return now + floor


def _stale(outlet: Outlet, now: float) -> bool:
    if not outlet.last_item_at:
        return True
    quiet = max(3 * (outlet.typical_gap or 0.0), 7 * 86400.0)
    return now - outlet.last_item_at > quiet


def _trouble(outlet: Outlet, state: str, detail: str, now: float) -> None:
    outlet.state = state
    outlet.detail = detail
    outlet.failures += 1
    outlet.trouble_since = outlet.trouble_since or now
    if state in (BROKEN, NO_FEED) and now - outlet.trouble_since > RETIRE_AFTER:
        outlet.state = RETIRED
        outlet.detail = f"retired after a month: {detail}"


async def poll(outlet: Outlet, client: httpx.AsyncClient,
               target: Optional[LocalNewsStore] = None) -> Outlet:
    """Read one outlet's feed and keep what is new. Never raises.

    Every outcome lands on the outlet's own record - its state, a sentence
    saying why, and when to try next - which is what the admin page shows.
    """
    target = target or store()
    now = time.time()
    outlet.last_poll = now
    try:
        if not outlet.feed_url or outlet.state == BROKEN:
            found = await discover(client, outlet.homepage)
            if not found:
                if outlet.feed_url and outlet.state == BROKEN:
                    found = outlet.feed_url  # keep trying the one we had
                else:
                    _trouble(outlet, NO_FEED, "the homepage offers no feed "
                             "we could find", now)
                    return _finish(outlet, target, now)
            if found != outlet.feed_url:
                log.info("local news: %s feed is %s", outlet.name, found)
                outlet.feed_url, outlet.etag, outlet.last_modified = found, "", ""

        if not await allowed(client, outlet.feed_url):
            outlet.state = DISALLOWED
            outlet.detail = "robots.txt does not allow reading this feed"
            return _finish(outlet, target, now)

        headers = {}
        if outlet.etag:
            headers["If-None-Match"] = outlet.etag
        if outlet.last_modified:
            headers["If-Modified-Since"] = outlet.last_modified
        reply = await _get(client, outlet.feed_url, headers)
        if reply.status_code == 304:
            outlet.failures, outlet.trouble_since = 0, 0.0
            outlet.state = STALE if _stale(outlet, now) else HEALTHY
            outlet.detail = "not modified since the last poll"
            return _finish(outlet, target, now)
        if reply.status_code in (401, 403, 429) or \
                _CHALLENGE.search(reply.text[:4000] if reply.content else ""):
            _trouble(outlet, BLOCKED,
                     f"the site refused us (HTTP {reply.status_code})", now)
            return _finish(outlet, target, now)
        if not reply.is_success:
            _trouble(outlet, BROKEN, f"HTTP {reply.status_code} from the feed",
                     now)
            return _finish(outlet, target, now)
        try:
            items = parse_feed(reply.content, str(reply.url))
        except ValueError as exc:
            _trouble(outlet, BROKEN, f"the feed could not be read: {exc}", now)
            return _finish(outlet, target, now)

        outlet.etag = reply.headers.get("etag", "")
        outlet.last_modified = reply.headers.get("last-modified", "")
        added = target.add_items(outlet.id, items)
        newest = max((i.published for i in items if i.published), default=0.0)
        outlet.last_item_at = max(outlet.last_item_at, newest)
        outlet.typical_gap = typical_gap(target.item_times(outlet.id))
        if items:
            full = sum(1 for i in items if words(i.text) >= ARTICLE_MIN_WORDS)
            outlet.full_text = full * 2 >= len(items)
        outlet.failures, outlet.trouble_since = 0, 0.0
        outlet.state = STALE if _stale(outlet, now) else HEALTHY
        outlet.detail = f"{len(items)} item(s) in the feed, {added} new"
        if not outlet.full_text:
            await _fetch_articles(outlet, client, target)
    except Exception as exc:  # noqa: BLE001 - one feed never stops the collector
        _trouble(outlet, BROKEN, f"{type(exc).__name__}: {exc}", now)
    return _finish(outlet, target, now)


async def _fetch_articles(outlet: Outlet, client: httpx.AsyncClient,
                          target: LocalNewsStore) -> None:
    """Fill in a teaser feed's newest stories from their pages."""
    teasers = target.latest_teasers(outlet.id, ARTICLES_PER_POLL)
    walled = 0
    for item in teasers:
        try:
            if not await allowed(client, item.url):
                continue
            page = await _get(client, item.url)
            if not page.is_success:
                walled += 1
                continue
            text = article_text(page.text[:MAX_BYTES])
            if words(text) < ARTICLE_MIN_WORDS or _PAYWALL.search(page.text):
                walled += 1
                continue
            target.set_body(item.url, text)
        except Exception as exc:  # noqa: BLE001
            log.info("local news: article %s could not be read: %s", item.url, exc)
    if teasers and walled == len(teasers):
        outlet.detail += "; articles are paywalled or blocked - teasers only"


def _finish(outlet: Outlet, target: LocalNewsStore, now: float) -> Outlet:
    outlet.next_poll = next_poll(outlet, now)
    try:
        target.save(outlet)
    except sqlite3.Error:
        log.warning("local news: could not save %s", outlet.name, exc_info=True)
    return outlet


def _demanded_outlets(target: LocalNewsStore, now: float) -> list:
    wanted: dict = {}
    for town, county, region in target.demanded(now - DEMAND_SECONDS):
        for outlet in target.for_place(town, county, region):
            wanted[outlet.id] = outlet
    return list(wanted.values())


async def poll_due(now: Optional[float] = None, limit: int = 40) -> int:
    """Poll every demanded outlet whose turn has come. Returns how many."""
    if not settings.local_news:
        return 0
    now = now or time.time()
    target = store()
    due = [o for o in _demanded_outlets(target, now)
           if o.state in POLLED and o.next_poll <= now][:limit]
    if not due:
        return 0
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT,
                                 follow_redirects=True) as client:
        await asyncio.gather(*(poll(o, client, target) for o in due))
    return len(due)


async def run_forever(every: float = 300.0) -> None:
    """The collector. Never raises; one bad sweep waits for the next."""
    while True:
        try:
            polled = await poll_due()
            if polled:
                log.info("local news: polled %d feed(s)", polled)
        except Exception:  # noqa: BLE001
            log.warning("local news: a sweep failed", exc_info=True)
        await asyncio.sleep(every)


async def refresh_place(place, budget: float = 2.5) -> int:
    """Poll a place's never-read or overdue outlets now, inside `budget`.

    The first question about a town finds its outlets unread - nothing has
    asked about it yet, so nothing has polled them. Reading them now, bounded
    like any other retrieval, is what makes that first question answerable.
    """
    target = store()
    now = time.time()
    due = [o for o in target.for_place(place.name, place.county, place.region)
           if o.state in POLLED and o.next_poll <= now][:8]
    if not due:
        return 0
    try:
        async with httpx.AsyncClient(timeout=min(FETCH_TIMEOUT, budget),
                                     follow_redirects=True) as client:
            await asyncio.wait_for(
                asyncio.gather(*(poll(o, client, target) for o in due)),
                timeout=budget)
    except asyncio.TimeoutError:
        log.info("local news: %d feed(s) for %s were still being read",
                 len(due), place.label)
    return len(due)


# --------------------------------------------------------------------------
# Matching, and what the writer reads
# --------------------------------------------------------------------------
def town_items(place, now: Optional[float] = None) -> list:
    """Stories about the town itself: its own outlets', and any naming it."""
    now = now or time.time()
    since = now - int(settings.local_news_window_days) * 86400
    out = []
    for item in store().items(since, place.region):
        own = (item.outlet_scope == TOWN
               and item.outlet_town.lower() == place.name.lower())
        if own or names(f"{item.title} {item.text}", place.name):
            out.append(item)
    return out[:PACKET_ITEMS]


def county_items(place, now: Optional[float] = None,
                 exclude: tuple = ()) -> list:
    """Stories about the county: its outlets', and any naming it."""
    if not place.county:
        return []
    now = now or time.time()
    since = now - int(settings.local_news_window_days) * 86400
    bare = _bare_county(place.county)
    label = place.county_label or place.county
    seen = {i.url for i in exclude}
    out = []
    for item in store().items(since, place.region):
        if item.url in seen:
            continue
        ours = (item.outlet_scope in (TOWN, COUNTY)
                and _bare_county(item.outlet_county).lower() == bare.lower())
        if ours or names(f"{item.title} {item.text}", label):
            out.append(item)
    return out[:PACKET_ITEMS]


def build_packet(items: list, scope: str, place) -> str:
    """The evidence, in the research packet's shape, never with hostnames."""
    import listener_clock
    import research

    now = listener_clock.now()
    budget = TOWN_WORDS if scope == TOWN else COUNTY_WORDS
    where = place.name if scope == TOWN else (place.county_label or place.county)
    parts = []
    for index, item in enumerate(items, 1):
        when = (datetime.fromtimestamp(item.published, timezone.utc)
                if item.published else None)
        stamp = when.strftime("%Y-%m-%d") if when else "unknown"
        parts.append(f"SOURCE {index}")
        parts.append(f"Title: {item.title}")
        parts.append(f"Published: {stamp} ({research.age_phrase(when, now)})")
        kind = ("a local outlet covering " + where if scope == TOWN
                else "an outlet covering " + where)
        parts.append(f"Source type: {kind}")
        if item.text:
            parts.append("Key evidence:")
            parts.append(clip(item.text, budget))
        parts.append("")
    return "\n".join(parts)


def report() -> dict:
    """What the collector holds and how its feeds are - for /api/health."""
    try:
        counts = store().counts()
    except sqlite3.Error as exc:
        return {"enabled": bool(settings.local_news), "error": str(exc)}
    return {"enabled": bool(settings.local_news),
            "poll_minutes": int(settings.local_news_poll_minutes),
            "window_days": int(settings.local_news_window_days),
            "order": ["town", "county", "exa (known outlets)", "nothing found"],
            **counts}


def admin_rows(limit: int = 200) -> list:
    """Every outlet and its state, for /admin. Troubled ones first."""
    order = {BROKEN: 0, BLOCKED: 1, NO_FEED: 2, STALE: 3, NEW: 4, HEALTHY: 5,
             DISALLOWED: 6, RETIRED: 7, EXCLUDED: 8}
    rows = sorted(store().outlets(), key=lambda o: (order.get(o.state, 9), o.name))
    return [{"name": o.name, "host": o.host, "town": o.town, "county": o.county,
             "scope": o.scope, "state": o.state, "detail": o.detail,
             "full_text": o.full_text, "source": o.source,
             "last_poll": o.last_poll, "last_item_at": o.last_item_at}
            for o in rows[:limit]]


# --------------------------------------------------------------------------
# The local ladder
# --------------------------------------------------------------------------
@dataclass
class LocalResult:
    """What the local ladder found for a place, and what it did."""

    place: object
    #: Which rung answered: "town", "county", "exa" or "" for none.
    scope: str = ""
    packet: object = None
    #: True when the town itself had nothing - the gap sentence is said.
    gap: bool = False
    #: The weather there, when the town had nothing (or it was asked for).
    weather: object = None
    attempts: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"place": self.place.as_dict() if self.place else None,
                "scope": self.scope, "gap": self.gap,
                "weather": bool(self.weather),
                "attempts": list(self.attempts)}


def gap_line(place, weather: bool = True, county_news: bool = True) -> str:
    """The sentence an episode opens with when the town had nothing (§194).

    **Composed here, never by the writer**, at the owner's direction: it is
    the one place FAM tells a listener what its search did not find, so its
    words are fixed and identical on every client. It speaks about our
    search - "we couldn't find" - never about the world ("nothing is
    happening"), which is the line [missed-not-absent] exists to hold. And
    it promises only what follows it: the weather when there is a forecast,
    the county's news when there is some.
    """
    town = place.name
    county = place.county_label or "the surrounding area"
    if weather and county_news:
        tail = (f" Here's the weather there, and the closest news we have, "
                f"from across {county}.")
    elif weather:
        tail = " Here's the weather there."
    elif county_news:
        tail = f" Here's the closest news we have, from across {county}."
    else:
        tail = ""
    return f"We couldn't find any recent news reports out of {town}.{tail}"


def _packet(items: list, scope: str, place, backend: str):
    import provenance as provenance_mod
    import research

    packet = research.Packet(backend=backend,
                             window_days=int(settings.local_news_window_days))
    packet.context = build_packet(items, scope, place)
    packet.sources = list(dict.fromkeys(host_of(i.url) for i in items))
    packet.results_returned = len(items)
    packet.provenance = provenance_mod.from_results(items, retriever=backend)
    return packet


async def research_local(place, brief=None, exa=None) -> LocalResult:
    """Town, then county, then Exa on the known outlets. Never raises.

    `exa` is the coroutine function that runs one Exa search
    (`(query, include_domains) -> Packet`), passed in so this module does
    not reach into the writer's retrieval code and tests can stand one in.
    """
    result = LocalResult(place=place)
    try:
        target = store()
        target.asked(place.name, place.county, place.region)
        await refresh_place(place)
    except Exception:  # noqa: BLE001 - an unreadable store is an empty one
        log.warning("local news: the store could not be used", exc_info=True)
        target = None

    town = town_items(place) if target else []
    if town:
        result.scope = TOWN
        result.packet = _packet(town, TOWN, place, "local-town")
        result.attempts.append(("town", len(town)))
        return result
    result.attempts.append(("town", 0))
    result.gap = True

    county = county_items(place) if target else []
    result.attempts.append(("county", len(county)))
    if county:
        result.scope = COUNTY
        result.packet = _packet(county, COUNTY, place, "local-county")
        return result

    if exa is not None:
        known = sorted({o.host for o in (target.for_place(
            place.name, place.county, place.region) if target else [])
            if o.host and o.state != EXCLUDED})
        query = " ".join(filter(None, [
            getattr(brief, "retrieval", "") or "", place.name, place.region]))
        try:
            # Only results that name the place survive inside the search
            # (`research.names_place`) - the fix that stops invention.
            packet = await exa(query, tuple(known), place.name)
        except Exception:  # noqa: BLE001
            log.warning("local news: the Exa rung failed for %s", place.label,
                        exc_info=True)
            packet = None
        result.attempts.append(("exa", len(getattr(packet, "sources", []) or [])
                                if packet else 0))
        if packet:
            # Every result here names the town, so this *is* town news and
            # there is no gap to announce.
            result.scope = "exa"
            result.gap = False
            result.packet = packet
            _learn(packet, place, target)
            return result
    return result


def _learn(packet, place, target: Optional[LocalNewsStore]) -> None:
    """Keep any site Exa found writing about the town as a candidate outlet.

    This is how the registry grows without anybody typing an outlet in: the
    next question about the town finds its stories already collected. A
    learned outlet is filed as `MENTION` - usually a regional site - so its
    stories count for a place only when they name it.
    """
    if target is None or packet is None:
        return
    import research

    provenance = getattr(packet, "provenance", None)
    for item in list(getattr(provenance, "items", []) or []):
        host = host_of(str(getattr(item, "url", "") or "")) or str(
            getattr(item, "label", "") or "")
        if not host or host in research.TIER_PRIMARY or \
                host in research.TIER_ESTABLISHED:
            continue  # a national outlet is not a local one
        target.add_outlet(host, f"https://{host}/", town=place.name,
                          county=place.county, region=place.region,
                          country=place.country, scope=MENTION,
                          source="learned")
