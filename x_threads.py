"""Conversations on X that one of FAM's episodes could add to (`/admin/x`).

## What this is

The X playbook (Find -> Add -> Explore -> Connect) is done by a person: FAM
replies to somebody else's post from its own account, adding an angle and,
only when it deepens the thread, an episode. The slow part was *Find* -
scrolling X for an hour for three to five threads worth joining - and the
expensive part was making an episode for each one.

This turns the search around. **It starts from episodes FAM has already
written and checked** - the current Trending edition and the most played
cached episodes - and looks for the conversations they answer. So the episode
exists before the thread is found, and a run is a short list of cards:

    the post, why it was picked, a drafted reply, the episode, Approve / Skip

## Rules that are load-bearing

**A person posts; FAM never does** ([no-social-posting]). FAM holds a
read-only X search credential and nothing that can write. Approve hands the
drafted words to X's own compose screen (`x.com/intent/post?in_reply_to=`)
and copies them to the clipboard; the person posts from the FAM account in
X, with the post in front of them. Nothing here can post while somebody is
asleep, and nothing here is an automated reply under X's automation rules.

**Asked when somebody is looking** ([sweep-on-demand]): a run is a button on
`/admin/x`, never a timer. X bills per post read, so `X_THREADS_MAX_READS`
caps one run and `X_THREADS_DAILY_READS` caps a day, both counted here.

**Staging spends nothing** ([zero-spend-staging]): `X_BEARER_TOKEN` is
scrubbed there, and a run says so rather than pretending it found nothing.

**A candidate carries its reason in words** ([candidate-reason]): which
episode it matched, how fast the thread is moving, how much room is left in
it. The page shows that line; a score with no sentence is not a reason.

**A layer that adds quality must not subtract availability**: with no
model key the reply is drafted in code from the episode's title, and the card
says so.

**Kept a week.** A post's text is stored only while it is a candidate and
pruned after `KEEP_DAYS`, as X's developer terms expect of content kept off X.
"""
from __future__ import annotations

import asyncio
import calendar
import json
import logging
import math
import re
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Iterable, Optional
from urllib.parse import quote

import db as store_db
import config
from paths import data_path

log = logging.getLogger("fam.x_threads")

SEARCH_URL = "https://api.x.com/2/tweets/search/recent"
INTENT_URL = "https://x.com/intent/post"

#: The share links this page makes are owned by this id, never a listener's:
#: a share's `user_id` is who recommended it, and here FAM did.
SHARER = "fam-x"

#: How long a candidate's row (and the post's text) is kept.
KEEP_DAYS = 7

#: X counts any link as this many characters, whatever its length.
LINK_CHARS = 23
#: A post's ceiling, and what the drafted words may use beside a link.
POST_CHARS = 280
DRAFT_CHARS = 220

#: Cards a run leaves on the page; the playbook's day is 3-5 found, 1-2 joined.
SHORTLIST = 8

#: Statuses. `waiting` is on the page; the others are the person's verdict.
WAITING, APPROVED, SKIPPED = "waiting", "approved", "skipped"
STATUSES = (WAITING, APPROVED, SKIPPED)

#: The playbook's four kinds of reply (FAM_X.pdf, examples 01-04).
ANGLE_AND_EPISODE = "angle_episode"     # 01: another angle + episode
QUESTION_ONLY = "question"              # 02: ask a better question, no link
THREAD_TO_EPISODE = "thread_episode"    # 03: their question has an episode
BOTH_SIDES = "both_sides"               # 04: connect competing perspectives
KINDS = {
    ANGLE_AND_EPISODE: "Another angle + episode",
    QUESTION_ONLY: "A better question, no link",
    THREAD_TO_EPISODE: "Their question, our episode",
    BOTH_SIDES: "Both sides",
}

#: Who is worth replying to. Smaller and nobody sees the reply; bigger and it
#: is buried under hundreds of others.
MIN_FOLLOWERS = 500
MAX_FOLLOWERS = 200_000
#: Past this many replies a new one is not read.
MAX_REPLIES = 50

#: Subjects FAM does not walk into uninvited, whatever episode matches:
#: a reply under somebody's grief or a flame war is not "adding to it".
AVOID = (
    "rip", "r.i.p", "passed away", "died", "killed", "funeral", "shooting",
    "suicide", "condolences", "prayers", "tragedy", "victims", "massacre",
    "abortion", "nsfw",
)

#: How a post asks something without a question mark.
_ASKS = re.compile(
    r"\?|\b(anyone|anybody|does anyone|how do|how does|why do|why does|why is|"
    r"what do you|what's the|is it just me|thoughts on|wondering|curious|"
    r"can someone|explain|i wish|would you)\b", re.I)
#: How a post takes a side.
_STANCE = re.compile(
    r"\b(overrated|underrated|hot take|unpopular opinion|i think|i don't think|"
    r"change my mind|the real reason|nobody talks about|honestly)\b", re.I)

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9'\-&.]*")
_STOP = {
    "the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "for", "is",
    "are", "was", "why", "how", "what", "who", "when", "where", "will", "can",
    "does", "did", "do", "it", "its", "this", "that", "with", "from", "by",
    "after", "before", "about", "as", "into", "over", "new", "more", "most",
    "i", "you", "we", "they", "he", "she", "his", "her", "their", "our",
    "says", "said", "could", "would", "should", "may", "might", "be", "has",
    "have", "had", "not", "no", "yes", "up", "out", "just", "than", "then",
}

