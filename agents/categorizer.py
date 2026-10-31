"""Nando, the categorizer - one node of the category tree per episode (§356).

docs/agent_rules/nando.md: pin every request to the single most specific
category it is truly about, before anything is researched; only nodes that
exist; by what it is about, not a word it contains; confirm after the
writer and change it if the episode turned out to be about something else;
record what every part of FAM said. The owner's decision (§346): code
first, folded into the brief's call, Haiku 5.5 only on ties; and (decided
item 38) the closest node the request is truly about - a broader node
rather than none, never a nearby wrong one.

This module is the code. Before any words: the node the brief's call
picked from the candidate list (§360), and what the tap already says
(`prior_for`, §357), which the words may only narrow. Then it reads what the
brief worked out - EI's category words, the resolved subject - before the
question's raw words, because the brief has already told "Miami heat wave"
from the basketball team and the raw words have not. The facet is chosen
first and every step stays inside it:

1. the brief's category words, by the tree's phrases (`resolve_category`);
2. a name that means two things, decided by the words around it
   (`category_collisions`, §358);
3. the brief's subject, by phrases;
4. the question, by phrases - never on a reading a decided name ruled out
   (a phrase in the question is how "hurricanes hitting the Carolina coast"
   became a hockey team, §209);
5. the vocabulary - a named thing, or another way of saying a node
   (`category_vocabulary`, §359);
6. the brief's category words by meaning (`category_near`, under the facet;
   words not embedded yet are queued, never embedded here);
7. the keyword map's deepest tree node for the question;
8. a name nothing decided: what all its readings share, or nothing;
9. the facet - broader, never wrong.

A node that lands on one reading of a name nothing decided is filed under
what the readings share instead (§361), and a placement below
`MIN_CONFIDENCE` under its parent. Every step reads memory: no file, store
or socket on the listener's path (the vocabulary and the Trending edition
are copies kept in memory, §361), and it never raises. A placement says
which step decided it (`how`), how sure it is, every node the steps found
(`candidates`) and any name left undecided (`ambiguous`), which is what the
brief's call and the tie call are shown and what `category_audit` logs.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger("fam.agents")


@dataclass(frozen=True)
class Placement:
    #: The node, or "" when nothing true was found.
    node: str = ""
    #: The facet, then each node down to `node`.
    path: tuple = ()
    #: Which step decided: category, subject, question, near, tags, facet.
    how: str = ""
    #: Every node the steps found, in the order found.
    candidates: tuple = ()
    #: How sure, 0 to 1 (§358): which step decided, lowered when a name
    #: that means two things was left undecided. Below `MIN_CONFIDENCE` the
    #: node is filed under its parent - still true, just broader.
    confidence: float = 0.0
    #: Names in the request that mean two things and that nothing around
    #: them decided (`category_collisions`), for C3P0 to ask about.
    ambiguous: tuple = ()
    #: What code read off the words when it was not solid enough to file
    #: (§364): shown to the model and logged, never filed by itself.
    suggestion: str = ""


NOWHERE = Placement()

#: How far each step is trusted (§358, §361). The tap's own filing and a
#: provider's are near certain; a phrase in the brief's words is the brief
#: understanding the question; a phrase in the raw question is the old
#: failure (§209); meaning and the keyword map are weakest and sit below the
#: floor, so what they find is filed under its parent. A facet, and what the
#: readings of an undecided name share, are always true.
CONFIDENCE = {
    "edition": 0.9, "tile": 0.9, "bank": 0.9, "dailyfam": 0.9, "provider": 0.9,
    "picked": 0.85,
    "parent": 0.7, "category": 0.8, "clash": 0.75, "entity": 0.8, "subject": 0.7,
    "wording": 0.65, "question": 0.6, "near": 0.45, "tags": 0.45, "shared": 1.0,
    "tie": 1.0, "facet": 1.0,
}
#: Below this a placement is filed under its parent node (decided item 38:
#: a broader node rather than a nearby wrong one).
MIN_CONFIDENCE = 0.5


@dataclass(frozen=True)
class Prior:
    """What the tap already says the episode is about, before any words are
    read (§357): its node and where that came from."""

    node: str = ""
    #: tile, bank, dailyfam, edition, parent
    source: str = ""


NO_PRIOR = Prior()

#: A DailyFAM prompt (`mixes.daily_prompt`): "The latest on Eagles (NFL) as
#: of ..." for a narrowed subject, "The latest on NFL as of ..." for a whole
#: one. Built by FAM, never typed by a listener, so its shape is ours.
_DAILY = re.compile(r"^The latest on (?P<shown>.+?)(?: \((?P<label>[^()]+)\))? as of ")


def path_for(node: str) -> tuple:
    """The facet, then each node down to `node` (as `Brief.category_path`)."""
    if not node:
        return ()
    try:
        import stories
        import topics

        path = list(reversed(topics.category_tree().ancestors(node))) + [node]
        if path and path[0] not in topics.FACETS:
            root = stories.facet_for(node)
            if root and root not in path:
                path.insert(0, root)
        return tuple(path)
    except Exception:  # noqa: BLE001
        return (node,)


#: The steps solid enough to file a node on their own (§364, the owner:
#: "at the end of the day, we want Nando to decide"). The tap's own filing,
#: a provider's field, the brief's own category words and the model's pick
#: from the candidate list. Everything code reads off the raw words - the
#: tree's phrases, the clash table, the vocabulary, the keyword map,
#: meaning - only builds the candidate list and the suggestion the model
#: is shown: three reviews found rules on raw words filing specific wrong
#: nodes ("boxing day sales" -> boxing) faster than they could be fixed.
SOLID = frozenset({"picked", "provider", "category", "tile", "bank", "dailyfam", "edition"})


def place(brief, query: str = "", field: tuple = (),
          prior: Prior = NO_PRIOR, picked: str = "", unsure: bool = False) -> Placement:
    """What Nando files at the request (§364): `suggest`'s node only when a
    solid source decided it (`SOLID`) - or the tap's own filing narrowed
    by the words on its branch, or the facet the brief's own words name.
    Otherwise nothing, with the candidates and the suggestion kept for the
    model and the audit; the writer's category then stands after the
    episode, as it did before Nando. Never raises."""
    try:
        found = suggest(brief, query, field, prior, picked, unsure)
        how = found.how.split("~")[0]
        solid = how in SOLID
        if not solid and found.node and prior.node and prior.source in SOLID:
            # On the tap's own branch, at or below its node: a narrowing of
            # a solid filing, which `_narrow` already enforced.
            solid = found.node == prior.node or prior.node in _branch(found.node)
        if not solid and how == "facet" and found.node:
            import stories

            words = " ".join(str(getattr(brief, "category", "") or "").split())
            named = stories.resolve_category(words) if words else ""
            solid = bool(named) and _facet(named) == found.node
        if solid:
            return found
        return Placement(node="", path=(), how="suggested" if found.node else "",
                         candidates=tuple(dict.fromkeys(
                             ((found.node,) if found.node else ()) + found.candidates)),
                         confidence=found.confidence, ambiguous=found.ambiguous,
                         suggestion=found.node)
    except Exception as exc:  # noqa: BLE001 - a category never costs an episode
        log.warning("nando could not place %r: %s", query, exc)
        return NOWHERE


def suggest(brief, query: str = "", field: tuple = (),
            prior: Prior = NO_PRIOR, picked: str = "", unsure: bool = False) -> Placement:
    """Every step's reading, in order: the node code would file, how and how
    sure (§356-§363). `place` files it only from a solid source; otherwise
    it is the suggestion and candidates the model is shown. Never raises.

    `field` is the facets a live game's or market's provider filed the
    story under (`provider_field`): Nando never files it anywhere else
    (nando.md, "never" 4) - a node outside them is passed over, and the
    provider's own deepest tag stands in.

    `prior` is what the tap already says (`prior_for`, §357): the words may
    narrow it - a node on its branch, below it - and never move it.

    `picked` is the node the brief's call chose from the candidate list
    (C3P0's call, or Nando's tie call, §360), checked here against the tree:
    it outranks every code step but the tap's own filing. `unsure` is C3P0
    saying the request is ambiguous: then his pick is a guess between the
    readings and is not taken (review, §361).

    A Go Deeper's parent (`Prior(..., "parent")`) is weaker than a tap's own
    filing: it is a title, placed by this module, and it stands only when
    the request itself places nothing more than a facet (review, §361)."""
    try:
        if unsure:
            picked = ""
        if picked and prior.source == "parent":
            prior = NO_PRIOR             # a hint: the brief's call outranks it (§363)
        if prior.source == "parent":
            placed = _place(brief, query)
            if placed.node and placed.node not in _facets():
                placed = _within(placed, field, query) if field else placed
                return _broader_when_unsure(placed)
            if not placed.node or _facet(prior.node) == placed.node:
                placed = Placement(node=prior.node, path=path_for(prior.node),
                                   how="parent",
                                   candidates=tuple(dict.fromkeys(
                                       (prior.node,) + placed.candidates)),
                                   confidence=CONFIDENCE["parent"],
                                   ambiguous=placed.ambiguous)
            placed = _within(placed, field, query) if field else placed
            return _broader_when_unsure(placed)
        if picked and _facet(picked) and (
                not prior.node or picked == prior.node or prior.node in _branch(picked)):
            import topics

            if picked in topics.FACETS or topics.category_tree().get(picked) is not None:
                placed = Placement(node=picked, path=path_for(picked), how="picked",
                                   candidates=(picked,), confidence=CONFIDENCE["picked"])
                placed = _within(placed, field, query) if field else placed
                return _broader_when_unsure(placed)
        placed = _place(brief, query, _facet(prior.node) if prior.node else "")
        if prior.node:
            placed = _narrow(prior, placed)
        placed = _within(placed, field, query) if field else placed
        return _broader_when_unsure(placed)
    except Exception as exc:  # noqa: BLE001 - a category never costs an episode
        log.warning("nando could not place %r: %s", query, exc)
        return NOWHERE


def _facets() -> frozenset:
    import topics

    return frozenset(topics.FACETS)


def _facet(node: str) -> str:
    import stories
    import topics

    return node if node in topics.FACETS else stories.facet_for(node)


def _within(placed: Placement, field: tuple, query: str) -> Placement:
    """`placed` if it sits in the provider's field, else the provider's own
    filing: its deepest tree node among the story's tags, else its facet."""
    if placed.node and _facet(placed.node) in field:
        return placed
    import topics

    tree = topics.category_tree()
    tags = [t for t in provider_tags(query)
            if t not in topics.FACETS and tree.get(t) is not None and _facet(t) in field]
    tags.sort(key=lambda n: (-tree.depth_of(n), n))
    node = tags[0] if tags else sorted(field)[0]
    return Placement(node=node, path=path_for(node), how="provider",
                     candidates=placed.candidates, confidence=CONFIDENCE["provider"])


