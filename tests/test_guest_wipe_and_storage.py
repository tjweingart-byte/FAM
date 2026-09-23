"""The 20/09 packet's other four: the guest boundary, sharing, the seed, the disk.

Four complaints with nothing in common except that each was invisible from
inside the app:

* the profile page drew a full profile for somebody with no account, and
  every gate's Sign up button opened a makeshift two-modal form rather than
  the screen the app opens on;
* a share link "does not work" - it was relative, because nothing prompts
  for `PUBLIC_BASE_URL` and so no deployment had one;
* seeded demonstration data could not be taken back out, so the
  recommendations could not be measured;
* the accounts are wiped on every deploy, and nothing said why.
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    for var, name in (("CACHE_PATH", "scripts"), ("MYFAM_DB", "myfam"),
                      ("MIXES_DB", "mixes"), ("SOCIAL_DB", "social"),
                      ("ATTACHMENTS_PATH", "attachments"),
                      ("ACCOUNTS_DB", "accounts"), ("PREFS_DB", "prefs"),
                      ("METERING_DB", "metering"), ("MESSAGES_DB", "messages"),
                      ("SAVED_DB", "saved"), ("SHARES_DB", "shares"),
                      ("QUOTAS_DB", "quotas")):
        monkeypatch.setenv(var, str(tmp_path / f"{name}.db"))
    import importlib

    import app as app_mod
    importlib.reload(app_mod)
    with TestClient(app_mod.app) as c:
        yield c


# --------------------------------------------------------------------------
# A share link that leaves the machine
# --------------------------------------------------------------------------
def test_a_share_link_is_absolute_without_anything_being_configured(client):
    """The reported bug. `PUBLIC_BASE_URL` is not prompted for anywhere, so
    no deployment had it, so every link was `/s/abc123` - correct, relative
    and useless in a message."""
    body = client.post("/api/share",
                       json={"query": "what happened with the fed", "minutes": 3},
                       headers={"host": "fam.example.com",
                                "x-forwarded-proto": "https"}).json()
    assert body["url"].startswith("https://fam.example.com/s/")
    assert body["public"] is True


def test_a_shared_link_opens_the_episode_it_names(client):
    """End to end, which is the only way this could have been caught: the
    share row holds the question and the length, and the landing page asks
    for exactly those - so the recipient gets the sharer's own script out of
    the shared cache."""
    made = client.post("/api/share",
                       json={"query": "the chip export rules", "minutes": 3,
                             "title": "Chips"},
                       headers={"host": "fam.example.com",
                                "x-forwarded-proto": "https"}).json()
    share_id = made["share"]["id"]
    page = client.get(f"/s/{share_id}")
    assert page.status_code == 200
    assert "the chip export rules" in page.text

    payload = client.get(f"/api/share/{share_id}").json()
    assert payload["question"] == "the chip export rules"
    assert payload["minutes"] == 3
    # No listener id ever reaches a stranger. This is the one response in the
    # app handed to people who are not listeners.
    assert "user_id" not in payload


def test_the_story_card_is_reachable_from_the_link_it_came_with(client):
    """Instagram and Snapchat cannot carry a link as text, so the card is the
    share. The client rasterises it; the server has to serve it."""
    made = client.post("/api/share", json={"query": "q", "minutes": 3},
                       headers={"host": "fam.example.com",
                                "x-forwarded-proto": "https"}).json()
    assert made["card"].startswith("https://fam.example.com/")
    card = client.get(made["card"])
    assert card.status_code == 200
    assert card.headers["content-type"].startswith("image/svg+xml")
    # Self-contained, or `canvas.toBlob` throws on the browser this feature is
    # mostly used from. No external font, no external image.
    assert "<image" not in card.text
    assert "@import" not in card.text


def test_a_host_that_is_not_a_host_gets_no_link(client):
    """`Host` is client-supplied, and one of the places the derived link
    lands is the `og:url` of a page served with `Cache-Control: public`.

    `html.escape` already stops that becoming markup. This stops it becoming
    a *different URL*: `evil.com/path?` and `good.com@evil.com` are both
    legal header values and neither is a host. Refused rather than
    sanitised - a host this server does not recognise is one it should not
    be naming in a link at all.
    """
    for hostile in ("evil.com/path?x=", "good.com@evil.com", "a b.com",
                    "evil.com#", "localhost:8000", "fam.local"):
        body = client.post("/api/share", json={"query": "q", "minutes": 3},
                           headers={"host": hostile}).json()
        assert body["public"] is False, f"{hostile!r} produced a link"
        assert body["url"].startswith("/s/")


def test_an_ipv6_host_still_gets_a_link(client):
    """The shape check must not refuse a real address. A bracketed literal is
    what a browser sends for one."""
    body = client.post("/api/share", json={"query": "q", "minutes": 3},
                       headers={"host": "[2001:db8::1]:8000",
                                "x-forwarded-proto": "https"}).json()
    assert body["url"].startswith("https://[2001:db8::1]:8000/s/")


def test_health_says_where_a_link_gets_its_host(client):
    """Three states that are indistinguishable from outside, and only one of
    them used to exist."""
    body = client.get("/api/health",
                      headers={"host": "fam.example.com",
                               "x-forwarded-proto": "https"}).json()["sharing"]
    assert body["link_host"] == "request"
    assert body["link_base"] == "https://fam.example.com"
    local = client.get("/api/health",
                       headers={"host": "localhost:8000"}).json()["sharing"]
    assert local["link_host"] == "none", (
        "a link to localhost resolves on the recipient's own machine")


# --------------------------------------------------------------------------
# What a guest can reach
# --------------------------------------------------------------------------
def test_the_profile_needs_an_account_and_the_page_says_so():
    """The whole Profile tab is a door for somebody with no account, and it
    makes no request for a profile it is not going to draw."""
    page = (_interface())
    assert "renderProfileGate" in page
    assert 'if(!AUTH.authenticated){ renderProfileGate(); return; }' in page


def test_every_account_gate_opens_the_real_sign_up_screen():
    """They used to open two chained modals asking for an address and then a
    password - a form that cannot offer a phone number, Google or Apple, so a
    listener who reached an account from DailyFAM and one who reached it from
    the front door were shown two different products."""
    page = _interface()
    assert "function gateActions()" in page
    # Deleted, not merely unused: a second sign-up form left standing is one
    # somebody wires a new gate to by accident.
    assert "function createAccount()" not in page
    assert "function signIn()" not in page
    assert "askEmailAndPassword" not in page


def test_the_welcome_screen_offers_to_continue_as_a_guest():
    """It said "Skip for now", which is what the two setup steps behind it
    still say. This one is not a postponement - it is a way of using the
    app."""
    page = _interface()
    assert "Continue as guest</div>" in page


def test_edit_profile_no_longer_asks_which_interests_are_shared():
    """The old shared-or-hidden block is gone, and so is the profile's own
    chooser that replaced it: YourFAM's Edit profile chooses them now."""
    page = _interface()
    # The heading itself is gone; the comment explaining where it went stays.
    assert '<div class="set-group">Shared on your profile</div>' not in page
    assert 'id="identityShared"' not in page
    assert "function renderSharedInterests" not in page
    assert "function toggleSharedInterest" not in page
    assert "function openProfileInterests" not in page
    assert 'id="identityInterests"' in page


