"""§305: episode quality, measured by category.

Pinned here:

* the code proxies score what they say they score, and name the sentences
  that cost the points;
* every written episode is kept with its measures, where its script is
  cached, and a listener's furthest point heard is kept beside it;
* the report rolls a node's episodes up the tree, borrows for thin nodes,
  holds each node to its own heading and sorts by what weak writing costs;
* a grade counts only once it agrees with the owner's own scores, and a
  grade with no quote from the script is thrown away;
* grading is off by default, never on staging, and capped per day;
* a deleted account's listening goes, and a full wipe takes every row.
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import os
import sys
import types

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import cache as cache_mod  # noqa: E402
import config  # noqa: E402
import episode_quality as Q  # noqa: E402
import script_generator as G  # noqa: E402
from pipeline import PodcastPipeline  # noqa: E402
from script_generator import plan_episode  # noqa: E402
from tts import DebugEngine  # noqa: E402

from test_pipeline import FakeGenerator  # noqa: E402


def K(name: str) -> str:
    """A key shaped like the cache's."""
    return hashlib.sha256(name.encode()).hexdigest()


K1 = K("k1")


def tune(monkeypatch, **fields):
    """Settings are frozen: swap the module's copy for a changed one."""
    monkeypatch.setattr(Q, "settings", dataclasses.replace(Q.settings, **fields))

GOOD = [
    "The Eagles beat Dallas twenty-four to ten on Sunday night in Philadelphia.",
    "It turned in the third quarter, because Jalen Hurts kept the ball on fourth down.",
    "That run forced Dallas to blitz, so the middle of the field opened for Saquon Barkley.",
    "Barkley ran for a hundred and forty yards, most of them after the blitz came.",
    "The win puts Philadelphia two games clear in the NFC East with six to play.",
]
EVIDENCE = ("Eagles 24, Cowboys 10. Philadelphia beat Dallas on Sunday Night "
            "Football. Jalen Hurts converted on fourth down; Saquon Barkley "
            "ran for 140 yards. The NFC East lead is two games.")


# --- the code proxies ------------------------------------------------------------


def test_a_good_script_scores_well_on_every_proxy():
    m = Q.measure(GOOD, "eagles cowboys score", EVIDENCE, 120)
    s = m["scores"]
    assert s["information"] == 1.0, m["checks"]["unsupported_names"]
    assert s["repetition"] == 1.0
    assert s["hook"] == 1.0
    assert s["storytelling"] == 1.0
    assert s["flow"] > 0.6
    assert m["opener"] == "the eagles beat dallas"


def test_a_name_the_evidence_never_mentions_is_named():
    m = Q.measure(GOOD + ["Coach Bill Belichick called it the play of the year."],
                  "eagles cowboys score", EVIDENCE)
    assert "Belichick" in m["checks"]["unsupported_names"]
    assert m["scores"]["information"] < 1.0
    # Without evidence there is nothing to ground against: no score, not 0.
    assert Q.measure(GOOD, "eagles", "")["scores"]["information"] is None


def test_the_hook_fails_on_a_banned_opener_and_off_the_question():
    m = Q.measure(["Picture this: a cold night in a big stadium.",
                   "Fans were everywhere."] + GOOD, "eagles cowboys score")
    hook = m["checks"]["hook"]
    assert hook["banned_opener"] is True
    assert hook["on_question"] is False
    assert m["scores"]["hook"] < 0.5


@pytest.mark.parametrize("ending,flag", [
    ("Will they hold on in December?", "question"),
    ("So, to sum up, the Eagles won and Dallas lost.", "recap"),
    ("Whether it lasts remains to be seen.", "tease"),
])
def test_an_ending_that_asks_recaps_or_teases_is_caught(ending, flag):
    m = Q.measure(GOOD + [ending], "eagles cowboys score", EVIDENCE)
    assert m["checks"]["ending"][flag] is True
    assert m["scores"]["storytelling"] < 1.0


def test_a_sentence_said_twice_is_repetition():
    again = "Barkley ran for a hundred and forty yards, most of them after the blitz."
    m = Q.measure(GOOD + [again], "eagles", EVIDENCE)
    assert m["checks"]["repeats"] == [again]
    assert m["scores"]["repetition"] < 1.0