def _provider_story(query: str):
    """The live game's or market move's story whose question this is, from
    the story pool, or None. The pool is memory; Trending (news, from GNews)
    holds no games or market moves and is not read, so this touches no
    database on the request path."""
    want = (query or "").strip()
    if not want:
        return None
    import stories

    try:
        held = list(stories.pool().held())
    except Exception:  # noqa: BLE001
        held = []

    for story in held:
        if ((getattr(story, "query", "") or "").strip() == want
                and getattr(story, "domain", "") in (stories.SPORTS, stories.MARKETS)):
            return story
    return None


def provider_tags(query: str) -> tuple:
    story = _provider_story(query)
    return tuple(getattr(story, "tags", ()) or ()) if story is not None else ()


def provider_field(query: str) -> tuple:
    """The facets a live game's or market's provider filed this question's
    story under (§187, `story_sources._in_field`), or () for anything else."""
    import topics

    return tuple(sorted({t for t in provider_tags(query) if t in topics.FACETS}))


def _with(placed: Placement, **changes) -> Placement:
    import dataclasses

    return dataclasses.replace(placed, **changes)


def _broader_when_unsure(placed: Placement) -> Placement:
    """Below `MIN_CONFIDENCE`, the parent node (or the facet): the episode
    is filed under something true rather than something possibly wrong."""
    if not placed.node or placed.confidence >= MIN_CONFIDENCE or len(placed.path) < 2:
        return placed
    parent = placed.path[-2]
    return _with(placed, node=parent, path=placed.path[:-1],
                 how=f"{placed.how}~broader")


