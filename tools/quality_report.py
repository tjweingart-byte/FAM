"""Episode quality by category, from the command line (PROBLEMS.md §306).

    python tools/quality_report.py                  # worst-costing nodes, 30 days
    python tools/quality_report.py --days 7 --all   # every node, thin ones too
    python tools/quality_report.py --json           # the report /admin/quality reads
    python tools/quality_report.py --grade 20       # grade the 20 newest ungraded
    python tools/quality_report.py --label KEY.SOURCED hook=4 flow=3

The same report as `/admin/quality`, read from `EPISODE_QUALITY_DB` on this
machine. `--grade` spends: it calls `QUALITY_GRADER_MODEL` once per episode,
inside `QUALITY_GRADE_DAILY`, and needs an ANTHROPIC_API_KEY - nothing in
the build container has one, so it says so rather than reporting nothing.
`--label` is the owner's own score, 1-5, for one metric of one episode; the
grader is trusted on a metric only once it agrees with enough of these.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import episode_quality as Q  # noqa: E402


def _cell(value) -> str:
    return "  -  " if value is None else f"{value:5.2f}"


def _pct(value) -> str:
    return "-" if value is None else f"{value:.0%}"


def show(report: dict, every: bool) -> None:
    print(f"{report['episodes']} episodes in the last {report['days']:g} days\n")
    nodes = report["nodes"] if every else [n for n in report["nodes"] if n["enough"]]
    if not nodes:
        print("  nothing with enough episodes yet"
              + ("" if every else " - --all shows the thin nodes"))
    head = (f"  {'node':<34}{'eps':>5}{'overall':>8}{'vs head':>8}"
            + "".join(f"{Q.LABELS[m][:10]:>11}" for m in Q.METRICS)
            + f"{'out<20s':>9}{'finished':>9}")
    print(head)
    for n in nodes:
        name = (" " * n["depth"] if every else "") + n["label"]
        short = "" if n["shortfall"] is None else f"{-n['shortfall']:+.2f}"
        print(f"  {name[:34]:<34}{n['episodes']:>5}{_cell(n['overall']):>8}"
              f"{short:>8}"
              + "".join(f"{_cell(n['scores'][m]['code']):>11}" for m in Q.METRICS)
              + f"{_pct(n['early_exit']):>9}{_pct(n['finished']):>9}")
        for worst in n["worst"][:1]:
            print(f"      weakest: {worst['title'] or worst['query']}  "
                  f"({worst['overall']:.2f})  {worst['episode']}")
    print("\n  grader calibration (your scores vs the grader's):")
    for metric, c in report["calibration"].items():
        print(f"    {Q.LABELS[metric]:<20} labelled {c['labelled']:>3}  paired {c['n']:>3}"
              f"  within one {_pct(c['within_one'])}"
              f"  {'TRUSTED' if c['trusted'] else 'not yet trusted'}")
    print("\n  does it predict how much was heard (Spearman)?")
    for metric, v in report["validity"].items():
        code, grade = v["code"], v["grade"]
        print(f"    {Q.LABELS[metric]:<20} code {code['predicts_heard']} (n={code['n']})"
              f"   grade {grade['predicts_heard']} (n={grade['n']})")
    g = report["grading"]
    print(f"\n  grading: {g['mode']} on {g['model']} ({g['effort']}), "
          f"{g['graded_today']} today of {g['daily_ceiling']}, ${g['spent_today']}")
    # Where each episode was placed before writing, against the writer's own
    # category, and which examples shaped which episodes (§307).
    placed = report.get("placement") or {}
    if placed:
        print("\n  placed before writing vs the writer's category: "
              + ", ".join(f"{k} {v}" for k, v in placed.items()))
    for miss in report.get("misplaced") or []:
        print(f"    placed on {miss['planned']!r}, writer said {miss['written']!r}"
              f" ({miss['episodes']})")
    if report.get("examples"):
        print("\n  examples: episodes shown each, and how they scored")
    for ex in report.get("examples") or []:
        print(f"    {ex['file']:<32}{ex['episodes']:>5}{_cell(ex['overall']):>8}  "
              + ", ".join(ex["nodes"]))


async def grade(limit: int) -> int:
    import credentials
    from anthropic_client import build_async_client

    key = credentials.active("ANTHROPIC_API_KEY")
    if not key:
        print("cannot grade: no ANTHROPIC_API_KEY on this machine")
        return 1
    done = await Q.grade_pending(build_async_client(key), limit)
    print(f"graded {done} of up to {limit}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=30.0)
    ap.add_argument("--all", action="store_true", help="show thin nodes too")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--grade", type=int, default=0, metavar="N",
                    help="grade the N newest ungraded episodes (spends)")
    ap.add_argument("--label", nargs="+", metavar=("EPISODE", "METRIC=SCORE"),
                    help="your own 1-5 scores for one episode")
    args = ap.parse_args()

    if args.grade:
        return asyncio.run(grade(args.grade))
    if args.label:
        episode, *pairs = args.label
        for pair in pairs:
            metric, _, score = pair.partition("=")
            if not Q.label(episode, metric.strip(), score.strip()):
                print(f"not kept: {pair} (metrics: {', '.join(Q.METRICS)}; 1-5)")
                return 1
        print(f"kept {len(pairs)} score{'s' if len(pairs) != 1 else ''} for {episode}")
        return 0
    if not Q.exists():
        print("no episodes measured on this machine yet "
              "(EPISODE_QUALITY_DB is created by the first written episode)")
        return 0
    report = Q.report(args.days)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        show(report, args.all)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
