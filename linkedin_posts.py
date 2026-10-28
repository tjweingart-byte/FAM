"""Go Deeper on LinkedIn: posts that teach something, and the episode that
goes further (`/admin/linkedin`).

## What this is

When somebody posts on LinkedIn to educate or inform, FAM adds a comment
with a link to an episode that goes deeper on the topic - systematically
adding depth for people already teaching something. A person at FAM
approves every comment and posts it from FAM's account, the same shape as
`/admin/x` (`x_threads.py`, §321), whose matching and drafting this reuses.

## Rules that are load-bearing

**Code finds, a person posts; FAM never does** ([no-social-posting]).
LinkedIn has no API that reads other people's posts, and its User Agreement
forbids scraping and automated comments. So nothing here fetches
linkedin.com and FAM holds no LinkedIn credential. **Find posts** (§327)
asks the web search FAM's research already pays for (Parallel, else
Perplexity), restricted to linkedin.com, for public posts from the last
week - the search vendor's index, not LinkedIn. A person may also paste a
post. Approve copies the comment and opens the post; the person pastes and
posts.

**Starts from episodes FAM already has**, like X: a run searches for the
Trending edition's and the most played episodes' names (`x_threads.seeds`),
so a post found from one has its episode before it is found. Every post is
ranked against every kept episode, and a found post no episode is the
subject of is not a card. A run is a button, never a timer
([sweep-on-demand]), capped per press and per day.

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
from urllib.parse import quote, unquote, urlparse

import db as store_db
import config
import x_threads
from paths import data_path
from config import as_minutes

log = logging.getLogger("fam.linkedin_posts")

SEARCH_URL = "https://www.linkedin.com/search/results/content/"

#: The share links this page makes are owned by this id, never a listener's.
SHARER = "fam-linkedin"

KEEP_DAYS = 7
#: How long a pruned post's id is remembered (`seen`): past the widest
#: search window (`LINKEDIN_WINDOW_DAYS`, at most 30), so a search can never
#: bring back a post a person already approved, skipped or had set aside.
SEEN_DAYS = 45

#: LinkedIn's ceiling on a comment, and what the drafted words may use
#: before the Go Deeper line.
COMMENT_CHARS = 1250
DRAFT_CHARS = 500

#: Shorter than this and a post has not taught anything yet.
MIN_CHARS = 120

WAITING, APPROVED, SKIPPED = "waiting", "approved", "skipped"
#: How a card came to be on the page.
FOUND, PASTED = "found", "pasted"
#: A found post the drafting call judged to be about something else (as
#: `x_threads.SET_ASIDE`, §326): kept only so the next run neither reads
#: nor judges it again, and never shown.
SET_ASIDE = "set_aside"
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
#: What a profile-and-words slug in `/posts/<slug>/` may hold.
_SLUG = re.compile(r"^[A-Za-z0-9_%\-]+$")


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
    # LinkedIn's search results hand out the URN percent-encoded
    # (`urn%3Ali%3Aactivity%3A...`); the clean address is rebuilt from the
    # digits, so decoding here admits nothing new.
    path = unquote(parts.path or "/")
    found = _URN.search(path)
    if found:
        kind, digits = found.group(1), found.group(2)
        return (f"{kind}:{digits}",
                f"https://www.linkedin.com/feed/update/urn:li:{kind}:{digits}/", "")
    if path.startswith("/posts/"):
        found = _ACTIVITY.search(path)
        if found:
            slug = (parts.path or "/")[len("/posts/"):].strip("/")
            if not _SLUG.match(slug):
                raise NotAPost("That LinkedIn address is not a post. Use the "
                               "post's “Copy link to post” and paste that.")
            author = slug.split("_", 1)[0] if "_" in slug else ""
            kind = "ugcPost" if "ugcPost" in found.group(0) else (
                "share" if found.group(0).startswith("share") else "activity")
            return (f"{kind}:{found.group(1)}",
                    f"https://www.linkedin.com/posts/{slug}/", author.lower())
    raise NotAPost("That LinkedIn address is not a post. Use the post's "
                   "“Copy link to post” and paste that.")


def author_key(slug: str, author_name: str) -> str:
    """Who a post is by, for one author a week: the profile slug when the
    address carries one, else the typed name - a `/feed/update/` address
    names nobody, and a check that quietly skips it is no check."""
    if slug:
        return slug.lower()
    name = " ".join(str(author_name or "").lower().split())
    return f"name:{name}" if name else ""


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


def catalogue(cache) -> list[dict]:
    """The episodes a post can be matched to: the newest 200 kept, never an
    explicit one, each with its names and its vector worked out once - a
    search run matches dozens of posts against the same episodes."""
    import embeddings

    rows = []
    try:
        rows = cache.recent(limit=200) if cache is not None else []
    except Exception as exc:  # noqa: BLE001 - a match never raises
        log.warning("linkedin: could not read the cache: %s", exc)
    out = []
    for row in rows:
        if row.get("explicit") or not row.get("query"):
            continue
        title = row.get("title") or row["query"]
        try:
            vector = embeddings.embed(f"{title}. {row['query']}")
        except Exception:  # noqa: BLE001
            vector = None
        out.append({"key": row["key"], "query": row["query"], "title": title,
                    "minutes": as_minutes(row.get("minutes") or 1),
                    "plays": int(row.get("plays") or 0),
                    "names": x_threads.names_for(title, row["query"]),
                    "vector": vector})
    return out


def rank(episodes: list[dict], text: str, *, limit: int = OFFERED) -> list[dict]:
    """`episodes` (a `catalogue`) most about what the post is about, best
    first, each with its match (0-1): the X page's `match` - embeddings 0.6,
    the episode's names 0.4. A name counts only as a whole word: a post runs
    to thousands of characters, and "Fed" is inside "federal"."""
    import embeddings

    try:
        post_vector = embeddings.embed(text)
    except Exception:  # noqa: BLE001
        post_vector = None
    low = (text or "").lower()
    scored = []
    for e in episodes:
        semantic = 0.0
        if post_vector is not None and e.get("vector") is not None:
            try:
                semantic = max(0.0, embeddings.cosine(post_vector, e["vector"]))
            except Exception:  # noqa: BLE001
                semantic = 0.0
        names = e.get("names") or ()
        named = (sum(1 for n in names if re.search(
            r"(?<!\w)" + re.escape(n.lower()) + r"(?!\w)", low)) / len(names)) if names else 0.0
        scored.append({"key": e["key"], "query": e["query"], "title": e["title"],
                       "minutes": e["minutes"], "plays": e["plays"],
                       "match": round(min(1.0, 0.6 * semantic + 0.4 * named), 4),
                       "off_subject": off_subject(text, e)})
    scored.sort(key=lambda e: (-e["match"], -e["plays"]))
    return scored[:limit]


def off_subject(text: str, episode: dict) -> str:
    """Why the post is not about the episode's subject, or "" when it is:
    the X page's `on_subject` (§326) - it must name what the episode names,
    and a one-word name must come with one of the episode's own subject
    words. "United" is not Manchester United; a shared word is not a shared
    subject. An episode with no names to check is judged by the match alone."""
    names = tuple(episode.get("names") or ())
    if not names:
        return ""
    seed = x_threads.Seed(episode.get("key", ""), episode.get("query", ""),
                          episode.get("title", ""), as_minutes(episode.get("minutes") or 1), names)
    return x_threads.on_subject(_Text(text), seed)


@dataclass
class _Text:
    """What `x_threads.on_subject` reads of a post: its words."""
    text: str


def episodes_for(cache, text: str, *, limit: int = OFFERED) -> list[dict]:
    """The kept episodes most about a post, best first (`rank` over the
    cache's `catalogue`)."""
    return rank(catalogue(cache), text, limit=limit)


def linked(episode: Optional[dict]) -> bool:
    """Whether an episode may be linked under the post: it matches, and
    neither the subject check nor the drafting call found it off-subject."""
    return (bool(episode) and float(episode.get("match") or 0) >= x_threads.LINK_FLOOR
            and not episode.get("off_subject"))


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
    elif episode and episode.get("off_subject"):
        bits.append(f"closest kept episode, “{episode['title']}”, is not its "
                    f"subject ({episode['off_subject']}), so no link yet")
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

First, when an episode is given, judge - strictly, because an off-topic \
comment is spam - `fits`: is the post about the same subject as the \
episode (the same company, person, team, idea or question, not merely a \
shared word or name: Apple the company is not apples, a United Airlines \
flight is not Manchester United), and would somebody who read this post \
want this exact episode? When unsure, false; say why in `why`. With no \
episode, `fits` is true. When `fits` is false, leave `comment` empty.

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
    "properties": {"fits": {"type": "boolean"}, "why": {"type": "string"},
                   "comment": {"type": "string"}, "question": {"type": "string"}},
    "required": ["fits", "why", "comment", "question"],
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
    """`(comment, question, drafted_by, dollars)`, for a caller that wants
    the words only (`judge_and_draft` is what the page uses)."""
    comment, question, by, dollars, _misfit = await judge_and_draft(text, episode, client)
    return comment, question, by, dollars


async def judge_and_draft(text: str, episode: Optional[dict],
                          client=None) -> tuple[str, str, str, float, str]:
    """`(comment, question, drafted_by, dollars, misfit)`: one call that
    first judges whether the post and the episode are about the same thing
    (`misfit` says why not, "" when they are), then writes the comment.
    Never raises but cancellation: a model that cannot be asked leaves the
    code's comment and the code's gate (`off_subject`), said on the card."""
    import credentials
    import metering

    if client is None and not credentials.active("ANTHROPIC_API_KEY"):
        return x_threads.trim(fallback_comment(episode), DRAFT_CHARS), "", "code", 0.0, ""
    model = config.settings.linkedin_model
    dollars = 0.0
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
        question = " ".join(str(body.get("question") or "").split())[:200]
        if linked(episode) and body.get("fits") is False:
            why = " ".join(str(body.get("why") or "").split())[:200]
            return "", question, model, dollars, why or "the post is about something else"
        comment = x_threads.trim(body.get("comment") or "", DRAFT_CHARS) \
            if linked(episode) else ""
        if linked(episode) and not comment:
            raise ValueError("an empty comment")
        return comment, question, model, dollars, ""
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("linkedin: drafting fell back to code: %s", exc)
        return (x_threads.trim(fallback_comment(episode), DRAFT_CHARS), "", "code",
                dollars, "")


async def _judged(drafter, text: str, episode: Optional[dict]) -> tuple:
    """A drafter's answer as `(comment, question, by, dollars, misfit)`: a
    stand-in that only drafts (four values) never judges."""
    out = tuple(await (drafter or judge_and_draft)(text, episode))
    return out if len(out) == 5 else out + ("",)


def mark_misfit(card: "Card", misfit: str) -> None:
    """Fold the drafting call's "not about this" into the card: the episode
    is off-subject, so the card has no comment and says why."""
    episode = card.episode
    if episode is not None:
        episode["off_subject"] = f"checked: {misfit}"
    card.draft = ""
    card.reason = reason(card.text, card.episode, card.screened)


class TooLong(ValueError):
    """The comment is longer than LinkedIn allows. Said, never cut."""


def go_deeper_line(episode: dict, link: str) -> str:
    return f"Go Deeper: “{episode['title']}” ({as_minutes(episode.get('minutes') or 1)} min) {link}"


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
    dollars: float = 0.0        # what drafting this card has cost, every match
    origin: str = PASTED        # FOUND by a search run, or PASTED by a person
    found_by: str = ""          # the episode whose search found it

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
                       dollars     REAL NOT NULL DEFAULT 0,
                       origin      TEXT NOT NULL DEFAULT 'pasted',
                       found_by    TEXT NOT NULL DEFAULT '',
                       status      TEXT NOT NULL DEFAULT 'waiting',
                       decided_at  REAL NOT NULL DEFAULT 0,
                       posted      TEXT NOT NULL DEFAULT '',
                       share_url   TEXT NOT NULL DEFAULT ''
                   )""")
            conn.execute("CREATE INDEX IF NOT EXISTS posts_status"
                         " ON posts(status, added_at)")
            # The ids of pruned posts, words gone: what stops a later search
            # carding a post FAM already commented on (§327).
            conn.execute(
                """CREATE TABLE IF NOT EXISTS seen (
                       post_id TEXT PRIMARY KEY,
                       at      REAL NOT NULL
                   )""")
            # One row per "Find posts" run: what it asked, what it cost, and
            # how the daily search ceiling is counted.
            conn.execute(
                """CREATE TABLE IF NOT EXISTS runs (
                       at       REAL NOT NULL,
                       searches INTEGER NOT NULL DEFAULT 0,
                       dollars  REAL NOT NULL DEFAULT 0,
                       report   TEXT NOT NULL DEFAULT '{}'
                   )""")

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
                  card.draft, card.question, card.drafted_by, float(card.dollars),
                  card.origin, card.found_by)
        cur = conn.execute(
            "UPDATE posts SET url = ?, text = ?, author = ?, author_name = ?,"
            " screened = ?, episodes = ?, chosen = ?, reason = ?, draft = ?,"
            " question = ?, drafted_by = ?, dollars = ?, origin = ?, found_by = ?"
            " WHERE post_id = ?",
            values + (card.post_id,))
        if cur.rowcount == 0:
            conn.execute(
                "INSERT INTO posts (url, text, author, author_name, screened,"
                " episodes, chosen, reason, draft, question, drafted_by, dollars,"
                " origin, found_by, post_id, added_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values + (card.post_id, at))

    def card(self, post_id: str) -> Optional[Card]:
        row = self._conn().execute(
            "SELECT post_id, url, text, author, author_name, screened, episodes,"
            " chosen, reason, draft, question, drafted_by, dollars, origin, found_by"
            " FROM posts WHERE post_id = ?", (post_id,)).fetchone()
        if not row:
            return None
        return Card(row[0], row[1], row[2], row[3], row[4], row[5],
                    json.loads(row[6] or "[]"), int(row[7]), row[8], row[9],
                    row[10], row[11], float(row[12] or 0), row[13], row[14])

    def get(self, post_id: str) -> Optional[dict]:
        rows = self.all(post_id=post_id)
        return rows[0] if rows else None

    def all(self, status: str = "", post_id: str = "", limit: int = 100) -> list[dict]:
        sql = ("SELECT post_id, added_at, url, text, author, author_name, screened,"
               " episodes, chosen, reason, draft, question, drafted_by, status,"
               " decided_at, posted, share_url, dollars, origin, found_by"
               " FROM posts WHERE status != 'set_aside'")
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
                "decided_at": r[14], "posted": r[15], "share_url": r[16],
                "dollars": float(r[17] or 0), "origin": r[18], "found_by": r[19]})
        return out

    def decide(self, post_id: str, status: str, at: float, *, posted: str = "",
               share_url: str = "", chosen: Optional[int] = None) -> bool:
        """A verdict. Only an approval writes what was posted: Skip or Back
        to waiting after one keeps the record of the comment that may be on
        LinkedIn now."""
        sql = "UPDATE posts SET status = ?, decided_at = ?"
        args: list = [status, at]
        if status == APPROVED:
            sql += ", posted = ?, share_url = ?"
            args += [posted, share_url]
        if chosen is not None:
            sql += ", chosen = ?"
            args.append(int(chosen))
        cur = self._conn().execute(sql + " WHERE post_id = ?", args + [post_id])
        return cur.rowcount > 0

    def recent_author(self, author: str, since: float, *, besides: str = "",
                      name: str = "") -> bool:
        """Whether `author` (or somebody of the same `name`) has a card
        waiting, or was commented on since `since`: one author a week, as on
        X. By name too, because one address shape carries a profile slug and
        the other only the name somebody typed."""
        name = " ".join(str(name or "").lower().split())
        if not author and not name:
            return False
        row = self._conn().execute(
            "SELECT 1 FROM posts WHERE (author = ? OR LOWER(author_name) = ?)"
            " AND post_id != ? AND (status = ? OR (status = ? AND decided_at >= ?))"
            " LIMIT 1",
            (author or None, name or None, besides, WAITING, APPROVED, since)).fetchone()
        return row is not None

    def status_of(self, post_id: str) -> str:
        """A post's status, set-aside included, or "" when it has no row."""
        row = self._conn().execute("SELECT status FROM posts WHERE post_id = ?",
                                   (post_id,)).fetchone()
        return row[0] if row else ""

    def find_text(self, text: str, *, besides: str = "") -> Optional[dict]:
        """A card already holding these words: one post reached as
        `activity:` in one address and `ugcPost:` in another is one post."""
        wanted = " ".join(str(text or "").split())
        for row in self._conn().execute(
                "SELECT post_id, text, status FROM posts WHERE post_id != ?",
                (besides,)).fetchall():
            if " ".join(str(row[1] or "").split()) == wanted:
                return {"post_id": row[0], "status": row[2]}
        return None

    def prune(self, now: float) -> int:
        """Cards past `KEEP_DAYS`; an approved one is kept until a week
        after its approval, or pruning would shorten one author a week."""
        cutoff = now - KEEP_DAYS * 86400
        where = "added_at < ? AND NOT (status = ? AND decided_at >= ?)"
        conn = self._conn()
        for (post_id,) in conn.execute(f"SELECT post_id FROM posts WHERE {where}",
                                       (cutoff, APPROVED, cutoff)).fetchall():
            conn.execute("INSERT OR IGNORE INTO seen (post_id, at) VALUES (?, ?)",
                         (post_id, now))
        cur = conn.execute(f"DELETE FROM posts WHERE {where}", (cutoff, APPROVED, cutoff))
        conn.execute("DELETE FROM seen WHERE at < ?", (now - SEEN_DAYS * 86400,))
        conn.execute("DELETE FROM runs WHERE at < ?", (now - 30 * 86400,))
        return cur.rowcount

    def dollars(self, now: Optional[float] = None) -> float:
        """Searching and drafting over the last seven days: the cards added
        and the runs pressed since then, one window for both."""
        since = (time.time() if now is None else now) - 7 * 86400
        drafted = self._conn().execute(
            "SELECT COALESCE(SUM(dollars), 0) FROM posts WHERE added_at >= ?",
            (since,)).fetchone()[0]
        searched = self._conn().execute(
            "SELECT COALESCE(SUM(dollars), 0) FROM runs WHERE at >= ?",
            (since,)).fetchone()[0]
        return round(float(drafted or 0) + float(searched or 0), 4)

    def known(self) -> set:
        """Every post on the page, set aside, or pruned within `SEEN_DAYS`."""
        conn = self._conn()
        return ({r[0] for r in conn.execute("SELECT post_id FROM posts")}
                | {r[0] for r in conn.execute("SELECT post_id FROM seen")})

    def insert_new(self, card: "Card", at: float, status: str = WAITING) -> bool:
        """Write a run's card only when the post has no row, its status in
        the same statement: a search never rewrites a card a person pasted
        or decided while it ran. False when the row was already there."""
        cur = self._conn().execute(
            "INSERT OR IGNORE INTO posts (url, text, author, author_name, screened,"
            " episodes, chosen, reason, draft, question, drafted_by, dollars,"
            " origin, found_by, post_id, added_at, status, decided_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (card.url, card.text, card.author, card.author_name, card.screened,
             json.dumps(card.episodes), int(card.chosen), card.reason, card.draft,
             card.question, card.drafted_by, float(card.dollars), card.origin,
             card.found_by, card.post_id, at, status,
             at if status != WAITING else 0.0))
        return cur.rowcount > 0

    # -- runs

    def record_run(self, at: float, searches: int, dollars: float, report: dict) -> None:
        """Write a run's row, or rewrite it: written before its first search
        and after each, so what was searched is counted even when the run
        dies before it finishes (as `/admin/x` does)."""
        cur = self._conn().execute(
            "UPDATE runs SET searches = ?, dollars = ?, report = ? WHERE at = ?",
            (int(searches), float(dollars), json.dumps(report), at))
        if cur.rowcount == 0:
            self._conn().execute(
                "INSERT INTO runs (at, searches, dollars, report) VALUES (?, ?, ?, ?)",
                (at, int(searches), float(dollars), json.dumps(report)))

    def searches_since(self, since: float) -> int:
        row = self._conn().execute("SELECT COALESCE(SUM(searches), 0) FROM runs"
                                   " WHERE at >= ?", (since,)).fetchone()
        return int(row[0] or 0)

    def last_run(self) -> Optional[dict]:
        row = self._conn().execute("SELECT at, searches, dollars, report FROM runs"
                                   " ORDER BY at DESC LIMIT 1").fetchone()
        if not row:
            return None
        return {"at": row[0], "searches": row[1], "dollars": row[2],
                **json.loads(row[3] or "{}")}

    def counts(self) -> dict:
        rows = self._conn().execute(
            "SELECT status, COUNT(*) FROM posts WHERE status != 'set_aside'"
            " GROUP BY status").fetchall()
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
    that is not a post, `Refused` (with the reason) for one the screen turns
    away unless `anyway`, and `OnThePage` for a post that already has a card
    - whatever `anyway` says, since its card holds a verdict."""
    now = time.time() if now is None else now
    held = held or await asyncio.to_thread(store)
    await asyncio.to_thread(held.prune, now)
    post_id, clean, slug = parse_url(url)
    text = str(text or "").strip()
    if not text:
        raise Refused("Paste the post's words as well: FAM does not read LinkedIn.")
    # A post a run set aside (about something else) may still be pasted: a
    # person looking at it is a better judge than the run was.
    was = await asyncio.to_thread(held.status_of, post_id)
    existing = None if was in ("", SET_ASIDE) else {"status": was}
    if existing is None:
        existing = await asyncio.to_thread(held.find_text, text, besides=post_id)
        if existing is not None and existing["status"] == SET_ASIDE:
            existing = None
    if existing is not None:
        raise OnThePage(f"That post is already on the page ({existing['status']}).")
    author_name = " ".join(str(author_name or "").split())[:120] or name_from_slug(slug)
    author = author_key(slug, author_name)
    reasons = [screen_post(text)]
    if not author:
        reasons.append("no author to check one a week against: type their name")
    elif await asyncio.to_thread(held.recent_author, author, now - 7 * 86400,
                                 besides=post_id, name=author_name):
        reasons.append("this author is on the page or was commented on this week")
    screened = "; ".join(r for r in reasons if r)
    if screened and not anyway:
        raise Refused(screened)
    card = Card(post_id, clean, text[:6000], author, author_name, screened)
    if was == SET_ASIDE:
        # What judging it already cost stays counted ([failures-visible]).
        before = await asyncio.to_thread(held.card, post_id)
        card.dollars = before.dollars if before is not None else 0.0
    await _match(cache, card, drafter)
    await asyncio.to_thread(held.put, card, now)
    if was == SET_ASIDE:
        await asyncio.to_thread(held.decide, post_id, WAITING, now)
    return await asyncio.to_thread(held.get, post_id) or {}


class OnThePage(ValueError):
    """The post already has a card; use Match again or Back to waiting."""


async def rematch(cache, post_id: str, *, drafter=None,
                  held: Optional[Store] = None) -> dict:
    """Match a card again - after the Go Deeper episode it needed was made."""
    held = held or await asyncio.to_thread(store)
    # A set-aside post is not on the page: matching it would bill a model
    # call for a card nobody can see.
    if await asyncio.to_thread(held.status_of, post_id) in ("", SET_ASIDE):
        raise KeyError(post_id)
    card = await asyncio.to_thread(held.card, post_id)
    if card is None:
        raise KeyError(post_id)
    await _match(cache, card, drafter)
    await asyncio.to_thread(held.put, card, time.time())
    return await asyncio.to_thread(held.get, post_id) or {}


async def _match(cache, card: Card, drafter=None) -> None:
    # Embedding every kept episode is CPU work; never on the loop that
    # streams listeners' audio.
    card.episodes = await asyncio.to_thread(episodes_for, cache, card.text)
    card.chosen = 0
    card.reason = reason(card.text, card.episode, card.screened)
    card.draft, card.question, card.drafted_by, dollars, misfit = await _judged(
        drafter, card.text, card.episode)
    if misfit:
        mark_misfit(card, misfit)
    # Every match's drafting is counted on its card and summed on the page
    # ([failures-visible]): Match again is a model call each time.
    card.dollars = round(card.dollars + float(dollars or 0), 6)


# --------------------------------------------------------------------------
# Finding posts: the web search FAM already uses, never linkedin.com itself

#: The two search vendors FAM's research already pays for, in the order
#: tried. Both index public LinkedIn posts; neither is LinkedIn.
BACKENDS = ("parallel", "perplexity")
#: Cards one run leaves on the page: the X page's day, 3-5 found, 1-2 joined.
SHORTLIST = 8
#: Results asked of one search.
RESULTS_PER_SEARCH = 10

#: How a search vendor titles a LinkedIn post: "Jane Doe on LinkedIn: ...",
#: "Jane Doe's Post - LinkedIn", or "... | Jane Doe | 12 comments".
_TITLE_ON = re.compile(r"^(.{2,80}?) on LinkedIn\b")
_TITLE_POST = re.compile(r"^(.{2,80}?)['’]s Post\b")
_TITLE_BAR = re.compile(r"\|\s*([^|]{2,80}?)\s*\|\s*\d+\s+comments?", re.I)


def backend() -> str:
    """The vendor a run searches with, or "" when none can be asked."""
    import research

    for name in BACKENDS:
        if research.backend_available(name):
            return name
    return ""


def available() -> tuple[bool, str]:
    """Whether a run can search, and why not. Cheap; no network."""
    try:
        import spend_guard

        if spend_guard.enabled():
            return False, "this deployment spends nothing (staging), so nothing is searched"
    except Exception:  # noqa: BLE001
        pass
    name = backend()
    if not name:
        return False, "neither PARALLEL_API_KEY nor PERPLEXITY_API_KEY is set"
    return True, f"searches LinkedIn's public posts through {name.title()}"


def author_from_title(title: str) -> str:
    """The poster's name from how a search vendor titled the post, or ""."""
    title = " ".join(str(title or "").split())
    for pattern in (_TITLE_ON, _TITLE_POST, _TITLE_BAR):
        found = pattern.search(title)
        if found:
            name = found.group(1).strip(" -|:")
            if name and len(name.split()) <= 6:
                return name
    return ""


