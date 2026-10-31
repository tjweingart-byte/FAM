"""What each category node is called, and the named things filed under it
(§359).

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
model, no file after the first load, never on a socket. A wording that
names two nodes is a tie and is returned as both; Nando never picks
between them (c3p0.md, "always" 2).
"""
from __future__ import annotations

import json
import logging
import pathlib
import threading
from typing import Optional

log = logging.getLogger("fam.categories")

PATH = pathlib.Path(__file__).resolve().parent / "category_vocabulary.json"
PENDING = PATH.with_name("category_vocabulary.pending.json")

#: Two-word nicknames, where the last word alone is someone else's too.
TWO_WORD_NICKNAMES = ("trail blazers", "red sox", "white sox", "blue jays",
                      "maple leafs", "red wings", "golden knights", "blue jackets",
                      "golden state")
#: Words that say a question is about sport, so a team's nickname may be
#: read as the team (`category_collisions`' sports cues, plus the leagues).
SPORT_WORDS = ("game", "games", "score", "scores", "trade", "trades", "roster",
               "coach", "playoff", "playoffs", "draft", "season", "lineup", "beat",
               "lost", "win", "won", "standings", "fans", "stadium", "arena",
               "signing", "injury", "injured", "rumors", "rumours", "nfl", "nba",
               "mlb", "nhl", "quarterback", "pitcher", "goalie", "point guard",
               "touchdown", "home run", "overtime", "series", "finals")

_LOCK = threading.Lock()
_CACHE: dict = {"key": None, "index": None}


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


def index(tree=None) -> dict:
    """`{wording: (nodes, needs_sport, kind)}` over both sources, built once
    per tree and vocabulary file. `kind` is `entity` for a named thing,
    else `wording`."""
    import topics

    tree = tree or topics.category_tree()
    try:
        stamp = (id(tree), getattr(tree, "_loaded_at", 0.0),
                 PATH.stat().st_mtime if PATH.exists() else 0.0)
    except OSError:
        stamp = (id(tree), getattr(tree, "_loaded_at", 0.0), 0.0)
    with _LOCK:
        if _CACHE["key"] == stamp and _CACHE["index"] is not None:
            return _CACHE["index"]
    built: dict = {}
    for wording, (nodes, sport) in derived(tree).items():
        built[wording] = (nodes, sport, "wording")
    data = load()
    for node, entry in (data.get("nodes") or {}).items():
        if tree.get(node) is None and node not in topics.FACETS:
            continue
        for wording in (entry or {}).get("wordings", ()) or ():
            key = _norm(wording)
            if not key or key == node:
                continue
            nodes, sport, kind = built.get(key, ((), False, "wording"))
            if node not in nodes:
                built[key] = (nodes + (node,), sport, kind)
    for name, entry in (data.get("entities") or {}).items():
        node = (entry or {}).get("node", "")
        key = _norm(name)
        if not key or (tree.get(node) is None and node not in topics.FACETS):
            continue
        nodes, _sport, _kind = built.get(key, ((), False, "entity"))
        if node not in nodes:
            built[key] = (nodes + (node,), False, "entity")
    with _LOCK:
        _CACHE.update(key=stamp, index=built)
    return built


def reset() -> None:
    with _LOCK:
        _CACHE.update(key=None, index=None)


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
        for wording, (nodes, needs_sport, kind) in built.items():
            if f" {wording} " not in padded:
                continue
            if needs_sport:
                if sporty is None:
                    sporty = _sporty(words) or facet == "sports"
                if not sporty:
                    continue
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
    """A node's drafted definition and exclusions, or {}. Piece 7's
    candidate list carries these to the brief."""
    entry = (load().get("nodes") or {}).get(node) or {}
    return {k: entry[k] for k in ("definition", "excludes", "wikidata") if entry.get(k)}
