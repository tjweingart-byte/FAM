"""Where a story is trending, in words a listener reads.

The Trending rail is organised by geography (§135, at the owner's
direction): stories trending across the world, and stories trending in the
listener's own part of it. This module is the one place that knows which
country is in which region and how a spread of coverage becomes a label.

**What "where" means here, and it is a deliberate choice.** A story's
geography is where it is *being covered* - the countries of the outlets
writing about it - not where it happened. "Trending in Europe" means
European press is running it, which is exactly the thing a regional trending
row is for: a US election covered wall to wall in London is trending in
Europe, and a flood in Kerala covered by nobody outside India is trending in
India and nowhere else. Where a thing happened would need a model to read
every headline; where it is being covered is a count GDELT already gives us.

Three scopes, from the widest:

* **world** - the coverage spans several regions, and no one of them owns it.
* **region** - one region owns it (North America, Europe, ...).
* **country** - one country owns it, inside its region.

Everything here is pure and keyless: dictionaries and arithmetic, no network,
no model. Countries arrive already folded to one spelling by
`stories.normalise_country`.
"""
from __future__ import annotations

from typing import Iterable

WORLD = "world"

#: Region keys, in the order the "View more" screen lists them after the
#: listener's own. Keys are stable identifiers; labels are what is drawn.
REGIONS = (
    "north-america", "latin-america", "europe", "middle-east", "africa",
    "south-asia", "east-asia", "southeast-asia", "oceania",
)

REGION_LABELS = {
    WORLD: "Worldwide",
    "north-america": "North America",
    "latin-america": "Latin America",
    "europe": "Europe",
    "middle-east": "Middle East",
    "africa": "Africa",
    "south-asia": "South Asia",
    "east-asia": "East Asia",
    "southeast-asia": "Southeast Asia",
    "oceania": "Oceania",
}

#: Country -> region, in the spelling `stories.normalise_country` produces.
#: Complete for every country that spelling table knows, because a country
#: missing here silently counts as nowhere - the §134 finding about the ISO
#: table, which this would otherwise repeat one layer up.
_MEMBERS = {
    "north-america": (
        "united states", "canada", "mexico",
    ),
    "latin-america": (
        "argentina", "bolivia", "brazil", "chile", "colombia", "costa rica",
        "cuba", "dominican republic", "ecuador", "el salvador", "guatemala",
        "haiti", "honduras", "jamaica", "nicaragua", "panama", "paraguay",
        "peru", "puerto rico", "uruguay", "venezuela", "bahamas", "barbados",
        "belize", "dominica", "grenada", "guyana", "saint kitts and nevis",
        "saint lucia", "saint vincent and the grenadines", "suriname",
        "trinidad and tobago", "antigua and barbuda",
    ),
    "europe": (
        "united kingdom", "ireland", "france", "germany", "spain", "portugal",
        "italy", "netherlands", "belgium", "luxembourg", "switzerland",
        "austria", "denmark", "norway", "sweden", "finland", "iceland",
        "poland", "czech republic", "slovakia", "hungary", "romania",
        "bulgaria", "greece", "croatia", "slovenia", "serbia",
        "bosnia and herzegovina", "montenegro", "macedonia", "albania",
        "estonia", "latvia", "lithuania", "ukraine", "belarus", "moldova",
        "russia", "malta", "cyprus", "andorra", "monaco", "san marino",
        "liechtenstein", "vatican city", "georgia", "armenia", "azerbaijan",
    ),
    "middle-east": (
        "israel", "palestine", "lebanon", "syria", "jordan", "iraq", "iran",
        "saudi arabia", "united arab emirates", "qatar", "kuwait", "bahrain",
        "oman", "yemen", "turkey", "egypt",
    ),
    "africa": (
        "nigeria", "south africa", "kenya", "ghana", "ethiopia", "uganda",
        "tanzania", "rwanda", "morocco", "algeria", "tunisia", "libya",
        "sudan", "south sudan", "senegal", "ivory coast", "cameroon",
        "angola", "mozambique", "zambia", "zimbabwe", "namibia", "botswana",
        "malawi", "madagascar", "mali", "niger", "burkina faso", "chad",
        "somalia", "eritrea", "djibouti", "benin", "togo", "guinea",
        "guinea-bissau", "sierra leone", "liberia", "gambia", "mauritania",
        "mauritius", "seychelles", "cape verde", "comoros", "gabon",
        "republic of the congo", "democratic republic of the congo",
        "central african republic", "equatorial guinea", "burundi",
        "lesotho", "eswatini", "sao tome and principe",
    ),
    "south-asia": (
        "india", "pakistan", "bangladesh", "sri lanka", "nepal", "bhutan",
        "maldives", "afghanistan",
    ),
    "east-asia": (
        "china", "japan", "south korea", "north korea", "taiwan",
        "hong kong", "macau", "mongolia",
    ),
    "southeast-asia": (
        "indonesia", "philippines", "vietnam", "thailand", "malaysia",
        "singapore", "burma", "cambodia", "laos", "brunei", "east timor",
    ),
    "oceania": (
        "australia", "new zealand", "fiji", "papua new guinea", "samoa",
        "tonga", "vanuatu", "solomon islands", "kiribati", "micronesia",
        "marshall islands", "nauru", "palau", "tuvalu",
    ),
}

