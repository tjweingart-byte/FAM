"""The 10.1 implementations packet, the third set (PROBLEMS.md §184)."""

from __future__ import annotations

import pathlib
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import dataclasses

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import app as appmod  # noqa: E402
import daily_edition  # noqa: E402
import mixes as mixes_mod  # noqa: E402
import push  # noqa: E402
from config import settings  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text()
SW = (ROOT / "static" / "sw.js").read_text()


def _fn(name):
    return INDEX.split("function " + name + "(", 1)[1].split("\n  }\n", 1)[0]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    with TestClient(appmod.app) as c:
        yield c


def signed_in(client, email, name, handle):
    client.post("/api/auth/signup", json={"email": email, "password": "password12"})
    client.post("/api/me", json={"name": name, "handle": handle})
    return client.get("/api/auth/me").json()["user_id"]


def _configure(monkeypatch, **changes):
    """`push` reads settings through `push._settings`; settings are frozen."""
    patched = dataclasses.replace(settings, **changes)
    monkeypatch.setattr(push, "_settings", lambda: patched)


@pytest.fixture
def keys(monkeypatch):
    """A server that can deliver: keys set, pywebpush importable."""
    _configure(monkeypatch, vapid_public_key="BPUBLIC", vapid_private_key="private",
               vapid_subject="mailto:ops@example.com", mix_reminders=True)
    monkeypatch.setitem(sys.modules, "pywebpush", type(sys)("pywebpush"))


# --- 1a. No suggested mixes; the (+) is circled ----------------------------

def test_dailyfam_suggests_no_mix_names():
    body = _fn("renderMixList")
    assert "starter" not in body
    assert "useStarterMix" not in INDEX and "mixStarters" not in INDEX
    assert ".starter-chip" not in INDEX


def test_the_new_mix_plus_is_circled():
    button = INDEX.split('id="newMixBtn"', 1)[1].split("</button>", 1)[0]
    head = INDEX.split('id="newMixBtn"', 1)[0].rsplit("<button", 1)[1]
    assert "new-mix-plus" in head
    assert "<svg" in button
    css = INDEX.split("  .new-mix-plus{", 1)[1].split("}", 1)[0]
    assert "border-radius:50%" in css and "border:1px solid var(--border-strong)" in css
    # Still hidden until /api/mixes answers (`showNewMixButton`).
    assert ".new-mix-plus[hidden]{ display:none; }" in INDEX


def test_old_clients_still_get_starters():
    """The field stays for installed clients; only this page stops drawing it."""
    src = (ROOT / "app.py").read_text()
    assert '"starters": [' in src


# --- 1b. A listen time per mix, and "your mix is ready" --------------------

def test_a_listen_time_is_cleaned_or_refused():
    assert push.clean_listen_at("7:05") == "07:05"
    assert push.clean_listen_at("") == ""
    for bad in ("25:00", "07:61", "seven", "7"):
        with pytest.raises(ValueError):
            push.clean_listen_at(bad)
    assert push.clean_zone("Europe/London") == "Europe/London"
    assert push.clean_zone("Mars/Olympus") == ""


def test_a_mix_keeps_its_listen_time_and_hides_it_from_others(tmp_path):
    store = mixes_mod.MixStore(str(tmp_path / "m.db"))
    mix = store.create("u", "Gym", ["f:nfl"])
    assert mix.listen_at == ""
    store.update("u", mix.id, listen_at="6:30", listen_tz="America/Chicago")
    again = store.get("u", mix.id)
    assert (again.listen_at, again.listen_tz) == ("06:30", "America/Chicago")
    assert again.as_dict()["listen_at"] == "06:30"
    assert "listen_at" not in again.public_dict()
    assert "listen_tz" not in again.public_dict()
    assert [m.id for m in store.with_listen_time()] == [mix.id]
    with pytest.raises(mixes_mod.MixError):
        store.update("u", mix.id, listen_at="99:99")
    store.update("u", mix.id, listen_at="")
    assert store.with_listen_time() == []


def test_the_api_sets_a_listen_time_and_never_shows_it_to_others(client):
    signed_in(client, "a@b.com", "Ann", "ann")
    mix = client.post("/api/mixes", json={"name": "Gym", "topic_ids": ["f:nfl"]}).json()
    res = client.patch(f"/api/mixes/{mix['id']}",
                       json={"listen_at": "07:30", "listen_tz": "America/New_York"})
    assert res.status_code == 200
    assert res.json()["listen_at"] == "07:30"
    bad = client.patch(f"/api/mixes/{mix['id']}", json={"listen_at": "lunch"})
    assert bad.status_code == 400
    other = TestClient(appmod.app)
    with other:
        signed_in(other, "b@b.com", "Bo", "bo")
        seen = other.get(f"/api/mixes/public/{mix['id']}").json()
        assert seen["name"] == "Gym" and "listen_at" not in seen


