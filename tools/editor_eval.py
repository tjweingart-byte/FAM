"""Shakespeare ahead of the tap: no editor, Haiku 5.5 high, Sonnet 5.5 (item 37, §437).

    AGENTS=1 EDITOR_AGENT=1 python tools/editor_eval.py --freeze   # research once
    AGENTS=1 EDITOR_AGENT=1 python tools/editor_eval.py --run      # write and score
    python tools/editor_eval.py --report                           # the table again

    # §501: the taught arms, on the same frozen packets
    AGENTS=1 EDITOR_AGENT=1 python tools/editor_eval.py --run --arms none_matched,haiku_taught

The owner decides Shakespeare's model ahead of the tap on this comparison
(decided item 37): about fifty fixed edition questions - Trending-style news,
DailyFAM subjects across the categories, evergreen - **researched once and
frozen**, then written three ways with the same writer:

* `none` - no editor; the writer reads the packets, as today;
* `haiku` - Shakespeare on `EDITOR_SEARCH_MODEL` (Haiku 5.5) at high effort;
* `sonnet` - Shakespeare on Sonnet 5.5 at high effort (pinned here: since
  §440 `EDITOR_MODEL` is Haiku).

Two more arms (§501), so the teaching is measured against §437's own results:

* `none_matched` - no editor; the writer's request names the house examples
  of the episode's facet (`EXAMPLE_MATCHING`);
* `haiku_taught` - Shakespeare on Haiku 5.5 high with his worked plans
  (`EDITOR_WORKED_PLANS`), the writer matched as in `none_matched`.

The first three arms run with both switched off, so they stay §437's
comparison whatever the deployment's defaults are now.

Each script is scored on what needs no person (decided item 30):
`episode_quality.measure`'s six code measures; Mclovin's sentence checks
(§435), every check counted, none enforced, so the arms are compared on what
the writer wrote; a blind 1-5 grade by Opus (`QUALITY_GRADER_MODEL`, so Sonnet
never grades itself), which sees the question, the evidence and the script
and never which arm wrote it; each arm's cost and time per episode; and,
for an arm with an editor, whether the writer followed his plan
(`episode_quality.plan_following`: opening, parts, order, landing).

One event loop for the whole run (§388). Frozen packets and results are kept
in `~/.fam/editor_eval/` (`--dir` to change), never in the repository: they
hold provider text. A run re-uses what is already written, so it can be
stopped and resumed. Needs `ANTHROPIC_API_KEY` and a search key.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import pathlib
import pickle
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: The frozen questions: (group, question). Editions are 1.5 minutes
#: (`BROWSE_MINUTES`); the evergreen ones are the bank's kind of question.
QUESTIONS = [
    # Trending-style news
    ("news", "what did the Federal Reserve decide at its latest meeting"),
    ("news", "what is happening with the war in Ukraine this week"),
    ("news", "what is the latest on the US government budget fight"),
    ("news", "what happened in the latest Israel Gaza ceasefire talks"),
    ("news", "what is going on with tariffs between the US and China"),
    ("news", "what is the latest on the UK economy"),
    ("news", "what did the Supreme Court rule on this week"),
    ("news", "what is happening with oil prices"),
    ("news", "what is the latest on artificial intelligence regulation in Europe"),
    ("news", "what is happening with the housing market in the US"),
    ("news", "what is the latest news from the Bank of Japan"),
    ("news", "what happened at the latest climate negotiations"),
    ("news", "what is the latest on the measles outbreaks"),
    ("news", "what is going on in French politics"),
    ("news", "what is the latest on immigration policy at the US border"),
    ("news", "what happened with the latest US jobs report"),
    ("news", "what is the latest on NATO defence spending"),
    ("news", "what is happening with India's economy"),
    # DailyFAM subjects, across the categories
    ("sports", "how are the Philadelphia Eagles doing this season"),
    ("sports", "what is the latest on the Los Angeles Lakers"),
    ("sports", "how is Manchester City doing in the Premier League"),
    ("sports", "what is the latest in Formula 1"),
    ("sports", "what happened in college football this weekend"),
    ("sports", "how are the New York Yankees' offseason moves going"),
    ("sports", "what is the latest in women's tennis"),
    ("money", "how is Nvidia stock doing"),
    ("money", "what is happening with bitcoin"),
    ("money", "what is the latest on Tesla"),
    ("money", "how did the stock market do this week"),
    ("tech", "what did Apple announce recently"),
    ("tech", "what is the latest from OpenAI"),
    ("tech", "what is happening with SpaceX's Starship"),
    ("tech", "what is the latest on quantum computing"),
    ("science", "what has the James Webb Space Telescope found lately"),
    ("science", "what is the latest research on weight loss drugs like Ozempic"),
    ("health", "what is the latest on bird flu"),
    ("culture", "what are the biggest movies in theaters right now"),
    ("culture", "what is the latest on Taylor Swift"),
    ("culture", "what albums came out this week"),
    ("culture", "who won the latest major book prize"),
    # Evergreen
    ("evergreen", "how do noise cancelling headphones work"),
    ("evergreen", "why did the Roman Empire fall"),
    ("evergreen", "how does the Federal Reserve set interest rates"),
    ("evergreen", "what caused the 2008 financial crisis"),
    ("evergreen", "how do vaccines train the immune system"),
    ("evergreen", "why is the Suez Canal so important"),
    ("evergreen", "how does a marathon get its distance"),
    ("evergreen", "what makes the sky blue"),
    ("evergreen", "how did the Apollo 13 crew get home"),
    ("evergreen", "how do passkeys replace passwords"),
]

ARMS = ("none", "haiku", "sonnet", "none_matched", "haiku_taught")
#: What `--arms` runs when it is not given: §437's three.
DEFAULT_ARMS = ("none", "haiku", "sonnet")
#: The editor each arm runs, or none.
ARM_EDITOR = {"haiku": "haiku", "sonnet": "sonnet", "haiku_taught": "haiku"}
#: The arms taught as §501 teaches: the writer's examples matched to the
#: story and Shakespeare's worked plans. Every other arm has both off.
TAUGHT_ARMS = frozenset({"none_matched", "haiku_taught"})
#: Each editor's model setting for Shakespeare (always at high effort, ahead).
ARM_SETTING = {"haiku": "editor_search_model", "sonnet": "editor_model"}
#: The model each editor runs, whatever the deployment's defaults are now: the
#: owner chose Haiku for `EDITOR_MODEL` on this comparison (§440), so the
#: Sonnet arm pins its own model to stay the same comparison.
ARM_MODEL = {"haiku": "anthropic:claude-haiku-5-5", "sonnet": "anthropic:claude-sonnet-5-5"}


class _Settings:
    """The arm's settings, put in place for one script and taken back after:
    the switches §501 added, and the editor's pinned model."""

    def __init__(self, arm: str) -> None:
        import config

        changes = {"editor_worked_plans": arm in TAUGHT_ARMS,
                   "example_matching": arm in TAUGHT_ARMS}
        editor = ARM_EDITOR.get(arm)
        if editor:
            changes[ARM_SETTING[editor]] = ARM_MODEL[editor]
        self.replaced = dataclasses.replace(config.settings, **changes)

    def __enter__(self):
        import config
        import script_generator
        from agents import models as agent_models

        self.modules = [config, script_generator, agent_models]
        self.kept = [m.settings for m in self.modules]
        for m in self.modules:
            m.settings = self.replaced
        return self

    def __exit__(self, *exc) -> None:
        for m, kept in zip(self.modules, self.kept):
            m.settings = kept
