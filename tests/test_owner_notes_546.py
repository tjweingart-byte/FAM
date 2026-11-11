"""The owner's 11.11 notes (§546).

1. Back an episode in a myFAM playlist, not only forward.
2. A vibe made mid-playlist holds the playlist: an episode that ends while the
   editor is open waits for it to close before the next one starts.
3. The screen that shows "myFAM" is two tabs, Mixes | Categories.
4. A dot on the myFAM tab in the morning, when the edition has written a mix
   the listener has not played since (`/api/mixes/fresh`).
5. The pop-up messages are a floating card, not a line on a fade of the
   page's colour (which was a white band over the dark vibe viewer).
"""
from __future__ import annotations

import os
import pathlib
import re
import sys
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import daily_edition  # noqa: E402
import mixes as M  # noqa: E402
import saved as saved_mod  # noqa: E402

PASSWORD = "a-long-enough-password"
INDEX = (pathlib.Path(__file__).resolve().parent.parent / "static" / "index.html").read_text()


def _fn(name: str) -> str:
    return INDEX.split("  function " + name + "(", 1)[1].split("\n  }\n", 1)[0]


# --- 1. back an episode ------------------------------------------------------


def test_back_sits_in_the_slot_that_balances_next():
    row = re.search(r'<div class="pc-row1">(.*?)\n          </div>', INDEX, re.S).group(1)
    slot = re.search(r'<span class="pc-slot">(.*?)</span>', row, re.S).group(1)
    assert 'id="mixPrevBtn"' in slot and 'onclick="prevMixItem()"' in slot and " hidden>" in slot
    assert row.index('id="mixPrevBtn"') < row.index('id="mixSkipBtn"')


def test_back_goes_to_the_episode_before_or_restarts_the_first():
    back = _fn("prevMixItem")
    assert "if(!mixPlay) return;" in back
    assert "if(mixPlay.pos > 0) mixPlay.pos -= 1;" in back
    assert "playMixItem(mixPlay.order[mixPlay.pos]);" in back
    # Off a Go Deeper follow-up, back is the playlist's episode (§536).
    assert "if(detour){ resumeMix(from); return; }" in back
    assert back.index("stopSpeech();") < back.index("playMixItem(")


def test_back_is_drawn_only_inside_a_playlist():
    draw = _fn("drawMixSkip")
    assert 'prev.hidden = !(on && mixPlay);' in draw


# --- 2. a vibe holds the playlist ---------------------------------------------


def test_an_end_under_the_vibe_editor_is_held_until_it_closes():
    assert "if(vibeComposerIsOpen()){ heldForVibe = carryOn; return; }" in INDEX
    # The hold comes after Go Deeper's own and before anything moves on.
    end = INDEX.split("if(goDeeperIsOpen()) return;", 1)[1]
    assert end.index("var carryOn = function(){") < end.index("if(vibeComposerIsOpen())")
    assert "if(onEnd) onEnd();" in end.split("if(vibeComposerIsOpen())", 1)[0]
    close = _fn("closeVibeComposer")
    assert "var held = heldForVibe;" in close and "heldForVibe = null;" in close
    assert close.index("heldForVibe = null;") < close.index("if(held) held();")


def test_anything_else_starting_drops_what_was_held():
    assert "heldForVibe = null;" in _fn("generate")


# --- 3. Mixes | Categories ----------------------------------------------------


def test_the_screen_is_two_tabs_under_the_wordmark():
    screen = INDEX.split('id="screen-playfam"', 1)[1].split("</section>", 1)[0]
    tabs = re.findall(r'class="pf-tab[^"]*" id="(\w+)"[^>]*>(\w+)</button>', screen)
    assert tabs == [("pfTabMixes", "Mixes"), ("pfTabCats", "Categories")]
    assert screen.index('class="pf-tabs"') < screen.index('id="newMixRow"') < screen.index('id="catChips"')
    assert 'id="catHome" hidden' in screen


def test_each_tab_shows_its_own_half():
    tab = _fn("showPlayfamTab")
    assert "row.hidden = cats;" in tab and "chips.hidden = !cats;" in tab
    shown = _fn("showCategoryPanel")
    assert 'home.hidden = on || !cats;' in shown
    # Picking a category from anywhere opens its tab.
    assert 'if(playfamTab !== "categories") showPlayfamTab("categories");' in _fn("pickCategory")


