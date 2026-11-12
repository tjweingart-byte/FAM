"""Optional surveys (surveys.py, PROBLEMS.md §560): the episode check, the
day-3 check-in and the two-week wrap-up."""
from __future__ import annotations

import csv
import io
import os
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import surveys as S  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
DAY = S.DAY


@pytest.fixture
def store(tmp_path):
    return S.SurveyStore(str(tmp_path / "surveys.db"))


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)


def listener(handle: str) -> TestClient:
    c = TestClient(appmod.app)
    r = c.post("/api/auth/signup", json={"email": f"{handle}@example.com",
                                         "password": "password12"})
    assert r.status_code == 200, r.text
    return c


def uid(c: TestClient) -> str:
    return c.get("/api/auth/me").json()["user_id"]


# --- when each survey is offered --------------------------------------------

def test_no_app_survey_before_day_three(store):
    assert store.offer("u", "open", account_created=0, now=2.9 * DAY) is None


def test_the_check_in_is_offered_on_day_three_and_not_twice_in_a_day(store):
    assert store.offer("u", "open", account_created=0, now=3 * DAY) == "day3"
    assert store.offer("u", "open", account_created=0, now=3.5 * DAY) is None
    assert store.offer("u", "open", account_created=0, now=4 * DAY) == "day3"


def test_answered_is_done(store):
    store.offer("u", "open", account_created=0, now=3 * DAY)
    store.answer("u", "day3", {"routine": {"pick": 7}}, now=3 * DAY)
    assert store.offer("u", "open", account_created=0, now=5 * DAY) is None


def test_not_now_three_times_is_never(store):
    for day in (3, 4, 5):
        assert store.offer("u", "open", account_created=0, now=day * DAY) == "day3"
        store.dismiss("u", "day3")
    assert store.offer("u", "open", account_created=0, now=6 * DAY) is None


def test_two_weeks_in_the_wrap_up_takes_the_check_ins_place(store):
    assert store.offer("u", "open", account_created=0, now=13 * DAY) == "wrap"
    # Offered today: the check-in is not offered instead.
    assert store.offer("u", "open", account_created=0, now=13.2 * DAY) is None
    store.answer("u", "wrap", {"disappointed": {"pick": "very"}}, now=13.3 * DAY)
    assert store.offer("u", "open", account_created=0, now=20 * DAY) is None


def test_the_episode_check_comes_after_the_first_episode_then_every_fifth(store):
    shown = []
    for n in range(1, 16):
        if store.offer("u", "episode_end", account_created=0, now=n * DAY) == "episode":
            shown.append(n)
    assert shown == [1, 5, 10, 15]


def test_the_episode_check_is_at_most_once_a_day(store):
    for n in range(1, 6):
        got = store.offer("u", "episode_end", account_created=0, now=DAY + n)
        assert got == ("episode" if n == 1 else None)


def test_no_listener_no_survey(store):
    assert store.offer("", "open", account_created=0, now=9 * DAY) is None
    assert store.offer("u", "lunch", account_created=0, now=9 * DAY) is None


# --- what an answer may be -------------------------------------------------

def test_answers_are_checked_against_the_survey():
    with pytest.raises(S.SurveyError):
        S.clean_answers("episode", {"rating": {"pick": "amazing"}})
    with pytest.raises(S.SurveyError):
        S.clean_answers("day3", {"routine": {"pick": 11}})
    with pytest.raises(S.SurveyError):
        S.clean_answers("day3", {"shoe_size": {"pick": "9"}})
    with pytest.raises(S.SurveyError, match="at least one"):
        S.clean_answers("day3", {})


def test_a_follow_up_that_should_not_have_shown_is_not_kept():
    got = S.clean_answers("episode", {"rating": {"pick": "loved"},
                                      "off": {"pick": ["facts"]}})
    assert got == {"rating": {"pick": "loved"}}


def test_the_name_box_and_the_other_box_are_kept():
    got = S.clean_answers("episode", {"rating": {"pick": "meh"},
                                      "off": {"pick": ["name"], "text": "Saquon"}})
    assert got["off"] == {"pick": ["name"], "text": "Saquon"}
    got = S.clean_answers("day3", {"role": {"text": "Coach"}})
    assert got == {"role": {"text": "Coach"}}
    # A question with no box keeps no text.
    got = S.clean_answers("day3", {"used": {"pick": ["search"], "text": "x"}})
    assert got == {"used": {"pick": ["search"]}}


def test_every_question_is_multiple_choice_with_other_or_a_scale_or_a_line():
    for survey in S.SURVEYS.values():
        for q in survey["questions"]:
            assert q["kind"] in ("single", "multi", "scale", "text")
            if q["kind"] == "scale":
                assert (q["min"], q["max"]) == (1, 10)


# --- the endpoints ----------------------------------------------------------

def test_a_guest_is_never_surveyed():
    guest = TestClient(appmod.app)
    assert guest.post("/api/surveys/offer", json={"moment": "open"}).json() == {"survey": None}
    r = guest.post("/api/surveys/answer", json={"survey": "day3",
                                                "answers": {"routine": {"pick": 5}}})
    assert r.status_code == 401