def test_the_listen_time_never_moves_the_edition():
    """It is when the phone is told, never when episodes are written."""
    src = (ROOT / "daily_edition.py").read_text()
    assert "listen_at" not in src


def _mix_at(tmp_path, listen_at="07:30", zone="America/New_York"):
    store = mixes_mod.MixStore(str(tmp_path / "m.db"))
    mix = store.create("u", "Gym", ["f:nfl"])
    store.update("u", mix.id, listen_at=listen_at, listen_tz=zone)
    return store, store.get("u", mix.id)


def _local(y, mo, d, h, mi, zone="America/New_York"):
    return datetime(y, mo, d, h, mi, tzinfo=ZoneInfo(zone)).timestamp()


def test_due_at_the_listen_time_once_the_edition_is_written(tmp_path, monkeypatch):
    store, mix = _mix_at(tmp_path)
    pushes = push.PushStore(str(tmp_path / "m.db"))
    monkeypatch.setattr(push, "_edition_ready", lambda now: True)
    assert push.due(store, pushes, _local(2026, 10, 2, 7, 29)) == []
    assert [(m.id, d) for m, d in push.due(store, pushes, _local(2026, 10, 2, 7, 31))] \
        == [(mix.id, "2026-10-02")]
    # Past the grace window, a "ready" is noise.
    assert push.due(store, pushes, _local(2026, 10, 2, 7, 30) + push.GRACE_SECONDS + 1) == []


def test_not_due_until_the_edition_is_written(tmp_path, monkeypatch):
    store, _ = _mix_at(tmp_path, listen_at="04:00")
    pushes = push.PushStore(str(tmp_path / "m.db"))
    monkeypatch.setattr(push, "_edition_ready", lambda now: False)
    assert push.due(store, pushes, _local(2026, 10, 2, 4, 5)) == []


def test_the_edition_is_ready_only_when_its_slot_is_built(tmp_path, monkeypatch):
    _configure(monkeypatch, daily_edition=True)
    edition = daily_edition.EditionStore(str(tmp_path / "e.db"))
    monkeypatch.setattr(daily_edition, "store", lambda: edition)
    now = _local(2026, 10, 2, 7, 30)
    slot = daily_edition.slot_id(daily_edition.last_slot(now))
    assert push._edition_ready(now) is False
    edition.claim(slot, now)
    assert push._edition_ready(now) is False
    edition.finish(slot, now, {"detail": "done"})
    assert push._edition_ready(now) is True


def test_the_listen_time_is_the_listeners_own_clock(tmp_path, monkeypatch):
    store, mix = _mix_at(tmp_path, listen_at="08:00", zone="Asia/Tokyo")
    pushes = push.PushStore(str(tmp_path / "m.db"))
    monkeypatch.setattr(push, "_edition_ready", lambda now: True)
    due = push.due(store, pushes, _local(2026, 10, 2, 8, 1, "Asia/Tokyo"))
    assert [(m.id, d) for m, d in due] == [(mix.id, "2026-10-02")]


def test_sent_once_a_day_and_only_where_it_can_be_delivered(tmp_path, monkeypatch, keys):
    store, mix = _mix_at(tmp_path)
    pushes = push.PushStore(str(tmp_path / "m.db"))
    monkeypatch.setattr(push, "_edition_ready", lambda now: True)
    sent = []
    monkeypatch.setattr(push, "_send_webpush",
                        lambda sub, payload, ps: sent.append((sub["endpoint"], payload)) or True)
    now = _local(2026, 10, 2, 7, 31)
    # Nowhere to send: nothing claimed, so subscribing in the window still works.
    assert push.tick(store, pushes, now) == []
    pushes.subscribe("u", {"endpoint": "https://push.example/1",
                           "keys": {"p256dh": "k", "auth": "a"}})
    assert push.tick(store, pushes, now) == [(mix.id, "2026-10-02", 1)]
    assert sent[0][1]["title"] == "Your Gym mix is ready"
    assert sent[0][1]["url"] == f"/?mix={mix.id}"
    assert push.tick(store, pushes, now + 120) == []
    # The next day is a new day.
    assert push.tick(store, pushes, now + 86400) == [(mix.id, "2026-10-03", 1)]