def test_and_then_is_a_list_and_because_is_a_story():
    listed = ["The Eagles played Dallas on Sunday in Philadelphia.",
              "And then Hurts threw a pass to A.J. Brown.",
              "Also Barkley ran for a hundred yards.",
              "Another score came in the fourth from Philadelphia."]
    told = Q.measure(GOOD, "eagles", EVIDENCE)["scores"]["flow"]
    assert Q.measure(listed, "eagles", EVIDENCE)["scores"]["flow"] < told


def test_depth_counts_sentences_with_nothing_in_them_and_the_time_used():
    thin = ["The Eagles won on Sunday.", "It was good.", "People were happy.",
            "It mattered a lot."]
    m = Q.measure(thin, "eagles", "", 300)
    assert set(m["checks"]["no_particular"]) == set(thin[1:])
    assert m["checks"]["budget_share"] < 0.2
    assert m["scores"]["depth"] < 0.3


def test_measuring_nothing_or_nonsense_never_raises():
    assert Q.measure([], "q")["scores"] == {m: None for m in Q.METRICS}
    assert Q.measure([None, "", "   "], "")["words"] == 0
    assert Q.measure(object(), "q")["scores"]["hook"] is None   # not iterable


# --- the store ---------------------------------------------------------------------


def _notes(**fields):
    notes = G.ScriptNotes()
    notes.written_from = EVIDENCE
    notes.structure = "sports_recap"
    notes.word_budget = 120
    notes.writer_model = "claude-haiku-5-5"
    notes.category = "american football"
    notes.title = "How the Eagles Broke Dallas"
    for name, value in fields.items():
        setattr(notes, name, value)
    return notes


def test_nothing_is_created_until_an_episode_is_written():
    assert Q.exists() is False
    assert Q.forget("u") == 0 and Q.clear() == 0
    assert Q.report()["episodes"] == 0
    assert Q.exists() is False


def test_a_written_episode_is_kept_with_its_measures():
    Q.note(K1, 1000, "eagles cowboys score", 1, "search", GOOD, _notes())
    row = Q.store().get(K1, 1000)
    assert row["origin"] == "search" and row["structure"] == "sports_recap"
    assert row["category"] == "american football"
    assert row["writer_model"] == "claude-haiku-5-5"
    assert row["prompt"] == Q.prompt_version() and len(row["prompt"]) == 12
    assert json.loads(row["sentences"]) == GOOD
    assert row["evidence"] == EVIDENCE
    assert json.loads(row["measures"])["scores"]["information"] == 1.0
    # A note with nothing to keep, or one that fails, is never a failure.
    Q.note("", 1, "q", 1, "search", GOOD)
    Q.note("k2", 1, "q", 1, "search", [])
    Q.note("k3", 1, "q", 1, "search", GOOD, notes=object())
    assert Q.store().get("k2", 1) is None


def test_a_listen_keeps_the_furthest_point_heard():
    Q.note(K1, 1000, "eagles", 1, "search", GOOD, _notes())
    Q.note_listen(f"{K1}.1000", "u", 30, 0, 1)
    Q.note_listen(f"{K1}.1000", "u", 12, 58, 1)  # a seek back is not less heard
    Q.note_listen("not an id!", "u", 30, 0, 1)   # dropped, never raised
    [listen] = Q.store().listens_for([K1])
    assert listen["heard"] == 30 and listen["duration"] == 58


def test_a_deleted_listener_and_a_wipe_take_what_they_should():
    Q.note(K1, 1000, "eagles", 1, "search", GOOD, _notes())
    Q.note_listen(f"{K1}.1000", "u", 30, 0, 1)
    Q.note_listen(f"{K1}.1000", "v", 30, 0, 1)
    assert Q.forget("u") == 1
    assert [r["heard"] for r in Q.store().listens_for([K1])] == [30]
    assert Q.clear() == 1
    assert Q.store().get(K1, 1000) is None
    assert Q.store().listens_for([K1]) == []


# --- the report ---------------------------------------------------------------------

TREE = {"nfl": ["american football", "sports"],
        "american football": ["sports"],
        "nba": ["basketball", "sports"],
        "basketball": ["sports"],
        "sports": []}


def _report(**kw):
    return Q.report(resolve=lambda words: words if words in TREE else "",
                    ancestors=lambda node: TREE.get(node, []),
                    label_of=lambda node: node.title(), **kw)


