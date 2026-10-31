"""Draft each category node's definition, exclusions, other wordings and
Wikidata id, and the entity table - for a person to check (§359).

    AGENTS=1 CATEGORIZER=1 python tools/draft_category_vocabulary.py
    AGENTS=1 CATEGORIZER=1 python tools/draft_category_vocabulary.py --limit 40
    python tools/draft_category_vocabulary.py --dry-run     # what it would ask, sending nothing

Nando's offline task (`docs/FAM_AGENTS.md`, Nando; PROBLEMS.md §359): one
call per batch of nodes through `agents.models.ask` - the only way an agent
reaches a model ([agent-model-layer]) - on `CATEGORIZER_MODEL` (Haiku 5.5,
the owner's choice for this draft), under Nando's signed rule book, metered
in `agent_calls`. The switches are the owner's: this tool sends nothing
unless the deployment it runs on has `AGENTS=1` and `CATEGORIZER=1`, and
refuses on staging like every agent call.

What comes back is FAM's own material about its own tree - no listener's
words go out. Every answer is checked in code before it is kept: a node id
the tree does not hold is dropped, an entity filed under a node the tree
does not hold is dropped, wordings are normalised and capped, and a
Wikidata id is kept only when Wikidata's own label is the node's or the
entity's name exactly (`wikimedia`'s rule: ambiguity is no answer). The
result is `category_vocabulary.pending.json`, which Nando never reads; a
person reads it, edits it, and renames it `category_vocabulary.json`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
from typing import Literal

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from pydantic import BaseModel, ConfigDict, Field  # noqa: E402

BATCH = 25
MAX_WORDINGS = 15


class NodeWords(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str
    definition: str = Field(max_length=220)
    excludes: str = Field(max_length=220)
    wordings: list[str] = Field(max_length=MAX_WORDINGS)


class NodeDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: list[NodeWords]


class Entity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    #: A node id from the list given, or "none".
    node: str
    kind: Literal["person", "team", "company", "organisation", "place", "work", "other"]


class EntityDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entities: list[Entity]


NODE_INSTRUCTIONS = """You are drafting the vocabulary FAM files episodes \
with. For each category node in the material, write:

- definition: one plain sentence saying what an episode filed here is about.
- excludes: one plain sentence naming what looks similar but belongs \
elsewhere - especially other things with the same words ("extreme weather: \
not sports teams named after storms"). Empty if nothing is easily confused.
- wordings: up to fifteen other ways people say this subject in a question, \
lower case, each one to four words - synonyms, common short names, the \
everyday phrase ("rents", "rent prices", "renters" for rental market). Never \
a word that just as often means something else, never a person's name, \
never a result or a date.

Answer for every node given, using the node id exactly as given. If you are \
unsure of a wording, leave it out: a missing wording costs nothing, a wrong \
one files episodes in the wrong place."""

ENTITY_INSTRUCTIONS = """You are filing named things - teams, companies, \
people, organisations - under FAM's category tree. For each name in the \
request material, choose the single node from the evidence material's list \
that an episode about that name would most often be about, using the node id \
exactly as listed, or "none" if no node fits. A team goes under its league \
or the team's own node; a company under its industry; a person under what \
they are known for. Never invent a node. Say the kind of thing it is."""


def tree_lines(tree) -> list:
    import topics

    lines = []
    for node in sorted(tree.nodes()):
        if node in topics.FACETS:
            continue
        path = " > ".join(list(reversed(tree.ancestors(node))) + [node])
        lines.append((node, path))
    return lines


def entity_names() -> list:
    """Named things the tree should not grow nodes for: the teams the seed
    leaves out on purpose, and the specifics DailyFAM offers."""
    import category_seed
    import topics

    names = list(getattr(category_seed, "LEFT_TO_GROW", ()))
    for _noun, picks in getattr(topics, "FOCUS_HINTS", {}).values():
        names += list(picks)
    seen, out = set(), []
    for name in names:
        key = name.lower().strip()
        if key and key not in seen:
            seen.add(key)
            out.append(name)
    return out


def _request(task: str, schema, instructions: str, material: tuple, max_tokens: int):
    from agents.models import AgentRequest

    return AgentRequest(agent="categorizer", task=task, switch_setting="categorizer",
                        model_setting="categorizer_model", rule_book="nando.md",
                        schema=schema, instructions=instructions, material=material,
                        timeout=90.0, max_tokens=max_tokens, effort="low",
                        surface="background")


