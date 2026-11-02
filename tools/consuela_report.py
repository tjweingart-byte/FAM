"""What Consuela chose, article by article (PROBLEMS.md §399).

    python tools/consuela_report.py            # the latest 20
    python tools/consuela_report.py --limit 100
    python tools/consuela_report.py --json

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
    args = parser.parse_args(argv)

    import db as store_db
    from agents import housekeeper
    from paths import data_path

    if not store_db.has_tables(data_path("HOUSEKEEPER_DB", "housekeeper.db")):
        print("Consuela has not run here: no housekeeper.db.")
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
            print(f"    held: {row['why']}")
        for item in row["kept"]:
            status = (item.get("figure") or {}).get("status", "")
            print(f"    [{item['kind']}{' ' + status if status else ''}] {item['text']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
