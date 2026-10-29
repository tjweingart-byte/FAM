"""Read a briefing instead of listening to it.

    python write.py "recap of the tour championship" --minutes 3
    python write.py "why is the sky blue" --minutes 1 --no-search
    python write.py "the fed decision" --minutes 5 --model claude-sonnet-5
    python write.py "nvda stock today" --compare claude-sonnet-5,claude-haiku-5-5
    python write.py "nvda stock today" --compare claude-haiku-5-5:low,claude-haiku-5-5:low+digest
    python write.py "why is the sky blue" --minutes 1 --grade

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

Under that, **the episode's quality** (§309): the code's six proxies, 0-1,
with the sentences that cost each its points - every script, for nothing.
`--grade` adds the model grader's 1-5 on the same six (`QUALITY_GRADER_MODEL`,
a call of its own after the script). Neither replaces reading it.
"""
from __future__ import annotations

import argparse
import asyncio
import time

import research
from anthropic_client import build_async_client
from config import DEFAULT_MINUTES, settings
from script_generator import ScriptGenerator, ScriptNotes, count_words, plan_episode


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--minutes", type=float, default=DEFAULT_MINUTES)
    ap.add_argument("--model", default=None)
    ap.add_argument("--no-search", action="store_true")
    ap.add_argument("--prompt", action="store_true", help="print the prompt that was sent")
    ap.add_argument("--no-ei", action="store_true",
                    help="skip episode intelligence and search the raw query")
    ap.add_argument("--grade", action="store_true",
                    help="grade the script with the quality grader (§309) - "
                         "one more model call, after the script")
    ap.add_argument("--compare", default="",
                    help="comma-separated writers, each MODEL or MODEL:EFFORT "
                         "(e.g. claude-sonnet-5,claude-haiku-5-5:medium); add "
                         "+digest to write from the evidence cut (§270): "
                         "prepare once, then write the same brief and evidence "
                         "with each, one after another (§269)")
    args = ap.parse_args()

    import dataclasses

    import script_generator

    overrides = {}
    if args.model:
        # The writer's model (§272); `MODEL` is the cache key's and the
        # other calls'.
        overrides["writer_model"] = args.model
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
    try:
        plan = await generator.prepare(plan, notes)
    except research.NoEvidence as refused:
        # The one outcome this loop has to show rather than crash on: every
        # retriever came back empty on a question that turns on current
        # facts, so FAM refuses instead of writing it from memory (§109).
        # Printed as the finding it is - the ladder is what to look at, not
        # the prompt.
        print(f"  REFUSED after {time.perf_counter() - prep_started:.1f}s")
        print(f"    {refused}")
        print("    Every rung of the retrieval ladder came back empty. Check "
              "PARALLEL_API_KEY and PERPLEXITY_API_KEY, then the log for "
              "which rungs ran.")
        # Non-zero: a refused episode and a written one must not look the
        # same to whatever ran this - a sweep over twenty prompts reads exit
        # codes, not prose.
        return 1
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
        for angle in brief.search_angles:
            print(f"    and         {angle!r}")
        print(f"    objective   {research.objective_for(brief)}")
        if brief.broader:
            print(f"    if empty    {brief.broader!r}")
        if brief.must_establish:
            print(f"    must answer {'; '.join(brief.must_establish)}")
        if brief.cautions:
            print(f"    careful of  {'; '.join(brief.cautions)}")
        if getattr(brief, "forecast_query", ""):
            print(f"    market      {brief.forecast_query!r}")
        if getattr(brief, "source_kind", "general") != "general":
            print(f"    sources     {brief.source_kind}")
        if brief.pronounce:
            print("    say as      " + "; ".join(
                f"{p['name']} = {p['say']}" for p in brief.pronounce))
        if brief.category_path:
            print(f"    filed under {' > '.join(brief.category_path)}")
        archived = (plan.evidence or "").count(
            "Source type: " + research.TIER_LABELS["archive"])
        if archived:
            # The archive's recall (§308), added into the search results.
            print(f"    archive     {archived} earlier report(s) added to the packet")
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

    if args.compare:
        return await compare(generator, plan, args.compare)

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
    # How the voice was told to say the hard names the writer found (§165).
    for name, say in notes.pronunciations:
        print(f"  SAY: {name} = {say}")
    # The guard firing means the prompt rule did not hold. It is caught before
    # anything is spoken, so the episode is fine - but a rule that has to be
    # caught is a rule to fix, and this is where somebody would see it.
    # PROBLEMS.md §94.
    for dropped in notes.meta_openings:
        print(f'  NOTE: held back a meta opening before it was spoken: "{dropped}"')
    # §259: a score, period or result the scoreboard did not vouch for.
    for dropped in getattr(notes, "score_drops", ()):
        print(f'  NOTE: score guard removed before it was spoken: "{dropped}"')
    # §269: a market figure the material did not state.
    for dropped in getattr(notes, "figure_drops", ()):
        print(f'  NOTE: figure guard removed before it was spoken: "{dropped}"')
    if words < plan.word_budget * 0.8:
        print("  NOTE: came in short. That is allowed now - it should mean it ran "
              "out of things worth saying, not that it gave up.")
    await print_quality(generator, plan, notes, sentences, args.grade)
    return 0


