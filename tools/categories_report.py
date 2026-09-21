"""What the ranking vocabulary has grown into.

    python tools/categories_report.py              # the tree as it stands
    python tools/categories_report.py --tree       # drawn as a tree
    python tools/categories_report.py --dry-run    # what one sweep would mint
    python tools/categories_report.py --json

`topics.py` ships with eight facets and twenty-nine subtags, all hand-written.
`categories.py` grows the rest from what listeners actually search for, and
the only way to judge whether that is working is to look at it: a vocabulary
full of real subjects is doing its job, and one full of sentence fragments is
ranking on noise.

`--dry-run` is the important one. It runs the promotion pass against this
machine's event log and prints what *would* be minted **without writing
anything and without calling a model**, so a deployment can see what the
sweep is about to do to its vocabulary before it does it. That matters more
here than in most places: a node is shared by every listener, so a bad batch
is a bad batch for everybody at once.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import categories as cat  # noqa: E402
import topics as topics_mod  # noqa: E402
from config import settings  # noqa: E402


def _children(nodes: dict) -> dict[str, list]:
    out: dict[str, list] = {}
    for node in nodes.values():
        out.setdefault(node.parent_id, []).append(node)
    for kids in out.values():
        kids.sort(key=lambda n: (-n.listeners, n.id))
    return out


def draw(store: cat.CategoryStore) -> None:
    """The tree, indented. Roots are the facets plus anything with no parent."""
    nodes = store.nodes()
    kids = _children(nodes)
    roots = sorted(set(kids) - set(nodes))

    def walk(parent: str, depth: int) -> None:
        for node in kids.get(parent, ()):
            mark = "~" if node.degraded else " "
            print(f"  {'  ' * depth}{mark} {node.label}"
                  f"   ({node.listeners} listener(s), {node.source})")
            walk(node.id, depth + 1)

    for root in roots:
        label = topics_mod.TAG_LABELS.get(root, root or "(no parent)")
        print(f"  {label}")
        walk(root, 1)
    print()
    print("  ~ means the node has not been placed by a model - it is where")
    print("    containment or a facet vote put it. See CATEGORIES_PLACE.")


def dry_run(store: cat.CategoryStore, days: int) -> list:
    """What one sweep would mint, writing nothing.

    A real promotion pass against a throwaway copy of the tree, so the
    containment parents it reports are the ones the real sweep would choose.
    Nothing here calls a model: `place` is the only thing that does, and it
    is not run.
    """
    import tempfile

    events = topics_mod.EventStore()
    texts = events.subject_texts(time.time() - days * 86400)
    scratch = cat.CategoryStore(
        os.path.join(tempfile.mkdtemp(), "dry-run.db"))
    for node in store.nodes().values():
        scratch.mint(node.id, node.parent_id, node.source,
                     node.listeners, node.uses, node.first_seen)
    return cat.promote(scratch, texts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tree", action="store_true",
                        help="draw the hierarchy rather than summarise it")
    parser.add_argument("--dry-run", action="store_true",
                        help="what one sweep would mint, writing nothing")
    parser.add_argument("--days", type=int,
                        default=settings.categories_window_days)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    store = cat.CategoryStore()
    report = store.report()

    if args.json:
        body = dict(report)
        if args.dry_run:
            body["would_mint"] = [n.as_dict() for n in dry_run(store, args.days)]
        print(json.dumps(body, indent=2))
        return 0

    print(f"{report['path']}")
    state = "growing" if settings.categories else "off (CATEGORIES=0)"
    placed = ("placed by a model" if settings.categories_place
              else "keyless (CATEGORIES_PLACE=0)")
    print(f"  {state}, {placed}")
    print()
    if not report["nodes"]:
        # Four ways to be empty and they are not the same thing. Saying "the
        # vocabulary is not working" here would be a claim about the code made
        # from a fact about a fresh database.
        print("  The tree is empty.")
        print("  Either nothing has been swept yet, or fewer than"
              f" {cat.MIN_LISTENERS} different listeners have used any one")
        print("  subject. The hand-written facets and subtags are unaffected -")
        print("  the feed ranks on those exactly as it did before this existed.")
    else:
        print(f"  {report['nodes']} nodes, {report['max_depth'] + 1} levels deep")
        print(f"  by depth:  {report['by_depth']}")
        print(f"  by source: {report['by_source']}")
        print(f"  {report['degraded']} not yet placed by a model")
        if report["full"]:
            print(f"  FULL at {cat.MAX_NODES} nodes - the least used stop"
                  " being refreshed")
        print()
        if args.tree:
            draw(store)

    if args.dry_run:
        print()
        would = dry_run(store, args.days)
        print(f"  one sweep over the last {args.days} days would mint"
              f" {len(would)} node(s):")
        for node in would:
            under = node.parent_id or "(no parent yet)"
            print(f"    {node.label:34} under {under:22}"
                  f" {node.listeners} listener(s)")
        if not would:
            print("    nothing - no phrase has enough different listeners"
                  " behind it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