def _narrow(prior: Prior, placed: Placement) -> Placement:
    """The words' node when it sits on the prior's branch at or below it,
    else the prior's node."""
    candidates = tuple(dict.fromkeys((prior.node,) + placed.candidates))
    if placed.node and (placed.node == prior.node or prior.node in _branch(placed.node)):
        how = placed.how if placed.node != prior.node else prior.source
        # Narrowing a known filing is at least as sure as the filing.
        return _with(placed, how=how, candidates=candidates,
                     confidence=max(placed.confidence, CONFIDENCE.get(prior.source, 0.7)))
    return Placement(node=prior.node, path=path_for(prior.node), how=prior.source,
                     candidates=candidates, confidence=CONFIDENCE.get(prior.source, 0.7),
                     ambiguous=placed.ambiguous)


def _interest_node(interest) -> str:
    """The tree node a catalogue interest is: its label or id when the tree
    holds one, else the deepest tree node among its tags, else its facet."""
    import stories
    import topics

    tree = topics.category_tree()
    for text in (getattr(interest, "label", ""), getattr(interest, "id", "")):
        node = stories.resolve_category(text) if text else ""
        if node:
            return node
    tags = [t for t in getattr(interest, "tags", ()) if tree.get(t) is not None]
    tags.sort(key=lambda n: (-tree.depth_of(n), n))
    if tags:
        return tags[0]
    return next((t for t in getattr(interest, "tags", ()) if t in topics.FACETS), "")