def test_without_keys_nothing_is_sent_and_it_says_why(monkeypatch):
    _configure(monkeypatch, vapid_public_key="", vapid_private_key="")
    state = push.status()
    assert state["available"] is False and "VAPID" in state["reason"]


def test_staging_sends_nothing(monkeypatch, keys):
    import spend_guard
    monkeypatch.setattr(spend_guard, "enabled", lambda: True)
    state = push.status()
    assert state["available"] is False and "staging" in state["reason"]


def test_the_push_endpoints(client, monkeypatch, keys):
    assert client.post("/api/push/subscribe", json={"subscription": {}}).status_code == 401
    state = client.get("/api/push").json()
    assert state["available"] is True and state["public_key"] == "BPUBLIC"
    assert state["subscribed"] is False
    signed_in(client, "a@b.com", "Ann", "ann")
    bad = client.post("/api/push/subscribe", json={"subscription": {"endpoint": "http://x"}})
    assert bad.status_code == 400
    good = client.post("/api/push/subscribe", json={"subscription": {
        "endpoint": "https://push.example/1", "keys": {"p256dh": "k", "auth": "a"}}})
    assert good.status_code == 200
    assert client.get("/api/push").json()["subscribed"] is True
    assert client.delete("/api/push/subscribe").json()["removed"] == 1


def test_subscribing_is_refused_where_it_cannot_deliver(client, monkeypatch):
    _configure(monkeypatch, vapid_public_key="")
    signed_in(client, "a@b.com", "Ann", "ann")
    res = client.post("/api/push/subscribe", json={"subscription": {
        "endpoint": "https://push.example/1", "keys": {"p256dh": "k", "auth": "a"}}})
    assert res.status_code == 409


def test_deleting_an_account_forgets_its_phones(client, monkeypatch):
    me = signed_in(client, "a@b.com", "Ann", "ann")
    appmod.PUSH.subscribe(me, {"endpoint": "https://push.example/1",
                               "keys": {"p256dh": "k", "auth": "a"}})
    assert appmod.erase_listener(me)["push"] == 1
    assert appmod.PUSH.subscriptions(me) == []


def test_the_page_offers_a_listen_time_and_the_worker_shows_it():
    body = _fn("renderMixBody")
    assert "mixWhenHTML(m)" in body
    assert 'type="time"' in _fn("mixWhenHTML")
    assert "listen_tz: listenerZone()" in _fn("setMixListenTime")
    # The server's own sentence when it cannot deliver.
    assert "pushState.reason" in _fn("mixWhenNote")
    assert 'addEventListener("push"' in SW
    assert "showNotification" in SW and 'addEventListener("notificationclick"' in SW


def test_the_env_example_documents_the_push_settings():
    example = (ROOT / ".env.example").read_text()
    for name in ("MIX_REMINDERS=1", "VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", "VAPID_SUBJECT"):
        assert name in example


# --- 2a. Invite new users from the friends page ----------------------------

def test_the_friends_page_invites_new_users():
    screen = INDEX.split('id="screen-friends"', 1)[1].split("</section>", 1)[0]
    assert 'onclick="inviteFriends()"' in screen
    assert "Invite new users" in screen
    # Both "Find new friends" pills open this page.
    assert "openFriends()" in _fn("renderProfile")
    assert "openFriends()" in _fn("renderMyFamFeed")


# --- 2b. No (+) in YourFAM's header ----------------------------------------

def test_the_profile_header_has_no_plus():
    head = _fn("renderProfile").split('document.getElementById("pfTop").innerHTML', 1)[1] \
        .split("// Who you are.", 1)[0]
    assert "openNewChatModal" not in head
    assert 'yfIcon("plus"' not in head
    assert 'onclick="openSettings()"' in head


# --- 2c. Friends with a vibe up come first, and swipe like stories ---------

def test_friends_with_a_vibe_come_first(client):
    me = signed_in(client, "ian@b.com", "Ian", "ian")
    people = {}
    for email, name, handle in (("a@b.com", "Ava", "ava"), ("c@b.com", "Cy", "cy"),
                                ("d@b.com", "Di", "di")):
        other = TestClient(appmod.app)
        with other:
            people[handle] = signed_in(other, email, name, handle)
            other.post("/api/friends/follow", json={"user_id": me})
            if handle == "di":
                other.post("/api/vibe", json={"query": "why bonds move", "minutes": 3,
                                              "title": "Bonds"})
    for handle in ("ava", "cy", "di"):
        client.post("/api/friends/follow", json={"user_id": people[handle]})
    circle = client.get("/api/profile").json()["circle"]
    assert circle[0]["handle"] == "di", "a friend with a vibe was not first"
    assert circle[0]["stories"]
    assert {p["handle"] for p in circle[1:]} == {"ava", "cy"}


