"""The iOS app (`ios/FAM`) asks for what the server has.

The Swift was written without a compiler to hand (PROBLEMS.md §502). A
compiler would not catch these anyway: a path that is not a route, a field
the server does not send, or a request the waitlist refuses all fail on a
phone, in review. So CI checks that every `/api/v1/...` path routes with the
method the app uses, that every field the app decodes is in the server's
real answer, and that the requests the waitlist must let through still are.
"""
from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path
from urllib.parse import urlencode

from fastapi.testclient import TestClient
from starlette.requests import Request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as appmod  # noqa: E402
import topics as topics_mod  # noqa: E402
from config import BROWSE_MINUTES  # noqa: E402

SWIFT = ROOT / "ios" / "FAM" / "FAM"
API = SWIFT / "API.swift"


def _api() -> str:
    return API.read_text()


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)([A-Z])", r"_\1", name).lower()


def _fields(struct: str) -> set[str]:
    body = re.search(r"struct %s:[^{]*\{(.*?)\n\}" % struct, _api(), re.S)
    assert body, f"no struct {struct} in API.swift"
    return {_snake(m) for m in re.findall(r"^    let (\w+):", body.group(1), re.M)}


def _calls() -> set[tuple[str, str]]:
    """(method, path) for every call the app makes."""
    found = set(re.findall(r'send\("(GET|POST|DELETE|PATCH)", "(api/v1/[a-z/\-]+)"', _api()))
    found |= {("GET", p) for p in re.findall(r'request\("GET", "(api/v1/[a-z/\-]+)"', _api())}
    found |= {("GET", p) for p in re.findall(r'\bget\("(api/v1/[a-z/\-]+)"', _api())}
    found |= set(re.findall(r'sendQuery\("(GET|POST|DELETE|PATCH)", "(api/v1/[a-z/\-]+)"', _api()))
    return found


def test_every_path_the_app_names_routes():
    paths = set(re.findall(r'"(api/v1/[a-z/\-]+)"', _api()))
    routes = {getattr(r, "path", "") for r in appmod.app.routes}
    missing = sorted(p for p in paths if "/" + p.replace("api/v1/", "api/") not in routes)
    assert not missing, missing


def test_every_call_routes_with_its_method():
    calls = _calls()
    assert ("GET", "api/v1/myfam") in calls and ("DELETE", "api/v1/account") in calls, calls
    routes = {(m, r.path) for r in appmod.app.routes
              for m in (getattr(r, "methods", None) or ())}
    missing = sorted((m, p) for m, p in calls
                     if (m, "/" + p.replace("api/v1/", "api/")) not in routes)
    assert not missing, missing


def test_signup_says_the_terms_were_agreed():
    body = re.search(r"func signUp.*?\n    \}", _api(), re.S).group(0)
    assert '"accept_terms": true' in body
    assert '"want_token": true' in body


def test_the_listener_fields_are_sent(monkeypatch):
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    c = TestClient(appmod.app)
    headers = {"X-FAM-Client": "ios/1.0.0+2"}
    signup = c.post("/api/v1/auth/signup", headers=headers, json={
        "email": "ios-contract@example.com", "password": "password12",
        "accept_terms": True, "want_token": True}).json()
    fields = _fields("Listener")
    assert fields <= set(signup), sorted(fields - set(signup))
    token = signup["session_token"]
    me = c.get("/api/v1/auth/me", headers={**headers, "Authorization": "Bearer " + token}).json()
    required = fields - {"session_token", "display_name", "status"}
    assert required <= set(me), sorted(required - set(me))
    # A phone that has no session asks too, and must still decode.
    guest = TestClient(appmod.app).get("/api/v1/auth/me", headers=headers).json()
    assert {"user_id", "email", "authenticated"} <= set(guest)


def test_rails_and_tiles_decode():
    # The endpoint adds these to every tile (`/api/myfam`: cached, the
    # friends who listened); the app decodes them leniently.
    added = {"cached", "friends_listened", "friends_faces"}
    source = inspect.getsource(appmod)
    for name in added:
        assert f'"{name}"' in source, name
    tile = topics_mod.TOPIC_BANK[0].as_dict()
    assert _fields("Tile") - added <= set(tile), sorted(_fields("Tile") - added - set(tile))
    assert _fields("Rail") <= {"key", "title", "topics", "empty_reason", "has_circle"}


def test_rails_answer_has_sections(monkeypatch):
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)
    page = TestClient(appmod.app).get("/api/v1/myfam",
                                      headers={"X-FAM-Client": "ios/1.0.0+2"}).json()
    assert "sections" in page
    for section in page["sections"]:
        # `has_circle` is the friends rail's alone.
        assert _fields("Rail") - {"has_circle"} <= set(section), section.keys()


def test_the_browse_length_is_the_servers():
    kept = re.search(r"static let browseMinutes = ([0-9.]+)", _api())
    assert kept and float(kept.group(1)) == BROWSE_MINUTES


def test_a_waiting_listener_may_play_the_samples(monkeypatch):
    body = re.search(r"func sampleAudio.*?\n    \}", _api(), re.S).group(0)
    items = dict(re.findall(r'URLQueryItem\(name: "(\w+)", value: ([^)]+?)\)', body))
    assert items.get("cached_only") == '"true"'
    q = "how does a heat pump work"
    values = {k: (q if v == "query" else
                  str(BROWSE_MINUTES) if v.startswith("Self.minutes") else v.strip('"'))
              for k, v in items.items()}
    monkeypatch.setattr(appmod, "_welcome_episodes", lambda: [{"query": q}])
    monkeypatch.setattr(appmod, "_audio_is_kept", lambda query, minutes: True)
    request = Request({"type": "http", "method": "GET", "path": "/api/audio",
                       "query_string": urlencode(values).encode(), "headers": []})
    assert appmod._welcome_sample_request(request), values


