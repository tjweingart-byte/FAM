"""Add a US county's local news outlets to the registry, from Wikidata (§194).

    python tools/local_outlets.py "Marin County" California
    python tools/local_outlets.py "Marin County" California --dry-run
    python tools/local_outlets.py --top 50 --seed     # §276: the seed file

Asks Wikidata (free, keyless) for newspapers and online newspapers whose place
of publication is in that county and which have an official website, and
files each one: an outlet published in a town covers that town, one
published at county level covers the county. Feeds are not needed here - the
collector finds each feed from its homepage on its first poll.

Run it somewhere with network access (this project's build container blocks
query.wikidata.org). Writes to the store `LOCAL_NEWS_DB` names, so point that
at the deployment's disk, or run it on the server.

`--top N` (§276) walks the N most populous US counties (`TOP_COUNTIES`)
instead of one, and `--seed` writes what it finds into `local_outlets.json`,
the seed every fresh disk starts from, rather than into a store: merged by
homepage, so it can be re-run and never drops an outlet already filed.
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
SEED = Path(__file__).resolve().parents[1] / "local_outlets.json"

#: The most populous US counties, largest first (2020 census, approximately
#: ordered) - where a seeded outlet answers the most local questions. Names
#: as Wikidata labels them, so the query below matches.
TOP_COUNTIES = (
    ("Los Angeles County", "California"), ("Cook County", "Illinois"),
    ("Harris County", "Texas"), ("Maricopa County", "Arizona"),
    ("San Diego County", "California"), ("Orange County", "California"),
    ("Kings County", "New York"), ("Miami-Dade County", "Florida"),
    ("Dallas County", "Texas"), ("Riverside County", "California"),
    ("Queens County", "New York"), ("Clark County", "Nevada"),
    ("King County", "Washington"), ("San Bernardino County", "California"),
    ("Tarrant County", "Texas"), ("Bexar County", "Texas"),
    ("Broward County", "Florida"), ("Santa Clara County", "California"),
    ("Wayne County", "Michigan"), ("Alameda County", "California"),
    ("New York County", "New York"), ("Middlesex County", "Massachusetts"),
    ("Philadelphia County", "Pennsylvania"), ("Suffolk County", "New York"),
    ("Sacramento County", "California"), ("Bronx County", "New York"),
    ("Palm Beach County", "Florida"), ("Nassau County", "New York"),
    ("Hillsborough County", "Florida"), ("Cuyahoga County", "Ohio"),
    ("Allegheny County", "Pennsylvania"), ("Oakland County", "Michigan"),
    ("Orange County", "Florida"), ("Franklin County", "Ohio"),
    ("Hennepin County", "Minnesota"), ("Travis County", "Texas"),
    ("Fairfax County", "Virginia"), ("Contra Costa County", "California"),
    ("Salt Lake County", "Utah"), ("Mecklenburg County", "North Carolina"),
    ("Wake County", "North Carolina"), ("Fulton County", "Georgia"),
    ("Montgomery County", "Maryland"), ("Pima County", "Arizona"),
    ("St. Louis County", "Missouri"), ("Honolulu County", "Hawaii"),
    ("Westchester County", "New York"), ("Milwaukee County", "Wisconsin"),
    ("Fresno County", "California"), ("Shelby County", "Tennessee"),
)

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


def seed_rows(rows: list, county: str, state: str) -> list:
    """Rows in the seed file's shape (`local_outlets.json`)."""
    bare = local_news._bare_county(county)  # noqa: SLF001
    return [{"name": r["name"], "homepage": r["homepage"], "town": r["town"],
             "county": bare, "region": state, "country": "US",
             "scope": r["scope"]} for r in rows]


def merge_seed(found: list, path: Path = SEED) -> int:
    """Add `found` to the seed file, by homepage. Returns how many were new."""
    data = json.loads(path.read_text())
    outlets = data.setdefault("outlets", [])
    known = {o.get("homepage") for o in outlets}
    new = [o for o in found if o["homepage"] not in known]
    for outlet in new:
        known.add(outlet["homepage"])
        outlets.append(outlet)
    outlets.sort(key=lambda o: (o.get("region", ""), o.get("county", ""),
                                o.get("name", "")))
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return len(new)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("county", nargs="?", help='e.g. "Marin County"')
    parser.add_argument("state", nargs="?", help="e.g. California")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would be added, add nothing")
    parser.add_argument("--top", type=int, default=0,
                        help="the N most populous US counties instead of one")
    parser.add_argument("--seed", action="store_true",
                        help="write into local_outlets.json, not the store")
    args = parser.parse_args()

    if args.top or args.seed:
        counties = list(TOP_COUNTIES[:args.top]) if args.top else \
            [(args.county, args.state)]
        if not all(c and s for c, s in counties):
            parser.error("name a county and state, or pass --top N")
        found: list = []
        for county, state in counties:
            try:
                rows = seed_rows(fetch(county, state), county, state)
            except Exception as exc:  # noqa: BLE001 - one county is not the run
                print(f"{county}, {state}: failed ({type(exc).__name__}: {exc})")
                continue
            print(f"{county}, {state}: {len(rows)} outlet(s)")
            found.extend(rows)
        if args.dry_run or not args.seed:
            print(json.dumps(found, indent=2, ensure_ascii=False))
            return 0
        added = merge_seed(found)
        print(f"{len(found)} outlet(s) found; {added} new in {SEED.name}")
        return 0
    if not (args.county and args.state):
        parser.error("name a county and state, or pass --top N")

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
