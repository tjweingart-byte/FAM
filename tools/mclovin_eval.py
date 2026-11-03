"""Mclovin's checks, scored on mistakes planted in code (training steps 1-2, §439).

    python tools/mclovin_eval.py                       # the editor eval's frozen scripts
    python tools/mclovin_eval.py --dir ~/.fam/editor_eval --show quote
    python tools/mclovin_eval.py --reviewed            # the nightly review's disagreements

Reads the scripts and frozen packets `tools/editor_eval.py` wrote (150
scripts from 50 edition questions), plants known mistakes into copies of
their sentences against each episode's own packets
(`agents/supervisor_cases.py`), and runs every sentence - planted, control
and unaltered - through Mclovin's checks (`agents/supervisor_checks.py`).

**Two numbers per check** (step 2):

* **caught** - of the mistakes planted for that check, how many it failed;
* **false flags** - of the unaltered sentences, how many it failed, plus
  any control it failed. An unaltered sentence may hold a real mistake, so
  this is the upper bound on flagging a clean sentence; `--show CHECK`
  prints them, to read which are real and which are the check being too
  strict (step 3: tighten the rule, add the case to the tests).

A check is **ready to enforce** when it catches at least `CAUGHT_MIN` of its
planted mistakes and flags at most `FLAGS_MAX` of clean sentences, over at
least `PLANTED_MIN` planted cases. Needs no key, no network; deterministic.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import pickle
import random
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: The bar for moving a check from count only to enforce (step 2).
CAUGHT_MIN = 0.90
FLAGS_MAX = 0.01
PLANTED_MIN = 20


def _now_of(path: pathlib.Path):
    from datetime import datetime, timezone

    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def run(folder: pathlib.Path, show: str = "") -> dict:
    from agents import supervisor_cases, supervisor_checks

    planted = collections.Counter()
    caught = collections.Counter()
    clean = 0
    flags = collections.Counter()
    controls = collections.Counter()
    control_flags = collections.Counter()
    shown: list = []
    misses: list = []
    for plan_path in sorted(folder.glob("q*.plan.pickle")):
        frozen = pickle.loads(plan_path.read_bytes())
        plan = frozen["plan"]
        now = _now_of(plan_path)                 # the packets' own day
        checker = supervisor_checks.Checker(plan, None, now=now)
        checker.enforce = frozenset()
        blocks = checker.blocks or []
        for i, result_path in enumerate(sorted(folder.glob(plan_path.name.split(".")[0]
                                                           + ".*.json"))):
            if result_path.name == "table.json":
                continue
            sentences = json.loads(result_path.read_text()).get("sentences") or []
            for sentence in sentences:
                clean += 1
                for c in checker.check(sentence).checks:
                    if c.result == "fail":
                        flags[c.check] += 1
                        if show and c.check == show:
                            shown.append((result_path.name, c.why, sentence))
            for case in supervisor_cases.plant(blocks, sentences, now,
                                               seed=zlib.crc32(result_path.name.encode())):
                verdict = {c.check: c for c in checker.check(case.sentence).checks}
                got = verdict.get(case.check)
                failed = got is not None and got.result == "fail"
                if case.should_fail:
                    planted[case.check] += 1
                    caught[case.check] += failed
                    if not failed:
                        misses.append((case.kind, case.sentence))
                else:
                    controls[case.check] += 1
                    control_flags[case.check] += failed
                    if failed and show == case.check:
                        shown.append(("control", got.why, case.sentence))
    # Two stories in one packet (step 1): an edition's packet is nearly
    # always one story, so mixed stories are planted into a packet made of
    # two episodes' packets - this question's and the next one's - and the
    # unaltered sentences checked against it are the controls.
    plans = sorted(folder.glob("q*.plan.pickle"))
    for a_path, b_path in zip(plans, plans[1:] + plans[:1]):
        a = pickle.loads(a_path.read_bytes())["plan"]
        b = pickle.loads(b_path.read_bytes())["plan"]
        checker = supervisor_checks.Checker(a, None, now=_now_of(a_path))
        other = supervisor_checks.Checker(b, None, now=_now_of(b_path)).blocks or []
        mine = list(checker.blocks or [])
        checker.blocks = mine + [blk for blk in other if not blk.live]
        checker.enforce = frozenset()
        for result_path in sorted(folder.glob(a_path.name.split(".")[0] + ".*.json")):
            sentences = json.loads(result_path.read_text()).get("sentences") or []
            for sentence in sentences:
                controls["mixed"] += 1
                got = {c.check: c for c in checker.check(sentence).checks}.get("mixed")
                if got is not None and got.result == "fail":
                    control_flags["mixed"] += 1
                    if show == "mixed":
                        shown.append(("two-story control", got.why, sentence))
            rng = random.Random(zlib.crc32(result_path.name.encode()))
            for case in supervisor_cases.plant_mixed(mine, [s for s in sentences
                                                            if len(s.split()) >= 6],
                                                     rng, other):
                planted["mixed"] += 1
                got = {c.check: c for c in checker.check(case.sentence).checks}.get("mixed")
                failed = got is not None and got.result == "fail"
                caught["mixed"] += failed
                if not failed:
                    misses.append((case.kind, case.sentence))
    table = {}
    for check in supervisor_checks.CHECKS:
        n = planted[check]
        rate_caught = round(caught[check] / n, 3) if n else None
        false = flags[check] + control_flags[check]
        rate_flags = round(false / max(1, clean + controls[check]), 4)
        ready = (n >= PLANTED_MIN and rate_caught is not None and rate_caught >= CAUGHT_MIN
                 and rate_flags <= FLAGS_MAX)
        table[check] = {"planted": n, "caught": rate_caught, "unaltered_flags": flags[check],
                        "controls": controls[check], "controls_flagged": control_flags[check],
                        "false_flag_rate": rate_flags, "ready_to_enforce": ready}
    return {"unaltered_sentences": clean, "checks": table, "shown": shown[:60],
            "misses": collections.Counter(k for k, _s in misses), "miss_examples": misses[:30]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default=str(pathlib.Path.home() / ".fam" / "editor_eval"))
    ap.add_argument("--show", default="", help="print one check's flags on unaltered sentences")
    ap.add_argument("--misses", action="store_true", help="print planted cases not caught")
    ap.add_argument("--reviewed", action="store_true",
                    help="print the nightly reviewer's disagreements (step 4): cases to "
                         "add to tests/test_mclovin_training_439.py")
    args = ap.parse_args()
    if args.reviewed:
        from agents import supervisor_training

        print(json.dumps(supervisor_training.report(), indent=1))
        for case in supervisor_training.disagreements():
            print(f"  [{case['kind']}: {', '.join(case['checks'])}; reviewer "
                  f"{case['reviewer']}] {case['text']}\n      {case['reason']}")
        return 0
    folder = pathlib.Path(args.dir).expanduser()
    if not any(folder.glob("q*.plan.pickle")):
        print(f"no frozen packets in {folder}: run tools/editor_eval.py --freeze --run first")
        return 1
    out = run(folder, args.show)
    print(f"unaltered sentences: {out['unaltered_sentences']}\n")
    print(f"{'check':14}{'planted':>9}{'caught':>9}{'flags':>8}{'controls':>10}"
          f"{'ctl fail':>10}{'false %':>9}  ready")
    for check, row in out["checks"].items():
        caught = "-" if row["caught"] is None else f"{row['caught']:.0%}"
        print(f"{check:14}{row['planted']:>9}{caught:>9}{row['unaltered_flags']:>8}"
              f"{row['controls']:>10}{row['controls_flagged']:>10}"
              f"{row['false_flag_rate']:>9.2%}  {'yes' if row['ready_to_enforce'] else 'no'}")
    if args.show:
        print(f"\n{args.show}: flags on unaltered sentences and controls")
        for name, why, sentence in out["shown"]:
            print(f"  [{name}] {why}\n      {sentence}")
    if args.misses:
        print("\nplanted, not caught:", dict(out["misses"]))
        for kind, sentence in out["miss_examples"]:
            print(f"  [{kind}] {sentence}")
    (folder / "mclovin_eval.json").write_text(json.dumps(
        {k: v for k, v in out.items() if k != "shown"}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
