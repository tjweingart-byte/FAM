"""Which sentences listeners stop at (§502).

    python tools/drop_off.py                 # the last 30 days
    python tools/drop_off.py --days 60 --min-reached 20 --top 40

Reads the quality store (`episode_quality.drop_off`): for every sentence,
of the listeners who reached it, the share who stopped there and heard
nothing after. Prints the worst sentences (with the ear checks each failed),
the opening / middle / landing, and, per ear check, flagged sentences
against passed ones - whether listeners act on the playbook's rules for the
ear. Positions come from the kept audio's timings where kept, else words.
No key, no network, no person's id.

A stop is not always the sentence's fault - a phone rings - so read the
rates over many listeners, never one episode's.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _rate(value) -> str:
    return "-" if value is None else f"{value:.1%}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=float, default=30.0)
    ap.add_argument("--min-reached", type=int, default=None)
    ap.add_argument("--top", type=int, default=None)
    args = ap.parse_args()
    import episode_quality

    found = episode_quality.drop_off(
        days=args.days,
        min_reached=(args.min_reached if args.min_reached is not None
                     else episode_quality.DROP_MIN_REACHED),
        top=args.top if args.top is not None else episode_quality.DROP_TOP)
    if not found["episodes"]:
        print(f"no listened episodes in the last {args.days:g} days")
        return 0
    print(f"{found['episodes']} episodes, {found['listens']} listens, "
          f"{found['measured']} with measured timings")
    print("\nwhere they stop:")
    for place, entry in found["by_place"].items():
        print(f"  {place:8s} {_rate(entry['exit_rate']):>7s} of {entry['reached']} reached")
    if found["by_ear_check"]:
        print("\nby ear check (flagged against passed):")
        for check, entry in found["by_ear_check"].items():
            print(f"  {check:13s} {_rate(entry['flagged']['exit_rate']):>7s} "
                  f"({entry['flagged']['reached']})  vs  "
                  f"{_rate(entry['passed']['exit_rate']):>7s} ({entry['passed']['reached']})")
    print("\nthe sentences most stopped at:")
    for s in found["worst"]:
        flags = f"  [{', '.join(s['ear'])}]" if s["ear"] else ""
        print(f"  {s['exit_rate']:.0%} of {s['reached']}  sentence {s['position'] + 1}/{s['of']}"
              f"  - {s['query']}{flags}\n      {s['sentence']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
