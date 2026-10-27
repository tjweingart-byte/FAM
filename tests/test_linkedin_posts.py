"""Go Deeper on LinkedIn (§322, `/admin/linkedin`).

A LinkedIn post that teaches something gets a comment linking the kept
episode that goes deeper. Nothing reads LinkedIn: a person opens the search
links and pastes a post; a person approves and posts the comment.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import time
from urllib.parse import parse_qs, urlparse

import pytest

import linkedin_posts as L
import x_threads

ROOT = pathlib.Path(__file__).resolve().parent.parent
NOW = 1_800_000_000.0


class FakeCache:
    def __init__(self, rows):
        self.rows = rows

    def recent(self, limit=40, exclude_author="", origin="", keys=()):
        rows = [r for r in self.rows if not keys or r["key"] in keys]
        return rows[:limit]


EPISODE = {"key": "k-rates", "query": "How does the Federal Reserve decide interest rates?",
           "title": "How the Federal Reserve Decides Interest Rates", "minutes": 3,
           "plays": 4, "explicit": False}
OTHER = {"key": "k-eagles", "query": "Can the Eagles defence hold up?",
         "title": "Why the Philadelphia Eagles Defence Is Thin", "minutes": 2,
         "plays": 9, "explicit": False}
SWEARS = {"key": "k-e", "query": "How does the Federal Reserve decide interest rates, damn it?",
          "title": "The Federal Reserve and Interest Rates", "minutes": 1,
          "plays": 50, "explicit": True}

URL = ("https://www.linkedin.com/posts/jane-doe-4b2a1c_interest-rates-"
       "activity-7251234567890123456-AbCd?utm_source=share")
TEACHING = (
    "Here's how the Federal Reserve decides interest rates, and why most people "
    "get it wrong.\n\n1. The committee reads inflation, not the stock market.\n"
    "2. Interest rates move slowly on purpose.\n3. The Federal Reserve signals "
    "before it acts.\n\nThe lesson for founders: plan for the signal, not the move.")


def _drafter(comment="The part that matters is what the committee signals first."):
    async def drafter(text, episode):
        if not L.linked(episode):
            return "", "How does the Fed decide rates?", "test-model", 0.0
        return comment, "How does the Fed decide rates?", "test-model", 0.001
    return drafter


def _add(cache=None, **kw):
    kw.setdefault("url", URL)
    kw.setdefault("text", TEACHING)
    return asyncio.run(L.add(cache or FakeCache([OTHER, EPISODE]), now=NOW,
                             drafter=kw.pop("drafter", _drafter()), **kw))


# --- a post's address -----------------------------------------------------------


def test_a_post_address_gives_its_id_author_and_a_clean_link():
    post_id, clean, author = L.parse_url(URL)
    assert post_id == "activity:7251234567890123456"
    assert author == "jane-doe-4b2a1c" and "utm_source" not in clean
    assert L.name_from_slug(author) == "Jane Doe"
    post_id, clean, author = L.parse_url(
        "linkedin.com/feed/update/urn:li:activity:7251234567890123456/")
    assert post_id == "activity:7251234567890123456" and author == ""
    assert clean == "https://www.linkedin.com/feed/update/urn:li:activity:7251234567890123456/"


@pytest.mark.parametrize("url, said", [
    ("", "Paste"),
    ("https://lnkd.in/abc123", "short link"),
    ("https://x.com/a/status/1", "not a LinkedIn address"),
    ("https://evil-linkedin.com/posts/a_b-activity-7251234567890123456-x", "not a LinkedIn"),
    ("https://www.linkedin.com/in/jane-doe/", "not a post"),
])
def test_what_is_not_a_post_is_said(url, said):
    with pytest.raises(L.NotAPost, match=said):
        L.parse_url(url)


# --- finding: links a person opens -----------------------------------------------


def test_search_links_are_linkedins_own_search_from_fams_episodes():
    [link] = L.search_links(FakeCache([OTHER]))
    parts = urlparse(link["url"])
    assert parts.netloc == "www.linkedin.com" and parts.path == "/search/results/content/"
    query = parse_qs(parts.query)
    assert query["keywords"] == ['"Philadelphia Eagles Defence" OR Eagles']
    assert query["sortBy"] == ['"date_posted"']
    assert link["title"] == OTHER["title"]


def test_nothing_here_reads_or_writes_linkedin():
    """[no-social-posting]: no request leaves for LinkedIn, no credential."""
    source = (ROOT / "linkedin_posts.py").read_text()
    for forbidden in ("httpx", "requests", "urlopen", "oauth", "LINKEDIN_TOKEN",
                      "credentials.active(\"LINKEDIN"):
        assert forbidden not in source


# --- choosing ---------------------------------------------------------------------


@pytest.mark.parametrize("text, why", [
    ("Short thought.", "too short"),
    ("Thrilled to announce I've accepted a new position as head of growth at a "
     "company I admire. Here's how I got there and the lessons I learned along the way.",
     "announcement or a promotion"),
    ("We're hiring! Three open roles on the platform team. Here's how our "
     "interview works and the framework we use to decide, so apply now.",
     "announcement or a promotion"),
    ("My grandfather passed away this week. Here's what he taught me about "
     "running a business for forty years, the lessons I keep coming back to.",
     "does not walk into"),
    ("Spent the weekend at the lake with the family, the weather was lovely and "
     "everyone had a good time. Back to the office tomorrow morning with fresh eyes.",
     "does not read as teaching"),
])
def test_posts_that_do_not_teach_are_turned_away_in_words(text, why):
    assert why in L.screen_post(text)


def test_a_teaching_post_passes():
    assert L.screen_post(TEACHING) == ""
    assert L.teaches("Plain words\n- one\n- two\n- three") is True


def test_the_best_kept_episode_matches_never_an_explicit_one():
    found = L.episodes_for(FakeCache([OTHER, SWEARS, EPISODE]), TEACHING)
    assert found[0]["key"] == "k-rates" and L.linked(found[0])
    assert "k-e" not in [e["key"] for e in found]
    assert not L.linked(found[-1])


# --- a pasted post -------------------------------------------------------------------


def test_a_pasted_post_becomes_a_card_with_its_episode_and_reason():
    card = _add()
    assert card["post_id"] == "activity:7251234567890123456"
    assert card["author_name"] == "Jane Doe" and card["status"] == L.WAITING
    assert card["episode"]["key"] == "k-rates" and card["linked"]
    assert "reads as teaching" in card["reason"] and "goes deeper with" in card["reason"]
    assert card["draft"].startswith("The part that matters")


def test_a_refused_post_can_be_added_anyway_and_says_so():
    with pytest.raises(L.Refused, match="announcement"):
        _add(text=TEACHING + "\n\nWe're hiring, apply now.")
    card = _add(text=TEACHING + "\n\nWe're hiring, apply now.", anyway=True)
    assert card["screened"] and "added anyway" in card["reason"]


def test_the_words_must_be_pasted_too():
    with pytest.raises(L.Refused, match="FAM does not read LinkedIn"):
        _add(text="   ")


def test_one_author_a_week():
    _add()
    with pytest.raises(L.Refused, match="this author"):
        _add(url=URL.replace("7251234567890123456", "7251234567890999999"))


def test_without_its_subject_there_is_no_comment_only_a_question():
    card = _add(cache=FakeCache([OTHER]))
    assert not card["linked"] and card["draft"] == ""
    assert card["question"] and "no link yet" in card["reason"]


def test_match_again_finds_the_episode_once_it_is_kept():
    _add(cache=FakeCache([OTHER]))
    card = asyncio.run(L.rematch(FakeCache([OTHER, EPISODE]),
                                 "activity:7251234567890123456", drafter=_drafter()))
    assert card["linked"] and card["episode"]["key"] == "k-rates" and card["draft"]


def test_with_no_model_the_comment_is_drafted_in_code(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    found = L.episodes_for(FakeCache([EPISODE]), TEACHING)
    comment, question, by, dollars = asyncio.run(L.draft(TEACHING, found[0]))
    assert by == "code" and comment and dollars == 0.0
    assert asyncio.run(L.draft(TEACHING, None))[0] == ""


def test_the_comment_ends_with_the_go_deeper_line_and_keeps_paragraphs():
    episode = dict(EPISODE, match=0.9)
    text = L.compose("First point.\n\n\n\nSecond   point.", episode, "https://f.am/s/abc")
    assert text == ("First point.\n\nSecond point.\n\nGo Deeper: “How the Federal Reserve "
                    "Decides Interest Rates” (3 min) https://f.am/s/abc")
    with pytest.raises(L.TooLong, match="LinkedIn allows 1250"):
        L.compose("w" * 1240, episode, "https://f.am/s/abc")


def test_old_cards_are_pruned_after_a_week():
    _add()
    held = L.store()
    assert held.prune(NOW + 8 * 86400) == 1 and held.all() == []


# --- the endpoints -------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    import app as appmod
    from fastapi.testclient import TestClient

    monkeypatch.setattr(appmod, "ADMIN_TOKEN", "secret")
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", FakeCache([OTHER, EPISODE]))
    monkeypatch.setattr(x_threads, "trending_now", lambda: [])
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return TestClient(appmod.app)


ADMIN = {"X-Admin-Token": "secret"}
PID = "activity:7251234567890123456"


def _detail(r):
    body = r.json()
    return body.get("detail") or body.get("error") or ""


def test_the_endpoints_are_admin_only(client):
    assert client.get("/api/admin/linkedin").status_code == 404
    assert client.post("/api/admin/linkedin/add", json={"url": URL, "text": TEACHING}).status_code == 404
    assert client.post("/api/admin/linkedin/decide",
                       json={"post_id": PID, "action": "skip"}).status_code == 404
    body = client.get("/api/admin/linkedin", headers=ADMIN).json()
    assert body["cards"] == [] and body["comment_chars"] == L.COMMENT_CHARS
    assert [s["key"] for s in body["searches"]] == ["k-eagles", "k-rates"]


def test_the_page_is_served_to_a_configured_admin(client):
    r = client.get("/admin/linkedin")
    assert r.status_code == 200 and "Posts to go deeper on" in r.text
    assert r.headers["x-robots-tag"] == "noindex"


def test_paste_approve_hands_back_the_comment_and_the_post(client):
    r = client.post("/api/admin/linkedin/add", headers=ADMIN, json={"url": URL, "text": TEACHING})
    assert r.status_code == 200 and r.json()["card"]["drafted_by"] == "code"
    heard = client.post("/api/admin/linkedin/listen", headers=ADMIN, json={"post_id": PID}).json()
    r = client.post("/api/admin/linkedin/decide", headers=ADMIN, json={
        "post_id": PID, "action": "approve", "text": "My edited comment."})
    assert r.status_code == 200
    body = r.json()
    assert "/s/" in body["share_url"] and body["share_url"] == heard["url"]
    assert body["text"].startswith("My edited comment.\n\nGo Deeper: “How the Federal")
    assert body["text"].endswith(body["share_url"])
    assert body["post_url"].startswith("https://www.linkedin.com/posts/jane-doe-4b2a1c_")
    [card] = client.get("/api/admin/linkedin?status=approved", headers=ADMIN).json()["cards"]
    assert card["posted"] == body["text"]


def test_a_refusal_is_said_and_can_be_overruled(client):
    r = client.post("/api/admin/linkedin/add", headers=ADMIN,
                    json={"url": URL, "text": "Thrilled to announce our webinar! " * 5})
    assert r.status_code == 409 and "announcement" in _detail(r)
    r = client.post("/api/admin/linkedin/add", headers=ADMIN,
                    json={"url": URL, "text": "Thrilled to announce our webinar! " * 5, "anyway": True})
    assert r.status_code == 200
    bad = client.post("/api/admin/linkedin/add", headers=ADMIN,
                      json={"url": "https://lnkd.in/x", "text": TEACHING})
    assert bad.status_code == 422 and "short link" in _detail(bad)


def test_no_episode_no_approve(client, monkeypatch):
    import app as appmod

    monkeypatch.setattr(appmod, "SCRIPT_CACHE", FakeCache([OTHER]))
    client.post("/api/admin/linkedin/add", headers=ADMIN, json={"url": URL, "text": TEACHING})
    r = client.post("/api/admin/linkedin/decide", headers=ADMIN,
                    json={"post_id": PID, "action": "approve", "text": "Words."})
    assert r.status_code == 409 and "Match again" in _detail(r)
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", FakeCache([OTHER, EPISODE]))
    card = client.post("/api/admin/linkedin/match", headers=ADMIN, json={"post_id": PID}).json()["card"]
    assert card["linked"]


def test_a_comment_too_long_for_linkedin_is_refused(client):
    client.post("/api/admin/linkedin/add", headers=ADMIN, json={"url": URL, "text": TEACHING})
    r = client.post("/api/admin/linkedin/decide", headers=ADMIN, json={
        "post_id": PID, "action": "approve", "text": "w" * 1240})
    assert r.status_code == 422 and "LinkedIn allows" in _detail(r)
    [card] = client.get("/api/admin/linkedin", headers=ADMIN).json()["cards"]
    assert card["status"] == L.WAITING


def test_skip_and_reopen(client):
    client.post("/api/admin/linkedin/add", headers=ADMIN, json={"url": URL, "text": TEACHING})
    assert client.post("/api/admin/linkedin/decide", headers=ADMIN, json={
        "post_id": PID, "action": "skip"}).json()["status"] == L.SKIPPED
    assert client.post("/api/admin/linkedin/decide", headers=ADMIN, json={
        "post_id": PID, "action": "reopen"}).json()["status"] == L.WAITING


def test_the_page_does_not_create_the_store(client):
    client.get("/api/admin/linkedin", headers=ADMIN)
    assert not os.path.exists(os.environ["LINKEDIN_POSTS_DB"])


def test_the_admin_menu_links_the_page():
    assert 'href="/admin/linkedin"' in (ROOT / "admin_ui" / "tracker.html").read_text()
