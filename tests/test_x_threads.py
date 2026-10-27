"""Conversations on X, found from FAM's own episodes (§321, `/admin/x`).

The playbook's Find step, done by code: start from episodes FAM already has,
look for the threads they answer, and leave a short list of cards with a
drafted reply. A person approves and posts in X; FAM never posts.
"""
from __future__ import annotations

import asyncio
import calendar
import os
import pathlib
import re
import time
from urllib.parse import parse_qs, urlparse

import pytest

import x_threads as X

NOW = 1_800_000_000.0
ROOT = pathlib.Path(__file__).resolve().parent.parent


class FakeCache:
    def __init__(self, rows):
        self.rows = rows

    def recent(self, limit=40, exclude_author="", origin="", keys=()):
        rows = [r for r in self.rows if not keys or r["key"] in keys]
        return rows[:limit]


EPISODE = {"key": "k-eagles", "query": "Can the Eagles defence hold up without their starters?",
           "title": "Why the Philadelphia Eagles Defence Is Thin", "minutes": 3,
           "plays": 12, "explicit": False}


def _post(pid="1", text="Does anyone think the Philadelphia Eagles defence can survive this?",
          author="fan", followers=5000, replies=6, likes=40, age_h=1.0, **extra):
    return X.Post(id=pid, text=text, created_at=NOW - age_h * 3600, author=author,
                  author_name=author.title(), followers=followers, likes=likes,
                  replies=replies, reposts=3, lang="en", **extra)


def _seed():
    return X.Seed(EPISODE["key"], EPISODE["query"], EPISODE["title"], 3,
                  ("Philadelphia Eagles",), "cached", 12)


# --- seeds and the search ----------------------------------------------------


def test_a_title_names_what_to_search_for():
    assert X.name_runs("Why the Philadelphia Eagles Defence Is Thin")[0] == \
        "Philadelphia Eagles Defence"
    # A title-cased title is joined by the names its question typed.
    assert X.names_for(EPISODE["title"], EPISODE["query"]) == (
        "Philadelphia Eagles Defence", "Eagles")
    assert X.name_runs("how the Fed sets rates in Washington") == ("Fed", "Washington")
    assert X.name_runs("nothing named here") == ()


def test_the_search_asks_for_conversations_being_started():
    q = X.x_query(["Jalen Hurts", "Eagles"])
    # With no subject words to go beside it, "Eagles" alone is not asked for.
    assert q.startswith('"Jalen Hurts" lang:en')
    assert X.x_query(["Eagles"]) == ""
    for part in ("lang:en", "-is:retweet", "-is:reply", "-is:nullcast"):
        assert part in q
    assert X.x_query([]) == ""


def test_seeds_are_kept_episodes_trending_first():
    class Story:
        title = "Storm hits Florida coast"
        anchors = ("Florida",)
    cache = FakeCache([EPISODE, {"key": "k-fl", "query": "How bad is the Florida storm?",
                                 "title": "The Florida Storm", "minutes": 1, "plays": 0}])
    out = X.seeds(cache, trending=[(Story(), "k-fl"), (Story(), "gone")])
    assert [s.key for s in out] == ["k-fl", "k-eagles"]
    assert out[0].origin == "trending" and out[0].names == ("Florida",)


def test_an_explicit_episode_is_not_offered():
    cache = FakeCache([dict(EPISODE, explicit=True)])
    assert X.seeds(cache) == []


def test_xs_response_is_read_with_its_authors():
    payload = {"data": [{"id": "77", "text": "Why is it?", "author_id": "9",
                         "created_at": "2027-01-15T08:00:00.000Z", "lang": "en",
                         "public_metrics": {"like_count": 4, "reply_count": 2,
                                            "retweet_count": 1, "quote_count": 0}}],
               "includes": {"users": [{"id": "9", "username": "amy", "name": "Amy",
                                       "public_metrics": {"followers_count": 900}}]}}
    [post] = X.parse(payload)
    assert (post.id, post.author, post.followers, post.replies) == ("77", "amy", 900, 2)
    # Read as UTC, whatever the machine's zone.
    assert post.created_at == calendar.timegm((2027, 1, 15, 8, 0, 0))
    assert post.url == "https://x.com/amy/status/77"