MINUTES = 1.5


def _dir(arg: str) -> pathlib.Path:
    path = pathlib.Path(arg).expanduser() if arg else pathlib.Path.home() / ".fam" / "editor_eval"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _slug(i: int) -> str:
    return f"q{i:02d}"


# ------------------------------------------------------------------ freeze
async def freeze(generator, out: pathlib.Path, only: list) -> None:
    """Research every question once, as an edition does (ahead, Haiku's
    brief at the ahead ceilings), and keep the prepared plan."""
    from agents import netenyahu
    from script_generator import ScriptNotes, plan_episode

    for i, (group, question) in enumerate(QUESTIONS):
        if only and i not in only:
            continue
        path = out / f"{_slug(i)}.plan.pickle"
        if path.exists():
            continue
        notes = ScriptNotes()
        notes.ahead = True
        notes.edition = True
        # No agent works on the freeze: the packets are the same for every arm.
        notes.agents = netenyahu.OFF
        started = time.perf_counter()
        try:
            plan = await generator.prepare(plan_episode(question, MINUTES), notes)
        except Exception as exc:  # noqa: BLE001
            print(f"  {_slug(i)} could not be researched: {type(exc).__name__}: {exc}")
            continue
        sources = (plan.evidence or "").count("\nSOURCE ") + (plan.evidence or "").startswith("SOURCE")
        with path.open("wb") as f:
            pickle.dump({"group": group, "question": question, "plan": plan,
                         "seconds": time.perf_counter() - started}, f)
        print(f"  {_slug(i)} frozen: {sources} sources, {time.perf_counter() - started:.1f}s"
              f"  - {question}")


