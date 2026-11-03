"""Show (or compute) the totals about groups of listeners (PROBLEMS.md §415).

    python tools/demographic_totals.py               # the latest week kept
    python tools/demographic_totals.py --week 2026-11-02
    python tools/demographic_totals.py --compute     # this week and last, now

Only cells of 100 or more listeners exist; a breakdown with any smaller group
is not kept at all. Nothing here is about anyone. `--compute` reads the
accounts, the consent, the place and the event log in place, as the nightly
task does.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--week", default="")
    parser.add_argument("--compute", action="store_true")
    args = parser.parse_args(argv)

    import demographic_totals as dt

    if args.compute:
        import accounts
        import consent
        import preferences
        import topics

        print(dt.run(sources={"accounts": accounts.AccountStore(),
                              "consent": consent.ConsentStore(),
                              "prefs": preferences.PreferenceStore(),
                              "events": topics.EventStore()}))
    if not dt.exists():
        print("no totals yet")
        return 0
    weeks = dt.store().weeks()
    week = args.week if args.week in weeks else (weeks[0] if weeks else "")
    cells = dt.store().week(week) if week else []
    print(f"week of {week or '-'}: {len(cells)} cells kept (groups of {dt.MIN_GROUP}+ only)")
    for c in cells:
        print(f"  {c['story']:10} {c['breakdown']:7} {c['group'] or '-':28} "
              f"{c['listeners']:6} listeners  {c['searches']:6} searches  {c['plays']:6} plays")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
