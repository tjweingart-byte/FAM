"""What myFAM would actually offer, and what each live source contributed.

    python tools/stories_report.py             # sweep, compose, print the pool
    python tools/stories_report.py --dry       # sweep only; write nothing
    python tools/stories_report.py --json

**Why this exists rather than a look at `/api/health`.** §52's rule: a check
must perform the real action rather than confirm that it was configured. Health
says which sources are ready; this one asks them, composes the tiles, and
prints the page a listener would see - including the ones that came back
templated because the composer was unavailable, which is the failure that
otherwise looks exactly like success.

It makes real requests and, without `--dry`, one real model call. That is the
whole cost of a refresh window for an entire deployment, so it is cheap to run
and worth reading before turning a source on for real.

What a good run looks like
--------------------------
Several sources reporting `signals`, a pool that spans more than one facet, and
`composed` well above `templated`. A pool of six tiles from one source is a
browse page about one corner of the world; that is a configuration finding, not
a bug, and `PROVIDER_ROLLOUT.md` is the list of what to turn on next.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stories  # noqa: E402
import story_sources  # noqa: E402
import topics  # noqa: E402
from config import settings  # noqa: E402


def print_sources(pool: stories.Pool) -> None:
    print("\nSources")
    if not pool.sources:
        print("  none registered")
        return
    for report in pool.sources:
        mark = {stories.SIGNALS: "ok  ", stories.NOT_CONFIGURED: "off ",
                stories.SKIPPED: "wait", stories.EMPTY: "--  ",
                stories.TIMEOUT: "SLOW", stories.SOURCE_FAILED: "FAIL"}.get(
                    report.outcome, "?   ")
        print(f"  {mark} {report.name:<20} {report.count:>2} signal(s)  "
              f"{report.detail}")


def print_pool(pool: stories.Pool, now: float) -> None:
    live = pool.live(now)
    print(f"\nPool: {len(live)} story/stories, "
          f"{sum(1 for s in live if s.degraded)} templated")
    if not live:
        # Never "nothing is happening". An empty pool is a fact about this
        # machine's configuration, which is what the sentence has to say.
        print(f"  {pool.empty_reason}")
        return
    for story in live:
        hours = story.age(now) / 3600.0
        left = (story.shelf_life - story.age(now)) / 3600.0
        print(f"\n  [{story.push(now):.2f}] {story.title}"
              f"{'   (templated)' if story.degraded else ''}")
        print(f"        {story.angle}")
        print(f"        ? {story.query}")
        print(f"        {story.domain} via {story.source} · "
              f"{', '.join(story.tags) or 'no tags'} · "
              f"{hours:.1f}h old, {left:.1f}h left"
              f"{' · outcome pending' if story.outcome_pending else ''}")


def print_rails(now: float) -> None:
    """The page itself, for a listener with no history.

    Deliberately the cold case: a listener with taste gets the pool ranked for
    them, and what this shows is what the *inventory* can carry on its own.
    """
    store = topics.EventStore(":memory:")
    feed = topics.build_feed(store, "", now=now)
    print("\nmyFAM, for a listener the app has never seen")
    for section in feed["sections"]:
        print(f"\n  {section['title']}")
        if not section["topics"]:
            print(f"    ({section['empty_reason']})")
            continue
        for tile in section["topics"]:
            live = " ·live" if tile.get("source") else ""
            print(f"    - {tile['title']}{live}")
            if tile.get("angle"):
                print(f"      {tile['angle']}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry", action="store_true",
                        help="sweep the sources but do not compose (no model call)")
    parser.add_argument("--json", action="store_true", help="machine-readable")
    args = parser.parse_args()

    if args.dry:
        import dataclasses

        stories.settings = dataclasses.replace(settings, stories_compose=False)

    installed = story_sources.install()
    if not args.json:
        print(f"STORIES={'1' if settings.stories else '0'}  "
              f"compose={'off (--dry)' if args.dry else settings.stories_compose}")
        print(f"  configured    {', '.join(installed['configured'])}")
        print(f"  installed     {', '.join(installed['installed']) or 'none'}")
        for problem in installed["problems"]:
            print(f"  CONFIG ERROR  {problem}")

    started = time.time()
    pool = await stories.refresh()
    took = time.time() - started

    if args.json:
        print(json.dumps({"took_seconds": round(took, 2),
                          "report": stories.report()}, indent=2, default=str))
        return 0

    print_sources(pool)
    print_pool(pool, started)
    print_rails(started)
    print(f"\nSwept and composed in {took:.1f}s. One refresh window serves "
          f"every listener for {settings.stories_ttl_seconds:.0f}s.")
    if not pool.live(started):
        print("Nothing to offer from live data on this machine. That is the "
              "shipped default, not a fault: myFAM serves its evergreen bank "
              "and each rail says why it is thin. See PROVIDER_ROLLOUT.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
