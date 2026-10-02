"""Weather for the place a question names (§194, at the owner's direction).

**Two providers, in a fixed order.** The US National Weather Service first
for a US place: it is free, keyless, it is the forecaster's own forecast, and
its official warnings come with it - a warning copied by a commercial API is a
copy, and this is the authority. Open-Meteo for everywhere else, and for a US
place whenever NWS fails (it has outages); that switch is logged and named in
the episode's record, never silent. Open-Meteo is called with
`OPEN_METEO_API_KEY` (the $29 plan), or keyless only under
`OPEN_METEO_KEYLESS=1`, because its free endpoint is non-commercial.

**On demand, then twice a day per place** (the owner's ruling). What people
ask for is the day's weather - the high, the low, the sky, the chance of
rain - and severe weather. So a place's forecast is fetched the first time
anybody asks about it, kept (`WeatherStore`), and refreshed by a sweep at
05:00 and 17:00 *in that place's own time* (`WEATHER_SWEEP_HOURS`) for as
long as somebody asked about it in the last 30 days. No timer per listener
and no half-hourly cache: calls scale with places and with two a day.

**Severe weather is the exception, and only for warnings.** A US question
asks NWS for the warnings in force at that moment (`WEATHER_LIVE_ALERTS`,
one free call), because a twelve-hour-old warning list is the one stale
fact here that could hurt somebody. If that call fails, the swept warnings
are used and the writer is told when they were checked.

**Rendered when asked, never stored as sentences.** The kept forecast is
structured (periods with start and end times, warnings with expiry, an
observation with its timestamp), and the facts are written at question time:
periods already over are left out, an observation older than two hours is
never called "now", and a warning that has expired is gone.

**A forecast is not an outcome.** The facts say what the forecast *calls
for*; the prompt block (`live_facts`, kind `WEATHER`) forbids turning one into
a certainty. A warning is stated as an official warning with its end on the
listener's clock.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

import credentials
import live_facts
import places
from config import settings
from paths import data_path

log = logging.getLogger("fam.weather")

NWS_BASE = "https://api.weather.gov"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_URL_PAID = "https://customer-api.open-meteo.com/v1/forecast"

NWS = "National Weather Service"
OPEN_METEO = "Open-Meteo"

#: How old an observed temperature may be and still be said as "right now".
OBSERVATION_MAX_AGE = 2 * 3600.0
#: How many forecast periods are said (NWS periods are half days).
PERIODS_SAID = 4
#: Each provider's whole budget: NWS needs a lookup of the grid point and
#: then three calls at once; Open-Meteo is one call.
NWS_TIMEOUT = 2.5
OPEN_METEO_TIMEOUT = 2.0
ALERTS_TIMEOUT = 1.5
#: How long a place stays in the sweep after its last question.
DEMAND_SECONDS = 30 * 86400
#: How often the sweeper wakes to see whose slot has come.
SWEEP_EVERY = 15 * 60

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


def _parse_time(text) -> Optional[datetime]:
    if not text:
        return None
    try:
        when = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# What is kept: a structured forecast, never sentences
# --------------------------------------------------------------------------
@dataclass
class Snapshot:
    """One provider's forecast for one place, as fetched."""

    source: str
    fetched_at: float
    #: When the provider issued it (ISO), which is what `as_of` reports.
    issued: str = ""
    #: The place's IANA time zone, which decides its sweep slots.
    tz: str = ""
    #: Each {"name", "start", "end", "daytime", "temp" | "high"/"low",
    #: "unit", "sky", "rain", "wind"} with ISO start and end.
    periods: list = field(default_factory=list)
    #: Each {"event", "ends"}.
    alerts: list = field(default_factory=list)
    #: {"at", "temp", "unit", "sky", "wind"} or {}.
    observation: dict = field(default_factory=dict)
    #: When the warnings were last checked (epoch seconds).
    alerts_checked: float = 0.0

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> "Snapshot":
        return cls(**json.loads(raw))


def place_key(place: places.Place) -> str:
    return f"{place.latitude:.2f},{place.longitude:.2f}"


