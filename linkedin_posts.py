"""Go Deeper on LinkedIn: posts that teach something, and the episode that
goes further (`/admin/linkedin`).

## What this is

When somebody posts on LinkedIn to educate or inform, FAM adds a comment
with a link to an episode that goes deeper on the topic - systematically
adding depth for people already teaching something. A person at FAM
approves every comment and posts it from FAM's account, the same shape as
`/admin/x` (`x_threads.py`, §321), whose matching and drafting this reuses.

## Rules that are load-bearing

**A person finds, a person posts; FAM never does** ([no-social-posting]).
LinkedIn has no API that reads other people's posts, and its User Agreement
forbids scraping and automated comments. So nothing here fetches LinkedIn:
the page builds *search links* from the episodes FAM already has (a person
opens them in their own browser), and a person pastes a post they found.
Approve copies the comment and opens the post; the person pastes and posts.
FAM holds no LinkedIn credential.

**Starts from episodes FAM already has**, like X: the search links are the
Trending edition's and the most played episodes' names, so a post found
from one has its episode before it is found. A pasted post is matched
against every kept episode.

**Teaching, not announcing.** `screen` turns away announcements, hiring
and promotion, and posts that do not read as teaching; a person may add
one anyway (the episode may simply give more context), and the card says so.

**No episode, no link.** Under `x_threads.LINK_FLOOR` the card offers no
comment - it names the Go Deeper question an episode would answer, to be
searched in FAM, and "Match again" finds it once it is kept.

**A candidate carries its reason in words** ([candidate-reason]), and with
no model key the comment is drafted in code and the card says so.

**Kept a week.** A pasted post's text is pruned after `KEEP_DAYS`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Optional
from urllib.parse import quote, urlparse

import db as store_db
import config
import x_threads
from paths import data_path

log = logging.getLogger("fam.linkedin_posts")

SEARCH_URL = "https://www.linkedin.com/search/results/content/"

#: The share links this page makes are owned by this id, never a listener's.
SHARER = "fam-linkedin"

KEEP_DAYS = 7

#: LinkedIn's ceiling on a comment, and what the drafted words may use
#: before the Go Deeper line.
COMMENT_CHARS = 1250
DRAFT_CHARS = 500

#: Shorter than this and a post has not taught anything yet.
MIN_CHARS = 120

WAITING, APPROVED, SKIPPED = "waiting", "approved", "skipped"
STATUSES = (WAITING, APPROVED, SKIPPED)

#: How many kept episodes a card offers: the best, and two to switch to.
OFFERED = 3

#: Posts that announce or sell rather than teach.
_PROMO = re.compile(
    r"\b(thrilled to (announce|share)|excited to (announce|share)|"
    r"(happy|proud|humbled) to (announce|share)|i'?m hiring|we'?re hiring|"
    r"join our team|open roles?|apply (now|here)|i'?ve accepted|new position|"
    r"starting a new (role|position|job)|link in (the )?comments|dm me|"
    r"book a (call|demo)|register (now|here)|promo code|% off|giveaway|"
    r"work anniversary|i'?m open to work)\b", re.I)

#: How a post sets out to teach or inform.
_TEACH = re.compile(
    r"\b(here'?s (how|why|what)|how to|lessons?|learned|mistakes?|tips?|"
    r"framework|explain(s|ed)?|the reason|why it matters|step(s)? \d|"
    r"guide|myths?|misconceptions?|did you know|data shows|research|"
    r"stud(y|ies)|according to|the difference between|what most people|"
    r"in short|tl;?dr|breakdown|takeaways?|insights?|key points?|"
    r"\d+\s+(things|ways|lessons|reasons|tips|mistakes|steps|ideas))\b", re.I)
#: A list - three lines that start with a number, a bullet or an arrow.
_LIST_LINE = re.compile(r"^\s*(\d+[.)]|[-•→✅▶*])\s+\S", re.M)

_ACTIVITY = re.compile(r"(?:activity|share|ugcPost)[:-](\d{15,22})")
_URN = re.compile(r"urn:li:(activity|share|ugcPost):(\d{15,22})")


class NotAPost(ValueError):
    """What was pasted is not a LinkedIn post's address. Said in a sentence."""


