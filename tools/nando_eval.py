"""Nando's measurement: forty questions, each with the one node it is about.

    python tools/nando_eval.py              # code only, on the seeded tree
    python tools/nando_eval.py --json
    python tools/nando_eval.py --with-brief # each question through the real brief first (needs a key)
    AGENTS=1 CONTEXTUALIZER=1 CATEGORIZER=1 python tools/nando_eval.py --with-brief
                                            # ... with C3P0's call choosing Nando's node (§360)

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
import time

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


#: Taps that carry context (§357): (question, how it was tapped, the
#: context, the node it is truly about). `tile` is a live story in the pool
#: with that category; `bank` and `dailyfam` are FAM's own questions,
#: recognised from their words; `deeper` is a Go Deeper with the title of
#: the episode it follows. Labelled by hand, like the forty.
DATE = "Saturday, October 31, 2026"
CONTEXT_TAPS = (
    ("what happened in the Eagles game", "tile", "philadelphia eagles", "philadelphia eagles"),
    ("Heat grips Miami as records fall", "tile", "extreme weather", "extreme weather"),
    ("United's new route to Lisbon", "tile", "airlines", "airlines"),
    ("why the Thunder keep winning", "tile", "nba", "nba"),
    ("Apple's quarter, explained", "tile", "smartphones", "smartphones"),
    ("how NIL money changed college football recruiting", "bank", "", "college football"),
    (f"The latest on NFL as of {DATE}: what happened in the last 24 hours", "dailyfam", "", "nfl"),
    (f"The latest on Eagles (NFL) as of {DATE}. Cover only Eagles, not NFL in general",
     "dailyfam", "", "philadelphia eagles"),
    (f"The latest on Artificial Intelligence as of {DATE}: what happened", "dailyfam", "",
     "artificial intelligence"),
    (f"The latest on Cryptocurrency as of {DATE}: what happened", "dailyfam", "", "crypto"),
    ("what about their defense", "deeper", "Philadelphia Eagles' playoff picture",
     "philadelphia eagles"),
    ("and what happens to prices next", "deeper", "Why the oil price jumped", "oil price"),
)


#: Twenty questions about names that mean two things, written and labelled
#: *after* the clash table (§358) and never used to tune it - a held-out
#: check on the forty, which the table was written beside. "" is a label
#: for a reading the tree has no node for (the bird): placing nothing is
#: right there, and any node is wrong. Ties a listener would be asked about
#: are labelled with what the readings share, or "".
HELD_OUT = (
    ("Heat advisory issued as temperatures soar in Phoenix", "extreme weather"),
    ("Heat beat the Knicks in overtime", "nba"),
    ("Hurricane makes landfall near Tampa", "extreme weather"),
    ("Hurricanes goalie pulled after three goals", "nhl"),
    ("thunderstorms and lightning expected tonight", "extreme weather"),
    ("Thunder point guard out with an injury", "nba"),
    ("Kings trade for a new goalie", "los angeles kings"),
    ("Kings point guard signs extension in Sacramento", "sacramento kings"),
    ("Panthers quarterback benched", "carolina panthers"),
    ("Rangers pitcher throws a no-hitter", "mlb"),
    ("Rangers win in overtime at the Garden on a power play", "new york rangers"),
    ("Jets coach fired after loss", "nfl"),
    ("fighter jets scrambled over the Baltic", "geopolitical conflict"),
    ("United cancels flights at Newark", "airlines"),
    ("United striker scores twice at Old Trafford", "premier league"),
    ("Apple unveils a new iPhone", "smartphones"),
    ("Amazon deforestation hits a record in Brazil", "climate science"),
    ("Amazon Web Services outage takes sites down", "cloud computing"),
    ("bald eagles return to nest along the river", ""),
    ("Mercury probe sends back new images of the planet", "space science"),
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
    # Under the label: more specific than the hand label, and true when the
    # label was ("United cancels flights" filed under `flights`, which sits
    # under `airlines`). Counted on its own, never as right.
    if label in set(tree.ancestors(placed)):
        return "narrower"
    return "wrong"


def run(places: dict) -> dict:
    """`places`: name -> function(question) -> node. Scores each."""
    tree = seeded_tree()
    out = {}
    for name, place in places.items():
        rows, totals = [], {"right": 0, "narrower": 0, "broader": 0, "unplaced": 0, "wrong": 0}
        for question, label in QUESTIONS:
            node = place(question) or ""
            verdict = score(tree, label, node)
            totals[verdict] += 1
            rows.append({"question": question, "label": label, "placed": node,
                         "verdict": verdict})
        out[name] = {"totals": totals, "rows": rows}
    return out


def run_held_out() -> dict:
    """Today against Nando on `HELD_OUT`. A "" label is right only when
    nothing is placed."""
    import stories
    from agents import categorizer

    tree = seeded_tree()
    result = {}
    for name, place in (("today (resolve_category)", stories.resolve_category),
                        ("nando, code, no brief", lambda q: categorizer.place(None, q).node)):
        totals, rows = {"right": 0, "narrower": 0, "broader": 0, "unplaced": 0, "wrong": 0}, []
        for question, label in HELD_OUT:
            node = place(question) or ""
            if not label:
                verdict = "right" if not node else "wrong"
            else:
                verdict = score(tree, label, node)
            totals[verdict] += 1
            rows.append({"question": question, "label": label, "placed": node,
                         "verdict": verdict})
        result[name] = {"totals": totals, "rows": rows}
    return result


def run_context() -> dict:
    """Today against Nando on `CONTEXT_TAPS`. A `tile` tap puts its story in
    the pool for the run, as the sweep would have."""
    import stories
    from agents import categorizer

    tree = seeded_tree()
    out = {"today (resolve_category)": [], "nando, from the tap": []}
    for question, how, context, label in CONTEXT_TAPS:
        added = None
        if how == "tile":
            added = stories.Story(subject=question, title=question, angle="",
                                  query=question, category=context,
                                  first_seen=time.time(), last_seen=time.time())
            stories.pool().stories.append(added)
        try:
            today = stories.resolve_category(question)
            prior = categorizer.prior_for(question, context if how == "deeper" else "")
            nando = categorizer.place(None, question, prior=prior).node
        finally:
            if added is not None:
                stories.pool().stories.remove(added)
        for name, node in (("today (resolve_category)", today), ("nando, from the tap", nando)):
            out[name].append({"question": question, "tap": how, "label": label,
                              "placed": node, "verdict": score(tree, label, node or "")})
    result = {}
    for name, rows in out.items():
        totals = {"right": 0, "narrower": 0, "broader": 0, "unplaced": 0, "wrong": 0}
        for r in rows:
            totals[r["verdict"]] += 1
        result[name] = {"totals": totals, "rows": rows}
    return result


def code_places() -> dict:
    import stories
    from agents import categorizer

    return {
        "today (resolve_category)": lambda q: stories.resolve_category(q),
        "nando, code, no brief": lambda q: categorizer.place(None, q).node,
    }


def brief_places() -> dict:
    """Each question through the real brief - with C3P0's call carrying
    Nando's candidates when the agents are switched on (§360) - then
    placed as at the request."""
    import episode_intelligence
    import script_generator
    from agents import categorizer, netenyahu

    def with_brief(question: str) -> str:
        notes = script_generator.ScriptNotes()
        notes.agents = netenyahu.for_episode("search")
        brief = asyncio.run(episode_intelligence.understand(question, 2, notes=notes))
        picked = (brief.contextualized or {}).get("node", "")
        return categorizer.place(brief, question, prior=categorizer.prior_for(question),
                                 picked=picked).node

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
    context = run_context()
    if args.json:
        print(json.dumps({"questions": result, "held_out": run_held_out(),
                          "taps": context}, indent=2))
        return 0
    for name, found in result.items():
        t = found["totals"]
        print(f"{name:<28} right {t['right']:>2}  narrower {t['narrower']:>2}  broader {t['broader']:>2}  "
              f"unplaced {t['unplaced']:>2}  wrong {t['wrong']:>2}   (of {len(QUESTIONS)})")
    for name, found in result.items():
        wrong = [r for r in found["rows"] if r["verdict"] == "wrong"]
        for r in wrong:
            print(f"  wrong, {name}: {r['question']!r} -> {r['placed']} (is {r['label']})")
    held = run_held_out()
    print()
    print(f"Held-out name clashes ({len(HELD_OUT)}, never used to tune the table):")
    for name, found in held.items():
        t = found["totals"]
        print(f"{name:<28} right {t['right']:>2}  narrower {t['narrower']:>2}  broader {t['broader']:>2}  "
              f"unplaced {t['unplaced']:>2}  wrong {t['wrong']:>2}")
    for name, found in held.items():
        for r in found["rows"]:
            if r["verdict"] in ("wrong",) or (name.startswith("nando") and r["verdict"] != "right"):
                print(f"  {r['verdict']}, {name}: {r['question'][:50]!r}"
                      f" -> {r['placed'] or '-'} (is {r['label'] or 'nothing'})")
    print()
    print(f"Taps that carry context ({len(CONTEXT_TAPS)}):")
    for name, found in context.items():
        t = found["totals"]
        print(f"{name:<28} right {t['right']:>2}  narrower {t['narrower']:>2}  broader {t['broader']:>2}  "
              f"unplaced {t['unplaced']:>2}  wrong {t['wrong']:>2}")
    for name, found in context.items():
        for r in found["rows"]:
            if r["verdict"] != "right":
                print(f"  {r['verdict']}, {name}: [{r['tap']}] {r['question'][:50]!r}"
                      f" -> {r['placed'] or '-'} (is {r['label']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
