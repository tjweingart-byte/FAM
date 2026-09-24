"""Trending's edition: what it holds, whether GNews answers, and a build now.

    python tools/trending_bank.py                 # the schedule and the edition
    python tools/trending_bank.py --verify        # one real GNews request
    python tools/trending_bank.py --dry           # collect and rank; write nothing
    python tools/trending_bank.py --build         # build the current slot now
    python tools/trending_bank.py --url https://your-app.onrender.com

**Run it on the server** (Render's Shell tab) after adding `GNEWS_KEY`. The
server builds on boot anyway when the current slot has no GNews edition; this
is for seeing it happen, and for rebuilding a slot on purpose.

`--verify` is §52: "a key is set" is not "the key works". `--dry` spends
GNews requests (about 26, counted against the day's ceiling) and no model
call. `--build` spends those, one composer call and ten episodes - roughly
what an edition costs twice a day - and replaces the current slot's edition.
`--url` reads a running server's `/api/health` and spends nothing.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gnews  # noqa: E402
import trending_bank as TB  # noqa: E402
from config import settings  # noqa: E402


def _print_edition(edition) -> None:
    if not edition:
        print("edition: none")
        return
    e = edition if isinstance(edition, dict) else edition.as_dict()
    print(f"edition {e['slot']} from {e['source']}")
    print(f"  {e['stories']} stories, {e['episodes_written']} episodes written, "
          f"${e['dollars']:.4f}, {e['requests']} GNews request(s)")
    if e.get("detail"):
        print(f"  {e['detail']}")
    episodes = list((e.get("episodes") or {}).values())
    for i, title in enumerate(e.get("titles", [])):
        status = episodes[i].get("status", "?") if i < len(episodes) else "?"
        print(f"  {i + 1:2d}. [{status:>11}] {title}")


def _report(report: dict) -> None:
    sched = report["schedule"]
    print(f"trending bank: {'on' if report['enabled'] else 'OFF'}; editions at "
          f"{', '.join(f'{h:02d}:00' for h in sched['hours'])} {sched['timezone']}")
    print(f"  current slot {sched['current_slot']}, next {sched['next']}")
    g = report["gnews"]
    print(f"  GNews: {'ready' if g['ready'] else 'not ready'} - {g['detail']}; "
          f"{g['requests_today']}/{g['daily_ceiling']} requests today")
    if report.get("last_failure"):
        print(f"  last failure: {report['last_failure']}")
    _print_edition(report.get("edition"))


async def _dry() -> None:
    signals, detail, requests = await TB.collect(
        time.time(), settings.trending_bank_size)
    print(f"{detail}; {requests} GNews request(s)")
    for i, s in enumerate(signals, 1):
        print(f"{i:2d}. [{s.coverage:>5}] {s.subject}")


async def _build() -> None:
    from cache import build_cache
    from script_generator import ScriptGenerator

    edition = await TB.build(generator=ScriptGenerator(), cache=build_cache(),
                             force=True)
    if edition is None:
        print("no edition was built:", TB.empty_reason(), TB._LAST_ATTEMPT or "")
        sys.exit(1)
    _print_edition(edition)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--dry", action="store_true")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--url", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.url:
        with urllib.request.urlopen(args.url.rstrip("/") + "/api/health",
                                    timeout=20) as response:
            report = json.load(response).get("trending_bank") or {}
        print(json.dumps(report, indent=2)) if args.json else _report(report)
        return
    if args.verify:
        ok, why = asyncio.run(gnews.verify())
        print(("OK: " if ok else "FAILED: ") + why)
        sys.exit(0 if ok else 1)
    if args.dry:
        asyncio.run(_dry())
        return
    if args.build:
        asyncio.run(_build())
        return
    report = TB.report()
    print(json.dumps(report, indent=2, default=str)) if args.json else _report(report)


if __name__ == "__main__":
    main()