def test_an_account_gets_the_episode_check_and_answers_it():
    c = listener("svyann")
    d = c.post("/api/surveys/offer", json={"moment": "episode_end"}).json()
    assert d["survey"]["id"] == "episode"
    r = c.post("/api/surveys/answer", json={
        "survey": "episode", "answers": {"rating": {"pick": "bad"},
                                         "off": {"pick": ["slow"]}},
        "episode": {"id": "ep123", "title": "Why the Eagles lost"}})
    assert r.status_code == 200, r.text
    rows = appmod.SURVEY_STORE.responses("episode")
    mine = [x for x in rows if x["user_id"] == uid(c)]
    assert mine[0]["episode_title"] == "Why the Eagles lost"
    assert mine[0]["answers"]["off"] == {"pick": ["slow"]}


def test_a_bad_answer_is_refused_with_a_sentence():
    c = listener("svyben")
    r = c.post("/api/surveys/answer", json={"survey": "day3", "answers": {}})
    assert r.status_code == 400
    body = r.json()
    assert "Not now" in (body.get("detail") or body.get("error") or "")


def test_a_known_date_of_birth_skips_the_age_question():
    assert any(q["id"] == "age" for q in S.definition("day3")["questions"])
    got = S.definition("day3", known={"birth_date": "2001-02-03"})
    assert all(q["id"] != "age" for q in got["questions"])
    assert "skip_if_known" not in str(got)


def test_admin_tallies_and_csv(monkeypatch):
    monkeypatch.setattr(appmod, "_require_admin", lambda request: None)
    c = listener("svycat")
    c.post("/api/surveys/answer", json={"survey": "wrap", "answers": {
        "disappointed": {"pick": "very"}, "routine": {"pick": 8},
        "change": {"text": "More sports"}}})
    wrap = [s for s in c.get("/api/admin/surveys").json()["surveys"] if s["id"] == "wrap"][0]
    q = {x["id"]: x for x in wrap["questions"]}
    assert {o["id"]: o["count"] for o in q["disappointed"]["options"]}["very"] >= 1
    assert q["routine"]["mean"] is not None
    assert any(t["text"] == "More sports" for t in q["change"]["texts"])
    r = c.get("/api/admin/surveys/wrap.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert any(row["user_id"] == uid(c) and row["change"] == "More sports" for row in rows)
    assert c.get("/api/admin/surveys/nope.csv").status_code == 404


def test_deleting_an_account_anonymises_its_answers():
    c = listener("svydan")
    user = uid(c)
    c.post("/api/surveys/answer", json={"survey": "day3",
                                        "answers": {"routine": {"pick": 4}}})
    assert c.request("DELETE", "/api/account").status_code == 200
    rows = appmod.SURVEY_STORE.responses("day3")
    assert not any(x["user_id"] == user for x in rows)
    assert any(x["answers"].get("routine") == {"pick": 4} for x in rows)


# --- the interface ----------------------------------------------------------

def test_the_episode_check_rides_on_whats_next_and_pauses_its_countdown():
    pulse = INDEX.split('id="surveyPulse"', 1)[1].split(">", 1)[0]
    assert "pauseNextUp()" in pulse
    show = INDEX.split("function showNextUp(", 1)[1].split("\n  }\n", 1)[0]
    assert "offerEpisodeSurvey(" in show


def test_every_survey_can_be_put_off():
    assert INDEX.count('onclick="surveyNotNow()"') >= 3


def test_an_app_survey_is_never_offered_over_audio_or_another_card():
    free = INDEX.split("function surveyMomentFree(", 1)[1].split("\n  }\n", 1)[0]
    assert "isPlaying" in free and "modal-overlay.active" in free
    assert "AUTH.authenticated" in free


# --- found on review --------------------------------------------------------

def test_an_answer_of_the_wrong_type_is_a_sentence_not_a_crash():
    for bad in ({"rating": {"pick": {"a": 1}}}, {"rating": {"pick": ["loved"]}},
                {"rating": {"pick": "meh"}, "off": {"pick": [["facts"]]}},
                {"rating": {"pick": "meh", "text": {"x": 1}}}):
        with pytest.raises(S.SurveyError):
            S.clean_answers("episode", bad)
    c = listener("svyeve")
    r = c.post("/api/surveys/answer", json={"survey": "episode",
                                            "answers": {"rating": {"pick": {"a": 1}}}})
    assert r.status_code == 400


def test_a_typed_line_is_never_a_formula_in_the_csv(store):
    store.answer("u", "wrap", {"change": {"text": "=HYPERLINK(\"http://x\")"}}, now=DAY)
    rows = list(csv.DictReader(io.StringIO(store.csv("wrap"))))
    assert rows[0]["change"] == "'=HYPERLINK(\"http://x\")"
    assert S.SurveyStore._cell("More sports") == "More sports"


def test_the_episode_check_never_replaces_an_app_survey_waiting():
    offer = INDEX.split("function offerEpisodeSurvey(", 1)[1].split("\n  }\n", 1)[0]
    assert offer.count('SURVEY.where === "sheet"') == 2
    show = INDEX.split("function showScreen(", 1)[1].split("\n  }\n", 1)[0]
    assert "followSurveyInvite(id)" in show
    swift = (ROOT / "ios" / "FAM" / "FAM" / "Surveys.swift").read_text()
    assert "pulse == nil, sheet == nil, invite == nil" in swift
