"""Write `static/places.json`: every country, and each one's states.

The waitlist's profile (10.8 packet) picks its country from a list that can
be typed into, and its state from that country's own list. The list is
ISO 3166 by way of `pycountry`, top-level subdivisions only - a state, a
province, a nation of the UK - because a county or a department is a city's
business, and the city stays typed.

`pycountry` is not a dependency of the app: the file is written once and
committed, and this is how it is written again.

    pip install pycountry && python tools/build_places.py
"""
from __future__ import annotations

import json
import pathlib
import re

import pycountry

OUT = pathlib.Path(__file__).resolve().parent.parent / "static" / "places.json"


#: The name a person types, where ISO's reads as a catalogue entry.
RENAMED = {
    "CD": "Democratic Republic of the Congo", "FK": "Falkland Islands",
    "FM": "Micronesia", "PS": "Palestine", "RU": "Russia",
    "VA": "Vatican City", "VG": "British Virgin Islands",
    "VI": "U.S. Virgin Islands",
}


def _name(entry) -> str:
    # "South Korea", not "Korea, Republic of".
    if getattr(entry, "alpha_2", "") in RENAMED:
        return RENAMED[entry.alpha_2]
    name = getattr(entry, "common_name", None) or entry.name
    # "Wales [Cymru GB-CYM]": the bracket is a code, not part of the name.
    return re.sub(r"\s*\[[^\]]*\]$", "", name)


def build() -> dict:
    countries = sorted(((_name(c), c.alpha_2) for c in pycountry.countries),
                       key=lambda c: c[0])
    regions = {}
    for _, code in countries:
        top = [_name(s) for s in pycountry.subdivisions.get(country_code=code) or []
               if not s.parent_code]
        if top:
            regions[code] = sorted(top)
    return {"countries": [list(c) for c in countries], "regions": regions}


if __name__ == "__main__":
    OUT.write_text(json.dumps(build(), ensure_ascii=False, separators=(",", ":")) + "\n",
                   encoding="utf-8")
    print(f"wrote {OUT}")
