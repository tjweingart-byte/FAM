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
    # `activation_needed`, `waitlist` and `home` are `/me`'s alone (§411);
    # a sign-up decodes them nil.
    mine = {"activation_needed", "waitlist", "home"}
    assert fields - mine <= set(signup), sorted(fields - mine - set(signup))
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
    # Opus as the web asks (decision 3, §242), and a replay may be a link
    # to its kept file (§252); the player falls back to PCM by itself.
    assert '"fmt", value: "opus"' in body and '"link", value: "1"' in body
    player = (SWIFT / "Player.swift").read_text()
    assert 'fmt: "pcm"' in player and "link_failed" in player
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


def test_the_loading_screen_gives_up_as_the_web_does():
    """§497: the give-up time and its two sentences are the web's, and the
    voice's own wait is a field `/api/progress` really sends."""
    web = (ROOT / "static" / "index.html").read_text()
    swift = (SWIFT / "Views" / "LoadingView.swift").read_text()
    for js, name in {"GEN_QUIET_MS": "quiet", "GEN_VOICE_MARGIN_MS": "voiceMargin"}.items():
        ms = re.search(r"var %s = (\d+);" % js, web)
        seconds = re.search(r"static let %s: TimeInterval = ([0-9.]+)" % name, swift)
        assert ms and seconds, (js, name)
        assert abs(float(seconds.group(1)) * 1000 - int(ms.group(1))) < 1e-6, (js, name)
    for js in ("GEN_SERVER_SILENT", "GEN_VOICE_SLOW"):
        words = re.search(r'var %s = "([^"]+)";' % js, web)
        assert words and f'"{words.group(1)}"' in swift, js
    assert "let voiceWait: Double?" in _api()
    assert "voice_wait=_voice_wait()" in (ROOT / "app.py").read_text()


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
    # `link` asks for a replay's kept file instead of a stream (§252).
    not_key = {"fmt", "link", "own", "cached_only", "episode", "ask_reading",
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
                   # A replay answered with a link to its kept Opus (§252).
                   "AudioLinkBody",
                   # A shared /s/ link opened in the app (decision 5), and the
                   # bank's tags for Recommended topics (`itemTags`).
                   "SharePayload", "BankTopic", "BankPage",
                   # DailyFAM: View more, Pick up, the faces row, the library.
                   "SectionPage", "TileGroup", "PickUpPage", "PickUpThread", "PickUpResume",
                   "SavedPage", "SavedItem", "CirclePage", "CirclePerson", "CircleMe", "Story",
                   # The story editor's layout, its stickers and @tags (§213, §297).
                   "StoryStyle", "StorySticker", "StoryTag",
                   "CatalogPage", "CatalogEpisode", "FriendFace", "RailsPage",
                   # Friends who listened, on the player (§543, `/api/listened`).
                   "ListenedPage",
                   # myFAM: mixes, the catalogue, sharing, push, the episode's topic.
                   "Mix", "MixItem", "MixPerson", "MixOwner", "RecommendedTopics", "Catalogue",
                   "Subject", "MixInvite", "MixShare", "ShareTarget", "PushStatus", "EpisodeTopic",
                   # A story card handed to Instagram or Snapchat (§558), and the
                   # notification choices on /api/push and /api/push/prefs (§562).
                   "StoryHandoff", "PushPrefs", "PushPrefsAnswer",
                   "ProfileSummary", "ProfileInterest",
                   # What changed since you last listened (§545): R5 on the
                   # card, Q5's bubbles inside the mix.
                   "MixSignal", "MixSince", "MixSinceSide",
                   # The category chips and panel on the myFAM tab (§500).
                   "CategoriesPage", "CategoryFacet", "CategoryNode", "CategoryEpisodes",
                   "CategorySub", "CategoryEpisode",
                   # Mixes | Categories' list and the morning dot (§550).
                   "CategoryChild", "FreshMixes",
                   # Messages, friends and a friend's profile (§213, §282, §285).
                   "InboxPage", "InboxThread", "ChatMember", "ChatMessage", "ChatSender",
                   "NoticeGroup", "ChatPage", "ChatWith", "SentMessage", "GroupAnswer",
                   "UnreadAnswer", "NoticePage", "FollowPerson", "FriendsPage", "FollowCounts",
                   "FollowAnswer", "PeoplePage", "PersonPage", "PersonMix", "PersonVibe",
                   "PersonFriend", "ProfileEcho",
                   # A grid vibe as posts: the line, likes, who heard it and
                   # the replies under it (§547, `vibe-posts`).
                   "VibePostsPage", "VibePostPerson", "VibePost", "VibePostLine",
                   "VibePostHeard", "VibeReply", "VibeRepliesPage", "VibeLikeAnswer",
                   # YourFAM: the hub, Settings, Edit profile, Saved for Later,
                   # the listening history and My Vibe (`op-profile-hub`).
                   "AccountInfo", "MyProfile", "MyVibe", "ProfileMix", "MyPreferences",
                   "PlaceInfo", "FacetLabel", "ResetStatus", "ResetStarted", "Activation",
                   "ActivationGender", "ActivationHelp", "ActivationCounting", "CountingNotice",
                   "ActivationAnswer", "PlansPage", "PlanTier", "PlanLimit", "VoicesPage",
                   "VoiceOption", "SavedShelf", "SavedFolder", "FolderAnswer", "HistoryPage",
                   "HistoryItem", "VibesPage", "VibeRow",
                   # searchFAM: the speller, attachments, trending searches.
                   "SpellAnswer", "AttachedFile", "TrendingSearch",
                   # The loading screen: the give-up time and the server's note
                   # (§497, `genStatusNote`).
                   "ServerHealth", "ServerHealthEnvironment",
                   "ServerHealthCredentials",
                   # The front door: the waitlist's status page (decision 2),
                   # its friend search, the client's status and the Terms (§228).
                   "WaitlistMe", "WaitlistFamPerson", "WaitlistDetails", "WaitlistPeople",
                   "WaitlistPerson", "WaitlistAdded", "ClientStatus", "TermsState",
                   "ConsentTermsPage"):
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


