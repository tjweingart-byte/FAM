"""Nando's measurement: forty questions, each with the one node it is about.

    python tools/nando_eval.py              # code only, on the seeded tree
    python tools/nando_eval.py --json
    python tools/nando_eval.py --with-brief # each question through the real brief first (needs a key)

§346 measured the tree's code placement on forty hand-labelled questions -
17 right, 1 wrong, 22 unplaced - but did not commit them. This is the set
rebuilt in the same kinds (shared names, colliding words, subjects the tree
lacks, plain wordings that differ from the tree's, and easy ones), committed
so before and after are the same questions from now on. Labelled once, by
hand, as the most specific node of the seeded tree each question is truly
about; where the tree has no node for the subject (Manchester United, Taylor
Swift) the label is the closest one above it, which is what Nando is asked
to find (decided item 38: a broader node rather than none, never a nearby
wrong one).

Each placement is scored against the label:

* **right** - the label itself;
* **broader** - an ancestor of the label, or its facet (true, less
  specific: allowed);
* **unplaced** - nothing;
* **wrong** - any other node (a nearby wrong one: the failure that matters).

Code only by default: `stories.resolve_category` on the question (today's
placement, §346's measure) beside `agents.categorizer.place` on the question
with no brief. `--with-brief` asks the brief for each question first (one
real call each, a few cents for the set) and places from it, which is what
Nando does at the request; this needs `ANTHROPIC_API_KEY` and is refused on
staging.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

#: (question, the node it is truly about). Kinds, roughly ten each: shared
#: names, colliding words, subjects the tree lacks, plain wordings, easy.
QUESTIONS = (
    # shared names - the reading decides the node
    ("how did Manchester United do this weekend", "premier league"),
    ("United Airlines new route to Lisbon", "airlines"),
    ("Apple's new iPhone sales", "smartphones"),
    ("Eagles vs Giants final score", "philadelphia eagles"),
    ("Lakers trade rumors", "los angeles lakers"),
    ("the Jets quarterback situation", "nfl"),
    ("Amazon warehouse workers strike", "retail business"),
    ("Mercury's orbit and what it tells us", "space science"),
    # colliding words - a word that names the wrong node
    ("Miami heat wave this week", "extreme weather"),
    ("hurricanes hitting the Carolina coast", "extreme weather"),
    ("high school football scores in my town", "high school sports"),
    ("the Fed and mortgage rates", "mortgage rates"),
    ("why the Rangers in Texas fired their manager", "mlb"),
    ("the gold rush in AI chip stocks", "semiconductors"),
    ("how the Thunder in Oklahoma City built their roster", "nba"),
    ("fusion of two big banks", "mergers acquisitions"),
    # subjects the tree lacks - the closest node above
    ("Taylor Swift tour dates", "live touring"),
    ("what the James Webb telescope found", "telescopes"),
    ("SpaceX Starship launch", "reusable rockets"),
    ("OpenAI's latest model", "language models"),
    ("Netflix subscriber numbers", "streaming business"),
    ("the new Marvel movie's opening weekend", "box office"),
    ("Wimbledon final", "tennis"),
    ("is creatine worth taking", "nutrition"),
    # plain wordings that differ from the tree's
    ("why rents keep rising", "rental market"),
    ("is bitcoin going up", "crypto"),
    ("how mRNA vaccines work", "vaccines"),
    ("how to sleep better", "sleep quality"),
    ("tech companies cutting jobs", "corporate layoffs"),
    ("electric car sales slowing", "electric vehicles"),
    ("who is ahead in the presidential polls", "polling"),
    ("the town council vote on the new library", "town council"),
    # easy - the tree's own words
    ("Fed interest rate decision", "federal reserve"),
    ("Stanley Cup odds", "stanley cup"),
    ("what's happening in Gaza", "gaza"),
    ("Ukraine peace talks", "ukraine"),
    ("tariffs on Chinese steel", "tariffs"),
    ("Formula One race results", "formula one"),
    ("oil price today", "oil price"),
    ("quantum computing breakthrough", "quantum computing"),
)


def seeded_tree():
    """The seeded tree in a throwaway file, as `category_tree()` serves it."""
    import categories
    import topics

    store = categories.CategoryStore(tempfile.mktemp(suffix=".db"))
    categories.apply_seed(store)
    topics.reset_category_tree()
    topics._CATEGORIES = store  # noqa: SLF001 - an eval's own tree
    return store


def score(tree, label: str, placed: str) -> str:
    import topics

    if not placed:
        return "unplaced"
    if placed == label:
        return "right"
    ancestry = set(tree.ancestors(label))
    import stories

    facet = stories.facet_for(label)
    if placed in ancestry or (placed in topics.FACETS and placed == facet):
        return "broader"
    return "wrong"


def run(places: dict) -> dict:
    """`places`: name -> function(question) -> node. Scores each."""
    tree = seeded_tree()
    out = {}
    for name, place in places.items():
        rows, totals = [], {"right": 0, "broader": 0, "unplaced": 0, "wrong": 0}
        for question, label in QUESTIONS:
            node = place(question) or ""
            verdict = score(tree, label, node)
            totals[verdict] += 1
            rows.append({"question": question, "label": label, "placed": node,
                         "verdict": verdict})
        out[name] = {"totals": totals, "rows": rows}
    return out


def code_places() -> dict:
    import stories
    from agents import categorizer

    return {
        "today (resolve_category)": lambda q: stories.resolve_category(q),
        "nando, code, no brief": lambda q: categorizer.place(None, q).node,
    }


def brief_places() -> dict:
    import episode_intelligence
    from agents import categorizer

    def with_brief(question: str) -> str:
        brief = asyncio.run(episode_intelligence.understand(question, 2))
        return categorizer.place(brief, question).node

    return {"nando, from the brief": with_brief}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--with-brief", action="store_true")
    args = parser.parse_args()
    places = code_places()
    if args.with_brief:
        import credentials
        import spend_guard

        if spend_guard.enabled() or not credentials.active("ANTHROPIC_API_KEY"):
            print("--with-brief needs ANTHROPIC_API_KEY on a deployment that may spend.")
            return 2
        places.update(brief_places())
    result = run(places)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    for name, found in result.items():
        t = found["totals"]
        print(f"{name:<28} right {t['right']:>2}  broader {t['broader']:>2}  "
              f"unplaced {t['unplaced']:>2}  wrong {t['wrong']:>2}   (of {len(QUESTIONS)})")
    for name, found in result.items():
        wrong = [r for r in found["rows"] if r["verdict"] == "wrong"]
        for r in wrong:
            print(f"  wrong, {name}: {r['question']!r} -> {r['placed']} (is {r['label']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
