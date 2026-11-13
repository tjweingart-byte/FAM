"""The category tree, out to a reviewer and back as proposals (PROBLEMS.md §585).

The pronunciation round trip (§549) for the tree: download every node with
its path, where it came from and how many episodes are filed there, give it
to another reader (a Claude chat on high effort, with `PROMPT`) and upload
the edits that come back.

**An upload changes nothing.** It is read into proposals, each beside the
node as it stands, refused in words where it cannot apply, and an admin
accepts them one at a time or the high-confidence ones together. An
accepted edit is the owner's (`CategoryStore.owner_*`): a removed phrase is
never grown back by the sweep, the writer's words or the seed, and a node
the owner placed is never moved by the placer. That is why accepting is a
person's act, as it is for a respelling.
"""
from __future__ import annotations

import csv
import io
import re
from typing import Iterable

import categories

#: The columns of a download, in order.
EXPORT_COLUMNS = ("node", "path", "parent", "depth", "children", "source",
                  "episodes", "episodes_below", "listeners", "owner")

#: What a reviewer may ask for, and what `target` means for each.
ACTIONS = {
    "move": "the new parent: a facet or another node",
    "remove": "empty - what sat below it moves up to its parent",
    "merge": "the node it is the same subject as; its children go there too",
    "rename": "the new phrase, which takes its place and its children",
    "add": "the parent the new node goes under",
}
CONFIDENCES = ("high", "medium", "low")
MAX_UPLOAD_ROWS = 2000

_ACTION_WORDS = {"move": "move", "reparent": "move", "remove": "remove",
                 "delete": "remove", "drop": "remove", "merge": "merge",
                 "rename": "rename", "add": "add", "new": "add", "create": "add"}
_TARGET_HEADERS = ("target", "new_parent", "parent", "into", "to", "new_name")
_REASON_HEADERS = ("reason", "why", "notes")

PROMPT = """You are fixing the category tree of a news-audio app. Every episode is filed
under ONE node of this tree, and the tree decides the episode's picture, how it
is ranked for a listener's taste, and which category tab it appears in. It was
partly hand-seeded and partly grown automatically from what listeners searched
for, so it has mistakes. Think carefully about each branch before answering.

Attached is a CSV of every node. Columns:
  node            the node's phrase (its id; matched against text as a phrase, in order)
  path            facet > ... > node - where it sits now
  parent          its parent: one of the eight facets (sports, business, money, tech,
                  science, health, culture, world) or another node
  depth, children how deep it is and how many nodes sit directly below it
  source          seed (hand-written), owner (the app's owner), or grown: search, story,
                  interest, writer, model (the last placed by an AI)
  episodes        kept episodes filed at this node; episodes_below includes its branch
  listeners       how many people's searches grew it
  owner           an edit the owner already made here - leave these alone unless clearly wrong

Look for:
  - nodes under the wrong parent (a team under the wrong league or sport, a company
    under the wrong industry, a person under the wrong field, a sport under world)
  - duplicates of one subject under two phrases (merge the weaker into the stronger)
  - nodes that are not subjects: fragments of questions, dates, everyday phrases
    ("last week", "big win", "power grid" alone is fine but "lead" is not), a
    headline's wording, misspellings, a name that means two unrelated things
  - a missing level that would group many siblings (add it, then move them under it)
  - a branch in the wrong facet entirely

Return ONLY a CSV, in a code block, with exactly these columns:
  action,node,target,confidence,reason
  action is one of:
    move    node -> target is its new parent (a facet or an existing node)
    remove  node is not a category; target empty. Its children move up to its parent.
    merge   node is the same subject as target; node is removed and its phrase,
            episodes' words and children all go to target
    rename  node -> target is a better phrase for the same subject (lower case,
            1-4 words, each 3+ letters, no digits)
    add     node is a NEW phrase -> target is its parent. List an add BEFORE any row
            that moves something under it.
  confidence: high (certain, e.g. a team in the wrong league), medium, low
  reason: one short phrase, e.g. "Penguins are an NHL team, not NFL"
Only rows you would CHANGE. Spell node and target exactly as they appear in the
node column (or, for add/rename, the new phrase). Do not touch the eight facets.
"""