def test_hey_fam_hears_what_the_web_hears():
    """`WakeListener`'s phrase is the web's `WAKE_PHRASE` (§158): the same
    greetings and the same spellings of the name."""
    web = (ROOT / "static" / "index.html").read_text()
    found = re.search(r"var WAKE_PHRASE = /\\b\(\?:([^)]*)\)\[\\s,\.!\]\+\(\?:([^)]*)\)", web)
    assert found, "WAKE_PHRASE moved"
    swift = (SWIFT / "WakeWord.swift").read_text()
    for word in found.group(1).split("|") + found.group(2).split("|"):
        plain = word.replace("'?", "['\\u{2019}]?")
        assert plain in swift, word


def test_the_front_door_decodes_what_the_server_sends(monkeypatch, tmp_path):
    """Sign up is the waitlist's join (decision 1): its body is the join's
    fields and its answer carries a token; the status page (decision 2),
    the client's status and the Terms block decode what the server sends."""
    import accounts as accounts_mod
    import preferences as prefs_mod
    import social as social_mod
    import waitlist as waitlist_mod
    acc = accounts_mod.AccountStore(str(tmp_path / "accounts.db"))
    monkeypatch.setattr(appmod, "ACCOUNTS", acc)
    monkeypatch.setattr(appmod, "WAITLIST", waitlist_mod.Waitlist(acc))
    monkeypatch.setattr(appmod, "SOCIAL", social_mod.SocialStore(str(tmp_path / "social.db")))
    monkeypatch.setattr(appmod, "PREFS", prefs_mod.PreferenceStore(str(tmp_path / "prefs.db")))
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    body = re.search(r"func joinWaitlist.*?\n    \}", _api(), re.S).group(0)
    sent = set(re.findall(r'"(\w+)":', body))
    assert sent <= set(appmod.WaitlistJoinRequest.model_fields), sorted(sent)
    assert {"email", "password", "referral_code", "accept_terms", "want_token"} <= sent
    # §571: the join asks for a password by the server's own minimum, the
    # same number and sentence the web's page uses.
    import accounts as accounts_mod
    auth_view = (SWIFT / "Views" / "AuthView.swift").read_text(encoding="utf-8")
    card = (SWIFT / "Views" / "FrontDoor.swift").read_text(encoding="utf-8")
    for swift in (auth_view, card):
        assert f"password.count >= {accounts_mod.MIN_PASSWORD}" in swift
        assert f"Use at least {accounts_mod.MIN_PASSWORD} characters for your password." in swift
    c = TestClient(appmod.app)
    headers = {"X-FAM-Client": "ios/1.0.0+2"}
    joined = c.post("/api/v1/waitlist/join", headers=headers, json={
        "email": "ios-join@example.com", "referral_code": "",
        "accept_terms": True, "want_token": True}).json()
    assert {"user_id", "email", "authenticated", "session_token"} <= set(joined), joined
    auth = {**headers, "Authorization": "Bearer " + joined["session_token"]}
    me = c.get("/api/v1/waitlist/me", headers=auth).json()
    assert _fields("WaitlistMe") <= set(me), sorted(_fields("WaitlistMe") - set(me))
    assert _fields("WaitlistDetails") <= set(me["details"])
    people = c.get("/api/v1/waitlist/people", params={"q": "zz"}, headers=auth).json()
    assert "people" in people
    status = c.get("/api/v1/client-status", headers=headers).json()
    assert _fields("ClientStatus") <= set(status), status
    terms = c.get("/api/v1/consent", headers=auth).json()["terms"]
    assert _fields("TermsState") <= set(terms), terms
    who = c.get("/api/v1/auth/me", headers=auth).json()
    assert {"waitlist", "home"} <= set(who)


