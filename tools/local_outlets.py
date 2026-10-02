"""Add a US county's local news outlets to the registry, from Wikidata (§193).

    python tools/local_outlets.py "Marin County" California
    python tools/local_outlets.py "Marin County" California --dry-run

Asks Wikidata (free, keyless) for newspapers and online newspapers whose place
of publication is in that county and which have an official website, and
files each one: an outlet published in a town covers that town, one
published at county level covers the county. Feeds are not needed here - the
collector finds each feed from its homepage on its first poll.

Run it somewhere with network access (this project's build container blocks
query.wikidata.org). Writes to the store `LOCAL_NEWS_DB` names, so point that
at the deployment's disk, or run it on the server.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

import local_news  # noqa: E402
import places  # noqa: E402

ENDPOINT = "https://query.wikidata.org/sparql"

#: newspaper (and every subclass: daily, weekly, ...), online newspaper.
QUERY = """
SELECT DISTINCT ?outlet ?outletLabel ?site ?placeLabel ?isCounty WHERE {
  VALUES ?type { wd:Q11032 wd:Q1153191 }
  ?outlet wdt:P31/wdt:P279* ?type ;
          wdt:P856 ?site ;
          wdt:P291 ?place .
  ?place wdt:P131* ?county .
  ?county wdt:P31 wd:Q47168 ;
          rdfs:label "%(county)s"@en ;
          wdt:P131 ?state .
  ?state rdfs:label "%(state)s"@en .
  FILTER NOT EXISTS { ?outlet wdt:P576 ?ended }
  BIND(?place = ?county AS ?isCounty)
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
"""


def fetch(county: str, state: str) -> list:
    query = QUERY % {"county": county.replace('"', ""),
                     "state": state.replace('"', "")}
    reply = httpx.get(ENDPOINT, params={"query": query, "format": "json"},
                      headers={"User-Agent": places.user_agent(),
                               "Accept": "application/sparql-results+json"},
                      timeout=60)
    reply.raise_for_status()
    rows = []
    for b in reply.json()["results"]["bindings"]:
        rows.append({
            "name": b["outletLabel"]["value"],
            "homepage": b["site"]["value"],
            "town": "" if b["isCounty"]["value"] == "true" else b["placeLabel"]["value"],
            "scope": "county" if b["isCounty"]["value"] == "true" else "town",
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("county", help='e.g. "Marin County"')
    parser.add_argument("state", help="e.g. California")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would be added, add nothing")
    args = parser.parse_args()

    rows = fetch(args.county, args.state)
    bare = local_news._bare_county(args.county)  # noqa: SLF001
    if args.dry_run:
        print(json.dumps(rows, indent=2))
        return 0
    added = 0
    for row in rows:
        before = local_news.store().outlets("homepage = ?", (row["homepage"],))
        local_news.store().add_outlet(
            row["name"], row["homepage"], town=row["town"], county=bare,
            region=args.state, country="US", scope=row["scope"],
            source="wikidata")
        added += 0 if before else 1
    print(f"{len(rows)} outlet(s) found for {args.county}, {args.state}; "
          f"{added} new")
    return 0


if __name__ == "__main__":
    sys.exit(main())
