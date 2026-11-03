"""The activation step (§411): the API before the screen.

Date of birth required and 13 and over; the place and gender optional;
counted by default once the notice is shown, a no kept; an under-13 date
confirmed, then the account deleted; nothing refused to an old client.
"""
from __future__ import annotations

import dataclasses
import datetime
import os
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import accounts as accounts_mod  # noqa: E402
import activation as activation_mod  # noqa: E402
import app as appmod  # noqa: E402
import consent as consent_mod  # noqa: E402
import preferences as prefs_mod  # noqa: E402

PASSWORD = "correct horse battery"
ROOT = pathlib.Path(__file__).resolve().parent.parent


def _years_ago(years: int, days: int = 0) -> str:
    today = datetime.date.today()
    try:
        then = today.replace(year=today.year - years)
    except ValueError:            # 29 February
        then = today.replace(year=today.year - years, day=28)
    return (then - datetime.timedelta(days=days)).isoformat()


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "ACCOUNTS", accounts_mod.AccountStore(str(tmp_path / "a.db")))
    monkeypatch.setattr(appmod, "PREFS", prefs_mod.PreferenceStore(str(tmp_path / "p.db")))
    monkeypatch.setattr(appmod, "CONSENT", consent_mod.ConsentStore(str(tmp_path / "c.db")))
    monkeypatch.setattr(appmod, "settings",
                        dataclasses.replace(appmod.settings, waitlist=False))
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)
    return appmod


