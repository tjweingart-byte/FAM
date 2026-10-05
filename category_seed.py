"""The starter vocabulary: a category tree a deployment has before anybody
has searched for anything.

## Why a seed exists at all

`categories.py` grows a vocabulary out of what listeners actually search for,
and everything about it is right except its first day. A phrase becomes a node
when `MIN_LISTENERS` different people have used it, so a deployment with no
traffic has **no vocabulary at all**, and one with a little traffic has a
handful of nodes shaped by whoever happened to arrive first. Until then the
ranker is back to the eight facets and twenty-nine hand-written subtags that
`categories.py` exists because of.

That costs two different things, and they are the two reasons this file is
here:

* **Resolution on day one.** A listener who searches "what the fed is likely
  to do about interest rates" should have `interest rates -> central banks ->
  money` in their taste profile from that first search, not `money` alone
  until two more strangers happen to ask about the same thing. Every rail on
  myFAM is a query over that profile, so a profile that cannot tell one
  corner of `money` from another is a page that cannot either - which is
  visible as a browse page that offers much the same tiles in much the same
  order to everybody.
* **Something for the algorithm to grow from.** A tree grown from nothing has
  no levels: containment can only deepen a phrase under another phrase
  somebody typed, so `cincinnati bengals` lands under `sports` and stays
  there until enough people type `nfl` on its own. Seeded, it lands under
  `american football` the first time the model places it - and even with no
  model at all, containment now has real intermediate nodes to deepen
  *against*. This is the half of the tree that `categories.py`'s own
  docstring says no amount of reading what people typed can invent.

## What it is not

**It is not a replacement for growth, and it never blocks it.** The sweep
runs exactly as it did; a seeded node is refreshed by a real sighting like
any other, and a phrase nobody seeded is minted on its own merits. The seed
is a floor under the vocabulary, not a ceiling on it - which is the same
shape as `startup.py` being a prior under the ranking rather than a second
ranking beside it.

**It is not an inventory.** A category is a word the ranker reasons in; it is
never a tile. Seeding a subject does not put an episode on a page, and a
deployment with a rich vocabulary and an empty bank still has an empty bank.
What it changes is how sharply the tiles that *are* there can be told apart -
see `topics._affinity`, which is the thing that reads this.

**Nothing here claims anything about the world.** Every node is the name of a
subject, never a fact about one - the same rule a browse tile written before
retrieval keeps (PROBLEMS.md §88, §102). "federal reserve" is a subject;
"the federal reserve cut rates" would be a claim, and there is a test that
bans the shape.

## How the subjects were chosen

Three rules, and they are worth stating because the obvious way to write this
file is the wrong one:

1. **Real subjects, not the bank's table of contents.** It would be easy to
   write twenty-eight nodes that each match one evergreen topic exactly, and
   that vocabulary would be worthless the moment somebody searched for
   something else. These are subjects people ask about; that they happen to
   cover most of the bank is a consequence and a check, not the design. The
   test that counts bank coverage asserts a **floor**, never a total.
2. **Broad at the top, specific at the bottom.** The levels are what the
   model has no way to invent and what containment has nothing to deepen
   against, so the middle of each branch is the valuable part.
3. **Nothing the hand-written vocabulary already owns.** `categories.mint`
   refuses a phrase that collides with a facet or a subtag slug, so a seed
   entry that collided would simply be dropped on the floor and the tree
   would be quietly smaller than this file says. A test asserts every entry
   here actually mints.

## Shape

`SEED` is nested dicts: a key is a phrase, its value is that phrase's
children. The eight top-level keys are `topics.TAG_LABELS` - the facets -
which are **roots and not rows**: they are not minted, they are only the
`parent_id` a depth-1 node carries, exactly as `categories.py` describes.
Insertion order is the mint order, so a parent always exists before its
children and `depth` is computed rather than declared.
"""
from __future__ import annotations

#: The starter tree. Facet -> subject -> narrower subject -> narrower still.
#:
#: Every phrase is at most `categories.MAX_PHRASE_WORDS` words, has no word
#: shorter than `categories.MIN_WORD`, contains no digits, and is already
#: normalised - all four are enforced by `categories.mint`, which returns
#: None rather than raising, so a violation here would be an entry that
#: silently never exists. `tests/test_category_seed.py` fails instead.
#: The four major North American leagues' teams, each under its league
#: (§202, at the owner's direction: "If I consistently listen to content about
#: the Cincinnati Bengals, that should be under [sports, american football,
#: bengals]"). A grown node needs `MIN_LISTENERS` different people, so without
#: these one fan's team stayed `sports` until two strangers asked about it.
#:
#: **A node matches any text holding all its words, in any order**, and
#: `stories.resolve_category` prefers the deepest node it finds - so a team
#: whose name is also an ordinary phrase would file that phrase under sport,
#: three levels deep, ahead of anything else. "Hurricanes hitting the
#: Carolina coast" is not hockey; "lightning in Tampa Bay tonight" is a
#: weather question FAM answers (§194). So:
#:
#: * a nickname alone only where it means nothing but the team ("bengals");
#: * otherwise the full name; and
#: * **no seed at all** for a team whose full name is also weather, nature,
#:   a place or the news (`LEFT_TO_GROW`). The tree still grows those from
#:   what listeners actually ask, where the words arrive as a sports question.
#:
#: A name with a word shorter than `categories.MIN_WORD` or a digit
#: ("St. Louis", "76ers") is left to grow as well.
#: `tests/test_taste_tree_202.py` checks that none of the phrases the rule
#: exists for matches a team.
def _teams(*names: str) -> dict:
    return {name: {} for name in names}


