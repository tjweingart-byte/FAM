"""The X agent's guardrails (§368, FAM_X_Marketing_Agent_Guardrails.pdf).

FAM is a participant in conversations already happening on X, not a bot
that distributes episodes: the original post anchors everything, a 1-5
relevance score with hard gates decides episode + comment / comment only /
skip, comments are conversation rather than advertisement, and nothing is
linked or approved before a person has checked it.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

import x_threads as X

NOW = 1_800_000_000.0

EPISODE = {"key": "k-eagles", "query": "Can the Eagles defence hold up without their starters?",
           "title": "Why the Philadelphia Eagles Defence Is Thin", "minutes": 3,
           "plays": 12, "explicit": False}


class FakeCache:
    def __init__(self, rows):
        self.rows = rows

    def recent(self, limit=40, exclude_author="", origin="", keys=()):
        return [r for r in self.rows if not keys or r["key"] in keys][:limit]


def _post(pid="1", text="Does anyone think the Philadelphia Eagles defence can survive this?",
          author="fan", replies=6, **extra):
    return X.Post(id=pid, text=text, created_at=NOW - 3600, author=author,
                  author_name=author.title(), followers=5000, likes=40,
                  replies=replies, reposts=3, lang="en", **extra)


def _run(verdict, *, replier=None, post=None):
    async def searcher(query, *, max_results, since):
        return [post or _post("1")]

    async def drafter(cand):
        return "Depth behind the starters is the real question. Who steps up?", \
            "test-model", 0.001, verdict
    return asyncio.run(X.run(FakeCache([EPISODE]), now=NOW, searcher=searcher,
                             drafter=drafter, replier=replier))


# --- the scale and its gates ---------------------------------------------------


def test_the_scale_is_the_briefs():
    assert sorted(X.RELEVANCE) == [1, 2, 3, 4, 5]
    assert X.COMMENT_RELEVANCE == 3 and X.EPISODE_RELEVANCE == 4
    # Code cannot apply the original-author test: never an episode on its word.
    assert X.CODE_RELEVANCE < X.EPISODE_RELEVANCE


@pytest.mark.parametrize("relevance", [1, 2])
def test_a_one_or_two_is_rejected(relevance):
    report = _run(X.Verdict(relevance=relevance, relevance_why="keywords only"))
    assert report["cards"] == 0
    assert f"relevance {relevance}/5" in report["off_subject"][0]
    [rejected] = X.store().all(status=X.SET_ASIDE)
    assert rejected["relevance"] == relevance and "keywords only" in rejected["relevance_why"]


def test_the_original_author_test_is_a_hard_gate_whatever_the_score():
    report = _run(X.Verdict(relevance=5, author_test=False,
                            relevance_why="a reply's tangent, not the post"))
    assert report["cards"] == 0
    assert "original author would not see why" in report["off_subject"][0]


def test_a_three_is_a_comment_only_never_an_episode():
    _run(X.Verdict(relevance=3, proposed={"title": "T", "scope": "", "query": "q"}))
    [card] = X.store().all()
    assert card["kind"] == X.QUESTION_ONLY
    assert card["recommendation"] == X.RECOMMEND_COMMENT
    assert card["proposed"] is None and "3/5" in card["reason"]


def test_a_four_with_a_kept_episode_is_episode_and_comment():
    _run(X.Verdict(relevance=4, relevance_why="answers the post's question"))
    [card] = X.store().all()
    assert card["kind"] != X.QUESTION_ONLY
    assert card["recommendation"] == X.RECOMMEND_EPISODE
    assert card["relevance_label"] == X.RELEVANCE[4]


def test_the_read_may_choose_a_comment_even_at_five():
    """Do not force episode links (§368 rule 5)."""
    _run(X.Verdict(relevance=5, recommendation=X.RECOMMEND_COMMENT))
    [card] = X.store().all()
    assert card["kind"] == X.QUESTION_ONLY and card["recommendation"] == X.RECOMMEND_COMMENT


def test_skip_is_an_outcome():
    report = _run(X.Verdict(relevance=4, recommendation=X.RECOMMEND_SKIP))
    assert report["cards"] == 0 and "skipping" in report["off_subject"][0]


def test_a_specific_episode_is_proposed_when_none_kept_answers():
    proposal = {"title": "Can the Eagles' Backups Hold the Pass Rush?",
                "scope": "Who replaces the injured starters and what changes.",
                "query": "Can the Eagles backups hold the pass rush"}
    _run(X.Verdict(relevance=5, episode_fits=False, proposed=proposal,
                   central="whether the defence survives the injuries"))
    [card] = X.store().all()
    # No link: the episode does not exist yet, so the reply cannot claim it.
    assert card["kind"] == X.QUESTION_ONLY
    assert card["proposed"] == proposal
    assert card["central"] == "whether the defence survives the injuries"


def test_with_no_model_a_card_is_a_comment_and_says_why(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEYS", raising=False)

    async def searcher(query, *, max_results, since):
        return [_post("1")]
    asyncio.run(X.run(FakeCache([EPISODE]), now=NOW, searcher=searcher))
    [card] = X.store().all()
    assert card["kind"] == X.QUESTION_ONLY and card["drafted_by"] == "code"
    assert card["relevance"] == X.CODE_RELEVANCE
    assert "not judged by a model" in card["relevance_why"]


def test_fives_come_before_fours_within_a_run():
    async def searcher(query, *, max_results, since):
        return [_post("1", author="a"), _post("2", author="b",
                text="Is the Philadelphia Eagles defence really this thin?")]

    async def drafter(cand):
        return "Who steps in for the starters?", "m", 0.0, X.Verdict(
            relevance=5 if cand.post.id == "2" else 4)
    asyncio.run(X.run(FakeCache([EPISODE]), now=NOW, searcher=searcher, drafter=drafter))
    assert [c["relevance"] for c in X.store().all()] == [5, 4]


# --- the model's answer --------------------------------------------------------


class _Reply:
    def __init__(self, body):
        self.content = [type("B", (), {"type": "text", "text": json.dumps(body)})()]
        self.usage = None


def _client(body):
    class C:
        class messages:
            @staticmethod
            async def create(**kw):
                C.seen = kw
                return _Reply(body)
    return C


def _cand():
    [cand] = X.shortlist([(_post(), X.Seed(EPISODE["key"], EPISODE["query"],
                                           EPISODE["title"], 3, ("Philadelphia Eagles",)))],
                         NOW, window_hours=6)
    return cand


BODY = {"subject": "the Eagles defence", "intent": "asking", "wants": "w",
        "same_subject": True, "choice": 0, "why": "same", "angle": "depth",
        "detail": "", "central": "can the defence survive", "author_would_see_it": True,
        "relevance": 5, "relevance_why": "the post's own question",
        "recommendation": "episode_comment", "proposed_title": "", "proposed_scope": "",
        "proposed_query": "", "other_same_subject": False,
        "reply": "The defence question is really about depth behind the starters. "
                 "Who do you trust to step in?"}


def test_the_model_answers_the_guardrails_before_the_reply(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    props = list(X.DRAFT_SCHEMA["properties"])
    assert props.index("author_would_see_it") < props.index("reply")
    assert props.index("relevance") < props.index("reply")
    reply, by, _d, verdict = asyncio.run(X.judge_and_draft(_cand(), _client(BODY)))
    assert by != "code" and verdict.relevance == 5 and verdict.author_test
    assert verdict.central == "can the defence survive"


def test_an_ad_is_sent_back_to_the_codes_words(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    body = dict(BODY, reply="AI is changing everything! Check out our FAM episode.")
    reply, by, _d, verdict = asyncio.run(X.judge_and_draft(_cand(), _client(body)))
    assert by == "code" and "sounded like an ad" in verdict.replaced


@pytest.mark.parametrize("words", [
    "Great question! Check it out.", "Don't miss this one", "#AI is huge",
    "Love this take", "Listen now 🔥"])
def test_what_an_advertisement_sounds_like(words):
    assert X.promotional(words)


@pytest.mark.parametrize("words", [
    "One distinction is whether AI replaces a doctor's judgment or improves the "
    "information used in diagnosis. Where do you think that line will be drawn?",
    "We explored this in a short episode on the defence's depth."])
def test_conversation_is_not_an_advertisement(words):
    assert X.promotional(words) == []


def test_a_reply_takes_up_something_the_post_said():
    post = "I don't think AI will replace doctors, but it'll change how we diagnose diseases."
    assert X.anchored("Diagnosis is where the change lands first.", post) or \
        X.anchored("Diagnose is the verb that matters here.", post)
    assert not X.anchored("Interest rates rose again.", post)


def test_a_proposal_only_when_no_episode_was_chosen(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    body = dict(BODY, proposed_title="T", proposed_query="q")
    _r, _b, _d, verdict = asyncio.run(X.judge_and_draft(_cand(), _client(body)))
    assert verdict.proposed is None
    body = dict(body, choice=-1)
    _r, _b, _d, verdict = asyncio.run(X.judge_and_draft(_cand(), _client(body)))
    assert verdict.proposed == {"title": "T", "scope": "", "query": "q"}


# --- replies are context, the original post the subject ------------------------


def test_replies_are_read_as_context_and_shown_to_the_read():
    asked = []

    async def replier(query, *, max_results, since):
        asked.append(query)
        return [_post("9", author="r", text="The backups are the real issue.", is_reply=True),
                _post("1"),  # the post itself is never its own reply
                _post("8", author="s", text="not a reply")]
    report = _run(X.Verdict(), replier=replier)
    assert asked == ["conversation_id:1 is:reply lang:en -is:retweet"]
    [card] = X.store().all()
    assert [r["author"] for r in card["replies"]] == ["r"]
    assert report["read"] == 4  # the post, then the three replies X billed


def test_the_prompt_says_replies_never_set_the_subject():
    cand = _cand()
    cand.replies = [_post("9", author="r", text="What about the Phillies?", is_reply=True)]
    prompt = X.draft_prompt(cand)
    assert prompt.index("ORIGINAL post") < prompt.index("What about the Phillies?")
    assert "never set" in prompt and "ORIGINAL post anchors" in X.DRAFT_SYSTEM


def test_a_post_with_no_replies_spends_no_read_on_them():
    asked = []

    async def replier(query, *, max_results, since):
        asked.append(query)
        return []
    _run(X.Verdict(), replier=replier, post=_post("1", replies=0))
    assert asked == []


# --- people approve; nothing is linked unheard ---------------------------------


@pytest.fixture
def client(monkeypatch):
    import app as appmod
    from fastapi.testclient import TestClient

    monkeypatch.setattr(appmod, "ADMIN_TOKEN", "secret")
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    return TestClient(appmod.app)


ADMIN = {"X-Admin-Token": "secret"}


def _card(kind=X.THREAD_TO_EPISODE, proposed=None):
    cand = _cand()
    cand.kind = kind
    cand.draft = "Depth is the real question here. Who steps up first?"
    cand.assess = {"relevance": 4, "recommendation": X.RECOMMEND_COMMENT,
                   "proposed": proposed}
    X.store().add(cand, time.time())


def _detail(r):
    body = r.json()
    return body.get("detail") or body.get("error") or ""


def test_no_approval_without_checking_the_claims(client):
    _card(X.QUESTION_ONLY)
    r = client.post("/api/admin/x/decide", headers=ADMIN,
                    json={"post_id": "1", "action": "approve"})
    assert r.status_code == 422 and "claim" in _detail(r)


def test_no_episode_linked_before_it_is_heard_through_its_link(client):
    _card()
    r = client.post("/api/admin/x/decide", headers=ADMIN,
                    json={"post_id": "1", "action": "approve", "verified": True})
    assert r.status_code == 409 and "Listen" in _detail(r)
    # Without the link, the comment can go.
    r = client.post("/api/admin/x/decide", headers=ADMIN, json={
        "post_id": "1", "action": "approve", "verified": True, "no_link": True})
    assert r.status_code == 200 and r.json()["share_url"] == ""


def test_listening_records_it_and_unlocks_the_link(client):
    _card()
    client.post("/api/admin/x/listen", headers=ADMIN, json={"post_id": "1"})
    [card] = client.get("/api/admin/x", headers=ADMIN).json()["cards"]
    assert card["listened_at"] > 0
    r = client.post("/api/admin/x/decide", headers=ADMIN,
                    json={"post_id": "1", "action": "approve", "verified": True})
    assert r.status_code == 200 and "/s/" in r.json()["share_url"]


def test_rejections_are_listed_for_audit_never_as_cards(client):
    _run(X.Verdict(relevance=2, relevance_why="wrong angle"))
    body = client.get("/api/admin/x", headers=ADMIN).json()
    assert body["cards"] == [] and body["rejected"][0]["relevance_why"] == "wrong angle"
    r = client.post("/api/admin/x/decide", headers=ADMIN, json={
        "post_id": "1", "action": "approve", "verified": True})
    assert r.status_code == 404


def test_a_written_proposal_is_matched_onto_its_card_unheard(client, monkeypatch):
    import app as appmod

    proposal = {"title": "Why the Philadelphia Eagles Defence Is Thin", "scope": "",
                "query": EPISODE["query"]}
    _card(X.QUESTION_ONLY, proposed=proposal)
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", FakeCache([]))
    r = client.post("/api/admin/x/match", headers=ADMIN, json={"post_id": "1"})
    assert r.status_code == 409 and "Search its question in FAM" in _detail(r)
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", FakeCache([EPISODE]))
    card = client.post("/api/admin/x/match", headers=ADMIN, json={"post_id": "1"}).json()["card"]
    assert card["kind"] != X.QUESTION_ONLY and card["episode"]["key"] == "k-eagles"
    assert card["recommendation"] == X.RECOMMEND_EPISODE and card["listened_at"] == 0
    r = client.post("/api/admin/x/decide", headers=ADMIN,
                    json={"post_id": "1", "action": "approve", "verified": True})
    assert r.status_code == 409


def test_nothing_here_can_post():
    """The guardrails' last line: never auto-publish ([no-social-posting])."""
    import pathlib

    source = (pathlib.Path(X.__file__)).read_text()
    assert ".post(" not in source and "oauth" not in source.lower()