# --------------------------------------------------------------------------
# A post's address


def parse_url(url: str) -> tuple[str, str, str]:
    """`(post_id, clean_url, author_slug)` from a LinkedIn post's address.

    Two shapes are posts: `/posts/<author>_<words>-activity-<id>-<code>` and
    `/feed/update/urn:li:activity:<id>/`. A short `lnkd.in` link is refused
    with what to do instead - following it would be FAM fetching LinkedIn.
    """
    raw = " ".join(str(url or "").split())
    if not raw:
        raise NotAPost("Paste the post's address.")
    if "://" not in raw:
        raw = "https://" + raw
    parts = urlparse(raw)
    host = (parts.hostname or "").lower()
    if host == "lnkd.in":
        raise NotAPost("That is a short link. Open it, then paste the address "
                       "from the browser bar.")
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        raise NotAPost("That is not a LinkedIn address.")
    path = parts.path or "/"
    found = _URN.search(path)
    if found:
        kind, digits = found.group(1), found.group(2)
        return (f"{kind}:{digits}",
                f"https://www.linkedin.com/feed/update/urn:li:{kind}:{digits}/", "")
    if path.startswith("/posts/"):
        found = _ACTIVITY.search(path)
        if found:
            slug = path[len("/posts/"):].strip("/")
            author = slug.split("_", 1)[0] if "_" in slug else ""
            kind = "ugcPost" if "ugcPost" in found.group(0) else (
                "share" if found.group(0).startswith("share") else "activity")
            return (f"{kind}:{found.group(1)}",
                    f"https://www.linkedin.com/posts/{slug}/", author.lower())
    raise NotAPost("That LinkedIn address is not a post. Use the post's "
                   "“Copy link to post” and paste that.")


def name_from_slug(slug: str) -> str:
    """"jane-doe-4b2a1" -> "Jane Doe": a profile slug's words, without the
    code LinkedIn adds to keep it unique."""
    words = [w for w in (slug or "").split("-") if w and not re.search(r"\d", w)]
    return " ".join(w.capitalize() for w in words)


# --------------------------------------------------------------------------
# Finding: search links a person opens, never a fetch


def search_url(names) -> str:
    """LinkedIn's own content search for posts naming any of `names`,
    newest first. Opened by a person in their own browser."""
    quoted = [f'"{n}"' if " " in n else n
              for n in (str(x).replace('"', "").strip() for x in names) if n]
    if not quoted:
        return ""
    keywords = " OR ".join(quoted)
    return (f"{SEARCH_URL}?keywords={quote(keywords)}"
            f"&sortBy={quote(chr(34) + 'date_posted' + chr(34))}")


def search_links(cache, *, trending=(), limit: int = 10) -> list[dict]:
    """One LinkedIn search per episode FAM already has, Trending first -
    `x_threads.seeds`, so both pages look for the same episodes."""
    out = []
    for seed in x_threads.seeds(cache, trending=trending, limit=limit):
        url = search_url(seed.names)
        if url:
            out.append({"title": seed.title, "names": list(seed.names),
                        "origin": seed.origin, "key": seed.key,
                        "minutes": seed.minutes, "url": url})
    return out


# --------------------------------------------------------------------------
# Choosing


def teaches(text: str) -> bool:
    text = text or ""
    return bool(_TEACH.search(text)) or len(_LIST_LINE.findall(text)) >= 3


def screen(text: str) -> str:
    """Why this post is not one to Go Deeper on, or "" when it is. Code only."""
    text = " ".join(str(text or "").split()) if text else ""
    if len(text) < MIN_CHARS:
        return "too short to have taught anything yet"
    low = text.lower()
    if any(re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", low)
           for w in x_threads.AVOID):
        return "a subject FAM does not walk into"
    try:
        import content_filter

        if content_filter.scrub(text) != text:
            return "carries a slur"
    except Exception:  # noqa: BLE001
        pass
    if _PROMO.search(text):
        return "an announcement or a promotion, not teaching"
    return ""