def test_the_categories_tab_lists_followed_then_the_rest():
    home = _fn("renderCategoryHome")
    assert home.index("Your categories") < home.index("!followed[f.id]")
    assert 'onclick="pickCategory(this.dataset.cat)"' in home
    assert "renderCategoryHome();" in _fn("renderCategoryChips")


# --- 4. the morning dot -------------------------------------------------------


def test_every_myfam_tab_carries_the_dot():
    tabs = re.findall(r'data-tab="playfam".*?<div class="lbl">myFAM</div>', INDEX)
    assert tabs and all("data-mix-dot" in t for t in tabs)
    assert INDEX.count("data-mix-dot aria-label") == INDEX.count('data-tab="playfam"')


def test_the_dot_is_the_servers_word_cleared_once_looked_at():
    check = _fn("checkFreshMixes")
    assert 'fetch("/api/mixes/fresh")' in check
    draw = _fn("drawMixDot")
    assert "mixDotSeen() !== freshMixes.edition" in draw
    assert "clearMixDot();" in INDEX.split("function openPlayFAM(", 1)[1].split("\n  }\n", 1)[0]
    assert "startFreshMixChecks();" in _fn("startNotificationPolling")


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)
    monkeypatch.setattr(appmod, "MIXES", M.MixStore(str(tmp_path / "api.db")))
    monkeypatch.setattr(appmod, "SAVED", saved_mod.SavedStore(str(tmp_path / "saved.db")))
    monkeypatch.setattr(appmod, "_picture_url", lambda q, t="", m=0: "")
    return TestClient(appmod.app)


def _edition(monkeypatch, status, built_at):
    monkeypatch.setattr(daily_edition, "store", lambda: SimpleNamespace(
        status=lambda slot: {"status": status, "built_at": built_at}))


def test_a_guest_has_no_new_mix(client):
    assert client.get("/api/mixes/fresh").json() == {"fresh": [], "edition": ""}


def test_a_written_mix_not_played_since_is_new(client, monkeypatch):
    assert client.post("/api/auth/signup", json={"email": "dot@fam.test",
                                                  "password": PASSWORD}).status_code == 200
    mix = client.post("/api/mixes", json={"name": "Morning", "topic_ids": ["f:nfl"]}).json()
    stored = [m for m in appmod.MIXES.all_mixes() if m.id == mix["id"]][0]
    # The edition ran after the mix was made...
    _edition(monkeypatch, daily_edition.READY, stored.created_at + 0.05)
    time.sleep(0.1)
    got = client.get("/api/mixes/fresh").json()
    assert got["fresh"] == [mix["id"]] and got["edition"]
    # ...and a play after it means it is not new any more.
    appmod.MIXES.note_played(stored.user_id, mix["id"])
    assert client.get("/api/mixes/fresh").json() == {"fresh": [], "edition": ""}


def test_a_mix_made_after_the_edition_is_not_new(client, monkeypatch):
    """The edition never wrote it: a dot would invite a wait."""
    client.post("/api/auth/signup", json={"email": "dot4@fam.test", "password": PASSWORD})
    client.post("/api/mixes", json={"name": "Morning", "topic_ids": ["f:nfl"]})
    _edition(monkeypatch, daily_edition.READY, time.time() - 3600)
    assert client.get("/api/mixes/fresh").json()["fresh"] == []


def test_no_finished_edition_no_dot(client, monkeypatch):
    client.post("/api/auth/signup", json={"email": "dot2@fam.test", "password": PASSWORD})
    client.post("/api/mixes", json={"name": "Morning", "topic_ids": ["f:nfl"]})
    _edition(monkeypatch, "claimed", None)
    assert client.get("/api/mixes/fresh").json()["fresh"] == []


def test_a_failure_is_no_dot_never_an_error(client, monkeypatch):
    client.post("/api/auth/signup", json={"email": "dot3@fam.test", "password": PASSWORD})
    client.post("/api/mixes", json={"name": "Morning", "topic_ids": ["f:nfl"]})

    def boom():
        raise RuntimeError("no edition store")

    monkeypatch.setattr(daily_edition, "store", boom)
    res = client.get("/api/mixes/fresh")
    assert res.status_code == 200 and res.json()["fresh"] == []


# --- 5. the pop-ups -----------------------------------------------------------


def test_the_popups_are_a_card_not_a_fade_of_the_page():
    rule = re.search(r"\.toast, \.snack\{([^}]*)\}", INDEX).group(1)
    assert "background:var(--surface)" in rule and "border-radius:14px" in rule
    assert "box-shadow:" in rule and "width:fit-content" in rule
    assert "rgba(var(--bg-rgb),0)" not in rule
