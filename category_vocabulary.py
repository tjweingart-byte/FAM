"""What each category node is called, and the named things filed under it
(§370).

Data beside the category seed (`category_seed.py`), at the owner's
direction: reviewed like the seed, never a signed rule book. The tree's own
phrases are exact and stay first (§209); this is the other ways people say
the same node, so "why rents keep rising" reaches `rental market` and
"Lakers trade rumors" reaches the Lakers - and the named things the tree
should not grow nodes for (a singer, a company, a team whose name is also
an everyday word, `category_seed.LEFT_TO_GROW`), each pointing at a node.

Two sources, read together:

* **Derived in code**, from data FAM already has and nothing fitted to a
  test: each seeded team's nickname ("lakers" for `los angeles lakers`),
  usable only when the question is about sport, because a nickname alone
  can be an everyday word ("jazz"); and each followable interest's label
  ("Cryptocurrency" for `crypto`).
* **Drafted, then checked**: `category_vocabulary.json` - each node's
  one-line definition and what it excludes, its other wordings and its
  Wikidata id, and the entity table. Written by `tools/draft_category_vocabulary.py`
  (Haiku 5.5 through the agent layer, the owner's decision; Wikidata ids
  kept only when Wikidata's own label matches) as a pending file a person
  reads before renaming it. Empty until then.

`match(text, facet)` is a dictionary walk over the question's words - no
model, never on a socket, and no file on the request path: the index is
built once and rebuilt in the background when the tree or the file changes
(§372). A wording that
names two nodes is a tie and is returned as both; Nando never picks
between them (c3p0.md, "always" 2).
"""
from __future__ import annotations

import json
import logging
import pathlib
import threading
import time
from typing import Optional

log = logging.getLogger("fam.categories")

PATH = pathlib.Path(__file__).resolve().parent / "category_vocabulary.json"
PENDING = PATH.with_name("category_vocabulary.pending.json")

#: Two-word nicknames, where the last word alone is someone else's too.
TWO_WORD_NICKNAMES = ("trail blazers", "red sox", "white sox", "blue jays",
                      "maple leafs", "red wings", "golden knights", "blue jackets")
#: Words that say a question is about sport, so a team's nickname may be
#: read as the team. Only words that are about sport and little else: not
#: "season", "lineup", "win" or "trade", which a festival, a menu, an
#: election and a tariff all have (review, §372 - "jazz festival lineup" was
#: filed under the Utah Jazz).
SPORT_WORDS = ("game", "games", "score", "scores", "roster", "coach", "playoff",
               "playoffs", "standings", "stadium", "arena", "injury", "injured",
               "nfl", "nba", "mlb", "nhl", "wnba", "quarterback", "pitcher", "goalie",
               "point guard", "touchdown", "home run", "overtime", "free agent",
               "head coach", "box score")
#: How often a running server looks at the file for a new draft: never per
#: request (§372).
CHECK_SECONDS = 60.0

_LOCK = threading.Lock()
_CACHE: dict = {"key": None, "index": None, "data": None, "checked": 0.0,
                "building": False}


def _empty() -> dict:
    return {"version": 1, "nodes": {}, "entities": {}}


def load(path: Optional[pathlib.Path] = None) -> dict:
    """The drafted file, or an empty one. Never raises."""
    path = path or PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _empty()
        data.setdefault("nodes", {})
        data.setdefault("entities", {})
        return data
    except FileNotFoundError:
        return _empty()
    except Exception as exc:  # noqa: BLE001 - a vocabulary is never worth a failure
        log.warning("category vocabulary unreadable (%s); using none", exc)
        return _empty()


def _norm(text: str) -> str:
    import categories

    return categories.normalise(text)


