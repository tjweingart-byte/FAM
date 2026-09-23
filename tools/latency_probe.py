#!/usr/bin/env python3
"""Where the wait in front of the first word goes, one step at a time.

`tools/pod_episode.py` asks a *running server* and reports what a client got.
This runs the production pipeline in this process - the real brief, the real
retrieval, the real writing call, the real voice - with the script cache off,
so every run is a cold generation and nothing it produces reaches Explore. It
prints the critical path to the first audio for each question and the median
across them:

    setup            cache key and near-match scan, before anything is asked
    brief            episode intelligence: one model call
    evidence         live lookup and retrieval, concurrently (both ladders)
    writer thinking  the writing call, request sent to first token (EFFORT)
    first sentence   first token to the first complete sentence
    voice queue      first sentence to its synthesis starting
    first synthesis  the voice rendering that sentence

Needs what the server needs: ANTHROPIC_API_KEY, EXA_API_KEY for the `exa`
backend, and a voice (`python verify_voice.py`). Without a voice it still
times everything up to the first sentence and says the voice was a stand-in.

    python tools/latency_probe.py --minutes 3 \\
        "what happened with the fed yesterday" "how does a heat pump work"

It spends what that many episodes spend. It never stops early to save money,
because a probe that ends at the first sentence cannot see the voice.
"""
from __future__ import annotations

import argparse
import asyncio
import pathlib
import statistics
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_QUESTIONS = (
    # One that needs today's facts, one evergreen, one that names a live event:
    # the three shapes EI and the retrieval ladder treat differently.
    "what happened with the fed yesterday",
    "how does a heat pump work",
    "how did the 49ers do last night",
)


async def probe(query: str, minutes: int, drain: bool) -> dict:
    import pipeline as pipeline_mod
    from script_generator import plan_episode

    pipe = pipeline_mod.PodcastPipeline(cache=None)
    plan = plan_episode(query, minutes)
    stats = pipeline_mod.GenerationStats()
    started = time.perf_counter()
    error = ""
    try:
        async for _chunk in pipe.stream_pcm(plan, stats):
            # The first synthesis is the last mark on the critical path; past
            # it the rest of the episode only matters with --drain.
            if not drain and stats.marks.at("first_tts_complete") is not None:
                break
    except Exception as exc:  # noqa: BLE001 - reported, and the run continues
        error = f"{type(exc).__name__}: {exc}"
    return {"query": query, "stages": stats.marks.stages(),
            "engine": stats.engine, "wall": time.perf_counter() - started,
            "error": error, "summary": stats.marks.summary()}


def table(runs: list[dict]) -> None:
    labels = [label for label, _a, _b in _critical_path()] + ["unaccounted",
                                                              "first audio"]
    width = max(len(r["query"]) for r in runs)
    width = min(max(width, 12), 34)
    print()
    print(f"{'':<17}" + "".join(f"{r['query'][:width]:>{width + 2}}"
                                for r in runs) + f"{'median':>10}")
    for label in labels:
        values = [r["stages"].get(label) for r in runs]
        known = [v for v in values if v is not None]
        cells = "".join(f"{('-' if v is None else f'{v:.2f}s'):>{width + 2}}"
                        for v in values)
        median = f"{statistics.median(known):.2f}s" if known else "-"
        print(f"{label:<17}{cells}{median:>10}")
    for run in runs:
        research = run["summary"].get("retrieval_seconds")
        live = run["summary"].get("live_seconds")
        print(f"\n  {run['query']!r}: engine={run['engine']} "
              f"retrieval={'-' if research is None else f'{research:.2f}s'} "
              f"live={'-' if live is None else f'{live:.2f}s'}"
              + (f"\n    FAILED: {run['error']}" if run["error"] else ""))


def _critical_path():
    from episode_marks import EpisodeMarks

    return EpisodeMarks.CRITICAL_PATH


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("questions", nargs="*", default=list(DEFAULT_QUESTIONS))
    parser.add_argument("--minutes", type=int, default=3)
    parser.add_argument("--drain", action="store_true",
                        help="speak each episode to the end rather than "
                             "stopping at the first audio")
    args = parser.parse_args(argv)

    import credentials

    if not credentials.active("ANTHROPIC_API_KEY"):
        print("No ANTHROPIC_API_KEY: every model stage would be the canned "
              "demo script, and timing that measures nothing. Run this where "
              "the server's credentials are (FAM_SECRETS, ~/.fam/env, or the "
              "environment).")
        return 2

    runs = []
    for question in args.questions:
        print(f"... {question!r}", flush=True)
        runs.append(asyncio.run(probe(question, args.minutes, args.drain)))
    table(runs)
    return 1 if any(r["error"] for r in runs) else 0


if __name__ == "__main__":
    raise SystemExit(main())