def screen_post(text: str) -> str:
    """`screen`, plus whether it reads as teaching at all. Separate because
    the list structure is read from the original line breaks."""
    why = screen(text)
    if why:
        return why
    if not teaches(text):
        return "does not read as teaching or informing"
    return ""


def episodes_for(cache, text: str, *, limit: int = OFFERED) -> list[dict]:
    """The kept episodes most about what the post is about, best first, each
    with its match (0-1): the X page's `match` - the cache's embeddings plus
    the episode's names - against every kept episode, never an explicit one."""
    import embeddings

    rows = []
    try:
        rows = cache.recent(limit=200) if cache is not None else []
    except Exception as exc:  # noqa: BLE001 - a match never raises
        log.warning("linkedin: could not read the cache: %s", exc)
    try:
        post_vector = embeddings.embed(text)
    except Exception:  # noqa: BLE001
        post_vector = None
    low = (text or "").lower()
    scored = []
    for row in rows:
        if row.get("explicit") or not row.get("query"):
            continue
        title = row.get("title") or row["query"]
        names = x_threads.names_for(title, row["query"])
        semantic = 0.0
        if post_vector is not None:
            try:
                semantic = max(0.0, embeddings.cosine(
                    post_vector, embeddings.embed(f"{title}. {row['query']}")))
            except Exception:  # noqa: BLE001
                semantic = 0.0
        named = (sum(1 for n in names if n.lower() in low) / len(names)) if names else 0.0
        score = round(min(1.0, 0.6 * semantic + 0.4 * named), 4)
        scored.append({"key": row["key"], "query": row["query"], "title": title,
                       "minutes": int(row.get("minutes") or 1),
                       "plays": int(row.get("plays") or 0), "match": score})
    scored.sort(key=lambda e: (-e["match"], -e["plays"]))
    return scored[:limit]


def linked(episode: Optional[dict]) -> bool:
    return bool(episode) and float(episode.get("match") or 0) >= x_threads.LINK_FLOOR


def reason(text: str, episode: Optional[dict], screened: str) -> str:
    """Why this card is on the page, in words ([candidate-reason])."""
    bits = []
    if screened:
        bits.append(f"added anyway: {screened}")
    elif teaches(text):
        bits.append("reads as teaching")
    if linked(episode):
        bits.append(f"goes deeper with “{episode['title']}” "
                    f"({int(episode['match'] * 100)}% match)")
    elif episode:
        bits.append(f"closest kept episode, “{episode['title']}”, is not its "
                    f"subject ({int(episode['match'] * 100)}%), so no link yet")
    else:
        bits.append("no kept episode to match yet")
    return "; ".join(bits) + "."


# --------------------------------------------------------------------------
# Drafting


DRAFT_SYSTEM = """You draft one LinkedIn comment for FAM, an app that turns \
what people care about into short audio stories. The comment goes under \
somebody else's post that teaches or explains something. A person at FAM \
reads it and posts it themselves.

How FAM comments:
- Add to their point; do not praise it. Open with something specific the \
episode goes further on: a distinction, the context behind their point, or \
what it leaves open. Two or three sentences, plain and professional.
- Never "great post", "love this", "thanks for sharing", never hashtags, \
emoji, exclamation marks or "check out".
- Never state a number, date, name or result unless it is in the post or \
the episode title. Never claim to know the author.
- Do not include a link or the episode's title: a line "Go Deeper: <title> \
<link>" is added after your words.
- At most {chars} characters.

Also write `question`: the Go Deeper question a listener would ask FAM \
after reading this post - one line, as somebody would type it."""

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {"comment": {"type": "string"}, "question": {"type": "string"}},
    "required": ["comment", "question"],
    "additionalProperties": False,
}


def draft_prompt(text: str, episode: Optional[dict]) -> str:
    lines = ["The post:", (text or "").strip(), ""]
    if linked(episode):
        lines.append(f"FAM's episode: “{episode['title']}” "
                     f"(the question it answers: {episode['query']})")
    else:
        lines.append("FAM has no episode on this yet. Leave `comment` empty "
                     "and write only the question.")
    return "\n".join(lines)