def _deepest_tag(tags) -> str:
    import topics

    tree = topics.category_tree()
    nodes = [t for t in tags if t not in topics.FACETS and tree.get(t) is not None]
    nodes.sort(key=lambda n: (-tree.depth_of(n), n))
    if nodes:
        return nodes[0]
    return next((t for t in tags if t in topics.FACETS), "")


def prior_for(query: str, context: str = "", notes=None) -> Prior:
    """What the tap already says the episode is about (§357), or `NO_PRIOR`.
    Memory reads only - the pool, the bank, the catalogue and, as
    `stories.context_for` already does on a tap, the Trending edition.
    Never raises. In order:

    1. an edition writer's own (`notes.category_prior`: the Trending story's
       category, which the writer knew before anything was asked);
    2. a tile: the live story or Trending story whose question this is;
    3. a bank topic with this question: its deepest tree tag;
    4. a DailyFAM prompt: the followed subject from the catalogue, and the
       narrowed specific within it when the tree places it there;
    5. a Go Deeper: the episode it follows, by the title it carried.
    """
    try:
        return _prior_for(query, context, notes)
    except Exception as exc:  # noqa: BLE001
        log.warning("nando could not read the tap's context for %r: %s", query, exc)
        return NO_PRIOR


def _resolved(text: str) -> str:
    import stories

    return stories.resolve_category(text) if text else ""


def _prior_for(query: str, context: str, notes) -> Prior:
    import stories
    import topics

    given = getattr(notes, "category_prior", None)
    if isinstance(given, Prior) and given.node and (
            given.node in topics.FACETS or topics.category_tree().get(given.node) is not None):
        return given
    want = " ".join((query or "").split())
    if not want:
        return NO_PRIOR
    held = list(stories.pool().held())
    try:
        import trending_bank

        # The edition already in memory - never a read of its store on the
        # listener's path (review, §361).
        held += list(trending_bank.stories_in_memory())
    except Exception:  # noqa: BLE001
        pass
    for story in held:
        if " ".join((getattr(story, "query", "") or "").split()) == want:
            node = _resolved(getattr(story, "category", "") or "") \
                or _deepest_tag(getattr(story, "tags", ()) or ())
            if node:
                return Prior(node, "tile")
    for topic in topics.TOPIC_BANK:
        if " ".join(topic.query.split()) == want:
            node = _resolved(getattr(topic, "category", "") or "") or _deepest_tag(topic.tags)
            if node:
                return Prior(node, "bank")
    daily = _DAILY.match(want)
    if daily:
        label = (daily.group("label") or daily.group("shown") or "").strip()
        interest = next((i for i in topics.INTEREST_CATALOGUE
                         if i.label.lower() == label.lower()), None)
        if interest is not None:
            node = _interest_node(interest)
            shown = (daily.group("shown") or "").strip() if daily.group("label") else ""
            narrowed = _resolved(shown)
            if not narrowed and shown and node:
                # "Eagles" under NFL: the vocabulary, on the subject's facet,
                # with the subject's own label as the sport context (§359).
                import category_vocabulary

                found, _kind, _w = category_vocabulary.match(
                    f"{shown} {label}", _facet(node))
                found = tuple(n for n in found if node in _branch(n))
                narrowed = found[0] if len(found) == 1 else ""
            if narrowed and node and (narrowed == node or node in _branch(narrowed)):
                node = narrowed
            if node:
                return Prior(node, "dailyfam")
    # The episode a Go Deeper follows, by its title - placed by this module's
    # own steps (clashes, the vocabulary, the sport gate), never by a raw
    # phrase match (review, §361: "Utah's jazz festival lineup").
    title = " ".join((context or "").split())
    if title:
        parent = _place(None, title)
        if parent.node and parent.confidence >= MIN_CONFIDENCE:
            # Read off the parent's title words: a hint for the model, not a
            # filing (§364) - `place` never files a "parent" prior alone.
            return Prior(parent.node, "parent")
    return NO_PRIOR


