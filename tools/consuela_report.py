"""What Consuela chose, article by article (PROBLEMS.md §399).

    python tools/consuela_report.py            # the latest 20
    python tools/consuela_report.py --limit 100
    python tools/consuela_report.py --json
    python tools/consuela_report.py --merges   # her dedupe's merges and conflicts (§401)

Her dry run's whole output: for each intake article, whether she chose
sentences (and which, with each figure's status) or held it, and why. Reads
`housekeeper.db` only; sends nothing, calls no model.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--merges", action="store_true",
                        help="her dedupe's merges and conflicts, and its last eval")
    args = parser.parse_args(argv)

    import db as store_db
    from agents import housekeeper
    from paths import data_path

    if not store_db.has_tables(data_path("HOUSEKEEPER_DB", "housekeeper.db")):
        print("Consuela has not run here: no housekeeper.db.")
        return 0
    if args.merges:
        from agents import dedupe

        rows = housekeeper.store().recent_merges(args.limit)
        if args.json:
            print(json.dumps(rows, indent=2))
            return 0
        passed, why = dedupe.eval_passed()
        print(f"eval for {dedupe.models_key()}: {'passed' if passed else why}")
        for row in rows:
            when = datetime.fromtimestamp(row["at"], timezone.utc).strftime("%Y-%m-%d %H:%M")
            print(f"\n{when}  {row['mode']}  {row['verdict']}  ({row['why']})")
            print(f"    keep:   {row['keeper_text']}")
            print(f"    repeat: {row['repeat_text']}")
        return 0
    rows = housekeeper.store().recent(args.limit)
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    print(f"counts: {housekeeper.store().counts()}")
    for row in rows:
        when = datetime.fromtimestamp(row["at"], timezone.utc).strftime("%Y-%m-%d %H:%M")
        print(f"\n{when}  {row['mode']}  {row['outcome']}  {row['url']}")
        if row["why"]:
            label = "held" if row["outcome"] == "held" else "note"
            print(f"    {label}: {row['why']}")
        for item in row["kept"]:
            status = (item.get("figure") or {}).get("status", "")
            print(f"    [{item['kind']}{' ' + status if status else ''}] {item['text']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