def _member(email="m@fam.test"):
    c = TestClient(appmod.app)
    r = c.post("/api/auth/signup", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return c


# --- the rules, in code ---------------------------------------------------------

def test_ages_and_genders():
    today = datetime.date(2026, 11, 3)
    assert accounts_mod.age_on("2013-11-03", today) == 13
    assert accounts_mod.age_on("2013-11-04", today) == 12
    assert accounts_mod.under_age("2013-11-04", today)
    assert not accounts_mod.under_age("2013-11-03", today)
    assert not accounts_mod.under_age("")
    assert accounts_mod.clean_gender(" Woman ") == "woman"
    assert accounts_mod.clean_gender("") == ""
    with pytest.raises(accounts_mod.AuthError):
        accounts_mod.clean_gender("my own words")


def test_needed_until_both_a_date_and_the_notice():
    account = {"status": "active", "birth_date": ""}
    assert activation_mod.needed(account, None)
    assert activation_mod.needed({**account, "birth_date": "2000-01-01"}, None)
    assert activation_mod.needed(account, {"allowed": True, "version": 1, "at": 1})
    assert not activation_mod.needed({**account, "birth_date": "2000-01-01"},
                                     {"allowed": False, "version": 1, "at": 1})
    assert not activation_mod.needed({"status": "waitlisted", "birth_date": ""}, None)
    assert not activation_mod.needed(None, None)


# --- the API ---------------------------------------------------------------------

def test_every_open_says_whether_the_step_is_needed(world):
    c = _member()
    assert c.get("/api/auth/me").json()["activation_needed"] is True
    assert TestClient(appmod.app).get("/api/auth/me").json().get(
        "activation_needed") in (None, False)


def test_the_screen_reads_what_it_draws(world):
    c = _member()
    body = c.get("/api/activation").json()
    assert body["needed"] and body["title"] == activation_mod.TITLE_FIRST
    assert [g["id"] for g in body["genders"]] == list(accounts_mod.GENDERS)
    assert body["min_age"] == 13
    assert body["counting"]["notice"]["body"].startswith("We use your age group")
    assert body["counting"]["asked"] is False
    assert "Shows on your profile" in body["help"]["location"]


def test_finishing_the_step_keeps_the_answers_and_counts_by_default(world):
    c = _member()
    r = c.post("/api/activation", json={"birth_date": "1990-05-04", "gender": "woman",
                                        "city": "Exton", "region": "PA",
                                        "country": "United States"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert not body["needed"] and body["counting"]["counted"] is True
    assert (body["birth_date"], body["gender"]) == ("1990-05-04", "woman")
    assert body["location"]["city"] == "Exton"
    assert c.get("/api/auth/me").json()["activation_needed"] is False
    account = c.get("/api/account").json()
    assert (account["birth_date"], account["gender"], account["counted"]) == (
        "1990-05-04", "woman", True)


def test_the_place_and_gender_are_optional_and_a_date_is_not(world):
    c = _member()
    assert c.post("/api/activation", json={"gender": "man"}).status_code == 422
    assert c.post("/api/activation", json={"birth_date": "1990-05-04"}).status_code == 200
    assert c.post("/api/activation", json={"birth_date": "1990-05-04",
                                           "gender": "something else"}).status_code == 400


def test_settings_turns_counting_off_and_finishing_again_never_undoes_it(world):
    c = _member()
    c.post("/api/activation", json={"birth_date": "1990-05-04"})
    r = c.post("/api/consent", json={"scope": "demographics", "allow": False})
    assert r.status_code == 200 and r.json()["demographics"]["counted"] is False
    c.post("/api/activation", json={"birth_date": "1990-05-04"})    # a new device
    assert c.get("/api/consent").json()["demographics"]["counted"] is False


def test_a_guest_is_never_counted(world):
    guest = TestClient(appmod.app)
    r = guest.post("/api/consent", json={"scope": "demographics", "allow": True})
    assert r.status_code == 401


def test_under_13_is_confirmed_first_then_the_account_is_deleted(world):
    c = _member()
    young = _years_ago(12)
    r = c.post("/api/activation", json={"birth_date": young})
    assert r.status_code == 409
    detail = r.json()
    assert detail["error"] == detail["confirm"]
    assert detail["confirm"].startswith("Is ") and detail["confirm"].endswith(" right?")
    assert detail["confirm_field"] == "confirm_under_13"
    assert appmod.ACCOUNTS.account(c.get("/api/auth/me").json()["user_id"])
    r = c.post("/api/activation", json={"birth_date": young, "confirm_under_13": True})
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert r.json()["message"] == activation_mod.UNDER_AGE
    me = c.get("/api/auth/me").json()
    assert me.get("authenticated") is False


def test_thirteen_today_is_let_in(world):
    c = _member()
    r = c.post("/api/activation", json={"birth_date": _years_ago(13)})
    assert r.status_code == 200 and not r.json()["needed"]


def test_a_correction_under_13_in_settings_follows_the_same_rule(world):
    c = _member()
    c.post("/api/activation", json={"birth_date": "1990-05-04"})
    assert c.post("/api/account", json={"birth_date": _years_ago(10)}).status_code == 409
    r = c.post("/api/account", json={"birth_date": _years_ago(10),
                                     "confirm_under_13": True})
    assert r.json()["deleted"] is True


def test_once_in_a_date_of_birth_is_corrected_never_removed(world):
    c = _member()
    c.post("/api/activation", json={"birth_date": "1990-05-04"})
    assert c.post("/api/account", json={"birth_date": ""}).status_code == 400
    assert c.post("/api/account", json={"birth_date": "1991-05-04"}).status_code == 200
    assert c.post("/api/account", json={"gender": "prefer-not-to-say"}).json()[
        "gender"] == "prefer-not-to-say"


def test_deleting_the_account_removes_the_new_answers(world):
    c = _member()
    c.post("/api/activation", json={"birth_date": "1990-05-04", "gender": "woman"})
    user = c.get("/api/auth/me").json()["user_id"]
    assert c.delete("/api/account").status_code == 200
    assert appmod.ACCOUNTS.account(user) is None
    assert appmod.CONSENT.get(user, consent_mod.DEMOGRAPHICS) is None


def test_an_old_client_is_refused_nothing(world):
    """Nothing is gated on the step: a client that never shows it keeps
    working, and its listener is simply never counted."""
    c = _member()
    assert c.get("/api/account").status_code == 200
    assert c.get("/api/preferences").status_code == 200
    assert c.get("/api/consent").json()["demographics"]["counted"] is False


def test_the_privacy_policy_says_what_the_step_does():
    page = (ROOT / "pages" / "privacy.html").read_text(encoding="utf-8")
    for words in ("Totals about groups of listeners", "Only groups of 100 or more",
                  "Count me in FAM's totals", "we delete their account",
                  "Your date of birth and gender are sent to none of these companies"):
        assert words in page, words
    assert appmod.PRIVACY_UPDATED == "3 November 2026"
    assert consent_mod.TERMS_VERSION == 2            # decision 6


def test_the_screen_s_one_place_box_is_split_like_any_typed_place(world):
    """§414: the activation screen has one box; "Exton, PA" is a town and a
    state, as a typed place is everywhere (`places.split_name`)."""
    c = _member()
    body = c.post("/api/activation", json={"birth_date": "1990-05-04",
                                           "city": "Exton, PA"}).json()
    assert (body["location"]["city"], body["location"]["region"]) == ("Exton", "Pennsylvania")
    body = c.post("/api/activation", json={"birth_date": "1990-05-04",
                                           "city": "Lyon"}).json()
    assert body["location"]["city"] == "Lyon"


def test_the_web_app_draws_the_step_and_settings_rows():
    page = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert 'id="activationOverlay"' in page
    assert ".then(checkActivation)" in page                 # every open
    assert "releaseActivation(id)" in page                  # held through sign-up
    assert 'scope: "demographics"' in page                  # Settings' switch
    assert "Count me in FAM\\u2019s totals" in page
    assert '"Date of birth"' in page and '"Gender"' in page
