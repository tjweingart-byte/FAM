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

**High engagement, every kept episode, then a read** (§337): a post under
`X_THREADS_MIN_ENGAGEMENT` interactions is not a card; a post found from one
episode is matched against every kept episode (`outreach_intelligence.rank`),
so the episode that answers it is offered even when another one's search
found it; and the drafting call reads the post first - its subject, what it
is doing, which episode answers it, the angle FAM adds - from what the
episodes actually say (`outreach_intelligence.py`), then writes in X's voice:
curiosity, and being part of the conversation.

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
import outreach_intelligence as OI
from paths import data_path
from config import as_minutes

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
#: A post the relevance check judged to be about something else (§326):
#: kept only so the next search neither reads nor judges it again, and never
#: shown on the page.
SET_ASIDE = "set_aside"

#: Words in a question that say nothing about its subject: a post sharing
#: only these with an episode ("eagles today") is not on its subject.
GENERIC = frozenset("""
happened happening happen today tonight yesterday tomorrow latest news update
updates week weekend month year years still going really right think know
make made mean thing things people story explained explain whats what's
first last next good best worst big biggest
""".split())

#: The playbook's four kinds of reply (FAM_X.pdf, examples 01-04).
ANGLE_AND_EPISODE = "angle_episode"     # 01: another angle + episode
QUESTION_ONLY = "question"              # 02: ask a better question, no link
THREAD_TO_EPISODE = "thread_episode"    # 03: their question has an episode
BOTH_SIDES = "both_sides"               # 04: connect competing perspectives
KINDS = {
    ANGLE_AND_EPISODE: "Another angle + episode",
    QUESTION_ONLY: "A comment, no link",
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

#: The guardrails' relevance scale (§368, FAM_X_Marketing_Agent_Guardrails
#: §6): how much FAM's contribution has to do with what the ORIGINAL post
#: says. Judged by the read, never computed from keyword overlap.
RELEVANCE = {
    1: "Unrelated to the original post",
    2: "Same broad subject, wrong angle",
    3: "Relevant, but limited value",
    4: "Specific and substantively useful",
    5: "Addresses a central question or argument",
}
#: Under this a card is rejected; at it, a comment only, never an episode.
COMMENT_RELEVANCE = 3
#: Only from here may a card link or propose an episode.
EPISODE_RELEVANCE = 4
#: What a read in code is worth: code cannot apply the original-author
#: test, so it never puts an episode under a stranger's post on its own word.
CODE_RELEVANCE = 3

#: The three outcomes (§368): an episode is never the required result.
RECOMMEND_EPISODE, RECOMMEND_COMMENT, RECOMMEND_SKIP = "episode_comment", "comment", "skip"
RECOMMENDATIONS = {
    RECOMMEND_EPISODE: "Episode + comment",
    RECOMMEND_COMMENT: "Comment only",
    RECOMMEND_SKIP: "Skip",
}

#: How many replies a card reads for context, and the most shown to the read.
REPLY_CONTEXT = 5

#: What an advertisement sounds like (§368 rule 4). A reply that says any of
#: these is not part of the conversation; its words go back to the code's.
_PROMO = re.compile(
    r"\b(check (it|this|us|them) out|check out (our|the|this|my) (episode|pod|show|link)|"
    r"link (below|in bio)|listen now|tune in (now|here|to (our|the episode))|"
    r"don'?t miss (this|it|out)|must[- ]listen|game[- ]?changer|changing everything|"
    r"changes everything|you won'?t believe|"
    r"great (question|post|point|take|thread)|love this|so true|"
    r"mind[- ]?blowing|hit play|click here|follow us|our (new|latest) episode)\b"
    r"|!|#[A-Za-z]|[\U0001F300-\U0001FAFF\u2600-\u26FF]", re.I)

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
    #: The episodes the read chooses among, `[(Seed, match)]`, `seed` first
    #: (§337); empty means `seed` alone.
    options: list = field(default_factory=list)
    #: What each offered episode says (`outreach_intelligence.material`), by key.
    material: dict = field(default_factory=dict)
    #: What the drafting call understood about the post (`OI.Read.as_dict`).
    read: dict = field(default_factory=dict)
    #: Replies read as secondary context (§368): they may say what people are
    #: debating, never what the card is about.
    replies: list = field(default_factory=list)
    #: The guardrails' judgement (§368): relevance 1-5 and why, the original-
    #: author test, the central message, the recommendation, and a proposed
    #: episode ({title, scope, query}) when none kept answers the post.
    assess: dict = field(default_factory=dict)
    #: When a person heard the linked episode through its link (§368).
    listened_at: float = 0.0


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
    generic = {news_clusters._stem(w) for w in GENERIC}
    seen, out = set(), []
    for word in _WORD.findall(f"{seed.query} {seed.title}"):
        low = word.lower().strip("'.-")
        if (len(low) < 4 or low in _STOP or low in named or low in seen
                or low in GENERIC or low in news_clusters.STOPWORDS or low.isdigit()
                or news_clusters._stem(low) in generic):
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
        # A one-word name with no subject words beside it is not searched at
        # all: every post it found would be billed, then dropped by
        # `on_subject`.
    # Two one-word names together ("Trump" and "Zelensky", a Trending
    # story's anchors) are as specific as a two-word name.
    single = [n for n in names if n and " " not in n]
    if len(single) >= 2:
        terms.insert(0, f"({single[0]} {single[1]})")
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
    # Whole words: "Ford" is not in "afford"; "#Ford" and "Ford's" are.
    named = [n for n in seed.names if re.search(
        r"(?<![a-z0-9])" + re.escape(n.lower()) + r"(?![a-z0-9])", low)]
    if not named:
        return "does not name what the episode is about"
    if any(" " in n for n in named) or len(named) >= 2:
        return ""
    import news_clusters

    # Only the name it matched is taken away: "Defence" in a longer name it
    # did not match is still one of the episode's subject words.
    generic = {news_clusters._stem(w) for w in GENERIC}
    words = news_clusters.tokens(post.text) - generic - {
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
                            as_minutes(row.get("minutes") or 1), names[:3],
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
                        as_minutes(row.get("minutes") or 1), names, "cached",
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
            # A long post's `text` is cut; its whole words are `note_tweet`.
            id=str(row.get("id") or ""),
            text=str((row.get("note_tweet") or {}).get("text") or row.get("text") or ""),
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
                        "in_reply_to_user_id,possibly_sensitive,note_tweet",
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


def promotional(text: str) -> list[str]:
    """What in a reply sounds like an advertisement (§368 rule 4): generic
    praise, a pitch, manufactured urgency, hashtags, emoji, exclamation."""
    out = []
    for m in _PROMO.finditer(text or ""):
        hit = m.group(0).strip().lower()
        if hit and hit not in out:
            out.append(hit)
    return out


def anchored(reply: str, post_text: str) -> bool:
    """Whether a reply takes up something concrete the post itself says -
    shares one of its content words - rather than talking past it (§368
    rule 4: acknowledge something concrete from the original post)."""
    import news_clusters

    generic = {news_clusters._stem(w) for w in GENERIC}
    return bool((news_clusters.tokens(reply) - generic)
                & (news_clusters.tokens(post_text) - generic))


def replies_query(post: "Post") -> str:
    """X's search for the replies under one post: what the thread is
    debating, read as context only (§368)."""
    return f"conversation_id:{post.id} is:reply lang:en -is:retweet"


def thread_replies(post: "Post", found: Iterable["Post"],
                   limit: int = REPLY_CONTEXT) -> list["Post"]:
    """The replies worth showing the read, most engaged first: only replies,
    never the post itself, never one carrying a slur."""
    out = []
    for p in found:
        if p.id == post.id or not p.is_reply or not p.text:
            continue
        try:
            import content_filter

            if content_filter.scrub(p.text) != p.text:
                continue
        except Exception:  # noqa: BLE001
            pass
        out.append(p)
    out.sort(key=lambda p: -engagement(p))
    return out[:limit]


def engagement(post: Post) -> int:
    """Everything people did with a post: likes, replies, reposts, quotes."""
    return post.likes + post.replies + post.reposts + post.quotes


def screen(post: Post, now: float, *, window_hours: float,
           replied_to: Iterable[str] = (), min_engagement: int = 0) -> str:
    """Why this post is not a thread to join, or "" when it is. Code only."""
    if post.is_reply:
        return "a reply, not a conversation's start"
    if post.lang and post.lang != "en":
        return "not in English"
    if post.sensitive:
        return "marked sensitive"
    if post.created_at and now - post.created_at > window_hours * 3600:
        return "older than the window"
    if engagement(post) < min_engagement:
        return "too little engagement yet"
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


def match(post: Post, seed: Optional[Seed], about: Optional[list] = None,
          post_vector: Optional[list] = None) -> float:
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
        if post_vector is None:
            post_vector = embeddings.embed(post.text)
        semantic = max(0.0, embeddings.cosine(post_vector, about))
    except Exception:  # noqa: BLE001
        semantic = 0.0
    low = post.text.lower()
    named = sum(1 for n in seed.names if n.lower() in low) / max(len(seed.names), 1)
    return round(min(1.0, 0.6 * semantic + 0.4 * named), 4)


def score(post: Post, seed: Optional[Seed], now: float,
          about: Optional[list] = None,
          post_vector: Optional[list] = None) -> tuple[float, dict]:
    parts = {
        "match": match(post, seed, about, post_vector),
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
    engaged = engagement(post)
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


def _seed_of(episode: dict) -> Seed:
    """A catalogue entry (`outreach_intelligence.catalogue`) as a Seed."""
    return Seed(episode["key"], episode["query"], episode["title"],
                as_minutes(episode.get("minutes") or 1),
                tuple(episode.get("names") or ())[:3], "cached",
                int(episode.get("plays") or 0))


def shortlist(found: list[tuple[Post, Seed]], now: float, *, window_hours: float,
              replied_to: Iterable[str] = (), size: int = SHORTLIST,
              episodes: Optional[list] = None,
              min_engagement: Optional[int] = None) -> list[Candidate]:
    """Screen, score and choose: one card per author, best first.

    With `episodes` (an `outreach_intelligence.catalogue`), every post is
    also matched against every kept episode (§337): the episode whose search
    found a post is not assumed to be the one that answers it. Each card
    keeps its best `OI.OFFERED` on-subject episodes for the read to choose
    among; a post no episode is the subject of is not a card."""
    import embeddings

    if min_engagement is None:
        min_engagement = config.settings.x_threads_min_engagement
    replied_to = list(replied_to)
    best: dict = {}
    vectors: dict = {e["key"]: e.get("vector") for e in episodes or ()}
    ranked: dict = {}
    posts: dict = {}            # each post's vector, embedded once

    def about(option: Seed):
        if option.key not in vectors:
            try:
                vectors[option.key] = embeddings.embed(f"{option.title}. {option.query}")
            except Exception:  # noqa: BLE001
                vectors[option.key] = None
        return vectors[option.key]

    for post, seed in found:
        if screen(post, now, window_hours=window_hours, replied_to=replied_to,
                  min_engagement=min_engagement):
            continue
        options = [seed] if seed is not None and not on_subject(post, seed) else []
        if post.id not in posts:
            try:
                posts[post.id] = embeddings.embed(post.text)
            except Exception:  # noqa: BLE001
                posts[post.id] = None
        if episodes:
            if post.id not in ranked:
                # A name to check is what keeps a shared word from passing
                # as a shared subject; an episode with none is not offered.
                ranked[post.id] = [e for e in OI.rank(episodes, post.text,
                                                      limit=OI.OFFERED + 1,
                                                      post_vector=posts[post.id])
                                   if e["names"] and not e["off_subject"]]
            for e in ranked[post.id]:
                if all(o.key != e["key"] for o in options):
                    options.append(_seed_of(e))
        if not options:
            continue
        scored = sorted(((score(post, o, now, about(o), posts[post.id]), o)
                         for o in options),
                        key=lambda pair: -pair[0][0])
        (total, parts), primary = scored[0]
        kept = best.get(post.id)
        if kept is None or total > kept.score:
            kind = kind_of(post, parts)
            cand = Candidate(post, primary, total, parts, kind,
                             reason(post, primary, parts, kind, now))
            cand.options = [(o, p["match"]) for (_t, p), o in scored[:OI.OFFERED]]
            if seed is not None and primary.key != seed.key:
                cand.reason += (f" Found searching for “{seed.title}”; another kept "
                                "episode is closer to it.")
            best[post.id] = cand
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


#: The drafting call: Outreach Intelligence's read (§337), X's voice, and
#: what an X card needs besides - the both-sides pair, and the reply.
DRAFT_RULES = """FAM's question is not "where can I share an episode?" but \
"where is an interesting conversation FAM could make more interesting?" FAM \
does not interrupt the conversation; it adds to it.

The ORIGINAL post anchors everything. Replies, when given, are secondary \
context: they may show what people are debating, but they never set the \
subject, and nothing is proposed because of one reply.

Then judge, in this order, before the reply:
- central: what the original author actually means - their central \
question, claim, concern or perspective - in one sentence.
- author_would_see_it: if the original author saw FAM's comment (and the \
episode, if any), would they IMMEDIATELY understand why it relates to what \
THEY posted? This is a hard gate: false rejects everything, whatever else \
you decide. Broad topic overlap is not enough. When unsure, false.
- relevance: 1-5, how directly FAM's contribution engages the ORIGINAL post:
  1 unrelated to the original post; 2 same broad subject, wrong angle; \
3 relevant but limited value; 4 specific and substantively useful (answers \
a raised question, gives directly useful context, compares the perspectives \
in play, or makes an overlooked distinction); 5 addresses the post's \
central question or argument. Keywords in common are never above 2.
- relevance_why: one sentence justifying the number.
- recommendation: "episode_comment", "comment" or "skip". An episode is \
never required: a concise, informed comment or question often adds more \
than audio. Below 3, skip. At 3, comment at most. episode_comment only at \
4-5 and only when an episode (kept, or the one you propose) is about the \
post's own point - not the general topic. Prefer fewer, better \
contributions: skip anything generic.
- proposed_title, proposed_scope, proposed_query: when relevance is 4-5 and \
NO offered episode answers this post but one should exist - a specific \
title on the author's actual point (not "The Future of AI" but "Can AI \
Diagnose Diseases More Accurately Than Doctors?"), its scope in one \
sentence, and the question to type into FAM to write it. "" otherwise, and \
"" whenever choice is not -1.
- other_same_subject: when a second post is given, is it about that same \
subject and conversation? false when no second post is given.
- reply: the comment itself. Acknowledge something concrete the original \
post says; offer a thought, distinction, relevant perspective or genuinely \
useful question; it must be worth reading if nobody clicks anything. \
Answer THIS post's own question, claim or point - not the episode's angle, \
not a reply's tangent. Never a pitch, generic praise, keyword-stuffed hook \
or manufactured urgency. Mention FAM's episode only when an offered \
episode was chosen; never mention a proposed one (it does not exist yet). \
No link: one is added after your words when there is an episode.
Weak: "AI is changing everything! Check out our FAM episode." Better: "One \
distinction is whether AI replaces a doctor's judgment or improves the \
information used in diagnosis. Where do you think that line will be drawn?\""""

DRAFT_SYSTEM = OI.system(OI.X, DRAFT_RULES, DRAFT_CHARS)

DRAFT_SCHEMA = OI.schema({
    "central": {"type": "string"},
    "author_would_see_it": {"type": "boolean"},
    "relevance": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
    "relevance_why": {"type": "string"},
    "recommendation": {"type": "string", "enum": list(RECOMMENDATIONS)},
    "proposed_title": {"type": "string"},
    "proposed_scope": {"type": "string"},
    "proposed_query": {"type": "string"},
    "other_same_subject": {"type": "boolean"},
    "reply": {"type": "string"},
})


def offered(cand: Candidate) -> list:
    """The episodes this card's read chooses among, `seed` first."""
    if cand.options:
        return [seed for seed, _match in cand.options]
    return [cand.seed] if cand.seed is not None else []


def draft_prompt(cand: Candidate) -> str:
    lines = [f"The ORIGINAL post, by @{cand.post.author}:", cand.post.text.strip(), ""]
    if cand.replies:
        lines.append("Replies under it - secondary context only; they never set "
                     "the subject:")
        for r in cand.replies[:REPLY_CONTEXT]:
            lines.append(f"- @{r.author}: {' '.join(r.text.split())[:280]}")
        lines.append("")
    if cand.other is not None:
        lines += [f"Another side, by @{cand.other.author}:", cand.other.text.strip(), "",
                  "Connect the two perspectives: name the trade-off between them."]
    if cand.kind == QUESTION_ONLY or cand.seed is None:
        lines.append("FAM has no kept episode to link here: choice is -1. Comment "
                     "or ask a better question - one that helps people in the "
                     "thread see what they disagree about. Do not mention FAM or "
                     "an episode. If relevance is 4-5 you may propose the episode "
                     "that should exist.")
        if cand.seed is not None:
            lines.append(f"(The nearest episode, for the read only: “{cand.seed.title}”.)")
    else:
        lines += ["FAM's kept episodes, closest first:",
                  OI.options_block([asdict(o) for o in offered(cand)], cand.material)]
    lines.append("Judge relevance in the `why` field in one sentence: what the "
                 "post is about, and whether the chosen episode is that.")
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
    #: Which offered episode answers the post (`offered(cand)`), when it fits.
    choice: int = 0
    #: The whole read (§337), shown on the card.
    read: Optional[dict] = None
    #: Why the words were replaced by the code's, or "".
    replaced: str = ""
    #: The guardrails (§368). The defaults are a read that found nothing
    #: wrong; every path that did not read with a model sets
    #: `relevance=CODE_RELEVANCE`, so code never links on its own word.
    relevance: int = EPISODE_RELEVANCE
    relevance_why: str = ""
    #: The original-author test: True passed, False failed (a hard gate),
    #: None not run - a read in code cannot ask it.
    author_test: Optional[bool] = True
    central: str = ""
    recommendation: str = ""
    #: `{title, scope, query}` of an episode that should exist, or None.
    proposed: Optional[dict] = None

    def assess(self) -> dict:
        return {"relevance": self.relevance, "relevance_why": self.relevance_why,
                "author_test": self.author_test, "central": self.central,
                "recommendation": self.recommendation,
                "proposed": dict(self.proposed) if self.proposed else None}


def _code_verdict(options, text: str, why: str, *, episode_fits: bool = True) -> Verdict:
    """The verdict when no model read the post: the code's gates have
    passed it, but relevance is capped at a comment (§368) and the card
    says why."""
    return Verdict(episode_fits=episode_fits, why=why,
                   read=OI.code_read(text, options, why=why).as_dict(),
                   relevance=CODE_RELEVANCE, recommendation=RECOMMEND_COMMENT,
                   relevance_why="not judged by a model, so a comment at most",
                   author_test=None)


def _proposal(body: dict) -> Optional[dict]:
    title = OI._line(body.get("proposed_title"), 140)
    query = OI._line(body.get("proposed_query"), 200)
    if not title or not query:
        return None
    return {"title": title, "scope": OI._line(body.get("proposed_scope")),
            "query": query}


async def draft(cand: Candidate, client=None) -> tuple[str, str, float]:
    """`(reply, drafted_by, dollars)`, for a caller that wants the words only
    (`judge_and_draft` is what a run uses)."""
    reply, by, dollars, _verdict = await judge_and_draft(cand, client)
    return reply, by, dollars


async def judge_and_draft(cand: Candidate, client=None) -> tuple:
    """`(reply, drafted_by, dollars, verdict)`: one call that first reads the
    post (§337) - what it is about, what it is doing, which offered episode
    answers it - then writes the reply to the post. Never raises but
    cancellation: a model that cannot be asked leaves the code's draft and
    the code's gate (`on_subject`), said so on the card."""
    import credentials
    import metering

    options = offered(cand)
    if client is None and not credentials.active("ANTHROPIC_API_KEY"):
        return trim(fallback_draft(cand)), "code", 0.0, _code_verdict(
            options, cand.post.text, "no model key, so read in code")
    model = config.settings.x_threads_model
    try:
        if client is None:
            from anthropic_client import build_async_client

            client = build_async_client(credentials.active("ANTHROPIC_API_KEY"))
        response = await client.messages.create(
            model=model, max_tokens=900, system=DRAFT_SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": DRAFT_SCHEMA}},
            messages=[{"role": "user", "content": draft_prompt(cand)}])
        usage = metering.Usage()
        usage.add_model_call(model, getattr(response, "usage", None))
        dollars = metering.price_of(usage).total
        text = next(b.text for b in response.content if b.type == "text")
        body = json.loads(text)
        read = OI.parse(body, len(options), model)
        try:
            relevance = max(1, min(5, int(body.get("relevance"))))
        except (TypeError, ValueError):
            # An answer with no number is not a judgement: a comment at most.
            relevance = CODE_RELEVANCE
        recommendation = str(body.get("recommendation") or "")
        if recommendation not in RECOMMENDATIONS:
            recommendation = ""
        verdict = Verdict(same_subject=read.same_subject,
                          episode_fits=read.choice >= 0,
                          other_same_subject=bool(body.get("other_same_subject")),
                          why=read.why, choice=max(read.choice, 0),
                          read=read.as_dict(), relevance=relevance,
                          relevance_why=OI._line(body.get("relevance_why")),
                          author_test=body.get("author_would_see_it") is not False,
                          central=OI._line(body.get("central")),
                          recommendation=recommendation,
                          proposed=_proposal(body) if read.choice < 0 else None)
        reply = trim(body.get("reply") or "")
        if not reply:
            # The judgement stands; only the words are the code's.
            return trim(fallback_draft(cand)), "code", dollars, verdict
        chosen = options[verdict.choice] if verdict.episode_fits and options else None
        said = cand.material.get(chosen.key) if chosen is not None else None
        stray = OI.unsupported_figures(reply, OI.sources_for(
            "\n".join([cand.post.text] + ([cand.other.text] if cand.other else [])),
            asdict(chosen) if chosen is not None else None, said))
        if stray:
            # A figure FAM has not got is not said under its name.
            verdict.replaced = (f"the drafted reply named {', '.join(stray[:3])}, "
                                "which neither the post nor the episode states")
            return trim(fallback_draft(cand)), "code", dollars, verdict
        pitch = promotional(reply)
        if pitch:
            # An advertisement is not part of the conversation (§368).
            verdict.replaced = (f"the drafted reply sounded like an ad "
                                f"({', '.join(pitch[:3])})")
            return trim(fallback_draft(cand)), "code", dollars, verdict
        return reply, model, dollars, verdict
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("x threads: drafting fell back to code: %s", exc)
        # The relevance check did not run: the code's gate has passed the
        # post, but no episode is linked on its word alone.
        why = "the relevance check could not run, so no link"
        return trim(fallback_draft(cand)), "code", 0.0, _code_verdict(
            options, cand.post.text, why, episode_fits=False)


def apply_verdict(cand: Candidate, verdict: Verdict) -> str:
    """Fold the judgement into the card. Returns why the card is dropped, or
    "" when it stays - the post itself is about something else."""
    if verdict.read:
        cand.read = dict(verdict.read)
    cand.assess = verdict.assess()
    if not verdict.same_subject:
        return verdict.why or "the post is about something else"
    # The guardrails' hard gates (§368), whatever the score.
    if verdict.author_test is False:
        return ("the original author would not see why this relates to their post"
                + (f": {verdict.relevance_why or verdict.why}"
                   if (verdict.relevance_why or verdict.why) else ""))
    if verdict.relevance < COMMENT_RELEVANCE:
        return (f"relevance {verdict.relevance}/5 ({RELEVANCE[verdict.relevance].lower()})"
                + (f": {verdict.relevance_why}" if verdict.relevance_why else ""))
    if verdict.recommendation == RECOMMEND_SKIP:
        return "the read recommends skipping" + (
            f": {verdict.relevance_why}" if verdict.relevance_why else "")
    if verdict.relevance < EPISODE_RELEVANCE or verdict.recommendation == RECOMMEND_COMMENT:
        # Relevant, but a comment is the contribution: no link, no proposal.
        comment_only = True
        verdict.episode_fits = False
        verdict.proposed = None
        cand.assess["proposed"] = None
    else:
        comment_only = False
    options = cand.options or []
    if verdict.episode_fits and 0 < verdict.choice < len(options):
        if options[verdict.choice][1] >= LINK_FLOOR:
            # The read chose another kept episode as the one that answers it.
            cand.seed, cand.parts["match"] = options[verdict.choice]
            cand.reason += (f" The read chose “{cand.seed.title}” as the episode"
                            " that answers it.")
        else:
            # The words were written from an episode too far from the post
            # to link; linking the first one instead would put one
            # episode's words over another's link.
            verdict.episode_fits = False
            cand.reason += (f" The read chose “{options[verdict.choice][0].title}”,"
                            " too far from the post to link.")
    if cand.other is not None and not verdict.other_same_subject:
        cand.other = None
        cand.kind = THREAD_TO_EPISODE if asks(cand.post.text) else ANGLE_AND_EPISODE
        cand.reason = cand.reason.split(" Another side from")[0]
    if not verdict.episode_fits and cand.kind != QUESTION_ONLY:
        cand.kind = QUESTION_ONLY
        cand.other = None
        cand.reason += (f" Relevance {verdict.relevance}/5: a comment is the contribution, so no link."
                        if comment_only else
                        " Same subject, but the episode does not answer this post, so no link.")
    if verdict.why:
        cand.reason += f" Checked: {verdict.why}"
    if verdict.replaced:
        cand.reason += f" Rewritten in code: {verdict.replaced}."
    # What the card recommends, as it now stands: an episode only when one
    # is linked; otherwise a comment (a proposal waits to be written).
    cand.assess["recommendation"] = (RECOMMEND_EPISODE if cand.kind != QUESTION_ONLY
                                     else RECOMMEND_COMMENT)
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
            # §337: what the drafting call understood about the post. Added
            # after the table shipped, so an existing file is widened.
            # §368: the guardrails' judgement, the replies read for context,
            # its relevance for ordering, and when the episode was heard.
            for column, kind in (("reading", "TEXT NOT NULL DEFAULT ''"),
                                 ("assess", "TEXT NOT NULL DEFAULT ''"),
                                 ("replies", "TEXT NOT NULL DEFAULT ''"),
                                 ("relevance", "INTEGER NOT NULL DEFAULT 0"),
                                 ("listened_at", "REAL NOT NULL DEFAULT 0")):
                try:
                    conn.execute(f"ALTER TABLE candidates ADD COLUMN {column} {kind}")
                except Exception:  # noqa: BLE001 - already there
                    pass
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

    def add(self, cand: Candidate, at: float, status: str = WAITING) -> None:
        episode = asdict(cand.seed) if cand.seed is not None else {}
        self._conn().execute(
            "INSERT OR IGNORE INTO candidates (post_id, found_at, score, kind,"
            " reason, post, other, episode, draft, drafted_by, status, reading,"
            " assess, replies, relevance)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cand.post.id, at, cand.score, cand.kind, cand.reason,
             json.dumps(asdict(cand.post)),
             json.dumps(asdict(cand.other)) if cand.other is not None else "",
             json.dumps(episode), cand.draft, cand.drafted_by, status,
             json.dumps(cand.read) if cand.read else "",
             json.dumps(cand.assess) if cand.assess else "",
             json.dumps([asdict(r) for r in cand.replies]) if cand.replies else "",
             int((cand.assess or {}).get("relevance") or 0)))

    def get(self, post_id: str) -> Optional[dict]:
        """One card by its post, or None - a set-aside post is not a card,
        so nothing can approve or reopen it by its id."""
        rows = [r for r in self.all(post_id=post_id) if r["status"] != SET_ASIDE]
        return rows[0] if rows else None

    def all(self, status: str = "", post_id: str = "", limit: int = 100) -> list[dict]:
        sql = ("SELECT post_id, found_at, score, kind, reason, post, other, episode,"
               " draft, drafted_by, status, decided_at, posted, share_url, reading,"
               " assess, replies, relevance, listened_at"
               " FROM candidates WHERE 1=1")
        args: list = []
        if status:
            sql += " AND status = ?"
            args.append(status)
        elif not post_id:
            sql += " AND status != ?"
            args.append(SET_ASIDE)
        if post_id:
            sql += " AND post_id = ?"
            args.append(post_id)
        # Waiting first; within a run, a 5 (the post's central question)
        # before a 4 before a comment (§368).
        sql += (" ORDER BY CASE status WHEN 'waiting' THEN 0 ELSE 1 END,"
                " found_at DESC, relevance DESC, score DESC LIMIT ?")
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
                "decided_at": r[11], "posted": r[12], "share_url": r[13],
                "read": json.loads(r[14]) if r[14] else None,
                "read_line": OI.summary(json.loads(r[14]) if r[14] else None),
                **_assessed(json.loads(r[15]) if r[15] else {}, r[3]),
                "replies": [{**x, "url": Post(**x).url}
                            for x in (json.loads(r[16]) if r[16] else [])],
                "listened_at": r[18] or 0.0})
        return out

    def listened(self, post_id: str, at: float) -> bool:
        """A person heard the card's episode through its link (§368)."""
        cur = self._conn().execute(
            "UPDATE candidates SET listened_at = ? WHERE post_id = ?", (at, post_id))
        return cur.rowcount > 0

    def attach(self, post_id: str, seed: "Seed", kind: str, reason: str) -> bool:
        """Put the episode written for a card's proposal on it (§368). It
        has not been heard through its link yet, so `listened_at` is reset."""
        row = self._conn().execute("SELECT assess FROM candidates WHERE post_id = ?",
                                   (post_id,)).fetchone()
        if row is None:
            return False
        assess = json.loads(row[0]) if row[0] else {}
        assess["recommendation"] = RECOMMEND_EPISODE
        # The proposal is written now: the card links it, it no longer proposes.
        assess["written_from"] = assess.pop("proposed", None)
        assess["proposed"] = None
        cur = self._conn().execute(
            "UPDATE candidates SET episode = ?, kind = ?, reason = ?, assess = ?,"
            " listened_at = 0 WHERE post_id = ?",
            (json.dumps(asdict(seed)), kind, reason, json.dumps(assess), post_id))
        return cur.rowcount > 0

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


