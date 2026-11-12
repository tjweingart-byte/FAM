"""Words in capitals said as words or as letters (PROBLEMS.md §567).

NASCAR and NVIDIA reached the voice as "N A S C A R" and "N V I D I A":
`spoken_text` spelled every two-to-six-capital word not on a short list.
The CMU dictionary now decides from four letters; the trial says them five
ways for the ear.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

import hard_words
import pronounce_review
import spoken_text
import voice_check
from spoken_text import speakable

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Said as words: from the dictionary, or listed where it lacks them.
WORDS = ["NASCAR", "NATO", "NASA", "DOGE", "EBITDA", "UNESCO",
         "AWOL", "HIPAA", "DACA", "FAANG", "LIV", "FOX", "OPEC", "IKEA",
         "CAPTCHA", "INTEL", "NEVER"]
#: Said as letters: the dictionary has them as their letters' names, they
#: are not in it, or they are short.
LETTERS = ["FBI", "ESPN", "HBO", "CNN", "NFL", "NBA", "NCAA", "UCLA", "MSNBC",
           "WNBA", "AARP", "ACLU", "NYSE", "TSMC", "LGBTQ", "AI", "WHO", "SEC",
           "SAT", "CEO", "IRS", "NAACP", "ADHD", "AMD"]


@pytest.mark.parametrize("word", WORDS)
def test_said_as_a_word_in_ordinary_case(word):
    assert speakable(f"the {word} story") == f"the {word.capitalize()} story"


@pytest.mark.parametrize("word", LETTERS)
def test_said_as_letters(word):
    assert speakable(f"the {word} story") == f"the {' '.join(word)} story"


def test_the_reported_cases_and_their_endings():
    assert speakable("NASCAR drivers and NVIDIA's chips") == "Nascar drivers and Envidia's chips"
    assert speakable("two NFLs") == "two N F Ls"     # as before: the s stays a word ending


def test_an_entry_that_is_its_letters_names_is_letters():
    assert spoken_text._spelled_as_letters("ESPN", "IY1 EH1 S P IY1 EH1 N")
    assert spoken_text._spelled_as_letters("WWE", "D AH1 B AH0 L Y UW0 D AH1 B AH0 Y UW0 IY1")
    assert not spoken_text._spelled_as_letters("NASCAR", "N AE1 S K AA2 R")
    assert not spoken_text._spelled_as_letters("ESP", "IY1 EH1 S P IY1 EH1 N")


def test_until_the_dictionary_has_loaded_the_old_rule_holds(monkeypatch):
    monkeypatch.setattr(hard_words, "peek", lambda: None)
    assert speakable("AWOL") == "A W O L"          # not listed: spelled, as before
    assert speakable("NASCAR") == "Nascar"          # listed
    assert speakable("BREAKING news") == "Breaking news"   # seven or more: a word


def test_peek_never_waits_and_starts_one_load(monkeypatch):
    started = []

    class Thread:
        def __init__(self, target, **kw):
            self.target = target

        def start(self):
            started.append(self.target)

    monkeypatch.setattr(hard_words, "_READY", type("E", (), {"is_set": lambda self: False})())
    monkeypatch.setattr(hard_words, "_KICKED", False)
    monkeypatch.setattr(hard_words.threading, "Thread", Thread)
    assert hard_words.peek() is None and hard_words.peek() is None
    assert started == [hard_words._load]


def test_word_case_upper_hands_the_voice_capitals(monkeypatch):
    monkeypatch.setattr(spoken_text, "WORD_CASE", "upper")
    assert speakable("NASCAR and NVIDIA's chips, by the FBI") == \
        "NASCAR and Envidia's chips, by the F B I"


def test_words_with_a_spoken_form_of_their_own():
    assert speakable("OK, said OPENAI and USAID on CSPAN") == \
        "okay, said Open A I and U S Aid on C Span"


def test_the_server_loads_the_dictionary_at_boot_off_the_loop():
    source = (ROOT / "app.py").read_text()
    assert "run_in_executor(None, _hard_words.warm)" in source


# --- the trial ----------------------------------------------------------------

def test_five_ways_to_say_capitals_and_which_is_today():
    assert pronounce_review.caps_variants("FBI") == {
        "spaced": "F B I", "dotted": "F.B.I.", "named": "eff bee eye",
        "upper": "FBI", "title": "Fbi"}
    assert pronounce_review.caps_choice("FBI") == "spaced"
    assert pronounce_review.caps_choice("NASCAR") == "title"


def _tool():
    spec = importlib.util.spec_from_file_location("stress_trial", ROOT / "tools" / "stress_trial.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_caps_trial_says_each_as_written_and_writes_a_page(monkeypatch, tmp_path):
    sent = []

    async def fake(text):
        sent.append(text)
        return {"pcm": b"\0\0" * 10, "rate": 24000, "heard": "fbi"}

    monkeypatch.setattr(voice_check, "say_and_hear", fake)
    tool = _tool()
    monkeypatch.setattr(sys, "argv", ["stress_trial.py", "--caps", "FBI,NASCAR",
                                      "--no-sentence", "--out", str(tmp_path)])
    assert tool.main() == 0
    # Past spoken_text: "F.B.I." and "Nascar" reach the voice as written.
    assert sent == ["F B I", "F.B.I.", "eff bee eye", "FBI", "Fbi",
                    "N A S C A R", "N.A.S.C.A.R.", "en ay ess see ay ar", "NASCAR", "Nascar"]
    page = (tmp_path / "index.html").read_text()
    assert "Capitals trial" in page and page.count("</code> <b>(today)</b>") == 2
    assert "<code>Nascar</code> <b>(today)</b>" in page
    assert len(list(tmp_path.glob("*.wav"))) == 10


# --- /admin: the same trial on the production voice ------------------------------

def test_admin_hears_a_word_in_capitals_five_ways_and_keeps_nothing(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import app as appmod
    import voice_bank

    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "ADMIN_TOKEN", "secret")
    bank = voice_bank.VoiceBank(str(tmp_path / "voice_bank.db"))
    voice_bank.reset(bank)
    sent = []

    async def fake(text):
        sent.append(text)
        return {"pcm": b"\0\0" * 10, "rate": 24000, "heard": "næskɑr"}

    monkeypatch.setattr(voice_check, "say_and_hear", fake)
    client = TestClient(appmod.app)
    try:
        assert client.post("/api/admin/pronunciations/hear-caps",
                           json={"word": "nascar"}).status_code == 404
        r = client.post("/api/admin/pronunciations/hear-caps", json={"word": "nascar"},
                        headers={"X-Admin-Token": "secret"}).json()
        assert r["word"] == "NASCAR" and r["today"] == "title"
        assert [v["variant"] for v in r["variants"]] == ["spaced", "dotted", "named", "upper", "title"]
        assert all(v["audio"].startswith("data:audio/wav;base64,") and "wav" not in v
                   for v in r["variants"])
        assert sent == ["N A S C A R", "N.A.S.C.A.R.", "en ay ess see ay ar", "NASCAR", "Nascar"]
        assert bank.voice_checks() == [] and bank.pronunciations() == []
        bad = client.post("/api/admin/pronunciations/hear-caps", json={"word": "F.B.I."},
                          headers={"X-Admin-Token": "secret"})
        assert bad.status_code == 422
    finally:
        voice_bank.reset(None)


def test_the_admin_page_has_the_capitals_box():
    page = (ROOT / "admin_ui" / "tracker.html").read_text()
    for needle in ('id="capsForm"', "/api/admin/pronunciations/hear-caps", "Hear 5 ways", "(today)"):
        assert needle in page, needle


def test_nvidia_is_spelled_as_it_sounds():
    """§569: "Nvidia" was still choppy - no English word opens "Nv"."""
    for written in ("NVIDIA", "Nvidia", "nVidia", "nvidia"):
        assert speakable(f"{written}'s chips") == "Envidia's chips", written
    assert speakable("Nvidias") == "Nvidias"        # whole words only