def objective(seed) -> str:
    """What a search is for, in words; Perplexity's whole query. It carries
    the episode's subject words beside its names (§326): "Eagles" alone
    finds birds as often as the team."""
    about = ", ".join((*seed.names, *x_threads.context_words(seed)))
    return (f"Recent public LinkedIn posts in which somebody teaches, explains "
            f"or gives their view on {seed.title} ({about}).")


async def search(seed, *, recency_days: int, name: str = "") -> tuple[list, float]:
    """One search for LinkedIn posts about `seed`: `(results, dollars)`.
    Restricted to linkedin.com through the vendor's own domain filter.
    Raises on a refusal - an outage is not a quiet empty result - and is
    counted on the Outside service requests page either way."""
    import provider_usage
    import research

    name = name or backend()
    # The episode's own subject words beside its names (§326): "Eagles"
    # alone finds birds as often as the team.
    query = " ".join((*seed.names, *x_threads.context_words(seed))) + " LinkedIn post"

    def _blocking():
        if name == "parallel":
            return research._search_parallel(
                query, RESULTS_PER_SEARCH, recency_days, ("linkedin.com",),
                objective(seed))
        return research._search_perplexity(
            objective(seed), RESULTS_PER_SEARCH, recency_days, ("linkedin.com",))

    try:
        results, dollars = await asyncio.to_thread(_blocking)
    except Exception:
        provider_usage.record(name, ok=False)
        raise
    provider_usage.record(name)
    return results, float(dollars)


