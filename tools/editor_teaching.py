"""Shakespeare's worked plans: check them, and draft one for a new example (§501).

    python tools/editor_teaching.py --check              # no key, no network
    python tools/editor_teaching.py --draft 2-house-music # needs ANTHROPIC_API_KEY

`examples/plans/<example>.json` is a worked plan for the house example of the
same name (`agents/worked_plans.py`): a packet in FAM's own words, numbered as
his packets are, and the plan an editor would make from it. `--check` runs
the same checks his teaching runs at load - every number resolves, no beat is
dropped, the example exists and has a facet - and prints what fails.

`--draft` asks Opus 5.5 (pinned here, as `tools/editor_eval.py` pins its
arms) to write the worked plan for an example that has none: from the
example's own script and two worked plans as the form, a packet stating the
script's facts as a source would - plus two or three lines an editor should
leave out - and the plan. It is written only if it passes the checks, and is
a draft: read it before committing it, as you would an example.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = pathlib.Path(__file__).resolve().parent.parent
DRAFT_MODEL = "claude-opus-5-5"

DRAFT_SYSTEM = """You write teaching material for the editor of a spoken audio news service. The editor reads numbered source sentences and answers with a plan: what the episode answers, which sentences to open on, the parts in order (each a short purpose that says what the part does, never a fact, and the sentence numbers behind it), and which sentences to land on.

You are given one finished episode and two worked plans as the form. Write the worked plan this episode was made from:

1. A packet of two or three sources. Every sentence states a fact the episode itself states, plainly, as a source would - in your own words, never copied from any outlet, never a fact the episode does not hold. Mark a source "archive" only when it reports something as history; give each a short title and a published date or "reference".
2. Add two or three sentences an editor should leave out - a preview or forecast, a line of advice, or something beside the point - each true in general and harmless.
3. The plan, by sentence number only ("P1.2", "A1.1"), in the order the episode tells it: open on what it opens on, a part per turn of the story (the purposes say because, therefore or but where the story does), land on what it lands on. Leave out the lines from step 2.
4. One sentence on what the worked plan teaches.

Reply with one JSON object and nothing else: {"teaches": str, "packet": [{"packet": "plugin" or "archive", "title": str, "published": str, "sentences": [str]}], "answer": {"answers": str, "summary": [str], "opening": [str], "beats": [{"purpose": str, "sentences": [str]}], "landing": [str], "thin": false, "missing": []}}"""


def check() -> int:
    from agents import worked_plans

    bad = 0
    paths = sorted(worked_plans.PLANS.glob("*.json"))
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            print(f"  {path.name}: not JSON - {exc}")
            bad += 1
            continue
        wrong = worked_plans.problems(data, path.stem)
        bad += bool(wrong)
        print(f"  {path.stem}: " + ("; ".join(wrong) if wrong else "ok"))
    import script_generator

    planned = {p.stem for p in paths}
    unplanned = [stem for stem in sorted(script_generator.EXAMPLE_STEMS.values())
                 if stem not in planned]
    if unplanned:
        print("examples with no worked plan yet: " + ", ".join(unplanned))
    return 1 if bad else 0


def _read_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        raise ValueError("no JSON object in the reply")
    return json.loads(match.group(0))


async def draft(stem: str, force: bool) -> int:
    from agents import worked_plans
    from anthropic_client import build_async_client

    example = ROOT / "examples" / f"{stem}.txt"
    if not example.exists():
        print(f"examples/{stem}.txt does not exist")
        return 1
    target = worked_plans.PLANS / f"{stem}.json"
    if target.exists() and not force:
        print(f"{target} exists (--force to replace it)")
        return 1
    forms = [worked_plans.render(d) for d in worked_plans.plans()[:2]]
    query, _, script = example.read_text(encoding="utf-8").partition("\n\n")
    prompt = ("Two worked plans, as the form:\n\n" + "\n\n".join(forms)
              + f"\n\nThe episode to write the worked plan for:\n<request>{query.strip()}"
              f"</request>\n<episode>\n{script.strip()}\n</episode>")
    client = build_async_client()
    response = await client.messages.create(
        model=DRAFT_MODEL, max_tokens=8000, system=DRAFT_SYSTEM,
        messages=[{"role": "user", "content": prompt}])
    text = "".join(getattr(b, "text", "") for b in response.content)
    try:
        data = {"example": stem, **_read_json(text)}
    except ValueError as exc:
        print(f"the draft was not readable: {exc}")
        return 1
    wrong = worked_plans.problems(data, stem)
    if wrong:
        print("the draft fails the checks: " + "; ".join(wrong))
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"drafted {target} - read it before committing it")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--draft", default="", help="an example's file name, without .txt")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if args.draft:
        return asyncio.run(draft(args.draft, args.force))
    return check()


if __name__ == "__main__":
    raise SystemExit(main())