#: The score's parts and their weights. Episode match leads because a reply
#: with nothing to add is the thing the playbook says not to post.
WEIGHTS = {"match": 0.35, "velocity": 0.30, "question": 0.15,
           "room": 0.10, "author": 0.10}


# --------------------------------------------------------------------------
# Shapes


@dataclass
class Post:
    """One post from X's recent search, in the fields a card needs."""

    id: str
    text: str
    created_at: float
    author: str = ""            # the @handle, without the @
    author_name: str = ""
    followers: int = 0
    verified_type: str = ""     # "business" / "government" are not people
    likes: int = 0
    replies: int = 0
    reposts: int = 0
    quotes: int = 0
    lang: str = ""
    is_reply: bool = False
    sensitive: bool = False

    @property
    def url(self) -> str:
        return f"https://x.com/{self.author or 'i'}/status/{self.id}"


@dataclass
class Seed:
    """An episode FAM already has, and the X search that looks for it."""

    key: str
    query: str                  # the episode's question
    title: str
    minutes: int
    names: tuple                # what the X search asks for
    origin: str = ""            # "trending" or "cached"
    plays: int = 0

    def search(self) -> str:
        return x_query(self.names, context_words(self))


@dataclass
class Candidate:
    post: Post
    seed: Optional[Seed]
    score: float
    parts: dict
    kind: str
    reason: str
    draft: str = ""
    drafted_by: str = ""        # a model id, or "code"
    other: Optional[Post] = None  # the opposing post, for BOTH_SIDES


# --------------------------------------------------------------------------
# Seeds: from the episodes FAM already has


def name_runs(text: str, limit: int = 3) -> tuple:
    """The names in a title - runs of capitalised words - most specific first.

    What an X search should ask for: "Jalen Hurts" finds the conversation,
    "quarterback" finds everybody. A title's first word counts only as the
    start of a longer run, since sentence case capitalises it whatever it is.
    """
    words = [w.strip("'-.") for w in _WORD.findall(
        (text or "").replace("’s", "").replace("'s", ""))]
    out, run, start = [], [], 0
    for i, word in enumerate(words + [""]):
        if (word and len(word) >= 2 and word[0].isupper()
                and word.lower() not in _STOP and not word.isdigit()):
            if not run:
                start = i
            run.append(word)
            continue
        if run and (start > 0 or len(run) >= 2):
            name = " ".join(run)
            if name not in out:
                out.append(name)
        run = []
    out.sort(key=lambda n: -len(n.split()))
    return tuple(out[:limit])


def names_for(title: str, query: str, limit: int = 3) -> tuple:
    """What to search X for, from an episode's title and its question.

    Both, because a title in title case capitalises everything ("Why the
    Philadelphia Eagles Defence Is Thin") while the question was typed in
    sentence case and names only the names ("Eagles"). The search ORs them,
    so the broad name finds the conversation and the long one ranks it. A
    run longer than three words is a title-cased phrase, not a name.
    """
    words = [w for w in _WORD.findall(title or "") if w.lower() not in _STOP]
    title_case = bool(words) and sum(w[0].isupper() for w in words) >= 0.7 * len(words)
    # In a title-cased title one capitalised word is just a word ("Thin").
    from_title = [n for n in name_runs(title, limit=6)
                  if not title_case or len(n.split()) >= 2]
    out, seen = [], set()
    for name in from_title + list(name_runs(query, limit=6)):
        if len(name.split()) > 3 or name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append(name)
    return tuple(out[:limit])


def context_words(seed: "Seed", limit: int = 4) -> tuple:
    """The episode's own subject words, beside its names: what tells the
    Eagles' defence from a post about eagles. The longest content words of
    its question and title that are not part of a name."""
    import news_clusters

    named = {w.lower() for n in seed.names for w in n.split()}
    seen, out = set(), []
    for word in _WORD.findall(f"{seed.query} {seed.title}"):
        low = word.lower().strip("'.-")
        if (len(low) < 4 or low in _STOP or low in named or low in seen
                or low in news_clusters.STOPWORDS or low.isdigit()):
            continue
        seen.add(low)
        out.append(low)
    out.sort(key=len, reverse=True)
    return tuple(out[:limit])


def x_query(names: Iterable[str], context: Iterable[str] = ()) -> str:
    """X's search syntax for posts naming any of `names`: English, original
    posts only (no reposts, replies or ads), so the result is conversations
    being *started*, which is where a reply is read.

    A name of two or more words ("Jalen Hurts") is specific enough alone. A
    one-word name ("Eagles") is asked for only beside one of `context`, the
    episode's own subject words - alone it finds birds as often as the team,
    and every post it finds is billed."""
    names = [str(x).replace('"', "").strip() for x in names]
    context = [str(c).replace('"', "").strip() for c in context if str(c).strip()]
    terms = []
    for name in (n for n in names if n):
        if " " in name:
            terms.append(f'"{name}"')
        elif context:
            around = context[0] if len(context) == 1 else "(" + " OR ".join(context) + ")"
            terms.append(f"({name} {around})")
        else:
            terms.append(name)
    if not terms:
        return ""
    body = terms[0] if len(terms) == 1 else "(" + " OR ".join(terms) + ")"
    return f"{body} lang:en -is:retweet -is:reply -is:nullcast"