def _interface() -> str:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "static", "index.html"), encoding="utf-8") as fh:
        return fh.read()


# --------------------------------------------------------------------------
# The interests the profile publishes
# --------------------------------------------------------------------------
def test_the_profile_returns_the_row_and_the_choices(client):
    client.post("/api/auth/signup",
                json={"email": "a@b.com", "password": "password1234"})
    client.post("/api/preferences",
                json={"interests": ["tech", "sports"], "topics": ["Formula 1"]})
    body = client.get("/api/profile").json()
    assert body["interests_max"] == 5
    assert len(body["interests_shown"]) <= 4
    assert body["interests_source"] in ("top", "pinned")
    # The editor needs the whole ranked list, not just the four on screen.
    assert len(body["interests_ranked"]) >= len(body["interests_shown"])


def test_somebody_elses_profile_never_publishes_what_they_listened_to(client):
    """The rule this endpoint exists under. Their own page may be ranked off
    their listening; a stranger's view of it may not, because an inferred
    pill row reads as a statement they made."""
    client.post("/api/auth/signup",
                json={"email": "them@b.com", "password": "password1234"})
    client.post("/api/me", json={"name": "Them", "handle": "them"})
    # Declared one thing, listened to another.
    client.post("/api/preferences", json={"interests": ["tech"]})
    client.post("/api/event", json={"kind": "complete", "topic_id": "nfl-trade",
                                    "text": "the nfl trade deadline"})

    client.cookies.clear()
    client.post("/api/auth/signup",
                json={"email": "me@b.com", "password": "password1234"})
    seen = client.get("/api/person", params={"handle": "them"}).json()
    assert "tech" in seen["interests"]
    assert "sports" not in seen["interests"], (
        "a stranger was shown what this listener has been playing")


