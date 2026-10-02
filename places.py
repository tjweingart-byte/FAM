"""Which town a question is about, where it is, and which county it is in (§193).

A local question - "what's going on in San Anselmo" - needs three things this
codebase never had: the town's county (so the county rung knows what to ask
for), its coordinates (so the weather knows where to look) and its country
(so the US goes to the National Weather Service). `geography.py` knows
countries and regions and nothing smaller.

**Where the answer comes from, in order, and why:**

1. **This store.** A resolved place is kept for good: a town does not move,
   so paying for it twice is waste.
2. **The outlet registry** (`local_news`), which records the town and county
   each outlet covers. Enough for news - the county rung needs a county name,
   not coordinates - and it costs nothing.
3. **Open-Meteo's place-name lookup** (GeoNames underneath), for coordinates
   and anything the registry does not hold. Its free endpoint is for
   non-commercial use only, so it is called with `OPEN_METEO_API_KEY`, or
   keyless only where `OPEN_METEO_KEYLESS=1` says so explicitly.

**Never from a model and never from the listener's profile.** EI names the
place as typed; the coordinates come from a catalogue, for the reason
`live_facts.Entity` gives - an invented identifier does not fail, it returns
somebody else's weather. And a listener's saved location never reaches an
episode (`preferences.Preferences.location`): the place is the one the
question names, so the episode is shared with everybody who asks it.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import asdict, dataclass
from typing import Optional

import httpx

import credentials
from config import settings
from paths import data_path

log = logging.getLogger("fam.places")

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
GEOCODE_URL_PAID = "https://customer-geocoding-api.open-meteo.com/v1/search"

#: Feature codes GeoNames gives a populated place or an administrative area.
#: A river or a mountain with a town's name is not the town.
_PLACE_CODES = re.compile(r"^(PPL|ADM)")

_US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida",
    "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
    "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}


@dataclass(frozen=True)
class Place:
    """One town, as a catalogue names it."""

    name: str
    #: The county or second-level area ("Marin County"). Empty when unknown,
    #: and then there is no county rung - never a guessed one.
    county: str = ""
    #: State, province or region ("California").
    region: str = ""
    #: ISO 3166 alpha-2, upper case ("US").
    country: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    #: Where this came from: "registry", "open-meteo" or "store".
    source: str = ""

    @property
    def located(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    @property
    def in_us(self) -> bool:
        return self.country == "US"

    @property
    def label(self) -> str:
        """How the episode names it: "San Anselmo, California"."""
        return ", ".join(p for p in (self.name, self.region) if p)

    @property
    def county_label(self) -> str:
        """The county as said aloud: "Marin County"."""
        county = self.county.strip()
        if not county:
            return ""
        if self.in_us and not re.search(
                r"\b(county|parish|borough|census area|municipality)\b",
                county, re.I):
            return f"{county} County"
        return county

    def as_dict(self) -> dict:
        return asdict(self)


def normalise(text: str) -> str:
    """The key a place is stored under: lower case, single spaces, no dots."""
    text = re.sub(r"[^\w\s,'-]", " ", (text or "").lower())
    return " ".join(text.replace(",", " , ").split()).replace(" ,", ",")


def split_name(text: str) -> tuple[str, str, str]:
    """"San Anselmo, CA" -> ("San Anselmo", "California", "").

    The model is asked for "Town, Region, Country"; people type anything.
    Only the town part is required.
    """
    parts = [p.strip() for p in (text or "").split(",") if p.strip()]
    if not parts:
        return "", "", ""
    town = parts[0]
    region = parts[1] if len(parts) > 1 else ""
    country = parts[2] if len(parts) > 2 else ""
    if region.upper() in _US_STATES:
        region = _US_STATES[region.upper()]
        country = country or "US"
    return town, region, country


# --------------------------------------------------------------------------
# The store
# --------------------------------------------------------------------------
class PlaceStore:
    """Resolved places, kept for good. Lives in the local news database."""

    def __init__(self, path: str | None = None) -> None:
        self.path = data_path("LOCAL_NEWS_DB", "local_news.db", path)
        self._lock = threading.Lock()
        with closing(self._connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS places ("
                " key TEXT PRIMARY KEY, name TEXT, county TEXT, region TEXT,"
                " country TEXT, latitude REAL, longitude REAL, source TEXT,"
                " resolved_at REAL)")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def get(self, key: str) -> Optional[Place]:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT name, county, region, country, latitude, longitude"
                " FROM places WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        return Place(row[0], row[1] or "", row[2] or "", row[3] or "",
                     row[4], row[5], "store")

    def put(self, key: str, place: Place) -> None:
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO places VALUES (?,?,?,?,?,?,?,?,?)",
                (key, place.name, place.county, place.region, place.country,
                 place.latitude, place.longitude, place.source, time.time()))


_STORE: list = [None]


def store() -> PlaceStore:
    if _STORE[0] is None:
        _STORE[0] = PlaceStore()
    return _STORE[0]


def reset_store() -> None:
    """Forget the open store, so a test's `LOCAL_NEWS_DB` is the one used."""
    _STORE[0] = None