class WeatherStore:
    """Each asked-about place's latest forecast, and when it was asked about."""

    def __init__(self, path: str | None = None) -> None:
        self.path = data_path("LOCAL_NEWS_DB", "local_news.db", path)
        self._lock = threading.Lock()
        with closing(self._connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS weather ("
                " key TEXT PRIMARY KEY, place TEXT, asked_at REAL,"
                " fetched_at REAL DEFAULT 0, snapshot TEXT DEFAULT '')")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def asked(self, place: places.Place) -> None:
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO weather (key, place, asked_at) VALUES (?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET asked_at = excluded.asked_at,"
                " place = excluded.place",
                (place_key(place), json.dumps(place.as_dict()), time.time()))

    def get(self, place: places.Place) -> Optional[Snapshot]:
        with closing(self._connect()) as db:
            row = db.execute("SELECT snapshot FROM weather WHERE key = ?",
                             (place_key(place),)).fetchone()
        if not row or not row[0]:
            return None
        try:
            return Snapshot.from_json(row[0])
        except (ValueError, TypeError):
            return None

    def put(self, place: places.Place, snap: Snapshot) -> None:
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO weather (key, place, asked_at, fetched_at, snapshot)"
                " VALUES (?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET"
                " fetched_at = excluded.fetched_at, snapshot = excluded.snapshot",
                (place_key(place), json.dumps(place.as_dict()), time.time(),
                 snap.fetched_at, snap.to_json()))

    def demanded(self, since: float) -> list:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT place, snapshot FROM weather WHERE asked_at >= ?",
                (since,)).fetchall()
        out = []
        for raw_place, raw_snap in rows:
            try:
                place = places.Place(**json.loads(raw_place))
                snap = Snapshot.from_json(raw_snap) if raw_snap else None
            except (ValueError, TypeError):
                continue
            out.append((place, snap))
        return out


_STORE: list = [None]


def store() -> WeatherStore:
    if _STORE[0] is None:
        _STORE[0] = WeatherStore()
    return _STORE[0]


def clear_cache() -> None:
    """Forget the open store and the grid points (tests, and a wipe)."""
    _STORE[0] = None
    _POINTS.clear()


# --------------------------------------------------------------------------
# The sweep slots, in the place's own time
# --------------------------------------------------------------------------
def sweep_hours() -> list:
    out = []
    for part in str(settings.weather_sweep_hours or "").split(","):
        part = part.strip()
        if part.isdigit() and 0 <= int(part) <= 23:
            out.append(int(part))
    return sorted(set(out)) or [5, 17]


def _zone(tz: str):
    try:
        return ZoneInfo(tz) if tz else timezone.utc
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def last_slot(tz: str, now: Optional[float] = None) -> float:
    """The most recent sweep slot at or before `now`, in the place's time."""
    zone = _zone(tz)
    local = datetime.fromtimestamp(now or time.time(), zone)
    candidates = []
    for days_back in (0, 1):
        day = (local - timedelta(days=days_back)).date()
        for hour in sweep_hours():
            slot = datetime(day.year, day.month, day.day, hour, tzinfo=zone)
            if slot <= local:
                candidates.append(slot.timestamp())
    return max(candidates) if candidates else 0.0


def is_current(snap: Optional[Snapshot], now: Optional[float] = None) -> bool:
    """Whether a kept forecast is from this slot - fetched since the last one."""
    if snap is None:
        return False
    return snap.fetched_at >= last_slot(snap.tz, now)


# --------------------------------------------------------------------------
# NWS
# --------------------------------------------------------------------------
_POINTS: dict = {}


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


def nws_alerts(alerts: dict) -> list:
    out = []
    for feature in list((alerts or {}).get("features") or [])[:3]:
        a = feature.get("properties") or {}
        event = str(a.get("event") or "").strip()
        if event:
            out.append({"event": event,
                        "ends": a.get("ends") or a.get("expires") or ""})
    return out