def _cue_in(cue: str, text: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(cue) + r"(?![a-z0-9])", text) is not None


def clashes(text: str) -> list:
    """The names in `text` that mean two things (`category_collisions`), each
    `(name, decided, readings, cued)`: `decided` is the one reading the words
    around the name point to, or None when none or several do - a tie,
    never guessed, and then `readings` are only those tied on cues; `cued`
    says whether any reading had a word for it at all
    (no cue is no evidence, review §361). A reading's own node name in the
    text ("new york rangers") decides it. A word inside another name
    (`NOT_THE_NAME`: "United States") is not the clash. Never raises."""
    try:
        import category_collisions

        low = " ".join((text or "").lower().split())
        out = []
        for name, readings in category_collisions.COLLISIONS.items():
            here = _without_other_names(low, name)
            if not _cue_in(name, here):
                continue
            rest = re.sub(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])", " ", here)
            # A cue that holds the name itself ("new york jets") is read in
            # the whole text; the others without the name (review, §361).
            hits = [sum(1 for cue in r.cues
                        if _cue_in(cue, here if _cue_in(name, cue) else rest))
                    + (1 if r.node and _cue_in(name, r.node) and _cue_in(r.node, here) else 0)
                    for r in readings]
            best = max(hits)
            decided = (readings[hits.index(best)]
                       if best and hits.count(best) == 1 else None)
            # A draw is between the readings that tied on cues; a reading
            # with none ("Kings game tonight": the monarchs) is not in it.
            if decided is None and best:
                readings = tuple(r for r, h in zip(readings, hits) if h == best)
            out.append((name, decided, readings, best > 0))
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("nando could not read the name clashes: %s", exc)
        return []


def _sport_context(text: str) -> bool:
    import category_vocabulary

    return category_vocabulary._sporty(category_vocabulary._norm(text or ""))