def test_the_surveys_decode_what_the_server_sends(monkeypatch):
    """§560 on iOS: the offer, every survey's questions, and the answer the
    app sends (`Surveys.swift` `payload`) is one the server keeps."""
    import surveys as surveys_mod

    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    c = TestClient(appmod.app)
    headers = {"X-FAM-Client": "ios/1.0.0+2"}
    token = c.post("/api/v1/auth/signup", headers=headers, json={
        "email": "ios-survey@example.com", "password": "password12",
        "accept_terms": True, "want_token": True}).json()["session_token"]
    auth = {**headers, "Authorization": "Bearer " + token}
    offer = c.post("/api/v1/surveys/offer", headers=auth, json={"moment": "episode_end"}).json()
    assert _fields("SurveyOffer") <= set(offer)
    survey = offer["survey"]
    assert _fields("Survey") <= set(survey), sorted(_fields("Survey") - set(survey))
    # Every key a question carries is one the app decodes (the rest would be
    # silently lost), and every field the app requires is sent.
    question_fields = _fields("SurveyQuestion")
    for sid in surveys_mod.SURVEYS:
        for q in surveys_mod.definition(sid)["questions"]:
            assert set(q) <= question_fields, (sid, sorted(set(q) - question_fields))
            assert {"id", "kind", "text"} <= set(q)
            for o in q.get("options", []):
                assert _fields("SurveyOption") == set(o)
            if "show_if" in q:
                assert set(q["show_if"]) == {"question", "in"}
            # The decoder's snake-case conversion would rename a detail key
            # with an underscore; none has one.
            assert all("_" not in k for k in q.get("detail", {}))
    # What `payload` sends: option ids, a list, a number, a box.
    r = c.post("/api/v1/surveys/answer", headers=auth, json={
        "survey": "episode", "build": "ios/1.0.0+2",
        "answers": {"rating": {"pick": "meh"}, "off": {"pick": ["name"], "text": "Saquon"}},
        "episode": {"id": "abc", "title": "Why the Eagles lost"}})
    assert r.status_code == 200, r.text
    r = c.post("/api/v1/surveys/answer", headers=auth, json={
        "survey": "day3", "answers": {"routine": {"pick": 7}, "role": {"text": "Coach"}}})
    assert r.status_code == 200, r.text
    assert c.post("/api/v1/surveys/dismiss", headers=auth,
                  json={"survey": "wrap"}).status_code == 200
    # The app's calls are the routes it names.
    api = _api()
    for path in ("api/v1/surveys/offer", "api/v1/surveys/answer", "api/v1/surveys/dismiss"):
        assert f'"POST", "{path}"' in api, path