# ------------------------------------------------------------------ one arm
async def write_arm(generator, frozen: dict, arm: str) -> dict:
    import metering
    from agents import editor, supervisor_checks
    from script_generator import ScriptNotes

    plan = frozen["plan"]
    notes = ScriptNotes()
    notes.ahead = True
    notes.edition = True
    started = time.perf_counter()
    editor_seconds = 0.0
    with _Settings(arm):
        if ARM_EDITOR.get(arm):
            t = time.perf_counter()
            block = await editor.outline(plan, notes, ahead=True,
                                         model_setting=ARM_SETTING[ARM_EDITOR[arm]])
            editor_seconds = time.perf_counter() - t
            if block:
                # His shape, as the editions apply it (§504).
                import script_generator

                plan = script_generator._with_shape(plan, notes)
            plan = dataclasses.replace(plan, editor_outline=block)
            if not block:
                print(f"    ({arm}: no plan - the writer read the packets)")
        sentences = [s async for s in generator.stream_prepared(plan, notes)]
    seconds = time.perf_counter() - started
    import episode_quality

    followed = episode_quality._plan_measure(notes, sentences)

    measured = episode_quality.measure(sentences, plan.query, plan.evidence,
                                       getattr(plan, "word_budget", 0))
    # Mclovin's checks, every one counted, none sent back.
    checker = supervisor_checks.Checker(plan, notes)
    checker.enforce = frozenset()
    fails: dict = {}
    for sentence in sentences:
        for c in checker.check(sentence).checks:
            if c.result == "fail":
                fails[c.check] = fails.get(c.check, 0) + 1
    cost = metering.price_of(notes.usage)
    return {"arm": arm, "sentences": sentences, "seconds": round(seconds, 2),
            "editor_seconds": round(editor_seconds, 2), "dollars": round(cost.total, 5),
            "planned": bool(ARM_EDITOR.get(arm) and getattr(plan, "editor_outline", "")),
            # Without the sentences he chose: those are provider text.
            "editor": {k: v for k, v in (getattr(notes, "editor", None) or {}).items()
                       if k != "plan"} or None,
            "plan_following": followed,
            "scores": measured.get("scores", {}),
            "ear": (measured.get("ear") or {}).get("score"), "mclovin": fails}


async def grade(client, frozen: dict, result: dict) -> dict:
    import episode_quality

    plan = frozen["plan"]
    graded, model, dollars, failed = await episode_quality.grade_script(
        client, plan.query, int(plan.minutes), "", plan.evidence, result["sentences"])
    return {"grade": graded, "grader": model, "grade_dollars": round(dollars or 0.0, 5),
            "grade_failed": failed}