def _write(n, category, script, minutes=1, origin="search", start=0):
    for i in range(n):
        Q.note(K(f"{category}-{start + i}"), 1000 + i, "eagles cowboys score",
               minutes, origin, script, _notes(category=category))


def test_a_node_rolls_up_into_every_ancestor(monkeypatch):
    tune(monkeypatch, quality_min_episodes=2)
    _write(3, "nfl", GOOD)
    _write(2, "nba", GOOD[:2] + ["So, to sum up, it went well?"])
    r = _report()
    by = {n["node"]: n for n in r["nodes"]}
    assert by["sports"]["episodes"] == 5 and by["sports"]["own_episodes"] == 0
    assert by["nfl"]["path"] == ["sports", "american football", "nfl"]
    assert by["american football"]["episodes"] == 3
    # The weaker branch is below its heading, and sorts first.
    assert by["nba"]["shortfall"] > 0 > by["nfl"]["shortfall"]
    assert r["nodes"][0]["node"] in ("nba", "basketball")
    assert by["nba"]["worst"][0]["episode"].split(".")[0] in (K("nba-0"), K("nba-1"))


def test_a_thin_node_borrows_and_an_unplaced_episode_is_said_so(monkeypatch):
    tune(monkeypatch, quality_min_episodes=3)
    _write(3, "nfl", GOOD)
    _write(1, "nba", GOOD)
    _write(1, "underwater basket weaving", GOOD)
    by = {n["node"]: n for n in _report()["nodes"]}
    assert by["nba"]["enough"] is False and by["nba"]["priority"] == 0.0
    assert by["nba"]["borrowed_from"] == "sports"
    assert by[Q.UNPLACED]["episodes"] == 1


def test_the_same_opening_across_a_node_is_flagged(monkeypatch):
    _write(3, "nfl", GOOD)
    assert {n["node"]: n for n in _report()["nodes"]}["nfl"]["shared_opener"] == 3


def test_listening_is_the_hook_and_the_storys_measure():
    _write(2, "nfl", GOOD)
    Q.note_listen(f"{K('nfl-0')}.1000", "a", 8, 60, 1)    # out on the hook
    Q.note_listen(f"{K('nfl-0')}.1000", "b", 55, 60, 1)   # finished
    Q.note_listen(f"{K('nfl-1')}.1001", "a", 30, 0, 1)    # neither
    nfl = {n["node"]: n for n in _report()["nodes"]}["nfl"]
    assert nfl["listens"] == 3
    assert nfl["early_exit"] == pytest.approx(1 / 3, abs=1e-3)
    assert nfl["finished"] == pytest.approx(1 / 3, abs=1e-3)
    assert nfl["scores"]["hook"]["listeners"] == pytest.approx(0.75)


def test_a_grade_counts_only_once_it_agrees_with_the_owner(monkeypatch):
    monkeypatch.setattr(Q, "CALIBRATION_MIN", 2)
    _write(2, "nfl", GOOD)
    for i in range(2):
        grade = {m: {"score": 1, "quote": GOOD[0], "reason": "r"} for m in Q.METRICS}
        Q.store().set_grade(K(f"nfl-{i}"), 1000 + i, grade, "m", 0.01)
    before = {n["node"]: n for n in _report()["nodes"]}["nfl"]
    assert before["scores"]["hook"]["trusted"] is False
    assert before["overall"] > 0.9      # still ranked on code
    for i in range(2):
        assert Q.label(f"{K(f'nfl-{i}')}.{1000 + i}", "hook", 1)
    r = _report()
    assert r["calibration"]["hook"]["trusted"] is True
    assert r["calibration"]["flow"]["trusted"] is False
    after = {n["node"]: n for n in r["nodes"]}["nfl"]
    assert after["scores"]["hook"]["trusted"] is True
    assert after["overall"] < before["overall"]


def test_a_label_is_held_to_the_scale():
    assert Q.label(f"{K1}.1", "hook", 6) is False
    assert Q.label(f"{K1}.1", "vibes", 3) is False
    assert Q.label("nonsense id", "hook", 3) is False
    assert Q.label(f"{K1}.1", "hook", "4") is True


