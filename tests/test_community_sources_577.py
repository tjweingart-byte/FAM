"""Reddit's and X's APIs, and groups a person sends to Egan (§577).

Pinned: the APIs are read-only and only asked for communities; a source that
fails falls back to the web search; a group a person sends is looked up,
given its public contact route, read by Egan and kept to contact whatever
its fit; nothing about a person reaches Egan.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as appmod  # noqa: E402
import communities as C  # noqa: E402
import community_sources as CS  # noqa: E402


# ------------------------------------------------------------- the clients

@pytest.fixture
def http(monkeypatch):
    """httpx with a fake network: `routes[(method, path)] = (status, json)`."""
    routes, seen = {}, []

    def handler(request):
        seen.append(request)
        status, body = routes.get((request.method, request.url.path), (404, {}))
        return httpx.Response(status, json=body)

    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return routes, seen


@pytest.fixture
def keys(monkeypatch):
    for name, value in (("REDDIT_CLIENT_ID", "id"), ("REDDIT_CLIENT_SECRET", "secret"),
                        ("X_BEARER_TOKEN", "xtoken")):
        monkeypatch.setenv(name, value)
    import credentials

    if hasattr(credentials, "reset"):
        credentials.reset()
    yield
    if hasattr(credentials, "reset"):
        credentials.reset()


def test_no_key_says_so():
    assert CS.reddit_available() == (False, "REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET are not set")
    assert CS.x_available()[0] is False
    with pytest.raises(CS.SourceError):
        CS.reddit_search("eagles")


def test_reddit_search_is_app_only_and_reads_subreddits(http, keys):
    routes, seen = http
    routes[("POST", "/api/v1/access_token")] = (200, {"access_token": "tok", "expires_in": 3600})
    routes[("GET", "/subreddits/search")] = (200, {"data": {"children": [
        {"data": {"display_name": "eagles", "title": "Philadelphia Eagles", "subscribers": 900000,
                  "public_description": "The Eagles.", "subreddit_type": "public", "over18": False}},
        {"data": {"display_name": "nsfwthing", "over18": True}}]}})
    found = CS.reddit_search("Philadelphia Eagles")
    assert [(f.ident, f.members, f.adult) for f in found] == [("eagles", 900000, False), ("nsfwthing", 0, True)]
    token, search = seen
    assert b"grant_type=client_credentials" in token.content
    assert search.headers["Authorization"] == "Bearer tok"
    assert "fam-outreach" in search.headers["User-Agent"] and "@" not in search.headers["User-Agent"]
    assert search.url.params["include_over_18"] == "false"
    # The token is kept: a second search asks for no new one.
    CS.reddit_search("Eagles")
    assert sum(r.url.path == "/api/v1/access_token" for r in seen) == 1


def test_x_reads_communities_and_says_when_refused(http, keys):
    routes, seen = http
    routes[("GET", "/2/communities/search")] = (200, {"data": [
        {"id": "1493446837214187523", "name": "Eagles Talk", "description": "Birds.",
         "member_count": 5200, "access": "Public"}, {"id": "nope"}]})
    found = CS.x_search("Eagles")
    assert [(f.ident, f.name, f.members, f.privacy) for f in found] == \
        [("1493446837214187523", "Eagles Talk", 5200, "public")]
    assert seen[0].headers["Authorization"] == "Bearer xtoken"
    routes[("GET", "/2/communities/123456")] = (403, {})
    with pytest.raises(CS.SourceError, match="refused"):
        CS.x_about("123456")


def test_the_clients_can_read_and_nothing_else():
    src = (ROOT / "community_sources.py").read_text()
    # One POST: Reddit's token. No posting, voting, messaging or following.
    assert len(re.findall(r"\.post\(", src)) == 1 and "REDDIT_TOKEN_URL" in src
    for word in ("/api/submit", "/api/comment", "/api/compose", "/2/tweets", "/2/dm",
                 "about/moderators", "/user/", "/2/users"):
        assert word not in src, word


# ------------------------------------------------------------- a run

def _held(tmp_path):
    return C.Store(str(tmp_path / "c.db"))


async def _reader(lead):
    lead.fit, lead.pitches, lead.message = 4, C.code_pitches(lead), "hi"
    return lead, 0.0


def test_a_run_uses_reddits_api_for_subreddits_and_never_an_adult_one(tmp_path, monkeypatch):
    monkeypatch.setattr(CS, "reddit_available", lambda: (True, "Reddit's API"))
    monkeypatch.setattr(CS, "reddit_search", lambda q: [
        CS.Found("reddit", "eagles", "Philadelphia Eagles", "Birds.", 900000, "public",
                 source_url="https://www.reddit.com/r/eagles"),
        CS.Found("reddit", "adultbirds", "x", adult=True)])
    held = _held(tmp_path)
    report = asyncio.run(C.run(None, ["Eagles"], ["reddit"], reader=_reader, held=held))
    assert report["kept"] == 1
    lead = held.get("reddit:eagles")
    assert lead["members"] == 900000 and lead["looked_up_with"] == "Reddit's API"
    assert lead["contact_url"].startswith("https://www.reddit.com/message/compose")


def test_an_api_that_fails_falls_back_to_the_web(tmp_path, monkeypatch):
    def broken(q):
        raise CS.SourceError("rate limited")

    monkeypatch.setattr(CS, "x_available", lambda: (True, "X's API"))
    monkeypatch.setattr(CS, "x_search", broken)
    monkeypatch.setattr(C, "backends", lambda: ["parallel"])
    monkeypatch.setattr(C, "_search_blocking", lambda q, p: ([SimpleNamespace(
        url="https://x.com/i/communities/123456789", title="Eagles | X", highlights=["Birds"])],
        0.001, "parallel"))
    held = _held(tmp_path)
    report = asyncio.run(C.run(None, ["Eagles"], ["x"], reader=_reader, held=held))
    assert report["kept"] == 1 and not report["failed"]
    assert held.get("x:123456789")["looked_up_with"] == "a web search (parallel)"


def test_with_only_an_api_key_the_page_can_search():
    import community_sources

    ok, _why = C.available()
    assert ok is False
    original = community_sources.reddit_available
    community_sources.reddit_available = lambda: (True, "Reddit's API")
    try:
        ok, why = C.available()
        assert ok and "Reddit's API" in why
    finally:
        community_sources.reddit_available = original


# ------------------------------------------------------------- sent to Egan

def test_a_link_this_page_cannot_read_says_what_it_can(tmp_path):
    with pytest.raises(C.NotACommunity, match="subreddit"):
        asyncio.run(C.lookup(None, "https://example.com/forum", held=_held(tmp_path)))


def test_a_sent_subreddit_is_read_from_the_api_and_kept_whatever_its_fit(tmp_path, monkeypatch):
    monkeypatch.setattr(CS, "reddit_available", lambda: (True, "Reddit's API"))
    monkeypatch.setattr(CS, "reddit_about", lambda name: CS.Found(
        "reddit", name, "Running in Austin", "Weekly group runs.", 12000, "public",
        source_url=f"https://www.reddit.com/r/{name}"))
    seen = []

    async def low(lead):
        seen.append(C.material(lead))
        lead.fit, lead.pitches, lead.message = 1, C.code_pitches(lead), "hi"
        return lead, 0.0

    held = _held(tmp_path)
    out = asyncio.run(C.lookup(None, "https://www.reddit.com/r/AustinRunning/", "",
                               "Mod Jane Roe said yes at a meetup, call 512-555-0199",
                               reader=low, held=held))
    lead = out["lead"]
    assert out["already"] is False and lead["status"] == C.NEW and lead["fit"] == 1
    assert lead["source"] == C.SENT and lead["subject"] == "Running in Austin"
    assert lead["contact_url"] == "https://www.reddit.com/message/compose?to=%2Fr%2Faustinrunning"
    assert "555" not in lead["note"]
    assert "Sent to Egan" in lead["reason"]
    # The note reaches Egan, without the number; it counts as one search.
    assert "FAM's team noted" in " ".join(t for _tag, t in seen[0])
    assert "555" not in " ".join(t for _tag, t in seen[0])
    assert held.searches_since(0) == 1


def test_a_sent_facebook_group_is_read_from_the_web(tmp_path):
    async def searcher(query, platform):
        assert query == "www.facebook.com/groups/eaglesfans" and platform == "facebook"
        return [SimpleNamespace(url="https://www.facebook.com/groups/eaglesfans",
                                title="Eagles Fans Nation | Facebook",
                                highlights=["Public group. 48K members. Contact the admins at "
                                            "hello@eaglesfans.org. Admin: Maria Lopez."])], 0.001, "parallel"

    out = asyncio.run(C.lookup(None, "https://facebook.com/groups/eaglesfans?ref=share", "Eagles",
                               searcher=searcher, reader=_reader, held=_held(tmp_path)))
    lead = out["lead"]
    assert lead["name"] == "Eagles Fans Nation" and lead["members"] == 48000
    assert lead["public_contact"] == ["hello@eaglesfans.org"] and lead["admin_hint"] == ["Maria Lopez"]
    assert lead["contact_url"].endswith("/members/admins")


def test_a_group_nothing_is_found_about_is_still_read_by_its_address(tmp_path):
    async def nothing(query, platform):
        return [], 0.001, "parallel"

    out = asyncio.run(C.lookup(None, "discord.gg/birdgang", "Eagles", searcher=nothing,
                               reader=_reader, held=_held(tmp_path)))
    assert out["lead"]["status"] == C.NEW
    assert "found nothing" in out["lead"]["looked_up_with"]


def test_a_group_fam_does_not_pitch_is_refused_with_why(tmp_path):
    async def grief(query, platform):
        return [SimpleNamespace(url="https://www.facebook.com/groups/loss", title="Grief support",
                                highlights=["A grief support group."])], 0.0, "parallel"

    with pytest.raises(ValueError, match="grief"):
        asyncio.run(C.lookup(None, "https://www.facebook.com/groups/loss", searcher=grief,
                             reader=_reader, held=_held(tmp_path)))


def test_one_already_on_the_page_is_returned_and_a_set_aside_one_read_again(tmp_path):
    held = _held(tmp_path)
    base = C.Lead("reddit", "eagles", "Eagles", C.community_url("reddit", "eagles"),
                  C.contact_url("reddit", "eagles"), "Eagles")
    held.add(base, 1.0)

    async def fail(query, platform):
        raise AssertionError("not looked up again")

    out = asyncio.run(C.lookup(None, "reddit.com/r/eagles", searcher=fail, held=held))
    assert out["already"] is True
    held.move("reddit:eagles", C.SET_ASIDE, 2.0, notes="mine")

    async def empty(query, platform):
        return [], 0.0, "parallel"

    out = asyncio.run(C.lookup(None, "reddit.com/r/eagles", searcher=empty, reader=_reader, held=held))
    assert out["already"] is False and out["lead"]["status"] == C.NEW
    assert out["lead"]["notes"] == "mine"


def test_the_daily_ceiling_holds_for_sent_groups_too(tmp_path, monkeypatch):
    import config
    import dataclasses

    monkeypatch.setattr(config, "settings",
                        dataclasses.replace(config.settings, communities_daily_searches=0))
    with pytest.raises(LookupError):
        asyncio.run(C.lookup(None, "reddit.com/r/eagles", held=_held(tmp_path)))


def test_the_send_endpoint(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_require_admin", lambda request: None)
    C.reset(_held(tmp_path))
    client = TestClient(appmod.app)
    r = client.post("/api/admin/groups/send", json={"url": "https://example.com/x"})
    assert r.status_code == 422 and "subreddit" in r.json()["error"]

    async def fake(cache, url, subject, note):
        return {"lead": {"id": "reddit:eagles", "status": "new"}, "already": False}

    monkeypatch.setattr(C, "lookup", fake)
    r = client.post("/api/admin/groups/send", json={"url": "https://reddit.com/r/eagles"})
    assert r.status_code == 200 and r.json()["lead"]["id"] == "reddit:eagles"