# --- choosing ----------------------------------------------------------------


@pytest.mark.parametrize("post,why", [
    (_post(is_reply=True), "a reply"),
    (_post(age_h=9), "older than the window"),
    (_post(followers=100), "too few followers"),
    (_post(followers=5_000_000), "too big"),
    (_post(replies=400), "too many replies"),
    (_post(verified_type="business"), "an organisation"),
    (_post(text="RIP to a legend, does anyone remember the Eagles in 1980?"), "does not walk into"),
    (_post(text="The Eagles play on Sunday."), "neither asks nor takes a side"),
])
def test_threads_fam_does_not_join(post, why):
    assert why in X.screen(post, NOW, window_hours=6)


def test_one_author_a_week():
    assert "replied to this week" in X.screen(_post(author="Fan"), NOW, window_hours=6,
                                         replied_to=["fan"])


def test_a_good_thread_passes_and_says_why_in_words():
    [cand] = X.shortlist([(_post(), _seed())], NOW, window_hours=6)
    assert cand.kind in (X.THREAD_TO_EPISODE, X.QUESTION_ONLY)
    assert "replies so far" in cand.reason and "interactions in" in cand.reason
    assert 0 < cand.score <= 1


def test_a_post_that_is_not_about_the_episode_gets_no_card():
    """The owner (10.27): every card must be about the post it answers."""
    post = _post(text="Would you trade having a car for a walkable city?")
    assert X.shortlist([(post, _seed())], NOW, window_hours=6) == []


def test_a_shared_word_is_not_a_shared_subject():
    """Eagles the birds are not the Philadelphia Eagles' defence."""
    seed = X.Seed("k", "Can the Eagles defence hold up?", "The Eagles defence",
                  3, ("Eagles",))
    birds = _post(text="Anyone else seen the bald eagles nesting by the river?")
    team = _post(text="Does anyone think the Eagles defence survives December?")
    assert "nothing else" in X.on_subject(birds, seed)
    assert X.on_subject(team, seed) == ""
    # A name of two words is specific enough alone.
    assert X.on_subject(_post(text="Why is Jalen Hurts so calm?"),
                        X.Seed("k", "q", "t", 1, ("Jalen Hurts",))) == ""


def test_man_united_posts_never_answer_a_united_airlines_episode():
    """The cards the owner sent back (10.27): a Trending episode on a
    diverted United flight was offered under two Manchester United posts."""
    seed = X.Seed("k", "Why was a United flight diverted over a threat?",
                  "United flight diverted over threat", 1, ("United",), "trending")
    carrick = _post(text="Man United will stick with Michael Carrick despite defeats "
                         "to Bournemouth and Como. He's expected to lead United against "
                         "Chelsea on Saturday. United fans, right decision or mistake?")
    howe = _post(text="MAN UNITED MANAGER UPDATE! Eddie Howe is reportedly a leading "
                      "candidate to replace Michael Carrick. Would Howe be the right "
                      "man for United?")
    flight = _post(text="Anyone know why the United flight got diverted? Real threat?")
    # X is never asked for "United" alone...
    assert seed.search().startswith("(United (")
    # ...and a post that only shares the word gets no card.
    assert X.on_subject(carrick, seed) and X.on_subject(howe, seed)
    assert X.shortlist([(carrick, seed), (howe, seed)], NOW, window_hours=6) == []
    assert X.on_subject(flight, seed) == ""


def test_a_one_word_name_is_searched_beside_the_episodes_own_words():
    seed = X.Seed("k", "Can the Eagles defence hold up without its starters?",
                  "The Eagles defence", 3, ("Jalen Hurts", "Eagles"))
    q = seed.search()
    assert q.startswith('("Jalen Hurts" OR (Eagles (')
    assert "defence" in q and "starters" in q


