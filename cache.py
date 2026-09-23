"""Shared episode cache: the script, and since §132 the audio beside it.

The script is what costs a model call, so it is what decides whether an
episode exists at all: it is keyed, shared, near-matched and expired here, and
everything else hangs off it.

**The audio is kept too, at the owner's direction (PROBLEMS.md §132).** This
file used to say the opposite - "synthesis costs essentially nothing, so store
the script and re-synthesise" - and that was true of a GPU in the same process.
It stopped being true when the voice moved to RunPod: every replay of a cached
episode, every Explore card and every shared link paid a round trip to a rented
GPU for audio that had already been made once. The GPU turned out to be the
largest line on the bill, and a few megabytes of SQLite are not.

So the first time an episode is spoken in a production voice, its PCM is kept
in `episode_audio`, beside the script it was made from, and every later play
of it is read from here and **never reaches the voice engine**. The rules that
keep that honest:

* the audio row lives and dies with its script row - it is only readable while
  the script is, a re-written script drops the audio it no longer matches, and
  a wipe or purge takes both
* it is keyed on the voice as well as the script, because a voice changes the
  audio and not the words
* only a production voice is ever kept: a placeholder tone written here would
  be a failure that outlives the outage that caused it (§51)
* it is raw PCM, zlib-compressed, and still streamed as raw PCM - there is no
  audio *file* anywhere, which is the settled constraint's actual subject
* the table has a byte ceiling (`AUDIO_CACHE_MAX_MB`), least recently played
  first out, because the deployment's disk is small and an evicted episode
  costs one re-synthesis rather than anything a listener loses

Storage is SQLite because it needs no new dependency, survives restarts, and -
unlike a dict in the process - is shared by every worker on the machine, which
is the whole point when the users are different people. Swap in Redis by
implementing the same two methods if you run more than one machine.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import zlib
from dataclasses import dataclass, field
from datetime import datetime
import sqlite3
import threading
import time
from typing import Optional, Protocol

import embeddings
from config import settings
from paths import data_path

log = logging.getLogger(__name__)

_PUNCT = re.compile(r"[^\w\s]")
_SPACE = re.compile(r"\s+")
# Words that carry no topic meaning, so "give me a recap of week 5" and
# "week 5 recap" land on the same entry.
_FILLER = {
    "a", "an", "the", "of", "for", "on", "in", "to", "and", "is", "are", "was",
    "were", "please", "give", "me", "tell", "about", "what", "whats", "who",
    "how", "why", "can", "you", "i", "want", "would", "like", "do", "does",
    "explain", "describe", "summarize", "summarise", "recap", "briefing",
    "podcast", "episode", "some", "any", "there", "this", "that", "with",
}
# Words that make a question a *current* one rather than a durable one.
#
# Deliberately broad. The model's own knowledge has a cutoff - Sonnet 5's is
# January 2026 - so anything with a present state has probably moved since,
# and a question does not have to say "latest" to be asking about now. "Who
# runs OpenAI" contains no time word at all and is exactly the kind of thing
# that goes stale.
#
# Over-triggering is close to free since PROBLEMS.md 56: a researched episode
# answers from knowledge immediately and the research lands underneath, so a
# wrong "yes" costs a background call rather than a wait the listener hears.
# A wrong "no" costs a confidently dated answer, which is much worse. Tuned
# accordingly.
_VOLATILE = {
    # explicit time
    "latest", "today", "todays", "tonight", "now", "current", "currently",
    "live", "breaking", "update", "updates", "updated", "happening",
    "right", "moment", "just", "recent", "recently", "this", "upcoming",
    "tomorrow", "yesterday", "week", "month", "season", "lately", "still",
    "anymore", "nowadays", "since", "ongoing", "modern",
    # who holds a position, which changes without announcing itself
    "ceo", "cfo", "cto", "president", "chairman", "chairwoman", "chair",
    "minister", "premier", "governor", "senator", "mayor", "pope", "king",
    "queen", "coach", "manager", "captain", "owner", "leader", "boss",
    "founder", "successor", "replacement", "hired", "fired", "resigned",
    "stepped", "appointed", "elected", "runs", "leads", "heads", "owns",
    # numbers that move
    "price", "prices", "cost", "costs", "worth", "valuation", "value",
    "stock", "stocks", "share", "shares", "market", "rate", "rates",
    "revenue", "earnings", "profit", "salary", "score", "scores", "record",
    "standings", "ranking", "rankings", "odds", "inflation", "rally",
    # things in progress
    "election", "war", "trial", "lawsuit", "strike", "merger", "acquisition",
    "launch", "launched", "release", "released", "playoffs", "tournament",
    "championship", "final", "finals", "draft", "negotiation", "negotiations",
    "ceasefire", "deal", "ban", "tariff", "tariffs", "ruling", "verdict",
    "outage", "recall", "shortage", "crisis",
    # a superlative is a claim about right now
    "best", "top", "biggest", "largest", "newest", "leading", "fastest",
    "cheapest", "popular", "trending", "winning", "won", "beat",
    # versions and models supersede each other constantly
    "version", "release", "model", "generation", "successor",
}

#: A year at or after this is asking about the present, whatever else it says.
#: Computed rather than written down, so it does not quietly rot.
_RECENT_YEAR = re.compile(r"\b(20\d\d)\b")


# Signals the query is about the person asking, which must never be shared.
# Deliberately limited to possessives and contact details: "me", "I" and "we"
# appear in perfectly ordinary phrasing ("give me a recap of week 5") and
# treating those as personal silently disables the cache for most real traffic.
_PERSONAL = re.compile(
    r"\b(my|mine|our|ours)\b"          # possessives - "my lab results"
    r"|[\w.+-]+@[\w-]+\.[\w.]+"        # email addresses
    r"|\b\d{3}[-.]?\d{3}[-.]?\d{4}\b",  # phone numbers
    re.I,
)


def normalize_query(query: str) -> str:
    """Reduce a query to a canonical topic key.

    Lowercase, strip punctuation, drop filler words, sort the remainder. So all
    of these collapse to the same key:

        "Give me a recap of week 5 of the NFL season"
        "NFL week 5 recap"
        "recap: week 5, NFL"
    """
    text = _PUNCT.sub(" ", query.lower())
    tokens = [t for t in _SPACE.split(text) if t and t not in _FILLER]
    return " ".join(sorted(set(tokens)))


def is_shareable(query: str) -> bool:
    """Should this query's output ever be served to a different person?

    Anything that reads as personal is generated fresh and never stored. This is
    a blunt heuristic, deliberately biased towards not sharing.
    """
    return not _PERSONAL.search(query)


def research_words() -> set:
    """The words that make a question a researched one.

    Served to the interface so it can say "checking recent sources" *before*
    the request goes out, from the same list the server decides with. A second
    copy in JavaScript would drift, and the two would disagree about what the
    listener was told.
    """
    return set(_VOLATILE)


def research_reason(query: str, now: Optional[float] = None) -> str:
    """Why this question should be researched, or "" if it should not.

    Returns a reason rather than a bool so the log can say what tripped it -
    a heuristic nobody can see the workings of is a heuristic nobody can tune.

    Deliberately a keyword test, not a model call: classifying the query with a
    model puts a round trip in front of the first word, which is the one cost
    this product refuses.

    **This no longer decides whether an episode is researched** (PROBLEMS.md
    76). Production is `SEARCH_MODE=always`; this is consulted only under
    `SEARCH_MODE=auto`, and by the cache, which still needs to know how long an
    answer stays true. Widening it changes what `auto` does and how long
    entries live - not what a listener hears.
    """
    text = query.lower()
    tokens = set(_SPACE.split(_PUNCT.sub(" ", text)))

    hits = tokens & _VOLATILE
    if hits:
        return f"mentions {', '.join(sorted(hits)[:3])}"

    # A recent year is a question about the present however it is phrased.
    # "in 2026" needs research; "in 1789" does not.
    this_year = datetime.fromtimestamp(now or time.time()).year
    for match in _RECENT_YEAR.finditer(text):
        if int(match.group(1)) >= this_year - 1:
            return f"asks about {match.group(1)}"

    # "Who is/runs/leads X" is a question about a seat someone currently holds,
    # and seats change without the question changing.
    if re.search(r"\bwho(?:'s|s)?\b.{0,20}\b(is|are|was|runs|leads|owns|heads|won|makes)\b", text):
        return "asks who currently holds a position"

    # "How many/much X does Y have" is a number that moves.
    if re.search(r"\bhow (?:many|much)\b", text):
        return "asks for a number that may have moved"

    return ""


def needs_fresh_information(query: str) -> bool:
    """Does answering this honestly require something that happened recently?"""
    return bool(research_reason(query))


#: What a scheduled event's episode is good for. Shorter than the ordinary
#: ceiling because the thing is coming: a preview written this morning is
#: honest this afternoon and wrong once it kicks off.
SCHEDULED_TTL_SECONDS = 1800


def ttl_for(query: str, *, live_status: str = "", outcome_dependent: bool = False,
            recency_days: int = 0) -> int:
    """How long a script stays usable, in seconds. **Zero means do not cache.**

    **The question this asks changed, and that is the whole of PROBLEMS.md §89.**
    It used to ask "do the words of this query *look* volatile", answered from
    `_VOLATILE`. That is a guess made before anything is known, and it was
    wrong in the most expensive possible direction: `"Chiefs game"` contains no
    volatile word, so an episode about a game in progress was cached for
    **twenty-four hours** and served to everyone who asked - and, because
    `recent()` is the Explore feed, entered Explore as well.

    Widening the keyword list is not the fix and must not be attempted. §76
    already settled that for research: a keyword list can always be widened by
    one more word, and the next query it misses is already written. "Chiefs",
    "score" and "game" would have missed "how is the match going".

    So it now asks **how long what this episode says will stay true**, which is
    answerable, because by the time anything is written we know what it was
    built from. In precedence order, most authoritative first:

    1. **A live fact's status**, which is the only thing here established by
       evidence rather than inferred. `in_progress` is uncacheable outright -
       no TTL is short enough for a score, and a ten-second entry still serves
       one listener the state another listener already saw change.
       `final` does not move, so it keeps the ordinary ceiling.
    2. **The brief's `outcome_dependent`**, which is EI's honest statement that
       the listener wants a *result*. Volatile until one exists. This is the
       half that works with no provider configured at all, and it is what
       actually fixes the `"Chiefs game"` case today.
    3. **The evidence window.** An episode written from evidence that had to be
       a day old is a claim about that day.
    4. **The keyword list**, unchanged, as the floor for the paths that have
       none of the above - `EPISODE_INTELLIGENCE=0`, offline `write.py`,
       `tools/seed_demo.py`. Never widened.

    Ordinary static content is untouched: with no live fact, no brief and no
    volatile word, this returns exactly what it always returned.
    """
    tokens = set(_SPACE.split(_PUNCT.sub(" ", query.lower())))
    keyword_ttl = (settings.cache_ttl_volatile if tokens & _VOLATILE
                   else settings.cache_ttl_seconds)

    status = (live_status or "").strip().lower()
    if status == "in_progress":
        return 0
    if status == "scheduled":
        return min(keyword_ttl, SCHEDULED_TTL_SECONDS)
    if status == "final":
        # Settled by evidence and it does not move again. The ordinary ceiling
        # is right, and shortening it here would throw away the shared-cache
        # discount on exactly the episodes most worth sharing.
        return keyword_ttl

    # `unknown`, or no live fact at all. Fall through to what the request and
    # the evidence say, never to a claim that the event is over.
    if outcome_dependent:
        return min(keyword_ttl, settings.cache_ttl_volatile)
    if recency_days and int(recency_days) <= 1:
        return min(keyword_ttl, settings.cache_ttl_volatile)
    return keyword_ttl


def cache_key(
    query: str,
    minutes: int,
    canonical: Optional[str] = None,
    canonical_context: str = "",
    searched: bool = False,
) -> str:
    """Key on the canonical topic, the duration, and the voice settings.

    Duration is part of the key because a 3-minute episode is structured
    differently from a trimmed 10-minute one - it is not the same script cut
    short. Model is included so changing MODEL doesn't serve stale output from
    the previous one.
    """
    payload = json.dumps(
        {
            "q": canonical or normalize_query(query),
            "m": int(minutes),
            # A follow-up is a different episode from the same words asked cold.
            "ctx": canonical_context or "",
            # A researched episode is a different thing from an instant one.
            "search": bool(searched),
            "model": settings.model,
            "wpm": settings.target_wpm,
            "v": 2,  # bump to invalidate everything after a prompt change
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def key_bucket(
    minutes: int, canonical_context: str = "", searched: bool = False
) -> str:
    """Everything in `cache_key` *except* the question.

    A near match is only allowed to look at entries that are otherwise the same
    kind of episode. Duration is not a formatting detail - a 3-minute script is
    written differently from a 10-minute one, not cut down from it - and a
    follow-up, a researched episode and a different model all produce different
    words for identical text. Comparing across those would be comparing two
    things that were never interchangeable in the first place.

    The vector space is in here too, so switching embedding backend or width
    retires the old vectors instead of comparing coordinates that no longer
    mean the same thing.
    """
    payload = json.dumps(
        {
            "m": int(minutes),
            "ctx": canonical_context or "",
            "search": bool(searched),
            "model": settings.model,
            "wpm": settings.target_wpm,
            "space": embeddings.space(),
            "v": 2,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _token_set(query: str) -> set:
    """The words a match has to share, counted the same way the vector counts
    them.

    Through `embeddings.tokens` rather than `.split()` so that spelled-out
    numbers fold to digits here too. They did not at first, and the two halves
    of the decision then disagreed with each other: "week five" and "week 5"
    were one question to the vector and two to the overlap guard, so a perfect
    match was refused for having only half its words in common.
    """
    return set(embeddings.tokens(normalize_query(query)))


def comparable(asked: str, stored: str) -> str:
    """"" if these two questions may share an episode, else why they may not.

    The cosine on its own is not enough, and the failure it permits is the
    expensive one. A cache miss costs a cent and a few seconds; a *false* hit
    plays a confident answer to a question nobody asked, which breaks the first
    duty of an episode - satisfy the thing that brought them. So the score has
    to clear a bar, and then survive three checks that a vector is known to be
    bad at:

    * **Numbers must be identical.** "NFL week 5" and "NFL week 6" score 0.70
      here and are different episodes. Digits are the detail an embedding blurs
      and a listener notices immediately.
    * **The questions must overlap lexically.** A vector can be talked into a
      high score by shared shape; requiring real shared words means a wrong
      match has to be wrong in two independent ways at once.
    * **They must agree about needing today's facts.** Answering a durable
      question from an episode written to be current, or the reverse, is wrong
      even when the subject matches - and freshness is already decided
      elsewhere in this module, for free.

    Returns the reason rather than a bool for the same reason `research_reason`
    does: a heuristic nobody can see the workings of is a heuristic nobody can
    tune. The bench prints these.
    """
    if embeddings.numbers(asked) != embeddings.numbers(stored):
        return "different numbers"
    if needs_fresh_information(asked) != needs_fresh_information(stored):
        return "one needs today's facts and the other does not"
    a, b = _token_set(asked), _token_set(stored)
    if not a or not b:
        return "nothing left after normalising"
    overlap = len(a & b) / float(len(a | b))
    if overlap < settings.cache_vector_overlap:
        return f"only {overlap:.2f} of the words in common"
    return ""


def best_match(
    query: str,
    rows,
    threshold: Optional[float] = None,
) -> Optional[tuple[str, float]]:
    """The closest stored question that may stand in for `query`.

    `rows` is `(key, stored_query, vector_blob)`. Kept as a plain function on
    plain tuples so both cache backends and the bench share one implementation
    of the decision - the alternative is two copies that disagree about what a
    hit is, which is the same shape of bug as a setting documented in two
    places.
    """
    limit = settings.cache_vector_threshold if threshold is None else threshold
    wanted = embeddings.embed(normalize_query(query))
    best: Optional[tuple[str, float]] = None
    for key, stored_query, blob in rows:
        if not blob or not stored_query:
            continue
        score = embeddings.cosine(wanted, embeddings.unpack(blob))
        if score < limit or (best and score <= best[1]):
            continue
        if comparable(query, stored_query):
            continue
        best = (key, score)
    return best


@dataclass
class StoredAudio:
    """One episode's finished audio, exactly as it was streamed the first time.

    `sentences` and `starts` are the ones *in this audio* - what was spoken and
    where each began - kept with it rather than read off the script row, so a
    replay publishes captions that match what is heard even when the original
    was cut to fit its length.
    """

    pcm: bytes
    sample_rate: int
    sentences: list = field(default_factory=list)
    starts: list = field(default_factory=list)


#: zlib level 1: measured on the reference recording at 74% of the raw size in
#: 30 ms a second of audio. Level 6 bought under half a percent more for twice
#: the time, and lzma's 59% cost fifteen times as long.
AUDIO_COMPRESSION = 1


def pack_audio(pcm: bytes) -> bytes:
    return zlib.compress(pcm, AUDIO_COMPRESSION)


def unpack_audio(blob: bytes) -> bytes:
    return zlib.decompress(blob)


def audio_ceiling_bytes() -> int:
    """0 means no ceiling."""
    return max(0, int(settings.audio_cache_max_mb)) * 1024 * 1024


class ScriptCache(Protocol):
    def get(self, key: str) -> Optional[list[str]]: ...
    def put(
        self, key: str, sentences: list[str], ttl: int, query: str, thread: str = "",
        minutes: int = 0, bucket: str = "", sources: str = "", author: str = "",
        title: str = "", summary: str = "", slide: bool = False
    ) -> None: ...
    #: The go-deeper thread stored with the script, or "" if there was none.
    #: Kept beside the sentences rather than inside them so a replayed episode
    #: can never speak it by accident.
    def thread(self, key: str) -> str: ...
    #: The episode's own title, written by the model on the same kind of
    #: trailing line as `thread` and stored for the same reason: a replayed
    #: episode has no `notes`, so without this a shared or Explore episode
    #: would be titled with somebody else's typed question while a freshly
    #: generated one had a real name.
    def title(self, key: str) -> str: ...
    #: When the live script under this key was written, or None if there is
    #: none. myFAM reads it to tell the episode a listener heard from one
    #: written since - see `topics.is_repeat`. Optional on a backend, like
    #: `summary`: callers read it with `getattr`.
    def written_at(self, key: str) -> Optional[float]: ...
    #: One sentence saying what the episode is, off the model's `<<SUMMARY:>>`
    #: line. What a "Pick up where you left off" card draws under its title,
    #: for the same reason every other myFAM tile carries a hook (§127).
    #: Optional on a backend: callers read it with `getattr`, so a cache that
    #: predates it answers "" rather than raising.
    def summary(self, key: str) -> str: ...
    #: Who the cached episode's facts came from, as stored JSON. Kept beside
    #: the script for the same reason `thread` is: a cache hit replays
    #: sentences and has no `notes`, so without this a shared or Explore
    #: episode would show an empty sources panel while a freshly generated one
    #: showed a full list. See `provenance.py`.
    def sources(self, key: str) -> str: ...
    #: Live entries, newest first. Explore replays these and never generates.
    #: `exclude_author` drops entries this listener generated themselves -
    #: Explore is other people's episodes, and your own coming back at you
    #: reads as the app having nothing rather than as a feature.
    def recent(self, limit: int = 40, exclude_author: str = "") -> list[dict]: ...
    #: The closest *near* match in the same bucket, or None. Only consulted
    #: after an exact lookup has already missed.
    def nearest(self, bucket: str, query: str) -> Optional[tuple[str, float]]: ...


class MemoryScriptCache:
    """Process-local cache. Fine for tests and single-worker development."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[float, list[str], str, str, int]] = {}
        #: key -> the listener who first generated it. Beside the tuple for
        #: the same reason `_sources` is: the shape the tests assert on stays
        #: unchanged. "" for anything written with no listener behind it -
        #: prefetch, a tool, a test - which is shown to everybody.
        self._authors: dict[str, str] = {}
        #: key -> provenance JSON. Beside the tuple rather than in it, so the
        #: shape the existing tests assert on is unchanged.
        self._sources: dict[str, str] = {}
        #: key -> the episode's own title, for the same reason.
        self._titles: dict[str, str] = {}
        #: key -> its one-sentence summary, for the same reason again.
        self._summaries: dict[str, str] = {}
        #: key -> (bucket, packed vector). Kept beside the entries rather than
        #: in the tuple so the shape the tests already assert on is unchanged.
        self._vectors: dict[str, tuple[str, bytes]] = {}
        #: (key, voice) -> [compressed pcm, sample rate, sentences, starts,
        #: last played]. The memory half of `episode_audio`.
        self._audio: dict[tuple[str, str], list] = {}
        #: key -> how many times it was played. The memory half of the
        #: `plays` column (§134).
        self._plays: dict[str, int] = {}
        #: key -> when its current script was written. Beside the tuple for
        #: the same reason as everything above it.
        self._created: dict[str, float] = {}
        self.hits = 0
        self.misses = 0

    def record_play(self, key: str) -> None:
        if key:
            self._plays[key] = self._plays.get(key, 0) + 1

    def plays(self, key: str) -> int:
        entry = self._data.get(key)
        if not entry or entry[0] < time.time():
            return 0
        return self._plays.get(key, 0)

    def written_at(self, key: str) -> Optional[float]:
        entry = self._data.get(key)
        if not entry or entry[0] < time.time():
            return None
        return self._created.get(key)

    def get(self, key: str) -> Optional[list[str]]:
        entry = self._data.get(key)
        if not entry or entry[0] < time.time():
            self.misses += 1
            return None
        self.hits += 1
        return list(entry[1])

    def put(
        self, key: str, sentences: list[str], ttl: int, query: str = "",
        thread: str = "", minutes: int = 0, bucket: str = "", sources: str = "",
        author: str = "", title: str = "", summary: str = "", slide: bool = False
    ) -> None:
        before = self._data.get(key)
        self._data[key] = (time.time() + ttl, list(sentences), thread, query, int(minutes))
        self._created[key] = time.time()
        # New words, so any audio kept for the old ones no longer matches -
        # and only new words (§134): a re-write that said the same thing keeps
        # the audio it already paid for.
        if before is None or list(before[1]) != list(sentences):
            self._drop_audio(key)
        if sources:
            self._sources[key] = sources
        if title:
            self._titles[key] = title
        if summary:
            self._summaries[key] = summary
        # First writer only. A second listener asking the same question is
        # served from this entry and never rewrites it, so authorship stays
        # "who paid for this" rather than "who asked most recently".
        self._authors.setdefault(key, author or "")
        if bucket and query:
            self._vectors[key] = (bucket, embeddings.pack(embeddings.embed(normalize_query(query))))

    def nearest(self, bucket: str, query: str) -> Optional[tuple[str, float]]:
        if not settings.cache_vector or not bucket:
            return None
        now = time.time()
        rows = [
            (key, self._data[key][3], vec)
            for key, (b, vec) in self._vectors.items()
            if b == bucket and key in self._data and self._data[key][0] >= now
        ]
        return best_match(query, rows)

    def recent(self, limit: int = 40, exclude_author: str = "") -> list[dict]:
        live = [
            {"key": k, "query": v[3], "minutes": v[4], "created": v[0],
             "plays": self._plays.get(k, 0), "thread": v[2],
             "title": self._titles.get(k, ""),
             "author": self._authors.get(k, "")}
            for k, v in self._data.items()
            if v[0] >= time.time() and v[3] and v[4] > 0
            and not (exclude_author and self._authors.get(k) == exclude_author)
        ]
        live.sort(key=lambda e: -e["created"])
        return live[:limit]

    def thread(self, key: str) -> str:
        entry = self._data.get(key)
        if not entry or entry[0] < time.time():
            return ""
        return entry[2]

    def sources(self, key: str) -> str:
        entry = self._data.get(key)
        if not entry or entry[0] < time.time():
            return ""
        return self._sources.get(key, "")

    def title(self, key: str) -> str:
        entry = self._data.get(key)
        if not entry or entry[0] < time.time():
            return ""
        return self._titles.get(key, "")

    def summary(self, key: str) -> str:
        entry = self._data.get(key)
        if not entry or entry[0] < time.time():
            return ""
        return self._summaries.get(key, "")

    # -- audio (§132) -------------------------------------------------------

    def _live(self, key: str) -> bool:
        entry = self._data.get(key)
        return bool(entry) and entry[0] >= time.time()

    def _drop_audio(self, key: str) -> None:
        for pair in [p for p in self._audio if p[0] == key]:
            self._audio.pop(pair, None)

    def has_audio(self, key: str, voice: str, sample_rate: int) -> bool:
        row = self._audio.get((key, voice))
        return bool(row) and self._live(key) and row[1] == int(sample_rate)

    def get_audio(self, key: str, voice: str, sample_rate: int) -> Optional[StoredAudio]:
        if not self.has_audio(key, voice, sample_rate):
            return None
        row = self._audio[(key, voice)]
        row[4] = time.time()
        return StoredAudio(unpack_audio(row[0]), row[1], list(row[2]), list(row[3]))

    def put_audio(self, key: str, voice: str, sample_rate: int, pcm: bytes,
                  sentences: list, starts: list) -> bool:
        if not pcm or not self._live(key):
            return False
        self._audio[(key, voice)] = [pack_audio(pcm), int(sample_rate),
                                     list(sentences), list(starts), time.time()]
        ceiling = audio_ceiling_bytes()
        if ceiling:
            while sum(len(r[0]) for r in self._audio.values()) > ceiling \
                    and len(self._audio) > 1:
                oldest = min(self._audio, key=lambda p: self._audio[p][4])
                self._audio.pop(oldest)
        return (key, voice) in self._audio

    def forget_author(self, author: str) -> int:
        """The memory backend's half of `SqliteScriptCache.forget_author`.

        Both exist because a wipe that silently did nothing on one backend
        would be indistinguishable from one that worked - which is the class
        of failure this project keeps a rule about.
        """
        if not author:
            return 0
        keys = [k for k, who in self._authors.items() if who == author]
        for key in keys:
            self._data.pop(key, None)
            self._authors.pop(key, None)
            self._sources.pop(key, None)
            self._titles.pop(key, None)
            self._summaries.pop(key, None)
            self._vectors.pop(key, None)
            self._drop_audio(key)
        return len(keys)

    def clear(self) -> int:
        removed = len(self._data)
        for table in (self._data, self._authors, self._sources, self._titles,
                      self._summaries, self._vectors, self._audio):
            table.clear()
        return removed

    def stats(self) -> dict:
        return {"backend": "memory", "entries": len(self._data), "hits": self.hits,
                "misses": self.misses, "audio_entries": len(self._audio),
                "audio_bytes": sum(len(r[0]) for r in self._audio.values())}