def posts_from(results) -> list[dict]:
    """Search results that are LinkedIn posts, in the fields a card needs:
    profiles, company pages, jobs and articles are not posts."""
    out, seen = [], set()
    for r in results or []:
        try:
            post_id, clean, slug = parse_url(getattr(r, "url", ""))
        except NotAPost:
            continue
        if post_id in seen:
            continue
        seen.add(post_id)
        title = str(getattr(r, "title", "") or "")
        text = "\n".join(str(h) for h in (getattr(r, "highlights", None) or []) if h)
        out.append({"post_id": post_id, "url": clean, "slug": slug,
                    "author_name": author_from_title(title) or name_from_slug(slug),
                    "text": (text or title).strip(),
                    "published": str(getattr(r, "published_date", "") or "")})
    return out


def _day_start(now: float) -> float:
    return now - (now % 86400)


async def run(cache, *, now: Optional[float] = None, searcher=None,
              drafter=None, held: Optional[Store] = None) -> dict:
    """Find, screen, rank and draft; leave the cards on the page.

    Starts from the episodes FAM already has (`x_threads.seeds`, the same
    the X page looks for), searches for public LinkedIn posts about each,
    and keeps the best few that teach something an episode goes deeper on.
    Returns the run's report, which is also stored and shown: a run that
    found nothing says what it asked and why nothing qualified
    ([failures-visible]).
    """
    now = time.time() if now is None else now
    held = held or await asyncio.to_thread(store)
    await asyncio.to_thread(held.prune, now)
    settings = config.settings
    report: dict = {"seeds": [], "searched": 0, "results": 0, "posts": 0,
                    "screened_out": 0, "cards": 0, "errors": [], "backend": ""}
    if searcher is None:
        ok, why = available()
        if not ok:
            report["stopped"] = why
            await asyncio.to_thread(held.record_run, now, 0, 0.0, report)
            return report
        report["backend"] = backend()
        searcher = search
    used = await asyncio.to_thread(held.searches_since, _day_start(now))
    room = min(settings.linkedin_seeds, settings.linkedin_daily_searches - used)
    if room < 1:
        report["stopped"] = (
            "LINKEDIN_SEEDS is 0, so a press searches for nothing"
            if settings.linkedin_seeds < 1 else
            "today's search ceiling (LINKEDIN_DAILY_SEARCHES) is used up")
        await asyncio.to_thread(held.record_run, now, 0, 0.0, report)
        return report
    chosen = await asyncio.to_thread(lambda: x_threads.seeds(
        cache, trending=x_threads.trending_now(), limit=room))
    report["seeds"] = [{"title": s.title, "names": list(s.names), "origin": s.origin}
                       for s in chosen]
    if not chosen:
        report["stopped"] = ("no kept episode has a name to search for: write "
                             "the Trending edition or play some episodes first")
        await asyncio.to_thread(held.record_run, now, 0, 0.0, report)
        return report
    searched, dollars, found = 0, 0.0, []
    await asyncio.to_thread(held.record_run, now, 0, 0.0, report)
    try:
        import research

        for seed in chosen:
            # Counted before it is asked: a search that times out after the
            # vendor billed it still counts toward the day's ceiling.
            searched += 1
            report["searched"] = searched
            await asyncio.to_thread(held.record_run, now, searched, dollars, report)
            try:
                results, cost = await searcher(
                    seed, recency_days=settings.linkedin_window_days)
            except asyncio.CancelledError:
                raise
            except research.ResearchUnavailable as exc:
                # Configuration, not a request: nothing was asked or billed.
                searched -= 1
                report["searched"] = searched
                report["errors"].append(f"{seed.title}: {exc}"[:200])
                continue
            except Exception as exc:  # noqa: BLE001 - one seed never ends the run
                report["errors"].append(f"{seed.title}: {exc}"[:200])
                continue
            dollars = round(dollars + cost, 6)
            report["results"] += len(results)
            # Counted the moment it is spent ([failures-visible]).
            await asyncio.to_thread(held.record_run, now, searched, dollars, report)
            found.extend((p, seed) for p in posts_from(results))
        known = await asyncio.to_thread(held.known)
        fresh = {p["post_id"] for p, _ in found if p["post_id"] not in known}
        picked = await asyncio.to_thread(_shortlist, cache, held, found, now)
        report["posts"] = len({p["post_id"] for p, _ in found})
        report["screened_out"] = max(0, len(fresh) - len(picked))
        # Twice the cards are ranked; drafting stops at `SHORTLIST`, so a
        # post set aside is replaced from further down, as on X.
        for card in picked:
            if report["cards"] >= SHORTLIST:
                break
            card.draft, card.question, card.drafted_by, cost, misfit = await _judged(
                drafter, card.text, card.episode)
            card.dollars = round(float(cost or 0), 6)
            if misfit:
                # About something else: remembered, so the next run neither
                # reads nor judges it again, and never shown.
                mark_misfit(card, misfit)
                if await asyncio.to_thread(held.insert_new, card, now, SET_ASIDE):
                    report["set_aside"] = report.get("set_aside", 0) + 1
                continue
            if await asyncio.to_thread(held.insert_new, card, now):
                report["cards"] += 1
    except asyncio.CancelledError:
        report["stopped"] = "the run was stopped before it finished"
        raise
    except Exception as exc:  # noqa: BLE001 - said on the page, then raised
        report["stopped"] = f"the run failed: {type(exc).__name__}: {exc}"[:300]
        raise
    finally:
        await asyncio.to_thread(held.record_run, now, searched, dollars, report)
    return report