#: Teams deliberately not seeded, each because its name is also something
#: else a listener asks about. Kept as a list so the next person extending
#: the seed sees the rule rather than re-adding one.
LEFT_TO_GROW = (
    # weather and nature
    "carolina hurricanes", "tampa bay lightning", "colorado avalanche",
    "oklahoma city thunder", "miami heat", "minnesota wild",
    "calgary flames", "colorado rockies", "tampa bay rays", "dallas stars",
    "miami dolphins", "florida panthers",
    # places, institutions and the news
    "ottawa senators", "washington nationals", "washington commanders",
    "kansas city chiefs", "houston texans", "texas rangers",
    "new england patriots", "san francisco giants", "new york giants",
    "new york jets", "winnipeg jets", "los angeles chargers",
    "houston rockets", "detroit pistons", "milwaukee brewers",
    "chicago bulls", "chicago bears", "buffalo bills", "orlando magic",
)

NFL_TEAMS = _teams(
    "bengals", "steelers", "baltimore ravens", "cleveland browns",
    "indianapolis colts", "jacksonville jaguars", "tennessee titans",
    "broncos", "las vegas raiders", "dallas cowboys", "philadelphia eagles",
    "detroit lions", "green bay packers", "minnesota vikings",
    "atlanta falcons", "carolina panthers", "new orleans saints",
    "buccaneers", "arizona cardinals", "los angeles rams", "niners",
    "seahawks")
NBA_TEAMS = _teams(
    "atlanta hawks", "boston celtics", "brooklyn nets", "charlotte hornets",
    "cleveland cavaliers", "dallas mavericks", "denver nuggets",
    "golden state warriors", "indiana pacers", "los angeles clippers",
    "los angeles lakers", "memphis grizzlies", "milwaukee bucks",
    "minnesota timberwolves", "new orleans pelicans", "new york knicks",
    "philadelphia sixers", "phoenix suns", "portland trail blazers",
    "sacramento kings", "san antonio spurs", "toronto raptors", "utah jazz",
    "washington wizards")
MLB_TEAMS = _teams(
    "arizona diamondbacks", "atlanta braves", "baltimore orioles",
    "boston red sox", "chicago cubs", "chicago white sox", "cincinnati reds",
    "cleveland guardians", "detroit tigers", "houston astros",
    "kansas city royals", "los angeles angels", "los angeles dodgers",
    "miami marlins", "minnesota twins", "new york mets", "new york yankees",
    "oakland athletics", "philadelphia phillies", "pittsburgh pirates",
    "san diego padres", "seattle mariners", "toronto blue jays")
NHL_TEAMS = _teams(
    "anaheim ducks", "boston bruins", "buffalo sabres", "chicago blackhawks",
    "columbus blue jackets", "detroit red wings", "edmonton oilers",
    "los angeles kings", "montreal canadiens", "nashville predators",
    "new jersey devils", "new york islanders", "new york rangers",
    "philadelphia flyers", "pittsburgh penguins", "san jose sharks",
    "seattle kraken", "toronto maple leafs", "vancouver canucks",
    "vegas golden knights", "washington capitals")


