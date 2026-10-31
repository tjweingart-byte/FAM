"""Nando, the categorizer - one node of the category tree per episode (§356).

docs/agent_rules/nando.md: pin every request to the single most specific
category it is truly about, before anything is researched; only nodes that
exist; by what it is about, not a word it contains; confirm after the
writer and change it if the episode turned out to be about something else;
record what every part of FAM said. The owner's decision (§346): code
first, folded into the brief's call, Haiku 5.5 only on ties; and (decided
item 38) the closest node the request is truly about - a broader node
rather than none, never a nearby wrong one.

This module is the code. It reads what the brief already worked out - EI's
category words, the resolved subject - before the question's raw words,
because the brief has already told "Miami heat wave" from the basketball
team and the raw words have not:

1. the brief's category words, by the tree's phrases (`resolve_category`);
2. the brief's subject, by phrases;
3. the question, by phrases - kept only when it agrees with the facet the
   brief named (a phrase in the question is how "hurricanes hitting the
   Carolina coast" became a hockey team, §209);
4. the brief's category words by meaning (`category_near`, the rung §235
   built for short category words, under the brief's facet);
5. the deepest tree node among the keyword map's tags for the question, on
   the same facet terms;
6. the facet the brief's category names - broader, never wrong.

Each step is a dictionary lookup or, at step 4, one embedding of a few
words; nothing here reads a file or the network, and it never raises. A
placement says which step decided it (`how`) and every node the steps
found (`candidates`), which is what a tie hands the model (piece 4) and
what `category_audit` logs.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

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


NOWHERE = Placement()


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


def place(brief, query: str = "", field: tuple = (),
          prior: Prior = NO_PRIOR) -> Placement:
    """The node this request is truly about, in code. Never raises.

    `field` is the facets a live game's or market's provider filed the
    story under (`provider_field`): Nando never files it anywhere else
    (nando.md, "never" 4) - a node outside them is passed over, and the
    provider's own deepest tag stands in.

    `prior` is what the tap already says (`prior_for`, §357): the words may
    narrow it - a node on its branch, below it - and never move it."""
    try:
        placed = _place(brief, query, _facet(prior.node) if prior.node else "")
        if prior.node:
            placed = _narrow(prior, placed)
        return _within(placed, field, query) if field else placed
    except Exception as exc:  # noqa: BLE001 - a category never costs an episode
        log.warning("nando could not place %r: %s", query, exc)
        return NOWHERE


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
                     candidates=placed.candidates)


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


def _narrow(prior: Prior, placed: Placement) -> Placement:
    """The words' node when it sits on the prior's branch at or below it,
    else the prior's node."""
    candidates = tuple(dict.fromkeys((prior.node,) + placed.candidates))
    if placed.node and (placed.node == prior.node or prior.node in _branch(placed.node)):
        return Placement(node=placed.node, path=placed.path,
                         how=placed.how if placed.node != prior.node else prior.source,
                         candidates=candidates)
    return Placement(node=prior.node, path=path_for(prior.node), how=prior.source,
                     candidates=candidates)


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
    if isinstance(given, Prior) and given.node:
        return given
    want = " ".join((query or "").split())
    if not want:
        return NO_PRIOR
    held = list(stories.pool().held())
    try:
        import trending_bank

        held += list(trending_bank.stories_now())
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
            if narrowed and node and (narrowed == node or node in _branch(narrowed)):
                node = narrowed
            if node:
                return Prior(node, "dailyfam")
    parent = _resolved(" ".join((context or "").split()))
    if parent:
        return Prior(parent, "parent")
    return NO_PRIOR


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

    def done(node: str, how: str) -> Placement:
        return Placement(node=node, path=path_for(node), how=how,
                         candidates=tuple(found))

    # Facet first (§357): the tap's, else the brief's words', else the
    # subject's. Every later step stays inside it, which is where the
    # cross-facet mistakes (the heat wave and the Heat) are stopped.
    by_words = seen(stories.resolve_category(words)) if words else ""
    by_subject = seen(stories.resolve_category(subject)) if subject else ""
    by_question = seen(stories.resolve_category(query)) if query else ""
    facet = prior_facet or (_facet(by_words) if by_words else "") or \
        (_facet(by_subject) if by_subject else "")

    def agrees(node: str) -> bool:
        return bool(node) and (not facet or stories.facet_for(node) == facet)

    # 1-3: phrases, the brief's words first. A facet alone from the words
    # is kept for step 6: a subject or question node under it is truer.
    if by_words and by_words not in topics.FACETS and agrees(by_words):
        return done(by_words, "category")
    if agrees(by_subject) and by_subject not in topics.FACETS:
        return done(by_subject, "subject")
    if agrees(by_question) and by_question not in topics.FACETS:
        return done(by_question, "question")
    # 4: the brief's words by meaning, under its facet.
    if words:
        import category_near

        near = seen(category_near.nearest(words, facet=facet, inline=1))
        if near:
            return done(near, "near")
    # 5: the keyword map's deepest tree node for the question.
    tagged = [t for t in topics.tags_for_text(query or "")
              if t not in topics.FACETS and tree.get(t) is not None and agrees(t)]
    if tagged:
        tagged.sort(key=lambda n: (-tree.depth_of(n), n))
        return done(seen(tagged[0]), "tags")
    # 6: broader, never wrong.
    if facet:
        return done(facet, "facet")
    for node in (by_subject, by_question):
        if node in topics.FACETS:
            return done(node, "facet")
    return Placement(candidates=tuple(found))


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

            written = category_near.nearest(words, inline=1)
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
                                  "node": node, "how": how}
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
