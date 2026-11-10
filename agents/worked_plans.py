"""Shakespeare's worked plans (§521): how an editor plans an episode, shown.

`examples/` shows him the finished episodes FAM wants; it never showed him a
plan. Each file in `examples/plans/` is one worked plan for one of those
examples: the packet it was made from, numbered exactly as his own packets
are (`agents.editor.packets_text`), the answer he would give
(`agents.editor.EditorAnswer`), and one line on what it teaches. The episode
it became is the example of the same name, already in his `<teaching>`.

**FAM's own words, never provider text.** The packet sentences are the
example's own facts written as a source would state them, plus a few he
should leave out - a preview, a forecast, a line of advice, something beside
the point - so a plan shows choosing, not only ordering. Nothing here is
evidence; his instructions say so.

**Checked in code, every time they load**: every number resolves, no beat is
dropped, the example exists and has a facet. A plan that fails is left out
and logged, never shown half-right; `tests/test_worked_plans_521.py` fails
on it first. `python tools/editor_teaching.py --check` prints the same.

Off with `EDITOR_WORKED_PLANS=0`: he is shown the examples alone, as before.
"""
from __future__ import annotations

import json
import logging
import pathlib
from typing import Optional

log = logging.getLogger("fam.agents")

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLANS = ROOT / "examples" / "plans"


def _facets() -> dict:
    try:
        import script_generator

        return dict(script_generator.EXAMPLE_FACETS)
    except Exception:  # noqa: BLE001
        return {}


def sources_of(data: dict) -> list:
    """The worked plan's packet as `NumberedSource`s, numbered per packet."""
    from agents.editor import NumberedSource

    out, counts = [], {"plugin": 0, "archive": 0}
    for item in data.get("packet") or []:
        packet = "archive" if item.get("packet") == "archive" else "plugin"
        counts[packet] += 1
        out.append(NumberedSource(packet, counts[packet], str(item.get("title") or ""),
                                  str(item.get("published") or ""),
                                  [str(s) for s in item.get("sentences") or []]))
    return out


def problems(data: dict, stem: str = "") -> list:
    """Why this worked plan cannot be shown, in words; [] when it can."""
    from agents.editor import EditorAnswer, to_outline

    found = []
    example = str(data.get("example") or "")
    if not example:
        return ["it names no example"]
    if stem and example != stem:
        found.append(f"its file is {stem} but it names {example}")
    if not (ROOT / "examples" / f"{example}.txt").exists():
        found.append(f"examples/{example}.txt does not exist")
    if example not in _facets():
        found.append(f"examples/facets.json gives {example} no facet")
    if not str(data.get("teaches") or "").strip():
        found.append("it says nothing about what it teaches")
    try:
        answer = EditorAnswer.model_validate(data.get("answer") or {})
    except Exception as exc:  # noqa: BLE001
        return found + [f"its answer is not his answer's shape: {exc}"]
    sources = sources_of(data)
    if not sources:
        return found + ["its packet is empty"]
    outline, dropped = to_outline(answer, sources)
    if dropped:
        found.append(f"code would drop part of it: {dropped}")
    if len(outline.beats) != len(answer.beats):
        found.append("a beat would not survive the checks")
    if not outline.opening or not outline.landing:
        found.append("it has no opening or no landing")
    return found


def load() -> list:
    """Every worked plan that passes `problems`, in file order. Never raises."""
    out = []
    try:
        for path in sorted(PLANS.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                log.warning("worked plan %s unreadable: %s", path.name, exc)
                continue
            wrong = problems(data, path.stem)
            if wrong:
                log.warning("worked plan %s left out: %s", path.name, "; ".join(wrong))
                continue
            out.append(data)
    except Exception:  # noqa: BLE001
        log.warning("could not read the worked plans", exc_info=True)
    return out


_CACHE: Optional[list] = None


def plans() -> list:
    global _CACHE
    if _CACHE is None:
        _CACHE = load()
    return _CACHE


def reset() -> None:
    global _CACHE
    _CACHE = None


def _query_of(example: str) -> str:
    try:
        head = (ROOT / "examples" / f"{example}.txt").read_text(encoding="utf-8")
        return head.partition("\n\n")[0].strip()
    except OSError:
        return example


def render(data: dict) -> str:
    """One worked plan as he reads it: what it teaches, the packet behind
    its numbers, and his answer."""
    from agents.editor import packets_text

    plugin, archive = packets_text(sources_of(data))
    answer = json.dumps(data["answer"], ensure_ascii=False)
    lines = [f'--- worked plan: "{_query_of(data["example"])}" '
             f'(it became the example {data["example"]}) ---',
             f"What it teaches: {' '.join(str(data['teaches']).split())}"]
    if plugin:
        lines += ["Now (P):", plugin]
    if archive:
        lines += ["Archive (A):", archive]
    lines += ["The plan:", answer]
    return "\n".join(lines)


def teaching(facet: str = "") -> str:
    """The worked plans block for his `<teaching>`, or "" when off or none
    load. When the episode's facet has worked plans, a line names them."""
    try:
        from config import settings

        if not getattr(settings, "editor_worked_plans", True):
            return ""
        loaded = plans()
        if not loaded:
            return ""
        facets = _facets()
        closest = [d["example"] for d in loaded if facet and facets.get(d["example"]) == facet]
        head = ("Worked plans: how an editor planned some of the episodes above. Each "
                "shows the packet, numbered as yours is, and the plan made from it. "
                "Notice what each leaves out as well as what it keeps. They teach "
                "planning, never facts.")
        if closest and getattr(settings, "example_matching", True):
            head += (" This episode is the same kind of story as "
                     + " and ".join(closest) + ".")
        return "\n\n".join([head] + [render(d) for d in loaded])
    except Exception:  # noqa: BLE001 - teaching, never worth an episode
        log.warning("could not show the worked plans", exc_info=True)
        return ""