def test_the_before_deployment_review_puts_post_episode_and_comment_side_by_side(monkeypatch):
    """§368 rule 10: compare the agent against real posts, side by side."""
    import importlib.util
    import pathlib

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEYS", raising=False)
    path = pathlib.Path(X.__file__).parent / "tools" / "x_agent_eval.py"
    spec = importlib.util.spec_from_file_location("x_agent_eval", path)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    post = tool.post_from({"url": "https://x.com/drjane/status/42",
                           "text": "Is the Philadelphia Eagles defence thin?"}, 0)
    assert (post.author, post.id) == ("drjane", "42")
    import outreach_intelligence as OI

    cache = FakeCache([EPISODE])
    row = asyncio.run(tool.judge(cache, tool.candidate(post, OI.catalogue(cache), [])))
    # Read in code: a comment at most, never an episode on code's word.
    assert row["recommendation"] == X.RECOMMEND_COMMENT and row["episode"] is None
    html = tool.page([row])
    for column in ("Original post", "Episode", "Proposed comment", "Relevance", "Reviewer"):
        assert column in html
    assert "x.com/drjane/status/42" in html


def test_a_read_in_code_never_claims_the_author_test_passed(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEYS", raising=False)

    async def searcher(query, *, max_results, since):
        return [_post("1")]
    asyncio.run(X.run(FakeCache([EPISODE]), now=NOW, searcher=searcher))
    [card] = X.store().all()
    assert card["author_test"] is None


def test_match_again_never_changes_an_approved_reply(client, monkeypatch):
    import app as appmod

    _card(X.QUESTION_ONLY, proposed={"title": "T", "scope": "", "query": EPISODE["query"]})
    assert client.post("/api/admin/x/decide", headers=ADMIN, json={
        "post_id": "1", "action": "approve", "verified": True}).status_code == 200
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", FakeCache([EPISODE]))
    r = client.post("/api/admin/x/match", headers=ADMIN, json={"post_id": "1"})
    assert r.status_code == 409 and "approved" in _detail(r)


def test_a_card_with_no_link_never_says_fam_has_an_episode():
    """Review finding: a card below LINK_FLOOR whose read still chose its
    episode kept a reply that named it, with nothing linked."""
    far = _post("1", text="Philadelphia Eagles fans: what is the best tailgate food?")

    async def searcher(query, *, max_results, since):
        return [far]

    async def drafter(cand):
        cand.kind = X.QUESTION_ONLY  # what a match under LINK_FLOOR makes
        return ("We covered this in a short episode on the defence.", "m", 0.0,
                X.Verdict(relevance=5, choice=0, episode_fits=True))
    asyncio.run(X.run(FakeCache([EPISODE]), now=NOW, searcher=searcher, drafter=drafter))
    [card] = X.store().all()
    assert card["kind"] == X.QUESTION_ONLY and card["drafted_by"] == "code"
    assert "episode" not in card["draft"].lower()


@pytest.mark.parametrize("words", [
    "Fine-tune in stages and the cost drops.",
    "I don't miss the old salary cap rules, do you?",
    "Teams rarely check out of a scheme mid-season.",
    "Here's the thing nobody prices in: depth.",
    "We covered this in our episode on the cap.",
    "Two paths ✓ and ✗ - which one holds?"])
def test_ordinary_words_are_not_an_advertisement(words):
    assert X.promotional(words) == []


def test_a_link_needs_a_four_or_five_on_the_server_too(client):
    _card()
    X.store()._conn().execute("UPDATE candidates SET relevance = 0, assess = ''")
    client.post("/api/admin/x/listen", headers=ADMIN, json={"post_id": "1"})
    r = client.post("/api/admin/x/decide", headers=ADMIN,
                    json={"post_id": "1", "action": "approve", "verified": True})
    assert r.status_code == 409 and "4 or 5" in _detail(r)


def test_a_written_proposal_is_no_longer_proposed(client, monkeypatch):
    import app as appmod

    _card(X.QUESTION_ONLY, proposed={"title": "T", "scope": "", "query": EPISODE["query"]})
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", FakeCache([EPISODE]))
    card = client.post("/api/admin/x/match", headers=ADMIN, json={"post_id": "1"}).json()["card"]
    assert card["episode"]["key"] == "k-eagles" and card["proposed"] is None