def _assessed(assess: dict, kind: str) -> dict:
    """A card's guardrail fields for the page (§368). A card from before
    them has no score: it says so rather than inventing one."""
    relevance = int(assess.get("relevance") or 0)
    recommendation = assess.get("recommendation") or (
        RECOMMEND_COMMENT if kind == QUESTION_ONLY else RECOMMEND_EPISODE)
    return {"relevance": relevance,
            "relevance_label": RELEVANCE.get(relevance, "Not scored"),
            "relevance_why": assess.get("relevance_why") or "",
            # True passed, None not run (read in code); a failed one is never
            # a card, only a rejection.
            # A card from before §368 was never asked: not run, never "passed".
            "author_test": assess.get("author_test"),
            "central": assess.get("central") or "",
            "recommendation": recommendation,
            "recommendation_label": RECOMMENDATIONS.get(recommendation, recommendation),
            "proposed": assess.get("proposed") or None,
            "anchored": assess.get("anchored", True) is not False}


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
              drafter=None, held: Optional[Store] = None, replier=None) -> dict:
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
        # The replies under a card are read with the same search, and
        # billed and counted the same way (§368).
        replier = replier or search
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
    # An episode with nothing specific to ask X for is not searched, and
    # takes no share of the reads (said, not counted as a failure).
    report["unsearchable"] = [s.title for s in chosen if not s.search()]
    chosen = [s for s in chosen if s.search()]
    if not chosen:
        report["stopped"] = ("no episode has a name specific enough to search X "
                             "for: write the Trending edition or play some episodes")
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
        # Every kept episode, not only the ones searched for (§337): the
        # episode that answers a post may not be the one that found it.
        episodes = await asyncio.to_thread(OI.catalogue, cache)
        picked = await asyncio.to_thread(
            shortlist, fresh, now,
            window_hours=config.settings.x_threads_window_hours,
            replied_to=held.replied_authors(now - 7 * 86400),
            size=2 * SHORTLIST, episodes=episodes)
        report["screened_out"] = len({p.id for p, _ in fresh}) - len(picked)
        report["off_subject"] = []
        for cand in picked:
            if report["cards"] >= SHORTLIST:
                break
            # What each offered episode says, so the read writes from it.
            cand.material = await asyncio.to_thread(
                lambda: {s.key: OI.material(cache, s.key) for s in offered(cand)})
            # The replies, as secondary context (§368): only for a card about
            # to be read, only inside the ceilings, never a failure.
            want = config.settings.x_threads_reply_reads
            if replier is not None and want > 0 and reads + 10 <= room \
                    and cand.post.replies > 0:
                try:
                    under = await replier(replies_query(cand.post),
                                          max_results=min(max(want, 10), room - reads),
                                          since=max(cand.post.created_at - 60, since))
                    reads += len(under)
                    report["read"] = reads
                    held.record_run(now, reads, report)
                    cand.replies = thread_replies(cand.post, under)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - context, not the card
                    report["errors"].append(f"replies to @{cand.post.author}: {exc}"[:200])
            result = await (drafter or judge_and_draft)(cand)
            reply, by, dollars = result[:3]
            verdict = result[3] if len(result) > 3 else Verdict()
            report["dollars"] = round(report["dollars"] + dollars, 4)
            had_other = cand.other is not None
            dropped = apply_verdict(cand, verdict)
            if dropped:
                report["off_subject"].append(f"@{cand.post.author}: {dropped}"[:200])
                # Remembered, unseen, so it is neither read nor judged again.
                cand.reason = f"Set aside: {dropped}"
                held.add(cand, now, status=SET_ASIDE)
                continue
            if by == "code" or (had_other and cand.other is None):
                # The code's words name the card's episode as it now stands
                # (the read may have changed it), and a reply written to
                # connect two posts, one of which is gone, is the code's.
                reply, by = trim(fallback_draft(cand)), "code"
            elif by != "code" and cand.kind == QUESTION_ONLY \
                    and "episode" in reply.lower():
                # A card with no link never says FAM has an episode - also when
                # the read chose one too far from the post to link (§368).
                # Told not to mention the episode, and did: the code's
                # question rather than words that point at nothing.
                reply, by = trim(fallback_draft(cand)), "code"
            cand.draft, cand.drafted_by = reply, by
            # Shown, not enforced: a person rewrites a reply that talks past
            # the post (§368 rule 4: acknowledge something concrete).
            cand.assess = {**(cand.assess or {}),
                           "anchored": anchored(reply, cand.post.text)}
            held.add(cand, now)
            report["cards"] += 1
            outcome = (cand.assess or {}).get("recommendation") or RECOMMEND_COMMENT
            report.setdefault("outcomes", {}).setdefault(outcome, 0)
            report["outcomes"][outcome] += 1
            if (cand.assess or {}).get("proposed"):
                report["proposed"] = report.get("proposed", 0) + 1
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
           "min_engagement": config.settings.x_threads_min_engagement,
           "max_reads": config.settings.x_threads_max_reads,
           "daily_reads": config.settings.x_threads_daily_reads,
           "window_hours": config.settings.x_threads_window_hours,
           "reply_reads": config.settings.x_threads_reply_reads,
           "relevance": RELEVANCE, "recommendations": RECOMMENDATIONS}
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