async def run(generator, out: pathlib.Path, only: list, arms: list, no_grade: bool) -> None:
    import random

    for i in range(len(QUESTIONS)):
        if only and i not in only:
            continue
        path = out / f"{_slug(i)}.plan.pickle"
        if not path.exists():
            continue
        frozen = pickle.loads(path.read_bytes())
        # The order the arms are written and graded in is shuffled per
        # question, so neither the writer's cache nor the grader sees a pattern.
        order = list(arms)
        random.Random(i).shuffle(order)
        for arm in order:
            done = out / f"{_slug(i)}.{arm}.json"
            if done.exists():
                continue
            try:
                result = await write_arm(generator, frozen, arm)
                if not no_grade:
                    result.update(await grade(generator.client, frozen, result))
            except Exception as exc:  # noqa: BLE001
                print(f"  {_slug(i)} {arm} failed: {type(exc).__name__}: {exc}")
                continue
            result.update({"group": frozen["group"], "question": frozen["question"]})
            done.write_text(json.dumps(result, indent=1, default=str))
            print(f"  {_slug(i)} {arm:6} {len(result['sentences']):2} sentences "
                  f"${result['dollars']:.4f} {result['seconds']:.1f}s  {frozen['question']}")


# ------------------------------------------------------------------ the table
def _mean(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(statistics.mean(xs), 3) if xs else None


def report(out: pathlib.Path) -> dict:
    import episode_quality

    rows = {arm: [] for arm in ARMS}
    for path in sorted(out.glob("q*.*.json")):
        data = json.loads(path.read_text())
        rows.setdefault(data["arm"], []).append(data)
    table = {}
    for arm, results in rows.items():
        if not results:
            continue
        entry = {"episodes": len(results),
                 "planned": sum(1 for r in results if r.get("planned")),
                 "dollars_per_episode": _mean([r["dollars"] for r in results]),
                 "seconds_per_episode": _mean([r["seconds"] for r in results]),
                 "editor_seconds": _mean([r["editor_seconds"] for r in results])}
        for metric in episode_quality.METRICS:
            entry[f"code:{metric}"] = _mean([(r.get("scores") or {}).get(metric)
                                             for r in results])
        entry["code:ear"] = _mean([r.get("ear") for r in results])
        graded = [r.get("grade") for r in results if r.get("grade")]
        for metric in episode_quality.METRICS:
            entry[f"opus:{metric}"] = _mean([((g.get(metric) or {}).get("score")
                                              if isinstance(g.get(metric), dict)
                                              else g.get(metric)) for g in graded])
        entry["opus_graded"] = len(graded)
        # Claims the grader found no support for in the evidence, per episode.
        entry["opus_unsupported_claims"] = _mean([len(g.get("unsupported_claims") or [])
                                                  for g in graded])
        checks: dict = {}
        for r in results:
            for check, n in (r.get("mclovin") or {}).items():
                checks[check] = checks.get(check, 0) + n
        followed = [r.get("plan_following") for r in results if r.get("plan_following")]
        if followed:
            for part in episode_quality.PLAN_PARTS + ("score",):
                entry[f"plan:{part}"] = _mean([f.get(part) for f in followed])
        entry["mclovin_fails_per_episode"] = {c: round(n / len(results), 2)
                                              for c, n in sorted(checks.items())}
        table[arm] = entry
    return table


def print_table(table: dict) -> None:
    keys = []
    for entry in table.values():
        keys += [k for k in entry if k not in keys]
    arms = list(table)
    print(f"{'':32}" + "".join(f"{a:>14}" for a in arms))
    for key in keys:
        cells = []
        for arm in arms:
            value = table[arm].get(key)
            if isinstance(value, dict):
                value = ", ".join(f"{k} {v}" for k, v in value.items()) or "-"
                cells.append(f"  {value}")
            else:
                cells.append(f"{'-' if value is None else value:>14}")
        print(f"{key:32}" + "".join(cells))


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--freeze", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--arms", default=",".join(DEFAULT_ARMS))
    ap.add_argument("--only", default="", help="question numbers, e.g. 0,1,2")
    ap.add_argument("--no-grade", action="store_true")
    ap.add_argument("--dir", default="")
    args = ap.parse_args()
    out = _dir(args.dir)
    only = [int(x) for x in args.only.split(",") if x.strip()]
    if args.freeze or args.run:
        from anthropic_client import build_async_client
        from script_generator import ScriptGenerator

        generator = ScriptGenerator()
        generator.client = build_async_client()
        if args.freeze:
            await freeze(generator, out, only)
        if args.run:
            await run(generator, out, only, [a for a in args.arms.split(",") if a in ARMS],
                      args.no_grade)
    table = report(out)
    (out / "table.json").write_text(json.dumps(table, indent=1))
    print_table(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