# --------------------------------------------------------------------------
# Out
# --------------------------------------------------------------------------

def export_rows(store, episodes: dict | None = None) -> list[dict]:
    """Every node, parents before children, as download rows."""
    episodes = dict(episodes or {})
    nodes = store.nodes()
    owner = getattr(store, "owner_decisions", dict)()
    kids: dict[str, list] = {}
    for node in nodes.values():
        kids.setdefault(node.parent_id, []).append(node.id)

    below_memo: dict[str, int] = {}

    def below(node_id: str, seen: frozenset = frozenset()) -> int:
        if node_id in below_memo:
            return below_memo[node_id]
        total = int(episodes.get(node_id, 0))
        for child in kids.get(node_id, ()):
            if child not in seen:
                total += below(child, seen | {node_id})
        below_memo[node_id] = total
        return total

    def path(node_id: str) -> str:
        chain = [node_id] + list(store.ancestors(node_id))
        top = nodes.get(chain[-1])
        if top is not None and top.parent_id:
            chain.append(top.parent_id)
        return " > ".join(reversed(chain))

    out = []
    for node in nodes.values():
        action, target = owner.get(node.id, ("", ""))
        out.append({"node": node.id, "path": path(node.id), "parent": node.parent_id,
                    "depth": node.depth, "children": len(kids.get(node.id, ())),
                    "source": node.source, "episodes": int(episodes.get(node.id, 0)),
                    "episodes_below": below(node.id), "listeners": node.listeners,
                    "owner": f"{action} {target}".strip() if action else ""})
    out.sort(key=lambda r: r["path"])
    return out


def export_csv(store, episodes: dict | None = None) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in export_rows(store, episodes):
        writer.writerow(row)
    return buf.getvalue()


# --------------------------------------------------------------------------
# Back
# --------------------------------------------------------------------------