# --------------------------------------------------------------------------
# A proposal, once written (§368)


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


def find_written(cache, card: dict) -> Optional[Seed]:
    """The kept episode written for a card's proposed episode, or None.

    The proposal's own question first, as typed; then - because FAM
    corrects a search at send ([autocorrect-at-send]) - the kept episode
    closest to the proposal's title and question, if it is close and on the
    post's subject. Code only; a person listens before it is linked."""
    proposed = card.get("proposed") or {}
    if not proposed.get("query") or cache is None:
        return None
    episodes = OI.catalogue(cache)
    want = _norm(proposed["query"])
    for e in episodes:
        if _norm(e["query"]) == want:
            return _seed_of(e)
    text = f"{proposed.get('title', '')}. {proposed['query']}"
    post_text = (card.get("post") or {}).get("text", "")
    for e in OI.rank(episodes, text, limit=3):
        if e["match"] >= WRITTEN_MATCH and not OI.off_subject(post_text, e):
            return _seed_of(next(x for x in episodes if x["key"] == e["key"]))
    return None


#: How close a kept episode must be to a proposal to be the one written for it.
WRITTEN_MATCH = 0.6


def attach_written(cache, post_id: str, held: Optional[Store] = None) -> dict:
    """Match a card's proposal against what is kept now; on a find, the card
    links that episode (still unheard). Returns the card. Raises KeyError
    for no card, LookupError when nothing written answers the proposal."""
    held = held or store()
    card = held.get(post_id)
    if card is None:
        raise KeyError(post_id)
    if card.get("status") == APPROVED:
        # What was handed to X is what was approved; it never changes under it.
        raise LookupError("This reply is approved. Put it back to waiting first.")
    if not card.get("proposed"):
        raise LookupError("This card proposes no episode.")
    seed = find_written(cache, card)
    if seed is None:
        raise LookupError("No kept episode answers the proposal yet. Search its "
                          "question in FAM, let it finish, then match again.")
    kind = THREAD_TO_EPISODE if asks(card["post"]["text"]) else ANGLE_AND_EPISODE
    reason = (card["reason"] + f" “{seed.title}” was written for this card's "
              "proposal; listen before linking it.")
    held.attach(post_id, seed, kind, reason)
    return held.get(post_id) or {}