def _verdict_run(verdict, post=None):
    async def searcher(query, *, max_results, since):
        return [post or _post("1")]

    async def drafter(cand):
        return "A reply about the post?", "test-model", 0.001, verdict
    return asyncio.run(X.run(FakeCache([EPISODE]), now=NOW, searcher=searcher,
                             drafter=drafter))


def test_the_model_judging_a_post_off_subject_drops_the_card():
    report = _verdict_run(X.Verdict(same_subject=False, why="about birds"))
    assert report["cards"] == 0 and "about birds" in report["off_subject"][0]
    assert X.store().all() == []


def test_a_post_set_aside_is_neither_shown_nor_judged_again():
    judged = []

    async def searcher(query, *, max_results, since):
        return [_post("1")]

    async def drafter(cand):
        judged.append(cand.post.id)
        return "r", "test-model", 0.001, X.Verdict(same_subject=False, why="birds")
    for at in (NOW, NOW + 60):
        asyncio.run(X.run(FakeCache([EPISODE]), now=at, searcher=searcher, drafter=drafter))
    assert judged == ["1"]
    assert X.store().all() == [] and X.store().counts() == {X.SET_ASIDE: 1}


def test_words_that_name_no_subject_do_not_make_one():
    seed = X.Seed("k", "What happened with the Eagles today?", "The Eagles today",
                  1, ("Eagles",))
    assert "happened" not in X.context_words(seed) and "today" not in X.context_words(seed)
    assert X.on_subject(_post(text="Did anyone see the eagles today by the lake?"), seed)


def test_an_episode_that_does_not_answer_the_post_is_not_linked():
    report = _verdict_run(X.Verdict(episode_fits=False, why="a different game"))
    [card] = X.store().all()
    assert report["cards"] == 1 and card["kind"] == X.QUESTION_ONLY
    assert "no link" in card["reason"] and "a different game" in card["reason"]


def test_a_relevant_card_says_what_was_checked():
    _verdict_run(X.Verdict(why="both about the Eagles' pass defence"))
    [card] = X.store().all()
    assert card["kind"] != X.QUESTION_ONLY
    assert "Checked: both about the Eagles' pass defence" in card["reason"]


def test_one_card_per_author_best_first():
    found = [(_post("1", author="a", likes=5), _seed()),
             (_post("2", author="a", likes=500), _seed()),
             (_post("3", author="b"), _seed())]
    out = X.shortlist(found, NOW, window_hours=6)
    assert [c.post.author for c in out].count("a") == 1
    assert out == sorted(out, key=lambda c: -c.score)


# --- the reply ----------------------------------------------------------------


def test_the_compose_screen_is_a_reply_with_the_words_in_it():
    url = X.intent_url("12345", "Hello & welcome")
    parsed = urlparse(url)
    assert parsed.netloc == "x.com" and parsed.path == "/intent/post"
    qs = parse_qs(parsed.query)
    assert qs["in_reply_to"] == ["12345"] and qs["text"] == ["Hello & welcome"]


def test_a_reply_that_fits_is_posted_word_for_word_with_its_link():
    words = "w" * (X.POST_CHARS - X.LINK_CHARS - 1)
    assert X.compose(words, "https://familiarize.net/s/abc") == \
        words + " https://familiarize.net/s/abc"


def test_a_reply_too_long_for_x_is_refused_never_cut():
    """What reaches X is exactly what the person approved (review, §321)."""
    with pytest.raises(X.TooLong):
        X.compose("w" * (X.POST_CHARS - X.LINK_CHARS), "https://familiarize.net/s/abc")
    # X weighs CJK and emoji as two.
    assert X.x_length("abc") == 3 and X.x_length("日本") == 4
    with pytest.raises(X.TooLong):
        X.compose("日" * 141)


def test_a_draft_cut_without_a_space_still_fits():
    assert len(X.trim("x" * 300, 220)) == 220
    assert len(X.trim("word " * 100, 220)) <= 220


