"""Weather for the place a question names (§193, at the owner's direction).

**Two providers, in a fixed order.** The US National Weather Service first
for a US place: it is free, keyless, it is the forecaster's own forecast, and
its official warnings come with it - a warning copied by a commercial API is a
copy, and this is the authority. Open-Meteo for everywhere else, and for a US
place whenever NWS fails (it has outages); that switch is logged and named in
what the writer is told, never silent. Open-Meteo is called with
`OPEN_METEO_API_KEY` (the $29 plan), or keyless only under
`OPEN_METEO_KEYLESS=1`, because its free endpoint is non-commercial.

**A forecast is not an outcome.** `live_facts` already tells the writer that a
prediction market is a forecast; a weather forecast is the same kind of claim
from a better source. The facts are worded as what the forecast *says* -
"the forecast calls for rain" - and the prompt block (`live_facts`, kind
`WEATHER`) forbids turning one into a certainty. A warning is stated as an
official warning with its expiry on the listener's clock.

**Cached by place and time window, never by listener**
(`WEATHER_CACHE_SECONDS`). Everybody asking about San Anselmo inside the same
half hour shares one set of calls, so calls scale with places, not people -
which is what keeps Open-Meteo on its $29 plan into the millions of questions.

**Freshness is enforced here, not requested.** An observation older than
`OBSERVATION_MAX_AGE` is dropped rather than called "now"; a forecast carries
the time it was issued (`LiveFacts.as_of`), and `live_facts` withholds one
older than its domain limit.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

import credentials
import live_facts
import places
from config import settings

log = logging.getLogger("fam.weather")

NWS_BASE = "https://api.weather.gov"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_URL_PAID = "https://customer-api.open-meteo.com/v1/forecast"

NWS = "National Weather Service"
OPEN_METEO = "Open-Meteo"

#: How old an observed temperature may be and still be said as "right now".
OBSERVATION_MAX_AGE = 2 * 3600.0
#: How many forecast periods are given (NWS periods are half days).
NWS_PERIODS = 4
#: Each provider's whole budget: NWS needs a lookup of the grid point and
#: then three calls at once; Open-Meteo is one call.
NWS_TIMEOUT = 2.5
OPEN_METEO_TIMEOUT = 2.0

#: WMO weather codes, as Open-Meteo reports them, in words.
WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle",
    55: "heavy drizzle", 56: "light freezing drizzle",
    57: "freezing drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
    66: "light freezing rain", 67: "freezing rain", 71: "light snow",
    73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers",
    81: "showers", 82: "heavy showers", 85: "light snow showers",
    86: "snow showers", 95: "thunderstorms", 96: "thunderstorms with hail",
    99: "thunderstorms with heavy hail",
}


def available() -> tuple[bool, str]:
    """Whether any weather can be served, and from what."""
    if not settings.weather:
        return False, "WEATHER=0"
    ok, why = open_meteo_available()
    if ok:
        return True, f"{NWS} for US places, then {OPEN_METEO} ({why})"
    return True, (f"{NWS} for US places only - {OPEN_METEO} is not usable: "
                  f"{why}")


def open_meteo_available() -> tuple[bool, str]:
    if credentials.active("OPEN_METEO_API_KEY"):
        return True, "OPEN_METEO_API_KEY set"
    if settings.open_meteo_keyless:
        return True, "OPEN_METEO_KEYLESS=1 (non-commercial use only)"
    return False, "no OPEN_METEO_API_KEY"


def _headers(accept: str = "application/geo+json") -> dict:
    # NWS refuses a request with no User-Agent and asks for a contact in it.
    return {"User-Agent": places.user_agent(), "Accept": accept}


def _degrees(value: float, unit: str) -> str:
    return f"{int(round(value))} degrees {unit}"


# --------------------------------------------------------------------------
# The cache: by place and time window
# --------------------------------------------------------------------------
_CACHE: dict = {}
_POINTS: dict = {}


def _cache_key(place: places.Place, now: float) -> str:
    window = max(60.0, float(settings.weather_cache_seconds))
    return (f"{place.latitude:.2f},{place.longitude:.2f}"
            f"@{int(now // window)}")


def clear_cache() -> None:
    _CACHE.clear()
    _POINTS.clear()


# --------------------------------------------------------------------------
# NWS
# --------------------------------------------------------------------------
async def _nws_json(client: httpx.AsyncClient, url: str) -> dict:
    import provider_usage

    try:
        reply = await client.get(url, headers=_headers())
    except Exception:
        provider_usage.record("nws", ok=False)
        raise
    provider_usage.record("nws", ok=reply.is_success)
    reply.raise_for_status()
    return reply.json() or {}


def _parse_time(text: str) -> Optional[datetime]:
    if not text:
        return None
    try:
        when = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def nws_facts(place: places.Place, forecast: dict, alerts: dict,
              observation: dict, now: Optional[datetime] = None
              ) -> Optional[live_facts.LiveFacts]:
    """NWS replies -> facts. Pure, so it is tested on recorded replies."""
    import listener_clock

    now = now or datetime.now(timezone.utc)
    props = (forecast or {}).get("properties") or {}
    periods = list(props.get("periods") or [])
    facts: list = []

    # Warnings first: they are the most important thing here, and they are
    # official. Stated as what they are, with when they end.
    for feature in list((alerts or {}).get("features") or [])[:3]:
        a = feature.get("properties") or {}
        event = str(a.get("event") or "").strip()
        if not event:
            continue
        ends = _parse_time(a.get("ends") or a.get("expires") or "")
        until = (f" until {listener_clock.say(ends, '%A at %H:%M %Z')}"
                 if ends else "")
        facts.append(f"The National Weather Service has issued an official "
                     f"{event}{until} for this area.")

    obs = (observation or {}).get("properties") or {}
    seen = _parse_time(obs.get("timestamp") or "")
    temp_c = (obs.get("temperature") or {}).get("value")
    if seen and temp_c is not None and \
            (now - seen).total_seconds() <= OBSERVATION_MAX_AGE:
        desc = str(obs.get("textDescription") or "").strip().lower()
        temp_f = float(temp_c) * 9 / 5 + 32
        facts.append(
            f"Observed at {listener_clock.say(seen, '%H:%M %Z')}: "
            f"{_degrees(temp_f, 'Fahrenheit')}"
            + (f" and {desc}" if desc else "") + ".")

    for period in periods[:NWS_PERIODS]:
        name = str(period.get("name") or "").strip()
        short = str(period.get("shortForecast") or "").strip().lower()
        temp = period.get("temperature")
        if not name or temp is None:
            continue
        unit = "Fahrenheit" if str(period.get("temperatureUnit", "F")) == "F" \
            else "Celsius"
        level = "high" if period.get("isDaytime") else "low"
        rain = (period.get("probabilityOfPrecipitation") or {}).get("value")
        wind = str(period.get("windSpeed") or "").strip()
        line = (f"{name}: the forecast calls for {short or 'no change'}, "
                f"{level} near {_degrees(float(temp), unit)}")
        if rain:
            line += f", {int(rain)} percent chance of rain"
        if wind:
            line += f", wind {wind}"
        facts.append(line + ".")

    if not facts:
        return None
    issued = _parse_time(props.get("updateTime") or props.get("generatedAt")
                         or "") or now
    return live_facts.LiveFacts(
        domain="weather", source=NWS, as_of=issued, facts=facts,
        kind=live_facts.WEATHER,
        url=f"https://forecast.weather.gov/MapClick.php?lat="
            f"{place.latitude:.4f}&lon={place.longitude:.4f}")


async def from_nws(place: places.Place) -> Optional[live_facts.LiveFacts]:
    """Ask NWS. Raises when it is broken; `None` when it has nothing."""
    lat, lon = f"{place.latitude:.4f}", f"{place.longitude:.4f}"
    async with httpx.AsyncClient(timeout=NWS_TIMEOUT,
                                 follow_redirects=True) as client:
        point = _POINTS.get((lat, lon))
        if point is None:
            point = (await _nws_json(client, f"{NWS_BASE}/points/{lat},{lon}")
                     ).get("properties") or {}
            _POINTS[(lat, lon)] = point
        forecast_url = point.get("forecast")
        if not forecast_url:
            return None

        async def observation() -> dict:
            stations_url = point.get("observationStations")
            if not stations_url:
                return {}
            stations = await _nws_json(client, stations_url)
            first = (list(stations.get("features") or []) or [{}])[0]
            sid = (first.get("properties") or {}).get("stationIdentifier")
            if not sid:
                return {}
            return await _nws_json(
                client, f"{NWS_BASE}/stations/{sid}/observations/latest")

        async def optional(coro) -> dict:
            # An observation or the alert list failing leaves the forecast
            # standing; the forecast failing is the provider failing.
            try:
                return await coro
            except Exception as exc:  # noqa: BLE001
                log.info("weather: an optional NWS call failed: %s", exc)
                return {}

        forecast, alerts, obs = await asyncio.gather(
            _nws_json(client, forecast_url),
            optional(_nws_json(client, f"{NWS_BASE}/alerts/active"
                                       f"?point={lat},{lon}")),
            optional(observation()))
    return nws_facts(place, forecast, alerts, obs)


# --------------------------------------------------------------------------
# Open-Meteo
# --------------------------------------------------------------------------
def open_meteo_facts(place: places.Place, data: dict,
                     now: Optional[datetime] = None
                     ) -> Optional[live_facts.LiveFacts]:
    """An Open-Meteo reply -> facts. Pure, so it is tested on a recorded one."""
    now = now or datetime.now(timezone.utc)
    us = place.in_us
    unit = "Fahrenheit" if us else "Celsius"
    speed = "miles an hour" if us else "kilometres an hour"
    facts: list = []

    current = (data or {}).get("current") or {}
    if current.get("temperature_2m") is not None:
        words = WMO.get(int(current.get("weather_code") or 0), "")
        line = f"Now: {_degrees(float(current['temperature_2m']), unit)}"
        if words:
            line += f" and {words}"
        if current.get("wind_speed_10m") is not None:
            line += f", wind {int(round(float(current['wind_speed_10m'])))} {speed}"
        facts.append(line + ".")

    daily = (data or {}).get("daily") or {}
    days = list(daily.get("time") or [])
    names = ["Today", "Tomorrow"]
    for i, day in enumerate(days[:3]):
        try:
            high = daily["temperature_2m_max"][i]
            low = daily["temperature_2m_min"][i]
        except (KeyError, IndexError, TypeError):
            continue
        if high is None or low is None:
            continue
        code = (daily.get("weather_code") or [None] * 3)[i]
        rain = (daily.get("precipitation_probability_max") or [None] * 3)[i]
        try:
            label = names[i] if i < 2 else datetime.fromisoformat(day).strftime("%A")
        except ValueError:
            label = day
        line = (f"{label}: the forecast calls for "
                f"{WMO.get(int(code), 'mixed conditions') if code is not None else 'mixed conditions'}"
                f", high {_degrees(float(high), unit)}, low "
                f"{_degrees(float(low), unit)}")
        if rain:
            line += f", {int(rain)} percent chance of rain"
        facts.append(line + ".")

    if not facts:
        return None
    return live_facts.LiveFacts(
        domain="weather", source=OPEN_METEO, as_of=now, facts=facts,
        kind=live_facts.WEATHER)


async def from_open_meteo(place: places.Place) -> Optional[live_facts.LiveFacts]:
    """Ask Open-Meteo. Raises when it is broken; `None` when it has nothing."""
    import provider_usage

    key = credentials.active("OPEN_METEO_API_KEY")
    us = place.in_us
    params = {
        "latitude": f"{place.latitude:.4f}",
        "longitude": f"{place.longitude:.4f}",
        "current": "temperature_2m,weather_code,wind_speed_10m",
        "daily": ("weather_code,temperature_2m_max,temperature_2m_min,"
                  "precipitation_probability_max"),
        "timezone": "auto", "forecast_days": 3,
        "temperature_unit": "fahrenheit" if us else "celsius",
        "wind_speed_unit": "mph" if us else "kmh",
    }
    url = OPEN_METEO_URL
    if key:
        params["apikey"] = key
        url = OPEN_METEO_URL_PAID
    try:
        async with httpx.AsyncClient(timeout=OPEN_METEO_TIMEOUT) as client:
            reply = await client.get(url, params=params,
                                     headers=_headers("application/json"))
    except Exception:
        provider_usage.record("open_meteo", ok=False)
        raise
    provider_usage.record("open_meteo", ok=reply.is_success)
    reply.raise_for_status()
    return open_meteo_facts(place, reply.json() or {})


# --------------------------------------------------------------------------
# The one entry point
# --------------------------------------------------------------------------
async def forecast_for(place: Optional[places.Place]
                       ) -> tuple[Optional[live_facts.LiveFacts], list]:
    """The weather at `place`, and what each provider did. Never raises.

    Returns `(facts or None, attempts)`, `attempts` being `(provider,
    outcome, detail)` in the order tried - the same shape `live_facts`
    reports, so a fallback is visible on the episode's own record.
    """
    attempts: list = []
    if place is None or not place.located:
        return None, [("weather", live_facts.NO_ENTITY,
                       "the place has no coordinates")]
    if not settings.weather:
        return None, [("weather", live_facts.NOT_CONFIGURED, "WEATHER=0")]

    now = time.time()
    key = _cache_key(place, now)
    held = _CACHE.get(key)
    if held is not None:
        return held, [("cache", live_facts.FACTS, key)]

    providers = []
    if place.in_us:
        providers.append((NWS, from_nws, NWS_TIMEOUT))
    ok, why = open_meteo_available()
    if ok:
        providers.append((OPEN_METEO, from_open_meteo, OPEN_METEO_TIMEOUT))
    else:
        attempts.append((OPEN_METEO, live_facts.NOT_CONFIGURED, why))

    for name, ask, budget in providers:
        try:
            facts = await asyncio.wait_for(ask(place), timeout=budget + 0.5)
        except asyncio.TimeoutError:
            attempts.append((name, live_facts.TIMEOUT, f"{name} timed out"))
            log.warning("weather: %s timed out for %s", name, place.label)
            continue
        except Exception as exc:  # noqa: BLE001 - a provider never ends an episode
            attempts.append((name, live_facts.PROVIDER_FAILED,
                             f"{type(exc).__name__}: {exc}"))
            log.warning("weather: %s failed for %s: %s", name, place.label, exc)
            continue
        if facts is None:
            attempts.append((name, live_facts.NO_FACTS, "nothing returned"))
            continue
        if attempts and any(a[0] == NWS for a in attempts):
            log.warning("weather: NWS could not serve %s; %s answered instead",
                        place.label, name)
        attempts.append((name, live_facts.FACTS, place.label))
        _CACHE[key] = facts
        return facts, attempts
    return None, attempts


class WeatherSource(live_facts.LiveSource):
    """Weather as a live-facts domain, for a question that *is* about weather.

    Resolution goes to a catalogue (`places.resolve`), never to the model:
    EI names the place as typed, and the coordinates come from the lookup.
    """

    name = "weather"
    domain = "weather"
    timeout_seconds = NWS_TIMEOUT + OPEN_METEO_TIMEOUT + 1.0

    def diagnose(self) -> tuple[bool, str]:
        return available()

    async def verify(self) -> tuple[bool, str]:
        place = places.Place("Washington", "District of Columbia",
                             "District of Columbia", "US", 38.8951, -77.0364)
        facts, attempts = await forecast_for(place)
        if facts:
            return True, f"{facts.source} answered for Washington, DC"
        return False, "; ".join(f"{a[0]}: {a[2]}" for a in attempts)

    async def resolve(self, brief) -> Optional[live_facts.Entity]:
        text = (getattr(brief, "place", "") or "").strip()
        if not text:
            return None
        place = await places.resolve(text)
        if place is None or not place.located:
            return None
        _PLACES[place.label] = place
        return live_facts.Entity(
            domain="weather", provider=self.name,
            id=f"{place.latitude:.3f},{place.longitude:.3f}",
            label=place.label)

    async def fetch(self, entity: live_facts.Entity
                    ) -> Optional[live_facts.LiveFacts]:
        place = _PLACES.get(entity.label)
        if place is None:
            lat, lon = (float(x) for x in entity.id.split(","))
            place = places.Place(entity.label, latitude=lat, longitude=lon)
        facts, attempts = await forecast_for(place)
        if facts is None and attempts and \
                all(a[1] in (live_facts.PROVIDER_FAILED, live_facts.TIMEOUT)
                    for a in attempts if a[1] != live_facts.NOT_CONFIGURED):
            raise RuntimeError("; ".join(f"{a[0]}: {a[2]}" for a in attempts))
        return facts


#: Places resolved by `WeatherSource.resolve`, by label, for its `fetch`.
_PLACES: dict = {}


def report() -> dict:
    """What weather this server can serve - for /api/health."""
    ok, why = available()
    om_ok, om_why = open_meteo_available()
    geo_ok, geo_why = places.geocoder_available()
    return {"enabled": bool(settings.weather), "ready": ok, "detail": why,
            "order": [NWS + " (US)", OPEN_METEO],
            "open_meteo": {"ready": om_ok, "detail": om_why},
            "place_lookup": {"ready": geo_ok, "detail": geo_why},
            "cache_seconds": float(settings.weather_cache_seconds)}