def test_a_tile_names_its_surface_and_topic():
    body = re.search(r"func audio\(query.*?\n    \}", _api(), re.S).group(0)
    assert 'name: "surface"' in body and 'name: "topic_id"' in body
    assert '"fmt", value: "pcm"' in body
    model = (SWIFT / "AppModel.swift").read_text()
    assert 'surface: "myfam"' in model and 'surface: "search"' in model


def test_trending_searches_decode(monkeypatch):
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)
    fields = _fields("TrendingSearch")
    assert fields == {"query", "title", "minutes"}, fields
    # What the handler appends for each search, read from its source so an
    # empty deployment still checks the shape.
    import inspect
    source = inspect.getsource(appmod.trending_searches)
    for name in fields:
        assert f'"{name}":' in source, name
    page = TestClient(appmod.app).get("/api/v1/searches/trending",
                                      headers={"X-FAM-Client": "ios/1.0.0+2"}).json()
    assert "searches" in page


def test_the_loading_screen_keeps_the_webs_clock():
    """The five steps' timings are the web's (§148, §263), read from
    static/index.html, so the two screens cannot drift apart."""
    web = (ROOT / "static" / "index.html").read_text()
    swift = (SWIFT / "Views" / "LoadingView.swift").read_text()
    pairs = {
        "GEN_STEP_MIN_MS": "stepMin",
        "GEN_STEP_FIRST_MIN_MS": "firstStepMin",
        "GEN_STEP_POLL_MS": "poll",
        "GEN_STEP_FIRST_POLL_MS": "firstPoll",
        "GEN_STEP_LAST_BEAT_MS": "lastBeat",
        "GEN_MIN_VISIBLE_MS": "minVisible",
    }
    for js, name in pairs.items():
        ms = re.search(r"var %s = (\d+);" % js, web)
        assert ms, js
        seconds = re.search(r"static let %s: TimeInterval = ([0-9.]+)" % name, swift)
        assert seconds, name
        assert abs(float(seconds.group(1)) * 1000 - int(ms.group(1))) < 1e-6, (js, name)
    # The same five labels, in the same order.
    labels = re.findall(r'<li data-step="\d"><span class="fam-step-dot">.*?</span><span>([^<]+)</span></li>',
                        web)
    assert labels and all(f'"{label}"' in swift for label in labels), labels


def test_progress_asks_about_the_same_episode_as_the_audio():
    """`/api/progress` is keyed like the audio request: the same fields."""
    def names(fn: str) -> set[str]:
        body = re.search(r"func %s\(.*?\n    \}" % fn, _api(), re.S).group(0)
        return set(re.findall(r'URLQueryItem\(name: "(\w+)"', body))
    # `fmt` is how to send it and `own` whose words they are; neither is the key.
    # `fmt` is how to send it, `own` whose words, `cached_only` a replay;
    # none of them changes which episode it is.
    assert names("audio") - {"fmt", "own", "cached_only"} == names("progress"), (names("audio"), names("progress"))


def test_the_players_side_channels_decode_what_the_server_sends():
    """Every field the player decodes from /api/next, /api/sources,
    /api/transcript, /api/episode/card, /api/saved and /api/share is a key
    the server writes somewhere - a renamed key would fail on a phone."""
    server = "\n".join(p.read_text() for p in ROOT.glob("*.py"))
    for struct in ("NextThread", "Sources", "SourceItem", "Transcript", "EpisodeCard",
                   "SavedState", "SharedLink", "NextUpPage",
                   # DailyFAM: View more, Pick up, the faces row, the library.
                   "SectionPage", "TileGroup", "PickUpPage", "PickUpThread", "PickUpResume",
                   "SavedPage", "SavedItem", "CirclePage", "CirclePerson", "CircleMe", "Story",
                   "CatalogPage", "CatalogEpisode", "FriendFace", "RailsPage"):
        body = re.search(r"struct %s:[^{]*\{(.*?)\n\}" % struct, _api(), re.S)
        assert body, struct
        for field in {_snake(m) for m in re.findall(r"^    let (\w+):", body.group(1), re.M)}:
            assert f'"{field}"' in server, (struct, field)


def test_listening_is_recorded_like_the_web():
    """The taste model hears iOS: complete at 85%, skip, progress, history."""
    now = (SWIFT / "NowPlaying.swift").read_text()
    web = (ROOT / "static" / "index.html").read_text()
    share = re.search(r"var FINISHED_SHARE = ([0-9.]+);", web).group(1)
    assert f"static let finishedShare = {share}" in now
    for kind in ('"complete"', '"skip"', '"search"'):
        assert kind in now, kind
    assert "api.noteResume(" in now and "api.noteListen(" in now


def test_provider_sign_in_sends_what_the_server_reads():
    """Sign in with Apple / Google: every body field is a ProviderRequest
    field, the token is asked for, and Apple's one-time code is passed so
    deleting the account can revoke it (5.1.1(v))."""
    body = re.search(r"func signIn\(provider:.*?\n    \}", _api(), re.S).group(0)
    sent = set(re.findall(r'"(\w+)":', body)) | set(re.findall(r'body\["(\w+)"\]', body))
    fields = set(appmod.ProviderRequest.model_fields)
    assert sent <= fields, sorted(sent - fields)
    assert {"provider", "id_token", "nonce", "accept_terms", "authorization_code", "want_token"} <= sent
    swift = (SWIFT / "Views" / "ProviderSignIn.swift").read_text()
    # Apple is sent the hash of the nonce; the server gets the raw value.
    assert "request.nonce = Self.sha256(appleNonce)" in swift
    assert 'send(provider: "apple", token: token, nonce: appleNonce' in swift
