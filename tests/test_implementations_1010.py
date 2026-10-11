"""§250, the owner's 10.10 implementations packet: the app's half.

* **#1** "Daily", "my", "Your" and "explore" are set in the one face the
  exploreFAM wordmark uses: Bricolage Grotesque at its regular weight.
* **#2** the mini player's X stops the episode as well as hiding the bar.
* **#3** light mode is the default; Settings' toggle is "Dark mode".
* **Stories** no Close Friends audience; a caption is as wide as its words
  allow and never breaks inside a word.

The waitlist page's half is in `test_waitlist.py`.
"""
from __future__ import annotations

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8") as f:
    INDEX = f.read()


def _fn(name):
    return INDEX.split("function " + name + "(", 1)[1].split("\n  }\n", 1)[0]


def _rule(selector):
    return re.search(r"\n  " + re.escape(selector) + r"\{([^}]*)\}", INDEX).group(1)


def test_every_wordmark_word_is_set_like_explore():
    wordmark, reel = _rule(".wordmark"), _rule(".reel-brand")
    for rule in (wordmark, reel, _rule(".xfam-pill")):
        assert "font-family:'Bricolage Grotesque'" in rule
    # explore's word has no weight of its own, so it is the regular 400;
    # Daily / my / Your match it rather than the old 500.
    assert "font-weight" not in reel
    assert "font-weight:400" in wordmark and "letter-spacing" not in wordmark
    for word in ('aria-label="DailyFAM">Daily<', 'aria-label="myFAM">my<'):
        assert '<h2 class="wordmark" ' + word in INDEX, word
    assert '<h2 class="wordmark" aria-label="YourFAM">Your' in INDEX


def test_the_mini_players_x_stops_the_episode():
    body = _fn("dismissNowBar")
    assert "stopSpeech();" in body and "hideNowBar();" in body
    assert "Still playing" not in INDEX
    assert 'class="nowbar-x" onclick="event.stopPropagation(); dismissNowBar()"' in INDEX


def test_light_is_the_default_and_dark_is_the_choice():
    prepaint = INDEX.split("// The theme before anything is drawn", 1)[1].split("</script>", 1)[0]
    assert 'localStorage.getItem("fam.theme") !== "dark"' in prepaint
    assert prepaint.count('setAttribute("data-theme", "light")') == 2  # storage refused too
    settings = _fn("renderSettings")
    assert 'settingsRow("Dark mode", currentTheme() === "dark" ? "On" : "Off"' in settings
    assert '"Light mode"' not in INDEX


def test_a_story_goes_to_everybody_who_follows():
    composer = INDEX.split('id="vibeComposer"', 1)[1].split('id="guestGateOverlay"', 1)[0]
    assert "Close Friends" not in re.sub(r"<!--.*?-->", "", composer, flags=re.S)
    post = _fn("postVibeStory")
    assert "audience" not in post and "'close'" not in post and "Close Friends" not in post
    assert 'settingsRow("Close friends"' not in INDEX


def test_a_caption_wraps_between_words_at_the_canvas_width():
    cap = _rule(".sc-cap")
    # Not shrunk to the room right of its centre, so a line holds more words.
    assert "width:max-content" in cap and "max-width:86%" in cap
    # A word moves to the next line whole; "anywhere" split words mid-way.
    assert "overflow-wrap:break-word" in cap and "anywhere" not in cap
    assert "overflow-wrap:anywhere" not in _rule(".story-caption")
    # Off centre it narrows rather than hang past the canvas's edge.
    draw = _fn("storyCanvasHTML")
    assert "Math.min(86, Math.round(200 * Math.min(st.cx, 1 - st.cx)) - 4)" in draw
    assert "max-width:' + room" in draw


def test_dark_surfaces_keep_dark_gold_in_light_mode():
    rule = re.search(r'html\[data-theme="light"\] \.reel-stage, html\[data-theme="light"\] \.story,'
                     r'\s*html\[data-theme="light"\] \.vcs\{([^}]*)\}', INDEX)
    dark = re.search(r"--copper:(#[0-9A-F]{6});", INDEX).group(1)
    assert rule and f"--copper:{dark};" in rule.group(1) and f"--gold:{dark};" in rule.group(1)
