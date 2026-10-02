"""Make real weather calls and say exactly what came back (§193).

    python tools/verify_weather.py "San Anselmo, California, US"
    python tools/verify_weather.py "Paris, Ile-de-France, FR"

Checks each part on its own, with a real request - a key being set is not the
key working: the place-name lookup (Open-Meteo), NWS's forecast, warnings
and observation (US places), and Open-Meteo's forecast. Then prints the facts
the writer would be given. Exit code 0 only when every part that should
answer did.

Run it where api.weather.gov and open-meteo.com are reachable - this
project's build container blocks both. Uses a scratch store, so a
deployment's kept forecasts and sweep list are untouched.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("LOCAL_NEWS_DB",
                      str(Path(tempfile.mkdtemp()) / "verify_weather.db"))

import places  # noqa: E402
import weather  # noqa: E402


async def main_async(text: str) -> int:
    ok = True
    geo_ok, geo_why = places.geocoder_available()
    print(f"place lookup   {'ready' if geo_ok else 'NOT READY'} - {geo_why}")
    place = await places.resolve(text)
    if place is None or not place.located:
        print(f"  could not place {text!r} - weather needs coordinates")
        return 1
    print(f"  {place.label} | county {place.county or '?'} | {place.country} | "
          f"{place.latitude:.4f}, {place.longitude:.4f}")

    if place.in_us:
        try:
            snap = await weather.from_nws(place)
            print(f"NWS            ok - {len(snap.periods)} periods, "
                  f"{len(snap.alerts)} warning(s), zone {snap.tz or '?'}, "
                  f"observation {'yes' if snap.observation else 'none'}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"NWS            FAILED - {type(exc).__name__}: {exc}")

    om_ok, om_why = weather.open_meteo_available()
    if om_ok:
        try:
            snap = await weather.from_open_meteo(place)
            print(f"Open-Meteo     ok - {len(snap.periods)} days, zone {snap.tz}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"Open-Meteo     FAILED - {type(exc).__name__}: {exc}")
    else:
        print(f"Open-Meteo     not configured - {om_why}")
        ok = ok and place.in_us

    facts, attempts = await weather.forecast_for(place)
    print("\nwhat the episode would use:")
    for name, outcome, detail in attempts:
        print(f"  {name:<30} {outcome:<16} {detail}")
    if facts is None:
        print("  NO WEATHER")
        return 1
    print(f"  source {facts.source}, issued {facts.as_of.isoformat()}")
    for line in facts.facts:
        print(f"    - {line}")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("place", help='"Town, State, Country"')
    return asyncio.run(main_async(parser.parse_args().place))


if __name__ == "__main__":
    sys.exit(main())