def on_subject(post: "Post", seed: Optional["Seed"]) -> str:
    """Why this post is not about the episode's subject, or "" when it is.

    Code only, and it runs before anything is drafted. A post must name what
    the episode names, and a one-word name must come with one of the
    episode's own subject words - a shared word is not a shared subject."""
    if seed is None:
        return "no episode to be about"
    low = (post.text or "").lower()
    named = [n for n in seed.names if n.lower() in low]
    if not named:
        return "does not name what the episode is about"
    if any(" " in n for n in named):
        return ""
    import news_clusters

    # Only the name it matched is taken away: "Defence" in a longer name it
    # did not match is still one of the episode's subject words.
    words = news_clusters.tokens(post.text) - {
        news_clusters._stem(w.lower()) for n in named for w in n.split()}
    about = news_clusters.tokens(f"{seed.title} {seed.query}")
    if not words & about:
        return "shares a name with the episode but nothing else"
    return ""


def seeds(cache, *, trending: Iterable = (), limit: int = 10) -> list[Seed]:
    """The episodes to look for conversations about, Trending first.

    `trending` is `[(story, episode_key)]` from the current edition; the rest
    is filled from the most played cached episodes. Only episodes that are
    kept (`cache.recent`) qualify: a card must be able to play what it links.
    One that swears (the E before its title) is not offered under a
    stranger's post, wherever it came from.
    """
    out: list[Seed] = []
    seen: set = set()
    wanted = {key: story for story, key in trending if key}
    entries = {}
    if cache is not None:
        try:
            if wanted:
                for row in cache.recent(limit=len(wanted), keys=list(wanted)):
                    entries[row["key"]] = row
            for row in cache.recent(limit=200):
                entries.setdefault(row["key"], row)
        except Exception as exc:  # noqa: BLE001 - a seed list never raises
            log.warning("x threads: could not read the cache: %s", exc)
    for key, story in wanted.items():
        row = entries.get(key)
        if not row or row.get("explicit"):
            continue
        names = tuple(getattr(story, "anchors", ()) or ()) or names_for(
            getattr(story, "title", "") or row.get("title", ""), row["query"])
        if names:
            out.append(Seed(key, row["query"], row.get("title") or story.title,
                            int(row.get("minutes") or 1), names[:3],
                            "trending", int(row.get("plays") or 0)))
            seen.add(key)
    rest = sorted((r for k, r in entries.items() if k not in seen),
                  key=lambda r: -int(r.get("plays") or 0))
    for row in rest:
        if len(out) >= limit:
            break
        if row.get("explicit"):
            continue
        names = names_for(row.get("title") or "", row["query"])
        if not names:
            continue
        out.append(Seed(row["key"], row["query"], row.get("title") or row["query"],
                        int(row.get("minutes") or 1), names, "cached",
                        int(row.get("plays") or 0)))
    return out[:limit]


# --------------------------------------------------------------------------
# Reading X


def parse(payload: dict) -> list[Post]:
    """X's v2 search response into posts, authors joined in."""
    users = {u.get("id"): u for u in
             ((payload or {}).get("includes") or {}).get("users") or []}
    out = []
    for row in (payload or {}).get("data") or []:
        metrics = row.get("public_metrics") or {}
        user = users.get(row.get("author_id")) or {}
        try:
            created = float(calendar.timegm(time.strptime(
                (row.get("created_at") or "")[:19], "%Y-%m-%dT%H:%M:%S")))
        except ValueError:
            created = 0.0
        out.append(Post(
            id=str(row.get("id") or ""), text=str(row.get("text") or ""),
            created_at=created, author=str(user.get("username") or ""),
            author_name=str(user.get("name") or ""),
            followers=int((user.get("public_metrics") or {}).get("followers_count") or 0),
            verified_type=str(user.get("verified_type") or ""),
            likes=int(metrics.get("like_count") or 0),
            replies=int(metrics.get("reply_count") or 0),
            reposts=int(metrics.get("retweet_count") or 0),
            quotes=int(metrics.get("quote_count") or 0),
            lang=str(row.get("lang") or ""),
            is_reply=bool(row.get("in_reply_to_user_id")),
            sensitive=bool(row.get("possibly_sensitive"))))
    return [p for p in out if p.id and p.text]


def token() -> str:
    import credentials

    return (credentials.active("X_BEARER_TOKEN") or "").strip()


def available() -> tuple[bool, str]:
    """Whether X can be asked, and why not. Cheap; no network."""
    try:
        import spend_guard

        if spend_guard.enabled():
            return False, "this deployment spends nothing (staging), so X is not asked"
    except Exception:  # noqa: BLE001
        pass
    if not token():
        return False, "X_BEARER_TOKEN is not set"
    return True, "X_BEARER_TOKEN present (not verified from this machine)"


async def search(query: str, *, max_results: int, since: float,
                 client=None) -> list[Post]:
    """One recent-search request. Raises on a refusal - an outage is not a
    quiet empty result - and counts the request either way."""
    import httpx
    import provider_usage

    params = {
        "query": query,
        "max_results": max(10, min(int(max_results), 100)),
        "sort_order": "relevancy",
        "start_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(since)),
        "tweet.fields": "created_at,public_metrics,lang,author_id,"
                        "in_reply_to_user_id,possibly_sensitive",
        "expansions": "author_id",
        "user.fields": "username,name,public_metrics,verified_type",
    }
    headers = {"Authorization": f"Bearer {token()}"}
    own = client is None
    client = client or httpx.AsyncClient(timeout=15.0)
    try:
        response = await client.get(SEARCH_URL, params=params, headers=headers)
    except Exception as exc:
        provider_usage.record("x", ok=False, why=type(exc).__name__)
        raise
    finally:
        if own:
            await client.aclose()
    if response.status_code != 200:
        provider_usage.record("x", ok=False, why=f"HTTP {response.status_code}")
        detail = ""
        try:
            body = response.json()
            detail = body.get("detail") or body.get("title") or ""
        except ValueError:
            pass
        raise RuntimeError(f"X refused the search (HTTP {response.status_code})"
                           + (f": {detail}" if detail else ""))
    provider_usage.record("x")
    return parse(response.json())


