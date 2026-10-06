"""GDELT: a second article index, and the news behind the story pool.

Two jobs from one upstream, because GDELT answers two different questions and
FAM has two different clocks for them:

* **Evidence** (`retrieve`) - a second index beside Exa: the rung an episode
  falls to when Exa comes back empty, and the optional cross-check.
* **Attention** (`discover`, `volume_for`, `GdeltTrendingSource`) - what the
  world's press is running, for the story pool and the Trending registry.

Read from the export files, never the search API (§211)
-------------------------------------------------------
FAM used to ask GDELT's DOC 2.0 search API for all of it. That API allows one
request every five seconds *per address*, and Render's outbound address is
shared with every other tenant on it: on 1/10 GDELT refused 384 of 384 (§191),
§144 had already seen it refuse requests sent one at a time, and §207's
breaker only limited how much the refusals cost. Pacing cannot fix a limit
other people are spending.

GDELT also publishes everything it reads as plain files, every fifteen
minutes (`lastupdate.txt` names the newest). They are file downloads, with no
per-address limit, and the Global Knowledge Graph file carries what FAM read
from DOC: each article's URL, title, publisher, date, GKG themes and the
people, organisations and places it names. So:

* **One background job downloads each new file** (`sync`, scheduled by
  `run_forever`): 96 a day, plus one `lastupdate.txt` each time - the same
  whether FAM has ten listeners or ten million.
* **Everything else reads the copy on disk** (`ExportStore`). The story
  sweep's worldwide and regional samples, the theme volumes and an episode's
  fallback search are local reads. **No listener's tap ever reaches GDELT.**

What the copy cannot do that DOC did: search article *bodies* (DOC matched
full text; the copy matches titles and the names GDELT extracted), reach back
three months (it holds `GDELT_EXPORT_KEEP_HOURS`), or read the translated
feed (the main export is English-language press). For a fallback rung and a
news sweep, a day of English headlines is what was being used.

Publisher countries come from GDELT's own domain-to-country list (fetched
once a month, `DOMAINS_URL`), and from the domain's country code where the
list does not know it. A `.com` the list does not know has no country, which
`stories.country_shares` already treats as "could not tell".

Not verified against the live service
-------------------------------------
The build container's egress proxy blocks `data.gdeltproject.org`, so the
file shapes below are written from GDELT's published codebook and pinned
against recorded rows in `tests/test_gdelt_exports_211.py`. **Nothing here has
downloaded a real file.** Run `python tools/gdelt_probe.py` somewhere with
network before believing it works.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import io
import logging
import re
import sqlite3
import threading
import time
import zipfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import httpx

import trending
from config import settings
from paths import data_path

log = logging.getLogger(__name__)

#: GDELT's list of news domains and the country each publishes from. A static
#: file, fetched at most once every `DOMAINS_MAX_AGE_SECONDS`.
DOMAINS_URL = ("http://data.gdeltproject.org/blog/2018-news-outlets-by-country-"
               "may2018-update/MASTER-GDELTDOMAINSBYCOUNTRY-MAY2018.txt")
DOMAINS_MAX_AGE_SECONDS = 30 * 86400

#: GKG themes measured for the story pool and the Trending registry, with the
#: plain-English subject each one stands for. Chosen to span domains rather
#: than to be exhaustive.
THEMES = (
    ("ECON_STOCKMARKET", "the stock market"),
    ("ECON_INFLATION", "inflation"),
    ("ENV_CLIMATECHANGE", "climate change"),
    ("WB_2670_JOBS", "jobs and employment"),
    ("EPU_POLICY_GOVERNMENT", "government policy"),
    ("TAX_DISEASE", "public health"),
    ("SCIENCE", "science"),
    ("MEDIA_SOCIAL", "social media"),
    ("CRISISLEX_CRISISLEXREC", "disasters and emergencies"),
    ("ELECTION", "elections"),
    ("TAX_FNCACT_SOLDIER", "armed conflict"),
    ("WB_635_PUBLIC_HEALTH", "healthcare"),
    ("ENERGY", "energy"),
    ("TECH", "technology"),
    ("SPORTS", "sport"),
)
THEME_CODES = frozenset(code for code, _subject in THEMES)

#: Country-code domains used as generic names (.io, .tv, .ai...), which say
#: nothing about where a publisher is.
GENERIC_TLDS = frozenset({
    "io", "co", "tv", "me", "ai", "fm", "ly", "to", "ws", "cc", "gg", "im",
    "la", "nu", "sh", "ac", "vc", "is", "it", "am", "ag", "tk", "ml", "ga",
    "cf", "gq",
})

#: GDELT writes some country names FIPS-style; these are the ones
#: `stories.normalise_country` would not otherwise recognise.
_FIPS_NAMES = {
    "korea, south": "south korea", "korea, north": "north korea",
    "congo, democratic republic of the": "democratic republic of the congo",
    "congo, republic of the": "republic of the congo",
    "gambia, the": "gambia", "bahamas, the": "bahamas",
    "cote d'ivoire": "ivory coast", "west bank": "palestine",
    "gaza strip": "palestine", "macedonia, the former yugoslav republic of": "macedonia",
}


class _Result:
    """One article, shaped like the Exa result the rest of FAM already reads.

    Duck-typed rather than adapted at the call site, so `research.rank_results`,
    `research.credibility`, `research.published_at` and `provenance.from_results`
    all work on it unchanged.
    """

    __slots__ = ("title", "url", "published_date", "highlights", "text",
                 "country")

    def __init__(self, title: str, url: str, published_date: str,
                 highlights: list, country: str = "") -> None:
        self.title = title
        self.url = url
        self.published_date = published_date
        self.highlights = highlights
        self.text = ""
        #: Where the *publisher* is ("united states"), or "" when unknown -
        #: what lets a story say which countries' press is running it.
        self.country = country


# --------------------------------------------------------------------------
# Reading the export files
# --------------------------------------------------------------------------
#: GKG 2.1 columns, by position (the codebook's order).
_DATE, _COLLECTION, _SOURCE, _URL = 1, 2, 3, 4
_THEMES, _LOCATIONS, _PERSONS, _ORGS, _EXTRAS = 7, 9, 11, 13, 26
_TITLE = re.compile(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>", re.S)
_STAMP = re.compile(r"(\d{14})\.gkg\.csv\.zip$")
#: Most characters of extracted names kept per article for search.
NAMES_CHARS = 400


def stamp_of(name: str) -> Optional[datetime]:
    """`20261006141500.gkg.csv.zip` -> its UTC time, or None."""
    found = _STAMP.search(name or "")
    if not found:
        return None
    try:
        return datetime.strptime(found.group(1), "%Y%m%d%H%M%S").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None


def name_for(at: datetime) -> str:
    return at.strftime("%Y%m%d%H%M%S") + ".gkg.csv.zip"


def parse_lastupdate(text: str) -> tuple:
    """`lastupdate.txt` -> `(url, md5)` of the newest GKG file, or ("", "").

    Three lines, `size md5 url`, one each for the event, mentions and GKG
    files; only the GKG one is read.
    """
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2].endswith(".gkg.csv.zip"):
            return parts[2], parts[1]
    return "", ""


def domain_country(domain: str, known: Optional[dict] = None) -> str:
    """The country a publisher's domain is in, or "" when nothing says."""
    import stories

    domain = (domain or "").lower().strip().rstrip(".")
    if domain.startswith("www."):
        domain = domain[4:]
    if known:
        hit = known.get(domain)
        if hit:
            return hit
    tld = domain.rsplit(".", 1)[-1] if "." in domain else ""
    if tld == "uk":
        return "united kingdom"
    if not tld or tld in GENERIC_TLDS:
        return ""
    return stories.ISO_COUNTRIES.get(tld, "")


