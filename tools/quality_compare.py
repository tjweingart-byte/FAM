"""Examples by node, measured: the same prompts written both ways (PROBLEMS.md §308).

    python tools/quality_compare.py                  # every ei_eval prompt, 1 min
    python tools/quality_compare.py --only sports    # one group
    python tools/quality_compare.py --minutes 2 --grade

Each prompt is prepared once - one brief, one search, one packet - and then
written twice from that same preparation: once shown every example
(`EXAMPLES_BY_NODE=0`, the arm called `eval-all`) and once shown the nearest
(`EXAMPLES_BY_NODE=1`, `eval-node`). So the only difference between the two
scripts printed is which examples the writer read, which is the question.

Both are kept in this machine's quality store (`EPISODE_QUALITY_DB`) under
those two origins, so `/admin` → Episode quality shows them side by side and
lists them under "Score these" for the owner to score by hand - twenty or so
scored is what tells whether the grader can be trusted ([episode-quality]).
`--grade` also has the grader score every one (it spends, inside
`QUALITY_GRADE_DAILY`).

Needs an ANTHROPIC_API_KEY, and a search key unless RESEARCH_BACKEND=gdelt;
refuses on staging, which spends nothing ([zero-spend-staging]). The build
container has neither key, so it says so rather than reporting nothing.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import episode_quality as Q  # noqa: E402

ARMS = (("eval-all", False), ("eval-node", True))


def _key(run: str, arm: str, query: str, minutes: int) -> str:
    """A 64-hex id like a cache key's, so each script is an episode the admin
    page can open and score - never a cache key, so nothing serves it."""
    return hashlib.sha256(f"quality-compare|{run}|{arm}|{query}|{minutes}"
                          .encode()).hexdigest()


async def _write(generator, plan, examples_by_node: bool):
    import script_generator as sg
    from script_generator import ScriptNotes

    base = sg.settings
    sg.settings = dataclasses.replace(base, examples_by_node=examples_by_node)
    notes = ScriptNotes()
    try:
        sentences = [s async for s in generator.stream_prepared(plan, notes)]
    finally:
        sg.settings = base
    return sentences, notes


async def run(prompts: list, minutes: int, grade: bool) -> int:
    import credentials
    import spend_guard
    from anthropic_client import build_async_client
    from script_generator import ScriptGenerator, ScriptNotes, plan_episode

    if spend_guard.enabled():
        print("cannot run: this is a zero-spend deployment (staging)")
        return 1
    key = credentials.active("ANTHROPIC_API_KEY")
    if not key:
        print("cannot run: no ANTHROPIC_API_KEY on this machine")
        return 1
    client = build_async_client(key)
    generator = ScriptGenerator()
    generator.client = client
    run_id = time.strftime("%Y%m%d-%H%M%S")
    stamp = int(time.time())
    written = {arm: [] for arm, _ in ARMS}

    for query in prompts:
        print("=" * 78)
        print(f'  "{query}"  ·  {minutes} min')
        try:
            plan = await generator.prepare(plan_episode(query, minutes),
                                           ScriptNotes())
        except Exception as exc:  # noqa: BLE001 - one prompt is not the run
            print(f"  could not prepare: {type(exc).__name__}: {exc}")
            continue
        for arm, by_node in ARMS:
            try:
                sentences, notes = await _write(generator, plan, by_node)
            except Exception as exc:  # noqa: BLE001
                print(f"  {arm}: writing failed: {type(exc).__name__}: {exc}")
                continue
            episode = _key(run_id, arm, query, minutes)
            Q.note(episode, stamp, query, minutes, arm, sentences, notes)
            written[arm].append((episode, stamp))
            scores = Q.measure(sentences, query, notes.written_from,
                               notes.word_budget)["scores"]
            shown = ", ".join(notes.examples) or "every example"
            print(f"\n  --- {arm}: shown {shown}")
            print("  " + " ".join(sentences))
            print("  code: " + "  ".join(
                f"{m} {'-' if v is None else f'{v:.2f}'}" for m, v in scores.items()))
            if grade:
                done = await Q.grade_stored(client, episode, stamp)
                if done:
                    print("  grade: " + "  ".join(
                        f"{m} {done[m]['score']}" for m in Q.METRICS if m in done))
        print()

    print("=" * 78)
    print(f"  run {run_id}: the code proxies' means, per arm")
    for arm, _ in ARMS:
        rows = [Q.store().get(k, s) for k, s in written[arm]]
        rows = [r for r in rows if r]
        means = _means(rows)
        print(f"  {arm:<10} {len(rows):>3} episodes  " + "  ".join(
            f"{m} {'-' if v is None else f'{v:.2f}'}" for m, v in means.items()))
    print("\n  Score them by hand: /admin → Episode quality → Score these.")
    return 0


def _means(rows: list) -> dict:
    import json

    out = {}
    for metric in Q.METRICS:
        values = []
        for row in rows:
            scores = (json.loads(row.get("measures") or "{}").get("scores") or {})
            if scores.get(metric) is not None:
                values.append(scores[metric])
        out[metric] = sum(values) / len(values) if values else None
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=1)
    ap.add_argument("--only", default="", help="one ei_eval group")
    ap.add_argument("--grade", action="store_true",
                    help="have the grader score every script (spends)")
    args = ap.parse_args()
    from ei_eval import PROMPTS

    groups = [args.only] if args.only else list(PROMPTS)
    unknown = [g for g in groups if g not in PROMPTS]
    if unknown:
        print(f"no such group: {', '.join(unknown)} (groups: {', '.join(PROMPTS)})")
        return 1
    prompts = [q for g in groups for q in PROMPTS[g]]
    return asyncio.run(run(prompts, args.minutes, args.grade))


if __name__ == "__main__":
    raise SystemExit(main())