# --------------------------------------------------------------------------
# Choosing


def asks(text: str) -> bool:
    return bool(_ASKS.search(text or ""))


def takes_a_side(text: str) -> bool:
    return bool(_STANCE.search(text or ""))


def screen(post: Post, now: float, *, window_hours: float,
           replied_to: Iterable[str] = ()) -> str:
    """Why this post is not a thread to join, or "" when it is. Code only."""
    if post.is_reply:
        return "a reply, not a conversation's start"
    if post.lang and post.lang != "en":
        return "not in English"
    if post.sensitive:
        return "marked sensitive"
    if post.created_at and now - post.created_at > window_hours * 3600:
        return "older than the window"
    if post.verified_type in ("business", "government"):
        return "an organisation, not a person"
    if post.followers < MIN_FOLLOWERS:
        return "too few followers to be seen"
    if post.followers > MAX_FOLLOWERS:
        return "too big an account: a reply is buried"
    if post.replies > MAX_REPLIES:
        return "too many replies already"
    low = (post.text or "").lower()
    if any(re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", low) for w in AVOID):
        return "a subject FAM does not walk into"
    try:
        import content_filter

        if content_filter.scrub(post.text) != post.text:
            return "carries a slur"
    except Exception:  # noqa: BLE001
        pass
    if post.author and post.author.lower() in {a.lower() for a in replied_to}:
        return "this author is on the page or was replied to this week"
    if not (asks(post.text) or takes_a_side(post.text)):
        return "neither asks nor takes a side"
    return ""


def velocity(post: Post, now: float) -> float:
    """Engagement per hour since it was posted, 0-1 on a log scale (about
    200 an hour is 1)."""
    hours = max((now - post.created_at) / 3600.0, 0.25) if post.created_at else 1.0
    per_hour = (post.likes + 2 * post.replies + 2 * post.reposts + 2 * post.quotes) / hours
    return min(1.0, math.log1p(per_hour) / math.log1p(200.0))


def match(post: Post, seed: Optional[Seed], about: Optional[list] = None) -> float:
    """How much the episode is about what the post is about, 0-1: the same
    embeddings the cache matches questions with, plus the seed's names.
    `about` is the episode's vector when the caller already has it - one
    encode per episode a run, not one per post."""
    if seed is None:
        return 0.0
    import embeddings

    try:
        if about is None:
            about = embeddings.embed(f"{seed.title}. {seed.query}")
        semantic = max(0.0, embeddings.cosine(embeddings.embed(post.text), about))
    except Exception:  # noqa: BLE001
        semantic = 0.0
    low = post.text.lower()
    named = sum(1 for n in seed.names if n.lower() in low) / max(len(seed.names), 1)
    return round(min(1.0, 0.6 * semantic + 0.4 * named), 4)


def score(post: Post, seed: Optional[Seed], now: float,
          about: Optional[list] = None) -> tuple[float, dict]:
    parts = {
        "match": match(post, seed, about),
        "velocity": round(velocity(post, now), 4),
        "question": 1.0 if asks(post.text) else (0.5 if takes_a_side(post.text) else 0.0),
        "room": round(1.0 - min(post.replies, MAX_REPLIES) / MAX_REPLIES, 4),
        # Best in the middle of the band: thousands to tens of thousands.
        "author": round(max(0.0, 1.0 - abs(math.log10(max(post.followers, 1)) - 4.0) / 1.5), 4),
    }
    total = sum(WEIGHTS[k] * v for k, v in parts.items())
    return round(total, 4), parts


#: Below this an episode is not the thread's subject, and the reply carries
#: no link (example 02) - the playbook's "only when it deepens the discussion".
LINK_FLOOR = 0.45


def kind_of(post: Post, parts: dict) -> str:
    if parts["match"] < LINK_FLOOR:
        return QUESTION_ONLY
    if asks(post.text):
        return THREAD_TO_EPISODE
    return ANGLE_AND_EPISODE


def reason(post: Post, seed: Optional[Seed], parts: dict, kind: str,
           now: float) -> str:
    """Why this card is on the page, in words ([candidate-reason])."""
    bits = []
    hours = (now - post.created_at) / 3600.0 if post.created_at else 0.0
    engaged = post.likes + post.replies + post.reposts + post.quotes
    bits.append(f"{engaged} interactions in {max(hours, 0.1):.1f}h")
    bits.append(f"{post.replies} replies so far")
    if asks(post.text):
        bits.append("asks a question")
    elif takes_a_side(post.text):
        bits.append("takes a side")
    if seed is not None and kind != QUESTION_ONLY:
        bits.append(f"matches “{seed.title}” ({int(parts['match'] * 100)}%)"
                    + (" from today's Trending" if seed.origin == "trending" else ""))
    elif seed is not None:
        bits.append(f"near “{seed.title}” but not its subject, so no link")
    return "; ".join(bits) + "."


def opposed(a: Post, b: Post) -> bool:
    """Two posts on one subject from different people, both taking a side:
    the material for "connect competing perspectives" (example 04)."""
    if a.author == b.author or not (takes_a_side(a.text) or asks(a.text)):
        return False
    import news_clusters

    return news_clusters.same_story(news_clusters.tokens(a.text),
                                    news_clusters.tokens(b.text))


def shortlist(found: list[tuple[Post, Seed]], now: float, *, window_hours: float,
              replied_to: Iterable[str] = (), size: int = SHORTLIST) -> list[Candidate]:
    """Screen, score and choose: one card per author, best first."""
    import embeddings

    replied_to = list(replied_to)
    best: dict = {}
    vectors: dict = {}
    for post, seed in found:
        if screen(post, now, window_hours=window_hours, replied_to=replied_to):
            continue
        if on_subject(post, seed):
            continue
        about = None
        if seed is not None:
            if seed.key not in vectors:
                try:
                    vectors[seed.key] = embeddings.embed(f"{seed.title}. {seed.query}")
                except Exception:  # noqa: BLE001
                    vectors[seed.key] = None
            about = vectors[seed.key]
        total, parts = score(post, seed, now, about)
        kept = best.get(post.id)
        if kept is None or total > kept.score:
            kind = kind_of(post, parts)
            best[post.id] = Candidate(post, seed, total, parts, kind,
                                      reason(post, seed, parts, kind, now))
    ranked = sorted(best.values(), key=lambda c: -c.score)
    out, authors = [], set()
    for cand in ranked:
        if cand.post.author in authors:
            continue
        authors.add(cand.post.author)
        out.append(cand)
        if len(out) >= size:
            break
    # A pair on one episode's subject, from two people, becomes one card.
    for cand in out:
        if cand.kind == QUESTION_ONLY or cand.seed is None:
            continue
        for other in ranked:
            if (other.post.id != cand.post.id and other.seed is not None
                    and other.seed.key == cand.seed.key
                    and opposed(cand.post, other.post)):
                cand.kind, cand.other = BOTH_SIDES, other.post
                cand.reason += f" Another side from @{other.post.author}."
                break
    return out


# --------------------------------------------------------------------------
# Drafting


DRAFT_SYSTEM = """You draft one reply on X for FAM, an app that turns what \
people care about into short audio stories. The reply goes under somebody \
else's post. A person at FAM reads it and posts it themselves.

First, judge relevance - strictly, because an off-topic reply is spam:
- same_subject: is the post about the same subject as FAM's episode - the \
same team, person, company, event or question - and not merely sharing a \
word or a name with it (the Philadelphia Eagles are not eagles; Apple the \
company is not apples; a United Airlines flight is not Manchester United)? When unsure, false.
- episode_fits: would someone reading this exact post want this exact \
episode - does it answer or deepen what the post is asking or arguing? A \
story on the same subject about something else is false. When unsure, false.
- other_same_subject: when a second post is given, is it about that same \
subject and conversation? false when no second post is given.

Then the reply, which must answer what THIS post says - its own question, \
claim or point - not the episode's angle:
- Read the post and answer it like a person. Contribute a thoughtful angle \
first: a distinction, a fact on the post's own subject, or what the two \
sides actually disagree about.
- End with one short, genuine question back to the poster.
- Only when episode_fits is true may you say "we explored this in a short \
episode" once, plainly; otherwise never mention FAM or an episode. Never "check out", never "link below", no hashtags, \
no emoji, no exclamation marks, no flattery ("great question").
- Never state a number, score, date or result unless it appears in the post \
or the episode title. Never claim to know the poster.
- At most {chars} characters. No link: one is added after your words when \
there is an episode."""

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "same_subject": {"type": "boolean"},
        "episode_fits": {"type": "boolean"},
        "other_same_subject": {"type": "boolean"},
        "why": {"type": "string"},
        "reply": {"type": "string"},
    },
    "required": ["same_subject", "episode_fits", "other_same_subject", "why", "reply"],
    "additionalProperties": False,
}