def _without_other_names(low: str, only: str = "") -> str:
    """`low` with the phrases in which a clash word is part of another name
    taken out ("power rangers", "united states") - unless a cue that holds
    the word itself is there ("manchester united states tour": the club).
    For one name, or every name. Read by the clash check and, so a stripped
    word cannot come back as a nickname, by the vocabulary step (§363)."""
    import category_collisions

    names = (only,) if only else tuple(category_collisions.NOT_THE_NAME)
    for name in names:
        held = any(_cue_in(cue, low) for r in category_collisions.COLLISIONS.get(name, ())
                   for cue in r.cues if _cue_in(name, cue))
        if held:
            continue
        for phrase in category_collisions.NOT_THE_NAME.get(name, ()):
            low = re.sub(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", " ", low)
    return low


def _shared(nodes) -> str:
    """The most specific node every one of `nodes` sits under, or ""."""
    import topics

    if not nodes or not all(nodes):
        return ""
    lines = [[n] + list(topics.category_tree().ancestors(n)) for n in nodes]
    for node in lines[0]:
        if all(node in line for line in lines[1:]):
            return node
    facets = {_facet(n) for n in nodes}
    return facets.pop() if len(facets) == 1 else ""


def _place(brief, query: str, prior_facet: str = "") -> Placement:
    import stories
    import topics

    tree = topics.category_tree()
    words = " ".join(str(getattr(brief, "category", "") or "").split())
    subject = " ".join(str(getattr(brief, "subject", "") or "").split())
    found: list = []

    def seen(node: str) -> str:
        if node and node not in found:
            found.append(node)
        return node

    # Names that mean two things (§358), read across the question and the
    # brief's own words. A decided one points at a reading; the others are
    # the readings it is not.
    named = clashes(" ".join((query or "", subject, words)))
    decided_named = [(n, d, rs) for n, d, rs, _c in named if d is not None]
    # A losing reading on the winner's own branch (consumer hardware above
    # smartphones) is not ruled out: it is the same truth, said less exactly.
    # Losing readings stay ruled out whatever else happens (§363).
    losing = [r.node for _n, d, rs in decided_named for r in rs
              if r is not d and r.node and not (
                  d.node and (r.node in _branch(d.node) or d.node in _branch(r.node)))]
    # Two decided names whose filed readings are not on one branch contradict
    # each other ("Eagles fans brave the heat wave": the Eagles and the
    # Heat's basketball both claim "fans"), and no table order settles it:
    # the contradicting readings become a tie between themselves. A reading
    # with no node (the bird) files nothing and contradicts nothing (§363).
    filed = [(n, d) for n, d, _rs in decided_named if d.node]
    conflict = any(not (a.node == b.node or a.node in _branch(b.node)
                        or b.node in _branch(a.node))
                   for i, (_x, a) in enumerate(filed) for _y, b in filed[i + 1:])
    decided = [] if conflict else [d for _n, d, _rs in decided_named]
    # Only a draw between readings that each had a cue is a tie; a name with
    # no cue at all is no evidence for any reading (review, §361).
    undecided = [(n, rs) for n, d, rs, cued in named if d is None and cued]
    if conflict:
        undecided.append(("+".join(n for n, _d in filed), tuple(d for _n, d in filed)))
    ambiguous = tuple(n for n, d, _rs, _c in named if d is None) + (
        tuple(n for n, _d in filed) if conflict else ())
    tied = [r.node for _n, rs in undecided for r in rs if r.node]

    def touches(node: str, nodes) -> bool:
        return any(node == other or other in _branch(node) for other in nodes)

    def done(node: str, how: str) -> Placement:
        sure = CONFIDENCE.get(how, 0.5)
        if node and how not in ("facet", "shared"):
            # A node on one reading of a name nothing decided is a guess
            # between the readings (review, §361: "Rangers season preview"
            # was filed under the NHL). It is filed under what all of that
            # name's readings share - or nothing - never one of them.
            for _name, readings in undecided:
                nodes = [r.node for r in readings]
                if touches(node, [n for n in nodes if n]):
                    node, how = _shared(nodes), "tie"
                    sure = 1.0 if node else 0.0
                    break
        return Placement(node=node, path=path_for(node), how=how,
                         candidates=tuple(found), confidence=round(sure, 3),
                         ambiguous=ambiguous)

    # Facet first (§357): the tap's, else the brief's words', else a decided
    # name's, else the subject's. Every later step stays inside it, which is
    # where the cross-facet mistakes (the heat wave and the Heat) are stopped.
    by_words = seen(stories.resolve_category(words)) if words else ""
    with_node = [d for d in decided if d.node]
    by_clash = seen(with_node[0].node) if with_node else ""
    by_subject = seen(stories.resolve_category(subject)) if subject else ""
    by_question = seen(stories.resolve_category(query)) if query else ""
    facet = (prior_facet or (_facet(by_words) if by_words else "")
             or (_facet(by_clash) if by_clash else "")
             or (_facet(by_subject) if by_subject else ""))

    def agrees(node: str) -> bool:
        return (bool(node) and (not facet or _facet(node) == facet)
                and not touches(node, losing))

    # 1: the brief's words by phrase - the brief has read the question, so
    # it outranks the clash table (only the tap's facet holds it).
    if (by_words and by_words not in topics.FACETS
            and (not prior_facet or _facet(by_words) == prior_facet)):
        return done(by_words, "category")
    # 2: a name that means two things, decided by the words around it.
    if by_clash and agrees(by_clash):
        # A team named in full under that reading is more specific still.
        for node in (by_subject, by_question):
            if node and node != by_clash and by_clash in _branch(node) and agrees(node):
                return done(node, "clash")
        return done(by_clash, "clash")
    if decided and not with_node:
        # The words point to a reading the tree has no node for (the bird):
        # nothing is filed rather than the other reading's node.
        return done("", "clash")
    # 3-4: phrases in the subject, then the question - never a node a
    # decided name ruled out. A phrase is the tree's own word, exact (§209).
    if agrees(by_subject) and by_subject not in topics.FACETS:
        return done(by_subject, "subject")
    if agrees(by_question) and by_question not in topics.FACETS:
        # A seeded team's full name means the team (the seed leaves out the
        # names that are everyday phrases, `LEFT_TO_GROW`); where one is
        # still both ("Utah jazz festival") it is a clash, decided above
        # (§363 - a gate here unplaced twenty right teams).
        return done(by_question, "question")
    # 5: the vocabulary (§359) - a named thing, or another way of saying a
    # node, in the brief's words, its subject, then the question. A wording
    # that names two nodes is a tie: what they share, never one of them.
    import category_vocabulary

    for text in (words, subject, query):
        # Without the phrases where a clash word is another name's ("power
        # rangers"), so it cannot come back as a team's nickname (§363).
        nodes, kind, _wording = category_vocabulary.match(
            _without_other_names(" ".join((text or "").lower().split())), facet, tree)
        nodes = tuple(n for n in nodes if agrees(n) and (
            n in topics.FACETS or tree.get(n) is not None))
        if len(nodes) == 1:
            return done(seen(nodes[0]), kind)
        if len(nodes) > 1:
            for n in nodes:
                seen(n)
            shared = _shared(list(nodes))
            if shared:
                return done(shared, "shared")
    # 6: the brief's words by meaning, under its facet.
    if words:
        import category_near

        # Never embedded while a listener waits (review, §361): words not
        # embedded yet are queued and placed next time, as the readers do.
        near = seen(category_near.nearest(words, facet=facet, inline=0))
        if near and agrees(near):
            return done(near, "near")
    # 7: the keyword map's deepest tree node for the question.
    sporty = facet == "sports" or _sport_context(query)
    tagged = [t for t in topics.tags_for_text(query or "")
              if t not in topics.FACETS and tree.get(t) is not None and agrees(t)
              # The keyword map reads team names into league tags, so in a
              # question with no sport in it no sports tag is taken (§361).
              and (sporty or _facet(t) != "sports")]
    if tagged:
        tagged.sort(key=lambda n: (-tree.depth_of(n), n))
        return done(seen(tagged[0]), "tags")
    # 8: an undecided name - what all its readings share, never one of them,
    # and only inside the facet already chosen.
    for _name, readings in undecided:
        shared = _shared([r.node for r in readings])
        if shared and (not facet or _facet(shared) == facet):
            return done(shared, "shared")
    # 9: broader, never wrong.
    if facet:
        return done(facet, "facet")
    for node in (by_subject, by_question):
        if node in topics.FACETS:
            return done(node, "facet")
    return Placement(candidates=tuple(found), ambiguous=ambiguous)


# ------------------------------------------------------------------ after the writer
def _branch(node: str) -> tuple:
    import topics

    return tuple(topics.category_tree().ancestors(node)) if node else ()


def confirm(request: str, words: str, field: tuple = ()) -> tuple:
    """`(node, how)`: the episode's node once it is written (nando.md,
    "always" 5). `request` is Nando's node from the request, `words` the
    writer's `<<CATEGORY:>>`. Never raises.

    * the writer named nothing the tree knows - the request's node stands
      (`kept`);
    * no request node - the writer's (`writer`);
    * the same node, or the writer's is under the request's on its branch -
      the writer's, the same truth said more exactly (`confirmed` /
      `narrowed`);
    * the writer's is above the request's - the request's, which is more
      specific and still true (`confirmed`);
    * anything else - the episode turned out to be about something else,
      and is re-filed under the writer's (`refiled`), both logged.

    A provider's field is never left (`field`)."""
    try:
        import stories

        written = stories.resolve_category(words) if words else ""
        if not written and words:
            import category_near

            # Queued, never embedded on the event loop (review, §361).
            written = category_near.nearest(words, inline=0)
        if field and written and _facet(written) not in field:
            written = ""
        if not written:
            return request, ("kept" if request else "")
        if not request:
            return written, "writer"
        if written == request:
            return request, "confirmed"
        if request in _branch(written):
            return written, "narrowed"
        if written in _branch(request) or (written == _facet(request)):
            return request, "confirmed"
        return written, "refiled"
    except Exception as exc:  # noqa: BLE001
        log.warning("nando could not confirm %r: %s", words, exc)
        return request, "kept" if request else ""


def settle(notes, query: str = "") -> str:
    """After the writer, on every write path: the node to store with the
    episode, or "" when Nando did not work on it. Records both decisions on
    `notes.category_settled` for the audit and puts the node on the live
    track, so the player's picture follows it. Never raises."""
    try:
        placed = getattr(notes, "category_placement", None)
        if placed is None:
            return ""
        field = provider_field(query)
        node, how = confirm(placed.node, getattr(notes, "category", "") or "", field)
        notes.category_settled = {"request": placed.node, "request_how": placed.how,
                                  "node": node, "how": how,
                                  "confidence": placed.confidence,
                                  "ambiguous": " ".join(placed.ambiguous)}
        if how == "refiled":
            log.info("nando re-filed %r: %s at the request, %s after the writer",
                     query, placed.node, node)
        key = getattr(notes, "caption_key", "")
        if key and node:
            import live_captions

            live_captions.publish_category(key, node)
        return node
    except Exception as exc:  # noqa: BLE001
        log.warning("nando could not settle %r: %s", query, exc)
        return ""


# ------------------------------------------------------------------ the candidate list and the tie call (§360)
#: How many candidate nodes the brief's call is shown: enough to hold every
#: reading and their parents, few enough to read in a glance.
MAX_CANDIDATES = 20


def candidates_for(query: str, context: str = "", notes=None) -> tuple:
    """`(nodes, tie)`: the nodes the brief's call may choose from, found in
    code before it is asked - the tap's own, every reading of a name that
    means two things, the tree's phrases, the vocabulary's matches and the
    keyword map's nodes, each with its parent so a broader true node is
    always on offer - and whether the request holds a tie code could not
    settle. The facets when nothing else is found. Memory only; never
    raises."""
    try:
        import category_vocabulary
        import stories
        import topics

        tree = topics.category_tree()
        found: list = []
        tie = False

        def add(node: str) -> None:
            if node and node not in found and (node in topics.FACETS
                                               or tree.get(node) is not None):
                found.append(node)

        prior = prior_for(query, context, notes)
        add(prior.node)
        for _name, decided, readings, cued in clashes(query):
            if decided is not None:
                add(decided.node)
            elif cued:
                tie = True
            for reading in readings:
                add(reading.node)
        add(stories.resolve_category(query))
        nodes, _kind, _w = category_vocabulary.match(query)
        tie = tie or len(nodes) > 1
        for node in nodes:
            add(node)
        for tag in topics.tags_for_text(query or ""):
            if tag not in topics.FACETS:
                add(tag)
        # Each node's parent right after it, so cutting the list to its size
        # never drops the broader true node first (review, §361).
        ordered: list = []
        for node in found:
            path = path_for(node)
            for n in (node, path[-2] if len(path) > 1 else ""):
                if n and n not in ordered:
                    ordered.append(n)
        found = ordered
        if not found:
            for facet in sorted(topics.FACETS):
                add(facet)
        return tuple(found[:MAX_CANDIDATES]), tie
    except Exception as exc:  # noqa: BLE001
        log.warning("nando could not list candidates for %r: %s", query, exc)
        return (), False


class TiePick(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str = Field(max_length=120)


TIE_INSTRUCTIONS = """You file one FAM request under one node of FAM's \
category tree. You are given the request and the candidate nodes with their \
paths. Choose the single most specific node the episode is truly about, from \
the list only, or "none". A node matches what the episode is about, not a \
word it contains: "hurricanes hitting the Carolina coast" is weather, not the \
hockey team. A broader true node is better than a specific wrong one."""


async def ask_tie(query: str, candidates: tuple, notes=None) -> str:
    """Nando's own call, only on a tie code could not settle, run beside the
    brief and never waited for (the owner's answer 5, §354): a node from
    `candidates`, or "". Never raises."""
    try:
        from agents import models, netenyahu
        from agents.contextualizer import candidate_block
        from config import settings

        request = models.AgentRequest(
            agent="categorizer", task="place", switch_setting="categorizer",
            model_setting="categorizer_model", rule_book="nando.md", schema=TiePick,
            instructions=TIE_INSTRUCTIONS,
            material=(("request", query),
                      ("evidence", "Candidate nodes (id | path):\n"
                       + candidate_block(candidates))),
            timeout=float(settings.categorizer_timeout_seconds),
            max_tokens=int(settings.categorizer_max_tokens), effort="low",
            surface="search")
        result = await models.ask(request, notes=notes)
        netenyahu.report("categorizer", result.status)
        if not result.ok:
            return ""
        node = result.value.node.strip()
        return node if node in candidates else ""
    except Exception as exc:  # noqa: BLE001
        log.warning("nando's tie call failed for %r: %s", query, exc)
        return ""


def tie_answer(task) -> str:
    """The tie call's node if it has already answered, else "" - and a
    call still running is cancelled: nothing waits for it."""
    if task is None:
        return ""
    if not task.done():
        task.cancel()
        return ""
    try:
        return task.result() or ""
    except BaseException:  # noqa: BLE001 - cancelled or failed: no answer
        return ""
