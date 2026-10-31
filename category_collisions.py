"""Names that mean two things, and the words that tell them apart (§358).

Data beside the category seed (`category_seed.py`), read by Nando's code
(`agents.categorizer`), reviewed like any change to the seed, and never a
rule book. The owner's examples (docs/agent_rules/c3p0.md and nando.md): the
Eagles a bird or a team, United an airline or a football club, hurricanes a
storm or a hockey team. Each name lists its readings: the tree node each
reading is filed under - "" for a reading the tree has no node for (the
bird), which is never filed - and the words around the name that point to
it. A name with no deciding words around it is a **tie**, and a tie is
never guessed (c3p0.md, "always" 2): Nando files under what the readings
share, or nothing, and C3P0 asks the listener when one can be asked.

Cues are whole words or phrases, lower case, matched against the question
and the brief's own words. Keep each list to words that are about the
reading itself ("storm", "puck"), never to words both readings use
("season", "record", "win" for weather records).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Reading:
    #: The node this reading is filed under, or "" when the tree has none.
    node: str
    cues: tuple


_STORM = ("storm", "storms", "landfall", "coast", "coastal", "evacuation", "evacuate",
          "forecast", "wind", "winds", "rain", "flooding", "flood", "weather", "category 1",
          "category 2", "category 3", "category 4", "category 5", "tropical", "path")
_SPORTS = ("game", "games", "score", "scores", "trade", "trades", "roster", "coach",
           "playoff", "playoffs", "draft", "lineup", "beat", "lost to", "defeated",
           "standings", "fans", "stadium", "arena", "signing", "injury", "injured")
_HOCKEY = _SPORTS + ("puck", "goalie", "goaltender", "nhl", "hockey", "power play",
                     "stanley cup", "rink")
_BASKETBALL = _SPORTS + ("nba", "basketball", "point guard", "rebounds", "dunk", "three-pointer")
_FOOTBALL = _SPORTS + ("nfl", "quarterback", "touchdown", "super bowl", "kickoff",
                       "linebacker", "receiver")
_BASEBALL = _SPORTS + ("mlb", "baseball", "pitcher", "home run", "inning", "innings",
                       "world series", "manager", "bullpen")
_SOCCER = _SPORTS + ("striker", "midfielder", "transfer", "premier league", "match",
                     "goal", "goals", "manager", "pitch", "champions league", "keeper")

COLLISIONS: dict[str, tuple[Reading, ...]] = {
    "heat": (
        Reading("extreme weather", ("wave", "heatwave", "temperature", "temperatures",
                                    "degrees", "hot", "humid", "humidity", "scorching",
                                    "advisory", "warning", "record high", "heatstroke")),
        Reading("nba", _BASKETBALL),
    ),
    "hurricane": (
        Reading("extreme weather", _STORM),
        Reading("nhl", _HOCKEY),
    ),
    "hurricanes": (
        Reading("extreme weather", _STORM),
        Reading("nhl", _HOCKEY),
    ),
    "thunder": (
        Reading("extreme weather", ("storm", "storms", "lightning", "thunderstorm", "rain",
                                    "weather", "forecast")),
        Reading("nba", _BASKETBALL),
    ),
    "lightning": (
        Reading("extreme weather", ("storm", "storms", "strike", "struck", "thunder",
                                    "weather", "forecast")),
        Reading("nhl", _HOCKEY),
    ),
    "rangers": (
        Reading("new york rangers", _HOCKEY),
        Reading("mlb", _BASEBALL + ("texas",)),
    ),
    "kings": (
        Reading("los angeles kings", _HOCKEY + ("los angeles", "la kings")),
        Reading("sacramento kings", _BASKETBALL + ("sacramento",)),
    ),
    "panthers": (
        Reading("carolina panthers", _FOOTBALL + ("carolina",)),
        Reading("nhl", _HOCKEY + ("florida",)),
    ),
    "cardinals": (
        Reading("arizona cardinals", _FOOTBALL + ("arizona",)),
        Reading("mlb", _BASEBALL + ("st. louis", "st louis")),
    ),
    "giants": (
        Reading("nfl", _FOOTBALL + ("new york giants",)),
        Reading("mlb", _BASEBALL + ("san francisco",)),
    ),
    "jets": (
        Reading("nfl", _FOOTBALL + ("new york jets",)),
        Reading("air travel", ("private jet", "private jets", "jet fuel", "flight",
                               "flights", "aviation", "airport")),
        Reading("geopolitical conflict", ("fighter", "fighter jets", "air force",
                                          "airstrike", "airstrikes", "military")),
    ),
    "eagles": (
        Reading("philadelphia eagles", _FOOTBALL + ("philadelphia", "philly")),
        Reading("", ("bird", "birds", "nest", "migration", "wildlife", "bald eagle",
                     "species", "wingspan")),
    ),
    "united": (
        Reading("airlines", ("airline", "airlines", "flight", "flights", "route", "routes",
                             "airport", "pilots", "fares", "united airlines", "boeing")),
        Reading("premier league", _SOCCER + ("manchester", "old trafford", "man utd",
                                             "man united")),
    ),
    "apple": (
        Reading("smartphones", ("iphone", "iphones", "ios", "app store")),
        Reading("consumer hardware", ("mac", "macbook", "ipad", "vision pro", "airpods",
                                      "watch", "device", "devices")),
        Reading("stock market", ("shares", "stock", "market cap", "earnings", "investors")),
        Reading("", ("orchard", "orchards", "fruit", "harvest", "cider", "pie")),
    ),
    "amazon": (
        Reading("ecommerce", ("prime", "delivery", "deliveries", "shopping", "seller",
                              "sellers", "online retail")),
        Reading("retail business", ("warehouse", "warehouses", "workers", "strike",
                                    "union")),
        Reading("cloud computing", ("aws", "web services", "cloud", "data center",
                                    "data centers")),
        Reading("climate science", ("rainforest", "deforestation", "river", "basin",
                                    "brazil", "indigenous", "drought")),
    ),
    "mercury": (
        Reading("space science", ("planet", "orbit", "sun", "probe", "spacecraft",
                                  "solar system", "nasa")),
        Reading("public health", ("poisoning", "contamination", "fish", "exposure",
                                  "levels", "toxic")),
    ),
    "bulls": (
        Reading("nba", _BASKETBALL + ("chicago",)),
        Reading("stock market", ("market", "stocks", "investors", "rally", "bear")),
    ),
    "bears": (
        Reading("nfl", _FOOTBALL + ("chicago",)),
        Reading("stock market", ("market", "stocks", "investors", "selloff", "bull")),
        Reading("", ("grizzly", "wildlife", "hibernation", "attack", "cubs")),
    ),
    "jaguars": (
        Reading("jacksonville jaguars", _FOOTBALL + ("jacksonville",)),
        Reading("automotive industry", ("car", "cars", "jaguar land rover", "electric",
                                        "carmaker", "models")),
    ),
}