def fallback_comment(episode: Optional[dict]) -> str:
    """The comment drafted in code, when no model can be asked. Plain and
    honest; the card says it was written this way."""
    if not linked(episode):
        return ""
    return ("This is worth more than a post allows. We went deeper on it in "
            "a short audio episode, for anyone who wants the fuller picture.")


async def draft(text: str, episode: Optional[dict],
                client=None) -> tuple[str, str, str, float]:
    """`(comment, question, drafted_by, dollars)`. Never raises but
    cancellation: a model that cannot be asked leaves the code's comment."""
    import credentials
    import metering

    if client is None and not credentials.active("ANTHROPIC_API_KEY"):
        return x_threads.trim(fallback_comment(episode), DRAFT_CHARS), "", "code", 0.0
    model = config.settings.linkedin_model
    try:
        if client is None:
            from anthropic_client import build_async_client

            client = build_async_client(credentials.active("ANTHROPIC_API_KEY"))
        response = await client.messages.create(
            model=model, max_tokens=800,
            system=DRAFT_SYSTEM.format(chars=DRAFT_CHARS),
            output_config={"format": {"type": "json_schema", "schema": DRAFT_SCHEMA}},
            messages=[{"role": "user", "content": draft_prompt(text, episode)}])
        usage = metering.Usage()
        usage.add_model_call(model, getattr(response, "usage", None))
        dollars = metering.price_of(usage).total
        body = json.loads(next(b.text for b in response.content if b.type == "text"))
        comment = x_threads.trim(body.get("comment") or "", DRAFT_CHARS) \
            if linked(episode) else ""
        question = " ".join(str(body.get("question") or "").split())[:200]
        if linked(episode) and not comment:
            raise ValueError("an empty comment")
        return comment, question, model, dollars
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("linkedin: drafting fell back to code: %s", exc)
        return x_threads.trim(fallback_comment(episode), DRAFT_CHARS), "", "code", 0.0


class TooLong(ValueError):
    """The comment is longer than LinkedIn allows. Said, never cut."""


def go_deeper_line(episode: dict, link: str) -> str:
    return f"Go Deeper: “{episode['title']}” ({int(episode.get('minutes') or 1)} min) {link}"


def compose(words: str, episode: dict, link: str) -> str:
    """The comment: the person's words, a blank line, the Go Deeper line.
    Line breaks are the person's and kept; raises `TooLong` rather than
    shortening words somebody approved."""
    words = "\n".join(" ".join(line.split()) for line in str(words or "").strip().splitlines())
    words = re.sub(r"\n{3,}", "\n\n", words)
    text = f"{words}\n\n{go_deeper_line(episode, link)}" if words else go_deeper_line(episode, link)
    if len(text) > COMMENT_CHARS:
        raise TooLong(f"The comment is {len(text)} characters with the Go Deeper "
                      f"line; LinkedIn allows {COMMENT_CHARS}.")
    return text


# --------------------------------------------------------------------------
# Storage


@dataclass
class Card:
    post_id: str
    url: str
    text: str
    author: str = ""            # the profile slug, for one author a week
    author_name: str = ""
    screened: str = ""          # why `screen` would have turned it away
    episodes: list = field(default_factory=list)
    chosen: int = 0
    reason: str = ""
    draft: str = ""
    question: str = ""
    drafted_by: str = ""

    @property
    def episode(self) -> Optional[dict]:
        if 0 <= self.chosen < len(self.episodes):
            return self.episodes[self.chosen]
        return None