def test_with_no_model_the_reply_is_drafted_in_code(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEYS", raising=False)
    [cand] = X.shortlist([(_post(), _seed())], NOW, window_hours=6)
    reply, by, dollars = asyncio.run(X.draft(cand))
    assert by == "code" and dollars == 0.0 and reply


def test_fam_never_posts_to_x():
    """[no-social-posting]: the module reads X's search and nothing else -
    no write endpoint, no POST, no user token."""
    source = (ROOT / "x_threads.py").read_text()
    assert ".post(" not in source
    assert "/2/tweets\"" not in source and "/2/tweets'" not in source
    assert "oauth" not in source.lower()
    assert X.SEARCH_URL.endswith("/2/tweets/search/recent")


# --- a run ---------------------------------------------------------------------


def _run(cache, searcher, **kw):
    async def drafter(cand):
        return "A drafted reply?", "test-model", 0.001
    return asyncio.run(X.run(cache, now=NOW, searcher=searcher, drafter=drafter, **kw))


def test_a_run_leaves_cards_and_says_what_it_did():
    asked = []

    async def searcher(query, *, max_results, since):
        asked.append((query, max_results, since))
        return [_post("1", author="a"), _post("2", author="b", text="The Eagles defence is overrated, honestly.")]

    report = _run(FakeCache([EPISODE]), searcher)
    assert report["cards"] == 2 and report["read"] == 2
    assert asked[0][0].startswith('("Philadelphia Eagles Defence" OR (Eagles (')
    assert asked[0][2] == NOW - 6 * 3600
    cards = X.store().all()
    assert {c["post_id"] for c in cards} == {"1", "2"}
    assert all(c["draft"] == "A drafted reply?" and c["status"] == X.WAITING for c in cards)
    assert X.store().last_run()["cards"] == 2


def test_a_post_already_on_the_page_is_not_carded_twice():
    async def searcher(query, *, max_results, since):
        return [_post("1")]
    _run(FakeCache([EPISODE]), searcher)
    assert _run(FakeCache([EPISODE]), searcher)["cards"] == 0


def test_a_failed_search_is_reported_not_swallowed():
    async def searcher(query, *, max_results, since):
        raise RuntimeError("X refused the search (HTTP 429)")
    report = _run(FakeCache([EPISODE]), searcher)
    assert report["cards"] == 0 and "HTTP 429" in report["errors"][0]


def test_the_daily_read_ceiling_stops_a_run(monkeypatch):
    import dataclasses
    import config

    monkeypatch.setattr(config, "settings",
                        dataclasses.replace(config.settings, x_threads_daily_reads=5))

    async def searcher(query, *, max_results, since):
        raise AssertionError("asked past the ceiling")
    assert "ceiling" in _run(FakeCache([EPISODE]), searcher)["stopped"]


def test_without_a_token_a_run_says_so(monkeypatch):
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("X_BEARER_TOKENS", raising=False)
    report = asyncio.run(X.run(FakeCache([EPISODE]), now=NOW))
    assert "X_BEARER_TOKEN" in report["stopped"]


def test_nothing_to_search_for_is_said():
    async def searcher(query, *, max_results, since):
        return []
    assert "no kept episode" in _run(FakeCache([]), searcher)["stopped"]


def test_reads_are_counted_even_when_the_run_dies():
    """X bills each read as it returns; a run that fails afterwards must not
    leave them uncounted for the daily ceiling (review, §321)."""
    async def searcher(query, *, max_results, since):
        return [_post("1")]

    async def drafter(cand):
        raise RuntimeError("database is locked")

    with pytest.raises(RuntimeError):
        asyncio.run(X.run(FakeCache([EPISODE]), now=NOW, searcher=searcher,
                          drafter=drafter))
    assert X.store().reads_since(NOW - 1) == 1
    assert "failed" in X.store().last_run()["stopped"]


def test_an_author_with_a_card_waiting_is_not_carded_again():
    async def searcher(query, *, max_results, since):
        return [_post("1", author="amy")]
    _run(FakeCache([EPISODE]), searcher)

    async def again(query, *, max_results, since):
        return [_post("2", author="amy")]

    async def drafter(cand):
        return "r", "m", 0.0
    report = asyncio.run(X.run(FakeCache([EPISODE]), now=NOW + 60,
                               searcher=again, drafter=drafter))
    assert report["cards"] == 0


def test_the_window_never_asks_x_past_seven_days(monkeypatch):
    import importlib
    import config

    monkeypatch.setenv("X_THREADS_WINDOW_HOURS", "500")
    try:
        assert importlib.reload(config).settings.x_threads_window_hours == 168.0
    finally:
        monkeypatch.delenv("X_THREADS_WINDOW_HOURS")
        importlib.reload(config)


def test_old_cards_are_pruned_after_a_week():
    held = X.store()
    [cand] = X.shortlist([(_post(), _seed())], NOW, window_hours=6)
    held.add(cand, NOW - (X.KEEP_DAYS + 1) * 86400)
    held.prune(NOW)
    assert held.all() == []


# --- the endpoints -------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    import app as appmod
    from fastapi.testclient import TestClient

    monkeypatch.setattr(appmod, "ADMIN_TOKEN", "secret")
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    return TestClient(appmod.app)


ADMIN = {"X-Admin-Token": "secret"}


def _card(kind=None):
    [cand] = X.shortlist([(_post("4242"), _seed())], NOW, window_hours=6)
    cand.kind = kind or X.THREAD_TO_EPISODE
    cand.draft = "Depth is the real question here. Who steps up first?"
    X.store().add(cand, time.time())
    return cand


def test_the_x_endpoints_are_admin_only(client):
    assert client.get("/api/admin/x").status_code == 404
    assert client.post("/api/admin/x/run").status_code == 404
    assert client.post("/api/admin/x/decide",
                       json={"post_id": "1", "action": "skip"}).status_code == 404
    body = client.get("/api/admin/x", headers=ADMIN).json()
    assert body["cards"] == [] and "available" in body["health"]


def test_the_page_is_served_to_a_configured_admin(client):
    r = client.get("/admin/x")
    assert r.status_code == 200 and "Conversations to join" in r.text
    assert r.headers["x-robots-tag"] == "noindex"


def test_approve_hands_back_xs_reply_box_with_the_words_and_the_episode(client):
    _card()
    r = client.post("/api/admin/x/decide", headers=ADMIN, json={
        "post_id": "4242", "action": "approve", "text": "My edited reply. Thoughts?"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == X.APPROVED
    assert "/s/" in body["share_url"] and body["text"].endswith(body["share_url"])
    assert body["text"].startswith("My edited reply. Thoughts?")
    qs = parse_qs(urlparse(body["intent_url"]).query)
    assert qs["in_reply_to"] == ["4242"] and qs["text"] == [body["text"]]
    [card] = client.get("/api/admin/x?status=approved", headers=ADMIN).json()["cards"]
    assert card["posted"] == body["text"]


def test_approving_a_reply_too_long_for_x_is_refused(client):
    _card()
    r = client.post("/api/admin/x/decide", headers=ADMIN, json={
        "post_id": "4242", "action": "approve", "text": "w" * 270})
    body = r.json()
    assert r.status_code == 422 and "X allows 280" in (body.get("detail") or body.get("error"))
    [card] = client.get("/api/admin/x", headers=ADMIN).json()["cards"]
    assert card["status"] == X.WAITING


def test_a_question_only_card_carries_no_link(client):
    _card(X.QUESTION_ONLY)
    body = client.post("/api/admin/x/decide", headers=ADMIN, json={
        "post_id": "4242", "action": "approve"}).json()
    assert body["share_url"] == "" and "/s/" not in body["text"]


def test_listen_first_gives_the_same_link_approve_will(client):
    _card()
    heard = client.post("/api/admin/x/listen", headers=ADMIN, json={"post_id": "4242"}).json()
    body = client.post("/api/admin/x/decide", headers=ADMIN, json={
        "post_id": "4242", "action": "approve"}).json()
    assert heard["url"] == body["share_url"]


def test_skip_and_reopen(client):
    _card()
    assert client.post("/api/admin/x/decide", headers=ADMIN, json={
        "post_id": "4242", "action": "skip"}).json()["status"] == X.SKIPPED
    assert client.post("/api/admin/x/decide", headers=ADMIN, json={
        "post_id": "4242", "action": "reopen"}).json()["status"] == X.WAITING


def test_search_without_a_token_is_refused_in_words(client, monkeypatch):
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    r = client.post("/api/admin/x/run", headers=ADMIN)
    assert r.status_code == 409 and "X_BEARER_TOKEN" in r.json().get("detail", r.text)


def test_health_does_not_create_the_store(client):
    client.get("/api/admin/x", headers=ADMIN)
    assert not os.path.exists(os.environ["X_THREADS_DB"])


def test_the_admin_menu_links_the_page():
    assert 'href="/admin/x"' in (ROOT / "admin_ui" / "tracker.html").read_text()


# --- the review before merging (§326) ------------------------------------------


def test_two_of_a_trending_storys_names_together_are_on_subject():
    seed = X.Seed("k", "Did Trump and Zelensky agree a ceasefire?",
                  "Trump and Zelensky", 1, ("Trump", "Zelensky"), "trending")
    assert X.on_subject(_post(text="Trump meets Zelensky at the White House"), seed) == ""
    assert seed.search().startswith("((Trump Zelensky) OR")


def test_a_name_is_matched_as_a_whole_word():
    seed = X.Seed("k", "Is the Ford electric truck worth it?", "Ford's electric truck",
                  1, ("Ford",))
    assert "does not name" in X.on_subject(
        _post(text="I cannot afford an electric truck this year"), seed)
    assert X.on_subject(_post(text="Is #Ford winning the electric truck race?"), seed) == ""


def test_a_long_post_is_read_whole():
    [post] = X.parse({"data": [{"id": "9", "text": "Cut short…",
                                "note_tweet": {"text": "The whole long post."}}]})
    assert post.text == "The whole long post."


def test_a_set_aside_post_cannot_be_acted_on_by_its_id():
    [cand] = X.shortlist([(_post("77"), _seed())], NOW, window_hours=6)
    X.store().add(cand, NOW, status=X.SET_ASIDE)
    assert X.store().get("77") is None


def test_when_the_relevance_check_fails_nothing_is_linked(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    class Broken:
        class messages:
            @staticmethod
            async def create(**kw):
                raise RuntimeError("overloaded")
    [cand] = X.shortlist([(_post(), _seed())], NOW, window_hours=6)
    reply, by, _d, verdict = asyncio.run(X.judge_and_draft(cand, Broken()))
    assert by == "code" and verdict.episode_fits is False


def test_a_both_sides_reply_is_not_kept_for_one_side():
    seed = X.Seed("k-eagles", EPISODE["query"], EPISODE["title"], 3,
                  ("Philadelphia Eagles", "Eagles"))
    a = _post("1", author="a", text="The Eagles defence is overrated, honestly.")
    b = _post("2", author="b", text="Honestly the Eagles defence is underrated.")
    [first, *_] = X.shortlist([(a, seed), (b, seed)], NOW, window_hours=6)
    assert first.kind == X.BOTH_SIDES and first.other is not None
    X.apply_verdict(first, X.Verdict(other_same_subject=False, why="birds"))
    assert first.other is None and first.kind != X.BOTH_SIDES


def test_a_run_rewrites_a_two_post_reply_when_one_post_goes():
    async def searcher(query, *, max_results, since):
        return [_post("1", author="a", text="The Eagles defence is overrated, honestly."),
                _post("2", author="b", text="Honestly the Eagles defence is underrated.")]

    async def drafter(cand):
        return ("Both of you are half right.", "test-model", 0.0,
                X.Verdict(other_same_subject=cand.other is None, why="checked"))
    asyncio.run(X.run(FakeCache([EPISODE]), now=NOW, searcher=searcher, drafter=drafter))
    cards = X.store().all()
    paired = [c for c in cards if c["drafted_by"] == "code"]
    assert paired, "the pair was formed and then split"
    assert all(c["draft"] != "Both of you are half right." for c in paired)
    assert all(c["other"] is None for c in cards)
