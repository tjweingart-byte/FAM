"""Run Consuela's dedupe eval and say whether her dedupe may leave its dry run (§401).

    AGENTS=1 HOUSEKEEPER=1 ANTHROPIC_API_KEY=... python tools/consuela_dedupe_eval.py
    python tools/consuela_dedupe_eval.py --no-record     # measure, keep nothing

Every case in `agents/dedupe_eval.py` - real repeats in other words, and the
guide's never-duplicates table - goes through the same code and the same two
models the nightly dedupe uses (`HOUSEKEEPER_MODEL`, then
`HOUSEKEEPER_REVIEW_MODEL`). It passes with no wrong merge and every call
answered; the result is kept in housekeeper.db, where the dedupe reads it
before it marks anything. Needs the sentence model
(`python tools/install_embed_model.py`) and, for the models, the agents on
and a key - without them every asked pair falls back and the run cannot pass.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-record", action="store_true",
                        help="measure only; do not keep the result")
    args = parser.parse_args(argv)

    from agents import dedupe_eval

    result = asyncio.run(dedupe_eval.run(record=not args.no_record))
    if not result.get("ran"):
        print(f"not run: {result['why']}")
        return 2
    print(f"models: {result['models']}")
    for row in result["details"]:
        mark = ("WRONG MERGE" if row["expected"] == "different" and row["verdict"] == "same"
                else "missed" if row["expected"] == "same" and row["verdict"] != "same"
                else "ok")
        asked = f" [first: {row['first'] or '-'}, review: {row['review'] or '-'}]"
        print(f"  {mark:11} {row['case']:40} sim {row['similarity']:.2f}  "
              f"{row['verdict']:9} {row['why']}{asked if row['first'] else ''}")
    for origin, o in sorted(result.get("by_origin", {}).items()):
        print(f"  {origin:9} {o['total']:4} cases, {o['wrong_merges']} wrong merge(s), "
              f"{o['missed']} missed")
    print(f"\n{result['total']} cases: {result['wrong_merges']} wrong merge(s), "
          f"{result['missed']} missed repeat(s), calls fell back: {result['fell_back']}")
    print("PASSED: the dedupe may leave its dry run once HOUSEKEEPER_DEDUPE_DRY_RUN=0"
          if result["passed"] else "NOT PASSED: the dedupe stays a dry run")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