def _country_name(raw: str) -> str:
    import stories

    text = " ".join((raw or "").lower().split())
    return stories.normalise_country(_FIPS_NAMES.get(text, text))


def parse_domains(text: str) -> dict:
    """GDELT's domain list -> `{domain: country}`. Tolerant of its layout:
    tab-separated, domain first and the country's name last."""
    out: dict = {}
    for line in (text or "").splitlines():
        parts = [p.strip() for p in line.split("\t")]
        if len(parts) < 2 or not parts[0] or "." not in parts[0]:
            continue
        name = _country_name(parts[-1])
        if name:
            out[parts[0].lower()] = name
    return out


def _names(field: str, limit: int = 12) -> list:
    out: list = []
    for item in (field or "").split(";"):
        item = item.strip()
        if item and item not in out:
            out.append(item)
        if len(out) >= limit:
            break
    return out


def _places(field: str, limit: int = 8) -> list:
    """V1LOCATIONS entries are `type#name#country#adm1#lat#long#id`."""
    out: list = []
    for item in (field or "").split(";"):
        parts = item.split("#")
        if len(parts) > 1 and parts[1].strip() and parts[1].strip() not in out:
            out.append(parts[1].strip())
        if len(out) >= limit:
            break
    return out


def parse_gkg_line(line: str, known: Optional[dict] = None) -> Optional[dict]:
    """One GKG row -> what the copy keeps of it, or None.

    Only web articles (collection 1) with a URL and a title are kept: an
    article FAM cannot name is not evidence and cannot be clustered. A row
    with too few columns is skipped rather than raising - one malformed line
    must not cost the file.
    """
    cols = line.rstrip("\r\n").split("\t")
    if len(cols) <= _EXTRAS:
        return None
    if cols[_COLLECTION].strip() != "1":
        return None
    url = cols[_URL].strip()
    if not url.startswith(("http://", "https://")):
        return None
    found = _TITLE.search(cols[_EXTRAS])
    title = " ".join(html.unescape(found.group(1)).split()) if found else ""
    if not title:
        return None
    stamp = cols[_DATE].strip()
    try:
        seen = datetime.strptime(stamp[:14], "%Y%m%d%H%M%S").replace(
            tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None
    themes = sorted({t for t in cols[_THEMES].split(";") if t in THEME_CODES})
    names = _names(cols[_PERSONS]) + _names(cols[_ORGS]) + _places(cols[_LOCATIONS])
    domain = cols[_SOURCE].strip().lower()
    return {
        "url": url,
        "title": title[:300],
        "domain": domain,
        "country": domain_country(domain, known),
        "seen": seen,
        "themes": (";" + ";".join(themes) + ";") if themes else "",
        "names": "; ".join(names)[:NAMES_CHARS].lower(),
        "all_themes": cols[_THEMES],
    }


def parse_gkg_file(blob: bytes, known: Optional[dict] = None,
                   keep: int = 0) -> tuple:
    """A zipped GKG file -> `(rows kept, theme counts over every row)`.

    Theme counts are taken over every article in the file, so the volume a
    theme is getting is a measurement of the whole feed; only `keep` rows
    are stored, because the disk is shared with every other store.
    """
    kept: list = []
    counts: dict = {}
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        members = [n for n in archive.namelist() if n.endswith(".csv")]
        if not members:
            raise ValueError("the GKG archive holds no .csv file")
        with archive.open(members[0]) as raw:
            for line_bytes in raw:
                row = parse_gkg_line(line_bytes.decode("utf-8", "replace"), known)
                if row is None:
                    continue
                for theme in set(row.pop("all_themes").split(";")) & THEME_CODES:
                    counts[theme] = counts.get(theme, 0) + 1
                if not keep or len(kept) < keep:
                    kept.append(row)
    return kept, counts


# --------------------------------------------------------------------------
# The copy on disk
# --------------------------------------------------------------------------
class ExportStore:
    """GDELT's last `GDELT_EXPORT_KEEP_HOURS` of articles, on the data disk.

    Kept on the disk rather than in memory so a redeploy does not download a
    day again, and so a day of articles is not held in the server's RAM.
    Plain tables, no full-text index: searching is the rare fallback rung,
    and a scan of a day's titles is milliseconds against the space an index
    would cost.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = data_path("GDELT_EXPORT_DB", "gdelt_export.db", path)
        self._lock = threading.Lock()
        with closing(self._connect()) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS files (
                    name TEXT PRIMARY KEY, stamp REAL NOT NULL,
                    rows INTEGER NOT NULL, fetched_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS articles (
                    url TEXT PRIMARY KEY, title TEXT NOT NULL,
                    domain TEXT NOT NULL, country TEXT NOT NULL,
                    seen REAL NOT NULL, themes TEXT NOT NULL,
                    names TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS articles_seen ON articles(seen);
                CREATE INDEX IF NOT EXISTS articles_country ON articles(country, seen);
                CREATE TABLE IF NOT EXISTS theme_counts (
                    stamp REAL NOT NULL, theme TEXT NOT NULL, n INTEGER NOT NULL,
                    PRIMARY KEY (stamp, theme));
                CREATE TABLE IF NOT EXISTS domains (
                    domain TEXT PRIMARY KEY, country TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    # -- writing -----------------------------------------------------------
    def has_file(self, name: str) -> bool:
        with closing(self._connect()) as db:
            return db.execute("SELECT 1 FROM files WHERE name=?",
                              (name,)).fetchone() is not None

    def add_file(self, name: str, stamp: float, rows: list, counts: dict,
                 now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        with self._lock, closing(self._connect()) as db, db:
            db.executemany(
                "INSERT OR REPLACE INTO articles (url, title, domain, country, "
                "seen, themes, names) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(r["url"], r["title"], r["domain"], r["country"], r["seen"],
                  r["themes"], r["names"]) for r in rows])
            db.executemany(
                "INSERT OR REPLACE INTO theme_counts (stamp, theme, n) VALUES (?, ?, ?)",
                [(stamp, theme, int(n)) for theme, n in counts.items()])
            db.execute("INSERT OR REPLACE INTO files (name, stamp, rows, fetched_at) "
                       "VALUES (?, ?, ?, ?)", (name, stamp, len(rows), now))

    def mark_missing(self, name: str, stamp: float,
                     now: Optional[float] = None) -> None:
        """A file GDELT never published (it skips one now and then): noted,
        with -1 rows, so it is not asked for again."""
        now = time.time() if now is None else now
        with self._lock, closing(self._connect()) as db, db:
            db.execute("INSERT OR IGNORE INTO files (name, stamp, rows, fetched_at) "
                       "VALUES (?, ?, -1, ?)", (name, stamp, now))

    def prune(self, keep_hours: float, now: Optional[float] = None) -> int:
        cutoff = (time.time() if now is None else now) - keep_hours * 3600
        with self._lock, closing(self._connect()) as db, db:
            gone = db.execute("DELETE FROM articles WHERE seen < ?", (cutoff,)).rowcount
            db.execute("DELETE FROM theme_counts WHERE stamp < ?", (cutoff,))
            db.execute("DELETE FROM files WHERE stamp < ?", (cutoff,))
        return int(gone or 0)

    def set_domains(self, known: dict, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        with self._lock, closing(self._connect()) as db, db:
            db.execute("DELETE FROM domains")
            db.executemany("INSERT OR REPLACE INTO domains (domain, country) VALUES (?, ?)",
                           list(known.items()))
            db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('domains_at', ?)",
                       (str(now),))

    def note_domains_tried(self, at: float) -> None:
        """Move the domain list's clock without touching the list."""
        with self._lock, closing(self._connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('domains_at', ?)",
                       (str(at),))

    # -- reading -----------------------------------------------------------
    def domains(self) -> dict:
        with closing(self._connect()) as db:
            return dict(db.execute("SELECT domain, country FROM domains").fetchall())

    def domains_at(self) -> float:
        with closing(self._connect()) as db:
            row = db.execute("SELECT value FROM meta WHERE key='domains_at'").fetchone()
        try:
            return float(row[0]) if row else 0.0
        except ValueError:
            return 0.0

    def newest(self) -> float:
        """When the newest file held was published (0.0 for none)."""
        with closing(self._connect()) as db:
            row = db.execute("SELECT MAX(stamp) FROM files WHERE rows >= 0").fetchone()
        return float(row[0] or 0.0) if row else 0.0

    def counts(self) -> dict:
        with closing(self._connect()) as db:
            files = db.execute("SELECT COUNT(*) FROM files WHERE rows >= 0").fetchone()[0]
            articles = db.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        return {"files": int(files), "articles": int(articles)}

    def volume(self, theme: str, since: float) -> float:
        with closing(self._connect()) as db:
            row = db.execute("SELECT SUM(n) FROM theme_counts WHERE theme=? AND stamp >= ?",
                             (theme, since)).fetchone()
        return float(row[0] or 0.0) if row else 0.0

    def _rows(self, sql: str, args: tuple) -> list:
        with closing(self._connect()) as db:
            return db.execute(sql, args).fetchall()

    def by_theme(self, theme: str, since: float, limit: int) -> list:
        return self._rows(
            "SELECT title, url, seen, country FROM articles WHERE seen >= ? "
            "AND instr(themes, ?) > 0 ORDER BY seen DESC LIMIT ?",
            (since, f";{theme};", int(limit)))

    def by_countries(self, countries: Iterable[str], since: float, limit: int) -> list:
        wanted = tuple(countries)
        if not wanted:
            return []
        marks = ",".join("?" for _ in wanted)
        return self._rows(
            f"SELECT title, url, seen, country FROM articles WHERE seen >= ? "
            f"AND country IN ({marks}) ORDER BY seen DESC LIMIT ?",
            (since, *wanted, int(limit)))

    def search(self, words: list, since: float, limit: int) -> list:
        """Articles whose title or extracted names carry these words, best
        first: most words matched, then newest. At least two words must match
        when the query has two - one shared word is a namesake, not a match."""
        if not words:
            return []
        # Words matched are counted in SQL, before the limit: a limit taken
        # over "any word matched" let a day of one common name fill it and
        # push out the article that names both (review fix).
        score = " + ".join("(title LIKE ? OR names LIKE ?)" for _ in words)
        likes: list = []
        for word in words:
            likes += [f"%{word}%", f"%{word}%"]
        need = min(2, len(words))
        rows = self._rows(
            f"SELECT title, url, seen, country, names FROM ("
            f"SELECT title, url, seen, country, names, ({score}) AS hits "
            f"FROM articles WHERE seen >= ?) WHERE hits >= ? "
            f"ORDER BY hits DESC, seen DESC LIMIT ?",
            (*likes, since, need, max(200, int(limit) * 10)))
        scored = []
        for title, url, seen, country, names in rows:
            text = f"{title.lower()} {names}"
            hits = sum(1 for w in words if re.search(rf"\b{re.escape(w)}", text))
            if hits >= need:
                scored.append((hits, seen, title, url, country))
        scored.sort(key=lambda row: (-row[0], -row[1]))
        return [(title, url, seen, country)
                for _h, seen, title, url, country in scored[:int(limit)]]


_STORE: list = [None]


def store() -> ExportStore:
    if _STORE[0] is None:
        _STORE[0] = ExportStore()
    return _STORE[0]


def reset_store() -> None:
    """Forget the open store, so a test's `GDELT_EXPORT_DB` is the one used."""
    _STORE[0] = None


def exists() -> bool:
    import os

    return os.path.exists(data_path("GDELT_EXPORT_DB", "gdelt_export.db"))


def _results(rows: list) -> list:
    out = []
    for title, url, seen, country in rows:
        stamp = datetime.fromtimestamp(seen, tz=timezone.utc)
        out.append(_Result(title=title, url=url,
                           published_date=stamp.strftime("%Y-%m-%d"),
                           # The title is the only text the export carries;
                           # passed as the one highlight rather than padded.
                           highlights=[title], country=country))
    return out


# --------------------------------------------------------------------------
# Fetching: the only code that talks to GDELT
# --------------------------------------------------------------------------
class _State:
    """What `/api/health` says about the downloads."""

    def __init__(self) -> None:
        self.last_ok = 0.0
        self.last_attempt = 0.0
        self.failures_in_a_row = 0
        self.last_error = ""
        self.last_file = ""

    def report(self) -> dict:
        return {"last_ok": self.last_ok or None,
                "last_attempt": self.last_attempt or None,
                "failures_in_a_row": self.failures_in_a_row,
                "last_error": self.last_error or None,
                "last_file": self.last_file or None}


STATE = _State()
_SYNCING = [False]


def _describe(exc: BaseException) -> str:
    """A failure in words - a timeout's own message is empty (§144)."""
    text = str(exc).strip()
    name = type(exc).__name__
    return f"{name}: {text}" if text else name


async def _download(client: httpx.AsyncClient, url: str) -> httpx.Response:
    import provider_usage

    try:
        response = await client.get(url)
    except Exception:
        provider_usage.record("gdelt", ok=False)
        raise
    provider_usage.record("gdelt", ok=response.is_success)
    return response


async def _refresh_domains(client: httpx.AsyncClient, target: ExportStore,
                           now: float) -> None:
    """GDELT's domain-to-country list, once a month. A failure leaves the
    country-code fallback in charge and is tried again next sync."""
    if now - target.domains_at() < DOMAINS_MAX_AGE_SECONDS:
        return
    try:
        response = await _download(client, DOMAINS_URL)
        response.raise_for_status()
        known = parse_domains(response.text)
        if known:
            await asyncio.to_thread(target.set_domains, known, now)
            log.info("gdelt: %d publisher domains placed by country", len(known))
    except Exception as exc:  # noqa: BLE001 - a country is a nicety
        log.info("gdelt: the domain list could not be read: %s", _describe(exc))
        # Asked again in a day, not at every sync (review fix).
        await asyncio.to_thread(target.note_domains_tried,
                                now - DOMAINS_MAX_AGE_SECONDS + 86400)


async def _fetch_file(client: httpx.AsyncClient, target: ExportStore,
                      name: str, md5: str, known: dict) -> int:
    """One GKG file into the copy. Returns the rows kept; -1 for a file
    GDELT never published. Raises on anything else."""
    url = f"{settings.gdelt_export_base.rstrip('/')}/{name}"
    response = await _download(client, url)
    at = stamp_of(name)
    stamp = at.timestamp() if at else time.time()
    if response.status_code == 404 and not md5:
        # A file behind the newest that GDELT never published. The newest
        # one (the file `lastupdate.txt` named, with its checksum) is never
        # written off: a 404 there raises and the next sync asks again.
        await asyncio.to_thread(target.mark_missing, name, stamp)
        return -1
    response.raise_for_status()
    blob = response.content
    if md5 and hashlib.md5(blob).hexdigest() != md5.lower():
        raise ValueError(f"{name} did not match the checksum GDELT published")
    rows, counts = await asyncio.to_thread(
        parse_gkg_file, blob, known, int(settings.gdelt_export_rows_per_file))
    await asyncio.to_thread(target.add_file, name, stamp, rows, counts)
    return len(rows)


async def sync(now: Optional[float] = None,
               client: Optional[httpx.AsyncClient] = None) -> dict:
    """Bring the copy up to date with GDELT's newest file. Never raises.

    Reads `lastupdate.txt`, fetches the newest GKG file and - when the copy
    is behind - up to `GDELT_EXPORT_BACKFILL_FILES` before it, oldest first,
    then drops what is older than `GDELT_EXPORT_KEEP_HOURS`. One sync at a
    time; a second caller returns at once.
    """
    if not settings.gdelt:
        return {"skipped": "GDELT=0"}
    if _SYNCING[0]:
        return {"skipped": "a sync is already running"}
    _SYNCING[0] = True
    now = time.time() if now is None else now
    STATE.last_attempt = now
    fetched = 0
    try:
        target = store()
        own = client is None
        client = client or httpx.AsyncClient(
            timeout=float(settings.gdelt_export_timeout_seconds),
            follow_redirects=True)
        try:
            await _refresh_domains(client, target, now)
            response = await _download(
                client, f"{settings.gdelt_export_base.rstrip('/')}/lastupdate.txt")
            response.raise_for_status()
            url, md5 = parse_lastupdate(response.text)
            newest_name = url.rsplit("/", 1)[-1] if url else ""
            newest = stamp_of(newest_name)
            if newest is None:
                raise ValueError("lastupdate.txt named no GKG file")
            keep_after = now - float(settings.gdelt_export_keep_hours) * 3600
            wanted = [(newest_name, md5)]
            for step in range(1, max(0, int(settings.gdelt_export_backfill_files)) + 1):
                earlier = newest - timedelta(minutes=15 * step)
                if earlier.timestamp() < keep_after:
                    break
                wanted.append((name_for(earlier), ""))
            known = await asyncio.to_thread(target.domains)
            for name, digest in reversed(wanted):
                if await asyncio.to_thread(target.has_file, name):
                    continue
                kept = await _fetch_file(client, target, name, digest, known)
                if kept >= 0:
                    fetched += 1
                    STATE.last_file = name
        finally:
            if own:
                await client.aclose()
        pruned = await asyncio.to_thread(
            target.prune, float(settings.gdelt_export_keep_hours), now)
        STATE.last_ok = now
        STATE.failures_in_a_row = 0
        STATE.last_error = ""
        if fetched:
            log.info("gdelt: %d export file(s) read; %d old article(s) dropped",
                     fetched, pruned)
        return {"fetched": fetched, "pruned": pruned}
    except Exception as exc:  # noqa: BLE001 - the next sync tries again
        STATE.failures_in_a_row += 1
        STATE.last_error = _describe(exc)[:200]
        log.warning("gdelt: export sync failed (%d in a row): %s",
                    STATE.failures_in_a_row, STATE.last_error)
        return {"fetched": fetched, "error": STATE.last_error}
    finally:
        _SYNCING[0] = False


async def run_forever() -> None:
    """The one job that downloads from GDELT. Never raises."""
    while True:
        await sync()
        await asyncio.sleep(max(60.0, float(settings.gdelt_export_poll_seconds)))


# --------------------------------------------------------------------------
# Reading: everything FAM asks of GDELT, answered from the copy
# --------------------------------------------------------------------------
class ExportStale(RuntimeError):
    """The copy holds nothing recent enough to be today's news."""


#: The copy is an outage, not a quiet day, when its newest file is older than
#: this: four missed polls.
STALE_AFTER_SECONDS = 3600.0


def freshness(now: Optional[float] = None) -> Optional[float]:
    """Seconds since the newest file held was published, or None for none."""
    newest = store().newest()
    if not newest:
        return None
    return max(0.0, (time.time() if now is None else now) - newest)


def _check_fresh() -> None:
    age = freshness()
    if age is None:
        raise ExportStale("GDELT's export copy is empty - no file has been read yet")
    if age > STALE_AFTER_SECONDS:
        raise ExportStale(f"GDELT's export copy is {age / 3600:.1f}h old")


#: Most words a free-text query keeps.
MAX_QUERY_WORDS = 10


def clean_query(query: str) -> str:
    """Free text reduced to its words - a degraded brief can send a whole
    DailyFAM prompt, brackets and all (§144)."""
    words = re.sub(r"[^\w\s'-]", " ", query or "").split()
    return " ".join(words[:MAX_QUERY_WORDS])


def _search_words(query: str) -> list:
    import news_clusters

    out: list = []
    for word in clean_query(query).lower().split():
        word = word.strip("'-")
        if len(word) >= 3 and word not in news_clusters.STOPWORDS and word not in out:
            out.append(word)
    return out


def available() -> tuple[bool, str]:
    """Whether GDELT is switched on. No credential exists to check."""
    if not settings.gdelt:
        return False, "GDELT=0"
    return True, "GDELT export files, keyless; read from the copy on disk"


async def retrieve(query: str, limit: int = 0,
                   recency_days: int = 0) -> list:
    """Articles for `query` from the copy, as Exa-shaped results. Never raises.

    A local read: nothing here reaches GDELT, so an episode's fallback rung
    costs GDELT nothing however many listeners there are. `[]` on any
    failure, because a fallback that could break an episode would be worse
    than none.
    """
    words = _search_words(query)
    if not words:
        return []
    ok, _why = available()
    if not ok:
        return []
    limit = limit or int(settings.gdelt_max_records)
    hours = float(settings.gdelt_export_keep_hours)
    if recency_days > 0:
        hours = min(hours, recency_days * 24.0)
    since = time.time() - hours * 3600
    try:
        rows = await asyncio.to_thread(store().search, words, since, limit)
    except Exception as exc:  # noqa: BLE001 - see docstring
        log.warning("gdelt: searching the copy failed for %r: %s", query,
                    _describe(exc))
        return []
    results = _results(rows)
    log.info("gdelt: %d article(s) for %r from the copy", len(results), query)
    return results


async def artlist(query: str, limit: int, hours: int, timeout: float = 0.0) -> list:
    """Recent articles for a selector, from the copy. **Raises** when the copy
    is empty or stale, unlike `retrieve` - a sweep must tell "GDELT had
    nothing" from "FAM has not read GDELT".

    Selectors: `theme:CODE`, or a `region_query` (`sourcecountry:` names,
    OR'd). Anything else is a word search.
    """
    await asyncio.to_thread(_check_fresh)
    since = time.time() - max(1, hours) * 3600
    target = store()
    if query.startswith("theme:"):
        rows = await asyncio.to_thread(target.by_theme, query[6:], since, limit)
    elif "sourcecountry:" in query:
        compact = set(re.findall(r"sourcecountry:(\w+)", query))
        countries = [c for c in _known_countries() if c.replace(" ", "") in compact]
        rows = await asyncio.to_thread(target.by_countries, countries, since, limit)
    else:
        rows = await asyncio.to_thread(target.search, _search_words(query), since, limit)
    return _results(rows)


def _known_countries() -> list:
    import geography

    return sorted(geography.REGION_OF)


def region_query(region: str) -> str:
    """One region's press: its main source countries, as the selector
    `artlist` reads (`sourcecountry:` names with their spaces removed)."""
    import geography

    names = geography.GDELT_SOURCES.get(region, ())
    if not names:
        return ""
    if len(names) == 1:
        return f"sourcecountry:{names[0]}"
    return "(" + " OR ".join(f"sourcecountry:{n}" for n in names) + ")"


async def discover(hot_themes: list, timeout: float = 0.0, hours: int = 12,
                   per_query: int = 75, regions=None,
                   concurrency: int = 6) -> tuple:
    """Read what the world's press, and each region's, is running right now.

    One read per hot theme for the worldwide sample, and one per region over
    that region's own press - all from the copy. Returns `(articles,
    scope_of, (failures, asked))`, so a sweep against an empty or stale copy
    is reported as an outage rather than as a quiet news day.
    """
    import geography

    regions = tuple(geography.REGIONS if regions is None else regions)
    jobs = [("world", f"theme:{theme}") for theme in hot_themes]
    jobs += [(region, region_query(region)) for region in regions
             if region_query(region)]
    found_in: dict = {}
    failures = 0
    articles: list = []
    for scope, query in jobs:
        try:
            rows = await artlist(query, per_query, hours, timeout)
        except Exception as exc:  # noqa: BLE001 - one read is not the sweep
            failures += 1
            log.info("gdelt: %s read failed: %s", scope, _describe(exc))
            continue
        for row in rows:
            # First finder wins, and a worldwide finding beats a regional
            # one: the world reads run first in `jobs`.
            found_in.setdefault(row.url, scope)
            articles.append(row)
    return articles, (lambda article: found_in.get(article.url, "")), \
        (failures, len(jobs))


async def volume_for(theme: str, timeout: float = 0.0) -> float:
    """How many articles carried a GKG theme in the last 24 hours, counted
    over every article GDELT read rather than the rows kept. Raises when the
    copy is empty or stale."""
    await asyncio.to_thread(_check_fresh)
    return await asyncio.to_thread(store().volume, theme, time.time() - 86400)


class GdeltTrendingSource(trending.TrendingSource):
    """The Trending registry's GDELT feed, ranked by measured theme volume.

    **The question is templated, not generated** - see `TRENDING.md`; the
    story pool's GDELT source is the one that finds actual stories.
    """

    name = "GDELT"
    cost_per_refresh = 0.0
    timeout_seconds = 30.0

    def diagnose(self) -> tuple[bool, str]:
        ok, why = available()
        if not ok:
            return False, f"GDELT is switched off ({why})"
        return True, "GDELT export files, keyless; not verified from this machine"

    async def verify(self) -> tuple[bool, str]:
        age = await asyncio.to_thread(freshness)
        if age is None:
            return False, "GDELT's export copy is empty - no file has been read yet"
        if age > STALE_AFTER_SECONDS:
            return False, f"GDELT's export copy is {age / 3600:.1f}h old"
        held = await asyncio.to_thread(lambda: store().counts())
        return True, (f"GDELT's export copy holds {held['articles']} article(s) "
                      f"from {held['files']} file(s), newest {age / 60:.0f} min old")

    async def fetch(self, limit: int) -> list:
        measured = []
        for theme, subject in THEMES:
            try:
                volume = await volume_for(theme)
            except Exception as exc:  # noqa: BLE001 - one theme must not sink the sweep
                log.debug("gdelt: theme %s failed: %s", theme, exc)
                continue
            if volume > 0:
                measured.append((subject, theme, volume))
        if not measured:
            return []
        measured.sort(key=lambda row: -row[2])
        now = datetime.now(timezone.utc)
        return [trending.TrendingItem(
            subject=subject,
            query=f"what is actually driving the news about {subject} right now",
            why_now=f"coverage of {subject} is running high across global media",
            source=self.name, as_of=now, rank=rank + 1, kind=trending.ATTENTION)
            for rank, (subject, _theme, _v) in enumerate(measured[:limit or 6])]


def report() -> dict:
    ok, why = available()
    out = {"enabled": bool(settings.gdelt), "ready": ok, "detail": why,
           "source": "export files", "endpoint": settings.gdelt_export_base,
           "themes_swept": len(THEMES), "verified_from_this_machine": False,
           "keep_hours": float(settings.gdelt_export_keep_hours),
           **STATE.report()}
    try:
        if exists():
            out.update(store().counts())
            age = freshness()
            out["newest_age_seconds"] = None if age is None else round(age)
    except Exception:  # noqa: BLE001 - a report is never load-bearing
        pass
    return out