def test_a_swipe_goes_to_the_next_friends_vibes():
    assert "function storyFriend(dir)" in INDEX
    swipe = INDEX.split('el.addEventListener("pointerup"', 1)[1].split("});", 1)[0]
    assert "storyFriend(dx < 0 ? 1 : -1)" in swipe
    # A tap still steps through one friend's vibes, and not after a swipe.
    assert 'onclick="storyTap(1)"' in INDEX and 'onclick="storyTap(-1)"' in INDEX
    assert "storySwipedAt" in _fn("storyTap")
    css = INDEX.split("  .story{", 1)[1].split("}", 1)[0]
    assert "touch-action:pan-y" in css


# --- 3a. The picture is the way to the profile -----------------------------

def test_the_vibe_viewer_has_no_view_profile_button():
    overlay = INDEX.split('id="storyOverlay"', 1)[1].split("<!-- A guest tapped", 1)[0]
    assert "story-profile" not in overlay
    assert ">View profile<" not in overlay
    who = overlay.split('class="story-who-btn"', 1)[1].split("</button>", 1)[0]
    assert 'onclick="storyProfile()"' in who
    assert 'id="storyAv"' in who


# --- Review fixes, before merging into Main --------------------------------

def test_no_contact_address_is_invented(monkeypatch, keys):
    _configure(monkeypatch, vapid_public_key="BPUBLIC", vapid_private_key="private",
               vapid_subject="")
    state = push.status()
    assert state["available"] is False and "VAPID_SUBJECT" in state["reason"]
    assert "fam.example" not in (ROOT / "push.py").read_text()


def test_nobody_outside_the_waitlist_is_notified(tmp_path, monkeypatch, keys):
    store, mix = _mix_at(tmp_path)
    pushes = push.PushStore(str(tmp_path / "m.db"))
    pushes.subscribe("u", {"endpoint": "https://push.example/1",
                           "keys": {"p256dh": "k", "auth": "a"}})
    monkeypatch.setattr(push, "_edition_ready", lambda now: True)
    monkeypatch.setattr(push, "_send_webpush", lambda sub, payload, ps: True)
    now = _local(2026, 10, 2, 7, 31)
    assert push.tick(store, pushes, now, allowed=lambda uid: False) == []
    # Not claimed, so once they are let in the same day's still goes out.
    assert push.tick(store, pushes, now + 60, allowed=lambda uid: True) \
        == [(mix.id, "2026-10-02", 1)]


def test_the_app_asks_the_waitlist_who_may_be_notified(monkeypatch):
    src = (ROOT / "app.py").read_text()
    assert "allowed=_may_be_notified" in src
    body = src.split("def _may_be_notified(", 1)[1].split("\n\n\n", 1)[0]
    assert "settings.waitlist" in body and "waitlist_mod.ACTIVE" in body


def test_the_permission_prompt_is_asked_inside_the_tap():
    body = _fn("setMixListenTime")
    assert body.index("askNotificationPermission()") < body.index("saveMix(")
    # And a service worker that never becomes ready cannot hang it.
    assert "setTimeout(function(){ finish(false); }" in _fn("subscribeToPush")


def test_an_empty_time_field_never_clears_the_listen_time():
    assert 'onchange="if(this.value) setMixListenTime(this.value)"' in INDEX


def test_a_notification_tap_only_hands_off_to_the_app_itself():
    assert 'at.pathname === "/"' in SW


def test_invites_carry_the_referral_link_while_the_waitlist_runs():
    load = _fn("loadInviteLink")
    assert "AUTH.waitlist" in load and "/api/waitlist/me" in load
    assert "referral_link" in load
    assert "inviteLink ||" in _fn("inviteFriends")
    assert "loadInviteLink();" in _fn("openFriends")
    # Signing out forgets it, and this browser's push subscription.
    forget = _fn("forgetOfflineCopies")
    assert 'inviteLink = "";' in forget and "unsubscribe()" in forget


def test_the_push_store_closes_its_connections():
    src = (ROOT / "push.py").read_text()
    assert "db.close()" in src
    assert "push_store._connect" not in src