def test_a_pinned_row_is_what_other_people_see(client):
    """A pin is a statement somebody made, which is the only kind of thing
    this endpoint publishes."""
    client.post("/api/auth/signup",
                json={"email": "them@b.com", "password": "password1234"})
    client.post("/api/me", json={"name": "Them", "handle": "them2"})
    client.post("/api/preferences", json={"interests": ["tech", "money"],
                                          "profile_interests": ["money"]})
    client.cookies.clear()
    client.post("/api/auth/signup",
                json={"email": "me@b.com", "password": "password1234"})
    seen = client.get("/api/person", params={"handle": "them2"}).json()
    assert seen["interests"] == ["money"]


# --------------------------------------------------------------------------
# Taking the seed back out
# --------------------------------------------------------------------------
def test_the_wipe_knows_exactly_which_listeners_the_seed_invents():
    """Two hand-written lists agreeing with each other is the same mistake
    made twice and then compared to itself (§107). This one is derived."""
    import demo_data
    from tools import seed_demo, wipe_demo_data

    assert wipe_demo_data.SEED_USER_IDS == tuple(
        user_id for user_id, _name, _handle in seed_demo.LISTENERS)
    assert demo_data.SEED_USER_IDS == wipe_demo_data.SEED_USER_IDS


def test_a_wipe_does_nothing_without_being_told_to(client):
    from tools import wipe_demo_data

    report = wipe_demo_data.wipe("all", dry_run=True)
    assert report["dry_run"] is True
    assert "events_removed" not in report, "a dry run removed something"


def test_the_admin_wipe_does_not_exist_without_a_token(client):
    """404 rather than 401, like `/api/usage`: an unconfigured deployment
    should not advertise that it has a delete endpoint at all."""
    assert client.post("/api/admin/wipe", json={"scope": "all"}).status_code == 404


def test_the_admin_wipe_defaults_to_a_dry_run(client, monkeypatch):
    import app as app_mod

    monkeypatch.setattr(app_mod, "ADMIN_TOKEN", "secret")
    body = client.post("/api/admin/wipe", json={"scope": "all"},
                       headers={"X-Admin-Token": "secret"}).json()
    assert body["dry_run"] is True, (
        "a request body that forgot a field emptied the event log")


def test_wiping_everything_empties_the_log_and_the_cache(client, monkeypatch):
    import app as app_mod

    monkeypatch.setattr(app_mod, "ADMIN_TOKEN", "secret")
    # An account's event: a guest's is never written (§127).
    client.post("/api/auth/signup", json={"email": "wipe@fam.test",
                                          "password": "a-long-enough-password"})
    client.post("/api/event", json={"kind": "play", "topic_id": "ai-agents",
                                    "text": "ai agents"})
    assert app_mod.EVENTS.count() > 0
    body = client.post("/api/admin/wipe",
                       json={"scope": "all", "dry_run": False},
                       headers={"X-Admin-Token": "secret"}).json()
    assert body["events_removed"] >= 1
    assert app_mod.EVENTS.count() == 0


def test_a_seed_wipe_leaves_other_listeners_alone(client, monkeypatch):
    """The default scope is the seed and only the seed."""
    import app as app_mod

    monkeypatch.setattr(app_mod, "ADMIN_TOKEN", "secret")
    client.post("/api/event", json={"kind": "play", "topic_id": "ai-agents",
                                    "text": "ai agents"})
    before = app_mod.EVENTS.count()
    client.post("/api/admin/wipe", json={"scope": "seed", "dry_run": False},
                headers={"X-Admin-Token": "secret"})
    assert app_mod.EVENTS.count() == before