def test_validity_is_a_rank_correlation_with_enough_episodes(monkeypatch):
    assert Q.spearman([1, 2, 3], [1, 2, 3]) == 1.0
    assert Q.spearman([1, 2, 3], [3, 2, 1]) == -1.0
    assert Q.spearman([1, 1, 1], [1, 2, 3]) is None
    _write(3, "nfl", GOOD)
    assert _report()["validity"]["hook"]["code"]["predicts_heard"] is None


# --- the grader ----------------------------------------------------------------------


def test_a_grade_without_a_real_quote_or_off_the_scale_is_dropped():
    data = {"hook": {"score": 4, "quote": GOOD[0], "reason": "situated"},
            "flow": {"score": 4, "quote": "a sentence nobody wrote", "reason": ""},
            "depth": {"score": 9, "quote": GOOD[1], "reason": ""},
            "repetition": {"score": 5, "quote": "", "reason": ""},
            "unsupported_claims": ["the coach's quote"]}
    grade = Q.read_grade(data, GOOD)
    assert set(grade) == {"hook", "unsupported_claims"}
    assert grade["unsupported_claims"] == ["the coach's quote"]


def test_the_rubric_and_schema_cover_every_metric():
    assert set(Q.RUBRIC) == set(Q.METRICS)
    assert set(Q.GRADE_SCHEMA["required"]) == set(Q.METRICS) | {"unsupported_claims"}
    for metric in Q.METRICS:
        assert Q.LABELS[metric] and metric in Q.GRADE_SYSTEM


def test_grading_is_off_by_default_and_never_on_staging(monkeypatch):
    monkeypatch.delenv("QUALITY_GRADE", raising=False)
    assert config.Settings().quality_grade == "off"
    assert Q.may_grade("prefetch") is False
    tune(monkeypatch, quality_grade="ahead")
    import spend_guard

    monkeypatch.setattr(spend_guard, "enabled", lambda: False)
    assert Q.may_grade("prefetch") and Q.may_grade("trending")
    assert not Q.may_grade("search")
    tune(monkeypatch, quality_grade="all")
    assert Q.may_grade("search")
    monkeypatch.setattr(spend_guard, "enabled", lambda: True)
    assert not Q.may_grade("prefetch")


class _Client:
    """A grader that answers with `data`, counting its calls."""

    def __init__(self, data):
        self.calls = 0
        self.data = data
        self.messages = self

    async def create(self, **kwargs):
        self.calls += 1
        assert kwargs["output_config"]["format"]["schema"] == Q.GRADE_SCHEMA
        assert "Eagles" in kwargs["messages"][0]["content"]
        return types.SimpleNamespace(
            stop_reason="end_turn", usage=None,
            content=[types.SimpleNamespace(type="text", text=json.dumps(self.data))])


def test_a_stored_episode_is_graded_once_within_the_daily_ceiling(monkeypatch):
    tune(monkeypatch, quality_grade_daily=1)
    _write(2, "nfl", GOOD)
    client = _Client({m: {"score": 4, "quote": GOOD[2], "reason": "ok"}
                      for m in Q.METRICS} | {"unsupported_claims": []})
    assert asyncio.run(Q.grade_pending(client, 5)) == 1
    assert client.calls == 1          # the ceiling stopped the second
    graded = [r for r in Q.store().episodes_since(0) if r["grade"]]
    assert len(graded) == 1
    row = Q.store().get(graded[0]["key"], graded[0]["sourced"])
    assert row["evidence"] == ""      # its only reader is done with it
    assert json.loads(row["grade"])["hook"]["score"] == 4


def test_an_unreadable_grade_is_a_notice_and_no_grade(monkeypatch):
    import pipeline_errors

    _write(1, "nfl", GOOD)
    seen = []
    monkeypatch.setattr(pipeline_errors, "record",
                        lambda *a, **k: seen.append(a))

    class Broken(_Client):
        async def create(self, **kwargs):
            return types.SimpleNamespace(stop_reason="end_turn", usage=None,
                                         content=[types.SimpleNamespace(
                                             type="text", text="not json")])

    assert asyncio.run(Q.grade_stored(Broken({}), K("nfl-0"), 1000)) is None
    assert seen and seen[0][1] == "quality grade failed"
    assert Q.store().get(K("nfl-0"), 1000)["grade"] == ""


# --- the writer and the app ----------------------------------------------------------


