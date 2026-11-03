"""Check that every filed episode's tag agrees everywhere, and repair it (§405).

    python tools/tag_sync.py                 # check every filed episode, list the drift
    python tools/tag_sync.py --repair        # then push each row's node to R2 and the archive
    python tools/tag_sync.py --repair --dry-run

The script row is the record: the bucket's metadata and the archive's
record are made to agree with it (`category_tag.push`). A row naming a node
the tree no longer holds is listed, never repaired here - which node
replaces it is a re-file, Consuela's. Reads with HEAD; writes only with
--repair.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--page", type=int, default=100000)
    args = parser.parse_args(argv)

    import cache as cache_mod
    import tag_sync

    cache = cache_mod.SqliteScriptCache()
    summary = tag_sync.run(cache, page=args.page, after="", record=False)
    if summary.get("why"):
        print(f"not checked: {summary['why']}")
        return 2
    print(f"{summary['checked']} filed episodes checked")
    for kind, count in summary["drift"].items():
        if count:
            print(f"  {count:6}  {kind}")
    for finding in summary["findings"][:200]:
        print(f"    {finding['kind']:20} {finding['key'][:50]}  {finding['detail']}")
    if not summary["findings"]:
        print("everything agrees")
        return 0
    if args.repair:
        results = tag_sync.repair(cache, summary["findings"], dry_run=args.dry_run)
        failed = [r for r in results if r["failed"]]
        print(f"\n{'would repair' if args.dry_run else 'repaired'} {len(results)} "
              f"episode(s); {len(failed)} with a step that failed")
        for r in failed:
            print(f"    {r['key'][:50]}: {'; '.join(r['failed'])}")
        return 1 if failed else 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
