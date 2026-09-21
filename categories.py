"""The category tree: a vocabulary that grows itself.

## The ceiling this removes

`topics.py` has always reasoned in a **hand-written** vocabulary: eight
facets, twenty-nine subtags under them, and a keyword list per entry written
by somebody. That is two levels deep and thirty-seven words wide, and every
one of them had to be typed into a Python file.

It is also demonstrably the binding constraint rather than the scoring. Three
separate mechanisms exist in `topics.py` purely to work around things the
vocabulary cannot say:

* `SUBTAG_WEIGHT`, because matching `sports` and matching `sports-drama`
  counted the same;
* `BROAD_MATCH_PENALTY`, because there is no `nfl` tag and no
  `college-football` tag - both are `sports`, and no weighting distinguishes
  them; and
* `familiar_words`, which reads a listener's own searches precisely because
  *the tags cannot express what they were interested in*.

The third one is the tell. When the fix for "recommend me better" is to stop
using the vocabulary and read the raw text instead, the vocabulary is what
needs fixing.

## What this holds

One table, and it is a **tree with no depth limit**:

    categories(id, label, parent_id, depth, source, first_seen, last_seen,
               uses, listeners, degraded)

`sports -> american football -> nfl -> cincinnati bengals` is four levels and
nothing here cares that it is four rather than two. A node's `depth` is its
parent's plus one, computed when it is minted, and `topics._affinity` reads it
directly: a deeper match is a more specific claim about an episode and is
worth more. `SUBTAG_WEIGHT` becomes the special case of that at depth 1.

## Where the nodes come from

**Nothing is typed into this file.** Three sources, all of them things the app
already collects:

* **What people searched for.** `events.text` for every behavioural kind,
  which is the only record in FAM of what somebody was interested in rather
  than which of eight headings it lived under.
* **What the live story pool is about.** A story already has a resolved
  subject, composed once per refresh window for the whole deployment.
* **What listeners typed into the interests catalogue.** `preferences.topics`
  holds free text precisely because seventy-three strings somebody wrote down
  is not the set of things a person can be interested in.

A phrase becomes a node when `MIN_LISTENERS` different people have used it, or
when it arrives from a story (which is already a statement that a subject is
being widely reported). That threshold is the whole of the spam control: one
person with an unusual interest gets it served by `familiar_words` and does
not get to add a word to everybody's vocabulary.

## How a node gets a parent

Two paths, and the keyless one always runs first, so this layer can add
resolution and **can never subtract availability** - the rule
`episode_intelligence` is built on.

* **Keyless.** A phrase's parent is the facet its own sightings were tagged
  with most often - "cincinnati bengals" turns up in queries tagged `sports`,
  so it lands under `sports`. Then containment deepens it: a phrase whose
  words are a strict superset of an existing node's, under the same facet, is
  that node's child. `bengals` before `cincinnati bengals` gives
  `sports -> bengals -> cincinnati bengals`.
* **With a model.** One call per sweep, for the whole deployment, batching
  every new phrase at once - the same economics `stories.py` runs on. It
  returns a *path* rather than a parent, so the levels nobody typed
  ("American Football", "NFL") are minted as intermediate nodes. This is the
  only way those exist, because no amount of reading what people typed
  invents a level nobody wrote.

Either way `degraded` records which answered, for the same reason
`Brief.degraded` does: a layer that quietly stopped working looks identical
from outside to one that is working.

## Two rules that are load-bearing

**Nothing here may call a model on a read path.** Minting happens in the
background sweep that already exists for stories; `match()` is a word-set
intersection against an in-process index and is the only thing the feed ever
calls.

**A node never rewrites history.** Events keep the tags they were written
with. What a new node *does* change is how those events are read: `taste`
re-matches an event's own text against the current tree, so a listener's
existing history becomes legible in the new vocabulary the moment it exists.
That asymmetry is deliberate - the stored tags are what the ranker believed
at the time and are worth keeping, and the text is what the listener actually
said and is worth re-reading.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Iterable, Optional

from paths import data_path

log = logging.getLogger(__name__)

#: How many different listeners have to have used a phrase before it becomes
#: part of everybody's vocabulary.
#:
#: Three, and the number is doing real work: one is anybody's passing
#: curiosity and two is a coincidence between two people. Below this a
#: listener's own interest is still served - `familiar_words` reads their raw
#: searches, which is exactly the mechanism for something only they care
#: about - it simply does not get to widen the ranking vocabulary for
#: everyone else.
MIN_LISTENERS = 3

#: How many *differently worded* questions a phrase has to turn up in.
#:
#: The listener threshold alone is not enough, and the first tree built
#: without this said so loudly: eight seeded queries produced thirty-nine
#: nodes, of which "reserve interest rate", "league title" and "rate decision"
#: were typical. Every sub-span of a phrase is seen by exactly the people who
#: saw the phrase, so a four-word run clears a listener threshold ten times
#: over and mints ten nodes, nine of them fragments.
#:
#: What separates a subject from a fragment is that people ask about a subject
#: in more than one way. "federal reserve" turns up in "what the fed said
#: about inflation" and "the federal reserve rate decision"; "reserve interest
#: rate" turns up only inside one phrasing, because it is a piece of a
#: sentence rather than a thing anybody is interested in.
#:
#: Two, not more: this is meant to catch a fragment, not to make a subject
#: prove itself. A phrase with three different listeners and two different
#: phrasings behind it is a subject.
MIN_TEXTS = 2

#: The most nodes the tree may hold. A vocabulary with no ceiling on the
#: *number of subjects* still needs a ceiling on the size of the file, and
#: this is high enough that nothing an honest deployment produces meets it.
#: When it is reached the least-used nodes are the ones that stop being
#: refreshed - see `prune`.
MAX_NODES = 4000

#: The most new nodes one sweep may mint. Not a cost control - minting is a
#: fraction of one model call - but a blast radius: a bad extraction run that
#: promoted two thousand phrases at once would be very hard to undo, and this
#: turns it into something that shows up in a report first.
MAX_NEW_PER_SWEEP = 40

#: How long a node survives with nothing matching it. A subject that stopped
#: being talked about should stop taking up room in the vocabulary, and the
#: clock is `last_seen` rather than membership - the §103 rule, that an
#: eviction which forgets is a creation.
NODE_TTL = 180 * 86400

#: The longest a phrase may be, in words. A subject, never a sentence: past
#: this an n-gram is a fragment of a question rather than a thing anybody is
#: interested in.
MAX_PHRASE_WORDS = 4

#: The shortest a word has to be to appear in a phrase.
MIN_WORD = 3

#: How often the growth sweep may run. A module constant rather than a
#: setting, deliberately: a vocabulary is not something a deployment needs to
#: tune the cadence of, and every env var is a line in four files and one more
#: thing that can be set two ways. An hour is far faster than the vocabulary
#: actually moves - `MIN_LISTENERS` different people have to have used a
#: phrase before anything changes - and slow enough that it is one model call
#: an hour at the very most, for the whole deployment.
SWEEP_INTERVAL = 3600.0

#: When the last sweep started, in this process. In memory like
#: `stories._LAST_SWEPT`, and for the same reason: a second worker sweeping an
#: hour later costs one extra model call and mints nothing new, because `mint`
#: is idempotent on the phrase.
_LAST_SWEEP = 0.0


def is_stale(now: Optional[float] = None) -> bool:
    """Whether the vocabulary is due a look. Cheap, and safe to ask often."""
    now = time.time() if now is None else now
    return now - _LAST_SWEEP >= SWEEP_INTERVAL


def reset_sweep() -> None:
    """Forget when the last sweep ran. For tests."""
    global _LAST_SWEEP
    _LAST_SWEEP = 0.0


#: Where a node came from, for the report and for nothing else.
SOURCE_SEARCH = "search"
SOURCE_STORY = "story"
SOURCE_INTEREST = "interest"
SOURCE_MODEL = "model"

_WORD = re.compile(r"[a-z0-9]+")


def _facet_slugs() -> frozenset[str]:
    """The eight pickable facets. Lazily, for the import-order reason above."""
    import topics
    return frozenset(topics.TAG_LABELS)


def _reserved_slugs() -> frozenset[str]:
    """Every name the hand-written vocabulary already uses.

    Wider than the facets, and the difference was found in the first real
    tree: `chips` is a *subtag* slug, and it is also a phrase six listeners
    typed. Minting it would put two entries under one key in the profile
    `taste` builds - the subtag's, scored by `tags_for_text`, and the
    category's, scored by the tree - and an episode about chips would then
    count as two different things being relevant to somebody.

    Normalised, because a subtag slug is hyphenated (`sports-drama`) and a
    phrase never is: `normalise` turns the first into "sports drama", which is
    exactly the phrase that would collide with it.
    """
    import topics
    return frozenset(normalise(tag) for tag in topics.TAG_WORDS)


def normalise(phrase: str) -> str:
    """One phrase, as the tree stores it: lower case, words only, collapsed.

    Deliberately the same crudeness `topics.tags_for_text` has. No stemming,
    no lemmatisation, no embedding - a phrase is a bag of words and two
    phrases are the same when their words are. What that buys is that
    matching is a set operation and costs microseconds on a browse path,
    which is the constraint everything here is written under.
    """
    return " ".join(_WORD.findall(str(phrase or "").lower()))


def words_of(text: str) -> frozenset[str]:
    """The words of a text that can take part in a phrase."""
    return frozenset(w for w in _WORD.findall(str(text or "").lower())
                     if len(w) >= MIN_WORD and not w.isdigit())


@dataclass(frozen=True)
class Node:
    """One category. `id` is the normalised phrase, which is what makes the
    tree idempotent: observing the same subject twice cannot mint it twice."""

    id: str
    label: str
    parent_id: str
    depth: int
    source: str
    first_seen: float
    last_seen: float
    uses: int
    listeners: int
    degraded: bool = False

    @property
    def words(self) -> frozenset[str]:
        return frozenset(self.id.split())

    def as_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "parent": self.parent_id,
                "depth": self.depth, "source": self.source,
                "uses": self.uses, "listeners": self.listeners,
                "first_seen": self.first_seen, "degraded": self.degraded}


class EmptyTree:
    """A tree with nothing in it, for when there cannot be one.

    A real object rather than `None` or a half-built `CategoryStore`, because
    every caller in `topics.py` is on a read path and the alternative is a
    `None` check at each of them - which is four places to forget, on the
    paths where forgetting takes the browse page down.

    What it buys is exact: with this in place, a deployment whose category
    database cannot be opened ranks on the hand-written eight facets and
    twenty-nine subtags, which is what every deployment ranked on before this
    module existed. Worse, and visibly so on `/api/health`; never broken.
    """

    path = ""

    def nodes(self) -> dict:
        return {}

    def get(self, node_id: str) -> None:
        return None

    def ancestors(self, node_id: str) -> list:
        return []

    def match(self, text: str) -> tuple:
        return ()

    def depth_of(self, node_id: str) -> int:
        return 0

    def report(self) -> dict:
        return {"path": "", "nodes": 0, "max_depth": 0, "by_depth": {},
                "by_source": {}, "degraded": 0, "full": False,
                "unavailable": True}


class CategoryStore:
    """The tree, on disk, with an in-process index for matching.

    The index is the whole reason this is a class rather than a few
    functions. `match()` is called for every tile on a browse page and for
    every event in a taste read, so it cannot be a scan: it is a word ->
    nodes map, rebuilt when the table changes and never per call.
    """

    def __init__(self, path: str | None = None) -> None:
        self.path = data_path("CATEGORIES_DB", "categories.db", path)
        self._local = threading.local()
        self._index: dict[str, set[str]] = {}
        self._nodes: dict[str, Node] = {}
        self._loaded_at = 0.0
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS categories (
                       id         TEXT PRIMARY KEY,
                       label      TEXT NOT NULL,
                       parent_id  TEXT NOT NULL DEFAULT '',
                       depth      INTEGER NOT NULL DEFAULT 0,
                       source     TEXT NOT NULL DEFAULT '',
                       first_seen REAL NOT NULL,
                       last_seen  REAL NOT NULL,
                       uses       INTEGER NOT NULL DEFAULT 0,
                       listeners  INTEGER NOT NULL DEFAULT 0,
                       degraded   INTEGER NOT NULL DEFAULT 0
                   )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS categories_parent"
                         " ON categories(parent_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS categories_seen"
                         " ON categories(last_seen)")
        self.reload()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    # --- reading ----------------------------------------------------------

    def reload(self) -> int:
        """Rebuild the in-process index from the table.

        Called after any write and once at start-up. Never on a read: a feed
        that rebuilt an index per page would be paying for the vocabulary on
        exactly the path this whole module is arranged to keep cheap.
        """
        try:
            rows = self._conn().execute(
                "SELECT id, label, parent_id, depth, source, first_seen,"
                " last_seen, uses, listeners, degraded FROM categories"
            ).fetchall()
        except Exception:
            log.exception("could not read the category tree")
            return len(self._nodes)
        nodes = {r[0]: Node(r[0], r[1], r[2], int(r[3]), r[4], float(r[5]),
                            float(r[6]), int(r[7]), int(r[8]), bool(r[9]))
                 for r in rows}
        index: dict[str, set[str]] = {}
        for node in nodes.values():
            # Indexed on the phrase's *rarest-looking* word would be cleverer
            # and is not worth it: a phrase is at most four words, so this
            # adds at most four entries and lets `match` start from the
            # smallest candidate set it can find.
            for word in node.words:
                index.setdefault(word, set()).add(node.id)
        self._nodes, self._index = nodes, index
        self._loaded_at = time.time()
        return len(nodes)

    def nodes(self) -> dict[str, Node]:
        return dict(self._nodes)

    def get(self, node_id: str) -> Optional[Node]:
        return self._nodes.get(node_id)

    def ancestors(self, node_id: str) -> list[str]:
        """Every node above this one, nearest first. Cycle-safe.

        Cycle-safe rather than assumed acyclic, because a parent can be
        reassigned by a model call and a model that returned "a is under b"
        and "b is under a" in one batch would otherwise hang a browse page.
        A guard here is cheaper than trusting a batch.
        """
        out: list[str] = []
        seen = {node_id}
        current = self._nodes.get(node_id)
        while current is not None and current.parent_id:
            if current.parent_id in seen:
                log.warning("cycle in the category tree at %r", current.parent_id)
                break
            out.append(current.parent_id)
            seen.add(current.parent_id)
            current = self._nodes.get(current.parent_id)
        return out

    def match(self, text: str) -> tuple[str, ...]:
        """Every category this text is about, each with its ancestors.

        **A node always carries its ancestors**, which is the rule
        `tags_for_text` already keeps for subtags: matching "cincinnati
        bengals" is also a match on the NFL, on American football and on
        sport, because it is all four of those things. Without that a listener
        whose history is specific would stop matching the general tiles
        entirely, which is the opposite of what more resolution is for.

        A word-set subset test, not a substring one. "bengals cincinnati"
        matches the same node as "cincinnati bengals", and "the bengals game"
        matches `bengals` - which is right, and is the same bluntness
        `tags_for_text` has had since it was written.
        """
        if not self._index:
            return ()
        text_words = words_of(text)
        if not text_words:
            return ()
        candidates: set[str] = set()
        for word in text_words:
            candidates |= self._index.get(word, set())
        hit: set[str] = set()
        for node_id in candidates:
            node = self._nodes.get(node_id)
            if node is not None and node.words <= text_words:
                hit.add(node_id)
                hit.update(self.ancestors(node_id))
        return tuple(sorted(hit))

    def depth_of(self, node_id: str) -> int:
        node = self._nodes.get(node_id)
        return node.depth if node else 0

    # --- writing ----------------------------------------------------------

    def mint(self, phrase: str, parent_id: str = "", source: str = "",
             listeners: int = 0, uses: int = 0, at: float = 0.0,
             degraded: bool = False) -> Optional[Node]:
        """Add a node, or refresh one that exists. Returns it, or None.

        Idempotent on the normalised phrase, which is what lets every source
        observe freely without anybody having to check first. Refreshing an
        existing node moves `last_seen` and adds to its counts and
        **deliberately does not touch its parent**: a parent is the expensive
        half and, once a model has placed something under "American
        Football", a later keyless sighting must not drag it back up to
        `sports`. `reparent` is the one way a parent changes.
        """
        phrase = normalise(phrase)
        now = at or time.time()
        if not phrase or len(phrase.split()) > MAX_PHRASE_WORDS:
            return None
        # The hand-written vocabulary owns its own names. The eight facets
        # are the roots of this tree and are not rows in it, and a subtag's
        # slug is a name `tags_for_text` already scores - see
        # `_reserved_slugs` for what minting either would do to `taste`.
        if phrase in _reserved_slugs():
            return None
        if any(len(w) < MIN_WORD or w.isdigit() for w in phrase.split()):
            # A phrase that is partly a number is nearly always a fragment of
            # a question - "week 5", "2026 draft" - and it ages badly in a
            # way a subject does not: next season the node is still there and
            # still matching.
            return None
        existing = self._nodes.get(phrase)
        depth = 0 if not parent_id else self.depth_of(parent_id) + 1
        try:
            if existing:
                self._conn().execute(
                    "UPDATE categories SET last_seen = ?, uses = uses + ?,"
                    " listeners = MAX(listeners, ?) WHERE id = ?",
                    (now, int(uses), int(listeners), phrase))
            else:
                if len(self._nodes) >= MAX_NODES:
                    log.warning("category tree is full at %d nodes", MAX_NODES)
                    return None
                self._conn().execute(
                    "INSERT INTO categories (id, label, parent_id, depth,"
                    " source, first_seen, last_seen, uses, listeners, degraded)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (phrase, phrase, parent_id, depth, source, now, now,
                     int(uses), int(listeners), int(degraded)))
        except Exception:
            log.exception("could not mint %r", phrase)
            return None
        self.reload()
        return self._nodes.get(phrase)

    def reparent(self, node_id: str, parent_id: str) -> bool:
        """Move one node under another, and its descendants with it.

        Depth is stored rather than derived on read, because `_affinity` asks
        for it once per tile per feed and a walk to the root per ask is a
        tree traversal on a browse path. The cost is this function: a stored
        depth has to be maintained, and a node moved without its children
        moving is a tree that lies about how specific its leaves are.

        Refuses a cycle rather than creating one. A model returning "a is
        under b" for a b that is already under a is a perfectly ordinary
        thing for a model to do, and it is not worth a hung page.
        """
        node = self._nodes.get(node_id)
        if node is None or node.parent_id == parent_id:
            return False
        if parent_id and (parent_id == node_id
                          or node_id in self.ancestors(parent_id)):
            log.warning("refusing to put %r under its own descendant %r",
                        node_id, parent_id)
            return False
        depth = 0 if not parent_id else self.depth_of(parent_id) + 1
        shift = depth - node.depth
        try:
            self._conn().execute(
                "UPDATE categories SET parent_id = ?, depth = ? WHERE id = ?",
                (parent_id, depth, node_id))
            if shift:
                for child in self._descendants(node_id):
                    self._conn().execute(
                        "UPDATE categories SET depth = depth + ? WHERE id = ?",
                        (shift, child))
        except Exception:
            log.exception("could not reparent %r", node_id)
            return False
        self.reload()
        return True

    def _descendants(self, node_id: str) -> list[str]:
        """Every node below this one. Breadth-first and cycle-safe."""
        out: list[str] = []
        frontier = [node_id]
        seen = {node_id}
        children: dict[str, list[str]] = {}
        for node in self._nodes.values():
            children.setdefault(node.parent_id, []).append(node.id)
        while frontier:
            current = frontier.pop()
            for child in children.get(current, ()):
                if child in seen:
                    continue
                seen.add(child)
                out.append(child)
                frontier.append(child)
        return out

    def prune(self, now: Optional[float] = None) -> int:
        """Drop nodes nothing has matched for `NODE_TTL`.

        A vocabulary that only ever grows is one that ends up ranking on what
        people were interested in two years ago. Dropping a node loses no
        history - events keep the tags they were written with - and any
        phrase that comes back is minted again by the same sweep that minted
        it the first time.

        A node with children is kept whatever its own age: it is holding a
        level of the tree up, and removing it would orphan everything below
        it into a flat list.
        """
        now = time.time() if now is None else now
        parents = {n.parent_id for n in self._nodes.values() if n.parent_id}
        stale = [n.id for n in self._nodes.values()
                 if now - n.last_seen > NODE_TTL and n.id not in parents]
        if not stale:
            return 0
        try:
            self._conn().executemany(
                "DELETE FROM categories WHERE id = ?", [(i,) for i in stale])
        except Exception:
            log.exception("could not prune the category tree")
            return 0
        self.reload()
        return len(stale)

    def report(self) -> dict:
        """What the tree currently holds. For a report, never for ranking."""
        by_depth: dict[int, int] = {}
        by_source: dict[str, int] = {}
        for node in self._nodes.values():
            by_depth[node.depth] = by_depth.get(node.depth, 0) + 1
            by_source[node.source] = by_source.get(node.source, 0) + 1
        return {
            "path": self.path,
            "nodes": len(self._nodes),
            "max_depth": max(by_depth) if by_depth else 0,
            "by_depth": dict(sorted(by_depth.items())),
            "by_source": dict(sorted(by_source.items())),
            "degraded": sum(1 for n in self._nodes.values() if n.degraded),
            "full": len(self._nodes) >= MAX_NODES,
        }


# --------------------------------------------------------------------------
# Growth
# --------------------------------------------------------------------------
#
# Everything below reads what the app already collects and writes nodes. It
# runs in the background sweep beside the story refresh, never on a read.

#: Words that break a phrase, on top of the ones `familiar_words` already
#: filters.
#:
#: The two lists are doing different jobs and the difference showed up
#: immediately. `familiar_words` asks *has this listener ever said this word*,
#: where "night" is a perfectly good signal and costs nothing. This asks *is
#: this a subject worth being a category*, and "last night" is not - it is
#: when something happened, it appears in thousands of questions, and three
#: people asking about three unrelated things last night would have minted it
#: as a branch of the vocabulary.
#:
#: **Extended rather than copied.** `_stopwords()` unions this with
#: `topics.FAMILIAR_STOPWORDS`, so the two cannot disagree about the shared
#: half the first time somebody adds a word to one of them.
PHRASE_STOPWORDS = frozenset("""
actually again ago already always another anybody anyone anything back begin
beginning behind better biggest change changed changes coming currently day
days deal doing else end ending essentially even ever every everybody
everyone everything explain explained explaining fully further future general
generally get getting give given go goes going got happen happened happening
happens help history hour hours know known last late later latest less let
long look looking lot main mainly major many matter matters mean means might
moment month months morning much need needs never news next night nights
nobody nothing now number often old once ongoing overall part parts past
people perhaps place put quick quickly rather real really reason reasons
recent recently right said say saying says second see seem seems several
short show shown side simple simply since situation something sometimes soon
sort start started starting state still story stories take taken talk talking
tell telling thing things think thinking though thought three time times
today told tomorrow tonight took top total truly turn turned two understand
update updated updates upcoming use used using usually want wanted week weeks
well whole work working world worth would year years yesterday yet
""".split())


def _stopwords() -> frozenset[str]:
    """Both lists, unioned. Lazily, because `topics` imports this module."""
    import topics
    return topics.FAMILIAR_STOPWORDS | PHRASE_STOPWORDS


def phrases_in(text: str) -> list[str]:
    """Candidate subjects in one piece of text, longest first.

    Runs of non-stopwords, up to `MAX_PHRASE_WORDS` long, plus every shorter
    run inside them. "what happened with the cincinnati bengals last night"
    yields `cincinnati bengals`, `cincinnati` and `bengals` - the stopwords
    break the run, so "with the" and "last night" never become subjects.

    Longest first because that is the order `promote` wants: a longer phrase
    is a more specific claim, and when both survive the threshold the shorter
    one becomes the parent.
    """
    stop = _stopwords()
    runs: list[list[str]] = [[]]
    for word in _WORD.findall(str(text or "").lower()):
        if len(word) < MIN_WORD or word.isdigit() or word in stop:
            runs.append([])
        else:
            runs[-1].append(word)
    out: list[str] = []
    for run in runs:
        for size in range(min(len(run), MAX_PHRASE_WORDS), 0, -1):
            for start in range(len(run) - size + 1):
                out.append(" ".join(run[start:start + size]))
    # De-duplicated keeping first sight, so the longest form of a repeated
    # phrase is the one that survives the ordering.
    seen: set[str] = set()
    return [p for p in out if not (p in seen or seen.add(p))]


def candidates(texts: Iterable[tuple[str, str]]
               ) -> dict[str, tuple[set[str], set[str]]]:
    """`{phrase: ({listener, ...}, {wording, ...})}`.

    Two sets rather than two counts, because both thresholds are about
    *distinctness*. One person searching the same thing forty times must not
    clear `MIN_LISTENERS`, and forty repeats of one sentence must not clear
    `MIN_TEXTS` - a count would let either through.

    The wording is the normalised whole text, so "What happened with the
    Bengals?" and "what happened with the bengals" are one wording, which is
    what they are.
    """
    out: dict[str, tuple[set[str], set[str]]] = {}
    for user_id, text in texts:
        wording = normalise(text)
        for phrase in phrases_in(text):
            users, wordings = out.setdefault(phrase, (set(), set()))
            users.add(user_id or "")
            wordings.add(wording)
    return out


def _drop_subsumed(seen: dict[str, tuple[set[str], set[str]]]) -> set[str]:
    """Phrases that never appear except inside a longer one.

    `federal reserve interest` turns up in exactly the questions
    `federal reserve interest rate` turns up in, said by exactly the same
    people - so it is not a second subject, it is the same subject with a
    word missing. Keeping it would put a node in the tree that can never
    match anything its parent does not already match, and would make every
    such episode score as though two different things about it were relevant.

    A phrase survives if it appears *anywhere* its longer form does not. That
    is what keeps "federal reserve" - which people also ask about without
    mentioning rates - and drops the run of fragments around it.
    """
    by_words = {p: frozenset(p.split()) for p in seen}
    dropped: set[str] = set()
    for phrase, words in by_words.items():
        for longer, longer_words in by_words.items():
            if longer is phrase or not (words < longer_words):
                continue
            if seen[phrase] == seen[longer]:
                dropped.add(phrase)
                break
    return dropped


def facet_hint(text: str) -> str:
    """The facet a phrase's own sightings point at, or "".

    The keyless parent. `topics.tags_for_text` already maps free text onto
    the eight facets, and a phrase that keeps turning up in queries about
    sport belongs under sport - which is a worse answer than a model's and a
    much better one than none, and it is the answer this layer degrades to.
    """
    import topics
    facets = topics.facets_only(topics.tags_for_text(text))
    return facets[0] if facets else ""


def _containment_parent(store: "CategoryStore", phrase: str) -> str:
    """The most specific existing node whose words this phrase contains.

    `cincinnati bengals` under `bengals` if `bengals` is already a node. A
    strict subset, so a phrase is never its own parent, and the longest
    candidate wins because it is the most specific true statement available.

    This is the keyless half of the hierarchy and it is honest about what it
    is: it discovers that one subject is a narrowing of another, and it
    cannot invent the level between them. Nothing about reading what people
    typed produces "American Football" when nobody typed it - only the model
    pass does that, and this is what the tree looks like until it runs.
    """
    words = frozenset(phrase.split())
    best, best_len = "", 0
    for node in store.nodes().values():
        if node.words < words and len(node.words) > best_len:
            best, best_len = node.id, len(node.words)
    return best


def promote(store: "CategoryStore", texts: Iterable[tuple[str, str]],
            always: Iterable[str] = (), source: str = SOURCE_SEARCH,
            now: Optional[float] = None, limit: int = MAX_NEW_PER_SWEEP
            ) -> list[Node]:
    """Mint whatever the log now says is a subject. The keyless sweep.

    `texts` is `(listener, text)` - searches, shares, saves, typed interests -
    and a phrase needs `MIN_LISTENERS` different people behind it. `always`
    bypasses that: a live story's subject has already been judged worth
    composing a tile about by the story pool, on evidence from four live
    sources, which is a stronger statement than three listeners typing the
    same words.

    **Shortest first.** A phrase minted now is a candidate parent for a
    longer one minted in the same sweep, so `bengals` has to exist before
    `cincinnati bengals` can be put under it. Sorting the other way produces
    a flat list of orphans that the next sweep cannot repair, because
    `mint` deliberately never moves an existing node's parent.
    """
    now = time.time() if now is None else now
    texts = list(texts)
    seen = candidates(texts)

    # Which facet each phrase's own sightings point at. Collected here rather
    # than in `candidates` because it needs the text, and `candidates` is
    # worth keeping as the one countable thing it is.
    votes: dict[str, dict[str, int]] = {}
    for _user, text in texts:
        facet = facet_hint(text)
        if not facet:
            continue
        for phrase in phrases_in(text):
            votes.setdefault(phrase, {})[facet] = (
                votes.setdefault(phrase, {}).get(facet, 0) + 1)

    redundant = _drop_subsumed(seen)
    eligible = [(p, users) for p, (users, wordings) in seen.items()
                if len(users) >= MIN_LISTENERS and len(wordings) >= MIN_TEXTS
                and p not in redundant and not store.get(p)]
    for phrase in always:
        phrase = normalise(phrase)
        if phrase and not store.get(phrase):
            # A story's subject bypasses both thresholds. It has already been
            # judged worth composing a tile about, from four live sources, by
            # the one layer in this app whose job is deciding what is being
            # widely reported - which is a stronger statement than three
            # people typing similar words.
            eligible.append((phrase, seen.get(phrase, (set(), set()))[0]))

    # Shortest first, then the ones the most people said. De-duplicated,
    # because `always` can name something the texts also cleared.
    ordered, listed = [], set()
    for phrase, users in sorted(eligible,
                                key=lambda pair: (len(pair[0].split()),
                                                  -len(pair[1]), pair[0])):
        if phrase not in listed:
            listed.add(phrase)
            ordered.append((phrase, users))

    minted: list[Node] = []
    for phrase, users in ordered:
        if len(minted) >= limit:
            break
        parent = _containment_parent(store, phrase)
        if not parent:
            facet_votes = votes.get(phrase, {})
            parent = (max(facet_votes, key=lambda f: facet_votes[f])
                      if facet_votes else "")
        node = store.mint(phrase, parent_id=parent, source=source,
                          listeners=len(users), uses=len(users), at=now,
                          degraded=True)
        if node:
            minted.append(node)
    return minted


# --------------------------------------------------------------------------
# Deepening: the levels nobody typed
# --------------------------------------------------------------------------

PLACER_SYSTEM = (
    "You organise subjects into a hierarchy for a podcast recommender. "
    "You are given subjects people actually searched for, and the top-level "
    "categories they must hang under. For each subject, return the full path "
    "from the top-level category down to it, inventing the intermediate "
    "levels that a person would recognise.\n\n"
    "Rules:\n"
    "- The first element of every path is one of the given top-level "
    "categories, exactly as spelled.\n"
    "- The last element is the subject, exactly as given.\n"
    "- Intermediate levels are ordinary lower-case names a listener would "
    "recognise ('american football', 'central banking'), never codes and "
    "never more than four words.\n"
    "- Two to five elements. Do not pad a path to make it deeper; a subject "
    "that really does sit directly under its category gets a path of two.\n"
    "- State nothing about the world. You are naming categories, not "
    "reporting events: no results, no dates, no numbers.\n"
    "- If a subject is not a subject - a fragment, a verb, a piece of a "
    "question - leave it out entirely rather than inventing a home for it."
)

PLACER_SCHEMA = {
    "type": "object",
    "properties": {
        "paths": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "path": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["subject", "path"],
            },
        },
    },
    "required": ["paths"],
}


def build_placer_prompt(subjects: list[str], facets: list[str]) -> str:
    """What the model is asked. Subjects and roots, and nothing else.

    Deliberately carries no listener, no counts and no episode text. This
    call decides *where a word belongs in a vocabulary*, which is a question
    about language rather than about anybody's taste - and a prompt that
    carried who searched for what would make the shared tree a per-listener
    one by the back door.
    """
    lines = ["Top-level categories:"]
    lines += [f"- {facet}" for facet in facets]
    lines.append("")
    lines.append("Subjects to place:")
    lines += [f"- {subject}" for subject in subjects]
    return "\n".join(lines)


def apply_paths(store: "CategoryStore", rows: Iterable[dict],
                now: Optional[float] = None) -> int:
    """Mint the intermediate levels and hang the subjects off them.

    The half that makes this a *tiered* vocabulary rather than a flat one.
    `sports / american football / nfl / cincinnati bengals` arrives as four
    strings; `american football` and `nfl` are minted because nobody ever
    typed them, and the subject is moved under the last of them.

    Separate from the call that produces the rows so it can be tested with no
    key and no network, which is the only way this path gets exercised in
    this repository at all.
    """
    now = time.time() if now is None else now
    facets = _facet_slugs()
    moved = 0
    for row in rows:
        subject = normalise((row or {}).get("subject", ""))
        path = [normalise(p) for p in (row or {}).get("path", ()) or ()]
        path = [p for p in path if p]
        if not subject or len(path) < 2:
            continue
        if store.get(subject) is None:
            # The model answered about something that is not in the tree.
            # Minting it here would let one malformed reply add subjects
            # nobody searched for, which is the one thing the listener
            # threshold exists to prevent.
            continue
        if path[-1] != subject or path[0] not in facets:
            # A path that does not start at a real root or end at the subject
            # it claims to place is not a path. Dropped rather than repaired:
            # guessing which end was wrong is how a tree acquires a branch
            # nobody can explain.
            continue
        parent = path[0]
        for level in path[1:-1]:
            if level in facets or level == subject:
                continue
            node = store.get(level) or store.mint(
                level, parent_id=parent, source=SOURCE_MODEL, at=now)
            if node is None:
                continue
            if node.parent_id != parent and node.source == SOURCE_MODEL:
                store.reparent(level, parent)
            parent = level
        if store.reparent(subject, parent):
            moved += 1
    return moved


async def place(store: "CategoryStore", subjects: list[str],
                now: Optional[float] = None) -> int:
    """Ask a model where new subjects belong, and build the levels it names.

    One call for the batch, once per sweep, for the whole deployment - the
    economics `stories.compose` runs on, and the reason a growing vocabulary
    is affordable at all.

    **Never raises, and every failure leaves the tree exactly as it was.** No
    key, a timeout, a refusal, unreadable JSON, a path that does not start at
    a real root: all of them return 0 and the keyless parents stand. That is
    `episode_intelligence`'s rule - a layer that adds quality must not be able
    to subtract availability - and here the availability in question is the
    whole ranking vocabulary.
    """
    import asyncio
    import json

    import credentials
    from anthropic_client import build_async_client
    from config import settings

    now = time.time() if now is None else now
    subjects = [normalise(s) for s in subjects]
    subjects = [s for s in subjects if s and store.get(s)]
    if not subjects or not settings.categories_place:
        return 0

    facets = sorted(_facet_slugs())
    try:
        client = build_async_client(credentials.active("ANTHROPIC_API_KEY"))
        response = await asyncio.wait_for(
            client.messages.create(
                model=settings.categories_model,
                max_tokens=settings.categories_max_tokens,
                system=PLACER_SYSTEM,
                output_config={
                    "effort": settings.categories_effort,
                    "format": {"type": "json_schema", "schema": PLACER_SCHEMA},
                },
                messages=[{"role": "user",
                           "content": build_placer_prompt(subjects, facets)}],
            ),
            timeout=float(settings.categories_place_timeout_seconds),
        )
    except asyncio.TimeoutError:
        log.warning("categories: the placer did not answer in %ss; %d subjects "
                    "keep their keyless parents",
                    settings.categories_place_timeout_seconds, len(subjects))
        return 0
    except Exception as exc:  # noqa: BLE001 - availability outranks diagnosis
        log.warning("categories: the placer failed (%s); %d subjects keep "
                    "their keyless parents", exc, len(subjects))
        return 0

    if getattr(response, "stop_reason", "") == "refusal":
        log.warning("categories: the placer declined; keeping keyless parents")
        return 0
    try:
        text = next(b.text for b in response.content if b.type == "text")
        rows = (json.loads(text) or {}).get("paths") or []
    except (StopIteration, AttributeError, ValueError, TypeError) as exc:
        log.warning("categories: the placer returned nothing readable (%s)", exc)
        return 0

    moved = apply_paths(store, rows, now)
    # A placed subject is no longer degraded: it is where a model put it
    # rather than where containment guessed. Done here rather than in
    # `apply_paths` so that function stays testable with no notion of what a
    # model is.
    for row in rows:
        subject = normalise((row or {}).get("subject", ""))
        node = store.get(subject)
        if node and node.degraded and node.parent_id:
            try:
                store._conn().execute(
                    "UPDATE categories SET degraded = 0 WHERE id = ?",
                    (subject,))
            except Exception:
                log.exception("could not clear degraded on %r", subject)
    store.reload()
    return moved


async def sweep(store: "CategoryStore", texts: Iterable[tuple[str, str]],
                always: Iterable[str] = (), now: Optional[float] = None
                ) -> dict:
    """One full growth cycle: promote, place, prune. Never raises.

    Called from the same background task that refreshes the story pool, and
    never from a request. The whole of this module's cost lives here: one
    model call, two SQLite passes and a rebuild of an in-process index.
    """
    from config import settings

    global _LAST_SWEEP
    now = time.time() if now is None else now
    if not settings.categories:
        return {"minted": 0, "placed": 0, "pruned": 0, "off": True}
    # Stamped before the work rather than after it. A sweep that fails should
    # wait its interval like any other, not retry on every page load - which
    # is how one broken model call becomes a request per browse.
    _LAST_SWEEP = now
    try:
        minted = promote(store, texts, always=always, now=now)
        placed = await place(store, [n.id for n in minted], now)
        pruned = store.prune(now)
    except Exception:
        # A browse page that failed to load because the vocabulary sweep
        # raised would be a much worse product than one ranked on last
        # week's vocabulary.
        log.exception("the category sweep failed; the tree is unchanged")
        return {"minted": 0, "placed": 0, "pruned": 0, "failed": True}
    return {"minted": len(minted), "placed": placed, "pruned": pruned,
            "nodes": len(store.nodes())}
