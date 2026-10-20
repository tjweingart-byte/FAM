"""The 10.8 packet (PROBLEMS.md §291)."""
from __future__ import annotations

import json
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import social as social_mod
from tests.test_waitlist import _join, world  # noqa: F401

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
WAITLIST_PAGE = (ROOT / "static" / "waitlist.html").read_text(encoding="utf-8")


def _found(client, term):
    r = client.get("/api/waitlist/people", params={"q": term})
    assert r.status_code == 200, r.text
    return [(p["name"], p["handle"]) for p in r.json()["people"]]


# --- the waitlist's friend search, checked -----------------------------------

def test_a_handle_with_an_underscore_is_found_as_typed(world):
    tj, _ = _join("tj@fam.test")
    tj.post("/api/me", json={"name": "TJ Weingart", "handle": "tj_weingart"})
    me, _ = _join("me@fam.test")
    # "tj_w" used to look for "tjw": the underscore was stripped, not escaped.
    for term in ("tj_w", "tj_weingart", "@tj_weingart", "@TJ_W"):
        assert _found(me, term) == [("TJ Weingart", "tj_weingart")], term
    # And an underscore is a letter, never "any one character".
    assert _found(me, "tjxw") == []


def test_like_wildcards_are_literal():
    assert social_mod.like_literal("a_b%c!") == "a!_b!%c!!"


def test_a_handle_typed_with_its_at_spends_no_exact_lookup(world):
    brady, _ = _join("brady@fam.test")
    brady.post("/api/me", json={"name": "Brady", "handle": "brady"})
    me, _ = _join("me@fam.test")
    for _ in range(30):
        assert _found(me, "@brady") == [("Brady", "brady")]


def test_a_number_without_its_code_is_tried_in_the_searchers_country(world):
    ollie, _ = _join("ollie@fam.test")
    ollie.post("/api/me", json={"name": "Ollie", "handle": "ollie"})
    assert ollie.post("/api/account", json={"phone": "+447700900123"}).status_code == 200
    me, _ = _join("me@fam.test")
    me.post("/api/account", json={"phone": "+447700900999"})
    assert _found(me, "07700 900123") == [("Ollie", "ollie")]
    # A US searcher still finds a US number typed without +1.
    us, _ = _join("us@fam.test")
    us.post("/api/me", json={"name": "Sam", "handle": "sam"})
    us.post("/api/account", json={"phone": "+15551234567"})
    assert _found(me, "+1 555 123 4567") == [("Sam", "sam")]


def test_a_name_saved_by_skipping_is_found_by_name_email_and_phone(world):
    nora, _ = _join("nora@fam.test")
    r = nora.post("/api/account", json={"display_name": "Nora Quinn", "phone": "+15550001111"})
    assert r.status_code == 200
    me, _ = _join("me@fam.test")
    for term in ("nora", "Quinn", "nora@fam.test", "(555) 000-1111"):
        assert _found(me, term) == [("Nora Quinn", "")], term
    # Once a handle goes with the name, they are found once, by it.
    nora.post("/api/me", json={"name": "Nora Quinn", "handle": "noraq"})
    assert _found(me, "nora") == [("Nora Quinn", "noraq")]


def test_an_account_already_in_is_not_found_by_its_account_name(world):
    inside, _ = _join("inside@fam.test")
    inside.post("/api/account", json={"display_name": "Ivy Inside"})
    world.ACCOUNTS._conn().execute(
        "UPDATE accounts SET status = 'active' WHERE email = ?", ("inside@fam.test",))
    me, _ = _join("me@fam.test")
    assert _found(me, "ivy") == []


# --- the waitlist's profile ---------------------------------------------------

def test_skip_waits_for_a_name_and_a_phone_number():
    bar = WAITLIST_PAGE.split('<div class="top setup-only">', 1)[1].split("</div>\n  <div", 1)[0]
    assert 'id="skipSetup" aria-disabled="true" onclick="skipSetup()"' in bar
    assert '.skip-btn[aria-disabled="true"]' in WAITLIST_PAGE
    skip = WAITLIST_PAGE.split("window.skipSetup = function", 1)[1].split("\n  };", 1)[0]
    assert skip.index("if(missingRequired().length){ askForRequired(); return; }") \
        < skip.index('api("/api/account"')
    assert "display_name:" in skip and "phone: typedPhone()" in skip
    required = WAITLIST_PAGE.split("function missingRequired(){", 1)[1].split("\n  }", 1)[0]
    assert '$("pName")' in required and "phoneDigits().length < 7" in required
    # The popup: the one line and an X, and nothing else to press.
    pop = WAITLIST_PAGE.split('id="reqPop"', 1)[1].split("</div>\n</div>", 1)[0]
    assert "Please fill out required fields before continuing" in pop
    assert pop.count("<button") == 1 and 'aria-label="Close"' in pop


