"""How much local news RSS actually gives us, town by town (§194).

    python tools/measure_local_news.py "San Anselmo, California, US" "Fairfax, California, US"
    python tools/measure_local_news.py --file towns.txt --json

For each town: resolves it, polls every outlet filed for it or its county (add
them first with `tools/local_outlets.py` or /api/admin/local-news/outlets),
and reports what the collector got - feeds found, their state, full text or
teasers, and how many stories from the last 7 days name the town or the
county, with how many words the writer would have.

This replaces guesses with numbers before anything is built on top of them.
Run it where the news sites are reachable - this project's build container
blocks them. It uses the store `LOCAL_NEWS_DB` names; point that at a scratch
file to measure without touching a deployment's collector.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

import local_news  # noqa: E402
import places  # noqa: E402


async def measure(text: str) -> dict:
    place = await places.resolve(text)
    if place is None:
        town, region, country = places.split_name(text)
        place = places.Place(town, region=region, country=country)
    target = local_news.store()
    outlets = target.for_place(place.name, place.county, place.region)
    async with httpx.AsyncClient(timeout=local_news.FETCH_TIMEOUT,
                                 follow_redirects=True) as client:
        polled = await asyncio.gather(
            *(local_news.poll(o, client, target) for o in outlets))
    week = time.time() - 7 * 86400
    town = [i for i in local_news.town_items(place) if i.published >= week]
    county = [i for i in local_news.county_items(place, exclude=tuple(town))
              if i.published >= week]
    return {
        "place": place.as_dict(),
        "outlets": [{"name": o.name, "state": o.state, "feed": o.feed_url,
                     "full_text": o.full_text, "detail": o.detail}
                    for o in polled],
        "town_stories_7d": len(town),
        "town_words": sum(local_news.words(i.text) for i in town),
        "county_stories_7d": len(county),
        "county_words": sum(local_news.words(i.text) for i in county),
        "would_answer": ("town" if town else "county" if county else
                         "weather only"),
    }


async def main_async(towns: list, as_json: bool) -> int:
    results = [await measure(t) for t in towns]
    if as_json:
        print(json.dumps(results, indent=2))
        return 0
    for r in results:
        p = r["place"]
        print(f"\n{p['name']}, {p['region']} (county: {p['county'] or '?'})")
        for o in r["outlets"] or [{"name": "no outlets filed", "state": "",
                                   "full_text": None, "detail": ""}]:
            text = {True: "full text", False: "teasers", None: "?"}[o["full_text"]]
            print(f"  {o['name']:<40} {o['state']:<10} {text:<9} {o['detail']}")
        print(f"  last 7 days: {r['town_stories_7d']} town stories "
              f"({r['town_words']} words), {r['county_stories_7d']} county "
              f"stories ({r['county_words']} words) -> {r['would_answer']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("towns", nargs="*",
                        help='"Town, State, Country" - one or more')
    parser.add_argument("--file", help="a file of towns, one per line")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    towns = list(args.towns)
    if args.file:
        towns += [line.strip() for line in Path(args.file).read_text().splitlines()
                  if line.strip()]
    if not towns:
        parser.error("name at least one town")
    return asyncio.run(main_async(towns, args.json))


if __name__ == "__main__":
    sys.exit(main())
