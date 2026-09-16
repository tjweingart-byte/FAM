"""Run the blueprint's milestone: 20 prompts, and what each one actually did.

    python tools/ei_eval.py                     # briefs and retrieval only, fast
    python tools/ei_eval.py --script            # write every episode too
    python tools/ei_eval.py --minutes 5         # at a different duration
    python tools/ei_eval.py --only sports       # one group
    python tools/ei_eval.py --no-ei             # the pre-EI path, for comparison

The milestone the packet asks for is: *20 prompts, 20 factually current
episodes, no invented events, no incorrect temporal references, every episode a
coherent story that fits its duration.* Three of those five are machine
checkable and two are not, and this tool is built around that split rather than
pretending otherwise:

**Checked here.** Whether a brief was produced or the layer degraded; whether
the search FAM actually ran differs from the words typed; whether a window was
applied to a question about a moment; whether the evidence contains what the
brief said the episode needs; how old and how well-sourced that evidence is;
and whether the script fits its duration.

**Surfaced, not judged.** Every relative time phrase the script used - "last
night", "yesterday", "two days ago" - is printed next to the publication dates
of the sources it was written from. A machine cannot tell you the episode said
"last night" about a Wednesday game. Put side by side, a person can, in
seconds, which is the whole point.

**The in-progress group is the one to run deliberately.** It cannot be run on
a schedule, because it needs something to actually be happening:

    LIVE_SPORTS_PROVIDER=fake python tools/ei_eval.py --only in-progress --script
    LIVE_FAKE_SPORTS_STATUS=final LIVE_SPORTS_PROVIDER=fake python tools/ei_eval.py --only in-progress --script
    python tools/ei_eval.py --only in-progress --script    # no provider at all

Those are the three states that matter and the three that were never checked.
Read `live facts` on each row - `facts / status=in_progress` means the episode
was told the game was on; anything else means it was told we do not know, and
the script must not contain a result either way. With no provider the row says
`not_configured`, and the script must say plainly that the current state is not
something we have, without claiming the world has reported nothing.

**Not touched.** Whether the story is any good. That is `write.py` and a
judgement call, and no harness is going to make it otherwise.

Needs an ANTHROPIC_API_KEY, and an EXA_API_KEY unless RESEARCH_BACKEND=claude.
Nothing in the build container has either, so this is a tool to run where the
keys are - it says so rather than reporting a green run on no data.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import credentials  # noqa: E402
import episode_intelligence as ei  # noqa: E402
import research as research_mod  # noqa: E402
import script_generator as sg  # noqa: E402
from script_generator import ScriptGenerator, ScriptNotes, count_words, plan_episode  # noqa: E402

#: Twenty prompts, chosen to exercise the failure modes the blueprint names
#: rather than to be representative traffic. Each group is a different way for
#: an episode to be wrong.
PROMPTS = {
    # A bare entity. The blueprint's opening example: this must not become a
    # generic company explainer if something just happened.
    "bare-entity": [
        "Nvidia",
        "Salesforce",
        "the Fed",
        "OpenAI",
    ],
    # Turns on a result that may or may not exist yet. Where "last night" and
    # invented winners come from.
    "sports": [
        "49ers game last night",
        "who won the Tour Championship",
        "Arsenal result",
        "what happened in the F1 race",
    ],
    # Turns on a number that moves. An article about a price is not a price.
    "markets": [
        "why did Salesforce fall",
        "what is the NASDAQ doing",
        "bitcoin price",
    ],
    # **Asked while it is still happening**, which is the group that did not
    # exist when this harness was written and is the one PROBLEMS.md §88 came
    # from. Not the same test as `future`: a thing that has not started has no
    # preview problem, because the previews are the correct evidence for it.
    # Here the previews are *all* that exists and they read as evidence for a
    # recap, so the only honest episode is an in-progress one.
    #
    # It cannot be run on a schedule, which is the point: run this group while
    # something is actually on, and read whether the script says where it
    # stands or tells you how it ended.
    "in-progress": [
        "Chiefs game",
        "Tell me about the Chiefs game",
        "how is the match going",
        "what is happening in the election count",
    ],
    # Explicitly about something that has not happened. The tense test: a
    # preview described in the past tense is the failure in the packet's table.
    "future": [
        "the next Fed meeting",
        "upcoming Apple event",
        "who plays in the Super Bowl",
    ],
    # No current trigger at all. These must NOT acquire an invented why-now -
    # the blueprint's "do not infer too much".
    "evergreen": [
        "how does a heat pump work",
        "why is the sky blue",
        "how do noise cancelling headphones work",
    ],
    # Asks for cause. The stock answer is not worth an episode.
    "causal": [
        "why are egg prices high",
        "why did the pound drop",
        "what caused the outage",
    ],
}

#: Relative time language. Printed beside the source dates, never judged.
TIME_PHRASES = re.compile(
    r"\b(last night|yesterday|today|tonight|this morning|this afternoon|"
    r"this evening|this week|last week|this month|last month|"
    r"\w+ days? ago|\w+ weeks? ago|\w+ months? ago|tomorrow|this weekend|"
    r"earlier today|just now|moments ago|on \w+day)\b", re.I)

#: The tense a completed event must not be described in before it has happened.
RESULT_WORDS = re.compile(
    r"\b(won|lost|beat|defeated|finished|final score|scored|the winner|"
    r"came (?:first|second|third))\b", re.I)


def keys_present() -> tuple[bool, str]:
    """Whether this machine can actually run the thing being asked for."""
    if not credentials.active("ANTHROPIC_API_KEY"):
        return False, ("ANTHROPIC_API_KEY is not set, so no brief and no script "
                       "can be produced. Run `python setup_key.py`, or set "
                       "FAM_SECRETS - see CREDENTIALS.md.")
    backend = (sg.settings.research_backend or "").lower()
    if backend == "exa":
        ok, detail = research_mod.diagnose()
        if not ok:
            return False, (f"RESEARCH_BACKEND=exa but {detail}. Set EXA_API_KEY, "
                           "or RESEARCH_BACKEND=claude to let the model search.")
    return True, ""


async def one(generator: ScriptGenerator, query: str, minutes: int,
              write_script: bool) -> dict:
    """Understand, retrieve, optionally write - and record what each step did."""
    notes = ScriptNotes()
    plan = plan_episode(query, minutes)

    started = time.perf_counter()
    try:
        plan = await generator.prepare(plan, notes)
    except Exception as exc:  # noqa: BLE001 - a failed row must not end the run
        return {"query": query, "error": f"{type(exc).__name__}: {exc}"}
    prep_seconds = time.perf_counter() - started

    brief = plan.brief
    row = {
        "query": query,
        "prep_seconds": prep_seconds,
        "degraded": bool(getattr(brief, "degraded", True)),
        "intent": getattr(brief, "intent", "-"),
        "structure": getattr(brief, "structure", "-"),
        "outcome_dependent": bool(getattr(brief, "outcome_dependent", False)),
        "why_now": getattr(brief, "why_now", ""),
        "confidence": getattr(brief, "why_now_confidence", "-"),
        "searched": getattr(brief, "retrieval", query),
        "window": getattr(brief, "recency_days", 0),
        "must": list(getattr(brief, "must_establish", [])),
        "missing": list(plan.thin_on),
        "live": getattr(plan.live, "detail", "") if plan.live is not None else "",
        "live_outcome": getattr(plan.live, "outcome", "") if plan.live is not None else "",
        "live_status": getattr(plan.live, "status", "") if plan.live is not None else "",
        "research": notes.research,
        # The dates the episode had to reason from. Printed beside whatever
        # relative phrases the script used, because that comparison is the only
        # way to see a wrong tense and no machine can make it.
        "source_dates": re.findall(r"Published: (\S+) \(([^)]+)\)", plan.evidence),
        "source_grades": re.findall(r"Source type: ([^\n]+)", plan.evidence),
        "script": "",
        "words": 0,
    }

    if write_script:
        started = time.perf_counter()
        sentences = []
        try:
            async for sentence in generator.stream_sentences(plan, notes):
                sentences.append(sentence)
        except Exception as exc:  # noqa: BLE001
            row["error"] = f"writing failed: {type(exc).__name__}: {exc}"
            return row
        row["script"] = " ".join(sentences)
        row["words"] = count_words(row["script"])
        row["write_seconds"] = time.perf_counter() - started
        row["budget"] = plan.word_budget
        row["thread"] = notes.thread

    row["cost"] = notes.usage.as_dict()
    return row


def show(row: dict, minutes: int, write_script: bool) -> None:
    print("=" * 78)
    print(f'  "{row["query"]}"')
    if row.get("error"):
        print(f"  FAILED: {row['error']}")
        return

    if row["degraded"]:
        print("  EI DEGRADED - the raw query was searched (the pre-EI path)")
    else:
        print(f"  {row['intent']} / {row['structure']}"
              f"{'   ·   answer is a RESULT' if row['outcome_dependent'] else ''}"
              f"   ·   why-now: {row['why_now'] or '-'} ({row['confidence']})")
    window = f"  · last {row['window']}d" if row["window"] else "  · no window"
    print(f"  searched  {row['searched']!r}{window}")

    if row["must"]:
        print(f"  must answer  {'; '.join(row['must'])}")
    if row["missing"]:
        print(f"  NOT ESTABLISHED  {'; '.join(row['missing'])}"
              "   <- the episode must say so, not fill it in")
    if row["live_outcome"]:
        # The whole point of the outcome vocabulary: "no provider", "provider
        # broke" and "no such game" are three different things and a report
        # that prints one word for all of them cannot be read.
        mark = "" if row["live_outcome"] == "facts" else "   <- NO LIVE EVIDENCE"
        print(f"  live facts  {row['live_outcome']} / status={row['live_status']}"
              f"   ({row['live']}){mark}")

    if row["source_dates"]:
        ages = ", ".join(f"{stamp} ({age})" for stamp, age in row["source_dates"])
        print(f"  evidence dated  {ages}")
    else:
        print("  evidence dated  NONE - the episode has nothing to date events by")
    if row["source_grades"]:
        print(f"  evidence graded  {'; '.join(row['source_grades'])}")

    if write_script and row["script"]:
        fit = row["words"] / max(1, row["budget"])
        flag = "" if 0.7 <= fit <= 1.1 else "   <- outside the duration"
        print(f"  {row['words']} words against a {row['budget']} budget "
              f"({fit:.0%}){flag}")

        said = TIME_PHRASES.findall(row["script"])
        if said:
            print(f"  TIME PHRASES USED: {', '.join(sorted(set(s.lower() for s in said)))}")
            print("    ^ check each against the source dates above. This is the "
                  "one thing no machine here can check for you.")
        results = RESULT_WORDS.findall(row["script"])
        if results and row["outcome_dependent"]:
            print(f"  RESULT LANGUAGE: {', '.join(sorted(set(r.lower() for r in results)))}")
            print("    ^ the answer to this one IS a result, so check the source "
                  "dates above: if they all predate the event, the episode was "
                  "written from previews and this language is invented. That is "
                  "§88, and it is the check this harness exists for.")
        elif results:
            print(f"  RESULT LANGUAGE: {', '.join(sorted(set(r.lower() for r in results)))}")
            print("    ^ if this event has not happened yet, that is an "
                  "invented result.")
        print()
        print("  " + row["script"][:600] + ("..." if len(row["script"]) > 600 else ""))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=3)
    ap.add_argument("--script", action="store_true",
                    help="write every episode, not just the brief and retrieval")
    ap.add_argument("--only", default="", help=f"one of: {', '.join(PROMPTS)}")
    ap.add_argument("--no-ei", action="store_true",
                    help="run the pre-EI path, for comparison")
    args = ap.parse_args()

    if args.no_ei:
        import dataclasses
        sg.settings = dataclasses.replace(sg.settings, episode_intelligence=False)

    ok, why = keys_present()
    if not ok:
        print(f"cannot run: {why}")
        return 2

    groups = {args.only: PROMPTS[args.only]} if args.only else PROMPTS
    generator = ScriptGenerator()

    rows: list = []
    for group, queries in groups.items():
        print(f"\n### {group}\n")
        for query in queries:
            row = await one(generator, query, args.minutes, args.script)
            rows.append(row)
            show(row, args.minutes, args.script)

    # --- the summary -------------------------------------------------------
    print("\n" + "=" * 78)
    total = len(rows)
    failed = [r for r in rows if r.get("error")]
    degraded = [r for r in rows if not r.get("error") and r["degraded"]]
    rewritten = [r for r in rows if not r.get("error")
                 and r["searched"].strip().lower() != r["query"].strip().lower()]
    thin = [r for r in rows if not r.get("error") and r["missing"]]
    undated = [r for r in rows if not r.get("error") and not r["source_dates"]]

    print(f"  {total} prompts")
    print(f"  {len(failed)} failed outright")
    print(f"  {len(degraded)} degraded to the raw query")
    print(f"  {len(rewritten)} had their search rewritten by EI")
    print(f"  {len(thin)} came back missing something the brief asked for")
    print(f"  {len(undated)} had no dated evidence at all"
          + ("   <- these cannot get tense right by anything but luck" if undated else ""))

    spend = sum(r.get("cost", {}).get("exa_cost", 0.0) for r in rows)
    calls = sum(r.get("cost", {}).get("model_calls", 0) for r in rows)
    print(f"  {calls} model calls, ${spend:.3f} of retrieval")
    if rows and not args.script:
        print("\n  Briefs and retrieval only. Add --script to judge the episodes,"
              "\n  which is where the temporal check actually happens.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