def nws_snapshot(forecast: dict, alerts: dict, observation: dict,
                 tz: str = "", now: Optional[float] = None) -> Optional[Snapshot]:
    """NWS replies -> a snapshot. Pure, so it is tested on recorded replies."""
    props = (forecast or {}).get("properties") or {}
    periods = []
    for p in list(props.get("periods") or []):
        if p.get("temperature") is None or not p.get("name"):
            continue
        periods.append({
            "name": str(p.get("name")), "start": p.get("startTime", ""),
            "end": p.get("endTime", ""), "daytime": bool(p.get("isDaytime")),
            "temp": float(p["temperature"]),
            "unit": "Fahrenheit" if str(p.get("temperatureUnit", "F")) == "F"
                    else "Celsius",
            "sky": str(p.get("shortForecast") or "").strip().lower(),
            "rain": (p.get("probabilityOfPrecipitation") or {}).get("value"),
            "wind": str(p.get("windSpeed") or "").strip()})
    if not periods:
        return None
    obs = (observation or {}).get("properties") or {}
    seen = obs.get("timestamp") or ""
    temp_c = (obs.get("temperature") or {}).get("value")
    observed = {}
    if seen and temp_c is not None:
        observed = {"at": seen, "temp": float(temp_c) * 9 / 5 + 32,
                    "unit": "Fahrenheit",
                    "sky": str(obs.get("textDescription") or "").strip().lower(),
                    "wind": ""}
    fetched = now or time.time()
    return Snapshot(
        source=NWS, fetched_at=fetched,
        issued=props.get("updateTime") or props.get("generatedAt") or "",
        tz=tz, periods=periods, alerts=nws_alerts(alerts),
        observation=observed, alerts_checked=fetched)


async def from_nws(place: places.Place) -> Optional[Snapshot]:
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
    return nws_snapshot(forecast, alerts, obs, tz=str(point.get("timeZone") or ""))


async def live_alerts(place: places.Place) -> Optional[list]:
    """The NWS warnings in force right now. `None` when they could not be read."""
    lat, lon = f"{place.latitude:.4f}", f"{place.longitude:.4f}"
    try:
        async with httpx.AsyncClient(timeout=ALERTS_TIMEOUT) as client:
            reply = await _nws_json(
                client, f"{NWS_BASE}/alerts/active?point={lat},{lon}")
    except Exception as exc:  # noqa: BLE001
        log.warning("weather: NWS warnings could not be read for %s: %s",
                    place.label, exc)
        return None
    return nws_alerts(reply)


# --------------------------------------------------------------------------
# Open-Meteo
# --------------------------------------------------------------------------
def open_meteo_snapshot(place: places.Place, data: dict,
                        now: Optional[float] = None) -> Optional[Snapshot]:
    """An Open-Meteo reply -> a snapshot. Pure, so it is tested on one."""
    us = place.in_us
    unit = "Fahrenheit" if us else "Celsius"
    tz = str((data or {}).get("timezone") or "")
    zone = _zone(tz)
    daily = (data or {}).get("daily") or {}
    periods = []
    for i, day in enumerate(list(daily.get("time") or [])):
        try:
            high = daily["temperature_2m_max"][i]
            low = daily["temperature_2m_min"][i]
            start = datetime.fromisoformat(day).replace(tzinfo=zone)
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if high is None or low is None:
            continue
        code = (daily.get("weather_code") or [None] * (i + 1))[i]
        rain = (daily.get("precipitation_probability_max") or [None] * (i + 1))[i]
        periods.append({
            "name": "", "start": start.isoformat(),
            "end": (start + timedelta(days=1)).isoformat(), "daytime": True,
            "high": float(high), "low": float(low), "unit": unit,
            "sky": WMO.get(int(code), "mixed conditions") if code is not None
                   else "mixed conditions",
            "rain": rain, "wind": ""})
    if not periods:
        return None
    current = (data or {}).get("current") or {}
    observed = {}
    if current.get("temperature_2m") is not None and current.get("time"):
        try:
            at = datetime.fromisoformat(current["time"]).replace(tzinfo=zone)
            observed = {
                "at": at.isoformat(), "temp": float(current["temperature_2m"]),
                "unit": unit,
                "sky": WMO.get(int(current.get("weather_code") or 0), ""),
                "wind": (f"{int(round(float(current['wind_speed_10m'])))} "
                         f"{'miles an hour' if us else 'kilometres an hour'}"
                         if current.get("wind_speed_10m") is not None else "")}
        except (TypeError, ValueError):
            observed = {}
    fetched = now or time.time()
    return Snapshot(source=OPEN_METEO, fetched_at=fetched,
                    issued=datetime.fromtimestamp(fetched, timezone.utc).isoformat(),
                    tz=tz, periods=periods, observation=observed)


async def from_open_meteo(place: places.Place) -> Optional[Snapshot]:
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
    return open_meteo_snapshot(place, reply.json() or {})