SEED: dict[str, dict] = {
    "sports": {
        # §187: the leagues, so a question about one files it as a subject
        # (depth two) rather than as the whole of a sport. §202: and their
        # teams, a level below.
        "american football": {"college football": {}, "super bowl": {},
                              "quarterback play": {}, "nfl": NFL_TEAMS},
        "basketball": {"playoff basketball": {}, "draft prospects": {},
                       "nba": NBA_TEAMS, "wnba": {}, "euroleague": {}},
        "football": {"transfer window": {}, "champions league": {},
                     "world cup": {}, "premier league": {},
                     "bundesliga": {}, "major league soccer": {}},
        "baseball": {"mlb": MLB_TEAMS, "world series": {}},
        "ice hockey": {"nhl": NHL_TEAMS, "stanley cup": {}},
        "golf": {"major championship": {}},
        "tennis": {},
        "motorsport": {"formula one": {}, "nascar": {}},
        "combat sports": {"boxing": {}, "mixed martial arts": {}, "ufc": {}},
        "olympic sport": {},
        "athletic training": {"injury recovery": {}},
        "team ownership": {"stadium funding": {}, "sports stadiums": {},
                           "player contracts": {}, "sports franchise": {}},
    },
    "tech": {
        "artificial intelligence": {"language models": {}, "machine learning": {},
                                    "autonomous vehicles": {},
                                    "tech regulation": {}},
        "semiconductors": {"semiconductor manufacturing": {},
                           "chip manufacturing": {},
                           "chip export controls": {}},
        "consumer hardware": {"smartphones": {}, "wearables": {}},
        "software business": {"cloud computing": {}, "cybersecurity": {},
                              "open source": {}},
        "social platforms": {"content moderation": {}, "creator economy": {}},
        "crypto": {"digital currency": {}},
        "space technology": {"reusable rockets": {}, "satellite internet": {}},
        "quantum computing": {},
        "robotics": {},
    },
    "money": {
        "central banks": {"federal reserve": {}, "interest rates": {},
                          "inflation": {}},
        "markets": {"stock market": {}, "bond market": {},
                    "market volatility": {}},
        "housing market": {"house prices": {}, "mortgage rates": {},
                           "rental market": {}},
        "commodity prices": {"oil price": {}, "gold price": {},
                             "food prices": {}},
        "personal finance": {"retirement saving": {}, "household debt": {}},
        "taxation": {},
        "currency markets": {},
    },
    "business": {
        "startups": {"venture capital": {}, "founder stories": {}},
        "corporate strategy": {"mergers acquisitions": {},
                               "pricing strategy": {}, "corporate layoffs": {}},
        "retail business": {"ecommerce": {}, "luxury goods": {}},
        "energy business": {"oil companies": {}, "renewable energy": {},
                            "electricity grids": {}},
        "airlines": {"air travel": {}, "flights": {}},
        "automotive industry": {"electric vehicles": {}},
        "advertising": {},
        "labour market": {"remote work": {}},
        "streaming business": {},
    },
    "culture": {
        "film": {"hollywood": {}, "box office": {}, "awards season": {},
                 "film franchises": {}},
        "television": {"streaming shows": {}, "reality television": {}},
        "music industry": {"pop music": {}, "live touring": {}, "hip hop": {}},
        "celebrity": {"celebrity comeback": {}},
        "books": {"publishing": {}},
        "food culture": {"restaurants": {}, "fine dining": {}},
        "fashion": {},
        "video games": {},
        "internet trends": {"short video": {}},
        "art world": {},
    },
    "health": {
        "nutrition": {"weight loss": {}, "protein intake": {},
                      "ultra processed food": {}},
        "mental health": {"anxiety": {}, "burnout": {},
                          "focus and attention": {}},
        "sleep quality": {"sleep research": {}},
        "exercise science": {"strength training": {}, "endurance training": {}},
        "medicine": {"vaccines": {}, "cancer research": {}, "public health": {}},
        "ageing": {"longevity research": {}},
        "habit formation": {},
        "womens health": {},
    },
    "science": {
        "space science": {"mars missions": {}, "telescopes": {},
                          "black holes": {}},
        "climate science": {"extreme weather": {}, "carbon removal": {}},
        "biology": {"genetics": {}, "neuroscience": {}, "microbiome": {}},
        "physics": {"particle physics": {}, "fusion power": {}},
        "earth science": {"earthquakes": {}, "ocean science": {}},
        "materials science": {},
        "archaeology": {},
    },
    "world": {
        # §187: the Middle East's own subjects, so a question about one of
        # them is a world subject - and so never a sports one.
        "geopolitical conflict": {
            "middle east": {"israel": {"tel aviv": {}, "jerusalem": {}},
                            "gaza": {}, "iran": {}, "lebanon": {}},
            "ukraine": {}, "taiwan": {}},
        "elections and politics": {"presidential election": {}, "polling": {},
                                   "political parties": {}},
        "trade policy": {"tariffs": {}, "sanctions": {}},
        "immigration": {},
        "energy security": {"oil supply": {}},
        "urban policy": {"urban housing": {}, "public transport": {}},
        "food supply": {},
        "international law": {},
        "global development": {},
    },
}


def rows() -> list[tuple[str, str]]:
    """Every seed node as `(phrase, parent_id)`, parents before children.

    Flattened here rather than at every call site, because the order is
    load-bearing: `categories.mint` computes a node's depth from its parent's,
    so a child minted first would be minted at depth 1 under a parent that
    does not exist yet and never corrected.
    """
    out: list[tuple[str, str]] = []

    def walk(subtree: dict, parent: str) -> None:
        for phrase, children in subtree.items():
            out.append((phrase, parent))
            walk(children, phrase)

    for facet, subtree in SEED.items():
        walk(subtree, facet)
    return out
