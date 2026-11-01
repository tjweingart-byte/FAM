"""Names that mean two things, and the words that tell them apart (§373).

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
("season", "record", "win" for weather records). A reading's own node name
("new york rangers") in the text decides it outright.

**No cue is no evidence** (review, §377): a name with nothing around it
that points to any reading tells Nando nothing - "tech giants", "kings and
queens of England" are neither league - so it is not filed under what the
readings share; only a draw between readings that each have a cue is.
`NOT_THE_NAME` lists phrases where the word is part of another name
("United States", "College of Cardinals") and is not the clash at all.
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
           "playoff", "playoffs", "draft", "beat", "lost to", "defeated",
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

#: Phrases in which the word is not the clash at all.
NOT_THE_NAME: dict[str, tuple[str, ...]] = {
    "united": ("united states", "united nations", "united kingdom", "united arab emirates",
               "united way", "united auto workers", "united church", "united methodist"),
    "cardinals": ("college of cardinals",),
    "giants": ("giants causeway", "giant's causeway", "tech giants", "gentle giants"),
    "kings": ("book of kings", "kings of leon", "kings and queens", "three kings",
              "kings cross", "king's cross"),
    "rangers": ("power rangers", "park rangers", "forest rangers"),
    "panthers": ("black panthers", "black panther party", "pink panther"),
    "jaguars": ("jaguar land rover",),
    "heat": ("heat pump", "heat pumps", "heat map"),
}

#: Words that make a seeded team's name an everyday phrase again: the team
#: reading must then win on its cues like any clash (§378).
_MUSIC = ("festival", "festivals", "music", "album", "band", "concert", "concerts",
          "brunch", "club", "musician", "musicians", "records", "saxophone", "playlist")

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
        Reading("", ("arrest", "arrested", "suspect", "investigation", "law enforcement",
                     "trail", "park", "wildfire")),
    ),
    "kings": (
        Reading("los angeles kings", _HOCKEY + ("los angeles", "la kings")),
        Reading("sacramento kings", _BASKETBALL + ("sacramento",)),
        Reading("", ("queen", "queens", "monarch", "monarchy", "royal", "throne", "crown",
                     "dynasty", "bible", "band", "album")),
    ),
    "panthers": (
        Reading("carolina panthers", _FOOTBALL + ("carolina",)),
        Reading("nhl", _HOCKEY + ("florida",)),
        Reading("", ("endangered", "wildlife", "species", "habitat", "big cat", "zoo")),
    ),
    "cardinals": (
        Reading("arizona cardinals", _FOOTBALL + ("arizona",)),
        Reading("mlb", _BASEBALL + ("st. louis", "st louis")),
        Reading("", ("pope", "conclave", "vatican", "church", "catholic", "bird", "birds",
                     "feeder")),
    ),
    "giants": (
        Reading("nfl", _FOOTBALL + ("new york giants",)),
        Reading("mlb", _BASEBALL + ("san francisco",)),
        Reading("", ("jazz", "industry", "earnings", "companies", "corporate")),
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
                             "pilots", "fares", "united airlines", "boeing")),
        Reading("premier league", _SOCCER + ("manchester", "manchester united",
                                             "old trafford", "man utd", "man united")),
    ),
    "apple": (
        Reading("smartphones", ("iphone", "iphones", "ios", "app store")),
        Reading("consumer hardware", ("mac", "macbook", "ipad", "vision pro", "airpods",
                                      "apple watch", "device", "devices")),
        Reading("stock market", ("shares", "stock", "stocks", "market cap", "earnings",
                                 "investors")),
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
    "jazz": (
        Reading("utah jazz", _BASKETBALL),
        Reading("", _MUSIC),
    ),
    "jaguars": (
        Reading("jacksonville jaguars", _FOOTBALL + ("jacksonville",)),
        Reading("automotive industry", ("car", "cars", "jaguar land rover", "electric",
                                        "carmaker", "models")),
    ),
}