# --------------------------------------------------------------------------
# Facts, written when the question is asked
# --------------------------------------------------------------------------
def render(snap: Snapshot, place: places.Place,
           now: Optional[datetime] = None) -> Optional[live_facts.LiveFacts]:
    """A kept forecast -> what the writer is told, as of `now`."""
    import listener_clock

    now = now or datetime.now(timezone.utc)
    zone = _zone(snap.tz)
    facts: list = []

    # Warnings first: the most important thing here, and official.
    for alert in snap.alerts:
        ends = _parse_time(alert.get("ends"))
        if ends is not None and ends <= now:
            continue  # it has expired
        until = (f" until {listener_clock.say(ends, '%A at %H:%M %Z')}"
                 if ends else "")
        facts.append(f"The National Weather Service has issued an official "
                     f"{alert['event']}{until} for this area.")
    if snap.source == NWS and snap.alerts_checked:
        checked = datetime.fromtimestamp(snap.alerts_checked, timezone.utc)
        if (now - checked).total_seconds() > 3600:
            facts.append(
                "Official warnings were last checked at "
                f"{listener_clock.say(checked, '%H:%M %Z')}; any issued since "
                "are not known here.")

    obs = snap.observation or {}
    seen = _parse_time(obs.get("at"))
    if seen and obs.get("temp") is not None and \
            (now - seen).total_seconds() <= OBSERVATION_MAX_AGE:
        line = (f"Observed at {listener_clock.say(seen, '%H:%M %Z')}: "
                f"{_degrees(float(obs['temp']), obs.get('unit', 'Fahrenheit'))}")
        if obs.get("sky"):
            line += f" and {obs['sky']}"
        if obs.get("wind"):
            line += f", wind {obs['wind']}"
        facts.append(line + ".")

    today = now.astimezone(zone).date()
    said = 0
    for p in snap.periods:
        end = _parse_time(p.get("end"))
        if end is not None and end <= now:
            continue  # already over
        if said >= PERIODS_SAID:
            break
        name = p.get("name") or ""
        start = _parse_time(p.get("start"))
        if not name and start is not None:
            day = start.astimezone(zone).date()
            name = ("Today" if day == today else
                    "Tomorrow" if day == today + timedelta(days=1)
                    else start.astimezone(zone).strftime("%A"))
        sky = p.get("sky") or "mixed conditions"
        unit = p.get("unit", "Fahrenheit")
        if "high" in p:
            line = (f"{name}: the forecast calls for {sky}, high "
                    f"{_degrees(p['high'], unit)}, low {_degrees(p['low'], unit)}")
        else:
            level = "high" if p.get("daytime") else "low"
            line = (f"{name}: the forecast calls for {sky}, {level} near "
                    f"{_degrees(p['temp'], unit)}")
        if p.get("rain"):
            line += f", {int(p['rain'])} percent chance of rain"
        if p.get("wind"):
            line += f", wind {p['wind']}"
        facts.append(line + ".")
        said += 1

    if not facts:
        return None
    issued = _parse_time(snap.issued) or datetime.fromtimestamp(
        snap.fetched_at, timezone.utc)
    url = ""
    if snap.source == NWS and place.located:
        url = (f"https://forecast.weather.gov/MapClick.php?lat="
               f"{place.latitude:.4f}&lon={place.longitude:.4f}")
    return live_facts.LiveFacts(domain="weather", source=snap.source,
                                as_of=issued, facts=facts,
                                kind=live_facts.WEATHER, url=url)


# --------------------------------------------------------------------------
# Fetching, on demand and by the sweep
# --------------------------------------------------------------------------
async def fetch(place: places.Place) -> tuple[Optional[Snapshot], list]:
    """Ask the providers in order and keep what answers. Never raises."""
    attempts: list = []
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
            snap = await asyncio.wait_for(ask(place), timeout=budget + 0.5)
        except asyncio.TimeoutError:
            attempts.append((name, live_facts.TIMEOUT, f"{name} timed out"))
            log.warning("weather: %s timed out for %s", name, place.label)
            continue
        except Exception as exc:  # noqa: BLE001 - a provider never ends an episode
            attempts.append((name, live_facts.PROVIDER_FAILED,
                             f"{type(exc).__name__}: {exc}"))
            log.warning("weather: %s failed for %s: %s", name, place.label, exc)
            continue
        if snap is None:
            attempts.append((name, live_facts.NO_FACTS, "nothing returned"))
            continue
        if any(a[0] == NWS for a in attempts):
            log.warning("weather: NWS could not serve %s; %s answered instead",
                        place.label, name)
        attempts.append((name, live_facts.FACTS, place.label))
        try:
            store().put(place, snap)
        except sqlite3.Error:
            log.warning("weather: could not keep the forecast for %s",
                        place.label, exc_info=True)
        return snap, attempts
    return None, attempts