def test_the_notification_choices_decode_what_the_server_sends(monkeypatch):
    """§562 on iOS: `/api/v1/push` carries `prefs` for an account, POST
    `/api/v1/push/prefs` answers with them, and every key the app decodes is
    one the server sends. iPhone delivery (APNs) comes after 1.0, so the app
    says so wherever a choice only reaches a browser (`iPhoneLater`)."""
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    c = TestClient(appmod.app)
    headers = {"X-FAM-Client": "ios/1.0.0+2", "X-FAM-TZ": "America/New_York"}
    token = c.post("/api/v1/auth/signup", headers=headers, json={
        "email": "ios-push@example.com", "password": "password12",
        "accept_terms": True, "want_token": True}).json()["session_token"]
    auth = {**headers, "Authorization": "Bearer " + token}
    status = c.get("/api/v1/push", headers=auth).json()
    assert _fields("PushStatus") <= set(status) | {"subscribed"}, status
    assert _fields("PushPrefs") == set(status["prefs"]), status["prefs"]
    r = c.post("/api/v1/push/prefs", headers=auth, json={
        "daily": True, "daily_at": "07:00", "tz": "America/New_York"})
    assert r.status_code == 200, r.text
    answer = r.json()
    assert _fields("PushPrefsAnswer") <= set(answer)
    assert answer["prefs"]["daily_at"] == "07:00"
    assert answer["prefs"]["daily_source"] == "chosen"
    for name in ("breaking", "finals", "daily"):
        r = c.post("/api/v1/push/prefs", headers=auth, json={name: False})
        assert r.status_code == 200 and r.json()["prefs"][name] is False, r.text
    assert ("POST", "api/v1/push/prefs") in _calls()
    mixes = (SWIFT / "Mixes.swift").read_text()
    assert 'static let iPhoneLater = "Notifications on iPhone come in a later version."' in mixes
    settings = (SWIFT / "Views" / "SettingsView.swift").read_text()
    web = (ROOT / "static" / "index.html").read_text()
    times = re.search(r'\[("06:30"[^\]]*)\]\.forEach', web).group(1)
    assert "static let times = [" + times + "]" in settings
    assert "MixesModel.iPhoneLater" in settings


def test_mix_rows_show_todays_episode_title_570():
    """§570 on the iPhone: a mix row reads the server's `episode_title`
    (today's episode) with the topic under it, and the player opens on it;
    the topic's `title` is still what a whole-list write sends back."""
    import inspect
    assert '"episode_title"' in inspect.getsource(appmod._mix_episode_titles)
    api = (SWIFT / "API.swift").read_text()
    assert "let episodeTitle: String" in api
    assert "episodeTitle.isEmpty ? title : episodeTitle" in api
    assert '["query": query, "title": title]' in api
    view = (SWIFT / "Views" / "MixView.swift").read_text()
    assert "Text(item.shownTitle)" in view
    assert 'item.title + (who.isEmpty ? "" : " \\u{00B7} " + who)' in view
    model = (SWIFT / "AppModel.swift").read_text()
    assert "!item.episodeTitle.isEmpty ? item.episodeTitle" in model


# --- Build 7 (§584) ---------------------------------------------------------

