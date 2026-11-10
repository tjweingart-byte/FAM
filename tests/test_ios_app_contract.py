"""The iOS app (`ios/FAM`) asks for what the server has.

The Swift was written without a compiler to hand (PROBLEMS.md §538). A
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


_LITERAL = r'"(api/v1/[^"]*)"(\s*\+\s*\w+)?'


def _norm(path: str, tail: str | None = None) -> str:
    """`api/v1/mixes/\\(id)/open` and `"api/v1/mixes/" + id` as the route
    template they hit: every interpolated or appended part is `{}`."""
    path = re.sub(r"\\\([^)]*\)", "{}", path)
    if tail:
        path += "{}"
    return path


def _route(path: str) -> str:
    """A server route with its parameters written `{}`."""
    return re.sub(r"\{[^}]*\}", "{}", path)


def _calls() -> set[tuple[str, str]]:
    """(method, path) for every call the app makes."""
    found = set()
    for verb, lit, tail in re.findall(r'(?:send|sendQuery)\("(GET|POST|DELETE|PATCH)", ' + _LITERAL, _api()):
        found.add((verb, _norm(lit, tail)))
    for lit, tail in re.findall(r'(?:request\("GET", |\bget\()' + _LITERAL, _api()):
        found.add(("GET", _norm(lit, tail)))
    return found


def test_every_path_the_app_names_routes():
    paths = {_norm(lit, tail) for lit, tail in re.findall(_LITERAL, _api())}
    routes = {_route(getattr(r, "path", "")) for r in appmod.app.routes}
    missing = sorted(p for p in paths if "/" + p.replace("api/v1/", "api/") not in routes)
    assert not missing, missing


def test_every_call_routes_with_its_method():
    calls = _calls()
    assert ("GET", "api/v1/myfam") in calls and ("DELETE", "api/v1/account") in calls, calls
    assert ("PATCH", "api/v1/mixes/{}") in calls and ("POST", "api/v1/mixes/{}/open") in calls, calls
    routes = {(m, _route(r.path)) for r in appmod.app.routes
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
    # `activation_needed` is `/me`'s alone (§411); a sign-up decodes it nil.
    assert fields - {"activation_needed"} <= set(signup), sorted(fields - set(signup))
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
    # `spelled`: the question as the speller would send it, for a chip with
    # no title (`deriveTitleFromPrompt(spelled || query)`).
    assert fields == {"query", "title", "minutes", "spelled"}, fields
    # What the handler appends for each search, read from its source so an
    # empty deployment still checks the shape.
    import inspect
    source = inspect.getsource(appmod.trending_searches)
    for name in fields:
        assert f'"{name}":' in source or f'["{name}"]' in source, name
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
    # `episode` names a kept episode to replay exactly (§173, §288): it
    # never writes, so the loading screen has nothing to ask about it.
    # `ask_reading` lets the server ask which reading of a name is meant
    # (300, §426); the reading chosen (`reading`) is part of the key, so
    # both requests carry that.
    # searchFAM's four are not the key either (`pipeline.key_for`): `voice`
    # and `voice_picked` choose how it is spoken, not what is written (the
    # loading screen's marks are the script's); `private` decides where it
    # is filed, not which episode; `attach` makes an episode with no key at
    # all, and `/api/progress` takes no attachments - the web's
    # `trackGenSteps` sends exactly these same fields.
    not_key = {"fmt", "own", "cached_only", "episode", "ask_reading",
               "voice", "voice_picked", "private", "attach"}
    assert names("audio") - not_key == names("progress"), (names("audio"), names("progress"))


def test_the_players_side_channels_decode_what_the_server_sends():
    """Every field the player decodes from /api/next, /api/sources,
    /api/transcript, /api/episode/card, /api/saved and /api/share is a key
    the server writes somewhere - a renamed key would fail on a phone."""
    server = "\n".join(p.read_text() for p in ROOT.glob("*.py"))
    for struct in ("NextThread", "Sources", "SourceItem", "Transcript", "EpisodeCard",
                   "SavedState", "SharedLink", "NextUpPage",
                   # A refused episode: "Which one do you mean?" and the limit
                   # screen (§426, `showLimitReached`).
                   "AudioRefusal", "QuotaVerdict",
                   # DailyFAM: View more, Pick up, the faces row, the library.
                   "SectionPage", "TileGroup", "PickUpPage", "PickUpThread", "PickUpResume",
                   "SavedPage", "SavedItem", "CirclePage", "CirclePerson", "CircleMe", "Story",
                   "CatalogPage", "CatalogEpisode", "FriendFace", "RailsPage",
                   # myFAM: mixes, the catalogue, sharing, push, the episode's topic.
                   "Mix", "MixItem", "MixPerson", "MixOwner", "RecommendedTopics", "Catalogue",
                   "Subject", "MixInvite", "MixShare", "ShareTarget", "PushStatus", "EpisodeTopic",
                   "ProfileSummary", "ProfileInterest",
                   # The category chips and panel on the myFAM tab (§500).
                   "CategoriesPage", "CategoryFacet", "CategoryNode", "CategoryEpisodes",
                   "CategorySub", "CategoryEpisode",
                   # Messages, friends and a friend's profile (§213, §282, §285).
                   "InboxPage", "InboxThread", "ChatMember", "ChatMessage", "ChatSender",
                   "NoticeGroup", "ChatPage", "ChatWith", "SentMessage", "GroupAnswer",
                   "UnreadAnswer", "NoticePage", "FollowPerson", "FriendsPage", "FollowCounts",
                   "FollowAnswer", "PeoplePage", "PersonPage", "PersonMix", "PersonVibe",
                   "PersonFriend", "ProfileEcho",
                   # YourFAM: the hub, Settings, Edit profile, Saved for Later,
                   # the listening history and My Vibe (`op-profile-hub`).
                   "AccountInfo", "MyProfile", "MyVibe", "ProfileMix", "MyPreferences",
                   "PlaceInfo", "FacetLabel", "ResetStatus", "ResetStarted", "Activation",
                   "ActivationGender", "ActivationHelp", "ActivationCounting", "CountingNotice",
                   "ActivationAnswer", "PlansPage", "PlanTier", "PlanLimit", "VoicesPage",
                   "VoiceOption", "SavedShelf", "SavedFolder", "FolderAnswer", "HistoryPage",
                   "HistoryItem", "VibesPage", "VibeRow",
                   # searchFAM: the speller, attachments, trending searches.
                   "SpellAnswer", "AttachedFile", "TrendingSearch"):
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


def test_sign_in_methods_decode_what_the_account_sends(monkeypatch):
    """YourFAM's Sign-in methods read `/api/account`; linking is a provider
    sign-in while signed in, and unlinking is DELETE /api/account/identity."""
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    c = TestClient(appmod.app)
    headers = {"X-FAM-Client": "ios/1.0.0+2"}
    token = c.post("/api/v1/auth/signup", headers=headers, json={
        "email": "ios-methods@example.com", "password": "password12",
        "accept_terms": True, "want_token": True}).json()["session_token"]
    page = c.get("/api/v1/account", headers={**headers, "Authorization": "Bearer " + token}).json()
    assert _fields("AccountInfo") <= set(page), sorted(_fields("AccountInfo") - set(page))
    assert page["has_password"] is True
    assert [i["provider"] for i in page["identities"]] == ["email"]
    server = "\n".join(p.read_text() for p in ROOT.glob("*.py"))
    for field in _fields("Identity"):
        assert f'"{field}"' in server, field
    assert ("DELETE", "api/v1/account/identity") in _calls()
    model = (SWIFT / "AppModel.swift").read_text()
    assert "func link(provider:" in model and "acceptTerms: false" in model


def test_a_mix_plays_as_the_web_plays_it():
    """A mix's episode is today's edition on the `dailyfam` surface, at the
    item's own length, dated by the server's prompt when it sends one; its
    loading screen is the glide (§509), and a reorder writes the whole list
    once while add/remove are operations (§498)."""
    model = (SWIFT / "AppModel.swift").read_text()
    mixes = (SWIFT / "Mixes.swift").read_text()
    assert 'surface: "dailyfam"' in model
    assert "minutes: item.minutes" in model
    assert "LoadingModel.Glide(" in model
    assert 'replacingOccurrences(of: "{date}"' in mixes
    assert "item.prompt.isEmpty && !item.dailyPrompt.isEmpty" in mixes
    views = "\n".join(p.read_text() for p in (SWIFT / "Views").glob("Mix*.swift"))
    assert '["topic_ids": items.map(\\.entry)]' in views
    assert '[adding ? "add" : "remove": [topicID]]' in views
    assert '["remove": [item.id]]' in model
    # The server's mix fields the app reads are the ones it writes.
    server = "\n".join(p.read_text() for p in ROOT.glob("*.py"))
    for name in ("listen_at", "listen_tz", "people", "role", "joint", "source_id"):
        assert f'"{name}"' in server, name


def test_the_subject_icons_are_the_webs():
    """`SubjectIcons.swift` is generated from the web's `CAT_ICONS`; a change
    to either without the other fails here."""
    import subprocess
    run = subprocess.run([sys.executable, str(ROOT / "tools" / "ios_icons.py"), "--check"],
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stderr


def test_the_topic_screen_and_story_cards_decode(monkeypatch):
    """The Topic screen behind every interest chip reads `/api/interest`, and
    the share sheets' story cards come from the `card` the share answers carry
    (an SVG the app rasterises to a PNG)."""
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    c = TestClient(appmod.app)
    headers = {"X-FAM-Client": "ios/1.0.0+2"}
    page = c.get("/api/v1/interest", headers=headers,
                 params={"id": "f:nfl", "label": "NFL", "filter": "latest", "offset": 0}).json()
    assert _fields("InterestPage") <= set(page), sorted(_fields("InterestPage") - set(page))
    friends = c.get("/api/v1/interest", headers=headers,
                    params={"id": "f:nfl", "label": "NFL", "filter": "friends"}).json()
    assert "reason" in friends and "episodes" in friends
    src = inspect.getsource(appmod.interest_episodes)
    for field in _fields("InterestCard") | _fields("InterestVibedBy"):
        assert f'"{field}"' in src or f"{field}=" in src, field
    share = c.post("/api/v1/share", headers=headers,
                   json={"query": "why is the sky blue", "minutes": 1.5, "title": "Sky"}).json()
    assert _fields("SharedLink") <= set(share), sorted(_fields("SharedLink") - set(share))
    from urllib.parse import urlsplit
    where = urlsplit(share["card"])
    card = c.get(where.path + ("?" + where.query if where.query else ""), headers=headers)
    assert card.status_code == 200 and "<svg" in card.text
    assert '"card"' in inspect.getsource(appmod.share_mix)
