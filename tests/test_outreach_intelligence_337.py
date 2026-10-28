"""The X and LinkedIn engines read a post before they write under it (§337).

The owner's flip chart: target posts with high engagement, cross-reference
FAM's cached episodes, find the match, and write what fits the post and the
platform - X for curiosity and the conversation, LinkedIn for context and
Go Deeper - with "some kind of awareness/intelligence (like Episode
Intelligence)". These pin each of those.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import linkedin_posts as L
import outreach_intelligence as OI
import x_threads as X

NOW = 1_800_000_000.0

EAGLES = {"key": "k-eagles", "query": "Can the Eagles defence hold up without their starters?",
          "title": "Why the Philadelphia Eagles Defence Is Thin", "minutes": 3,
          "plays": 12, "explicit": False}
RATES = {"key": "k-rates", "query": "How does the Federal Reserve decide interest rates?",
         "title": "How the Federal Reserve Decides Interest Rates", "minutes": 3,
         "plays": 4, "explicit": False}
SAYS = {"k-rates": ["The Federal Reserve meets eight times a year.",
                    "Its committee watches inflation before anything else."],
        "k-eagles": ["The Eagles lost three starters in a fortnight."]}


class Cache:
    """The script cache as the engines read it: kept rows, and what each
    episode says."""

    def __init__(self, rows, said=SAYS):
        self.rows, self.said = rows, said

    def recent(self, limit=40, exclude_author="", origin="", keys=()):
        return [r for r in self.rows if not keys or r["key"] in keys][:limit]

    def get(self, key, current=True):
        return self.said.get(key)

    def summary(self, key):
        return {"k-rates": "How the Fed's committee sets rates, and what it reads first."}.get(key, "")

    def thread(self, key):
        return ""


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class Model:
    """A stand-in for the Anthropic client that answers with `body`."""

    def __init__(self, **body):
        self.body = json.dumps(body)
        self.messages = self
        self.asked = []

    async def create(self, **kw):
        self.asked.append(kw)
        body = self.body

        class R:
            content = [_Block(body)]
            usage = None
        return R()


def _post(pid="1", text="Does anyone think the Philadelphia Eagles defence can survive this?",
          author="fan", likes=40, replies=6, reposts=3, **kw):
    return X.Post(id=pid, text=text, created_at=NOW - 3600, author=author,
                  author_name=author.title(), followers=5000, likes=likes,
                  replies=replies, reposts=reposts, lang="en", **kw)


def _seed(row=EAGLES, names=("Philadelphia Eagles",)):
    return X.Seed(row["key"], row["query"], row["title"], 3, names, "cached", 12)


READ = {"subject": "the Eagles' defence", "intent": "asking",
        "wants": "whether the defence can hold", "same_subject": True, "choice": 0,
        "why": "the post asks about the Eagles' defence; so does the episode",
        "angle": "how many starters it has lost", "detail": "three starters in a fortnight",
        "other_same_subject": False}


# --- high engagement -------------------------------------------------------------


def test_a_post_under_the_engagement_floor_is_not_a_card():
    quiet = _post(likes=2, replies=1, reposts=0)
    assert X.screen(quiet, NOW, window_hours=6, min_engagement=25) == "too little engagement yet"
    assert X.shortlist([(quiet, _seed())], NOW, window_hours=6, min_engagement=25) == []
    assert X.shortlist([(_post(), _seed())], NOW, window_hours=6, min_engagement=25)


def test_the_floor_is_a_setting_copied_into_env_example():
    assert X.config.settings.x_threads_min_engagement == 25
    assert L.config.settings.linkedin_min_engagement == 20
    example = open(X.config.__file__.replace("config.py", ".env.example")).read()
    assert "X_THREADS_MIN_ENGAGEMENT=25" in example
    assert "LINKEDIN_MIN_ENGAGEMENT=20" in example


def test_linkedin_reads_engagement_off_the_search_result():
    assert L.engagement_from("Jane Doe on LinkedIn | 12 comments", "1.2K reactions") == 1212
    assert L.engagement_from("12 comments", "12 comments") == 12
    assert L.engagement_from("Jane Doe on LinkedIn: rates") == -1


# --- every kept episode, not only the one searched for ------------------------------


def test_a_post_found_from_one_episode_is_matched_to_the_one_that_answers_it():
    text = "Does anyone actually know how the Federal Reserve decides interest rates?"
    post = _post(text=text)
    episodes = OI.catalogue(Cache([EAGLES, RATES]))
    # Found searching for the Eagles episode, which it is not about.
    [cand] = X.shortlist([(post, _seed())], NOW, window_hours=6, episodes=episodes)
    assert cand.seed.key == "k-rates"
    assert "another kept episode is closer to it" in cand.reason
    # Without the catalogue it is no card at all, as before.
    assert X.shortlist([(post, _seed())], NOW, window_hours=6) == []


# --- the read -------------------------------------------------------------------------


def test_the_read_comes_before_the_words():
    for system in (X.DRAFT_SYSTEM, L.DRAFT_SYSTEM):
        assert system.index("subject:") < system.index("choice:") < system.index("Then write")
    for schema in (X.DRAFT_SCHEMA, L.DRAFT_SCHEMA):
        fields = list(schema["properties"])
        assert fields[:3] == ["subject", "intent", "wants"]
        assert fields.index("detail") < fields.index("reply" if "reply" in fields else "comment")


def test_each_platform_has_its_own_voice():
    assert "curiosity" in X.DRAFT_SYSTEM and "conversation" in X.DRAFT_SYSTEM
    assert "context" in L.DRAFT_SYSTEM and "Go Deeper" in L.DRAFT_SYSTEM
    assert OI.VOICE[OI.LINKEDIN] not in X.DRAFT_SYSTEM
    assert OI.VOICE[OI.X] not in L.DRAFT_SYSTEM


def test_the_read_is_shown_what_the_episode_says_not_only_its_title():
    [cand] = X.shortlist([(_post(), _seed())], NOW, window_hours=6)
    cand.material = {"k-eagles": OI.material(Cache([EAGLES]), "k-eagles")}
    model = Model(**READ, reply="Three starters in a fortnight is a lot to absorb. Who steps up?")
    reply, by, _d, verdict = asyncio.run(X.judge_and_draft(cand, model))
    prompt = model.asked[0]["messages"][0]["content"]
    assert "The Eagles lost three starters in a fortnight." in prompt
    assert by == X.config.settings.x_threads_model and reply.startswith("Three starters")
    assert verdict.read["angle"] == "how many starters it has lost"


def test_the_read_may_choose_another_offered_episode():
    rates = _seed(RATES, ("Federal Reserve",))
    cand = X.Candidate(_post(), _seed(), 0.9, {"match": 0.8}, X.THREAD_TO_EPISODE, "Why.")
    cand.options = [(cand.seed, 0.8), (rates, 0.7)]
    assert X.apply_verdict(cand, X.Verdict(choice=1, read=dict(READ, choice=1))) == ""
    assert cand.seed.key == "k-rates" and cand.parts["match"] == 0.7
    assert "The read chose" in cand.reason and cand.read["subject"]
    # Never one under the link floor, whatever the read says.
    cand = X.Candidate(_post(), _seed(), 0.9, {"match": 0.8}, X.THREAD_TO_EPISODE, "Why.")
    cand.options = [(cand.seed, 0.8), (rates, 0.2)]
    X.apply_verdict(cand, X.Verdict(choice=1))
    assert cand.seed.key == "k-eagles"


def test_no_episode_chosen_means_no_link():
    [cand] = X.shortlist([(_post(), _seed())], NOW, window_hours=6)
    model = Model(**dict(READ, choice=-1), reply="What would change your mind on the depth?")
    _reply, _by, _d, verdict = asyncio.run(X.judge_and_draft(cand, model))
    assert verdict.episode_fits is False
    X.apply_verdict(cand, verdict)
    assert cand.kind == X.QUESTION_ONLY


def test_a_figure_fam_has_not_got_is_never_said():
    [cand] = X.shortlist([(_post(), _seed())], NOW, window_hours=6)
    cand.material = {"k-eagles": OI.material(Cache([EAGLES]), "k-eagles")}
    model = Model(**READ, reply="They have allowed 31 points a game since October.")
    reply, by, _d, verdict = asyncio.run(X.judge_and_draft(cand, model))
    assert by == "code" and "31" not in reply and "31" in verdict.replaced
    X.apply_verdict(cand, verdict)
    assert "Rewritten in code" in cand.reason
    assert OI.unsupported_figures("eight times a year, 3 starters", [
        "meets eight times a year", "lost 3 starters"]) == []
    assert OI.unsupported_figures("1,200 fans and 40%", ["1200 fans"]) == ["40"]


def test_with_no_model_the_read_is_the_codes(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    [cand] = X.shortlist([(_post(), _seed())], NOW, window_hours=6)
    _reply, by, _d, verdict = asyncio.run(X.judge_and_draft(cand))
    assert by == "code" and verdict.read["by"] == "code"
    assert verdict.read["intent"] == "asking" and verdict.read["choice"] == 0


def test_a_run_stores_the_read_and_the_page_shows_it():
    async def searcher(query, *, max_results, since):
        return [_post()]

    async def drafter(cand):
        assert cand.material["k-eagles"]["excerpt"].startswith("The Eagles lost")
        return ("Who steps up?", "test-model", 0.001,
                X.Verdict(why="checked", read=dict(READ)))
    asyncio.run(X.run(Cache([EAGLES]), now=NOW, searcher=searcher, drafter=drafter))
    [card] = X.store().all()
    assert card["read"]["subject"] == "the Eagles' defence"
    assert card["read_line"] == ("Asking about the Eagles' defence; "
                                 "adds: how many starters it has lost")


def test_an_old_answer_without_a_choice_still_reads():
    assert OI.parse({"episode_fits": False}, 1, "m").choice == -1
    assert OI.parse({"fits": True}, 1, "m").choice == 0
    assert OI.parse({}, 1, "m").choice == 0
    assert OI.parse({"choice": 5}, 3, "m").choice == -1
    assert OI.parse({"intent": "shouting"}, 1, "m").intent == "other"


def test_material_reads_only_what_the_cache_keeps():
    said = OI.material(Cache([RATES]), "k-rates", chars=60)
    assert said["summary"].startswith("How the Fed's committee")
    assert said["excerpt"] == "The Federal Reserve meets eight times a year."

    class Bare:
        def recent(self, **kw):
            return []
    assert OI.material(Bare(), "k-rates") == {"summary": "", "excerpt": "", "next": ""}


# --- LinkedIn ---------------------------------------------------------------------------


TEACHING = (
    "Here's how the Federal Reserve decides interest rates, and why most people "
    "get it wrong.\n\n1. The committee reads inflation, not the stock market.\n"
    "2. Interest rates move slowly on purpose.\n3. The Federal Reserve signals "
    "before it acts.\n\nThe lesson for founders: plan for the signal, not the move.")


class _Result:
    def __init__(self, url, title="", text=""):
        self.url, self.title, self.published_date = url, title, "2026-10-26"
        self.highlights = [text] if text else []


def _url(n, slug):
    return f"https://www.linkedin.com/posts/{slug}_rates-activity-72512345678901{n:05d}-AbCd"


def _li_run(results, drafter=None):
    async def searcher(seed, *, recency_days):
        return results.get(seed.title, []), 0.0

    async def drafts(text, episode):
        return "The committee signals first.", "q?", "test-model", 0.0, ""
    return asyncio.run(L.run(Cache([RATES]), now=NOW, searcher=searcher,
                             drafter=drafter or drafts))


def test_linkedin_drops_a_quiet_post_keeps_an_unknown_one_and_ranks_the_loud_first():
    _li_run({RATES["title"]: [
        _Result(_url(1, "quiet-one"), "Quiet One on LinkedIn | 3 comments", TEACHING),
        _Result(_url(2, "unknown-one"), "Unknown One on LinkedIn: rates", TEACHING + " A."),
        _Result(_url(3, "loud-one"), "Loud One on LinkedIn | 400 comments", TEACHING + " B."),
    ]})
    cards = {c["author_name"]: c for c in L.store().all()}
    assert set(cards) == {"Unknown One", "Loud One"}
    assert cards["Loud One"]["engagement"] == 400 and cards["Unknown One"]["engagement"] == -1
    assert "400 reactions and comments" in cards["Loud One"]["reason"]


def test_linkedins_read_chooses_among_the_offered_episodes():
    second = dict(RATES, key="k-rates-2", title="Why the Federal Reserve Signals Interest Rates")
    card = L.Card("activity:1", "u", TEACHING, "jane", "Jane", "",
                  [dict(RATES, match=0.8, off_subject=""),
                   dict(second, match=0.7, off_subject="")])
    read = OI.parse(dict(READ, choice=1), 2, "m")
    assert L.apply(card, ("Words.", "q?", "m", 0.0, "", read)) == ""
    assert card.chosen == 1 and "The read chose" in card.reason
    assert card.read["choice"] == 1


def test_linkedins_read_saying_none_fits_unlinks_every_offered_episode():
    card = L.Card("activity:1", "u", TEACHING, "jane", "Jane", "",
                  [dict(RATES, match=0.8, off_subject=""),
                   dict(RATES, key="k-2", match=0.7, off_subject="")])
    read = OI.parse(dict(READ, choice=-1), 2, "m")
    assert L.apply(card, ("", "q?", "m", 0.0, "not this", read)) == "not this"
    assert not L.offered(card) and card.draft == ""


def test_linkedins_model_path_reads_writes_and_guards_figures():
    episode = dict(RATES, match=0.9, off_subject="")
    materials = {"k-rates": OI.material(Cache([RATES]), "k-rates")}
    model = Model(**dict(READ, intent="teaching"), comment="It meets eight times a year.",
                  question="How does the Fed signal a move?")
    comment, _q, by, _d, misfit, read = asyncio.run(L.judge_and_draft(
        TEACHING, episode, model, options=[episode], materials=materials))
    assert comment.startswith("It meets eight") and by != "code" and not misfit
    assert read.intent == "teaching"
    assert "meets eight times a year" in model.asked[0]["messages"][0]["content"]
    model = Model(**READ, comment="Rates rose 525 basis points.", question="q?")
    comment, _q, by, _d, _m, read = asyncio.run(L.judge_and_draft(
        TEACHING, episode, model, options=[episode], materials=materials))
    assert by == "code" and "525" not in comment and "525" in read.replaced


@pytest.mark.parametrize("module", ["x_threads", "linkedin_posts", "outreach_intelligence"])
def test_nothing_here_posts_or_reads_linkedin(module):
    import pathlib
    source = (pathlib.Path(__file__).resolve().parent.parent / f"{module}.py").read_text()
    assert ".post(" not in source and "oauth" not in source.lower()
    assert "httpx" not in source or module == "x_threads"
