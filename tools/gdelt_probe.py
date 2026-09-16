"""Prove GDELT actually works, from a machine that can reach it.

    python tools/gdelt_probe.py
    python tools/gdelt_probe.py --query "chiefs broncos"

**Why this exists.** Nothing in the build container has ever made a real
request to `api.gdeltproject.org` - the egress proxy blocks it - so every
shape in `gdelt.py` is written from the documented API and pinned against
recorded payloads. Those tests prove the parsing; they prove nothing about
whether the endpoint still answers in that shape.

That is precisely the §52 gap: a check that answers a cheaper question than
the one being asked and then reports OK. This asks the real question.

It checks both jobs GDELT does for FAM, because they use different modes and
either can break alone:

* **retrieval** - `mode=artlist`, the second index beside Exa
* **volume** - `mode=timelinevolraw`, what the Trending row ranks on

A pass means the endpoint answered, the JSON parsed, and the fields FAM reads
were present. It does not mean the *content* is good - read the output.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dataclasses  # noqa: E402

import config  # noqa: E402
import gdelt  # noqa: E402
import research  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", default="undersea cable",
                        help="what to retrieve, as a listener might ask")
    parser.add_argument("--themes", type=int, default=4,
                        help="how many GKG themes to measure")
    args = parser.parse_args()

    # The probe forces GDELT on regardless of GDELT=0, because being switched
    # off is the shipped default and is not what is being tested here.
    gdelt.settings = dataclasses.replace(config.settings, gdelt=True)

    print(f"endpoint  {gdelt.DOC_API}")
    print("credential  none needed\n")

    failures = 0

    # --- retrieval --------------------------------------------------------
    print(f"== Retrieval (mode=artlist) for {args.query!r}")
    try:
        results = await gdelt.retrieve(args.query, limit=5, recency_days=3)
    except Exception as exc:  # noqa: BLE001 - this tool reports, never raises
        print(f"   FAILED  {type(exc).__name__}: {exc}\n")
        results, failures = [], failures + 1
    else:
        if not results:
            # `retrieve` swallows errors by contract, so an empty list here is
            # either a genuinely empty result or a failure already logged.
            print("   no articles came back. Either the query found nothing or "
                  "the request failed - check the log line above.\n")
            failures += 1
        else:
            print(f"   {len(results)} article(s)\n")
            for item in results:
                when = research.published_at(item)
                print(f"   - {research.credibility(item):12} "
                      f"{(when.date().isoformat() if when else 'no date'):12} "
                      f"{item.title[:64]}")
            print()
            # The two things most likely to be silently wrong in this adapter.
            undated = sum(1 for r in results if research.published_at(r) is None)
            if undated == len(results):
                print("   WARNING  no article carried a readable date. `seendate` "
                      "may have changed shape; `gdelt._iso` reads it.\n")
                failures += 1
            if all(research.credibility(r) == "unverified" for r in results):
                print("   WARNING  every source graded 'unverified'. URLs may not "
                      "be arriving in the field `research.host_of` reads.\n")

    # --- volume -----------------------------------------------------------
    print("== Volume (mode=timelinevolraw), what Trending ranks on")
    measured = []
    for theme, subject in gdelt.THEMES[:args.themes]:
        try:
            volume = await gdelt.volume_for(
                theme, float(gdelt.settings.gdelt_timeout_seconds))
        except Exception as exc:  # noqa: BLE001
            print(f"   {subject:28} FAILED  {type(exc).__name__}: {exc}")
            failures += 1
            continue
        measured.append((subject, volume))
        print(f"   {subject:28} {volume}")

    if measured and all(v == 0.0 for _s, v in measured):
        print("\n   WARNING  every theme measured zero. Either the themes are "
              "wrong or `parse_volume` is reading the wrong field.")
        failures += 1

    print()
    if failures:
        print(f"{failures} check(s) failed. GDELT is NOT proven from this machine.")
        return 1
    print("GDELT answered and parsed. Safe to set GDELT=1.")
    print("For the second index on every episode, also set GDELT_CROSS_CHECK=1.")
    print("For the Trending row, set TRENDING_SOURCE=gdelt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