def clean_wordings(node: str, wordings) -> list:
    import categories

    out = []
    for w in wordings or ():
        w = categories.normalise(w)
        if w and w != node and 1 <= len(w.split()) <= 4 and w not in out:
            out.append(w)
    return out[:MAX_WORDINGS]


async def draft_nodes(tree, limit: int = 0) -> tuple:
    from agents import models

    lines = tree_lines(tree)[: limit or None]
    kept, problems = {}, []
    for start in range(0, len(lines), BATCH):
        batch = lines[start:start + BATCH]
        material = (("evidence", "\n".join(f"{n} | {p}" for n, p in batch)),)
        result = await models.ask(_request("draft vocabulary", NodeDraft, NODE_INSTRUCTIONS,
                                           material, 6000))
        if not result.ok:
            problems.append(f"nodes {start}-{start + len(batch)}: {result.status} {result.reason}")
            continue
        asked = {n for n, _p in batch}
        for entry in result.value.nodes:
            if entry.node not in asked or tree.get(entry.node) is None:
                problems.append(f"dropped an answer for {entry.node!r}: not asked or not in the tree")
                continue
            kept[entry.node] = {"definition": entry.definition.strip(),
                                "excludes": entry.excludes.strip(),
                                "wordings": clean_wordings(entry.node, entry.wordings)}
    return kept, problems


async def draft_entities(tree) -> tuple:
    from agents import models

    names = entity_names()
    listing = "\n".join(n for n, _p in tree_lines(tree))
    kept, problems = {}, []
    for start in range(0, len(names), 40):
        batch = names[start:start + 40]
        material = (("request", "\n".join(batch)), ("evidence", listing))
        result = await models.ask(_request("draft entities", EntityDraft,
                                           ENTITY_INSTRUCTIONS, material, 4000))
        if not result.ok:
            problems.append(f"entities {start}: {result.status} {result.reason}")
            continue
        for entity in result.value.entities:
            if entity.node == "none":
                continue
            if tree.get(entity.node) is None:
                problems.append(f"dropped {entity.name!r}: {entity.node!r} is not in the tree")
                continue
            kept[entity.name.lower().strip()] = {"node": entity.node, "kind": entity.kind}
    return kept, problems


def wikidata_id(label: str) -> str:
    """The Wikidata id whose English label is exactly `label`, or "" -
    none, several, or no answer (`wikimedia.pick`'s rule)."""
    try:
        import wikimedia

        found = wikimedia._wikidata({"action": "wbsearchentities", "search": label,
                                     "language": "en", "type": "item", "limit": 7,
                                     "format": "json"})
        hits = [h for h in (found or {}).get("search", ())
                if wikimedia.normalise(h.get("label", "")) == wikimedia.normalise(label)]
        return hits[0]["id"] if len(hits) == 1 else ""
    except Exception:  # noqa: BLE001 - an id is optional
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=0, help="only the first N nodes")
    parser.add_argument("--no-entities", action="store_true")
    parser.add_argument("--no-wikidata", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    import category_vocabulary
    import categories
    import topics

    tree = topics.category_tree()
    if not tree.nodes():
        categories.apply_seed(tree)
    lines = tree_lines(tree)[: args.limit or None]
    if args.dry_run:
        print(f"would ask about {len(lines)} nodes in {-(-len(lines) // BATCH)} calls, "
              f"and {len(entity_names())} named things, on CATEGORIZER_MODEL")
        return 0

    nodes, problems = asyncio.run(draft_nodes(tree, args.limit))
    entities = {}
    if not args.no_entities:
        entities, more = asyncio.run(draft_entities(tree))
        problems += more
    if not args.no_wikidata:
        for node, entry in nodes.items():
            qid = wikidata_id(node)
            if qid:
                entry["wikidata"] = qid
        for name, entry in entities.items():
            qid = wikidata_id(name)
            if qid:
                entry["wikidata"] = qid
    out = {"version": 1, "nodes": nodes, "entities": entities,
           "drafted": {"model_setting": "CATEGORIZER_MODEL", "problems": problems}}
    category_vocabulary.PENDING.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n",
                                           encoding="utf-8")
    print(f"wrote {category_vocabulary.PENDING.name}: {len(nodes)} nodes, "
          f"{len(entities)} named things, {len(problems)} problem(s)")
    for line in problems[:20]:
        print(f"  {line}")
    print("Read it, edit it, then rename it category_vocabulary.json - Nando never reads "
          "the pending file.")
    return 0 if nodes else 1


if __name__ == "__main__":
    sys.exit(main())
