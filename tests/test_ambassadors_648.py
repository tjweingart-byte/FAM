"""FAM Ambassadors (PROBLEMS.md §648, the owner's 11.17 notes), the keyboard
put away by a tap off a field, and Instagram's link on the clipboard.

An admin presses "Add new ambassador" and a code is made; the ambassador
enters it in Settings (or on the profile step after being let in), wears
the "FAM Ambassador" tag on their profile, and is on the unlimited plan for
free. A code holds one account; a revoked code takes both back.
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import accounts as accounts_mod
from tests.test_waitlist import PASSWORD, _join, world  # noqa: F401 (fixture)

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
ADMIN = {"X-Admin-Token": "admin-secret"}


@pytest.fixture
def acc(tmp_path):
    return accounts_mod.AccountStore(str(tmp_path / "accounts.db"))


def _account(acc, uid="amb"):
    token, _ = acc.new_session(uid)
    acc.sign_up(uid, f"{uid}@fam.test", PASSWORD)
    return token


# --- the store -------------------------------------------------------------

def test_a_code_makes_an_account_an_unlimited_ambassador(acc):
    token = _account(acc)
    assert acc.listener_for(token).tier == "free"
    made = acc.new_ambassador("Ian")
    assert made["code"].startswith("FAM-") and len(made["code"]) == 13
    # Case, spaces and dashes forgiven; the prefix optional.
    typed = made["code"][4:].lower().replace("-", " ")
    assert acc.redeem_ambassador("amb", typed) == made["code"]
    me = acc.listener_for(token)
    assert me.ambassador and me.tier == "unlimited"
    assert me.as_dict()["ambassador"] is True
    assert acc.account("amb")["plan"] == "unlimited"
    # Entering it again is not an error.
    assert acc.redeem_ambassador("amb", made["code"]) == made["code"]


def test_one_code_one_account_and_one_account_one_code(acc):
    _account(acc, "a")
    _account(acc, "b")
    first, second = acc.new_ambassador("A"), acc.new_ambassador("B")
    acc.redeem_ambassador("a", first["code"])
    with pytest.raises(accounts_mod.AmbassadorError, match="already in use"):
        acc.redeem_ambassador("b", first["code"])
    with pytest.raises(accounts_mod.AmbassadorError, match="already a FAM Ambassador"):
        acc.redeem_ambassador("a", second["code"])


def test_wrong_codes_are_refused_and_counted(acc):
    _account(acc, "guess")
    accounts_mod._AMBASSADOR_MISSES.pop("guess", None)
    for _ in range(accounts_mod.AMBASSADOR_TRIES):
        with pytest.raises(accounts_mod.AmbassadorError, match="isn’t right"):
            acc.redeem_ambassador("guess", "FAM-AAAA-AAAA")
    real = acc.new_ambassador()
    with pytest.raises(accounts_mod.AmbassadorError, match="Too many"):
        acc.redeem_ambassador("guess", real["code"])
    accounts_mod._AMBASSADOR_MISSES.pop("guess", None)


def test_revoking_takes_back_the_tag_and_the_plan(acc):
    token = _account(acc)
    made = acc.new_ambassador()
    acc.redeem_ambassador("amb", made["code"])
    assert acc.revoke_ambassador(made["code"]) == "amb"
    me = acc.listener_for(token)
    assert not me.ambassador and me.tier == "free"
    rows = acc.ambassadors()
    assert rows[0]["revoked"] and rows[0]["user_id"] == "amb"
    with pytest.raises(accounts_mod.AmbassadorError):
        acc.redeem_ambassador("amb", made["code"])
    assert acc.revoke_ambassador("FAM-ZZZZ-ZZZZ") is None


def test_a_deleted_account_leaves_its_code_on_the_list_unheld(acc):
    _account(acc)
    made = acc.new_ambassador("Gone")
    acc.redeem_ambassador("amb", made["code"])
    acc.delete_account("amb")
    row = acc.ambassadors()[0]
    assert row["code"] == made["code"] and row["user_id"] == "" and not row["revoked"]


# --- the endpoints ---------------------------------------------------------

def _member(world, email):
    client, _ = _join(email)
    uid = client.get("/api/auth/me").json()["user_id"]
    world.WAITLIST.grant([uid])
    return client, uid


def test_admin_makes_a_code_and_the_listener_enters_it(world):
    guest = TestClient(world.app)
    assert guest.post("/api/admin/ambassadors", json={"name": "x"}).status_code == 404
    assert guest.get("/api/admin/ambassadors").status_code == 404
    made = guest.post("/api/admin/ambassadors", json={"name": "Ian"}, headers=ADMIN).json()
    client, uid = _member(world, "ian@fam.test")
    r = client.post("/api/ambassador", json={"code": "nope"})
    assert r.status_code == 400
    r = client.post("/api/ambassador", json={"code": made["code"]})
    assert r.status_code == 200 and r.json()["ambassador"] is True
    me = client.get("/api/auth/me").json()
    assert me["ambassador"] is True and me["tier"] == "unlimited"
    listed = guest.get("/api/admin/ambassadors", headers=ADMIN).json()
    assert listed["summary"] == {"codes": 1, "active": 1, "waiting": 0}
    assert listed["rows"][0]["user_id"] == uid and listed["rows"][0]["name"] == "Ian"
    r = guest.post("/api/admin/ambassadors/revoke", json={"code": made["code"]}, headers=ADMIN)
    assert r.status_code == 200
    assert client.get("/api/auth/me").json()["ambassador"] is False


def test_a_guest_cannot_enter_a_code(world):
    made = TestClient(world.app).post("/api/admin/ambassadors", json={}, headers=ADMIN).json()
    r = TestClient(world.app).post("/api/ambassador", json={"code": made["code"]})
    assert r.status_code in (401, 403)


def test_the_admin_page_is_served_and_linked():
    page = (ROOT / "admin_ui" / "ambassadors.html").read_text(encoding="utf-8")
    assert "Add new ambassador" in page and "/api/admin/ambassadors" in page
    assert 'href="/admin/ambassadors"' in (ROOT / "admin_ui" / "tracker.html").read_text()


# --- the app ---------------------------------------------------------------

def test_settings_sign_up_and_both_profiles_carry_it():
    assert 'settingsRow("FAM Ambassador code"' in INDEX
    assert 'id="identityAmbassadorField" data-first-run' in INDEX
    assert "saveIdentityAccount(false).then(saveIdentityAmbassador)" in INDEX
    assert "(AUTH.ambassador ? ambassadorTag() : '')" in INDEX
    assert "(pub.ambassador ? ambassadorTag() : '')" in INDEX


def test_a_tap_off_a_field_puts_the_keyboard_away():
    assert "function isTypingField(el)" in INDEX
    assert "field.blur();" in INDEX
    swift = (ROOT / "ios" / "FAM" / "FAM" / "KeyboardDismiss.swift").read_text()
    assert "cancelsTouchesInView = false" in swift and "endEditing(true)" in swift
    app = (ROOT / "ios" / "FAM" / "FAM" / "FAMApp.swift").read_text()
    assert "KeyboardDismisser.shared.install()" in app


def test_instagram_gets_the_episode_link_for_its_link_sticker():
    assert 'key === "instagram_story"' in INDEX and "copyQuietly(igLink)" in INDEX
    handoff = (ROOT / "ios" / "FAM" / "FAM" / "StoryHandoff.swift").read_text()
    assert 'item["public.utf8-plain-text"] = episode.absoluteString' in handoff
    share = (ROOT / "ios" / "FAM" / "FAM" / "Views" / "SocialShare.swift").read_text()
    assert 'link: key == "instagram_story" ? absoluteLink : nil' in share
