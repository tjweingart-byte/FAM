"""How much does the semantic term improve Made for you? An offline benchmark.

    python tools/eval_recommendations.py           # needs the model (install_embed_model.py)
    python tools/eval_recommendations.py --verbose

There is no real listening log to measure against yet, so this is a labelled
proxy (§131): for each of the 28 bank topics, three searches somebody who
wants that episode might plausibly have typed - written as a listener would
ask, not as paraphrases of the tile, and deliberately *including* ones that
share words with it, so the tag vocabulary gets its fair chance. Each set of
searches becomes one listener's whole history; the question is where the
intended tile lands in Made for you, with the ranker as shipped before §131
(tags only) and with the semantic term added.

Three scenarios, easiest first:

* **focused** - all three searches, one interest.
* **single** - one search only, which is most listeners early on.
* **mixed** - two interests interleaved (three searches each); both targets
  scored. Tests "max, not mean" - a blended history is the realistic one.

Ranks are **tie-aware**: the shipped ranker breaks ties by topic id, which is
alphabetical luck, so a tile tied with four others is scored at the average
of the five positions. A tile under `RELEVANCE_FLOOR` is not on the rail at
all and counts as a miss. The rail shows six, so hit@6 is the number a
listener sees.

What this can and cannot tell you: it measures whether the ranker puts the
right *subject* in front of somebody whose searches were about it. It says
nothing about whether they would have tapped it, which is what
`tools/ctr_report.py --by algo` measures on real traffic. And the searches
were written by the same author as the feature, which is the bias to discount
for - mitigated by writing them before running anything, and by the tag
baseline being given every word the tiles use.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The tag baseline gets the vocabulary production has: the 180-node starter
# tree every deployment applies at boot (§126), in a scratch database so this
# never reads or writes the real one. Without it the baseline is weaker than
# what ships and the semantic term looks better than it is - the first run of
# this tool did exactly that (single-search MRR 0.46 against 0.52 seeded).
if "CATEGORIES_DB" not in os.environ:
    os.environ["CATEGORIES_DB"] = os.path.join(tempfile.mkdtemp(), "categories.db")

import categories  # noqa: E402
import taste_vectors  # noqa: E402
import topics  # noqa: E402

categories.apply_seed(topics.category_tree())
topics.reset_topic_tags()

SEARCHES = {
    "nil-arms-race": ["how much are college football players getting paid now",
                      "why did that quarterback transfer for money",
                      "are college sports turning professional"],
    "operator-ceos": ["why did the founder come back as ceo",
                      "should startup founders stay in charge",
                      "boardroom fights over who runs the company"],
    "ai-agents": ["can ai actually do tasks on its own now",
                  "what is an autonomous agent in chatgpt",
                  "will ai assistants replace office work"],
    "hollywood-comebacks": ["actors who had a career revival",
                            "how did that washed up star get famous again",
                            "second acts in movie careers"],
    "golf-evolution": ["what is liv golf and why does it matter",
                       "how is the pga tour changing",
                       "why are golfers leaving for saudi money"],
    "habits-research": ["how long does it take to form a habit",
                        "atomic habits does it actually work",
                        "how to stick to a new routine"],
    "fed-next-move": ["will interest rates go down this year",
                      "why did the central bank hold rates",
                      "when will mortgage rates drop"],
    "space-race": ["what is spacex doing next",
                   "how do reusable rockets land",
                   "why is it cheaper to launch satellites now"],
    "song-breaks-internet": ["how do songs go viral on tiktok",
                             "why is that song everywhere",
                             "how do spotify playlists make hits"],
    "restaurant-scene": ["why can't i get a reservation anywhere",
                         "hottest new restaurants",
                         "how do restaurants get so popular"],
    "the-trade": ["biggest nba trade this season",
                  "why did the team trade their star player",
                  "blockbuster trade explained"],
    "sleep-science": ["how much sleep do i actually need",
                      "why do i wake up tired",
                      "is napping good for you"],
    "chip-supply": ["why is taiwan so important for chips",
                    "what does tsmc actually make",
                    "semiconductor shortage explained"],
    "hormuz": ["why are oil prices going up",
               "iran and the oil shipping lanes",
               "what happens if tankers can't get through the gulf"],
    "morning-mindset": ["best morning routine",
                        "how to stop checking my phone when i wake up",
                        "how to start the day with more energy"],
    "founder-motivation": ["how do i stay motivated building my startup",
                           "founder burnout",
                           "how to keep going when your business is struggling"],
    "housing-market": ["why are houses so expensive",
                       "will home prices fall",
                       "is now a good time to buy a house"],
    "longevity-claims": ["does intermittent fasting help you live longer",
                         "what does bryan johnson actually do",
                         "anti aging supplements that work"],
    "streaming-economics": ["why does netflix keep raising prices",
                            "why did my show get removed from streaming",
                            "too many streaming subscriptions"],
    "election-mechanics": ["how do networks call a race",
                           "when will we know who won the election",
                           "what does too close to call mean"],
    "energy-grid": ["what happens to power when the wind stops",
                    "can the grid run on solar",
                    "why do blackouts happen"],
    "attention-economy": ["why are videos getting shorter",
                          "is tiktok killing youtube",
                          "how short form video changed media"],
    "transfer-window": ["how do soccer transfers work",
                        "biggest transfer fee ever",
                        "what is a release clause in football"],
    "stadium-money": ["why do taxpayers fund stadiums",
                      "is a new stadium good for a city",
                      "team threatening to move without a new arena"],
    "anxiety-loop": ["how to stop overthinking",
                     "why do i worry so much",
                     "is anxiety ever useful"],
    "pricing-psychology": ["why do prices end in 99",
                           "tricks stores use to make you spend more",
                           "how companies decide what to charge"],
    "food-supply": ["how does food get to grocery stores",
                    "why are grocery shelves empty",
                    "where does a city's food come from"],
    "training-load": ["how do pro athletes avoid injury",
                      "load management in the nba",
                      "how elite runners structure their training"],
}

#: Pairs of interests for the mixed scenario - deliberately unrelated, so a
#: mean of the two would be about neither.
MIXED = [("golf-evolution", "ai-agents"), ("fed-next-move", "sleep-science"),
         ("streaming-economics", "transfer-window"), ("chip-supply", "anxiety-loop"),
         ("housing-market", "song-breaks-internet"), ("hormuz", "habits-research"),
         ("space-race", "restaurant-scene"), ("nil-arms-race", "longevity-claims"),
         ("election-mechanics", "morning-mindset"), ("energy-grid", "the-trade")]

NOW = 10_000_000.0
POOL = list(topics.TOPIC_BANK)


def events_for(queries):
    return [topics.Event("u", "search", "", q, topics.tags_for_text(q), NOW - (i + 1) * 3600)
            for i, q in enumerate(queries)]


def scores(events, semantic_on):
    profile = topics.taste(events, NOW)
    semantic = (taste_vectors.for_listener(events, POOL, NOW, settled=True)
                if semantic_on else {})
    return {t.id: topics._affinity(t, profile) + semantic.get(t.id, 0.0) for t in POOL}


def rank_of(target, table):
    """Tie-aware 1-based rank, or None when under the floor (not on the rail)."""
    mine = table[target]
    if mine <= topics.RELEVANCE_FLOOR:
        return None
    above = sum(1 for s in table.values() if s > mine)
    tied = sum(1 for s in table.values() if s == mine)
    return above + (tied + 1) / 2.0


def summarise(ranks):
    n = len(ranks)
    hit = lambda k: sum(1 for r in ranks if r is not None and r <= k) / n  # noqa: E731
    mrr = sum(1.0 / r for r in ranks if r is not None) / n
    missing = sum(1 for r in ranks if r is None) / n
    return {"hit@1": hit(1), "hit@3": hit(3), "hit@6": hit(6), "mrr": mrr,
            "off_rail": missing}


def single_search_changes():
    """(better, worse, unchanged) over every one-search case - the check that
    an additive term did not buy its average by losing somewhere else."""
    better = worse = same = 0
    for tid, queries in SEARCHES.items():
        for q in queries:
            a = rank_of(tid, scores(events_for([q]), False)) or 99
            b = rank_of(tid, scores(events_for([q]), True)) or 99
            better += b < a
            worse += b > a
            same += b == a
    return better, worse, same


def run(verbose=False):
    results = {}
    for mode in (False, True):
        focused, single, mixed = [], [], []
        for tid, queries in SEARCHES.items():
            r = rank_of(tid, scores(events_for(queries), mode))
            focused.append(r)
            for q in queries:
                single.append(rank_of(tid, scores(events_for([q]), mode)))
            if verbose:
                print(f"  {'sem' if mode else 'tag'} {tid:<22} focused rank {r}")
        for a, b in MIXED:
            qs = [x for pair in zip(SEARCHES[a], SEARCHES[b]) for x in pair]
            table = scores(events_for(qs), mode)
            mixed += [rank_of(a, table), rank_of(b, table)]
        results[mode] = {"focused": summarise(focused), "single": summarise(single),
                         "mixed": summarise(mixed)}
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if not taste_vectors.enabled():
        print("No semantic model: " + taste_vectors.describe().get("reason", ""))
        return 1
    results = run(args.verbose)
    print(f"{'scenario':<9} {'metric':<9} {'tags only':>10} {'+semantic':>10}")
    for scenario in ("focused", "single", "mixed"):
        for metric in ("hit@1", "hit@3", "hit@6", "mrr", "off_rail"):
            a = results[False][scenario][metric]
            b = results[True][scenario][metric]
            print(f"{scenario:<9} {metric:<9} {a:>10.2f} {b:>10.2f}")
        print()
    better, worse, same = single_search_changes()
    print(f"single-search cases: {better} better, {worse} worse, {same} unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
