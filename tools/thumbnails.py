"""Paint, review and inspect the tile pictures (§160, THUMBNAILS.md).

    python tools/thumbnails.py plan                      # what is missing, and what painting it costs - spends nothing
    python tools/thumbnails.py scenes --limit 20         # the scenes Claude would write - one cheap call, no images
    python tools/thumbnails.py run --limit 20            # paint the next 20 nodes, broadest first
    python tools/thumbnails.py run --node "college football" --regenerate
    python tools/thumbnails.py run --retry-failed --limit 10
    python tools/thumbnails.py status                    # counts, spend, and what is waiting for a person
    python tools/thumbnails.py approve "nfl"             # put a held picture on tiles
    python tools/thumbnails.py reject "nfl"              # take one off them
    python tools/thumbnails.py export ./thumbs           # write every picture to a folder to look at

Runs against this machine's databases (`THUMBNAILS_DB`, `CATEGORIES_DB`).
Against a deployment, use `/admin/thumbnails` instead - it is the same code
behind an admin sign-in.

`run` respects the daily image ceiling (`THUMBNAILS_DAILY_IMAGES`) unless
`--ignore-daily-cap` is given, which is what the first seeding of the whole
tree wants: 188 nodes is one sitting, not three days.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import categories as cat  # noqa: E402
import thumbnails as th  # noqa: E402
import topics as topics_mod  # noqa: E402
from config import settings  # noqa: E402


def _tree():
    tree = topics_mod.category_tree()
    # The same idempotent seed the server applies at boot, so a machine that
    # has never run the server still has the 180 starter nodes to paint.
    cat.apply_seed(tree)
    return tree


def cmd_plan(args) -> int:
    tree = _tree()
    missing = th.wanted(tree, th.store(), retry_failed=args.retry_failed)
    est = th.estimate(len(missing))
    ok, why = th.configured()
    out = {"missing": len(missing), "estimate": est,
           "daily_room": th.daily_room(th.store()),
           "configured": ok, "reason": why,
           "first": [th.path_text(tree, n) for n in missing[:15]]}
    if args.json:
        print(json.dumps(out, indent=2))
        return 0
    print(f"{len(missing)} nodes have no picture.")
    print(f"Painting them should cost about ${est['total_usd']} "
          f"(${est['images_usd']} images + ${est['claude_usd']} Claude, "
          f"~{est['expected_images']} images at {settings.thumbnails_expected_attempts} "
          f"tries each; {est['basis']}).")
    print(f"Room under today's ceiling: {out['daily_room']} images.")
    if not ok:
        print(f"Cannot paint here yet: {why}.")
    for p in out["first"]:
        print("  ", p)
    if len(missing) > 15:
        print(f"   ... and {len(missing) - 15} more")
    return 0


def cmd_scenes(args) -> int:
    tree = _tree()
    nodes = args.node or th.wanted(tree, th.store(), regenerate=True)[:args.limit]
    paths = [th.path_text(tree, n) for n in nodes]
    scenes = asyncio.run(th.claude_writer(paths))
    for s in scenes:
        leak = " [NAMES THE SUBJECT - would not be painted]" \
            if th.names_leak(s.node, s.scene, s.flagged) else ""
        flag = " (held for review)" if s.flagged else ""
        print(f"{s.node}{flag}{leak}\n    {s.scene}\n")
    return 0


def cmd_run(args) -> int:
    tree = _tree()
    ok, why = th.configured()
    if not ok:
        print(f"Cannot paint: {why}.", file=sys.stderr)
        return 2
    todo = th.wanted(tree, th.store(), regenerate=args.regenerate,
                     retry_failed=args.retry_failed, only=args.node or ())
    todo = todo[:args.limit]
    est = th.estimate(len(todo))
    print(f"Painting {len(todo)} nodes, about ${est['total_usd']}.")
    result = asyncio.run(th.backfill(
        args.limit, only=args.node or (), regenerate=args.regenerate,
        retry_failed=args.retry_failed,
        ignore_daily_cap=args.ignore_daily_cap, tree=tree))
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    for row in result.get("nodes", []):
        print(f"  {row['status']:9} {row['node']}"
              + (f"  - {row['reason']}" if row.get("reason") else ""))
    for err in result.get("errors", []):
        print("  error:", err)
    if result.get("skipped"):
        print("Skipped:", result["skipped"])
    if result.get("stopped"):
        print("Stopped (nothing recorded against the nodes):", result["stopped"])
    if result.get("capped"):
        print("Stopped at the daily ceiling (THUMBNAILS_DAILY_IMAGES).")
    print(f"approved {result.get('approved', 0)}, waiting for review "
          f"{result.get('review', 0)}, failed {result.get('failed', 0)}; "
          f"spent ~${result.get('spend_usd', 0)}")
    return 0


def cmd_status(args) -> int:
    health = th.health()
    rows = [t.as_dict() for t in th.store().all()] if th._exists() else []
    if args.json:
        print(json.dumps({"health": health, "thumbnails": rows}, indent=2))
        return 0
    c = health.get("counts", {})
    print(f"live {c.get('approved', 0)}, waiting {c.get('review', 0)}, "
          f"failed {c.get('failed', 0)}, rejected {c.get('rejected', 0)}")
    print(f"spent ~${health.get('spend_usd', 0)} on "
          f"{health.get('images_generated', 0)} images; "
          f"{health.get('images_last_24h', 0)} in the last 24h")
    for r in rows:
        if r["status"] in (th.STATUS_REVIEW, th.STATUS_FAILED):
            print(f"  {r['status']:8} {r['node']}  - {r['reason']}")
    return 0


def cmd_decide(args, status: str) -> int:
    if not th._exists() or th.store().get(args.node) is None:
        print(f"No picture for {args.node!r}.", file=sys.stderr)
        return 1
    if status == th.STATUS_APPROVED and th.store().image(
            args.node, any_status=True) is None:
        print(f"{args.node!r} has no picture to approve; repaint it.",
              file=sys.stderr)
        return 1
    th.store().set_status(args.node, status,
                          "" if status == th.STATUS_APPROVED
                          else "rejected by a person")
    print(f"{args.node}: {status}")
    return 0


def cmd_export(args) -> int:
    os.makedirs(args.dir, exist_ok=True)
    count = 0
    for t in th.store().all() if th._exists() else []:
        found = th.store().image(t.node_id, any_status=True)
        if not found:
            continue
        ext = {"image/webp": "webp", "image/jpeg": "jpg"}.get(found[1], "png")
        name = f"{t.status}-{t.node_id.replace(' ', '_').replace('/', '_')}.{ext}"
        with open(os.path.join(args.dir, name), "wb") as fh:
            fh.write(found[0])
        count += 1
    print(f"wrote {count} pictures to {args.dir}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan")
    p.add_argument("--retry-failed", action="store_true")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("scenes")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--node", action="append")

    p = sub.add_parser("run")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--node", action="append")
    p.add_argument("--regenerate", action="store_true")
    p.add_argument("--retry-failed", action="store_true")
    p.add_argument("--ignore-daily-cap", action="store_true")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("status")
    p.add_argument("--json", action="store_true")

    for name in ("approve", "reject"):
        p = sub.add_parser(name)
        p.add_argument("node")

    p = sub.add_parser("export")
    p.add_argument("dir")

    args = parser.parse_args(argv)
    if args.cmd == "plan":
        return cmd_plan(args)
    if args.cmd == "scenes":
        return cmd_scenes(args)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "approve":
        return cmd_decide(args, th.STATUS_APPROVED)
    if args.cmd == "reject":
        return cmd_decide(args, th.STATUS_REJECTED)
    return cmd_export(args)


if __name__ == "__main__":
    raise SystemExit(main())