def _swift(*parts: str) -> str:
    return (SWIFT.joinpath(*parts)).read_text()


def _listen_page() -> str:
    return (ROOT / "static" / "listen.html").read_text()


def test_a_shared_episode_plays_before_the_app_is_open_584(monkeypatch):
    """A `/s/` link opened by somebody signed out or on the waitlist plays on
    the listen page's own screen, not held until they are let in; its words
    are the page's, and its audio is the request the gate lets through."""
    links = _swift("AppLinks.swift")
    assert "if stage == .signedOut || stage == .waiting, case .episode = link {" in links
    assert "openSharedListenIfHeld()" in links
    assert "api.sharedEpisode(id)" in links and "api.shareOpened(id)" in links
    # Exactly the shared length (1.5 stays 1.5), the share surface, nothing else.
    assert ('api.audio(query: share.question, minutes: share.minutes, surface: "share")'
            in links)
    root = _swift("FAMApp.swift")
    assert "SharedListenLayer()" in root and "model.stageSettled()" in root
    assert "model.askSignUp" in _swift("Views", "FrontDoor.swift")
    view = _swift("Views", "SharedListenView.swift")
    page = _listen_page()
    for words in ["Shared with you", "A FAM episode", "Nothing to play",
                  "This link has expired", " min listen", "Want to hear more?",
                  "This is one episode. FAM makes them about anything you ask.",
                  "Ask your own question", "Browse episodes",
                  "FAM makes an episode about anything you ask.",
                  "Ask FAM anything of your own and it will make you an episode."]:
        assert words in page, words
        assert words in view, words
    assert "Be a part of the conversation with FAM &rarr;" in page
    assert "Be a part of the conversation with FAM \\u{2192}" in view
    for words in ["Starting the episode", "That", "s the episode.",
                  "That episode could not be played just now."]:
        assert words in page and words in links, words
    assert "FAMAPI.minutes(share.minutes)" in view
    # The request the app builds for that tap passes the waitlist's gate.
    body = re.search(r"func audio\(query.*?\n    \}", _api(), re.S).group(0)
    assert 'URLQueryItem(name: "fmt", value: "opus")' in body
    monkeypatch.setattr(appmod.SHARES, "is_shared",
                        lambda q, minutes: q == "why is the sky blue" and minutes == "1.5")
    values = {"q": "why is the sky blue", "minutes": "1.5", "fmt": "opus",
              "link": "1", "surface": "share"}
    request = Request({"type": "http", "method": "GET", "path": "/api/audio",
                       "query_string": urlencode(values).encode(), "headers": []})
    assert appmod._shared_episode_request(request)
    # Swift's `FAMAPI.minutes(1.5)` is "1.5", never "1".
    assert "value == value.rounded() ? String(Int(value)) : String(value)" in _api()
    read = Request({"type": "http", "method": "GET", "path": "/api/share/abc123",
                    "query_string": b"", "headers": []})
    assert appmod._shared_episode_request(read)


def test_settings_password_note_is_the_webs_584():
    """Settings' note under the account rows is `passwordSettingsNote()`:
    it depends on `has_password` and on `/api/auth/reset`'s `available`."""
    web = (ROOT / "static" / "index.html").read_text()
    settings = _swift("Views", "SettingsView.swift")
    for words in ["Set a password so you can sign back in on any device",
                  "; password reset isn\\u2019t available yet. ",
                  "Password reset isn\\u2019t available yet, so keep your password somewhere safe. "]:
        assert words in web, words
        assert words.replace("\\u2019", "\\u{2019}") in settings, words
    assert "resetReady = (try? await model.api.resetStatus())?.available ?? false" in settings
    assert "passwordNote(hasPassword: you.account?.hasPassword, resetReady: resetReady)" in settings
    assert 'fetch("/api/auth/reset")' in web


