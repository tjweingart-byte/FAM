"""One real episode, with every intermediate kept, so quality can be judged.

    python tools/visual_trace.py "how do undersea cables get repaired"

This is the tool for the question "the illustration is not good enough - where
did it go wrong?", which has at least six possible answers and no way to tell
them apart from the finished picture:

1. the **Visual Director** chose a dull or undrawable subject
2. the **prompt** did not carry the references, or carried them weakly
3. the **image model** returned something that is not FAM's style
4. the **threshold** lost part of the line
5. the **thinning or the traversal** broke or retraced it
6. the **smoothing** rounded the life out of it

So it runs the real path - real episode intelligence, real research, the real
Visual Director, the real OpenAI provider, the real line processor - and writes
every stage to a folder it does not clean up.

    visual-traces/<timestamp>-<slug>/
        episode.json            what FAM understood the episode to be
        references/             the approved illustrations that were in force
        attempt-1/
            director-brief.json what the illustration was to be OF
            prompt.txt          the exact text sent to the image model
            1-source.png        the artwork as the model returned it
            2-ink-mask.png      what was judged to be line
            3-skeleton.png      thinned to a centreline
            4-route.png         the traversal, before any smoothing
            5-final.png         the finished vector, rendered
            6-overlay.png       the finished line over the original artwork
        visual.svg              the canonical asset
        thumbnail.png           rendered from the vector, at full size
        result.json             status, metrics, validation, timings, cost

**It spends money.** One episode is one image (about $0.17 at
`VISUAL_IMAGE_QUALITY=high`), plus the director's small model call, plus
whatever the episode itself costs in research and script. Retries cost another
image each. Nothing here is free, and nothing here is cached.

`--dry-run` runs the whole path with the synthetic provider instead, which
costs nothing and proves the harness before you spend on it.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import pathlib
import re
import shutil
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "episode"


def say(*parts) -> None:
    print(*parts, flush=True)


async def run(query: str, minutes: int, folder: pathlib.Path,
              dry_run: bool) -> int:
    import credentials
    import config
    import understanding
    import visual_provider
    import visual_style
    import visuals
    from script_generator import ScriptGenerator, ScriptNotes, plan_episode

    credentials.load()

    if dry_run:
        # Everything else is the real path; only the pixels are local.
        for module in (visuals, visual_provider):
            module.settings = dataclasses.replace(
                config.settings, visual_image_provider="synthetic")

    provider = visual_provider.build_provider()
    ready, why = provider.configured()
    say(f"\n{BOLD}Visual trace{RESET}")
    say(f"  query      {query!r}  ({minutes} min)")
    say(f"  provider   {provider.name} · {getattr(provider, 'model', '-')} · "
        f"{getattr(provider, 'quality', '-')}")
    if not ready:
        say(f"\n{BOLD}Cannot run: {why}{RESET}\n")
        return 1

    references = visual_style.references()
    if references:
        say(f"  style      {len(references)} approved reference(s): "
            + ", ".join(ref.name for ref in references))
    else:
        say(f"  style      {BOLD}NO REFERENCES{RESET} - the house style is "
            f"being described in words only.")
        say(f"{DIM}             Drop the approved illustrations into "
            f"{visual_style.REFERENCE_DIR} and run again.{RESET}")
    say(f"  writing    {'live' if credentials.active('ANTHROPIC_API_KEY') else 'NO KEY - the director and EI will degrade'}")
    say(f"  trace      {folder}")

    folder.mkdir(parents=True, exist_ok=True)
    # Copied rather than named, so the folder is still readable after somebody
    # changes what is in visual_references/ - which is the whole point of
    # running this more than once.
    if references:
        kept = folder / "references"
        kept.mkdir(exist_ok=True)
        for ref in references:
            shutil.copy2(visual_style.REFERENCE_DIR / ref.name, kept / ref.name)

    visuals.TRACE_DIR = folder
    understanding.clear()

    # --- the real episode -------------------------------------------------
    # Exactly production's ordering: the drawing is asked for first and waits
    # on the bus, then the writing path publishes what it worked out, then the
    # director picks it up. Starting them the other way round would test a
    # sequence the product never runs.
    started = time.monotonic()
    visual_id = visuals.request(query, minutes=minutes, surface="search",
                                reason="visual_trace", live=True)
    if not visual_id:
        say(f"\n{BOLD}Nothing was started.{RESET} That question is not "
            f"eligible for an illustration, or VISUALS=0.\n")
        return 1

    plan = plan_episode(query, minutes, search=True)
    notes = ScriptNotes()
    episode: dict = {"query": query, "minutes": minutes}
    try:
        prepared = await ScriptGenerator().prepare(plan, notes)
        episode["brief"] = (prepared.brief.as_dict()
                            if prepared.brief is not None else None)
        episode["evidence"] = prepared.evidence
        episode["thin_on"] = list(prepared.thin_on)
        episode["research"] = notes.research
        say(f"  {DIM}episode understood in {time.monotonic() - started:.1f}s"
            f"{RESET}")
    except Exception as exc:  # noqa: BLE001 - the drawing is the subject here
        episode["error"] = str(exc)
        say(f"  {DIM}the episode's own understanding failed ({exc}); the "
            f"director will work from the query{RESET}")

    (folder / "episode.json").write_text(
        json.dumps(episode, indent=2, default=str), encoding="utf-8")

    # --- wait for the drawing --------------------------------------------
    say(f"\n{DIM}  drawing…{RESET}")
    record = None
    last = ""
    for _ in range(1200):
        await asyncio.sleep(0.5)
        record = visuals.store().get(visual_id)
        if record is None:
            continue
        if record.status != last:
            last = record.status
            say(f"{DIM}    {record.status}{RESET}")
        if record.status in ("ready", "failed", "unconfigured"):
            break

    elapsed = time.monotonic() - started
    if record is None:
        say(f"\n{BOLD}No record was ever written.{RESET}\n")
        return 1

    # --- keep everything --------------------------------------------------
    result = {
        "status": record.status,
        "attempts": record.attempts,
        "provider": record.provider,
        "model": record.model,
        "error": record.error,
        "cost_usd": round(record.cost_usd, 4),
        "cost_basis": "priced from the provider's published rate, not billed",
        "total_seconds": round(elapsed, 1),
        "director_brief": record.brief,
        "metrics": record.metrics,
        "references": [ref.name for ref in references],
        "visual_id": record.id,
    }
    (folder / "result.json").write_text(
        json.dumps(result, indent=2, default=str), encoding="utf-8")

    if record.status == "ready":
        (folder / "visual.svg").write_text(visuals.svg(record.id),
                                           encoding="utf-8")
        (folder / "thumbnail.png").write_bytes(visuals.thumbnail(record.id))

    # --- say what happened ------------------------------------------------
    say("")
    if record.status == "ready":
        metrics = record.metrics
        say(f"  {BOLD}ready{RESET} in {elapsed:.1f}s on attempt "
            f"{record.attempts} · ${record.cost_usd:.3f} priced")
        say(f"  curves {metrics.get('curves')} · retraced "
            f"{metrics.get('retraced', 0):.1%} · bridged "
            f"{metrics.get('bridged')} · components "
            f"{metrics.get('components')}")
        if metrics.get("warnings"):
            say(f"  {BOLD}warnings{RESET} {'; '.join(metrics['warnings'])}")
        subject = (record.brief or {}).get("subject", "")
        form = (record.brief or {}).get("primary_form", "")
        if subject or form:
            say(f"  drawn as   {subject}{' / ' + form if form else ''}")
    else:
        say(f"  {BOLD}{record.status}{RESET} after {record.attempts} attempt(s) "
            f"in {elapsed:.1f}s · ${record.cost_usd:.3f} priced")
        say(f"  {record.error}")
        say(f"\n{DIM}  Every attempt's artwork and stages are still in the "
            f"trace folder - a failure is the most informative thing this "
            f"tool produces.{RESET}")

    say(f"\n{BOLD}Look at, in this order:{RESET}")
    say(f"  {folder}/attempt-1/1-source.png     did the model draw FAM's style?")
    say(f"  {folder}/attempt-1/6-overlay.png    did the vectoriser keep it?")
    say(f"  {folder}/attempt-1/3-skeleton.png   if not, was it thinning or the threshold?")
    say(f"  {folder}/attempt-1/director-brief.json   was it even the right subject?")
    say(f"  {folder}/attempt-1/prompt.txt       what was actually asked for")
    say("")
    return 0 if record.status == "ready" else 2


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one real episode and keep every visual intermediate.")
    parser.add_argument("query", help="what to ask FAM")
    parser.add_argument("--minutes", type=int, default=3)
    parser.add_argument("--out", default="",
                        help="where to write the trace "
                             "(default: visual-traces/<timestamp>-<slug>)")
    parser.add_argument("--dry-run", action="store_true",
                        help="use the synthetic provider - proves the harness "
                             "without spending anything")
    args = parser.parse_args()

    folder = (pathlib.Path(args.out) if args.out else
              ROOT / "visual-traces"
              / f"{time.strftime('%Y%m%d-%H%M%S')}-{slug(args.query)}")
    # Deliberately never cleaned up, here or anywhere else. The whole value of
    # this tool is that the evidence is still there tomorrow.
    return asyncio.run(run(args.query, args.minutes, folder, args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