def test_the_writer_notes_what_the_episode_was_written_from():
    plan = plan_episode("a question", 2, search=False)
    plan.evidence = "the packet"
    notes = G.ScriptNotes()
    reader = G._ScriptReader(plan, notes)
    reader.feed("One sentence here. ")
    reader.finish()
    assert notes.written_from == "the packet"
    assert notes.word_budget == plan.word_budget


def test_every_place_a_script_is_cached_measures_it():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ("pipeline.py", "prefetch.py", "daily_edition.py",
                 "trending_bank.py"):
        source = open(os.path.join(root, name), encoding="utf-8").read()
        assert source.count("category_audit.note(") == source.count(
            "episode_quality.note("), name


class _Writer(FakeGenerator):
    async def stream_sentences(self, plan, notes=None):
        async for s in super().stream_sentences(plan, notes):
            yield s
        if notes is not None:
            notes.category = "heavyweight boxing"
            notes.word_budget = plan.word_budget


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "DEMO_MODE", False)
    monkeypatch.setattr(appmod, "ADMIN_TOKEN", "secret")
    monkeypatch.setattr(appmod, "SCRIPT_CACHE",
                        cache_mod.SqliteScriptCache(str(tmp_path / "e.db")))
    monkeypatch.setattr(
        appmod, "_make_pipeline",
        lambda voice=None, author="": PodcastPipeline(
            generator=_Writer(), engine=DebugEngine(), cache=appmod.SCRIPT_CACHE,
            voice=voice))
    with TestClient(appmod.app) as c:
        c.post("/api/auth/signup", json={"email": "q@b.com",
                                         "password": "password12"})
        yield c


ADMIN = {"X-Admin-Token": "secret"}


def test_a_heard_episode_is_measured_listened_to_and_reported(client):
    q = "what a chaotic press conference signals about the fight"
    r = client.get(f"/api/audio?q={q}&minutes=1&fmt=pcm&surface=search")
    episode = r.headers.get("x-fam-episode")
    assert episode
    [row] = Q.store().episodes_since(0)
    assert row["query"] == q and row["category"] == "heavyweight boxing"
    assert row["origin"] == "search"

    r = client.post("/api/progress", json={"query": q, "minutes": 1,
                                           "seconds": 12, "episode": episode})
    assert r.status_code == 200, r.text
    assert Q.store().listens_for([row["key"]])[0]["heard"] == 12
    # An older client sends no episode, and is simply not measured.
    assert client.post("/api/progress", json={"query": q, "minutes": 1,
                                              "seconds": 40}).status_code == 200
    assert Q.store().listens_for([row["key"]])[0]["heard"] == 12

    assert client.get("/api/admin/quality").status_code == 404
    report = client.get("/api/admin/quality", headers=ADMIN).json()
    assert report["episodes"] == 1 and report["nodes"]
    one = client.get(f"/api/admin/quality/episode?episode={episode}",
                     headers=ADMIN).json()
    assert one["query"] == q and "evidence" not in one and one["sentences"]
    r = client.post("/api/admin/quality/label", headers=ADMIN,
                    json={"episode": episode, "scores": {"hook": 2, "flow": 4}})
    assert r.json() == {"ok": True, "kept": 2}
    assert client.post("/api/admin/quality/label", headers=ADMIN,
                       json={"episode": episode,
                             "scores": {"hook": 9}}).status_code == 422


def test_a_guest_listen_is_never_kept(client):
    client.post("/api/auth/logout")
    client.post("/api/progress", json={"query": "q", "minutes": 1, "seconds": 12,
                                       "episode": f"{K1}.1000"})
    assert not Q.exists() or Q.store().listens_for([K1]) == []


def test_a_deleted_account_takes_its_listening(client):
    user = client.get("/api/auth/me").json()["user_id"]
    Q.note_listen(f"{K1}.1000", user, 30, 0, 1)
    assert appmod.erase_listener(user)["quality_listens"] == 1


def test_the_admin_page_is_linked_and_signs_in_like_the_others():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    page = open(os.path.join(root, "admin_ui", "quality.html")).read()
    tracker = open(os.path.join(root, "admin_ui", "tracker.html")).read()
    assert 'href="/admin/quality"' in tracker
    assert "/api/admin/login" in page and "forgotLink" in page
    assert "/api/admin/quality" in page