# --------------------------------------------------------------------------
# Open-Meteo's place-name lookup
# --------------------------------------------------------------------------
def geocoder_available() -> tuple[bool, str]:
    """Whether the place-name lookup may be called, and why not if not."""
    if credentials.active("OPEN_METEO_API_KEY"):
        return True, "Open-Meteo place-name lookup with OPEN_METEO_API_KEY"
    if settings.open_meteo_keyless:
        return True, ("Open-Meteo's free endpoint (OPEN_METEO_KEYLESS=1: "
                      "non-commercial use only)")
    return False, ("no OPEN_METEO_API_KEY, and OPEN_METEO_KEYLESS is off - "
                   "the free endpoint is non-commercial, so it is not used")


def _pick(results: list, region: str, country: str) -> Optional[dict]:
    """The best populated place among the catalogue's answers."""
    places = [r for r in results
              if _PLACE_CODES.match(str(r.get("feature_code", "")))]
    if country:
        wanted = country.upper()
        named = {"united states": "US", "usa": "US", "us": "US",
                 "united kingdom": "GB", "uk": "GB"}.get(country.lower(), wanted)
        places = [r for r in places
                  if str(r.get("country_code", "")).upper() == named] or places
    if region:
        matching = [r for r in places
                    if str(r.get("admin1", "")).lower() == region.lower()]
        if matching:
            places = matching
    if not places:
        return None
    # The catalogue already orders by relevance; population breaks the tie
    # between two towns of one name in the same region.
    return max(places[:5], key=lambda r: int(r.get("population") or 0))


async def geocode(town: str, region: str = "", country: str = "",
                  timeout: float = 4.0) -> Optional[Place]:
    """Ask Open-Meteo where `town` is. `None` when it does not know or failed."""
    ok, why = geocoder_available()
    if not ok:
        log.info("places: cannot look up %r - %s", town, why)
        return None
    import provider_usage

    key = credentials.active("OPEN_METEO_API_KEY")
    params = {"name": town, "count": 10, "language": "en", "format": "json"}
    url = GEOCODE_URL
    if key:
        params["apikey"] = key
        url = GEOCODE_URL_PAID
    try:
        async with httpx.AsyncClient(
                timeout=timeout,
                headers={"User-Agent": user_agent()}) as client:
            reply = await client.get(url, params=params)
        provider_usage.record("open_meteo", ok=reply.is_success)
        reply.raise_for_status()
        results = list((reply.json() or {}).get("results") or [])
    except Exception as exc:  # noqa: BLE001 - a lookup that fails is a miss
        provider_usage.record("open_meteo", ok=False)
        log.warning("places: Open-Meteo lookup failed for %r: %s", town, exc)
        return None
    best = _pick(results, region, country)
    if best is None:
        return None
    return Place(
        name=str(best.get("name") or town),
        county=str(best.get("admin2") or ""),
        region=str(best.get("admin1") or region),
        country=str(best.get("country_code") or "").upper(),
        latitude=float(best["latitude"]) if "latitude" in best else None,
        longitude=float(best["longitude"]) if "longitude" in best else None,
        source="open-meteo")


def user_agent() -> str:
    """What every request this feature sends says about who is asking."""
    contact = settings.fetch_contact or "unknown"
    return f"FAM local news and weather ({contact})"


async def resolve(text: str) -> Optional[Place]:
    """The place `text` names, or `None`. Never raises, never guesses.

    A place the registry knows is answered without the network, and kept;
    coordinates are added from the catalogue when it can be asked, because
    the weather needs them and the news does not.
    """
    town, region, country = split_name(text)
    if not town:
        return None
    key = normalise(", ".join(p for p in (town, region, country) if p))
    try:
        held = store().get(key)
    except sqlite3.Error:
        log.warning("places: the store could not be read", exc_info=True)
        held = None
    if held is not None and held.located:
        return held

    import local_news

    known = local_news.registry_place(town, region)
    found = await geocode(town, region, country)
    place = _merge(known, found, held)
    if place is None:
        return None
    try:
        store().put(key, place)
    except sqlite3.Error:
        log.warning("places: could not keep %r", key, exc_info=True)
    return place


def _merge(*candidates: Optional[Place]) -> Optional[Place]:
    """One place from what each source knew, first non-empty value winning.

    The registry goes first for the county, because it is what the outlets
    themselves were filed under, and the county rung matches on it.
    """
    present = [c for c in candidates if c is not None]
    if not present:
        return None

    def first(attr):
        for c in present:
            value = getattr(c, attr)
            if value not in (None, ""):
                return value
        return None

    return Place(
        name=first("name") or "", county=first("county") or "",
        region=first("region") or "", country=first("country") or "",
        latitude=first("latitude"), longitude=first("longitude"),
        source="+".join(dict.fromkeys(c.source for c in present if c.source)))