def derived(tree) -> dict:
    """`{wording: (nodes, needs_sport)}` from data FAM already has."""
    import category_seed
    import topics

    out: dict = {}

    def add(wording: str, node: str, sport: bool) -> None:
        wording = _norm(wording)
        if not wording or not node or tree.get(node) is None and node not in topics.FACETS:
            return
        nodes, needs = out.get(wording, ((), sport))
        if node not in nodes:
            out[wording] = (nodes + (node,), needs and sport)

    teams = []
    for name in ("NFL_TEAMS", "NBA_TEAMS", "MLB_TEAMS", "NHL_TEAMS"):
        teams += list(getattr(category_seed, name, {}) or {})
    for team in teams:
        words = team.split()
        if len(words) < 2:
            continue                        # "steelers", "niners": already a phrase
        two = " ".join(words[-2:])
        add(two if two in TWO_WORD_NICKNAMES else words[-1], team, True)
    for interest in getattr(topics, "INTEREST_CATALOGUE", ()):
        label = _norm(interest.label)
        node = ""
        import stories

        node = stories.resolve_category(interest.id) or stories.resolve_category(label)
        if label and node and label != node:
            add(label, node, False)
    return out


def _stamp(tree) -> tuple:
    try:
        mtime = PATH.stat().st_mtime if PATH.exists() else 0.0
    except OSError:
        mtime = 0.0
    return (id(tree), getattr(tree, "_loaded_at", 0.0), str(PATH), mtime)


def index(tree=None) -> dict:
    """`{wording: (nodes, needs_context, kind)}` over both sources. `kind`
    is `entity` for a named thing, else `wording`; `needs_context` says the
    wording is also an everyday word and may be read only in a question
    about its own field.

    **Never rebuilt on the request path** (§372): built once, then checked
    against the tree and the file at most every `CHECK_SECONDS`, and a
    stale index is rebuilt in a background thread while the old one keeps
    answering."""
    import topics

    tree = tree or topics.category_tree()
    with _LOCK:
        have = _CACHE["index"]
        same_tree = have is not None and bool(_CACHE["key"]) and _CACHE["key"][0] == id(tree)
        if same_tree:
            stale = time.monotonic() - _CACHE["checked"] >= CHECK_SECONDS
            if stale and not _CACHE["building"]:
                # Whether the tree or the file changed is asked in the
                # background; the copy we have answers meanwhile.
                _CACHE["checked"] = time.monotonic()
                _CACHE["building"] = True
                threading.Thread(target=_refresh, args=(tree,),
                                 name="category-vocabulary", daemon=True).start()
            return have
        if have is not None:
            # A new tree object (a wipe, a reload in another module): the
            # copy we have answers while the new one is built beside it
            # (review, §372) - its nodes are checked against the tree by
            # every reader anyway. At most once an interval (§374).
            if (not _CACHE["building"]
                    and time.monotonic() - _CACHE["checked"] >= CHECK_SECONDS / 6):
                _CACHE["building"] = True
                _CACHE["checked"] = time.monotonic()
                threading.Thread(target=_refresh, args=(tree,),
                                 name="category-vocabulary", daemon=True).start()
            return have
    # First use only: built here (boot primes it when the agents are on, §372).
    built = _build(tree, _stamp(tree))
    with _LOCK:
        _CACHE["checked"] = time.monotonic()
    return built


def _refresh(tree) -> None:
    stamp = _stamp(tree)
    with _LOCK:
        unchanged = _CACHE["key"] == stamp
        if unchanged:
            _CACHE["building"] = False
    if not unchanged:
        _build(tree, stamp)


def _build(tree, stamp) -> dict:
    """Build the index (first use, a new tree, or the background)."""
    import topics

    try:
        built = _built(tree)
        data = load()
    except Exception:  # noqa: BLE001 - keep what answered before
        log.exception("category vocabulary could not be built")
        with _LOCK:
            _CACHE["building"] = False
            return _CACHE["index"] or {}
    with _LOCK:
        _CACHE.update(key=stamp, index=built, data=data, building=False)
    return built


