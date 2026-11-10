"""Before-and-after pairs for the writer, made before launch only (§521).

    python tools/teaching_pairs.py --propose            # needs ANTHROPIC_API_KEY
    python tools/teaching_pairs.py --propose --limit 6 --dir ~/.fam/editor_eval
    python tools/teaching_pairs.py --check              # no key

The writer learns most from its own mistakes fixed. `--propose` reads the
scripts `tools/editor_eval.py` wrote from its frozen packets - never a
listener's episode, never the cache or the quality store - and picks the
ones the code measures flag most: a weak opening, an ending that recaps,
teases or asks, signposts, repetition, facts joined by "and", and the ear
checks (§521). Opus 5.5 rewrites each from the **same packet** under the
house rules, told what the code found. A rewrite is kept only if:

* the code finds fewer flaws, and no ear or story score falls;
* the grader (`episode_quality.grade_script`) marks it at least
  `MIN_GRADE_GAIN` higher on the mean of six, with no more unsupported
  claims than the draft.

Kept pairs go to `~/.fam/teaching_pairs/` (`--out`), never to `examples/`:
read each, then move the ones worth teaching into `examples/pairs/`, where
the writer's instructions show them (`TEACHING_PAIRS`). The reason line is
built in code from the flaws the rewrite fixed.

**Before launch only, on a developer's machine** (the owner, 2026-11-09:
"we absolutely can't use opus to rewrite episodes after launch"). It
refuses to run on a deployed service (`RENDER` / `RENDER_SERVICE_ID` set),
nothing on the server imports it, and no schedule runs it. Training, never
a rewrite pass: no episode a listener hears is ever rewritten.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import pickle
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAIRS = ROOT / "examples" / "pairs"
REWRITE_MODEL = "claude-opus-5-5"
#: The grade (mean of six, 1-5) a rewrite must gain to be kept.
MIN_GRADE_GAIN = 0.3

#: Each flaw the code can name, in the words the reason line uses.
FLAW_WORDS = {
    "banned_opener": "opened by orienting",
    "off_question": "opened away from what was asked",
    "unsituated": "opened with no who, what or when",
    "question_ending": "ended on a question",
    "recap": "ended on a recap",
    "tease": "ended on a tease",
    "signposts": "announced its own structure",
    "repeats": "said a thing twice",
    "additive": "joined facts with \"and\" where the story needed because or but",
    "long": "ran sentences too long to hold by ear",
    "front_loaded": "buried the subject behind a long lead",
    "figures": "stacked numbers in one sentence",
    "said_after": "named the speaker after the quote",
    "visual": "used visual language",
    "brackets": "used brackets",
    "long_list": "listed five things in a row",
    "monotone": "ran sentences all one length",
}

REWRITE_SYSTEM = """You are the senior writer of a spoken audio news service, fixing a junior writer's draft so it can be used to teach. Rewrite the draft as the house voice described below: the same question answered, from the same sources, saying only what the sources say - no fact, number, name or date they do not hold. Fix every flaw you are told about without adding new ones. Keep it no longer than the draft. Reply with the spoken script alone: no title, no notes, no lines after it.

The house rules:

"""


def refuse_on_a_server() -> str:
    """Why this may not run here, or "" - it is for before launch, on a
    developer's machine, never a deployed service."""
    if os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID"):
        return ("teaching_pairs runs before launch on a developer's machine, never on a "
                "deployed service: no episode a listener hears is ever rewritten by Opus")
    return ""


def flaws(sentences: list, query: str, evidence: str = "") -> list:
    """The flaws the code measures find in a script, as `FLAW_WORDS` keys."""
    import episode_quality

    m = episode_quality.measure(sentences, query, evidence)
    checks = m.get("checks") or {}
    found = []
    hook = checks.get("hook") or {}
    if hook.get("banned_opener"):
        found.append("banned_opener")
    if hook and not hook.get("on_question", True):
        found.append("off_question")
    if hook and not hook.get("situated", True):
        found.append("unsituated")
    ending = checks.get("ending") or {}
    if ending.get("question"):
        found.append("question_ending")
    for key in ("recap", "tease"):
        if ending.get(key):
            found.append(key)
    if checks.get("signposts"):
        found.append("signposts")
    if checks.get("repeats"):
        found.append("repeats")
    if len(checks.get("additive_joins") or []) >= 2:
        found.append("additive")
    for check, where in ((m.get("ear") or {}).get("checks") or {}).items():
        if where:
            found.append(check)
    return found


def why_line(before: list, after: list) -> str:
    fixed = [FLAW_WORDS[f] for f in before if f not in after and f in FLAW_WORDS]
    if not fixed:
        return ""
    return "The draft " + (", ".join(fixed[:-1]) + " and " + fixed[-1]
                           if len(fixed) > 1 else fixed[0]) + "; the rewrite does not."


def pair_text(query: str, before: str, after: str, why: str) -> str:
    return (f"{query.strip()}\n\nBEFORE:\n{before.strip()}\n\nAFTER:\n{after.strip()}"
            f"\n\nWHY: {why.strip()}\n")


def parse_pair(text: str) -> dict:
    """A pair file's parts, or {} - the writer's own reader."""
    import script_generator

    return script_generator.parse_pair(text)


