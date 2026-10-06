"""Prove GDELT's export files actually work, from a machine that can reach them.

    python tools/gdelt_probe.py
    python tools/gdelt_probe.py --query "chiefs broncos"

**Why this exists.** Nothing in the build container has ever downloaded a
real file from `data.gdeltproject.org` - the egress proxy blocks it - so the
file shapes in `gdelt.py` are written from GDELT's codebook and pinned against
recorded rows. Those tests prove the parsing; they prove nothing about whether
the files still have that shape.

That is precisely the §52 gap: a check that answers a cheaper question than
the one being asked and then reports OK. This asks the real question: it runs
one real sync into a scratch copy (never the deployment's own), then reads it
the three ways FAM does (§209):

* **files** - `lastupdate.txt`, the newest GKG file and a little behind it
* **volume** - the theme counts the story sweep ranks on
* **retrieval** - the search an episode's fallback rung makes

A pass means the files downloaded, parsed and searched. It does not mean the
*content* is good - read the output.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dataclasses  # noqa: E402

import config  # noqa: E402
import gdelt  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", default="undersea cable",
                        help="what to retrieve, as a listener might ask")
    parser.add_argument("--files", type=int, default=2,
                        help="how many files behind the newest to fetch")
    args = parser.parse_args()

    # Forced on regardless of GDELT=0 (the shipped default), and pointed at a
    # scratch copy so a probe never writes into a deployment's store.
    scratch = tempfile.mkdtemp(prefix="gdelt-probe-")
    os.environ["GDELT_EXPORT_DB"] = os.path.join(scratch, "gdelt_export.db")
    gdelt.reset_store()
    gdelt.settings = dataclasses.replace(
        config.settings, gdelt=True, gdelt_export_backfill_files=args.files)

    print(f"files from  {gdelt.settings.gdelt_export_base}")
    print(f"copy at     {os.environ['GDELT_EXPORT_DB']}\n")
    failures = 0

    print("== Sync")
    result = await gdelt.sync()
    print(f"   {result}")
    held = gdelt.store().counts()
    print(f"   held: {held['files']} file(s), {held['articles']} article(s)")
    if result.get("error") or not held["articles"]:
        print("   FAIL  nothing was read. `parse_gkg_line` may be reading the "
              "wrong columns, or the files moved.")
        return 1
    placed = gdelt.store()._rows(
        "SELECT COUNT(*) FROM articles WHERE country != ''", ())[0][0]
    print(f"   {placed} of {held['articles']} placed in a country")

    print("\n== Volume (what the story sweep ranks on)")
    measured = []
    for theme, subject in gdelt.THEMES:
        volume = await gdelt.volume_for(theme)
        measured.append(volume)
        print(f"   {subject:28} {volume:.0f}")
    if not any(measured):
        print("   WARNING  every theme counted zero - the theme codes may have "
              "changed in GDELT's vocabulary.")
        failures += 1

    print(f"\n== Retrieval for {args.query!r}")
    results = await gdelt.retrieve(args.query, limit=5)
    for row in results:
        print(f"   {row.published_date}  {row.country or '?':16} {row.title[:70]}")
    if not results:
        print("   (nothing - try a query from today's headlines with --query)")

    print()
    if failures:
        print(f"{failures} check(s) failed. GDELT is NOT proven from this machine.")
        return 1
    print("GDELT's export files downloaded and parsed. Safe to set GDELT=1.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