class SqliteScriptCache:
    """Cross-process cache with no new dependencies.

    Every uvicorn worker on the box shares one file, so a hit generated by one
    listener is immediately available to the next - which is the entire point of
    sharing output between different users.
    """

    def __init__(self, path: str | None = None) -> None:
        # Through data_path so an explicit path gets the same directory
        # guarantee as the configured one.
        self.path = data_path("CACHE_PATH", "scripts.db", path or settings.cache_path)
        self._local = threading.local()
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS scripts (
                       key       TEXT PRIMARY KEY,
                       expires   REAL NOT NULL,
                       created   REAL NOT NULL,
                       hits      INTEGER NOT NULL DEFAULT 0,
                       query     TEXT,
                       sentences TEXT NOT NULL
                   )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS scripts_expires ON scripts(expires)")
            # Added after the table shipped, so existing caches need widening
            # rather than recreating - a cache file is not worth a migration
            # framework, and losing it would only cost one regeneration.
            for column, ddl in (
                ("thread", "ALTER TABLE scripts ADD COLUMN thread TEXT NOT NULL DEFAULT ''"),
                # Explore replays cached scripts, and a script cannot be
                # replayed without knowing how long it was written to run.
                ("minutes", "ALTER TABLE scripts ADD COLUMN minutes INTEGER NOT NULL DEFAULT 0"),
                # The near-match pair. `bucket` is everything about the episode
                # except the question, so a scan only ever compares entries
                # that were interchangeable to begin with; `vector` is the
                # question itself, embedded once at write time. Rows written
                # before this migration simply have no vector and are invisible
                # to near matching - they still serve exact hits.
                ("bucket", "ALTER TABLE scripts ADD COLUMN bucket TEXT NOT NULL DEFAULT ''"),
                ("vector", "ALTER TABLE scripts ADD COLUMN vector BLOB"),
                # Provenance, beside the script for the same reason `thread`
                # is: a replayed episode has no `notes` to rebuild it from.
                ("sources", "ALTER TABLE scripts ADD COLUMN sources TEXT NOT NULL DEFAULT ''"),
                # Who generated it first. Explore is *other people's*
                # episodes, and without this the shared cache cannot tell
                # whose is whose - so a listener's own questions came back to
                # them in a feed whose whole premise is that they did not.
                #
                # It is provenance, never identity: nothing about the key or
                # the bucket reads it, so two listeners asking the same
                # question still share one script and one cost. Rows written
                # before this migration have no author and are shown to
                # everybody, which is what they were already doing.
                ("author", "ALTER TABLE scripts ADD COLUMN author TEXT NOT NULL DEFAULT ''"),
                # The episode's own title, off the model's trailing marker
                # line. Beside the script for the same reason `thread` is: a
                # replayed episode has no `notes`, so without this a shared or
                # Explore episode would be titled with whatever the first
                # listener happened to type while a freshly generated one had
                # a real name. Rows written before this migration have none
                # and fall back to the question, which is what every row did
                # before it existed.
                ("title", "ALTER TABLE scripts ADD COLUMN title TEXT NOT NULL DEFAULT ''"),
                # One sentence on what the episode is, off `<<SUMMARY:>>`, for
                # the resume card's second line (§127). Same reasoning as
                # `title`, and the same fallback: a row without one draws no
                # line rather than an invented one.
                ("summary", "ALTER TABLE scripts ADD COLUMN summary TEXT NOT NULL DEFAULT ''"),
                # The lifetime the entry was written with (§134), so a play
                # can tell an evergreen entry - which may slide - from a
                # volatile one, which must not. Rows written before this have
                # 0 and never slide, which is what they did before.
                ("ttl", "ALTER TABLE scripts ADD COLUMN ttl INTEGER NOT NULL DEFAULT 0"),
                # How many times the episode was actually *played* (§134) -
                # the number on an Explore card. `hits` is not that: it counts
                # every `get`, and a normal play reads the cache two or three
                # times (the pacing probe, the pipeline's own lookup, a near
                # match), as does a prefetch checking whether to bother.
                ("plays", "ALTER TABLE scripts ADD COLUMN plays INTEGER NOT NULL DEFAULT 0"),
            ):
                try:
                    conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass  # already there
            # After the ALTERs, so a cache file created before this release
            # gets the column before anything tries to index it.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS scripts_bucket ON scripts(bucket, expires)"
            )
            # §132: the finished audio, one row per (script, voice). No
            # `expires` of its own - it is readable exactly while its script
            # row is, so the two can never disagree about whether an episode
            # still exists.
            conn.execute(
                """CREATE TABLE IF NOT EXISTS episode_audio (
                       key         TEXT NOT NULL,
                       voice       TEXT NOT NULL,
                       sample_rate INTEGER NOT NULL,
                       created     REAL NOT NULL,
                       played      REAL NOT NULL,
                       bytes       INTEGER NOT NULL,
                       sentences   TEXT NOT NULL,
                       starts      TEXT NOT NULL,
                       pcm         BLOB NOT NULL,
                       PRIMARY KEY (key, voice)
                   )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS episode_audio_played ON episode_audio(played)"
            )

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
            # WAL lets readers proceed while another worker is writing, which
            # matters when several episodes are being generated at once.
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    def get(self, key: str) -> Optional[list[str]]:
        try:
            conn = self._conn()
            row = conn.execute(
                "SELECT sentences, expires FROM scripts WHERE key = ?", (key,)
            ).fetchone()
            now = time.time()
            if not row or row[1] < now:
                return None
            conn.execute("UPDATE scripts SET hits = hits + 1 WHERE key = ?", (key,))
            return json.loads(row[0])
        except Exception:
            # A cache is an optimisation. If it breaks, the episode is still
            # generated the slow way rather than failing.
            log.exception("script cache read failed; regenerating")
            return None

    def written_at(self, key: str) -> Optional[float]:
        """When the live script under `key` was written, or None.

        Not `get`: that counts a hit, and asking about a tile on a browse page
        is nobody listening. Raises rather than answering None on a broken
        database, because None means "no script" and myFAM reads that as "a
        tap would write a new one" - a failure must not look like freshness.
        """
        row = self._conn().execute(
            "SELECT created, expires FROM scripts WHERE key = ?", (key,)
        ).fetchone()
        if not row or row[1] < time.time():
            return None
        return float(row[0])

    @staticmethod
    def _slide(conn, key: str, expires: float, ttl, created, now: float) -> None:
        """Keep an evergreen entry alive while people keep playing it (§134).

        Called from `record_play` and nowhere else: a pacing probe, a prefetch
        existence check or a GPU-wake hint reads the cache too, and none of
        those is anybody listening. `ttl` is 0 for an entry the pipeline did
        not mark as free to slide (see its `slide` flag), and only an entry
        written at the ordinary ceiling slides - `ttl_for` gave
        anything time-sensitive a shorter one, and a claim about now must not
        outlive the window it was true in. It moves the expiry to a full
        lifetime from *this* read and never past `CACHE_MAX_AGE_SECONDS` from
        the write, so an explainer nobody re-reads for a month still goes.

        The point is the audio: it is readable only while its script is, so a
        fixed day from the first write threw away the stored audio of exactly
        the episodes being played most, and the next play re-voiced them on
        RunPod.
        """
        limit = int(settings.cache_max_age_seconds or 0)
        base = int(settings.cache_ttl_seconds or 0)
        if limit <= 0 or not ttl or int(ttl) < base or base <= 0:
            return
        target = min(now + int(ttl), float(created or now) + limit)
        if target > expires + 60:     # not a write per read for nothing
            conn.execute("UPDATE scripts SET expires = ? WHERE key = ?",
                         (target, key))

    def record_play(self, key: str) -> None:
        """One listener started this episode. The Explore card's number, and
        the one event that keeps an evergreen entry alive (`_slide`)."""
        if not key:
            return
        try:
            conn = self._conn()
            conn.execute(
                "UPDATE scripts SET plays = plays + 1 WHERE key = ?", (key,))
            row = conn.execute(
                "SELECT expires, ttl, created FROM scripts WHERE key = ?",
                (key,)).fetchone()
            now = time.time()
            if row and row[0] >= now:
                self._slide(conn, key, row[0], row[1], row[2], now)
        except Exception:
            log.exception("could not count a play; continuing")

    def plays(self, key: str) -> int:
        try:
            row = self._conn().execute(
                "SELECT plays FROM scripts WHERE key = ? AND expires >= ?",
                (key, time.time())).fetchone()
        except Exception:
            log.exception("could not read a play count")
            return 0
        return int(row[0]) if row else 0

    def put(
        self, key: str, sentences: list[str], ttl: int, query: str = "",
        thread: str = "", minutes: int = 0, bucket: str = "", sources: str = "",
        author: str = "", title: str = "", summary: str = "", slide: bool = False
    ) -> None:
        """Store the script, and the vector for the question that produced it.

        `slide` says a play may keep it alive past `ttl` (§134, `_slide`). Off
        unless the caller knows the episode makes no claim about a window of
        time - prefetch, tools and tests never say so.

        The embedding happens **here**, on the write, and that is the whole
        design. Doing it on the read would put work in front of the first word
        - the one cost this product refuses - and would have to be repeated for
        every lookup. Doing it once, after the episode has already been
        generated and the listener is already hearing it, costs nothing anyone
        can perceive.
        """
        if not sentences:
            return
        try:
            now = time.time()
            vector = None
            if bucket and query:
                vector = embeddings.pack(embeddings.embed(normalize_query(query)))
            # What the key held before, so kept audio survives a re-write
            # that said the same thing (below).
            before = self._conn().execute(
                "SELECT sentences FROM scripts WHERE key = ?", (key,)).fetchone()
            # `COALESCE` on the existing author rather than the new one:
            # a re-write of a live entry (a longer TTL, fresher sources) must
            # not hand authorship to whoever happened to trigger it. Only a
            # row that has none takes one.
            self._conn().execute(
                "INSERT INTO scripts"
                " (key, expires, created, hits, query, sentences, thread, minutes,"
                "  bucket, vector, sources, author, title, summary, ttl)"
                " VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET"
                "  expires = excluded.expires, created = excluded.created,"
                "  ttl = excluded.ttl,"
                "  query = excluded.query, sentences = excluded.sentences,"
                "  thread = excluded.thread, minutes = excluded.minutes,"
                "  bucket = excluded.bucket, vector = excluded.vector,"
                "  sources = excluded.sources,"
                "  author = CASE WHEN scripts.author != '' THEN scripts.author"
                "                ELSE excluded.author END,"
                # A re-write keeps the title it has unless it is bringing a
                # new one. A longer TTL or fresher sources must not blank the
                # name of an episode that is already in somebody's feed.
                "  title = CASE WHEN excluded.title != '' THEN excluded.title"
                "               ELSE scripts.title END,"
                "  summary = CASE WHEN excluded.summary != '' THEN excluded.summary"
                "                 ELSE scripts.summary END",
                (key, now + ttl, now, query[:500], json.dumps(sentences),
                 thread[:200], int(minutes), bucket, vector, sources or "",
                 (author or "")[:64], (title or "")[:120], (summary or "")[:240],
                 int(ttl) if slide else 0),
            )
            # New words under this key, so audio kept for the old ones would
            # replay an episode that no longer matches its own captions.
            #
            # **Only if the words are actually new** (§134). Two listeners
            # generating one key at once both write it, with a script that
            # may well be word for word the same, and a re-write that changed
            # nothing was throwing away a whole voiced episode - one more
            # RunPod call for a thing already paid for.
            if before is None or before[0] != json.dumps(sentences):
                self._conn().execute("DELETE FROM episode_audio WHERE key = ?", (key,))
        except Exception:
            log.exception("script cache write failed; continuing")

    # -- audio (§132) -------------------------------------------------------

    def has_audio(self, key: str, voice: str, sample_rate: int) -> bool:
        try:
            row = self._conn().execute(
                "SELECT 1 FROM episode_audio a JOIN scripts s ON s.key = a.key"
                " WHERE a.key = ? AND a.voice = ? AND a.sample_rate = ?"
                " AND s.expires >= ?",
                (key, voice, int(sample_rate), time.time()),
            ).fetchone()
            return row is not None
        except Exception:
            log.exception("audio cache check failed")
            return False

    def get_audio(self, key: str, voice: str, sample_rate: int) -> Optional[StoredAudio]:
        """The kept audio for this episode in this voice, or None.

        None whenever the rate differs from the engine's: the stream header has
        already been written at the engine's rate, and PCM at another one would
        play at the wrong pitch rather than fail.
        """
        try:
            conn = self._conn()
            row = conn.execute(
                "SELECT a.pcm, a.sample_rate, a.sentences, a.starts"
                " FROM episode_audio a JOIN scripts s ON s.key = a.key"
                " WHERE a.key = ? AND a.voice = ? AND s.expires >= ?",
                (key, voice, time.time()),
            ).fetchone()
            if not row or int(row[1]) != int(sample_rate):
                return None
            conn.execute(
                "UPDATE episode_audio SET played = ? WHERE key = ? AND voice = ?",
                (time.time(), key, voice))
            return StoredAudio(unpack_audio(row[0]), int(row[1]),
                               json.loads(row[2]), json.loads(row[3]))
        except Exception:
            # Same rule as the script: a broken store means the episode is
            # synthesised the old way, never that it fails.
            log.exception("audio cache read failed; synthesising")
            return None

    def put_audio(self, key: str, voice: str, sample_rate: int, pcm: bytes,
                  sentences: list, starts: list) -> bool:
        """Keep an episode's audio. Only beside a live script row, never alone.

        Returns whether it was kept. Compression happens here, so call it off
        the event loop - `PodcastPipeline` does, after the last byte is sent.
        """
        if not pcm:
            return False
        try:
            blob = pack_audio(pcm)
            now = time.time()
            cur = self._conn().execute(
                "INSERT OR REPLACE INTO episode_audio"
                " (key, voice, sample_rate, created, played, bytes, sentences,"
                "  starts, pcm)"
                " SELECT ?, ?, ?, ?, ?, ?, ?, ?, ? FROM scripts"
                " WHERE key = ? AND expires >= ?",
                (key, voice, int(sample_rate), now, now, len(blob),
                 json.dumps(list(sentences)), json.dumps(list(starts)),
                 sqlite3.Binary(blob), key, now),
            )
            if not cur.rowcount:
                return False
            self._evict_audio()
            # An episode larger than the whole ceiling is evicted by its own
            # write; saying it was kept would be a claim nothing can serve.
            return self.has_audio(key, voice, sample_rate)
        except Exception:
            log.exception("audio cache write failed; continuing")
            return False

    def _evict_audio(self) -> None:
        """Least recently played first, until the table fits its ceiling.

        Only audio goes: the script stays, so an evicted episode costs one
        re-synthesis on its next play and is then kept again.
        """
        ceiling = audio_ceiling_bytes()
        if not ceiling:
            return
        conn = self._conn()
        total = conn.execute(
            "SELECT COALESCE(SUM(bytes), 0) FROM episode_audio").fetchone()[0]
        if total <= ceiling:
            return
        for key, voice, size in conn.execute(
                "SELECT key, voice, bytes FROM episode_audio ORDER BY played ASC"
        ).fetchall():
            if total <= ceiling:
                break
            conn.execute("DELETE FROM episode_audio WHERE key = ? AND voice = ?",
                         (key, voice))
            total -= size
        log.info("audio cache: evicted down to %.1f MB", total / 1048576)

    def nearest(self, bucket: str, query: str) -> Optional[tuple[str, float]]:
        """The closest live entry in this bucket, or None.

        Brute force, deliberately. `sqlite-vec` was the obvious thing to reach
        for and is premature: measured here, an exact scan of 1,000 vectors
        takes 0.06 ms and 100,000 takes 2.4 ms, against a live cache bounded by
        a 24-hour TTL. An extension adds a loadable binary to every deployment
        to save a number nobody can perceive. Revisit when the scan is the
        slowest thing on the miss path, which it is nowhere near being.

        `CACHE_VECTOR_SCAN` caps the rows considered, newest first, so the cost
        stays bounded however large the cache grows.
        """
        if not settings.cache_vector or not bucket:
            return None
        try:
            rows = self._conn().execute(
                "SELECT key, query, vector FROM scripts"
                " WHERE bucket = ? AND expires >= ? AND vector IS NOT NULL"
                " ORDER BY created DESC LIMIT ?",
                (bucket, time.time(), int(settings.cache_vector_scan)),
            ).fetchall()
        except Exception:
            log.exception("near-match scan failed; treating as a miss")
            return None
        return best_match(query, rows)

    def sources(self, key: str) -> str:
        """Provenance JSON for a cached episode, or "" if there is none.

        Same shape as `thread` and for the same reason - a replayed episode
        has no `notes` to rebuild it from, so without this a cache hit would
        show an empty sources panel where a fresh generation showed a full one.
        """
        try:
            row = self._conn().execute(
                "SELECT sources, expires FROM scripts WHERE key = ?", (key,)
            ).fetchone()
            if not row or row[1] < time.time():
                return ""
            return row[0] or ""
        except Exception:
            log.exception("script cache sources read failed; continuing")
            return ""

    def thread(self, key: str) -> str:
        try:
            row = self._conn().execute(
                "SELECT thread, expires FROM scripts WHERE key = ?", (key,)
            ).fetchone()
            if not row or row[1] < time.time():
                return ""
            return row[0] or ""
        except Exception:
            log.exception("script cache thread read failed")
            return ""

    def title(self, key: str) -> str:
        """The episode's own name, or "" - in which case the caller falls back
        to the question, which is what everything did before this existed."""
        try:
            row = self._conn().execute(
                "SELECT title, expires FROM scripts WHERE key = ?", (key,)
            ).fetchone()
            if not row or row[1] < time.time():
                return ""
            return row[0] or ""
        except Exception:
            log.exception("script cache title read failed")
            return ""

    def summary(self, key: str) -> str:
        """The episode's one-sentence summary, or "" when it has none."""
        try:
            row = self._conn().execute(
                "SELECT summary, expires FROM scripts WHERE key = ?", (key,)
            ).fetchone()
            if not row or row[1] < time.time():
                return ""
            return row[0] or ""
        except Exception:
            log.exception("script cache summary read failed")
            return ""

    def recent(self, limit: int = 40, exclude_author: str = "") -> list[dict]:
        """Live cache entries, newest first - the raw material for Explore.

        Only *shareable* queries are ever written here (see `is_shareable`),
        so everything in this table is already safe to show another listener.
        That property is what makes an Explore feed possible at all, and it is
        a reason to be careful about ever loosening the personal-query filter.

        Entries with no recorded duration are skipped rather than guessed at: a
        script written for one minute replayed as a five-minute episode would
        be padded with silence.

        `exclude_author` is the Explore rule: the feed is what *other people*
        have already generated, so a listener's own episodes are dropped from
        their own. It is a filter on display and never on storage - the entry
        stays in the shared cache, still serves them an instant replay, and
        still appears on everybody else's feed.
        """
        try:
            rows = self._conn().execute(
                "SELECT key, query, minutes, created, plays, thread, title,"
                " author FROM scripts"
                " WHERE expires >= ? AND query != '' AND minutes > 0"
                "   AND (? = '' OR author != ?)"
                " ORDER BY created DESC LIMIT ?",
                (time.time(), exclude_author or "", exclude_author or "",
                 int(limit)),
            ).fetchall()
        except Exception:
            log.exception("could not read recent scripts")
            return []
        return [
            # `author` is here for exactly one display decision - whether a
            # *friend* generated this - and must not leave the server as an
            # id. `/api/explore` resolves it to a name and a picture and emits
            # neither. Authorship is provenance, never identity: nothing about
            # the key or the bucket reads it, and a listener id one field away
            # from the key is one refactor away from being in it.
            {"key": r[0], "query": r[1], "minutes": r[2], "created": r[3],
             "plays": r[4], "thread": r[5] or "", "title": r[6] or "",
             "author": r[7] or ""}
            for r in rows
        ]

    def purge_expired(self) -> int:
        try:
            now = time.time()
            cur = self._conn().execute("DELETE FROM scripts WHERE expires < ?", (now,))
            # Audio has no clock of its own; it goes when its script does.
            self._conn().execute(
                "DELETE FROM episode_audio WHERE key NOT IN"
                " (SELECT key FROM scripts WHERE expires >= ?)", (now,))
            return cur.rowcount or 0
        except Exception:
            return 0

    def forget_author(self, author: str) -> int:
        """Drop every script one listener wrote first. For clearing seed data.

        **Not part of account deletion, and it must not become part of it.**
        The shared cache holds no identity - `author` is provenance, so that
        Explore can leave a listener's own episodes off their own feed - and a
        listener leaving does not un-write the episodes other people are
        listening to. `erase_listener` says so and is right.

        What this is for is the other thing: a deployment seeded with demo
        episodes so the browse surfaces had something to show, now being
        cleared so the recommendations can be judged on real listening. Those
        episodes were written *by* the seed and are exactly identified by who
        wrote them. Anything written before the column existed has no author
        and is not touched by this, which is correct - a script nobody can
        attribute is not demonstrably seed data.
        """
        if not author:
            return 0
        try:
            self._conn().execute(
                "DELETE FROM episode_audio WHERE key IN"
                " (SELECT key FROM scripts WHERE author = ?)", (author,))
            cur = self._conn().execute(
                "DELETE FROM scripts WHERE author = ?", (author,))
            return cur.rowcount or 0
        except Exception:
            log.exception("could not drop scripts authored by %r", author)
            return 0

    def clear(self) -> int:
        """Empty the whole cache. Every entry, expired or not.

        The one lever that makes "start seeing only new episode titles" true
        on a deployment nobody can shell into. It costs one regeneration per
        question anybody asks again, and nothing else: the audio beside each
        script (§132) goes with it, and every entry is reproducible.
        """
        try:
            self._conn().execute("DELETE FROM episode_audio")
            cur = self._conn().execute("DELETE FROM scripts")
            return cur.rowcount or 0
        except Exception:
            log.exception("could not clear the script cache")
            return 0

    def stats(self) -> dict:
        try:
            row = self._conn().execute(
                "SELECT COUNT(*), COALESCE(SUM(hits), 0) FROM scripts WHERE expires >= ?",
                (time.time(),),
            ).fetchone()
            audio = self._conn().execute(
                "SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM episode_audio"
            ).fetchone()
            return {"backend": "sqlite", "path": self.path, "entries": row[0],
                    "hits_served": row[1], "audio_entries": audio[0],
                    "audio_bytes": audio[1]}
        except Exception:
            return {"backend": "sqlite", "path": self.path, "error": "unavailable"}


CANONICAL_PROMPT = """Reduce this listener request to a canonical topic label so \
that differently-worded requests for the SAME briefing collapse together.

Rules:
- Lowercase. Three to eight words. No punctuation.
- Keep every distinguishing detail: subject, number, season, year, place.
- Drop phrasing, politeness and format words ("give me", "a recap of", "podcast").
- Two requests that deserve the SAME briefing must produce the identical label.
  Two requests that deserve DIFFERENT briefings must not.

Examples:
  "Give me a recap of week 5 of the NFL season" -> nfl season week 5 recap
  "NFL week 5 recap" -> nfl season week 5 recap
  "what happened in week five of the nfl" -> nfl season week 5 recap
  "Why is the sky blue?" -> why the sky is blue

Output only the label."""


async def canonical_key(query: str, client) -> str:
    """Collapse equivalent phrasings that lexical normalisation cannot.

    `normalize_query` handles punctuation, word order and filler, but it is
    lexical: "week 5 of the NFL season" and "NFL week 5" differ by one real
    word ("season") and so miss each other. A small model closes that gap.

    The tradeoff is honest: this adds one fast call (~300-500 ms, ~$0.0002) to
    the front of every request, which is pure overhead on a miss and a large win
    on a hit. Worth enabling when traffic concentrates on popular topics;
    leave it off for a long tail of unique queries. Off by default.
    """
    try:
        message = await client.messages.create(
            model=settings.canonical_key_model,
            max_tokens=40,
            system=CANONICAL_PROMPT,
            messages=[{"role": "user", "content": query}],
        )
        if message.stop_reason == "refusal":
            return normalize_query(query)
        text = " ".join(b.text for b in message.content if b.type == "text")
        label = _SPACE.sub(" ", _PUNCT.sub(" ", text.lower())).strip()
        # Never let a chatty answer become the key.
        return label if 0 < len(label) <= 80 else normalize_query(query)
    except Exception:
        log.warning("canonical key lookup failed; using lexical key", exc_info=True)
        return normalize_query(query)


def build_cache() -> Optional[ScriptCache]:
    if not settings.cache_enabled:
        return None
    if settings.cache_backend == "memory":
        return MemoryScriptCache()
    return SqliteScriptCache()