def draft_prompt(cand: Candidate) -> str:
    lines = [f"The post, by @{cand.post.author}:", cand.post.text.strip(), ""]
    if cand.other is not None:
        lines += [f"Another side, by @{cand.other.author}:", cand.other.text.strip(), "",
                  "Connect the two perspectives: name the trade-off between them."]
    if cand.seed is not None:
        lines.append(f"FAM's episode: “{cand.seed.title}” "
                     f"(the question it answers: {cand.seed.query})")
    if cand.kind == QUESTION_ONLY or cand.seed is None:
        lines.append("The episode will not be linked. Ask a better question - one "
                     "that helps people in the thread see what they disagree about. "
                     "Do not mention FAM or an episode.")
    lines.append("Judge relevance in the `why` field in one sentence: what the "
                 "post is about, and whether the episode is that.")
    return "\n".join(lines)


def fallback_draft(cand: Candidate) -> str:
    """The reply drafted in code, when no model can be asked. Plain and
    honest; the card says it was written this way."""
    if cand.kind == QUESTION_ONLY or cand.seed is None:
        return "What would change your mind on this - the cost, the evidence, or who it affects most?"
    if cand.other is not None:
        return (f"There's a real trade-off between these two views. We explored "
                f"both sides in a short episode, “{cand.seed.title}”. "
                "Which side matters more for you?")
    return (f"We explored this in a short episode, “{cand.seed.title}”. "
            "What part of it are you most curious about?")