def test_a_full_wipe_takes_the_vocabulary_the_log_taught(client, monkeypatch):
    """The grown vocabulary is minted *from* the event log, so it cannot
    outlive it.

    `taste` re-reads an event's own text against the current tree. A tree
    left standing after a wipe is therefore a vocabulary with nothing left to
    say it about - and it would go on ranking a blank-slate feed on subjects
    minted from episodes nobody can play any more, with nothing on the
    outside saying so.

    **What comes back is the declared floor**, and that is this rule read the
    other way round rather than an exception to it: what a wipe takes is what
    the *log* taught, and `category_seed.py` was never taught by anything. A
    deployment with no listening is exactly the deployment that seed exists
    for, so leaving it out would make a wipe destructive of something no
    listener produced - and the next boot would put it back anyway, so the
    only difference would be which page saw the tree half-built.
    """
    import app as app_mod
    import categories as categories_mod
    import category_seed
    import topics as topics_mod

    monkeypatch.setattr(app_mod, "ADMIN_TOKEN", "secret")
    tree = topics_mod.category_tree()
    tree.mint("cincinnati bengals", parent_id="", source="test")
    learned = categories_mod.seed_report(tree)["learned"]
    assert learned >= 1, "could not mint a node to test with"

    body = client.post("/api/admin/wipe",
                       json={"scope": "all", "dry_run": False},
                       headers={"X-Admin-Token": "secret"}).json()
    assert body["categories_dropped"] >= 1, (
        "a full wipe reported nothing removed from the vocabulary")

    after = categories_mod.seed_report(topics_mod.category_tree())
    assert after["learned"] == 0, (
        "the vocabulary survived the wipe of the log it was minted from")
    assert after["seeded"] == len(category_seed.rows()), (
        "the wipe took the declared floor with what the log taught")
    assert body["categories_seeded"] == len(category_seed.rows())


def test_a_full_wipe_drops_the_tree_this_worker_is_holding(client, monkeypatch):
    """`topics.category_tree` caches the store per process. A cleared table
    read through a warm handle is a wipe that reports success and changes
    nothing until the next restart - which is the silent half-failure this
    whole module is written against."""
    import app as app_mod
    import topics as topics_mod

    monkeypatch.setattr(app_mod, "ADMIN_TOKEN", "secret")
    held = topics_mod.category_tree()
    client.post("/api/admin/wipe", json={"scope": "all", "dry_run": False},
                headers={"X-Admin-Token": "secret"})
    assert topics_mod.category_tree() is not held, (
        "the wipe left the previous tree object in the module cache")


def test_a_seed_wipe_leaves_the_vocabulary_alone(client, monkeypatch):
    """It is the *log* that pays for the vocabulary, and a seed wipe does not
    empty the log. Removing three invented listeners is not a reason to
    forget what every real one has searched for."""
    import app as app_mod
    import topics as topics_mod

    monkeypatch.setattr(app_mod, "ADMIN_TOKEN", "secret")
    topics_mod.category_tree().mint("home espresso", parent_id="", source="test")
    client.post("/api/admin/wipe", json={"scope": "seed", "dry_run": False},
                headers={"X-Admin-Token": "secret"})
    assert topics_mod.category_tree().nodes(), (
        "a seed wipe emptied the whole vocabulary")


def test_the_dry_run_counts_the_vocabulary_too(client, monkeypatch):
    """The useful half of the answer is the count, and this is the one item
    on the list nobody expects to be on it."""
    import app as app_mod

    monkeypatch.setattr(app_mod, "ADMIN_TOKEN", "secret")
    body = client.post("/api/admin/wipe", json={"scope": "all"},
                       headers={"X-Admin-Token": "secret"}).json()
    assert "categories_total" in body
    assert "categories_dropped" not in body, "a dry run removed something"


# --------------------------------------------------------------------------
# Whether a redeploy erases the listeners
# --------------------------------------------------------------------------
def test_health_measures_where_each_database_lives(client):
    """Measured rather than configured - §52 applied to durability. A store
    pointed at `/data` on a host with no disk attached reports `image`, which
    is exactly the case a settings check cannot see."""
    body = client.get("/api/health").json()
    assert body["storage"]["note"]
    for entry in body["databases"]:
        assert entry["persistence"] in ("disk", "image", "memory", "unknown")
        # Whether somebody *told* this machine where to put it. That pair -
        # ephemeral and configured - is the whole diagnosis.
        assert "configured" in entry


def test_the_doctor_separates_a_laptop_from_a_broken_deployment(client):
    """A warning every developer sees on every run is a warning nobody reads,
    which is how this one got missed in the first place."""
    from tools import storage_doctor

    report = storage_doctor._local_report()
    configured_and_ephemeral = [
        d["name"] for d in report["databases"]
        if d.get("persistence") == "image" and d.get("configured")]
    # The fixture sets every path explicitly, so this is the deployment-shaped
    # case: told where to go, and still inside the code's filesystem.
    assert configured_and_ephemeral, "the doctor cannot see the case it exists for"
    assert storage_doctor._render(report) == 1