def refused(key: str, node: str, tree) -> str:
    """Why a normalised wording may not stand for `node`, or "" (§385):

    * it is a one-letter word with nothing longer to anchor it - "M&A"
      normalises to "m a", which a question normalises the same way, so
      "I'm a Lakers fan" ("i m a lakers fan") would read as mergers. "e
      commerce" and "women s health" keep their long word and stay;
    * it is another node's own name - that node already matches it, and
      listing it under a second node only manufactures a tie ("nfl" under
      american football);
    * it is a name known to mean two things, or a word known not to be the
      name (`category_collisions`) - those are decided there, never here."""
    import category_collisions
    import topics

    words = key.split()
    if (any(len(w) == 1 and not w.isdigit() for w in words)
            and not any(len(w) >= 3 for w in words)):
        return "a one-letter word with nothing longer"
    if key != node and (tree.get(key) is not None or key in topics.FACETS):
        return f"the node {key!r}'s own name"
    if key in category_collisions.COLLISIONS or key in category_collisions.NOT_THE_NAME:
        return "a name that means two things"
    return ""


def _built(tree) -> dict:
    import topics

    built: dict = {}
    for wording, (nodes, sport) in derived(tree).items():
        built[wording] = (nodes, sport, "wording")
    data = load()
    for node, entry in (data.get("nodes") or {}).items():
        if tree.get(node) is None and node not in topics.FACETS:
            continue
        for wording in (entry or {}).get("wordings", ()) or ():
            key = _norm(wording)
            if not key or key == node or refused(key, node, tree):
                continue
            nodes, sport, kind = built.get(key, ((), False, "wording"))
            if node not in nodes:
                built[key] = (nodes + (node,), sport, kind)
    for name, entry in (data.get("entities") or {}).items():
        node = (entry or {}).get("node", "")
        key = _norm(name)
        if not key or (tree.get(node) is None and node not in topics.FACETS):
            continue
        # A one-word name ("Bills", "Lions", "Mercedes") is also an everyday
        # word, and keeps any context a derived nickname already asked for
        # (review, §372): it is read only in a question about its field.
        nodes, needs, _kind = built.get(key, ((), False, "entity"))
        needs = needs or len(key.split()) == 1
        if node not in nodes:
            nodes = nodes + (node,)
        built[key] = (nodes, needs, "entity")
    return built


def reset() -> None:
    with _LOCK:
        _CACHE.update(key=None, index=None, data=None, checked=0.0, building=False)


def _sporty(text: str) -> bool:
    padded = f" {text} "
    return any(f" {_norm(w)} " in padded for w in SPORT_WORDS)


def match(text: str, facet: str = "", tree=None) -> tuple:
    """`(nodes, kind, wording)` for the longest wording in `text` the
    vocabulary knows, kept to `facet` when one is given; `((), "", "")` for
    none. More than one node is a tie. Never raises."""
    try:
        import stories
        import topics

        words = _norm(text)
        if not words:
            return (), "", ""
        built = index(tree)
        padded = f" {words} "
        sporty = None
        best = None
        for wording, (nodes, needs_context, kind) in built.items():
            if f" {wording} " not in padded:
                continue
            if needs_context:
                # An everyday word too: read only inside its own field - a
                # sports question for a team, else the facet already known.
                if sporty is None:
                    sporty = _sporty(words) or facet == "sports"
                in_field = tuple(n for n in nodes if (
                    (n if n in topics.FACETS else stories.facet_for(n)) == "sports"
                    and sporty) or (facet and (n if n in topics.FACETS
                                               else stories.facet_for(n)) == facet))
                if not in_field:
                    continue
                nodes = in_field
            if facet:
                nodes = tuple(n for n in nodes if (n if n in topics.FACETS
                                                   else stories.facet_for(n)) == facet)
                if not nodes:
                    continue
            rank = (kind == "entity", len(wording.split()), len(wording))
            if best is None or rank > best[0]:
                best = (rank, nodes, kind, wording)
        if best is None:
            return (), "", ""
        return best[1], best[2], best[3]
    except Exception as exc:  # noqa: BLE001
        log.warning("category vocabulary match failed: %s", exc)
        return (), "", ""


def definition(node: str) -> dict:
    """A node's drafted definition and exclusions, or {}, from the copy the
    index holds - never a read of the file per call (review, §372). The
    candidate list carries these to the brief."""
    index()
    entry = ((_CACHE["data"] or {}).get("nodes") or {}).get(node) or {}
    return {k: entry[k] for k in ("definition", "excludes", "wikidata") if entry.get(k)}