def x_length(text: str, links: int = 0) -> int:
    """A post's length as X counts it: most Latin, Greek and Cyrillic
    characters weigh 1, everything else (CJK, emoji) 2, and each link 23.
    Counted per code point, so an emoji sequence errs long - the safe side
    of a ceiling X enforces."""
    total = 0
    for ch in str(text or ""):
        cp = ord(ch)
        light = (cp <= 4351 or 8192 <= cp <= 8205 or 8208 <= cp <= 8223
                 or 8242 <= cp <= 8247)
        total += 1 if light else 2
    return total + links * (LINK_CHARS + 1)


def trim(text: str, limit: int = DRAFT_CHARS) -> str:
    """A draft cut to `limit` at a word, the ellipsis included."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:max(limit - 1, 0)]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(",;:-") + "…"


@dataclass
class Verdict:
    """The drafting call's judgement of one card, before its words are used.
    `same_subject` false drops the card; `episode_fits` false keeps the reply
    but takes the episode off it; `other_same_subject` false drops the second
    post of a both-sides card."""

    same_subject: bool = True
    episode_fits: bool = True
    other_same_subject: bool = True
    why: str = ""


async def draft(cand: Candidate, client=None) -> tuple[str, str, float]:
    """`(reply, drafted_by, dollars)`, for a caller that wants the words only
    (`judge_and_draft` is what a run uses)."""
    reply, by, dollars, _verdict = await judge_and_draft(cand, client)
    return reply, by, dollars


async def judge_and_draft(cand: Candidate, client=None) -> tuple:
    """`(reply, drafted_by, dollars, verdict)`: one call that first judges
    whether the post and the episode are about the same thing, then writes
    the reply to the post. Never raises but cancellation: a model that
    cannot be asked leaves the code's draft and the code's gate
    (`on_subject`), said so on the card."""
    import credentials
    import metering

    if client is None and not credentials.active("ANTHROPIC_API_KEY"):
        return trim(fallback_draft(cand)), "code", 0.0, Verdict(why="checked in code")
    model = config.settings.x_threads_model
    try:
        if client is None:
            from anthropic_client import build_async_client

            client = build_async_client(credentials.active("ANTHROPIC_API_KEY"))
        response = await client.messages.create(
            model=model, max_tokens=600,
            system=DRAFT_SYSTEM.format(chars=DRAFT_CHARS),
            output_config={"format": {"type": "json_schema", "schema": DRAFT_SCHEMA}},
            messages=[{"role": "user", "content": draft_prompt(cand)}])
        usage = metering.Usage()
        usage.add_model_call(model, getattr(response, "usage", None))
        dollars = metering.price_of(usage).total
        text = next(b.text for b in response.content if b.type == "text")
        body = json.loads(text)
        verdict = Verdict(same_subject=bool(body.get("same_subject")),
                          episode_fits=bool(body.get("episode_fits")),
                          other_same_subject=bool(body.get("other_same_subject")),
                          why=str(body.get("why") or "")[:300])
        reply = trim(body.get("reply") or "")
        if not reply and verdict.same_subject:
            raise ValueError("an empty reply")
        return reply, model, dollars, verdict
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("x threads: drafting fell back to code: %s", exc)
        return trim(fallback_draft(cand)), "code", 0.0, Verdict(why="checked in code")


def apply_verdict(cand: Candidate, verdict: Verdict) -> str:
    """Fold the judgement into the card. Returns why the card is dropped, or
    "" when it stays - the post itself is about something else."""
    if not verdict.same_subject:
        return verdict.why or "the post is about something else"
    if cand.other is not None and not verdict.other_same_subject:
        cand.other = None
        cand.kind = THREAD_TO_EPISODE if asks(cand.post.text) else ANGLE_AND_EPISODE
        cand.reason = cand.reason.split(" Another side from")[0]
    if not verdict.episode_fits and cand.kind != QUESTION_ONLY:
        cand.kind = QUESTION_ONLY
        cand.other = None
        cand.reason += " Same subject, but the episode does not answer this post, so no link."
    if verdict.why:
        cand.reason += f" Checked: {verdict.why}"
    return ""


class TooLong(ValueError):
    """The reply is longer than X allows. Said, never cut: what reaches X
    must be exactly what the person approved."""


def compose(text: str, link: str = "") -> str:
    """The post: the person's words, then the link. Raises `TooLong`
    rather than shortening words somebody approved."""
    text = " ".join(str(text or "").split())
    length = x_length(text, links=1 if link else 0)
    if length > POST_CHARS:
        raise TooLong(f"The reply is {length} characters as X counts them"
                      f"{' with the link' if link else ''}; X allows {POST_CHARS}.")
    return f"{text} {link}" if link else text


def intent_url(post_id: str, text: str) -> str:
    """X's own compose screen, as a reply to `post_id`, with `text` in it.
    The person presses Post; FAM never does."""
    return f"{INTENT_URL}?in_reply_to={quote(str(post_id))}&text={quote(text)}"


# --------------------------------------------------------------------------
# Storage


class Store:
    def __init__(self, path: str | None = None) -> None:
        self.path = data_path("X_THREADS_DB", "x_threads.db", path)
        self._local = threading.local()
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS candidates (
                       post_id    TEXT PRIMARY KEY,
                       found_at   REAL NOT NULL,
                       score      REAL NOT NULL DEFAULT 0,
                       kind       TEXT NOT NULL DEFAULT '',
                       reason     TEXT NOT NULL DEFAULT '',
                       post       TEXT NOT NULL,
                       other      TEXT NOT NULL DEFAULT '',
                       episode    TEXT NOT NULL DEFAULT '',
                       draft      TEXT NOT NULL DEFAULT '',
                       drafted_by TEXT NOT NULL DEFAULT '',
                       status     TEXT NOT NULL DEFAULT 'waiting',
                       decided_at REAL NOT NULL DEFAULT 0,
                       posted     TEXT NOT NULL DEFAULT '',
                       share_url  TEXT NOT NULL DEFAULT ''
                   )""")
            conn.execute("CREATE INDEX IF NOT EXISTS candidates_status"
                         " ON candidates(status, found_at)")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS runs (
                       at      REAL NOT NULL,
                       reads   INTEGER NOT NULL DEFAULT 0,
                       report  TEXT NOT NULL DEFAULT '{}'
                   )""")

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = store_db.connect(self.path, timeout=5.0, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    # -- candidates

    def known(self) -> set:
        return {r[0] for r in self._conn().execute("SELECT post_id FROM candidates")}

    def replied_authors(self, since: float) -> list:
        """Authors approved since `since`, and those with a card still
        waiting: one author a week means one card, not one per run."""
        rows = self._conn().execute(
            "SELECT post FROM candidates WHERE (status = ? AND decided_at >= ?)"
            " OR status = ?", (APPROVED, since, WAITING)).fetchall()
        return [json.loads(r[0]).get("author", "") for r in rows]

    def add(self, cand: Candidate, at: float) -> None:
        episode = asdict(cand.seed) if cand.seed is not None else {}
        self._conn().execute(
            "INSERT OR IGNORE INTO candidates (post_id, found_at, score, kind,"
            " reason, post, other, episode, draft, drafted_by)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cand.post.id, at, cand.score, cand.kind, cand.reason,
             json.dumps(asdict(cand.post)),
             json.dumps(asdict(cand.other)) if cand.other is not None else "",
             json.dumps(episode), cand.draft, cand.drafted_by))

    def get(self, post_id: str) -> Optional[dict]:
        rows = self.all(post_id=post_id)
        return rows[0] if rows else None

    def all(self, status: str = "", post_id: str = "", limit: int = 100) -> list[dict]:
        sql = ("SELECT post_id, found_at, score, kind, reason, post, other, episode,"
               " draft, drafted_by, status, decided_at, posted, share_url"
               " FROM candidates WHERE 1=1")
        args: list = []
        if status:
            sql += " AND status = ?"
            args.append(status)
        if post_id:
            sql += " AND post_id = ?"
            args.append(post_id)
        sql += (" ORDER BY CASE status WHEN 'waiting' THEN 0 ELSE 1 END,"
                " found_at DESC, score DESC LIMIT ?")
        args.append(int(limit))
        out = []
        for r in self._conn().execute(sql, args).fetchall():
            post = json.loads(r[5])
            post["url"] = Post(**post).url
            other = json.loads(r[6]) if r[6] else None
            if other:
                other["url"] = Post(**other).url
            out.append({
                "post_id": r[0], "found_at": r[1], "score": r[2], "kind": r[3],
                "kind_label": KINDS.get(r[3], r[3]), "reason": r[4],
                "post": post, "other": other,
                "episode": json.loads(r[7]) if r[7] else None,
                "draft": r[8], "drafted_by": r[9], "status": r[10],
                "decided_at": r[11], "posted": r[12], "share_url": r[13]})
        return out

    def decide(self, post_id: str, status: str, at: float, *, posted: str = "",
               share_url: str = "") -> bool:
        cur = self._conn().execute(
            "UPDATE candidates SET status = ?, decided_at = ?, posted = ?,"
            " share_url = ? WHERE post_id = ?",
            (status, at, posted, share_url, post_id))
        return cur.rowcount > 0

    def prune(self, now: float) -> int:
        cur = self._conn().execute("DELETE FROM candidates WHERE found_at < ?",
                                   (now - KEEP_DAYS * 86400,))
        self._conn().execute("DELETE FROM runs WHERE at < ?",
                             (now - 30 * 86400,))
        return cur.rowcount

    def counts(self) -> dict:
        rows = self._conn().execute(
            "SELECT status, COUNT(*) FROM candidates GROUP BY status").fetchall()
        return {s: n for s, n in rows}

    # -- runs

    def record_run(self, at: float, reads: int, report: dict) -> None:
        """Write a run's row, or rewrite it: a run's row is written before
        its first search and again after each, so reads X has billed are
        counted even when the run dies before it finishes."""
        cur = self._conn().execute("UPDATE runs SET reads = ?, report = ? WHERE at = ?",
                                   (int(reads), json.dumps(report), at))
        if cur.rowcount == 0:
            self._conn().execute("INSERT INTO runs (at, reads, report) VALUES (?, ?, ?)",
                                 (at, int(reads), json.dumps(report)))

    def reads_since(self, since: float) -> int:
        row = self._conn().execute("SELECT COALESCE(SUM(reads), 0) FROM runs"
                                   " WHERE at >= ?", (since,)).fetchone()
        return int(row[0] or 0)

    def last_run(self) -> Optional[dict]:
        row = self._conn().execute("SELECT at, reads, report FROM runs"
                                   " ORDER BY at DESC LIMIT 1").fetchone()
        if not row:
            return None
        return {"at": row[0], "reads": row[1], **json.loads(row[2] or "{}")}


_STORE: list = []
_STORE_LOCK = threading.Lock()


def _exists() -> bool:
    """Whether there is a store to read, without creating one: the health
    page must not be what makes this database on a machine that never ran."""
    if _STORE:
        return True
    try:
        # Its file, or its schema under Postgres (`db.has_tables`).
        return store_db.has_tables(data_path("X_THREADS_DB", "x_threads.db"))
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
# A run


def trending_now() -> list:
    """`[(story, episode_key)]` from the current Trending edition."""
    try:
        import trending_bank

        edition = trending_bank.current()
        if edition is None:
            return []
        live = {s.id for s in trending_bank.stories_now()}
        return [(s, (edition.episodes.get(s.id) or {}).get("key", ""))
                for s in edition.stories if s.id in live]
    except Exception as exc:  # noqa: BLE001
        log.warning("x threads: no Trending edition to seed from: %s", exc)
        return []


def _day_start(now: float) -> float:
    return now - (now % 86400)


async def run(cache, *, now: Optional[float] = None, searcher=None,
              drafter=None, held: Optional[Store] = None) -> dict:
    """Find, screen, score and draft; leave the cards on the page.

    Returns the run's report, which is also stored and shown: a run that
    found nothing says what it asked and why nothing qualified, because a
    quiet empty page looks exactly like a broken one ([failures-visible]).
    """
    now = time.time() if now is None else now
    held = held or store()
    held.prune(now)
    report: dict = {"seeds": [], "asked": 0, "read": 0, "screened_out": 0,
                    "cards": 0, "errors": [], "dollars": 0.0}
    if searcher is None:
        ok, why = available()
        if not ok:
            report["stopped"] = why
            held.record_run(now, 0, report)
            return report
        searcher = search
    room = min(config.settings.x_threads_max_reads,
               config.settings.x_threads_daily_reads - held.reads_since(_day_start(now)))
    if room < 10:
        report["stopped"] = ("today's X read ceiling (X_THREADS_DAILY_READS) "
                             "is used up")
        held.record_run(now, 0, report)
        return report
    chosen = await asyncio.to_thread(seeds, cache, trending=trending_now(),
                                     limit=config.settings.x_threads_seeds)
    report["seeds"] = [{"title": s.title, "search": s.search(), "origin": s.origin}
                       for s in chosen]
    if not chosen:
        report["stopped"] = ("no kept episode has a name to search for: write "
                             "the Trending edition or play some episodes first")
        held.record_run(now, 0, report)
        return report
    per = max(10, min(100, room // len(chosen)))
    since = now - config.settings.x_threads_window_hours * 3600
    found: list = []
    reads = 0
    held.record_run(now, 0, report)
    try:
        for seed in chosen:
            if reads + 10 > room:
                break
            ask = min(per, room - reads)
            try:
                posts = await searcher(seed.search(), max_results=ask, since=since)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - one seed never ends the run
                report["errors"].append(f"{seed.title}: {exc}"[:200])
                continue
            reads += len(posts)
            report["asked"] += 1
            report["read"] = reads
            # Counted the moment X has billed them ([failures-visible]).
            held.record_run(now, reads, report)
            found.extend((p, seed) for p in posts)
        known = held.known()
        fresh = [(p, s) for p, s in found if p.id not in known]
        # Embedding every post is CPU work; it never runs on the loop that
        # streams listeners' audio.
        # Twice the cards wanted, so a card the judgement drops can be
        # replaced from further down the ranking.
        picked = await asyncio.to_thread(
            shortlist, fresh, now,
            window_hours=config.settings.x_threads_window_hours,
            replied_to=held.replied_authors(now - 7 * 86400),
            size=2 * SHORTLIST)
        report["screened_out"] = len({p.id for p, _ in fresh}) - len(picked)
        report["off_subject"] = []
        for cand in picked:
            if report["cards"] >= SHORTLIST:
                break
            result = await (drafter or judge_and_draft)(cand)
            reply, by, dollars = result[:3]
            verdict = result[3] if len(result) > 3 else Verdict()
            report["dollars"] = round(report["dollars"] + dollars, 4)
            dropped = apply_verdict(cand, verdict)
            if dropped:
                report["off_subject"].append(f"@{cand.post.author}: {dropped}"[:200])
                continue
            if by != "code" and not verdict.episode_fits and cand.kind == QUESTION_ONLY \
                    and "episode" in reply.lower():
                # Told not to mention the episode, and did: the code's
                # question rather than words that point at nothing.
                reply, by = trim(fallback_draft(cand)), "code"
            cand.draft, cand.drafted_by = reply, by
            held.add(cand, now)
            report["cards"] += 1
    except asyncio.CancelledError:
        report["stopped"] = "the run was stopped before it finished"
        raise
    except Exception as exc:  # noqa: BLE001 - said on the page, then raised
        report["stopped"] = f"the run failed: {type(exc).__name__}: {exc}"[:300]
        raise
    finally:
        held.record_run(now, reads, report)
    return report


def health() -> dict:
    ok, why = available()
    out = {"available": ok, "detail": why,
           "max_reads": config.settings.x_threads_max_reads,
           "daily_reads": config.settings.x_threads_daily_reads,
           "window_hours": config.settings.x_threads_window_hours}
    if not _exists():
        out["counts"], out["reads_today"], out["last_run"] = {}, 0, None
        return out
    try:
        held = store()
        out["counts"] = held.counts()
        out["reads_today"] = held.reads_since(_day_start(time.time()))
        out["last_run"] = held.last_run()
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
    return out