def _split(script: str) -> list:
    return [s for s in re.split(r"(?<=[.!?])\s+", " ".join(script.split())) if s]


def _mean_grade(grade) -> float:
    import episode_quality

    if not grade:
        return 0.0
    values = []
    for metric in episode_quality.METRICS:
        v = grade.get(metric)
        v = v.get("score") if isinstance(v, dict) else v
        if isinstance(v, (int, float)):
            values.append(float(v))
    return sum(values) / len(values) if values else 0.0


def candidates(results_dir: pathlib.Path, limit: int) -> list:
    """The frozen comparison's scripts with the most flaws the code names."""
    found = []
    for path in sorted(results_dir.glob("q*.*.json")):
        data = json.loads(path.read_text())
        frozen = results_dir / (path.name.split(".", 1)[0] + ".plan.pickle")
        if not frozen.exists() or not data.get("sentences"):
            continue
        plan = pickle.loads(frozen.read_bytes())["plan"]
        named = flaws(data["sentences"], plan.query, plan.evidence)
        if named:
            found.append((len(named), -_mean_grade(data.get("grade")), path.name, data, plan,
                          named))
    found.sort(key=lambda c: (-c[0], -c[1]))
    seen, out = set(), []
    for item in found:
        if item[4].query in seen:
            continue
        seen.add(item[4].query)
        out.append(item)
    return out[:limit]


async def propose(results_dir: pathlib.Path, out: pathlib.Path, limit: int) -> int:
    import episode_quality
    import script_generator
    from anthropic_client import build_async_client

    client = build_async_client()
    out.mkdir(parents=True, exist_ok=True)
    kept = 0
    for _, _, name, data, plan, before_flaws in candidates(results_dir, limit):
        draft = " ".join(data["sentences"])
        told = "; ".join(FLAW_WORDS[f] for f in before_flaws if f in FLAW_WORDS)
        response = await client.messages.create(
            model=REWRITE_MODEL, max_tokens=8000,
            system=REWRITE_SYSTEM + script_generator.system_prompt(),
            messages=[{"role": "user", "content":
                       f"<request>{plan.query}</request>\n\n<evidence>\n{plan.evidence}\n"
                       f"</evidence>\n\n<draft>\n{draft}\n</draft>\n\nThe draft {told}."}])
        rewrite = "".join(getattr(b, "text", "") for b in response.content).strip()
        after = _split(rewrite)
        after_flaws = flaws(after, plan.query, plan.evidence)
        m_before = episode_quality.measure(data["sentences"], plan.query, plan.evidence)
        m_after = episode_quality.measure(after, plan.query, plan.evidence)
        fell = [k for k in ("hook", "storytelling", "flow")
                if (m_after["scores"].get(k) or 0) < (m_before["scores"].get(k) or 0)]
        if (m_after.get("ear") or {}).get("score", 0) < (m_before.get("ear") or {}).get("score", 0):
            fell.append("ear")
        if len(after_flaws) >= len(before_flaws) or fell:
            print(f"  {name}: not kept - flaws {len(before_flaws)} -> {len(after_flaws)}"
                  + (f", fell: {', '.join(fell)}" if fell else ""))
            continue
        g_before = data.get("grade")
        if not g_before:
            g_before = (await episode_quality.grade_script(
                client, plan.query, int(plan.minutes), "", plan.evidence, data["sentences"]))[0]
        g_after = (await episode_quality.grade_script(
            client, plan.query, int(plan.minutes), "", plan.evidence, after))[0]
        gain = _mean_grade(g_after) - _mean_grade(g_before)
        claims = (len((g_after or {}).get("unsupported_claims") or []),
                  len((g_before or {}).get("unsupported_claims") or []))
        if gain < MIN_GRADE_GAIN or claims[0] > claims[1]:
            print(f"  {name}: not kept - grade {gain:+.2f}, unsupported {claims[1]} -> {claims[0]}")
            continue
        why = why_line(before_flaws, after_flaws)
        path = out / (re.sub(r"[^a-z0-9]+", "-", plan.query.lower()).strip("-")[:48] + ".txt")
        path.write_text(pair_text(plan.query, draft, rewrite, why), encoding="utf-8")
        kept += 1
        print(f"  {name}: kept, grade {gain:+.2f} -> {path}")
    print(f"{kept} pair(s) kept in {out} - read them before moving any into examples/pairs/")
    return 0


def check() -> int:
    bad = 0
    for path in sorted(PAIRS.glob("*.txt")):
        ok = bool(parse_pair(path.read_text(encoding="utf-8")))
        bad += not ok
        print(f"  {path.name}: {'ok' if ok else 'not a pair (query, BEFORE:, AFTER:, WHY:)'}")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--propose", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--dir", default="", help="tools/editor_eval.py's results")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if args.propose:
        refused = refuse_on_a_server()
        if refused:
            print(refused)
            return 2
        results = (pathlib.Path(args.dir).expanduser() if args.dir
                   else pathlib.Path.home() / ".fam" / "editor_eval")
        out = (pathlib.Path(args.out).expanduser() if args.out
               else pathlib.Path.home() / ".fam" / "teaching_pairs")
        return asyncio.run(propose(results, out, args.limit))
    return check()


if __name__ == "__main__":
    raise SystemExit(main())
