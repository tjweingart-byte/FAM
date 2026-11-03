"""Measure Sonic on what the archive holds (§407; the training plan's S1).

    python tools/sonic_recall_eval.py              # run, print, keep the result
    python tools/sonic_recall_eval.py --no-record

Questions written in code from stored facts and readings, asked through
Sonic's own read-only connection; no model, nothing sent out. Prints how
often he brought back the fact a question came from, and the two hard
rules: nothing from inside the search's window, every packet oldest first.
Needs filed facts - Consuela consolidating with HOUSEKEEPER_DRY_RUN=0 - and,
on Atlas, MONGODB_READ_URI.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-record", action="store_true")
    args = parser.parse_args(argv)

    from agents import retriever_eval

    result = retriever_eval.run(record=not args.no_record)
    if not result.get("ran"):
        print(f"not run: {result['why']}")
        return 2
    if not result["total"]:
        print("nothing to ask about: the archive holds no filed facts or readings yet")
        return 2
    for row in result["details"]:
        mark = "found" if row["found"] else "MISSED"
        flags = []
        if row["leaks"]:
            flags.append(f"{row['leaks']} LEAK(S)")
        if row["order_error"]:
            flags.append("OUT OF ORDER")
        if row["beyond_family"]:
            flags.append(f"{row['beyond_family']} beyond the node's family")
        print(f"  {mark:6} {row['kind']:7} {row['lookup'] or '-':15} "
              f"({row['chosen_by'] or '-':6}) {row['question'][:50]:50} "
              f"{row['items']} items {row['ms']:.0f} ms {' '.join(flags)}")
    print(f"\n{result['total']} questions: found {result['found']} "
          f"({result['found_rate']:.0%}); leaks {result['leaks']}; "
          f"out of order {result['order_errors']}; beyond family {result['beyond_family']}; "
          f"empty {result['empty']}; fell back {result['fell_back']}; "
          f"mean {result['ms']} ms")
    print("PASSED the hard rules" if result["passed"] else "FAILED a hard rule")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
