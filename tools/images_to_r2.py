#!/usr/bin/env python3
"""Move pictures out of the databases into the R2 bucket (§330, image_store.py).

    python tools/images_to_r2.py          # count what is still inline
    python tools/images_to_r2.py --move   # move it, a batch at a time

Run where the server runs (Render's Shell), with the same `AUDIO_STORE=r2`
and R2 keys. The server does the same move once per boot, so this is for
seeing the count, or for moving before a deploy. Compare-and-set per row:
safe while the server is up. Exit status 0 when nothing is left inline.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import image_store  # noqa: E402

#: (label, table, column, what an inline value looks like)
INLINE = (
    ("profiles", "people", "avatar", "avatar LIKE 'data:%'"),
    ("groups", "groups", "avatar", "avatar LIKE 'data:%'"),
    ("mixes", "mixes", "cover", "cover LIKE 'data:%'"),
)


def inline_counts() -> dict:
    out = {}
    for label, opener in image_store._MIGRATORS:
        store = opener()
        conn = store._conn()
        if label == "thumbnails":
            n = 0
            for column in ("image", "pending_image"):
                for (blob,) in conn.execute(
                        f"SELECT {column} FROM thumbnails WHERE {column} IS NOT NULL"):
                    n += 0 if image_store.pointer_name(blob) else 1
            out[label] = n
            continue
        _, table, _, where = next(i for i in INLINE if i[0] == label)
        out[label] = int(conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0])
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--move", action="store_true",
                        help="move inline pictures into the bucket")
    parser.add_argument("--batch", type=int, default=200)
    args = parser.parse_args(argv)
    print("bucket:", image_store.describe()["store"])
    if args.move:
        if not image_store.enabled():
            print("no bucket: set AUDIO_STORE=r2 and its keys first")
            return 1
        while True:
            moved = image_store.migrate(args.batch)
            print("moved", moved)
            counts = [v for v in moved.values() if isinstance(v, int)]
            if len(counts) < len(moved) or not any(counts):
                break
    left = inline_counts()
    print("still inline:", left)
    return 0 if not any(left.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
