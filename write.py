"""Read a briefing instead of listening to it.

    python write.py "recap of the tour championship" --minutes 3
    python write.py "why is the sky blue" --minutes 1 --no-search
    python write.py "the fed decision" --minutes 5 --model claude-sonnet-5

The script is the product. Audio is just how it gets delivered, and listening to
a five minute episode to judge one prompt change is a slow way to work. This
prints the script, how long it will actually run, and how much it cost, in a few
seconds.

Judge it on: does the first sentence tell you something true and specific? Would
any of it survive being written about a different topic? Does it end because it
is finished, or because it ran out of room?

**Read the EI block above the script first.** A weak episode is either a weak
brief or a weak script written from a good one, and the fix for each is in a
different file - the block prints what was understood, what was actually
searched for, and what the evidence failed to establish, so the two are told
apart rather than guessed at. `--no-ei` runs the pre-EI path for comparison.

Read the last two sentences hardest. They are the part with no test behind it -
the rules say land it and stop, never tease, no rhetorical question, no recap -
and a prompt rule is only a request until you have seen the model obey it. The
predicted follow-up is printed under the script: it is never spoken, and the
script is barred from gesturing at it, so it is checked separately.
"""
from __future__ import annotations

import argparse
import asyncio
import time

from anthropic_client import build_async_client
from config import DEFAULT_MINUTES, settings
from script_generator import ScriptGenerator, ScriptNotes, count_words, plan_episode

PRICES = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--minutes", type=int, default=DEFAULT_MINUTES)
    ap.add_argument("--model", default=None)
    ap.add_argument("--no-search", action="store_true")
    ap.add_argument("--prompt", action="store_true", help="print the prompt that was sent")
    ap.add_argument("--no-ei", action="store_true",
                    help="skip episode intelligence and search the raw query")
    args = ap.parse_args()

    import dataclasses

    import script_generator

    overrides = {}
    if args.model:
        overrides["model"] = args.model
    if args.no_ei:
        overrides["episode_intelligence"] = False
    if args.no_search:
        # `enable_web_search` is the *other* switch - the model's own search
        # tool - and `plan_episode` has never read it, so this flag printed
        # "search off" and researched anyway. Unnoticeable while the default
        # was `auto` and most queries went unresearched; a lie now that every
        # episode is researched (PROBLEMS.md 76). The mode is what decides.
        overrides["search_mode"] = "never"
    if overrides:
        script_generator.settings = dataclasses.replace(settings, **overrides)

    plan = plan_episode(args.query, args.minutes,
                        search=False if args.no_search else None)
    active = script_generator.settings

    generator = ScriptGenerator()
    generator.client = build_async_client()

    # Read off the plan, not off a setting. What this line is for is telling
    # you whether the script you are about to judge was researched, and only
    # the plan knows.
    print(f'"{args.query}"  ·  {plan.minutes} min  ·  {active.model}'
          f'  ·  search {"on" if plan.search else "off"}'
          f'{f" ({active.research_backend})" if plan.search else ""}\n')

    notes = ScriptNotes()

    # Understand, look up, retrieve - explicitly, and timed, so the two things
    # this loop exists to judge can be judged separately. A bad episode is
    # either a bad brief or a bad script written from a good one, and those
    # have completely different fixes; printing only the script makes them look
    # identical. `stream_sentences` calls this again and it no-ops, so the
    # production path is still what runs.
    prep_started = time.perf_counter()
    plan = await generator.prepare(plan, notes)
    prep_seconds = time.perf_counter() - prep_started

    brief = plan.brief
    if brief is not None and not brief.degraded:
        print(f"  EI ({prep_seconds:.1f}s incl. retrieval)")
        print(f"    intent      {brief.intent}   ·   structure {brief.structure}"
              f"{'   ·   answer is a RESULT' if brief.outcome_dependent else ''}")
        print(f"    subject     {brief.subject}")
        print(f"    why now     {brief.why_now or '-'} ({brief.why_now_confidence})")
        print(f"    searched    {brief.retrieval!r}"
              f"{f' · last {brief.recency_days}d' if brief.recency_days else ' · no window'}")
        if brief.must_establish:
            print(f"    must answer {'; '.join(brief.must_establish)}")
        if brief.cautions:
            print(f"    careful of  {'; '.join(brief.cautions)}")
        if plan.thin_on:
            print(f"    NOT FOUND   {'; '.join(plan.thin_on)}  <- one search "
                  "missed it; that is a fact about the search, not the world")
        if plan.live is not None:
            # The outcome and the status, not just "we got something" - a
            # provider that failed, one that has no such game and one that is
            # not configured are three different reasons an episode is about
            # to be written blind, and this loop is where a person catches it.
            print(f"    live facts  {plan.live.outcome}"
                  f"   ·   status {plan.live.status}   ·   {plan.live.detail}")
            if plan.live.facts is not None:
                for line in plan.live.facts.facts:
                    print(f"                - {line}")
            else:
                print("                nothing live was established; the writer "
                      "is told so and must not state a result")
        print()
    elif brief is not None:
        print(f"  EI degraded: {'; '.join(brief.notes)} - searching the raw "
              f"query, which is the pre-EI behaviour\n")

    if args.prompt:
        print(script_generator.build_prompt(plan))
        print("\n" + "=" * 72 + "\n")

    started = time.perf_counter()
    first_at = None
    sentences = []
    async for sentence in generator.stream_sentences(plan, notes):
        if first_at is None:
            first_at = time.perf_counter() - started
        sentences.append(sentence)

    elapsed = time.perf_counter() - started
    text = " ".join(sentences)
    words = count_words(text)
    spoken_minutes = words / active.target_wpm

    print(text)
    print("\n" + "-" * 72)
    print(f"  {words} words  ->  {spoken_minutes:.1f} min spoken "
          f"(you asked for {plan.minutes})")
    print(f"  first sentence after {first_at or 0:.1f}s, finished in {elapsed:.1f}s")
    # Never spoken. Printed because half of the ending rewrite lives here: the
    # suggestion moved out of the script and into this line, so judging the
    # script alone would only see half of the change.
    if notes.thread:
        print(f'  predicted follow-up (never spoken): "{notes.thread}"')
    else:
        print("  NOTE: no predicted follow-up. Go Deeper falls back without one.")
    # The guard firing means the prompt rule did not hold. It is caught before
    # anything is spoken, so the episode is fine - but a rule that has to be
    # caught is a rule to fix, and this is where somebody would see it.
    # PROBLEMS.md §94.
    for dropped in notes.meta_openings:
        print(f'  NOTE: held back a meta opening before it was spoken: "{dropped}"')
    if words < plan.word_budget * 0.8:
        print("  NOTE: came in short. That is allowed now - it should mean it ran "
              "out of things worth saying, not that it gave up.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