def test_share_targets_are_keyed_as_the_server_keys_them_584():
    """`.convertFromSnakeCase` may rewrite a dictionary's keys; the share
    targets are put back to the server's snake_case wherever they are read,
    so `instagram_story` / `snapchat_story` reach the sheet's order and
    hand-off either way."""
    import sharing
    api = _api()
    assert "static func serverKeyed<Value>(" in api and "static func snakeKey(" in api
    assert api.count("targets = ShareTarget.serverKeyed(") == 2
    social = _swift("Views", "SocialShare.swift")
    for key in ("instagram_story", "snapchat_story"):
        assert key in inspect.getsource(sharing)
        assert f'"{key}"' in social

    def snake(key: str) -> str:  # SnakeKey, as written in Swift
        return re.sub(r"(?<!^)([A-Z])", r"_\1", key).lower()

    assert snake("instagramStory") == "instagram_story"
    assert snake("instagram_story") == "instagram_story"
    assert snake("sms") == "sms"


def test_every_server_keyed_dictionary_is_put_back_to_snake_case_584():
    """Audit 4: survey `detail` and the catalogue's `tag_parent` / `tag_labels`
    are dictionaries keyed by server ids. A future id with an underscore
    (`college_football`) must not come back camel-cased, so each goes
    through the same `serverKeyed` as the share targets."""
    api = _api()
    assert "static func serverKeyed<Value>(" in api
    assert "detail = ShareTarget.serverKeyed(" in api
    assert "tagParent = ShareTarget.serverKeyed(" in api
    assert "tagLabels = ShareTarget.serverKeyed(" in api
    # Every [String: ...] the app decodes is one of the known, normalised ones.
    decoded = re.findall(r"^    let (\w+): \[String: (\w+)\]\??", api, re.M)
    normalised = {"targets", "detail", "tagParent", "tagLabels"}
    assert {name for name, _ in decoded} == normalised
    for name, _ in decoded:
        assert name in normalised or re.search(r"%s = ShareTarget\.serverKeyed\(" % name, api), name


def test_staging_ribbon_and_demo_badge_584():
    """A staging or zero-spend server's ribbon on every screen and the
    player's "not your episode" badge on a server with no working key."""
    web = (ROOT / "static" / "index.html").read_text()
    assert '(env.name === "staging" ? "Staging" : "Zero spend")' in web
    assert ">not your episode</span>" in web
    api = _api()
    assert '(environment?.name == "staging" ? "Staging" : "Zero spend") + " \\u{00B7} " + client' in api
    assert 'var demoMode: Bool { mode == "demo" || credentials?.state == "rejected" }' in api
    model = _swift("AppModel.swift")
    assert "envRibbon = health.ribbon(client: FAMAPI.client)" in model
    assert "demoMode = health.demoMode" in model
    ribbon = _swift("Views", "EnvRibbon.swift")
    assert 'Text("not your episode")' in ribbon
    assert "EnvRibbonWindow.show(words, dark:" in _swift("FAMApp.swift")
    assert "if model.demoMode {" in _swift("Views", "PlayerView.swift")
    health = TestClient(appmod.app).get("/api/v1/health").json()
    assert "mode" in health and "environment" in health


def test_an_empty_search_plays_the_demo_like_the_web_584():
    """Return in an empty box: the web's `generate("spacex")`, its title as
    the question, at the search page's Length, on searchFAM."""
    web = (ROOT / "static" / "index.html").read_text()
    assert 'if(!typed){ sendSearch("", ""); return; }' in web
    assert 'generate("spacex");' in web
    assert "spacex:{title:\"SpaceX Starship: This Week's Test Flight\"" in web
    model = _swift("AppModel.swift")
    assert "static let demoQuestion = \"SpaceX Starship: This Week's Test Flight\"" in model
    assert "EpisodeKey(query: Self.demoQuestion, minutes: minutes)" in model
    search = _swift("Views", "SearchView.swift")
    assert "guard !typed.isEmpty || !attach.isEmpty else { return }" not in search
