"""Decided item 37's two weeks: editions with and without Shakespeare (§454).

    python tools/editor_trial.py            # the last 14 days
    python tools/editor_trial.py --days 21

Reads the quality store (`episode_quality.editor_trial`): every episode
written ahead of the tap while `EDITOR_TRIAL=1` carries its arm - with
Shakespeare's plan, or left without it by a hash of its question. Compared
on listening - left in the first 20 seconds, heard 85%, the mean share
heard - and on the six code measures. First, for every episode he planned
(trial or not), whether the writer followed his plan (§521): opening, parts,
order, landing. No key, no network, no person's id.

Read with the listens: a few hundred listens an arm tells little; a
5-point gap in early exits needs on the order of a thousand an arm.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=14.0)
    args = ap.parse_args()
    import episode_quality

    followed = episode_quality.plan_following_report(days=args.days)["surfaces"]
    for surface, entry in followed.items():
        # Whether the writer followed his plan (§521), every episode he planned.
        parts = ", ".join(f"{p} {'-' if entry[p] is None else f'{entry[p]:.2f}'}"
                          for p in episode_quality.PLAN_PARTS + ("score",))
        print(f"plan followed, {surface} ({entry['episodes']} episodes): {parts}")
    result = episode_quality.editor_trial(days=args.days)
    arms = result["arms"]
    if not arms:
        print(f"no trial episodes in the last {args.days:g} days "
              "(is EDITOR_TRIAL=1 with AGENTS=1 EDITOR_AGENT=1?)")
        return 0
    names = [a for a in ("with", "without") if a in arms]

    def cell(value, pct=False):
        if value is None:
            return "-"
        return f"{value:.1%}" if pct else f"{value:.3f}" if isinstance(value, float) else str(value)

    print(f"last {args.days:g} days" + "".join(f"{n:>14s}" for n in names))
    for label, key, pct in (("episodes", "episodes", False), ("listens", "listens", False),
                            ("left in 20 s", "early_exit", True),
                            ("heard 85%", "finished", True), ("share heard", "heard", True)):
        print(f"{label:16s}" + "".join(f"{cell(arms[n][key], pct):>14s}" for n in names))
    for metric in episode_quality.METRICS:
        print(f"code: {metric:10s}" + "".join(f"{cell(arms[n]['code'][metric]):>14s}"
                                              for n in names))
    if result["unplanned"]:
        print(f"({result['unplanned']} 'with' episodes he was asked about but gave no plan)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
