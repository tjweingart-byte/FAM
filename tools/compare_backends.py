"""Parallel against Perplexity, on the same questions and the same brief.

    python tools/compare_backends.py --dry-run                 # the plan, spends nothing
    python tools/compare_backends.py                           # packets only, ~25 questions
    python tools/compare_backends.py --write                   # and a script from each
    python tools/compare_backends.py --write --grade           # and the grader's 1-5
    python tools/compare_backends.py --queries real.txt --write

Asked by the owner (§356): Parallel is the primary search and Perplexity the
second look, and Perplexity's terms let FAM keep what it returns while
Parallel's do not (docs/PROVIDER_TERMS.md). Whether Perplexity could be the
primary turns on how much worse it is, and nobody had measured it: §258 chose
both on price and "nothing was measured against the live APIs".

**What is held the same, so only the index differs.** One brief per question,
written once by EI and handed to both runs, so the two searches ask the same
thing with the same window and the same `must_establish`. The second look
stays on the same index (`RESEARCH_SECOND_LOOK=same`), so a Parallel run is
never quietly rescued by Perplexity or the other way round. The archive's
recall is off (`ARCHIVE_RECALL=0`): passages FAM kept earlier would land in
both packets. If a backend fails outright the ladder still falls back so the
episode can be written, and the row says so (`fell_back`) - count that row
as a loss for the backend that failed, not as its evidence.

**What is measured, per question and backend.** From the packet: sources,
distinct outlets, how many are dated and how old, the source types (wire,
major outlet, primary, ...), the characters of evidence, and which of the
brief's `must_establish` items it missed - the same coarse token test the
pipeline uses to decide on a second look. With `--write`: the script, the
code's six quality proxies (§309), and with `--grade` the model grader's 1-5.

**What is not measured: which is better.** Machines score proxies; the call is
a person's. `--write` saves both scripts for every question as A and B in a
random order, with the key in `key.json`, so they can be read or listened to
blind before anyone looks at which was which.

The live providers are installed as the server installs them at boot
(`live_sources.install()`), so a sports question is checked against the same
scoreboard it would be in production; the run prints which were installed.

Nothing here writes to the cache, the archive or the event log: it calls
`ScriptGenerator` directly, as `write.py` does, never the pipeline.

Real listeners' questions beat the built-in list: copy recent searches from
`/admin` into a file, one per line, and pass `--queries`.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import copy
import csv
import json
import os
import random
import re
import statistics
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The cloud environment the owner runs this in names the key
# `FAM_ANTHROPIC_KEY`; `config` reads `ANTHROPIC_API_KEY` at import, so the
# one is copied to the other first. A set `ANTHROPIC_API_KEY` wins.
if not os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("FAM_ANTHROPIC_KEY"):
    os.environ["ANTHROPIC_API_KEY"] = os.environ["FAM_ANTHROPIC_KEY"]

import research
from config import settings
from script_generator import ScriptGenerator, ScriptNotes, plan_episode

BACKENDS = ("parallel", "perplexity")
QUESTIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "compare_backends_questions.txt")
#: What one question costs, roughly: EI once, then per backend one or two
#: searches at $0.001 (`fast` on both) and, with `--write`, one Haiku script.
#: An estimate for the confirmation prompt, never a meter - the run prints
#: what each packet reported spending.
EST_BRIEF = 0.004
EST_SEARCH = 0.002
EST_SCRIPT = 0.01
EST_GRADE = 0.01

_SOURCE = re.compile(r"^SOURCE \d+$", re.M)
_PUBLISHED = re.compile(r"^Published: (\d{4}-\d{2}-\d{2}|unknown)", re.M)
_TYPE = re.compile(r"^Source type: (.+)$", re.M)


def load_questions(path: str) -> list:
    with open(path, encoding="utf-8") as handle:
        return [line.strip() for line in handle
                if line.strip() and not line.lstrip().startswith("#")]


@contextlib.contextmanager
def configured(**changes):
    """Set fields on the one `settings` every module shares, then put them back.

    `Settings` is frozen, and every module imported the same instance, so one
    `object.__setattr__` reaches the research module, the generator and the
    ladder alike - which a `dataclasses.replace` on one module's copy would
    not.
    """
    before = {name: getattr(settings, name) for name in changes}
    for name, value in changes.items():
        object.__setattr__(settings, name, value)
    try:
        yield
    finally:
        for name, value in before.items():
            object.__setattr__(settings, name, value)


def packet_measures(evidence: str, now: datetime) -> dict:
    """What a packet holds, read from the packet's own text (`build_packet`)."""
    evidence = evidence or ""
    stamps = _PUBLISHED.findall(evidence)
    ages = []
    for stamp in stamps:
        if stamp == "unknown":
            continue
        try:
            when = datetime.strptime(stamp, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        ages.append(max(0.0, (now - when).total_seconds() / 86400))
    types: dict = {}
    for label in _TYPE.findall(evidence):
        types[label.strip()] = types.get(label.strip(), 0) + 1
    return {
        "sources": len(_SOURCE.findall(evidence)),
        "dated": len(ages),
        "median_age_days": round(statistics.median(ages), 1) if ages else None,
        "newest_age_days": round(min(ages), 1) if ages else None,
        "evidence_chars": len(evidence),
        "types": types,
    }


async def run_backend(generator, plan, backend: str, args) -> dict:
    """One backend's packet - and script, with `--write` - from the shared brief."""
    notes = ScriptNotes()
    row: dict = {"backend": backend}
    started = time.perf_counter()
    with configured(research_backend=backend, research_second_look="same",
                    archive_recall=False):
        try:
            prepared = await generator.prepare(copy.copy(plan), notes)
        except research.NoEvidence as refused:
            row.update(refused=str(refused), seconds=round(
                time.perf_counter() - started, 2))
            return row
        row["prepare_seconds"] = round(time.perf_counter() - started, 2)
        info = dict(getattr(notes, "research", None) or {})
        row.update(
            ran=info.get("backend", ""),
            rungs="+".join(info.get("rungs") or []),
            fell_back=info.get("fell_back_from", ""),
            retried=bool(info.get("retried")),
            searches=info.get("searches"),
            search_seconds=info.get("seconds"),
            search_cost=info.get("cost"),
            results_returned=info.get("results_returned"),
            outlets=len(info.get("sources") or []),
            hosts=" ".join(info.get("sources") or []),
            missing=list(prepared.thin_on or info.get("missing") or []),
        )
        row.update(packet_measures(prepared.evidence,
                                   datetime.now(timezone.utc)))
        row["evidence"] = prepared.evidence
        if not args.write:
            return row

        import episode_quality

        sentences = []
        async for sentence in generator.stream_sentences(prepared, notes):
            sentences.append(sentence)
        row["script"] = " ".join(sentences)
        measures = episode_quality.measure(sentences, plan.query,
                                           notes.written_from, notes.word_budget)
        row["words"] = measures.get("words")
        row["proxies"] = measures.get("scores") or {}
        if args.grade:
            grade, _model, dollars, failed = await episode_quality.grade_script(
                generator.client, plan.query, plan.minutes, notes.structure,
                notes.written_from, sentences)
            row["grade"] = {m: (grade or {}).get(m, {}).get("score")
                            for m in episode_quality.METRICS} if grade else {}
            row["grade_cost"] = dollars
            if failed:
                row["grade_failed"] = failed
    return row


def _mean(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return round(statistics.mean(values), 2) if values else None


def summary(results: list, write: bool) -> str:
    """The report's table: one line per backend, then who won each question."""
    lines = ["| | " + " | ".join(BACKENDS) + " |", "|---|" + "---|" * len(BACKENDS)]

    def row(label, fn):
        cells = []
        for backend in BACKENDS:
            runs = [r["runs"][backend] for r in results if backend in r["runs"]]
            cells.append(str(fn(runs)))
        lines.append(f"| {label} | " + " | ".join(cells) + " |")

    row("questions run", len)
    row("refused (nothing found anywhere)", lambda rs: sum(1 for r in rs if r.get("refused")))
    row("fell back to another index", lambda rs: sum(1 for r in rs if r.get("fell_back")))
    row("needed its second look", lambda rs: sum(1 for r in rs if r.get("retried")))
    row("sources per packet (mean)", lambda rs: _mean(r.get("sources") for r in rs))
    row("distinct outlets in all results returned (mean)", lambda rs: _mean(r.get("outlets") for r in rs))
    row("dated sources (mean)", lambda rs: _mean(r.get("dated") for r in rs))
    row("median age, days (mean)", lambda rs: _mean(r.get("median_age_days") for r in rs))
    row("evidence characters (mean)", lambda rs: _mean(r.get("evidence_chars") for r in rs))
    row("must-establish items missed (total)",
        lambda rs: sum(len(r.get("missing") or []) for r in rs))
    row("search seconds (mean)", lambda rs: _mean(r.get("search_seconds") for r in rs))
    row("search cost, $ (total)",
        lambda rs: round(sum(r.get("search_cost") or 0 for r in rs), 4))
    if write:
        import episode_quality

        for metric in episode_quality.METRICS:
            row(f"proxy: {metric} (mean, 0-1)",
                lambda rs, m=metric: _mean((r.get("proxies") or {}).get(m) for r in rs))
            row(f"grade: {metric} (mean, 1-5)",
                lambda rs, m=metric: _mean((r.get("grade") or {}).get(m) for r in rs))

    # Per question, on the one number that most decides an episode: what the
    # brief said it needs and the packet does not have. Ties are ties.
    wins = {b: 0 for b in BACKENDS}
    ties = 0
    for result in results:
        runs = result["runs"]
        if len(runs) < 2 or any(r.get("refused") for r in runs.values()):
            continue
        missed = {b: len(runs[b].get("missing") or []) for b in BACKENDS}
        best = min(missed.values())
        leaders = [b for b in BACKENDS if missed[b] == best]
        if len(leaders) > 1:
            ties += 1
        else:
            wins[leaders[0]] += 1
    lines.append("")
    lines.append("Fewer must-establish items missed, per question: "
                 + ", ".join(f"{b} {wins[b]}" for b in BACKENDS) + f", tied {ties}.")
    return "\n".join(lines)


def save(out: str, results: list, write: bool, seed: int) -> None:
    os.makedirs(out, exist_ok=True)
    flat = []
    for index, result in enumerate(results, 1):
        for backend, run in result["runs"].items():
            flat.append({"n": index, "query": result["query"], **{
                k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
                for k, v in run.items() if k not in ("evidence", "script")}})
    if flat:
        fields = sorted({k for row in flat for k in row},
                        key=lambda k: (k not in ("n", "query", "backend"), k))
        with open(os.path.join(out, "results.csv"), "w", newline="",
                  encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(flat)
    os.makedirs(os.path.join(out, "packets"), exist_ok=True)
    for index, result in enumerate(results, 1):
        for backend, run in result["runs"].items():
            with open(os.path.join(out, "packets", f"q{index:02d}-{backend}.txt"),
                      "w", encoding="utf-8") as handle:
                handle.write(result["query"] + "\n\n" + (run.get("evidence") or ""))
    if write:
        # Blind: A and B in a random order per question, the key apart.
        shuffle = random.Random(seed)
        key = {}
        os.makedirs(os.path.join(out, "blind"), exist_ok=True)
        for index, result in enumerate(results, 1):
            order = list(BACKENDS)
            shuffle.shuffle(order)
            key[f"q{index:02d}"] = {"query": result["query"], "A": order[0], "B": order[1]}
            for letter, backend in zip("AB", order):
                script = (result["runs"].get(backend) or {}).get("script") or "(no script)"
                with open(os.path.join(out, "blind", f"q{index:02d}-{letter}.txt"),
                          "w", encoding="utf-8") as handle:
                    handle.write(result["query"] + "\n\n" + script + "\n")
        with open(os.path.join(out, "key.json"), "w", encoding="utf-8") as handle:
            json.dump(key, handle, indent=2)
    with open(os.path.join(out, "report.md"), "w", encoding="utf-8") as handle:
        handle.write(f"# Parallel vs Perplexity - {len(results)} questions\n\n")
        handle.write(summary(results, write) + "\n")
        if write:
            handle.write("\nRead `blind/` before opening `key.json`.\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--queries", default=QUESTIONS_FILE,
                    help="file of questions, one per line (# for comments)")
    ap.add_argument("--minutes", type=float, default=3.0)
    ap.add_argument("--limit", type=int, default=0, help="only the first N questions")
    ap.add_argument("--write", action="store_true",
                    help="write a script from each packet (one writer call each)")
    ap.add_argument("--grade", action="store_true",
                    help="with --write, grade each script (one more call each)")
    ap.add_argument("--out", default="",
                    help="where to save (default backend_comparison/<time>)")
    ap.add_argument("--seed", type=int, default=0,
                    help="the blind order's seed (default: from the clock)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, spend nothing")
    ap.add_argument("--yes", action="store_true", help="do not ask before spending")
    args = ap.parse_args()
    if args.grade and not args.write:
        ap.error("--grade grades scripts, so it needs --write")

    questions = load_questions(args.queries)
    if args.limit:
        questions = questions[:args.limit]
    if not questions:
        print(f"no questions in {args.queries}")
        return 2
    per_question = EST_BRIEF + len(BACKENDS) * (
        EST_SEARCH + (EST_SCRIPT if args.write else 0)
        + (EST_GRADE if args.grade else 0))
    print(f"{len(questions)} questions x {', '.join(BACKENDS)}  ·  {args.minutes} min"
          f"  ·  {'packets and scripts' if args.write else 'packets only'}"
          f"{' and grades' if args.grade else ''}")
    print(f"estimated spend ~${per_question * len(questions):.2f} "
          "(plus any live lookups the briefs ask for)")
    missing = [b for b in BACKENDS if not research.backend_available(b)]
    if args.dry_run:
        for question in questions:
            print(f"  - {question}")
        if missing:
            print("not configured here: " + ", ".join(
                f"{b.upper()}_API_KEY" for b in missing))
        return 0
    if missing:
        print("cannot compare: " + "; ".join(
            f"{b}: {research.backend_diagnose(b)[1]}" for b in missing))
        return 2
    if not args.yes and input("spend it? [y/N] ").strip().lower() != "y":
        return 1

    # The live providers the server installs at boot (`app.py`): without
    # them a sports question has no scoreboard, and the score guard cuts
    # every true score from both scripts alike (§359).
    import live_sources

    live = live_sources.install()
    print("live facts: " + (", ".join(live["installed"]) or "none installed")
          + "".join(f"\n  problem: {p}" for p in live["problems"]))

    from anthropic_client import build_async_client

    seed = args.seed or int(time.time())
    out = args.out or os.path.join(
        "backend_comparison", datetime.now().strftime("%Y%m%d-%H%M%S"))
    return asyncio.run(_run(questions, args, out, seed, build_async_client()))


async def _run(questions, args, out, seed, client) -> int:
    generator = ScriptGenerator()
    generator.client = client
    results = []
    for index, question in enumerate(questions, 1):
        plan = plan_episode(question, args.minutes, search=True)
        # The one brief both backends search from.
        plan = await generator.understand(plan, ScriptNotes())
        if plan.brief is None or getattr(plan.brief, "degraded", False):
            print(f"[{index}] {question!r}: EI did not produce a brief; both "
                  "runs search the raw query")
        runs = {}
        for backend in BACKENDS:
            runs[backend] = await run_backend(generator, plan, backend, args)
        results.append({"query": question, "runs": runs})
        print(f"[{index}/{len(questions)}] {question}")
        for backend in BACKENDS:
            r = runs[backend]
            if r.get("refused"):
                print(f"    {backend:<10} REFUSED - nothing found")
                continue
            print(f"    {backend:<10} {r.get('sources')} sources, {r.get('outlets')} outlets, "
                  f"{r.get('dated')} dated, missed {len(r.get('missing') or [])}"
                  f"{', fell back to ' + r['rungs'] if r.get('fell_back') else ''}"
                  f"{', second look' if r.get('retried') else ''}")
        save(out, results, args.write, seed)  # after every question, so a stop keeps it
    print()
    print(summary(results, args.write))
    print(f"\nsaved to {out}/ (report.md, results.csv, packets/"
          f"{', blind/ and key.json' if args.write else ''})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
