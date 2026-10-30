"""App Store Connect's text is pasted from appstore/listing.json, so it is held here.

App Store Connect counts characters and refuses a field one over; Apple's
review refuses a listing that promises what the build lacks (2.3) or says
"beta" (2.2). Both are cheaper to fail here than in a review round.
"""
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
LISTING = json.loads((ROOT / "appstore" / "listing.json").read_text())

#: Apple's limits, as App Store Connect shows them under each field.
LIMITS = {
    "name": 30,
    "subtitle": 30,
    "promotional_text": 170,
    "description": 4000,
    "keywords": 100,
    "copyright": 200,
}

#: TestFlight's Test Information, Beta App Review and per-build fields.
TESTFLIGHT_LIMITS = {"beta_app_description": 4000, "what_to_test": 4000}

TESTFLIGHT = LISTING["testflight"]


def _text():
    return "\n".join(LISTING[k] for k in LIMITS)


def test_every_field_fits_apples_limit():
    for field, limit in LIMITS.items():
        assert 0 < len(LISTING[field]) <= limit, (field, len(LISTING[field]))


def test_keywords_waste_nothing():
    # Commas with no spaces, no repeats, and nothing the name or subtitle
    # already indexes - each would spend characters on no new searches.
    words = LISTING["keywords"].split(",")
    assert all(w == w.strip() and w for w in words)
    assert len(words) == len(set(words))
    indexed = set(re.findall(r"[a-z]+", (LISTING["name"] + " " + LISTING["subtitle"]).lower()))
    assert not indexed & set(words), indexed & set(words)


def test_the_listing_promises_nothing_the_build_lacks():
    text = _text().lower()
    # APP_STORE.md, "What the iOS build must have": no beta wording, no
    # prices, no other apps' names, no "hey FAM", no live scores, no pushes;
    # phone sign-up is left off the 1.0 build (gap 12).
    for phrase in ("beta", "prototype", "demo", "$", "free trial", "subscription",
                   "best ", "spotify", "podcasts app", "hey fam", "live score",
                   "notification", "phone number"):
        assert phrase not in text, phrase


def test_the_links_are_the_pages_the_server_serves():
    app = (ROOT / "app.py").read_text()
    home = "https://familiarize.net"
    assert LISTING["marketing_url"] == home
    for field, path in (("support_url", "/support"), ("privacy_policy_url", "/privacy")):
        assert LISTING[field] == home + path
        assert f'@app.get("{path}"' in app
    assert home + "/terms" in LISTING["description"]


def test_the_copyright_names_the_seller():
    assert re.fullmatch(r"20\d\d Apali LLC", LISTING["copyright"])


def test_testflight_fields_fit_apples_limit():
    for field, limit in TESTFLIGHT_LIMITS.items():
        assert 0 < len(TESTFLIGHT[field]) <= limit, (field, len(TESTFLIGHT[field]))
    assert 0 < len(TESTFLIGHT["beta_review"]["notes"]) <= 4000


def test_testflight_gives_the_reviewer_what_external_review_asks_for():
    # APP_STORE.md Part E: privacy policy, feedback email, and a demo account
    # that is active and unlimited, so the reviewer never meets the waitlist.
    assert TESTFLIGHT["privacy_policy_url"] == LISTING["privacy_policy_url"]
    assert TESTFLIGHT["feedback_email"]
    review = TESTFLIGHT["beta_review"]
    assert review["sign_in_required"] and review["username"] and review["password"]
    assert "active and unlimited" in review["username"]
    notes = review["notes"]
    # 5.1.2(i) and 1.2: say it is AI, that consent is asked, how to report
    # and block, and where account deletion is (5.1.1(v)).
    for needed in ("written by AI", "asks permission", "reported", "blocked",
                   "Delete account", "/terms"):
        assert needed in notes, needed


def test_no_real_credential_is_committed():
    # The demo account's details are typed into App Store Connect by the
    # owner; this file only holds where they go (credentials-fetched).
    review = TESTFLIGHT["beta_review"]
    for field in ("username", "password", "contact"):
        assert re.fullmatch(r"(<[^<>]+>[ ,]*)+", review[field]), field
    assert TESTFLIGHT["feedback_email"].startswith("<")


def test_the_reviewer_is_pointed_at_screens_that_exist():
    page = (ROOT / "static" / "index.html").read_text()
    for label in ("Delete account", "Blocked people"):
        assert label in page, label
