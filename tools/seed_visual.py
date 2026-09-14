#!/usr/bin/env python3
"""Put one continuous-line asset where the server will find it.

The app never makes artwork (see `episode_visuals`), so there is no way to see
the feature work without an asset on disk. This copies the bundled test fixture
into the visuals folder under the id a given query hashes to, which is the
whole of the manual test procedure in
`docs/FAM_CONTINUOUS_LINE_VISUALS.md`.

    python tools/seed_visual.py "why do NFL teams move cities"
    python tools/seed_visual.py "why do NFL teams move cities" --status processing
    python tools/seed_visual.py "why do NFL teams move cities" --broken
    python tools/seed_visual.py --list

The fixture is deliberately not artwork: it is a Lissajous figure drawn in one
stroke, chosen because it has a real path length to reveal and because nobody
could mistake it for a finished FAM visual.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import episode_visuals  # noqa: E402

FIXTURES = pathlib.Path(__file__).resolve().parent / "visual_fixtures"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("query", nargs="?", default="", help="what the listener asked")
    ap.add_argument("--status", choices=("ready", "processing", "failed"),
                    default="ready", help="what the manifest should claim")
    ap.add_argument("--broken", action="store_true",
                    help="seed the two-path asset, to see the rejection path")
    ap.add_argument("--list", action="store_true", help="show what is installed")
    args = ap.parse_args()

    directory = episode_visuals.visuals_dir(create=True)

    if args.list or not args.query:
        print(f"visuals folder: {directory}")
        if not directory.is_dir():
            print("  (does not exist yet)")
            return 0
        assets = sorted(p.name for p in directory.iterdir() if p.is_file())
        for name in assets:
            print("  " + name)
        if not assets:
            print("  (empty)")
        return 0

    vid = episode_visuals.visual_id(args.query)
    target = directory / f"{vid}.svg"

    if args.status == "ready":
        source = FIXTURES / ("two-paths.svg" if args.broken else "sample-line.svg")
        shutil.copyfile(source, target)
        print(f"copied {source.name} -> {target}")
    else:
        # A status the manifest claims rather than a file: "processing" is the
        # state where there is nothing on disk yet, which is the whole point
        # of being able to say it.
        target.unlink(missing_ok=True)

    index_path = directory / episode_visuals.INDEX_NAME
    try:
        index = json.loads(index_path.read_text()) if index_path.is_file() else {}
    except ValueError:
        index = {}
    if args.status == "ready":
        index.pop(args.query, None)
        index.pop(vid, None)
    else:
        index[args.query] = {"status": args.status,
                             "reason": "seeded by tools/seed_visual.py"}
    if index:
        index_path.write_text(json.dumps(index, indent=2) + "\n")
    elif index_path.is_file():
        index_path.unlink()

    record = episode_visuals.record_for(args.query)
    print(f"query    {args.query!r}")
    print(f"id       {vid}")
    print(f"status   {record['status']}" + (f"  ({record['reason']})"
                                            if record["reason"] else ""))
    if record["vector_url"]:
        print(f"vector   {record['vector_url']}")
    print("\nNow search for exactly that query in the app and open the player.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