def _unwrapped(text: str) -> str:
    """The CSV inside whatever a chat wrapped it in: a ``` fence, prose
    before the header."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    fence = re.search(r"```[a-zA-Z]*\n(.*?)```", text, re.S)
    if fence:
        text = fence.group(1)
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if re.match(r"\s*\"?action\"?\s*[,\t;]", line, re.I):
            return "\n".join(lines[i:])
    return text


def _pick(row: dict, headers: Iterable[str]) -> str:
    for h in headers:
        if row.get(h):
            return str(row[h]).strip()
    return ""


def read_upload(text: str, store) -> dict:
    """What an uploaded CSV proposes, checked against the tree as it is now.
    Changes nothing.

    Returns `{"proposals": [...], "refused": [...]}`, proposals in the
    file's order (an add comes before the moves that need it). A proposal is
    `{action, node, target, confidence, reason, parent, path, children,
    episodes, needs}`; a refusal is `{line, node, why}`."""
    body = _unwrapped(text)
    if not body.strip():
        raise ValueError("The file is empty.")
    try:
        dialect = csv.Sniffer().sniff(body.split("\n", 1)[0], delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(body), dialect=dialect)
    if not reader.fieldnames:
        raise ValueError("The file has no header row.")
    reader.fieldnames = [re.sub(r"[^a-z_]", "", (h or "").strip().lower().replace(" ", "_"))
                         for h in reader.fieldnames]
    if "action" not in reader.fieldnames or "node" not in reader.fieldnames:
        raise ValueError(
            "The file needs action and node columns (action,node,target,confidence,reason) - "
            f"it has {', '.join(h for h in reader.fieldnames if h) or 'none'}.")

    nodes = store.nodes()
    facets = categories._facet_slugs()
    exported = {r["node"]: r for r in export_rows(store)}
    # What the upload itself brings into being or takes away, in order, so a
    # later row may name a node an earlier one added.
    coming: set[str] = set()
    going: set[str] = set()
    seen: set[tuple] = set()

    def place_ok(name: str) -> bool:
        return name in facets or (name in nodes and name not in going) or name in coming

    proposals: list = []
    refused: list = []
    for n, row in enumerate(reader, start=1):
        line = reader.line_num
        if n > MAX_UPLOAD_ROWS:
            refused.append({"line": line, "node": "",
                            "why": f"Only the first {MAX_UPLOAD_ROWS} rows are read."})
            break
        raw_action = str(row.get("action") or "").strip().lower()
        node = categories.normalise(row.get("node") or "")
        target = categories.normalise(_pick(row, _TARGET_HEADERS))
        if not raw_action and not node:
            continue
        action = _ACTION_WORDS.get(raw_action)

        def refuse(why: str) -> None:
            refused.append({"line": line, "node": node[:60], "why": why})

        if action is None:
            refuse(f"'{raw_action}' is not an action ({', '.join(ACTIONS)})")
            continue
        if node in facets:
            refuse("the eight facets are fixed")
            continue
        if action == "add":
            if (node in nodes and node not in going) or node in coming:
                refuse("already in the tree - move it instead")
                continue
            if not place_ok(target):
                refuse(f"the parent '{target}' is not a facet or a category")
                continue
        else:
            if node not in nodes or node in going:
                refuse("not in the tree" + (" (an earlier row already removed it)"
                                            if node in going else ""))
                continue
            if action == "remove" and target:
                action = "merge"
            if action in ("move", "merge"):
                if not target:
                    refuse(f"{action} needs a target")
                    continue
                if not place_ok(target) or target == node:
                    refuse(f"'{target}' is not a facet or another category")
                    continue
                if target in nodes and node in store.ancestors(target):
                    refuse(f"'{target}' is below '{node}' - that would be a loop")
                    continue
                if action == "move" and nodes[node].parent_id == target:
                    continue  # already there
            if action == "rename":
                if not target or target == node:
                    refuse("rename needs a different phrase")
                    continue
                if target in nodes and target not in going:
                    refuse(f"'{target}' is already in the tree - merge into it instead")
                    continue
        if (action, node, target) in seen:
            continue
        seen.add((action, node, target))
        if action == "add" or action == "rename":
            coming.add(target if action == "rename" else node)
        if action in ("remove", "merge", "rename"):
            going.add(node)
        confidence = str(row.get("confidence") or "").strip().lower()
        here = exported.get(node, {})
        needs = target if target in coming and target not in nodes else ""
        proposals.append({
            "action": action, "node": node, "target": target,
            "confidence": confidence if confidence in CONFIDENCES else "",
            "reason": _pick(row, _REASON_HEADERS)[:200],
            "parent": here.get("parent", ""), "path": here.get("path", ""),
            "children": here.get("children", 0),
            "episodes": here.get("episodes_below", 0), "needs": needs})
    return {"proposals": proposals, "refused": refused}


def apply(store, action: str, node: str, target: str = "", reason: str = "") -> dict:
    """One accepted proposal, saved as the owner's. Raises ValueError."""
    action = _ACTION_WORDS.get(str(action or "").strip().lower())
    if action == "move":
        store.owner_move(node, target, reason)
        return {"ok": True, "said": f"moved under {categories.normalise(target)}"}
    if action == "remove":
        moved = store.owner_remove(node, "", reason)
        return {"ok": True, "said": "removed" + (f"; {moved} below it moved up" if moved else "")}
    if action == "merge":
        moved = store.owner_remove(node, target, reason)
        return {"ok": True, "said": f"merged into {categories.normalise(target)}"
                + (f" with {moved} below it" if moved else "")}
    if action == "rename":
        store.owner_rename(node, target, reason)
        return {"ok": True, "said": f"renamed {categories.normalise(target)}"}
    if action == "add":
        store.owner_add(node, target, reason)
        return {"ok": True, "said": f"added under {categories.normalise(target)}"}
    raise ValueError(f"'{action}' is not an action ({', '.join(ACTIONS)})")