async def forecast_for(place: Optional[places.Place]
                       ) -> tuple[Optional[live_facts.LiveFacts], list]:
    """The weather at `place`, and what was done to get it. Never raises.

    The kept forecast when it is from the current sweep slot; otherwise it
    is fetched now (the first question about a place, or a missed sweep).
    For a US place the warnings in force are asked for at the moment of the
    question (`WEATHER_LIVE_ALERTS`). Returns `(facts or None, attempts)`,
    `attempts` being `(provider, outcome, detail)` in the order tried.
    """
    if place is None or not place.located:
        return None, [("weather", live_facts.NO_ENTITY,
                       "the place has no coordinates")]
    if not settings.weather:
        return None, [("weather", live_facts.NOT_CONFIGURED, "WEATHER=0")]

    try:
        store().asked(place)
        snap = store().get(place)
    except sqlite3.Error:
        log.warning("weather: the store could not be read", exc_info=True)
        snap = None
    attempts: list = []
    if is_current(snap):
        attempts.append(("kept", live_facts.FACTS, snap.source))
    else:
        fresh, attempts = await fetch(place)
        if fresh is not None:
            snap = fresh
        elif snap is not None:
            # Every provider failed: the last kept forecast, with its issue
            # time, is still a forecast - and is withheld by `live_facts`
            # once it is past the domain's limit.
            attempts.append(("kept", live_facts.FACTS,
                             "the last kept forecast; the providers failed"))
    if snap is None:
        return None, attempts

    if place.in_us and settings.weather_live_alerts and \
            time.time() - snap.alerts_checked > 600:
        alerts = await live_alerts(place)
        if alerts is not None:
            snap.alerts, snap.alerts_checked = alerts, time.time()
            attempts.append((NWS + " warnings", live_facts.FACTS,
                             f"{len(alerts)} in force"))
            try:
                store().put(place, snap)
            except sqlite3.Error:
                pass
        else:
            attempts.append((NWS + " warnings", live_facts.PROVIDER_FAILED,
                             "the swept warnings are used"))
    return render(snap, place), attempts


async def sweep_due(now: Optional[float] = None) -> int:
    """Refresh every asked-about place whose sweep slot has come."""
    if not settings.weather:
        return 0
    now = now or time.time()
    try:
        due = [place for place, snap in store().demanded(now - DEMAND_SECONDS)
               if place.located and not is_current(snap, now)]
    except sqlite3.Error:
        log.warning("weather: the sweep could not read the store", exc_info=True)
        return 0
    for place in due:
        await fetch(place)
    return len(due)


async def run_forever(every: float = SWEEP_EVERY) -> None:
    """The twice-daily sweep: wakes often, fetches only places whose slot came."""
    while True:
        try:
            swept = await sweep_due()
            if swept:
                log.info("weather: swept %d place(s)", swept)
        except Exception:  # noqa: BLE001
            log.warning("weather: a sweep failed", exc_info=True)
        await asyncio.sleep(every)


class WeatherSource(live_facts.LiveSource):
    """Weather as a live-facts domain, for a question that *is* about weather.

    Resolution goes to a catalogue (`places.resolve`), never to the model:
    EI names the place as typed, and the coordinates come from the lookup.
    """

    name = "weather"
    domain = "weather"
    timeout_seconds = NWS_TIMEOUT + OPEN_METEO_TIMEOUT + ALERTS_TIMEOUT + 1.0

    def diagnose(self) -> tuple[bool, str]:
        return available()

    async def verify(self) -> tuple[bool, str]:
        place = places.Place("Washington", "District of Columbia",
                             "District of Columbia", "US", 38.8951, -77.0364)
        snap, attempts = await fetch(place)
        if snap:
            return True, f"{snap.source} answered for Washington, DC"
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
            "sweep_hours_local": sweep_hours(),
            "live_alerts": bool(settings.weather_live_alerts)}