class Store:
    def __init__(self, path: str | None = None) -> None:
        self.path = data_path("LINKEDIN_POSTS_DB", "linkedin_posts.db", path)
        self._local = threading.local()
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS posts (
                       post_id     TEXT PRIMARY KEY,
                       added_at    REAL NOT NULL,
                       url         TEXT NOT NULL,
                       text        TEXT NOT NULL,
                       author      TEXT NOT NULL DEFAULT '',
                       author_name TEXT NOT NULL DEFAULT '',
                       screened    TEXT NOT NULL DEFAULT '',
                       episodes    TEXT NOT NULL DEFAULT '[]',
                       chosen      INTEGER NOT NULL DEFAULT 0,
                       reason      TEXT NOT NULL DEFAULT '',
                       draft       TEXT NOT NULL DEFAULT '',
                       question    TEXT NOT NULL DEFAULT '',
                       drafted_by  TEXT NOT NULL DEFAULT '',
                       status      TEXT NOT NULL DEFAULT 'waiting',
                       decided_at  REAL NOT NULL DEFAULT 0,
                       posted      TEXT NOT NULL DEFAULT '',
                       share_url   TEXT NOT NULL DEFAULT ''
                   )""")
            conn.execute("CREATE INDEX IF NOT EXISTS posts_status"
                         " ON posts(status, added_at)")

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = store_db.connect(self.path, timeout=5.0, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    def put(self, card: Card, at: float) -> None:
        """Write a card, or rewrite it (a new match keeps its verdict)."""
        conn = self._conn()
        values = (card.url, card.text, card.author, card.author_name, card.screened,
                  json.dumps(card.episodes), int(card.chosen), card.reason,
                  card.draft, card.question, card.drafted_by)
        cur = conn.execute(
            "UPDATE posts SET url = ?, text = ?, author = ?, author_name = ?,"
            " screened = ?, episodes = ?, chosen = ?, reason = ?, draft = ?,"
            " question = ?, drafted_by = ? WHERE post_id = ?",
            values + (card.post_id,))
        if cur.rowcount == 0:
            conn.execute(
                "INSERT INTO posts (url, text, author, author_name, screened,"
                " episodes, chosen, reason, draft, question, drafted_by,"
                " post_id, added_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values + (card.post_id, at))

    def card(self, post_id: str) -> Optional[Card]:
        row = self._conn().execute(
            "SELECT post_id, url, text, author, author_name, screened, episodes,"
            " chosen, reason, draft, question, drafted_by FROM posts"
            " WHERE post_id = ?", (post_id,)).fetchone()
        if not row:
            return None
        return Card(row[0], row[1], row[2], row[3], row[4], row[5],
                    json.loads(row[6] or "[]"), int(row[7]), row[8], row[9],
                    row[10], row[11])

    def get(self, post_id: str) -> Optional[dict]:
        rows = self.all(post_id=post_id)
        return rows[0] if rows else None

    def all(self, status: str = "", post_id: str = "", limit: int = 100) -> list[dict]:
        sql = ("SELECT post_id, added_at, url, text, author, author_name, screened,"
               " episodes, chosen, reason, draft, question, drafted_by, status,"
               " decided_at, posted, share_url FROM posts WHERE 1=1")
        args: list = []
        if status:
            sql += " AND status = ?"
            args.append(status)
        if post_id:
            sql += " AND post_id = ?"
            args.append(post_id)
        sql += (" ORDER BY CASE status WHEN 'waiting' THEN 0 ELSE 1 END,"
                " added_at DESC LIMIT ?")
        args.append(int(limit))
        out = []
        for r in self._conn().execute(sql, args).fetchall():
            episodes = json.loads(r[7] or "[]")
            for e in episodes:
                e["linked"] = linked(e)
            chosen = int(r[8])
            episode = episodes[chosen] if 0 <= chosen < len(episodes) else None
            out.append({
                "post_id": r[0], "added_at": r[1], "url": r[2], "text": r[3],
                "author": r[4], "author_name": r[5], "screened": r[6],
                "episodes": episodes, "chosen": chosen, "episode": episode,
                "linked": linked(episode), "reason": r[9], "draft": r[10],
                "question": r[11], "drafted_by": r[12], "status": r[13],
                "decided_at": r[14], "posted": r[15], "share_url": r[16]})
        return out

    def decide(self, post_id: str, status: str, at: float, *, posted: str = "",
               share_url: str = "", chosen: Optional[int] = None) -> bool:
        sql = "UPDATE posts SET status = ?, decided_at = ?, posted = ?, share_url = ?"
        args: list = [status, at, posted, share_url]
        if chosen is not None:
            sql += ", chosen = ?"
            args.append(int(chosen))
        cur = self._conn().execute(sql + " WHERE post_id = ?", args + [post_id])
        return cur.rowcount > 0

    def recent_author(self, author: str, since: float, *, besides: str = "") -> bool:
        """Whether `author` has a card waiting, or was commented on since
        `since`: one author a week, as on X."""
        if not author:
            return False
        row = self._conn().execute(
            "SELECT 1 FROM posts WHERE author = ? AND post_id != ? AND"
            " (status = ? OR (status = ? AND decided_at >= ?)) LIMIT 1",
            (author, besides, WAITING, APPROVED, since)).fetchone()
        return row is not None

    def prune(self, now: float) -> int:
        cur = self._conn().execute("DELETE FROM posts WHERE added_at < ?",
                                   (now - KEEP_DAYS * 86400,))
        return cur.rowcount

    def counts(self) -> dict:
        rows = self._conn().execute(
            "SELECT status, COUNT(*) FROM posts GROUP BY status").fetchall()
        return {s: n for s, n in rows}


_STORE: list = []
_STORE_LOCK = threading.Lock()


def _exists() -> bool:
    """Whether there is a store to read, without creating one."""
    if _STORE:
        return True
    try:
        return store_db.has_tables(data_path("LINKEDIN_POSTS_DB", "linkedin_posts.db"))
    except Exception:  # noqa: BLE001
        return False


def store() -> Store:
    with _STORE_LOCK:
        if not _STORE:
            _STORE.append(Store())
        return _STORE[0]


def reset(new: Optional[Store] = None) -> None:
    with _STORE_LOCK:
        _STORE.clear()
        if new is not None:
            _STORE.append(new)


# --------------------------------------------------------------------------
# Adding and matching


class Refused(ValueError):
    """A post the screen turned away; `anyway` adds it regardless."""


async def add(cache, *, url: str, text: str, author_name: str = "",
              anyway: bool = False, now: Optional[float] = None,
              drafter=None, held: Optional[Store] = None) -> dict:
    """Screen a pasted post, match it against the kept episodes, draft the
    comment and leave the card on the page. Raises `NotAPost` for an address
    that is not a post and `Refused` (with the reason) for one the screen
    turns away, unless `anyway`."""
    now = time.time() if now is None else now
    held = held or store()
    held.prune(now)
    post_id, clean, author = parse_url(url)
    text = str(text or "").strip()
    if not text:
        raise Refused("Paste the post's words as well: FAM does not read LinkedIn.")
    screened = screen_post(text)
    if not screened and held.recent_author(author, now - 7 * 86400, besides=post_id):
        screened = "this author is on the page or was commented on this week"
    if screened and not anyway:
        raise Refused(screened)
    card = Card(post_id, clean, text[:6000], author,
                " ".join(str(author_name or "").split())[:120] or name_from_slug(author),
                screened)
    await _match(cache, card, drafter)
    held.put(card, now)
    return held.get(post_id) or {}


async def rematch(cache, post_id: str, *, drafter=None,
                  held: Optional[Store] = None) -> dict:
    """Match a card again - after the Go Deeper episode it needed was made."""
    held = held or store()
    card = held.card(post_id)
    if card is None:
        raise KeyError(post_id)
    await _match(cache, card, drafter)
    held.put(card, time.time())
    return held.get(post_id) or {}


async def _match(cache, card: Card, drafter=None) -> None:
    # Embedding every kept episode is CPU work; never on the loop that
    # streams listeners' audio.
    card.episodes = await asyncio.to_thread(episodes_for, cache, card.text)
    card.chosen = 0
    card.reason = reason(card.text, card.episode, card.screened)
    card.draft, card.question, card.drafted_by, dollars = await (drafter or draft)(
        card.text, card.episode)
    if dollars:
        log.info("linkedin: drafted for $%.4f", dollars)


def health() -> dict:
    out = {"available": True,
           "detail": "nothing here reads LinkedIn: a person finds and pastes posts"}
    if not _exists():
        out["counts"] = {}
        return out
    try:
        out["counts"] = store().counts()
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
    return out
