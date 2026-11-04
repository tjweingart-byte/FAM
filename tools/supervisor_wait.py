"""What Mclovin's checks add in front of the first word on searchFAM (item 14, §438).

    python tools/supervisor_wait.py                 # ten search questions
    python tools/supervisor_wait.py --trials 2

The spec is about a second to the first word. Mclovin checks every sentence
before it is spoken, on search too (decided item 14), and only the first
chunk waits on it: after that the checks run while earlier sentences play.
So the number that matters is what his work adds **before the first sentence
is handed to the voice** - the code checks on the first sentences, and any
rewrite a failing one waits on.

Each question is prepared once on the search surface (brief and search, as a
tap does), then written twice from the same packets - Mclovin off, then on -
and timed: the first and second sentence from the writer's request, the time
spent in his checks on every sentence, and each rewrite asked before the
first sentence and how long it took. The off/on difference includes the
writer's own variance, so the checks' and rewrites' own times are measured
directly beside it.

Needs `ANTHROPIC_API_KEY` and a search key. One event loop (§388).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

QUESTIONS = [
    "what did the Federal Reserve decide at its latest meeting",
    "how are the Philadelphia Eagles doing this season",
    "what is happening with bitcoin",
    "what did Apple announce recently",
    "what is the latest on bird flu",
    "what is going on with tariffs between the US and China",
    "what is the latest in Formula 1",
    "how is Nvidia stock doing",
    "what has the James Webb Space Telescope found lately",
    "why did the Roman Empire fall",
]


async def one(generator, question: str, minutes: float) -> dict:
    import script_generator as sg
    from agents import netenyahu, supervisor_checks
    from script_generator import ScriptNotes, plan_episode

    notes = ScriptNotes()
    notes.agents = netenyahu.OFF
    plan = await generator.prepare(plan_episode(question, minutes), notes)

    async def write(mclovin: bool) -> dict:
        notes = ScriptNotes()
        notes.agents = netenyahu.EpisodeRun(("supervisor",) if mclovin else (), {})
        check_seconds, repairs = [], []
        original_check = supervisor_checks.Checker.check
        original_repair = sg.ScriptGenerator._repair

        def timed_check(self, sentence):
            started = time.perf_counter()
            try:
                return original_check(self, sentence)
            finally:
                check_seconds.append(time.perf_counter() - started)

        async def timed_repair(self, plan, notes, item):
            started = time.perf_counter()
            out = await original_repair(self, plan, notes, item)
            repairs.append({"seconds": time.perf_counter() - started, "kept": bool(out),
                            "why": item.why[:120], "before_first": first[0] is None})
            return out

        supervisor_checks.Checker.check = timed_check
        sg.ScriptGenerator._repair = timed_repair
        first = [None]
        times = []
        started = time.perf_counter()
        try:
            async for _sentence in generator.stream_prepared(plan, notes):
                times.append(time.perf_counter() - started)
                if first[0] is None:
                    first[0] = times[0]
        finally:
            supervisor_checks.Checker.check = original_check
            sg.ScriptGenerator._repair = original_repair
        rows = (notes.supervisor or {}).get("rows", []) if mclovin else []
        return {"first": times[0] if times else None,
                "second": times[1] if len(times) > 1 else None,
                "sentences": len(times), "check_ms": [s * 1000 for s in check_seconds],
                "repairs": repairs, "fails": len([r for r in rows if r["result"] == "fail"])}

    off = await write(False)
    on = await write(True)
    return {"question": question, "off": off, "on": on}


def _ms(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return "-"
    return f"median {statistics.median(xs) * 1000:.0f} ms, max {max(xs) * 1000:.0f} ms"


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--minutes", type=float, default=1.5)
    ap.add_argument("--only", type=int, default=0, help="first N questions")
    args = ap.parse_args()
    from anthropic_client import build_async_client
    from script_generator import ScriptGenerator

    generator = ScriptGenerator()
    generator.client = build_async_client()
    results = []
    for _trial in range(args.trials):
        for question in QUESTIONS[: args.only or None]:
            try:
                r = await one(generator, question, args.minutes)
            except Exception as exc:  # noqa: BLE001
                print(f"  failed: {question}: {type(exc).__name__}: {exc}")
                continue
            results.append(r)
            before = [x for x in r["on"]["repairs"] if x["before_first"]]
            print(f"  first sentence off {r['off']['first'] or 0:.2f}s on "
                  f"{r['on']['first'] or 0:.2f}s; checks max "
                  f"{max(r['on']['check_ms'] or [0]):.2f} ms; rewrites before the first "
                  f"word {len(before)} ({sum(x['seconds'] for x in before):.2f}s)  {question}")
    checks = [ms for r in results for ms in r["on"]["check_ms"]]
    repairs = [x for r in results for x in r["on"]["repairs"]]
    before = [x for x in repairs if x["before_first"]]
    print()
    print(f"episodes                          {len(results)}")
    print(f"first sentence, Mclovin off        {_ms([r['off']['first'] for r in results])}")
    print(f"first sentence, Mclovin on         {_ms([r['on']['first'] for r in results])}")
    print(f"second sentence, off / on          {_ms([r['off']['second'] for r in results])} / "
          f"{_ms([r['on']['second'] for r in results])}")
    if checks:
        print(f"code checks per sentence           median {statistics.median(checks):.2f} ms, "
              f"max {max(checks):.2f} ms, over {len(checks)} sentences")
    print(f"rewrites asked                     {len(repairs)} "
          f"(kept {sum(1 for x in repairs if x['kept'])})")
    print(f"rewrites before the first word     {len(before)} in {len(results)} episodes; "
          f"{_ms([x['seconds'] for x in before])}")
    for x in repairs:
        print(f"    {'kept   ' if x['kept'] else 'dropped'} {x['seconds']:.2f}s  {x['why']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
