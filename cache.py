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
import secrets
import time
import zlib
from dataclasses import dataclass, field
from datetime import datetime
import sqlite3
import threading
import time
from typing import Optional, Protocol

import audio_codec
import audio_store
import content_filter
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




#: The brief intents that ask for a score or a game update - what is never
#: current in sports until it is final (`ttl_for`, 10.1 #3).
SPORTS_UPDATE_INTENTS = ("recap", "update")


#: How long an episode built on a forecast stays current (§194).
WEATHER_TTL_SECONDS = 3600


def ttl_for(query: str, *, live_status: str = "", outcome_dependent: bool = False,
            recency_days: int = 0, live_domain: str = "",
            intent: str = "") -> int:
    """How long a script stays *current*, in seconds. **Zero means never.**

    **Current, not kept** (§143, at the owner's direction). Every episode is
    kept a week (`CACHE_LIFE_SECONDS`) and stamped with when its information
    was sourced; this is only the window in which a *new request* may be
    served it as the answer. The replay surface - Explore, which sends
    `cached_only` - plays anything kept, with the sourced time on it; a
    search, a tile or a shared link is served a current script or writes a
    new one, as it always was once a script expired. So 0 no
    longer means "do not write it": it means a game in progress is written,
    kept, replayable as what it was at the time it was sourced, and never
    handed to somebody asking about that game now.

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
       evidence rather than inferred. `in_progress` is never current - no
       window is short enough for a score, and a ten-second one still serves
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

    **Sports scores and game updates are never current** (10.1 #3, at the
    owner's direction). In the sports domain, a score or a game update - EI's
    `recap` or `update` intent - that evidence has not settled as `final` is
    kept and replayable but never served to a new request, so nobody is
    handed a score another listener heard two hours ago, and the search box's
    trending searches never offer one. A `final` keeps its ordinary window:
    that result does not move. **Not `outcome_dependent`**: EI's gate sets it
    for every live-domain brief, so keying on it made every sports episode
    never current - previews included, which re-opened §173's "upcoming
    dodgers game" paying twice. A preview keeps its two hours. The domain and
    the intent are EI's reading of the request, never the words.
    """
    tokens = set(_SPACE.split(_PUNCT.sub(" ", query.lower())))
    keyword_ttl = (settings.cache_ttl_volatile if tokens & _VOLATILE
                   else settings.cache_ttl_seconds)

    status = (live_status or "").strip().lower()
    if status == "in_progress":
        return 0
    # **Weather is current for an hour** (§194): a forecast is reissued
    # through the day and an observation is "now" for less than that. Decided
    # by what the episode was built on - a weather block reached the writer -
    # never by the word "weather" in the question.
    if (live_domain or "").strip().lower() == "weather":
        return min(keyword_ttl, WEATHER_TTL_SECONDS)
    if ((live_domain or "").strip().lower() == "sports"
            and (intent or "").strip().lower() in SPORTS_UPDATE_INTENTS
            and status != "final"):
        return 0
    if status == "scheduled":
        # A preview is honest until the thing kicks off, and a game that has
        # started reads `in_progress` above. Its own thirty minutes until
        # §173; now the volatile window, so one afternoon's "upcoming game"
        # is one episode rather than one per half hour.
        return min(keyword_ttl, settings.cache_ttl_volatile)
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


#: Words a myFAM search ignores when it counts what two texts share: alone,
#: "the" or "what" would make every episode a match for every question.
SEARCH_STOPWORDS = frozenset("""
a an and are as at be been but by can did do does for from had has have how
i in into is it its me my of on or our so than that the their them then
there these they this to was we were what when where which who why will
with would you your about
""".split())
#: The closest a search result may be with no word in common - only an
#: embedding that understands meaning gets there; the lexical fallback's
#: cosine is word overlap by another name.
SEARCH_MIN_COSINE = 0.5
_search_vectors: dict[str, list[float]] = {}


def _search_words(text: str) -> set:
    return {t for t in embeddings.tokens(text or "") if t not in SEARCH_STOPWORDS}


def rank_similar(asked: str, entries: list[dict], limit: int = 12) -> list[dict]:
    """Cached episodes most like what was typed, best first (§181).

    The search behind myFAM's search button. `entries` are `recent()` rows;
    each is compared on its question and its title together. Two signals,
    and an entry needs one of them to be a result at all: the share of the
    typed words it contains, or an embedding cosine of at least
    `SEARCH_MIN_COSINE`. Ordered by words found, then the cosine, then plays.
    Never a model call; an entry's vector is computed once and remembered.
    """
    wanted = _search_words(asked)
    if not wanted:
        return []
    probe = embeddings.embed(normalize_query(asked))
    scored = []
    for entry in entries:
        text = f"{entry.get('query', '')} {entry.get('title', '')}"
        found = len(wanted & _search_words(text)) / float(len(wanted))
        # Keyed on the words as well as the key: a re-written episode keeps
        # its key and may change its title.
        key = (entry.get("key") or "") + "\n" + text
        vector = _search_vectors.get(key)
        if vector is None:
            if len(_search_vectors) > 5000:
                _search_vectors.clear()
            vector = _search_vectors[key] = embeddings.embed(normalize_query(text))
        score = embeddings.cosine(probe, vector)
        if found <= 0 and score < SEARCH_MIN_COSINE:
            continue
        scored.append((found, score, entry.get("plays", 0), entry))
    scored.sort(key=lambda row: (-row[0], -row[1], -row[2]))
    return [row[3] for row in scored[:limit]]


@dataclass
class StoredAudio:
    """One episode's finished audio, exactly as it was streamed the first time.

    `sentences` and `starts` are the ones *in this audio* - what was spoken and
    where each began - kept with it rather than read off the script row, so a
    replay publishes captions that match what is heard even when the original
    was cut to fit its length.

    Held packed (§237): `blob` is what `codec` made of the PCM, and `slices()`
    decodes it a second at a time as it plays, so a five-minute Opus episode
    does not wait to be decoded whole before its first word. `pcm` decodes
    all of it, for exports and tests.
    """

    blob: bytes
    sample_rate: int
    sentences: list = field(default_factory=list)
    starts: list = field(default_factory=list)
    codec: str = audio_codec.ZLIB
    #: Samples in the original PCM, so a codec's padding never moves a caption.
    frames: int = 0

    def slices(self):
        return audio_codec.slices(self.codec, self.blob, self.sample_rate, self.frames)

    @property
    def pcm(self) -> bytes:
        return audio_codec.decode(self.codec, self.blob, self.sample_rate, self.frames)


#: zlib level 1, kept for rows packed before §237 (`audio_codec.ZLIB_LEVEL`).
AUDIO_COMPRESSION = audio_codec.ZLIB_LEVEL


def _explicit_json(sentences_json) -> bool:
    """Whether a stored script swears (§171). False for anything unreadable:
    an E is a claim about the episode, and nothing is claimed off a guess."""
    try:
        return content_filter.is_explicit(json.loads(sentences_json or "[]"))
    except (TypeError, ValueError):
        return False


def _voiced_a_slur(sentences_json) -> bool:
    """Whether kept audio was voiced from a script with a slur in it - one
    written before the filter (§171). Unreadable is not a slur."""
    try:
        return content_filter.has_slur(json.loads(sentences_json or "[]"))
    except (TypeError, ValueError):
        return False


def pack_audio(pcm: bytes) -> bytes:
    return zlib.compress(pcm, AUDIO_COMPRESSION)


def unpack_audio(blob: bytes) -> bytes:
    return zlib.decompress(blob)


def audio_ceiling_bytes() -> int:
    """0 means no ceiling."""
    return max(0, int(settings.audio_cache_max_mb)) * 1024 * 1024


#: `episode_audio.tier` for audio somebody saved, shared or vibed (§237),
#: kept past its first week. Anything else is "" - recent audio.
KEPT_TIER = "kept"


def recent_seconds() -> float:
    """How long every episode's audio is kept (§237): AUDIO_RECENT_DAYS."""
    return max(1, int(settings.audio_recent_days)) * 86400.0


def kept_pin_seconds() -> float:
    """How far ahead each sweep pins the script of kept audio. A week and a
    day, so a deployment that stops sweeping for a while loses nothing - the
    audio is only readable while its script is (§132)."""
    return recent_seconds() + 86400.0


