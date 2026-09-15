"""What prefetch would warm, and whether it is paying for itself.

    python tools/prefetch_report.py                 # what it would warm, right now
    python tools/prefetch_report.py --listener abc  # for one listener
    python tools/prefetch_report.py --live          # ask a running server instead

Two questions, and they need different answers.

**"What would it warm?"** runs the candidate sources against this machine's
stores and prints the plan without spending anything. Sources are forbidden to
call a model or the network, so this is free and can be run against production
data to see what a deployment *would* do before switching `PREFETCH=1` on.

**"Is it paying?"** is the hit rate, and it only exists on a server that has
actually been warming - the ledger is in that process. `--live` reads it from
`/api/health`. It is the number CLAUDE.md's open question turns on: every
speculative script costs money and every one not fetched costs a wait, and
nothing but the per-source hit rate says where the line is.

There is no command here that warms anything. Spending money is the server's
job, under its budget; a tool that could spend outside that budget would be a
second place the ceiling has to be enforced.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import prefetch  # noqa: E402
import prefetch_sources  # noqa: E402


def show_plan(listener: str) -> int:
    import mixes as mixes_mod
    import topics as topics_mod
    from config import settings

    installed = prefetch_sources.install(
        event_store=topics_mod.EventStore(), mix_store=mixes_mod.MixStore())
    print(f"sources: {', '.join(installed) or 'none'}")
    print(f"level:   {settings.prefetch_level}"
          f"   ·   per cycle: {settings.prefetch_per_cycle}"
          f"   ·   {'ON' if settings.prefetch else 'OFF (PREFETCH=0)'}")
    print(f"budget:  {settings.prefetch_daily_episodes} episodes / "
          f"${settings.prefetch_daily_dollars:.2f} a day\n")

    worker = prefetch.Prefetcher()
    plan = worker.plan(listener)
    if not plan:
        print("nothing to warm. With no listener only trending answers, and a "
              "cold event log has no plays in it yet - seed one with "
              "`python tools/seed_demo.py`.")
        return 0

    print(f"would warm {len(plan)} episode(s)"
          + (f" for {listener}" if listener else " (shared, no listener)") + ":\n")
    width = max(len(c.source) for c in plan)
    for rank, candidate in enumerate(plan, 1):
        print(f"  {rank:>2}. [{candidate.source:<{width}}] {candidate.query}")
        print(f"      {candidate.minutes} min  ·  {candidate.reason}")
    print("\nNothing was spent: the sources are forbidden to call a model or "
          "the network, which is what makes this safe to run anywhere.")
    return 0


def show_live(base: str) -> int:
    url = base.rstrip("/") + "/api/health"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            health = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"could not read {url}: {exc}")
        return 2

    report = health.get("prefetch") or {}
    if not report:
        print("that server reports no prefetch section - it is running code "
              "from before prefetch existed.")
        return 1

    print(f"prefetch: {'ON' if report.get('enabled') else 'OFF'}"
          f"   ·   level {report.get('level')}"
          f"   ·   sources: {', '.join(report.get('sources') or []) or 'none'}")
    if not report.get("built"):
        print("no prefetcher has been built in that process yet, so there is "
              "nothing to report on.")
        return 0

    budget = report.get("budget") or {}
    print(f"budget:   {budget.get('episodes_used')}/{budget.get('max_episodes')} "
          f"episodes, ${budget.get('dollars_used', 0):.3f}/"
          f"${budget.get('max_dollars', 0):.2f} today")

    warmed, taken = report.get("warmed", 0), report.get("taken", 0)
    rate = report.get("hit_rate")
    print(f"\nwarmed {warmed}, taken {taken}, hit rate "
          + ("no data yet" if rate is None else f"{rate:.0%}"))
    if report.get("skipped_already_cached"):
        print(f"  {report['skipped_already_cached']} already in the cache "
              "(somebody else had already paid for them)")
    if report.get("failures"):
        print(f"  {report['failures']} failed")

    by_source = report.get("by_source") or {}
    if by_source:
        print("\n  per source - this is the number the open question turns on:")
        for name, row in by_source.items():
            got, put = row.get("taken", 0), row.get("warmed", 0)
            share = f"{got / put:.0%}" if put else "-"
            print(f"    {name:<10} warmed {put:>4}  taken {got:>4}  "
                  f"{share:>5}  ${row.get('dollars', 0):.3f}")
        print("\n  A source warming a lot and taken rarely is paying for "
              "episodes nobody wanted.\n  One taken nearly every time is "
              "probably worth warming deeper (PREFETCH_LEVEL=script).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--listener", default="",
                    help="whose feed and mixes to plan for")
    ap.add_argument("--live", nargs="?", const="http://127.0.0.1:8000",
                    default="", metavar="URL",
                    help="read the hit rate from a running server instead")
    args = ap.parse_args()
    return show_live(args.live) if args.live else show_plan(args.listener)


if __name__ == "__main__":
    raise SystemExit(main())
