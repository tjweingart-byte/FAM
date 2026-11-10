"""The audio spike (`ios/FAMSpike`) asks for what the server gives a guest.

The Swift was written without a compiler (PROBLEMS.md §518). A phone would
find a broken request only as a 403 or a decoding failure, so CI checks the
parts a compiler could not: every path routes, every field the spike decodes
is in a sample, and the audio request has exactly the shape the waitlist
lets through for a guest (`app._welcome_sample_request`).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlencode

from starlette.requests import Request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as appmod  # noqa: E402
import topics as topics_mod  # noqa: E402
from config import BROWSE_MINUTES  # noqa: E402

API = ROOT / "ios" / "FAMSpike" / "FAMSpike" / "SpikeAPI.swift"


def _swift() -> str:
    return API.read_text()


def _audio_items() -> dict[str, str]:
    """The query items `SpikeAPI.audio(for:)` builds, as name -> value."""
    body = re.search(r"func audio\(for sample: Sample\).*?\n    \}", _swift(), re.S)
    assert body, "no audio(for:) in SpikeAPI.swift"
    items = dict(re.findall(r'URLQueryItem\(name: "(\w+)", value: ([^)]+)\)', body.group(0)))
    assert items, "audio(for:) builds no query items"
    return items


def test_every_path_the_spike_asks_for_routes():
    paths = set(re.findall(r'"(api/v1/[a-z/\-]+)"', _swift()))
    assert paths == {"api/v1/welcome", "api/v1/audio"}, paths
    routes = {getattr(r, "path", "") for r in appmod.app.routes}
    missing = sorted(p for p in paths if "/" + p.replace("api/v1/", "api/") not in routes)
    assert not missing, missing


def test_every_field_the_spike_decodes_is_in_a_sample():
    body = re.search(r"struct Sample:[^{]*\{(.*?)\n    \}", _swift(), re.S)
    assert body, "no Sample in SpikeAPI.swift"
    fields = set(re.findall(r"let (\w+):", body.group(1)))
    assert fields, "Sample decodes nothing"
    tile = {**topics_mod.TOPIC_BANK[0].as_dict(), "minutes": BROWSE_MINUTES}
    assert fields <= set(tile), sorted(fields - set(tile))


def test_the_sample_length_is_the_kept_length():
    kept = re.search(r'static let sampleMinutes = "([^"]+)"', _swift())
    assert kept and kept.group(1) == str(BROWSE_MINUTES)


def test_the_audio_request_is_one_a_guest_may_make(monkeypatch):
    items = _audio_items()
    assert items.get("cached_only") == '"true"', "a guest may only replay"
    assert items.get("fmt") == '"pcm"', "the spike decodes PCM only"
    q = "how does a heat pump work"
    values = {k: (q if v.strip() == "sample.query" else
                  str(BROWSE_MINUTES) if v.strip() == "Self.sampleMinutes" else
                  v.strip().strip('"'))
              for k, v in items.items()}
    monkeypatch.setattr(appmod, "_welcome_episodes", lambda: [{"query": q}])
    monkeypatch.setattr(appmod, "_audio_is_kept", lambda query, minutes: True)
    request = Request({"type": "http", "method": "GET", "path": "/api/audio",
                       "query_string": urlencode(values).encode(), "headers": []})
    assert appmod._welcome_sample_request(request), values