class ScriptCache(Protocol):
    def get(self, key: str, current: bool = True) -> Optional[list[str]]: ...
    def sourced_at(self, key: str) -> Optional[float]: ...
    def put(
        self, key: str, sentences: list[str], ttl: int, query: str, thread: str = "",
        minutes: int = 0, bucket: str = "", sources: str = "", author: str = "",
        title: str = "", summary: str = "", slide: bool = False,
        sourced_at: Optional[float] = None, category: str = ""
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
    #: What kind of thing the episode is about, as the writer worded it on
    #: its `<<CATEGORY:>>` line (§189) - raw words, resolved against the
    #: category tree where it is read. Optional on a backend, like `summary`.
    def category(self, key: str) -> str: ...
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
    def recent(self, limit: int = 40, exclude_author: str = "",
               origin: str = "") -> list[dict]: ...
    #: The closest *near* match in the same bucket, or None. Only consulted
    #: after an exact lookup has already missed.
    def nearest(self, bucket: str, query: str) -> Optional[tuple[str, float]]: ...
    #: §173: the key holding one heard episode (`episode_id`), current or not.
    def resolve_episode(self, episode: str) -> str: ...
    #: §173: keep a heard episode at least this long; never makes it current.
    def keep_until(self, key: str, until: float) -> bool: ...


#: SQL for "until when a new request may be served this row" (§143). Rows
#: written before `fresh_until` existed have 0 there, and were current for
#: exactly as long as they were kept - which is what this reads for them.
_CURRENT_UNTIL = "(CASE WHEN fresh_until > 0 THEN fresh_until ELSE expires END)"
#: SQL for "when this row's information was sourced". Pre-§143 rows recorded
#: only when they were written, which is the closest thing they have.
_SOURCED = "(CASE WHEN sourced_at > 0 THEN sourced_at ELSE created END)"


#: The `origin` of a row that is an earlier episode under a re-written key
#: (§173). Never current, never near-matched, never on Explore or a rail - it
#: exists so the listener who heard it can hear it again from their history.
ARCHIVE_ORIGIN = "archive"

_EPISODE_ID = re.compile(r"^([0-9a-f]{64})(?:\.(\d{1,12}))?$")


def archive_key(key: str, sourced: float) -> str:
    """Where the episode `key` held when it was sourced at `sourced` goes when
    the key is written again (§173). Derived, so nothing has to be looked up
    to find it: a history row naming (key, sourced) can always compute it."""
    return hashlib.sha256(
        f"archive|{key}|{int(sourced or 0)}".encode("utf-8")).hexdigest()


def episode_id(key: str, sourced: Optional[float]) -> str:
    """One heard episode's identity, for the listening history (§173).

    The cache key alone is not one: a key is a *question*, and a question
    asked again once its answer stopped being current is written again under
    the same key. The key plus when that episode's information was sourced
    is. "" for an episode with no key (an attachment is never cached)."""
    if not key:
        return ""
    return f"{key}.{int(sourced or 0)}"


def parse_episode_id(value: str) -> Optional[tuple[str, int]]:
    """`(key, sourced)` from `episode_id`, or None for anything malformed.
    A bare key (no sourced part) parses with sourced 0 - "whatever is kept
    under this key" - which is how history rows from before §173 replay."""
    m = _EPISODE_ID.match((value or "").strip())
    if not m:
        return None
    return m.group(1), int(m.group(2) or 0)


def _clocks(ttl: int, sourced_at: Optional[float], now: float) -> tuple:
    """(sourced, current until, kept until) for a write (§143).

    Kept `CACHE_LIFE_SECONDS` from when it was sourced - a week, whatever the
    episode is about - and current for `ttl` from then, which `ttl_for` may
    make 0. Never kept for less than it is current: an evergreen ceiling set
    above the life keeps the row as long as it is servable. A sourced time in
    the future is a clock error, not a fact, and is read as now.
    """
    sourced = min(float(sourced_at or now), now)
    life = int(settings.cache_life_seconds or 0)
    if life <= 0:
        # `CACHE_LIFE_SECONDS=0`: kept only while current, which is exactly
        # how the cache behaved before §143 (a negative `ttl` is written
        # already expired, as it always was).
        until = sourced + int(ttl or 0)
        return sourced, until, until
    fresh_until = sourced + max(0, int(ttl or 0))
    return sourced, fresh_until, max(sourced + life, fresh_until)


def describe_sourced(sourced_at: float, now: Optional[float] = None) -> str:
    """How long ago an episode's information was sourced, in words (§143).

    For logs and tools. The interface formats its own, in the reader's clock.
    """
    if not sourced_at:
        return "unknown"
    age = max(0.0, (time.time() if now is None else now) - float(sourced_at))
    if age < 90:
        return "just now"
    if age < 5400:
        return f"{int(round(age / 60))} min ago"
    if age < 36 * 3600:
        return f"{int(round(age / 3600))} h ago"
    return f"{int(round(age / 86400))} days ago"


class MemoryScriptCache:
    """Process-local cache. Fine for tests and single-worker development."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[float, list[str], str, str, int]] = {}
        #: key -> the listener who first generated it. Beside the tuple for
        #: the same reason `_sources` is: the shape the tests assert on stays
        #: unchanged. "" for anything written with no listener behind it -
        #: prefetch, a tool, a test - which is shown to everybody.
        self._authors: dict[str, str] = {}
        #: key -> the surface that first wrote it ("search", "myfam",
        #: "dailyfam", "trending", "prefetch", ...). Explore reads it (§147).
        self._origins: dict[str, str] = {}
        #: key -> the voice the episode is spoken in when nobody chose one
        #: (§147): a bank voice drawn at random for a browse episode, or the
        #: voice a search was first heard in. Explore replays it.
        self._voices: dict[str, str] = {}
        #: key -> provenance JSON. Beside the tuple rather than in it, so the
        #: shape the existing tests assert on is unchanged.
        self._sources: dict[str, str] = {}
        #: key -> the episode's own title, for the same reason.
        self._titles: dict[str, str] = {}
        #: key -> its one-sentence summary, for the same reason again.
        self._summaries: dict[str, str] = {}
        #: key -> the writer's `<<CATEGORY:>>` words (§189).
        self._categories: dict[str, str] = {}
        #: key -> (bucket, packed vector). Kept beside the entries rather than
        #: in the tuple so the shape the tests already assert on is unchanged.
        self._vectors: dict[str, tuple[str, bytes]] = {}
        #: (key, voice) -> {blob, rate, sentences, starts, played, created,
        #: codec, frames, tier}. The memory half of `episode_audio`.
        self._audio: dict[tuple[str, str], list] = {}
        #: key -> how many times it was played. The memory half of the
        #: `plays` column (§134).
        self._plays: dict[str, int] = {}
        #: key -> when its current script was written. Beside the tuple for
        #: the same reason as everything above it.
        self._created: dict[str, float] = {}
        #: key -> (sourced, current until) - §143. The tuple's first field is
        #: how long the entry is *kept*; this is how long it is *current*.
        self._clocks: dict[str, tuple[float, float]] = {}
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

    def _current(self, key: str, now: Optional[float] = None) -> bool:
        entry = self._data.get(key)
        now = time.time() if now is None else now
        if not entry or entry[0] < now:
            return False
        clocks = self._clocks.get(key)
        return (clocks[1] if clocks else entry[0]) >= now

    def sourced_at(self, key: str) -> Optional[float]:
        entry = self._data.get(key)
        if not entry or entry[0] < time.time():
            return None
        clocks = self._clocks.get(key)
        return clocks[0] if clocks else self._created.get(key, 0.0)

    def extend_current(self, key: str, until: float) -> bool:
        """See `SqliteScriptCache.extend_current`."""
        if not self._current(key):
            return False
        entry = self._data[key]
        sourced, fresh = self._clocks.get(key, (self._created.get(key, 0.0), entry[0]))
        fresh = max(fresh, float(until))
        self._clocks[key] = (sourced, fresh)
        self._data[key] = (max(entry[0], fresh),) + tuple(entry[1:])
        return True

    def written_at(self, key: str) -> Optional[float]:
        if not self._current(key):
            return None
        # A live entry with no recorded write time was written at some
        # unknown point, which must read as "long ago" and never as "no
        # script" - None is what tells myFAM a tap would write a new one.
        return self._created.get(key, 0.0)

    def get(self, key: str, current: bool = True) -> Optional[list[str]]:
        entry = self._data.get(key)
        if (not entry or entry[0] < time.time()
                or (current and not self._current(key))):
            self.misses += 1
            return None
        self.hits += 1
        return list(entry[1])

    def put(
        self, key: str, sentences: list[str], ttl: int, query: str = "",
        thread: str = "", minutes: int = 0, bucket: str = "", sources: str = "",
        author: str = "", title: str = "", summary: str = "", slide: bool = False,
        sourced_at: Optional[float] = None, origin: str = "", voice: str = "",
        category: str = ""
    ) -> None:
        before = self._data.get(key)
        # A search re-writing an entry makes it a search episode; nothing
        # else changes where an episode came from (§147).
        if origin == "search" or not self._origins.get(key):
            self._origins[key] = origin or ""
        if voice and not self._voices.get(key):
            self._voices[key] = voice
        now = time.time()
        # A different, still-kept episode under this key moves aside (§173).
        if (before is not None and before[0] >= now
                and list(before[1]) != list(sentences)):
            self._archive(key, self.sourced_at(key) or 0.0)
        sourced, fresh_until, expires = _clocks(ttl, sourced_at, now)
        self._data[key] = (expires, list(sentences), thread, query, int(minutes))
        self._created[key] = now
        self._clocks[key] = (sourced, fresh_until)
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
        if category:
            self._categories[key] = category
        elif before is not None and list(before[1]) != list(sentences):
            # New words with no category of their own (§209): the old one
            # described the old script.
            self._categories.pop(key, None)
        # First writer only. A second listener asking the same question is
        # served from this entry and never rewrites it, so authorship stays
        # "who paid for this" rather than "who asked most recently".
        self._authors.setdefault(key, author or "")
        if bucket and query:
            self._vectors[key] = (bucket, embeddings.pack(embeddings.embed(normalize_query(query))))

    def _archive(self, key: str, sourced: float) -> str:
        """See `SqliteScriptCache._archive`."""
        target = archive_key(key, sourced)
        self._data[target] = self._data[key]
        self._created[target] = self._created.get(key, 0.0)
        self._clocks[target] = (sourced, 1.0)
        self._origins[target] = ARCHIVE_ORIGIN
        for table in (self._authors, self._voices, self._sources, self._titles,
                      self._summaries, self._categories, self._plays):
            if key in table:
                table[target] = table[key]
        self._drop_audio(target)
        for pair in [p for p in self._audio if p[0] == key]:
            self._audio[(target, pair[1])] = self._audio.pop(pair)
        return target

    def resolve_episode(self, episode: str) -> str:
        """See `SqliteScriptCache.resolve_episode`."""
        parsed = parse_episode_id(episode)
        if parsed is None:
            return ""
        key, sourced = parsed
        if self._live(key) and (not sourced
                                or int(self.sourced_at(key) or 0) == sourced):
            return key
        if not sourced:
            return ""
        target = archive_key(key, sourced)
        return target if self._live(target) else ""

    def keep_until(self, key: str, until: float) -> bool:
        """See `SqliteScriptCache.keep_until`."""
        if not self._live(key):
            return False
        entry = self._data[key]
        self._data[key] = (max(entry[0], float(until)),) + tuple(entry[1:])
        return True

    def nearest(self, bucket: str, query: str) -> Optional[tuple[str, float]]:
        if not settings.cache_vector or not bucket:
            return None
        now = time.time()
        rows = [
            (key, self._data[key][3], vec)
            for key, (b, vec) in self._vectors.items()
            if b == bucket and self._current(key, now)
        ]
        return best_match(query, rows)

    def voice_of(self, key: str) -> str:
        return self._voices.get(key, "") if key in self._data else ""

    def explicit(self, key: str) -> bool:
        """See `SqliteScriptCache.explicit`."""
        return self._live(key) and content_filter.is_explicit(self._data[key][1])

    def set_voice(self, key: str, voice: str) -> str:
        """See `SqliteScriptCache.set_voice`."""
        if not key or not voice or key not in self._data:
            return self.voice_of(key)
        self._voices.setdefault(key, voice)
        if not self._voices[key]:
            self._voices[key] = voice
        return self._voices[key]

    def origin_of(self, key: str) -> str:
        return self._origins.get(key, "") if key in self._data else ""

    def recent(self, limit: int = 40, exclude_author: str = "",
               origin: str = "") -> list[dict]:
        live = [
            {"key": k, "query": v[3], "minutes": v[4],
             "created": self._created.get(k, 0.0),
             "plays": self._plays.get(k, 0), "thread": v[2],
             "title": self._titles.get(k, ""),
             "author": self._authors.get(k, ""),
             "sourced_at": self.sourced_at(k) or 0.0,
             "current": self._current(k),
             "origin": self._origins.get(k, ""),
             "voice": self._voices.get(k, ""),
             "explicit": content_filter.is_explicit(v[1])}
            for k, v in self._data.items()
            if v[0] >= time.time() and v[3] and v[4] > 0
            and self._origins.get(k, "") != ARCHIVE_ORIGIN
            and not (exclude_author and self._authors.get(k) == exclude_author)
            and not (origin and self._origins.get(k, "") != origin)
        ]
        live.sort(key=lambda e: -e["created"])
        return live[:limit]

    def authored_by(self, authors, since: float = 0.0,
                    limit: int = 200) -> list[dict]:
        wanted = {a for a in authors if a}
        if not wanted:
            return []
        now = time.time()
        rows = [
            {"key": k, "query": v[3], "minutes": v[4],
             "created": self._created.get(k, 0.0),
             "title": self._titles.get(k, ""), "author": self._authors.get(k, ""),
             "sourced_at": self.sourced_at(k) or 0.0}
            for k, v in self._data.items()
            if v[0] >= now and v[3] and v[4] > 0
            and self._origins.get(k, "") != ARCHIVE_ORIGIN
            and self._authors.get(k, "") in wanted
            and self._created.get(k, 0.0) >= since
        ]
        rows.sort(key=lambda e: -e["created"])
        return rows[:limit]

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

    def category(self, key: str) -> str:
        entry = self._data.get(key)
        if not entry or entry[0] < time.time():
            return ""
        return self._categories.get(key, "")

    # -- audio (§132) -------------------------------------------------------

    def _live(self, key: str) -> bool:
        entry = self._data.get(key)
        return bool(entry) and entry[0] >= time.time()

    def _drop_audio(self, key: str) -> None:
        for pair in [p for p in self._audio if p[0] == key]:
            self._audio.pop(pair, None)

    def has_audio(self, key: str, voice: str, sample_rate: int) -> bool:
        row = self._audio.get((key, voice))
        return bool(row) and self._live(key) and row["rate"] == int(sample_rate)

    def has_any_audio(self, key: str) -> bool:
        """Whether any voice's audio is kept for this episode. A marker for a
        guest's sample tile only - playing still asks `has_audio` for the
        voice and rate that will actually be served."""
        return self._live(key) and any(k == key for k, _v in self._audio)

    def get_audio(self, key: str, voice: str, sample_rate: int) -> Optional[StoredAudio]:
        if not self.has_audio(key, voice, sample_rate):
            return None
        row = self._audio[(key, voice)]
        row["played"] = time.time()
        return StoredAudio(row["blob"], row["rate"], list(row["sentences"]),
                           list(row["starts"]), row["codec"], row["frames"])

    def put_audio(self, key: str, voice: str, sample_rate: int, pcm: bytes,
                  sentences: list, starts: list) -> bool:
        """The memory twin of `SqliteScriptCache.put_audio`, without a bucket:
        this backend is tests and single-process development."""
        if not pcm or not self._live(key):
            return False
        codec, blob = audio_codec.encode(pcm, sample_rate)
        now = time.time()
        self._audio[(key, voice)] = {
            "blob": blob, "rate": int(sample_rate), "sentences": list(sentences),
            "starts": list(starts), "played": now, "created": now,
            "codec": codec, "frames": len(pcm) // audio_codec.SAMPLE_BYTES,
            "tier": ""}
        ceiling = audio_ceiling_bytes()
        if ceiling:
            while sum(len(r["blob"]) for r in self._audio.values()) > ceiling \
                    and len(self._audio) > 1:
                oldest = min(self._audio, key=lambda p: self._audio[p]["played"])
                self._audio.pop(oldest)
        return (key, voice) in self._audio

    def sweep_audio(self, held, now: Optional[float] = None,
                    check_kept: bool = True) -> dict:
        """See `SqliteScriptCache.sweep_audio`. No bucket here, so keeping is
        a mark and deleting is a pop."""
        now = time.time() if now is None else now
        cutoff = now - recent_seconds()
        counts = {"kept": 0, "deleted": 0, "released": 0, "failed": 0,
                  "uploaded": 0}
        for pair, row in list(self._audio.items()):
            key = pair[0]
            is_held = key in held and self._origins.get(key) != ARCHIVE_ORIGIN
            if row["tier"] != KEPT_TIER and row["created"] < cutoff:
                if is_held:
                    row["tier"] = KEPT_TIER
                    counts["kept"] += 1
                else:
                    self._audio.pop(pair)
                    counts["deleted"] += 1
            elif row["tier"] == KEPT_TIER and check_kept and not is_held:
                self._audio.pop(pair)
                counts["released"] += 1
        for key, _voice in [p for p, r in self._audio.items() if r["tier"] == KEPT_TIER]:
            entry = self._data.get(key)
            if entry is not None:
                self._data[key] = (max(entry[0], now + kept_pin_seconds()),) + entry[1:]
        return counts

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
            self._categories.pop(key, None)
            self._vectors.pop(key, None)
            self._clocks.pop(key, None)
            self._drop_audio(key)
        return len(keys)

    def anonymise_author(self, author: str) -> int:
        """The memory backend's half of `SqliteScriptCache.anonymise_author`."""
        if not author:
            return 0
        keys = [k for k, who in self._authors.items() if who == author]
        for key in keys:
            self._authors[key] = ""
        return len(keys)

    def clear(self) -> int:
        removed = len(self._data)
        for table in (self._data, self._authors, self._origins, self._voices,
                      self._sources, self._titles,
                      self._summaries, self._categories, self._vectors,
                      self._audio, self._clocks):
            table.clear()
        return removed

    def stats(self) -> dict:
        return {"backend": "memory", "entries": len(self._data), "hits": self.hits,
                "misses": self.misses, "audio_entries": len(self._audio),
                "audio_bytes": sum(len(r["blob"]) for r in self._audio.values())}


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
                # §143: when the information the episode was written from was
                # *sourced* - the retrieval, not the write - and how long it
                # stays *current*. `expires` is now how long the row is kept
                # (a week for everything); `fresh_until` is how long a new
                # request may be served it as the answer. Rows written before
                # this have 0 in both and read exactly as they did: sourced
                # when created, current until they expire (`_CURRENT_UNTIL`).
                ("sourced_at", "ALTER TABLE scripts ADD COLUMN sourced_at REAL NOT NULL DEFAULT 0"),
                ("fresh_until", "ALTER TABLE scripts ADD COLUMN fresh_until REAL NOT NULL DEFAULT 0"),
                # §147: which surface wrote it. Explore is searched episodes
                # only, and without this the cache cannot tell a search from
                # a myFAM tile, a DailyFAM edition or a warmed guess. Rows
                # written before it have '' and are not on Explore.
                ("origin", "ALTER TABLE scripts ADD COLUMN origin TEXT NOT NULL DEFAULT ''"),
                # §147: the voice the episode is spoken in when the listener
                # did not choose one - a random bank voice for a browse
                # episode, the searcher's own for a search. Kept so every
                # later play is the same voice and so its kept audio is hit.
                ("voice", "ALTER TABLE scripts ADD COLUMN voice TEXT NOT NULL DEFAULT ''"),
                # §189: what kind of thing the episode turned out to be about,
                # off the writer's `<<CATEGORY:>>` line, so a written tile's
                # picture and facet come from the episode rather than from a
                # guess made before it was researched. Rows written before
                # this have '' and keep the tile's own category.
                ("category", "ALTER TABLE scripts ADD COLUMN category TEXT NOT NULL DEFAULT ''"),
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
            # §237. `codec` says what packed `pcm` (rows before this are
            # zlib); `frames` is the original length in samples; `object_key`
            # names the copy in the bucket ("" when there is none); `tier` is
            # "" for a first-week episode and "kept" for one somebody saved,
            # shared or vibed. With a bucket, `pcm` is a hot copy and may be
            # empty - the row then still describes the episode, and a play
            # reads the bytes from `object_key`.
            for column, ddl in (
                ("codec", "ALTER TABLE episode_audio ADD COLUMN codec TEXT NOT NULL DEFAULT 'zlib'"),
                ("frames", "ALTER TABLE episode_audio ADD COLUMN frames INTEGER NOT NULL DEFAULT 0"),
                ("object_key", "ALTER TABLE episode_audio ADD COLUMN object_key TEXT NOT NULL DEFAULT ''"),
                ("tier", "ALTER TABLE episode_audio ADD COLUMN tier TEXT NOT NULL DEFAULT ''"),
            ):
                try:
                    conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass  # already there
            conn.execute("CREATE INDEX IF NOT EXISTS episode_audio_tier"
                         " ON episode_audio(tier, created)")
            # Objects whose rows are gone, waiting to be deleted from the
            # bucket. A row is dropped in one statement wherever it is
            # dropped; the network call happens later, off every path a
            # listener waits on, and survives a restart in between.
            conn.execute(
                """CREATE TABLE IF NOT EXISTS audio_deletes (
                       name TEXT PRIMARY KEY,
                       at   REAL NOT NULL
                   )"""
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

    def get(self, key: str, current: bool = True) -> Optional[list[str]]:
        """The script under `key`, or None.

        `current` (the default) answers "may a new request be served this as
        the answer" - kept *and* inside its current window (§143). Every path
        that would otherwise write an episode asks that. A replay surface
        (`cached_only`: Explore) passes False and gets
        anything still kept, because it is replaying a specific finished
        episode whose sourced time is on the card.
        """
        try:
            conn = self._conn()
            row = conn.execute(
                "SELECT sentences, expires, fresh_until FROM scripts WHERE key = ?",
                (key,)
            ).fetchone()
            now = time.time()
            if not row or row[1] < now:
                return None
            if current and (row[2] or row[1]) < now:
                return None
            conn.execute("UPDATE scripts SET hits = hits + 1 WHERE key = ?", (key,))
            # Scrubbed on the way out too (§171): a row written before the
            # filter existed is kept a week, and must not say what a new
            # episode could not.
            return content_filter.scrub_all(json.loads(row[0]))
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
            f"SELECT created, {_CURRENT_UNTIL} FROM scripts WHERE key = ?", (key,)
        ).fetchone()
        # Current rather than kept (§143): None means "a tap would write a new
        # one", and a tap is served only a current script.
        if not row or row[1] < time.time():
            return None
        return float(row[0])

    def extend_current(self, key: str, until: float) -> bool:
        """Keep a *current* entry current until `until`. False if it is not.

        For the DailyFAM edition (§143): a subject a listener tapped before
        the edition reached it was written by the tap path, sourced after the
        edition began and just as fresh as the edition's own would be - but
        current for `ttl_for`'s fifteen minutes rather than until the next
        edition. Re-writing it would pay for the same episode twice; this
        gives it the edition's window instead. Never makes a non-current
        entry current, so a mid-game episode stays out of reach.
        """
        try:
            cur = self._conn().execute(
                "UPDATE scripts SET fresh_until = MAX(?, " + _CURRENT_UNTIL + "),"
                " expires = MAX(expires, ?)"
                " WHERE key = ? AND expires >= ? AND " + _CURRENT_UNTIL + " >= ?",
                (float(until), float(until), key, time.time(), time.time()))
            return bool(cur.rowcount)
        except Exception:
            log.exception("could not extend a cache entry; continuing")
            return False

    def sourced_at(self, key: str) -> Optional[float]:
        """When the information in the kept episode under `key` was sourced,
        or None if nothing is kept there (§143). Rows from before the column
        existed answer with when they were written, which is the closest
        thing they recorded."""
        try:
            row = self._conn().execute(
                f"SELECT {_SOURCED}, expires FROM scripts WHERE key = ?", (key,)
            ).fetchone()
        except Exception:
            log.exception("script cache sourced_at read failed")
            return None
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
            # Both clocks move together: only an evergreen entry slides, and
            # an evergreen entry is current for as long as it is kept.
            conn.execute("UPDATE scripts SET expires = ?, fresh_until = ?"
                         " WHERE key = ?", (target, target, key))

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
        author: str = "", title: str = "", summary: str = "", slide: bool = False,
        sourced_at: Optional[float] = None, origin: str = "", voice: str = "",
        category: str = ""
    ) -> None:
        """Store the script, and the vector for the question that produced it.

        **`ttl` is how long it stays current, not how long it is kept**
        (§143). Every row is kept `CACHE_LIFE_SECONDS` (a week) from when its
        information was sourced, and `ttl` - `ttl_for`'s answer, which may be
        0 - only decides how long a new request may be served it. `sourced_at`
        is when the retrieval happened; it defaults to now.

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
            sourced, fresh_until, expires = _clocks(ttl, sourced_at, now)
            vector = None
            if bucket and query:
                vector = embeddings.pack(embeddings.embed(normalize_query(query)))
            # What the key held before, so kept audio survives a re-write
            # that said the same thing (below).
            before = self._conn().execute(
                f"SELECT sentences, {_SOURCED}, expires FROM scripts WHERE key = ?",
                (key,)).fetchone()
            # A different episode under the same key: the one it replaces
            # moves aside rather than being lost (§173), because somebody
            # heard it and their history points at it.
            if (before is not None and before[0] != json.dumps(sentences)
                    and before[2] >= now):
                try:
                    self._archive(key, float(before[1] or 0.0))
                except Exception:
                    # Never at the cost of the new episode: a failed archive
                    # loses the old one (as before §173), not both.
                    log.exception("could not archive the episode under a"
                                  " re-written key; writing the new one")
            # `COALESCE` on the existing author rather than the new one:
            # a re-write of a live entry (a longer TTL, fresher sources) must
            # not hand authorship to whoever happened to trigger it. Only a
            # row that has none takes one.
            self._conn().execute(
                "INSERT INTO scripts"
                " (key, expires, created, hits, query, sentences, thread, minutes,"
                "  bucket, vector, sources, author, title, summary, ttl,"
                "  sourced_at, fresh_until, origin, voice, category)"
                " VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET"
                "  expires = excluded.expires, created = excluded.created,"
                "  ttl = excluded.ttl, sourced_at = excluded.sourced_at,"
                "  fresh_until = excluded.fresh_until,"
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
                "                 ELSE scripts.summary END,"
                # A re-write's category, even an empty one, when it wrote
                # new words (§209): the old category described the old
                # script. Kept only for a re-write that said the same thing.
                "  category = CASE WHEN excluded.category != ''"
                "                  THEN excluded.category"
                "                  WHEN scripts.sentences = excluded.sentences"
                "                  THEN scripts.category ELSE '' END,"
                # §147. Where it came from is the first writer's, except that
                # a search re-writing it makes it a searched episode. Its
                # voice is the first one it was given, so every later play
                # (and the audio kept for it) stays the same voice.
                "  origin = CASE WHEN excluded.origin = 'search' THEN 'search'"
                "                WHEN scripts.origin != '' THEN scripts.origin"
                "                ELSE excluded.origin END,"
                "  voice = CASE WHEN scripts.voice != '' THEN scripts.voice"
                "               ELSE excluded.voice END",
                (key, expires, now, query[:500], json.dumps(sentences),
                 thread[:200], int(minutes), bucket, vector, sources or "",
                 (author or "")[:64], (title or "")[:120], (summary or "")[:240],
                 int(ttl) if slide else 0, sourced, fresh_until,
                 (origin or "")[:16], (voice or "")[:80], (category or "")[:60]),
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
                self._forget_audio("key = ?", (key,))
        except Exception:
            log.exception("script cache write failed; continuing")

    # -- audio (§132) -------------------------------------------------------

    def _archive(self, key: str, sourced: float) -> str:
        """Copy the row under `key` to its archive key, with its audio (§173).

        The copy is never current (`fresh_until` 1), has no bucket or vector
        (so no near match finds it), never slides (`ttl` 0) and is marked
        `ARCHIVE_ORIGIN` (so no feed or rail shows it). It keeps how long it
        is kept, and its author, so `forget_author` still reaches it. The
        audio is *moved*: it belongs to these words, not the new ones.
        """
        target = archive_key(key, sourced)
        conn = self._conn()
        columns = [r[1] for r in conn.execute("PRAGMA table_info(scripts)")]
        fixed = {"key": "?", "fresh_until": "1.0", "bucket": "''",
                 "vector": "NULL", "ttl": "0", "hits": "0",
                 "origin": f"'{ARCHIVE_ORIGIN}'"}
        picks = ", ".join(fixed.get(c, c) for c in columns)
        conn.execute(
            f"INSERT OR REPLACE INTO scripts ({', '.join(columns)})"
            f" SELECT {picks} FROM scripts WHERE key = ?", (target, key))
        self._forget_audio("key = ?", (target,))
        conn.execute("UPDATE episode_audio SET key = ? WHERE key = ?", (target, key))
        return target

    def resolve_episode(self, episode: str) -> str:
        """The key holding the heard episode `episode` names, or "" (§173).

        The key itself while it still holds that episode, else the archive
        copy made when the key was written again. Kept, not current: this is
        a replay of something already heard."""
        parsed = parse_episode_id(episode)
        if parsed is None:
            return ""
        key, sourced = parsed
        now = time.time()
        try:
            conn = self._conn()
            row = conn.execute(
                f"SELECT {_SOURCED}, expires FROM scripts WHERE key = ?",
                (key,)).fetchone()
            if row and row[1] >= now and (not sourced or int(row[0] or 0) == sourced):
                return key
            if not sourced:
                return ""
            target = archive_key(key, sourced)
            row = conn.execute("SELECT expires FROM scripts WHERE key = ?",
                               (target,)).fetchone()
            return target if row and row[0] >= now else ""
        except Exception:
            log.exception("could not resolve a heard episode")
            return ""

    def keep_until(self, key: str, until: float) -> bool:
        """Keep the row under `key` (and so its audio) at least until `until`
        (§173). Never makes it current: a listening-history row pins the
        episode it names for as long as the history shows it."""
        try:
            cur = self._conn().execute(
                "UPDATE scripts SET expires = MAX(expires, ?)"
                " WHERE key = ? AND expires >= ?",
                (float(until), key, time.time()))
            return bool(cur.rowcount)
        except Exception:
            log.exception("could not keep a heard episode; continuing")
            return False

    def has_audio(self, key: str, voice: str, sample_rate: int) -> bool:
        try:
            row = self._conn().execute(
                "SELECT a.sentences FROM episode_audio a JOIN scripts s ON s.key = a.key"
                " WHERE a.key = ? AND a.voice = ? AND a.sample_rate = ?"
                " AND s.expires >= ?",
                (key, voice, int(sample_rate), time.time()),
            ).fetchone()
            # Audio `get_audio` would drop (§171) is not audio anybody may be
            # promised: a guest's tap is let through on this answer, and must
            # never reach the voice engine because of it.
            return row is not None and not _voiced_a_slur(row[0])
        except Exception:
            log.exception("audio cache check failed")
            return False

    def has_any_audio(self, key: str) -> bool:
        """Whether any voice's audio is kept for this episode, while its
        script is still readable. See the memory backend's twin."""
        try:
            rows = self._conn().execute(
                "SELECT a.sentences FROM episode_audio a JOIN scripts s ON s.key = a.key"
                " WHERE a.key = ? AND s.expires >= ?",
                (key, time.time()),
            ).fetchall()
            # See `has_audio`: audio voiced with a slur in it is not kept audio.
            return any(not _voiced_a_slur(r[0]) for r in rows)
        except Exception:
            log.exception("audio cache check failed")
            return False

    def _forget_audio(self, where: str, params: tuple) -> int:
        """Drop the audio rows matching `where`, and queue their objects for
        deletion from the bucket (§237). The one way a row is dropped, so a
        wipe, a purge, a re-write or a forgotten author can never leave an
        object behind that nothing points at."""
        conn = self._conn()
        now = time.time()
        for (name,) in conn.execute(
                f"SELECT object_key FROM episode_audio WHERE ({where})"
                " AND object_key != ''", params).fetchall():
            conn.execute("INSERT OR IGNORE INTO audio_deletes (name, at)"
                         " VALUES (?, ?)", (name, now))
        cur = conn.execute(f"DELETE FROM episode_audio WHERE {where}", params)
        return cur.rowcount or 0

    def get_audio(self, key: str, voice: str, sample_rate: int) -> Optional[StoredAudio]:
        """The kept audio for this episode in this voice, or None.

        None whenever the rate differs from the engine's: the stream header has
        already been written at the engine's rate, and PCM at another one would
        play at the wrong pitch rather than fail.

        With a bucket (§237) this may make a network call - the hot copy is
        read when there is one, else the object - so call it off the event
        loop; `PodcastPipeline` does. An object the bucket no longer has drops
        its row: the episode is voiced again and kept again, never failed.
        """
        try:
            conn = self._conn()
            row = conn.execute(
                "SELECT a.pcm, a.sample_rate, a.sentences, a.starts, a.codec,"
                " a.frames, a.object_key"
                " FROM episode_audio a JOIN scripts s ON s.key = a.key"
                " WHERE a.key = ? AND a.voice = ? AND s.expires >= ?",
                (key, voice, time.time()),
            ).fetchone()
            if not row or int(row[1]) != int(sample_rate):
                return None
            # Audio voiced before the slur filter (§171) cannot be scrubbed:
            # it is dropped, and the episode is voiced again from the
            # scrubbed script - and kept again, clean.
            if _voiced_a_slur(row[2]):
                self._forget_audio("key = ?", (key,))
                return None
            blob = row[0] or b""
            if not blob:
                blob, missing = self._read_object(row[6])
                if not blob:
                    # Only an object the bucket says is not there drops the
                    # row. A bucket that did not answer drops nothing - an
                    # outage must never delete what somebody saved; this play
                    # is voiced again and the row stays for the next one.
                    if missing:
                        self._forget_audio("key = ? AND voice = ?", (key, voice))
                    return None
                # A hot copy for the next play, under the same ceiling.
                conn.execute("UPDATE episode_audio SET pcm = ? WHERE key = ? AND voice = ?",
                             (sqlite3.Binary(blob), key, voice))
                self._evict_audio()
            conn.execute(
                "UPDATE episode_audio SET played = ? WHERE key = ? AND voice = ?",
                (time.time(), key, voice))
            return StoredAudio(bytes(blob), int(row[1]), json.loads(row[2]),
                               json.loads(row[3]), row[4] or audio_codec.ZLIB,
                               int(row[5] or 0))
        except Exception:
            # Same rule as the script: a broken store means the episode is
            # synthesised the old way, never that it fails.
            log.exception("audio cache read failed; synthesising")
            return None

    @staticmethod
    def _read_object(name: str) -> tuple[bytes, bool]:
        """(bytes, missing). `missing` is True only when the bucket answered
        that the object is not there; no bucket configured, a timeout or an
        error is (b"", False) - unknown, never gone."""
        store = audio_store.get_store() if name else None
        if store is None:
            return b"", False
        try:
            blob = store.get(name)
        except audio_store.AudioStoreError as exc:
            log.warning("audio store: %s; voicing this episode again", exc)
            return b"", False
        return (blob or b""), blob is None

    @classmethod
    def _fetch_object(cls, name: str) -> bytes:
        return cls._read_object(name)[0]

    def put_audio(self, key: str, voice: str, sample_rate: int, pcm: bytes,
                  sentences: list, starts: list) -> bool:
        """Keep an episode's audio. Only beside a live script row, never alone.

        Returns whether it was kept. Packing (Opus, §237) and the upload to
        the bucket happen here, so call it off the event loop -
        `PodcastPipeline` does, after the last byte is sent. The row is
        written only after the upload succeeded, so a row with an object name
        always names an object; a failed upload keeps the audio here alone
        and says so in the log.
        """
        if not pcm:
            return False
        try:
            now = time.time()
            live = self._conn().execute(
                "SELECT 1 FROM scripts WHERE key = ? AND expires >= ?",
                (key, now)).fetchone()
            if not live:
                return False
            codec, blob = audio_codec.encode(pcm, sample_rate)
            frames = len(pcm) // audio_codec.SAMPLE_BYTES
            name = ""
            store = audio_store.get_store()
            if store is not None:
                candidate = audio_store.object_name(
                    audio_store.RECENT, secrets.token_hex(16))
                try:
                    store.put(candidate, blob)
                    name = candidate
                except audio_store.AudioStoreError as exc:
                    log.warning("audio store: %s; keeping this episode in"
                                " scripts.db only", exc)
            # A row this replaces may name an object of its own.
            self._forget_audio("key = ? AND voice = ?", (key, voice))
            cur = self._conn().execute(
                "INSERT OR REPLACE INTO episode_audio"
                " (key, voice, sample_rate, created, played, bytes, sentences,"
                "  starts, pcm, codec, frames, object_key, tier)"
                " SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '' FROM scripts"
                " WHERE key = ? AND expires >= ?",
                (key, voice, int(sample_rate), now, now, len(blob),
                 json.dumps(list(sentences)), json.dumps(list(starts)),
                 sqlite3.Binary(blob), codec, frames, name, key, now),
            )
            if not cur.rowcount:
                if name:
                    self._conn().execute("INSERT OR IGNORE INTO audio_deletes"
                                         " (name, at) VALUES (?, ?)", (name, now))
                return False
            log.info("audio kept as %s (%.0f%% of raw)%s", codec,
                     100 * audio_codec.ratio(codec, blob, frames),
                     " and in the bucket" if name else "")
            self._evict_audio()
            # An episode larger than the whole ceiling is evicted by its own
            # write; saying it was kept would be a claim nothing can serve.
            return self.has_audio(key, voice, sample_rate)
        except Exception:
            log.exception("audio cache write failed; continuing")
            return False

    def _evict_audio(self) -> None:
        """Least recently played first, until the hot copies fit the ceiling.

        Only audio goes: the script stays. A row whose audio is also in the
        bucket (§237) loses only its hot copy and is read from the bucket
        next time; one with no object loses the row and costs one
        re-synthesis on its next play.
        """
        ceiling = audio_ceiling_bytes()
        if not ceiling:
            return
        conn = self._conn()
        total = conn.execute(
            "SELECT COALESCE(SUM(length(pcm)), 0) FROM episode_audio").fetchone()[0]
        if total <= ceiling:
            return
        for key, voice, size, name in conn.execute(
                "SELECT key, voice, length(pcm), object_key FROM episode_audio"
                " WHERE length(pcm) > 0 ORDER BY played ASC"
        ).fetchall():
            if total <= ceiling:
                break
            if name:
                conn.execute("UPDATE episode_audio SET pcm = x'' WHERE key = ? AND voice = ?",
                             (key, voice))
            else:
                conn.execute("DELETE FROM episode_audio WHERE key = ? AND voice = ?",
                             (key, voice))
            total -= size
        log.info("audio cache: evicted down to %.1f MB", total / 1048576)

    def sweep_audio(self, held, now: Optional[float] = None,
                    check_kept: bool = True) -> dict:
        """Apply §237's retention to every episode's audio.

        Audio is kept a week (`AUDIO_RECENT_DAYS`) for everybody. At a week
        old, audio whose episode is in `held` - the cache keys of everything
        somebody saved, shared or vibed (`app._held_episode_keys`) - is copied
        to `kept/` in the bucket's Infrequent Access class and its script is
        pinned; everything else is deleted. `check_kept` also releases kept
        audio nobody holds any more (once a day is enough; it reads every
        kept row).

        A copy that fails leaves the row as it was, to be tried next sweep:
        the bucket keeps `recent/` a day past the week for exactly that.
        Never raises; returns what it did.
        """
        now = time.time() if now is None else now
        counts = {"kept": 0, "deleted": 0, "released": 0, "failed": 0,
                  "uploaded": 0}
        try:
            conn = self._conn()
            store = audio_store.get_store()
            if store is not None:
                counts["uploaded"] = self._upload_strays(store)
            rows = conn.execute(
                "SELECT a.key, a.voice, a.object_key, COALESCE(s.origin, ''), a.tier"
                " FROM episode_audio a LEFT JOIN scripts s ON s.key = a.key"
                " WHERE (a.tier != ? AND a.created < ?)"
                " OR (a.tier = ? AND a.object_key LIKE ?)",
                (KEPT_TIER, now - recent_seconds(), KEPT_TIER,
                 audio_store.RECENT + "%")).fetchall()
            for key, voice, name, origin, tier in rows:
                if tier != KEPT_TIER and (key not in held or origin == ARCHIVE_ORIGIN):
                    counts["deleted"] += self._forget_audio(
                        "key = ? AND voice = ?", (key, voice))
                    continue
                target = name
                if name and store is not None:
                    target = audio_store.swap_prefix(name, audio_store.KEPT)
                    try:
                        store.copy(name, target, audio_store.INFREQUENT)
                    except audio_store.AudioStoreError as exc:
                        log.warning("audio store: %s; will try again next sweep", exc)
                        counts["failed"] += 1
                        continue
                    conn.execute("INSERT OR IGNORE INTO audio_deletes (name, at)"
                                 " VALUES (?, ?)", (name, now))
                # Cold now: with an object the hot copy goes, and the next
                # play reads the bucket. Without one, the row is all there is.
                conn.execute(
                    "UPDATE episode_audio SET tier = ?, object_key = ?,"
                    " pcm = CASE WHEN ? != '' THEN x'' ELSE pcm END"
                    " WHERE key = ? AND voice = ?",
                    (KEPT_TIER, target, target, key, voice))
                counts["kept"] += tier != KEPT_TIER
            if check_kept:
                for key, voice in conn.execute(
                        "SELECT key, voice FROM episode_audio WHERE tier = ?",
                        (KEPT_TIER,)).fetchall():
                    if key not in held:
                        counts["released"] += self._forget_audio(
                            "key = ? AND voice = ?", (key, voice))
            # Kept audio is only readable while its script is (§132).
            conn.execute(
                "UPDATE scripts SET expires = MAX(expires, ?) WHERE key IN"
                " (SELECT key FROM episode_audio WHERE tier = ?)",
                (now + kept_pin_seconds(), KEPT_TIER))
        except Exception:
            log.exception("audio sweep failed; it runs again next hour")
        return counts

    def _upload_strays(self, store, limit: int = 200) -> int:
        """Put audio that has no object into the bucket: rows from before the
        bucket was turned on, and rows whose upload failed. A bounded number
        per sweep, so turning the bucket on is a backfill over hours rather
        than one burst. As packed - an old zlib row stays zlib."""
        conn = self._conn()
        done = 0
        for key, voice, blob in conn.execute(
                "SELECT key, voice, pcm FROM episode_audio"
                " WHERE object_key = '' AND length(pcm) > 0 LIMIT ?",
                (int(limit),)).fetchall():
            name = audio_store.object_name(audio_store.RECENT, secrets.token_hex(16))
            try:
                store.put(name, bytes(blob))
            except audio_store.AudioStoreError as exc:
                log.warning("audio store: %s; backfill resumes next sweep", exc)
                break
            cur = conn.execute(
                "UPDATE episode_audio SET object_key = ?"
                " WHERE key = ? AND voice = ? AND object_key = ''", (name, key, voice))
            if not cur.rowcount:  # dropped or re-kept while uploading
                conn.execute("INSERT OR IGNORE INTO audio_deletes (name, at)"
                             " VALUES (?, ?)", (name, time.time()))
            done += bool(cur.rowcount)
        return done

    def drain_deletes(self, limit: int = 500) -> int:
        """Delete queued objects from the bucket. Deleting is free on R2 and
        idempotent, so a name is only dequeued once its delete answered."""
        store = audio_store.get_store()
        if store is None:
            return 0
        done = 0
        conn = self._conn()
        for (name,) in conn.execute(
                "SELECT name FROM audio_deletes ORDER BY at LIMIT ?",
                (int(limit),)).fetchall():
            try:
                store.delete(name)
            except audio_store.AudioStoreError as exc:
                log.warning("audio store: %s; delete retried next sweep", exc)
                break
            conn.execute("DELETE FROM audio_deletes WHERE name = ?", (name,))
            done += 1
        return done

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
                # Current only (§143): a near match stands in for a hit, and
                # a hit is served only a current script.
                "SELECT key, query, vector FROM scripts"
                f" WHERE bucket = ? AND {_CURRENT_UNTIL} >= ? AND vector IS NOT NULL"
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
            return content_filter.scrub(row[0] or "")
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
            return content_filter.scrub(row[0] or "")
        except Exception:
            log.exception("script cache title read failed")
            return ""

    def category(self, key: str) -> str:
        """The writer's `<<CATEGORY:>>` words (§189), or "" when it has none."""
        try:
            row = self._conn().execute(
                "SELECT category, expires FROM scripts WHERE key = ?", (key,)
            ).fetchone()
            if not row or row[1] < time.time():
                return ""
            return row[0] or ""
        except Exception:
            log.exception("script cache category read failed")
            return ""

    def summary(self, key: str) -> str:
        """The episode's one-sentence summary, or "" when it has none."""
        try:
            row = self._conn().execute(
                "SELECT summary, expires FROM scripts WHERE key = ?", (key,)
            ).fetchone()
            if not row or row[1] < time.time():
                return ""
            return content_filter.scrub(row[0] or "")
        except Exception:
            log.exception("script cache summary read failed")
            return ""

    def explicit(self, key: str) -> bool:
        """Whether the kept episode swears (§171) - the E on its title.

        Read off the sentences it would play rather than stored beside them,
        so a row written before the mark existed answers too. Counts no hit:
        the player asks this every couple of seconds while an episode plays.
        """
        try:
            row = self._conn().execute(
                "SELECT sentences, expires FROM scripts WHERE key = ?", (key,)
            ).fetchone()
        except Exception:
            log.exception("script cache explicit read failed")
            return False
        if not row or row[1] < time.time():
            return False
        return _explicit_json(row[0])

    def voice_of(self, key: str) -> str:
        """The voice this episode is spoken in when nobody chose one (§147)."""
        try:
            row = self._conn().execute(
                "SELECT voice FROM scripts WHERE key = ? AND expires >= ?",
                (key, time.time())).fetchone()
        except Exception:
            log.exception("script cache voice read failed")
            return ""
        return (row[0] or "") if row else ""

    def set_voice(self, key: str, voice: str) -> str:
        """Give a kept episode a voice if it has none, and return the one it
        has. First one wins, so two listeners tapping one browse tile at once
        hear - and keep audio for - the same voice (§147)."""
        if not key or not voice:
            return self.voice_of(key)
        try:
            self._conn().execute(
                "UPDATE scripts SET voice = ? WHERE key = ? AND voice = ''",
                (voice[:80], key))
        except Exception:
            log.exception("script cache voice write failed")
        return self.voice_of(key)

    def origin_of(self, key: str) -> str:
        try:
            row = self._conn().execute(
                "SELECT origin FROM scripts WHERE key = ?", (key,)).fetchone()
        except Exception:
            return ""
        return (row[0] or "") if row else ""

    def recent(self, limit: int = 40, exclude_author: str = "",
               origin: str = "") -> list[dict]:
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

        `origin` keeps only entries one surface wrote. Explore passes
        "search" (§147): its feed is what other people *searched*, never a
        myFAM tile, a DailyFAM edition or a warmed guess.
        """
        now = time.time()
        try:
            rows = self._conn().execute(
                "SELECT key, query, minutes, created, plays, thread, title,"
                f" author, {_SOURCED}, {_CURRENT_UNTIL}, origin, voice,"
                " sentences FROM scripts"
                " WHERE expires >= ? AND query != '' AND minutes > 0"
                f"   AND origin != '{ARCHIVE_ORIGIN}'"
                "   AND (? = '' OR author != ?)"
                "   AND (? = '' OR origin = ?)"
                " ORDER BY created DESC LIMIT ?",
                (now, exclude_author or "", exclude_author or "",
                 origin or "", origin or "", int(limit)),
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
             "plays": r[4], "thread": content_filter.scrub(r[5] or ""),
             "title": content_filter.scrub(r[6] or ""),
             "author": r[7] or "",
             # Whether it swears (§171), read off the script it would play,
             # so every card naming it can draw the E.
             "explicit": _explicit_json(r[12]),
             # §143: when its information was sourced, and whether a new
             # request would still be served it. Replay surfaces show the
             # first; nothing here hides an entry that is kept but not current.
             "sourced_at": float(r[8] or 0.0), "current": float(r[9]) >= now,
             "origin": r[10] or "", "voice": r[11] or ""}
            for r in rows
        ]

    def authored_by(self, authors, since: float = 0.0,
                    limit: int = 200) -> list[dict]:
        """Live entries first generated by any of `authors`, newest first.

        The read behind the "created" half of myFAM's friends rail: an episode
        a friend made is one they chose to hear, whether or not the log still
        has their play of it. `author` is provenance and never identity (see
        `recent`), and this reads it for the same one display decision - the
        ids go no further than `topics.rank_friends`, which returns tiles.
        """
        wanted = sorted({a for a in authors if a})
        if not wanted:
            return []
        marks = ",".join("?" for _ in wanted)
        try:
            rows = self._conn().execute(
                "SELECT key, query, minutes, created, title, author,"
                f" {_SOURCED} FROM scripts"
                " WHERE expires >= ? AND query != '' AND minutes > 0"
                f"   AND origin != '{ARCHIVE_ORIGIN}'"
                f"   AND author IN ({marks}) AND created >= ?"
                " ORDER BY created DESC LIMIT ?",
                (time.time(), *wanted, float(since), int(limit)),
            ).fetchall()
        except Exception:
            log.exception("could not read scripts by author")
            return []
        return [{"key": r[0], "query": r[1], "minutes": r[2], "created": r[3],
                 "title": r[4] or "", "author": r[5] or "",
                 "sourced_at": float(r[6] or 0.0)} for r in rows]

    def purge_expired(self) -> int:
        try:
            now = time.time()
            # Never the script of kept audio (§237): the sweep pins those,
            # and a deployment that stopped sweeping for a while must not
            # lose something a listener saved because its clock ran out.
            cur = self._conn().execute(
                "DELETE FROM scripts WHERE expires < ? AND key NOT IN"
                " (SELECT key FROM episode_audio WHERE tier = ?)", (now, KEPT_TIER))
            # Audio has no clock of its own; it goes when its script does.
            self._forget_audio(
                "key NOT IN (SELECT key FROM scripts WHERE expires >= ?)"
                " AND tier != ?", (now, KEPT_TIER))
            return cur.rowcount or 0
        except Exception:
            return 0

    def anonymise_author(self, author: str) -> int:
        """Clear one listener's id from every script they wrote first (§208).

        Account deletion's half of `author`. The episodes stay - other people
        are listening to them, and `forget_author` below explains why a
        listener leaving must not un-write them - but the id was the one field
        in the shared cache that pointed at a person, and it outlived the
        account. Cleared, a script reads like one written before the column
        existed: unattributed. Archived rows (§173) share the table and are
        cleared with it. Returns how many rows changed; -1 is never returned,
        a failure is logged and counts 0.
        """
        if not author:
            return 0
        try:
            cur = self._conn().execute(
                "UPDATE scripts SET author = '' WHERE author = ?", (author,))
            return cur.rowcount or 0
        except Exception:
            log.exception("could not clear authorship for %r", author)
            return 0

    def forget_author(self, author: str) -> int:
        """Drop every script one listener wrote first. For clearing seed data.

        **Not part of account deletion, and it must not become part of it.**
        `author` is provenance, so that Explore can leave a listener's own
        episodes off their own feed - and a listener leaving does not un-write
        the episodes other people are listening to. Account deletion clears
        the id instead (`anonymise_author`, §208).

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
            self._forget_audio(
                "key IN (SELECT key FROM scripts WHERE author = ?)", (author,))
            cur = self._conn().execute(
                "DELETE FROM scripts WHERE author = ?", (author,))
            return cur.rowcount or 0
        except Exception:
            log.exception("could not drop scripts authored by %r", author)
            return 0

    # --- Replaying kept episodes on staging (§172) -------------------------
    #
    # Staging spends nothing, so it cannot write a real episode. What it can do
    # is replay one: an episode production already wrote and voiced plays on
    # staging for nothing, exactly as it played the first time. These three
    # move an episode from one deployment's cache to another's, whole, with
    # the one field that points at a person - `author` - left behind.
    # Column lists are read from the table, not typed here, so a column added
    # next month travels too.

    #: Never exported: who wrote it first is provenance about a listener.
    PRIVATE_COLUMNS = ("author",)

    def export_keys(self, limit: int = 50) -> list[dict]:
        """The most-played episodes still kept, with whether each has audio."""
        rows = self._conn().execute(
            "SELECT s.key, s.title, s.query, s.plays,"
            " EXISTS(SELECT 1 FROM episode_audio a WHERE a.key = s.key)"
            " FROM scripts s WHERE s.expires >= ?"
            " ORDER BY s.plays DESC, s.created DESC LIMIT ?",
            (time.time(), max(1, int(limit)))).fetchall()
        return [{"key": r[0], "title": r[1] or r[2] or "", "plays": r[3],
                 "audio": bool(r[4])} for r in rows]

    def export_episode(self, key: str) -> Optional[dict]:
        """One kept episode - its script row and every voiced copy - as JSON-safe
        data. None when there is no such episode."""
        conn = self._conn()
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM scripts WHERE key = ?", (key,)).fetchone()
            if row is None:
                return None
            audio = conn.execute("SELECT * FROM episode_audio WHERE key = ?", (key,)).fetchall()
        finally:
            conn.row_factory = None
        voiced = []
        for a in audio:
            copy = {k: a[k] for k in a.keys()}
            # Bytes travel, never a bucket name (§237): the importing
            # deployment has its own bucket, or none (staging).
            if not copy.get("pcm"):
                copy["pcm"] = self._fetch_object(copy.get("object_key") or "")
            if not copy["pcm"]:
                continue
            copy["object_key"], copy["tier"] = "", ""
            voiced.append({k: _portable(v) for k, v in copy.items()})
        return {"script": {k: _portable(row[k]) for k in row.keys()
                           if k not in self.PRIVATE_COLUMNS},
                "audio": voiced}

    def import_episode(self, data: dict, keep_until: float) -> str:
        """Write an exported episode into this cache. Kept until at least
        `keep_until` so a replay set does not evaporate a week after import;
        `sourced_at` and `fresh_until` travel unchanged, so an old answer is
        never served as a current one. Returns the key."""
        conn = self._conn()
        script = dict(data["script"])
        for private in self.PRIVATE_COLUMNS:
            script.pop(private, None)
        script["expires"] = max(float(script.get("expires") or 0), keep_until)
        columns = {r[1] for r in conn.execute("PRAGMA table_info(scripts)")}
        names = [k for k in script if k in columns]
        conn.execute(
            f"INSERT OR REPLACE INTO scripts ({', '.join(names)})"
            f" VALUES ({', '.join('?' for _ in names)})",
            [_restored(script[k]) for k in names])
        audio_columns = {r[1] for r in conn.execute("PRAGMA table_info(episode_audio)")}
        for audio in data.get("audio", []):
            audio = {**audio, "object_key": "", "tier": ""}
            if not audio.get("pcm"):
                continue
            names = [k for k in audio if k in audio_columns]
            conn.execute(
                f"INSERT OR REPLACE INTO episode_audio ({', '.join(names)})"
                f" VALUES ({', '.join('?' for _ in names)})",
                [_restored(audio[k]) for k in names])
        return script["key"]

    def clear(self) -> int:
        """Empty the whole cache. Every entry, expired or not.

        The one lever that makes "start seeing only new episode titles" true
        on a deployment nobody can shell into. It costs one regeneration per
        question anybody asks again, and nothing else: the audio beside each
        script (§132) goes with it, and every entry is reproducible.
        """
        try:
            self._forget_audio("1 = 1", ())
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


def _portable(value):
    """A column value as JSON: bytes (zlib PCM, a vector) as tagged base64."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        import base64
        return {"b64": base64.b64encode(bytes(value)).decode("ascii")}
    return value


def _restored(value):
    if isinstance(value, dict) and set(value) == {"b64"}:
        import base64
        return base64.b64decode(value["b64"])
    return value