async def print_quality(generator, plan, notes, sentences, grade: bool) -> None:
    """The episode's quality, measured as the app measures it (§309)."""
    import episode_quality

    measures = episode_quality.measure(sentences, plan.query, notes.written_from,
                                       notes.word_budget)
    print()
    print(episode_quality.format_measures(measures))
    if grade:
        result, model, dollars, failed = await episode_quality.grade_script(
            generator.client, plan.query, plan.minutes, notes.structure,
            notes.written_from, sentences)
        print(episode_quality.format_grade(result)
              + f"\n  ({model}, ${dollars:.4f}){'  ' + failed if failed else ''}")


def writers(spec: str) -> list:
    """`[(model, effort, digest)]` from "MODEL[:EFFORT][+digest],..."; effort
    defaults to EFFORT. `+digest` writes from the evidence cut (§270)."""
    out = []
    for item in (part.strip() for part in spec.split(",")):
        if not item:
            continue
        item, plus, flag = item.partition("+")
        model, _, effort = item.partition(":")
        out.append((model.strip(), effort.strip() or settings.effort,
                    bool(plus) and flag.strip() == "digest"))
    return out


async def compare(generator, plan, spec: str) -> int:
    """The same prepared episode written by each writer in turn (§269).

    Prepared once, above - the brief, the evidence and the live facts are
    shared, so the only difference between the scripts printed is the
    writer. That is the question a writer swap asks, and the one a listening
    test needs answered before anybody hears a cheaper writer. Sequential, so
    a slow first writer never makes the second look slow.
    """
    import dataclasses

    import metering
    import script_generator

    base = script_generator.settings
    chosen = writers(spec)
    cut_plan = whole_plan = dataclasses.replace(plan, evidence_digest="")
    if any(digest for _, _, digest in chosen):
        # Cut once, as `prepare` would, so every `+digest` writer reads the
        # same cut and every other writer the same whole packet (§270).
        import evidence_digest

        if not plan.evidence:
            print("  evidence cut: no packet to cut\n")
        else:
            result = await evidence_digest.digest(plan)
            print(f"  evidence cut: {result.outcome} - {result.detail} "
                  f"({result.seconds:.1f}s)\n")
            cut_plan = dataclasses.replace(plan, evidence_digest=result.text)
    for model, effort, digest in chosen:
        plan = cut_plan if digest else whole_plan
        script_generator.settings = dataclasses.replace(base, writer_model=model,
                                                        effort=effort)
        notes = ScriptNotes()
        started = time.perf_counter()
        first_at = None
        sentences = []
        try:
            async for sentence in generator.stream_prepared(plan, notes):
                if first_at is None:
                    first_at = time.perf_counter() - started
                sentences.append(sentence)
        except Exception as exc:  # noqa: BLE001 - one writer failing is a finding
            print(f"=== {model} · effort {effort}: FAILED - {exc}\n")
            continue
        finally:
            script_generator.settings = base
        text = " ".join(sentences)
        words = count_words(text)
        cost = metering.price_of(notes.usage).claude
        label = f"{model} · effort {effort}" + (" · evidence cut" if digest else "")
        print(f"=== {label}")
        print(text)
        print(f"  {words} words  ->  {words / base.target_wpm:.1f} min spoken"
              f"  ·  first sentence after {first_at or 0:.1f}s"
              f"  ·  finished in {time.perf_counter() - started:.1f}s"
              f"  ·  writer ${cost:.4f}")
        # The writer's tokens: input is what it read, output is its thinking
        # plus the script - so between two writers of the same script length,
        # the difference in output is the difference in thinking.
        # Cache writes too (review, §273): the first writer of a model and
        # effort pays the cold read, so compare `from cache` before timings.
        read = (notes.usage.input_tokens + notes.usage.cache_read_tokens
                + notes.usage.cache_write_tokens)
        print(f"  writer read {read} tokens ({notes.usage.cache_read_tokens} from "
              f"cache, {notes.usage.cache_write_tokens} written to it), wrote "
              f"{notes.usage.output_tokens} (thinking + script)")
        if notes.title:
            print(f'  title: "{notes.title}"')
        if notes.thread:
            print(f'  predicted follow-up (never spoken): "{notes.thread}"')
        for name in ("meta_openings", "score_drops", "figure_drops"):
            for dropped in getattr(notes, name, ()):
                print(f'  NOTE ({name}): "{dropped}"')
        import episode_quality

        print(episode_quality.format_measures(episode_quality.measure(
            sentences, plan.query, notes.written_from, notes.word_budget)))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