def _shortlist(cache, held: Store, found: list, now: float) -> list[Card]:
    """Screen and rank what a run found: one card per author, best first.
    The episode a post was found from is not assumed to be its match - each
    post is ranked against every kept episode, and one that no episode is
    the subject of is not a card (a found post with nothing to link is not
    worth a person's minute; a pasted one is, so it keeps its card)."""
    known = held.known()
    episodes = catalogue(cache)
    best: dict = {}
    for post, seed in found:
        if post["post_id"] in known or post["post_id"] in best:
            continue
        if screen_post(post["text"]):
            continue
        author = author_key(post["slug"], post["author_name"])
        if not author or held.recent_author(author, now - 7 * 86400,
                                            name=post["author_name"]):
            continue
        if held.find_text(post["text"]) is not None:
            continue
        offered = rank(episodes, post["text"])
        if not offered or not linked(offered[0]):
            continue
        card = Card(post["post_id"], post["url"], post["text"][:6000], author,
                    post["author_name"], "", offered, 0, origin=FOUND,
                    found_by=seed.title)
        card.reason = (f"found searching LinkedIn for “{seed.title}”"
                       + (f", posted {post['published']}" if post["published"] else "")
                       + "; " + reason(card.text, card.episode, ""))
        best[post["post_id"]] = (offered[0]["match"], card)
    out, authors = [], set()
    for _, card in sorted(best.values(), key=lambda pair: -pair[0]):
        if card.author in authors:
            continue
        authors.add(card.author)
        out.append(card)
        if len(out) >= 2 * SHORTLIST:
            break
    return out


def health() -> dict:
    ok, why = available()
    settings = config.settings
    out = {"available": ok, "detail": why,
           "daily_searches": settings.linkedin_daily_searches,
           "seeds": settings.linkedin_seeds,
           "window_days": settings.linkedin_window_days}
    if not _exists():
        out["counts"], out["dollars"] = {}, 0.0
        out["searches_today"], out["last_run"] = 0, None
        return out
    try:
        held = store()
        out["counts"] = held.counts()
        out["dollars"] = held.dollars()
        out["searches_today"] = held.searches_since(_day_start(time.time()))
        out["last_run"] = held.last_run()
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
    return out