REGION_OF = {country: region for region, members in _MEMBERS.items()
             for country in members}
# Central Asia has no row of its own - too little coverage to fill one - and
# is filed with the region its press most often sits beside.
REGION_OF.update({"kazakhstan": "europe", "kyrgyzstan": "east-asia",
                  "tajikistan": "south-asia", "turkmenistan": "middle-east",
                  "uzbekistan": "south-asia"})

#: How to ask GDELT for one region's press: the countries whose outlets stand
#: for it, as GDELT's `sourcecountry:` operator spells them (the country name
#: with its spaces removed). Not every member - a query with fifty OR terms is
#: a query GDELT truncates - but the ones that carry most of the region's
#: output in the index.
GDELT_SOURCES = {
    "north-america": ("unitedstates", "canada", "mexico"),
    "latin-america": ("brazil", "argentina", "colombia", "chile", "peru",
                      "venezuela"),
    "europe": ("unitedkingdom", "france", "germany", "spain", "italy",
               "netherlands", "poland", "ireland"),
    "middle-east": ("israel", "saudiarabia", "unitedarabemirates", "turkey",
                    "egypt", "qatar", "iran"),
    "africa": ("nigeria", "southafrica", "kenya", "ghana", "ethiopia"),
    "south-asia": ("india", "pakistan", "bangladesh", "srilanka"),
    "east-asia": ("japan", "southkorea", "china", "taiwan", "hongkong"),
    "southeast-asia": ("philippines", "indonesia", "singapore", "malaysia",
                       "vietnam", "thailand"),
    "oceania": ("australia", "newzealand"),
}

#: A region owns a story when this share of its coverage is from there.
REGION_OWNS = 0.6
#: A country owns a story when this share of its coverage is from there.
COUNTRY_OWNS = 0.6
#: A story is worldwide when at least this many regions each carry this
#: share of it. Two is a bilateral story; three is the world noticing.
WORLD_REGIONS = 3
WORLD_REGION_SHARE = 0.12


def region_of(country: str) -> str:
    """The region a country is in, or "" for one this table does not know."""
    import stories

    return REGION_OF.get(stories.normalise_country(country), "")


def label_for(key: str) -> str:
    """What a scope key reads as on screen."""
    if key in REGION_LABELS:
        return REGION_LABELS[key]
    return " ".join(word.capitalize() for word in (key or "").split())


def region_shares(countries: Iterable) -> dict:
    """`(country, share)` pairs folded up to regions. Unknown countries drop."""
    out: dict = {}
    for name, share in countries or ():
        region = REGION_OF.get(name, "")
        if region:
            out[region] = out.get(region, 0.0) + float(share)
    return out


def scope_for(countries: Iterable, fallback_region: str = "") -> tuple:
    """Where a story is trending: `(scope, key, label)`.

    `scope` is `world`, `region` or `country`; `key` is the region key or the
    country name; `label` is what the card says. A story with no country data
    at all is `world` unless the source that found it knows better
    (`fallback_region` - a regional sweep that found it in one region's press,
    or a league that plays in one country).
    """
    countries = tuple(countries or ())
    regions = region_shares(countries)
    if not regions:
        if fallback_region and fallback_region in REGION_LABELS:
            if fallback_region == WORLD:
                return WORLD, WORLD, REGION_LABELS[WORLD]
            return "region", fallback_region, REGION_LABELS[fallback_region]
        return WORLD, WORLD, REGION_LABELS[WORLD]

    spread = sum(1 for share in regions.values() if share >= WORLD_REGION_SHARE)
    top_region, top_share = max(regions.items(), key=lambda kv: (kv[1], kv[0]))
    if spread >= WORLD_REGIONS or top_share < REGION_OWNS:
        return WORLD, WORLD, REGION_LABELS[WORLD]

    top_country, country_share = max(
        ((name, float(share)) for name, share in countries
         if REGION_OF.get(name) == top_region),
        key=lambda kv: (kv[1], kv[0]), default=("", 0.0))
    if top_country and country_share >= COUNTRY_OWNS:
        return "country", top_country, label_for(top_country)
    return "region", top_region, REGION_LABELS[top_region]


def matches(scope: str, key: str, listener_country: str) -> bool:
    """Whether a story's geography is the listener's own part of the world."""
    import stories

    country = stories.normalise_country(listener_country)
    if not country or scope == WORLD:
        return False
    if scope == "country":
        return key == country
    return key == REGION_OF.get(country, "")
