"""New house examples, proposed from what listeners heard through (§520).

    python tools/example_candidates.py                  # the last 30 days
    python tools/example_candidates.py --days 60 --min-listens 30
    python tools/example_candidates.py --write          # candidate files

`examples/` is the strongest lever on the writing ([examples-lever]), and
it was all evergreen explainers. This reads the quality store
(`episode_quality.example_candidates`): per facet, the written episodes with
enough listens, the most heard to 85% first, then the fewest who left in the
first 20 seconds - facets with the fewest examples first, so the gaps lead.

**Nothing is added by code.** `--write` puts each candidate in
`~/.fam/example_candidates/` (`--dir` to change) in the examples' own format
- the query, a blank line, the script - never in `examples/`. To adopt one,
read it aloud, edit it, move it into `examples/`, give it a facet in
`examples/facets.json`, and draft its worked plan with
`python tools/editor_teaching.py --draft <name>`. A news episode's facts
date fast: an example teaches how a story is told, so prefer one whose shape
outlives its facts. No key, no network, no person's id.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def slug(query: str) -> str:
    words = re.findall(r"[a-z0-9]+", query.lower())
    return "-".join(words[:6]) or "episode"


def file_name(candidate: dict) -> str:
    try:
        minutes = max(1, round(float(candidate.get("minutes") or 1)))
    except (TypeError, ValueError):
        minutes = 1
    return f"{minutes}-{slug(candidate['query'])}.txt"


def file_text(candidate: dict) -> str:
    return candidate["query"] + "\n\n" + " ".join(candidate["sentences"]).strip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=float, default=30.0)
    ap.add_argument("--min-listens", type=int, default=None)
    ap.add_argument("--per-facet", type=int, default=3)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--dir", default="")
    args = ap.parse_args()
    import episode_quality

    found = episode_quality.example_candidates(
        days=args.days, per_facet=args.per_facet,
        min_listens=(args.min_listens if args.min_listens is not None
                     else episode_quality.CANDIDATE_MIN_LISTENS))
    if not found["facets"]:
        print(f"no episode with {found['min_listens']}+ listens in the last "
              f"{args.days:g} days")
        return 0
    out = None
    if args.write:
        out = (pathlib.Path(args.dir).expanduser() if args.dir
               else pathlib.Path.home() / ".fam" / "example_candidates")
        out.mkdir(parents=True, exist_ok=True)
    for facet, entry in found["facets"].items():
        print(f"\n{facet} - {entry['examples_now']} example(s) now")
        for c in entry["candidates"]:
            print(f"  {c['finished']:.0%} heard 85%, {c['early_exit']:.0%} left in 20 s, "
                  f"{c['listens']} listens, {c['minutes']} min  - {c['query']}")
            if out is not None:
                path = out / file_name(c)
                path.write_text(file_text(c), encoding="utf-8")
                print(f"    -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
