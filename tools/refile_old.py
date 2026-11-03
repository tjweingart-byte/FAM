"""Re-file the episodes written before Nando: a list first, applied only after a yes (§428).

    python tools/refile_old.py                          # the dry run: writes refile_old.json/.md
    python tools/refile_old.py --out /tmp/refile.json   # somewhere else
    python tools/refile_old.py --apply refile_old.json --approve <sha256> [--dry-run]

The dry run changes nothing; it asks Nando's own small call for each episode
code could not file (as a new episode does), so it runs where he is switched
on - production - unless `--no-model` (code alone, free, on staging). It
prints the list's SHA-256; `--apply` refuses any other value, so what is
applied is exactly what was reviewed. Each change goes through
`category_tag.retag` (script row, audio metadata, archive together).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="refile_old.json")
    parser.add_argument("--apply", default="")
    parser.add_argument("--approve", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-model", action="store_true",
                        help="code alone, no call of Nando's: free, and much shorter")
    args = parser.parse_args(argv)

    import cache as cache_mod
    import refile_old

    # The tree as the server keeps it (seeded at boot); read, never written.
    cache = cache_mod.SqliteScriptCache()
    if args.apply:
        out = refile_old.apply(cache, args.apply, args.approve, dry_run=args.dry_run)
        if out.get("refused"):
            print(out["refused"])
            return 2
        verb = "would re-file" if args.dry_run else "re-filed"
        print(f"{verb} {out['applied']}; skipped {len(out['skipped'])}; "
              f"{len(out['failed'])} with a step that failed")
        for item in out["skipped"] + out["failed"]:
            print(f"    {item['key'][:50]}  {item['why']}")
        return 1 if out["failed"] else 0

    proposal = refile_old.propose(cache, model=not args.no_model)
    digest = refile_old.write_report(proposal, args.out)
    print(json.dumps({k: proposal[k] for k in ("episodes", "unchanged", "unplaced",
                                                "model", "calls")}))
    if proposal["model"] and proposal["calls"]["asked"] and not proposal["calls"]["answered"]:
        print("Nando's call never answered: is he switched on (AGENTS=1, CATEGORIZER=1),"
              " his rule book signed, and a key set? The list is code's alone.")
    print(f"{len(proposal['changes'])} would change - the list: {args.out} "
          f"(and {os.path.splitext(args.out)[0]}.md)")
    print(f"sha256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