def test_birth_date_is_typed_day_month_year():
    field = WAITLIST_PAGE.split('id="pBirth"', 1)[1].split(">", 1)[0]
    assert 'type="text"' in field and 'placeholder="DD/MM/YYYY"' in field
    assert 'type="date"' not in WAITLIST_PAGE
    assert "function dmyToIso(" in WAITLIST_PAGE and "function isoToDmy(" in WAITLIST_PAGE
    save = WAITLIST_PAGE.split("window.saveProfile = function", 1)[1].split("\n  };", 1)[0]
    assert "birth_date: birth" in save


def test_country_and_state_are_lists_from_places_json():
    places = json.loads((ROOT / "static" / "places.json").read_text(encoding="utf-8"))
    names = dict((code, name) for name, code in places["countries"])
    assert names["US"] == "United States" and names["GB"] == "United Kingdom"
    assert len(places["regions"]["US"]) >= 50 and "California" in places["regions"]["US"]
    assert places["regions"]["GB"] == ["England", "Northern Ireland", "Scotland", "Wales"]
    assert "Ontario" in places["regions"]["CA"]
    for field in ("pCountry", "pRegion"):
        tag = WAITLIST_PAGE.split(f'id="{field}"', 1)[1].split(">", 1)[0]
        assert 'role="combobox"' in tag
    # The city stays typed.
    assert 'role="combobox"' not in WAITLIST_PAGE.split('id="pCity"', 1)[1].split(">", 1)[0]
    assert 'fetch("/places.json"' in WAITLIST_PAGE


def test_no_photo_shows_a_camera():
    assert '<div class="av" id="photoPreview"></div>' in WAITLIST_PAGE
    draw = WAITLIST_PAGE.split("function drawPhoto(){", 1)[1].split("\n  }", 1)[0]
    assert ": CAMERA_IC;" in draw and "initials(" not in draw


# --- the app's sign-up profile -------------------------------------------------

def test_the_apps_first_run_asks_for_a_phone_and_gates_skip():
    screen = INDEX.split('<section class="screen" id="screen-identity">', 1)[1].split("</section>", 1)[0]
    assert 'id="identityPhoneField" data-first-run' in screen
    assert 'id="identitySkip" role="button" aria-disabled="true"' in screen
    skip = INDEX.split("function skipIdentity(){", 1)[1].split("\n  }", 1)[0]
    assert skip.index("showIdentityRequired()") < skip.index("saveIdentityAccount(true)")
    save = INDEX.split("function saveIdentity(){", 1)[1].split("\n  function ", 1)[0]
    assert "if(identityMissing().length){ showIdentityRequired(); return; }" in save
    # Edit profile asks for neither: `identityMissing` is the first run's.
    assert 'if(identityMode === "edit") return [];' in INDEX
    pop = INDEX.split('id="identityReqPop"', 1)[1].split("</div>\n    </div>", 1)[0]
    assert "Please fill out required fields before continuing" in pop
    assert pop.count("<button") == 1


# --- Spotify's bar -------------------------------------------------------------

def test_actions_are_confirmed_by_the_bar_with_somewhere_to_go():
    assert 'id="snack"' in INDEX and "function snack(msg, action){" in INDEX
    for line in ('snack("Added to Queue", { label: "Open", run: openQueue });',
                 'snack("Removed from Queue", { label: "Undo"',
                 'snack("Saved for Later", { label: "Open", run: openSaved });',
                 'snack("Vibed to your story", { label: "View"',
                 'snack("Added to " + saved.name, { label: "Open"'):
        assert line in INDEX, line
    for gone in ('toast("Added to your queue")', "VIBE! — posted to your story",
                 'toast("Saved for later \\u2713")'):
        assert gone not in INDEX, gone
    # Both themes give it its own colours, the reverse of the page's.
    assert INDEX.count("--snack-bg:") == 2
