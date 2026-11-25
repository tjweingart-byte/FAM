"""§693: a tester's five reports (11.24) - Go Deeper offering the episode
just heard, round and round; a Go Deeper that only rephrased; sources
popping up mid-episode; a voice switch that threw the episode away; an
episode that ended in the middle of a sentence."""
import dataclasses
import pathlib
import re

import app as app_mod
import episode_intelligence
import pipeline
import pipeline_errors
import script_generator as sg

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGE = (ROOT / "static" / "index.html").read_text()
AUDIO_JS = (ROOT / "static" / "fam-audio.js").read_text()


def _split(text):
    out = []
    while True:
        match = sg.find_sentence_end(text)
        if not match:
            return out, text
        out.append(text[: match.end()].strip())
        text = text[match.end():]


# --- ended in the middle of a sentence -------------------------------------

def test_an_abbreviation_before_a_name_does_not_end_a_sentence():
    for line in ("The U.S. Supreme Court ruled on Friday.",
                 "They play at 7 p.m. Eastern on ESPN.",
                 "Apple Inc. said on Monday it would.",
                 "It cost a lot, i.e. more than planned, e.g. double.",
                 '"Why?" he asked.',
                 "Yes... maybe not."):
        assert _split(line + " Next one.")[0] == [line], line


def test_sentences_that_really_end_on_an_abbreviation_still_do():
    assert _split("He lives in the U.S. The court ruled. Next one.")[0] == [
        "He lives in the U.S.", "The court ruled."]
    assert _split("They play at 7 p.m. It is on ESPN. Next one.")[0] == [
        "They play at 7 p.m.", "It is on ESPN."]
    assert _split("It ended Oct. 14. Markets fell. Next one.")[0] == [
        "It ended Oct. 14.", "Markets fell."]


def test_a_stream_waits_for_the_word_that_decides():
    assert sg.find_sentence_end("He lives in the U.S. ") is None
    assert sg.find_sentence_end("He lives in the U.S. Th") is None
    assert sg.find_sentence_end("He lives in the U.S. The ") is not None
    assert sg.find_sentence_end("It fell. ") is None
    assert sg.find_sentence_end("It fell. T") is not None


def test_an_episode_cut_at_its_length_is_a_notice(monkeypatch):
    seen = []
    monkeypatch.setattr(pipeline_errors, "record",
                        lambda *a, **k: seen.append(a))
    stats = pipeline.GenerationStats()
    stats.query = "why the fed held"
    pipeline._note_cut_at_length(stats, ["It held."], ["Then it cut."])
    assert seen and seen[0][1] == "stopped at its length before the script's end"
    assert "It held." in seen[0][2] and "Then it cut." in seen[0][2]
    assert seen[0][4] == pipeline_errors.NOTICE
    stats.truncated = True                      # once an episode
    pipeline._note_cut_at_length(stats, ["It held."], ["Then it cut."])
    assert len(seen) == 1


# --- Go Deeper: the same episode, round and round --------------------------

def test_a_thread_leading_back_to_what_was_heard_is_no_thread():
    heard = "Why the Fed Held Rates Again"
    assert sg.circles_back("why the Fed held rates again", "x y z", heard)
    assert sg.circles_back("what the rate cut means for mortgage holders",
                           "What the cut means for mortgages")
    assert not sg.circles_back("whether banks pass the cut on to savers", heard)
    assert not sg.circles_back("", heard)
    # A thread on one subject of a long request is a real thread.
    long_prompt = ("Today's edition: the Eagles, the Sixers, the Phillies, the Fed, "
                   "Nvidia earnings, the Senate budget vote, SpaceX launches, "
                   "injuries and the playoff picture")
    assert not sg.circles_back("the Eagles injuries and playoff picture", long_prompt)
    assert not sg.circles_back("whether the appeal gets heard", "", "")


def _plan(**over):
    plan = sg.plan_episode("whether banks pass the cut on", 2,
                           context="Why the Fed Held Rates Again", search=False)
    return dataclasses.replace(plan, **over)


def test_the_reader_drops_a_thread_that_circles_back():
    notes = sg.ScriptNotes()
    reader = sg._ScriptReader(_plan(), notes)
    reader.feed("Banks moved within a day. "
                "<<NEXT: why the Fed held rates again>>")
    reader.finish()
    assert notes.thread == ""
    notes = sg.ScriptNotes()
    reader = sg._ScriptReader(_plan(), notes)
    reader.feed("Banks moved within a day. "
                "<<NEXT: how savers' accounts changed afterwards>>")
    reader.finish()
    assert notes.thread == "how savers' accounts changed afterwards"


# --- Go Deeper: not going deeper -------------------------------------------

SAID = "The Fed held its rate at 4.25 percent on Wednesday. Two governors dissented."


def test_a_follow_up_writer_is_shown_what_was_said():
    prompt = sg.build_prompt(_plan(heard=SAID))
    assert "<already_said>" in prompt and SAID in prompt
    assert "never tell it again in other words" in prompt
    assert "leads somewhere neither briefing has been" in prompt
    # Without it, the follow-up is written from the title alone, as before.
    bare = sg.build_prompt(_plan())
    assert "<already_said>" not in bare and "<already_heard>" in bare
    # And an episode that follows nothing is told none of it.
    alone = sg.build_prompt(sg.plan_episode("why the fed held", 2, search=False))
    assert "FOLLOW-UP" not in alone and "neither briefing" not in alone


def test_the_brief_aims_the_search_past_what_was_said():
    with_it = episode_intelligence.build_ei_prompt(
        "whether banks pass it on", 2, "Why the Fed Held Rates Again", heard=SAID)
    assert SAID in with_it and "Aim the search past it" in with_it
    without = episode_intelligence.build_ei_prompt(
        "whether banks pass it on", 2, "Why the Fed Held Rates Again")
    assert "already_said" not in without


def test_what_was_heard_is_never_in_the_key():
    import asyncio
    a = asyncio.run(pipeline.key_for(_plan()))
    b = asyncio.run(pipeline.key_for(_plan(heard=SAID)))
    assert a == b


class _Cache:
    def __init__(self, scripts):
        self.scripts = scripts

    def resolve_episode(self, episode):
        key = episode.split("@")[0]
        return key if key in self.scripts else ""

    def script(self, key):
        return list(self.scripts.get(key, []))


class _Saved:
    def __init__(self, rows):
        self.rows = rows

    def history(self, user, limit=200, **_):
        return list(self.rows)


def test_heard_before_reads_the_script_by_id_or_by_the_listeners_history(monkeypatch):
    monkeypatch.setattr(app_mod, "parse_episode_id", lambda e: ("k", 1) if e else None)
    monkeypatch.setattr(app_mod, "SCRIPT_CACHE", _Cache({"k1": ["One.", "Two."]}))
    monkeypatch.setattr(app_mod, "SAVED", _Saved([
        {"query": "other", "minutes": 2, "title": "Something Else",
         "context": "", "episode": ""},
        {"query": "why the fed held", "minutes": 2,
         "title": "Why the Fed Held Rates Again", "context": "", "episode": "k1@5"},
    ]))
    # By the id the player was given.
    assert app_mod._heard_before("u", "anything", "k1@5") == "One. Two."
    # An installed client sends none: the listener's own history, by title.
    assert app_mod._heard_before("u", "why the fed held rates again") == "One. Two."
    # Nothing found: "" and the follow-up is written from the title alone.
    assert app_mod._heard_before("u", "Never Heard Of It") == ""
    assert app_mod._heard_before("", "Why the Fed Held Rates Again") == ""


def test_heard_before_never_raises(monkeypatch):
    class Broken:
        def history(self, *a, **k):
            raise RuntimeError("down")
    monkeypatch.setattr(app_mod, "SAVED", Broken())
    assert app_mod._heard_before("u", "Title") == ""


def test_the_clients_name_the_episode_a_follow_up_follows():
    assert '"&heard=" + encodeURIComponent(listener.heard)' in AUDIO_JS
    assert "heard: opts.heard || \"\"" in PAGE
    api = (ROOT / "ios/FAM/FAM/API.swift").read_text()
    assert 'URLQueryItem(name: "heard", value: heard)' in api
    assert "ep.heardID = player.episodeID" in (
        ROOT / "ios/FAM/FAM/NowPlaying.swift").read_text()


# --- neither voice nor length is changed mid-episode -----------------------

def test_the_player_offers_no_length():
    """The owner, 11.25: "take length off the player too". It rebuilt the
    episode playing from its start, as the voice row did."""
    modal = PAGE[PAGE.index('id="speedLengthOverlay"'):PAGE.index('id="goDeeperOverlay"')]
    shown = re.sub(r"<!--.*?-->", "", modal, flags=re.S)
    assert ">Length<" not in shown and "openLengthMenu" not in shown
    assert ">Speed<" in shown
    assert "regenerateAtNewLength" not in PAGE and "Rebuilding at" not in PAGE
    menu = PAGE[PAGE.index("function openLengthMenu(){"):PAGE.index("function ackSearchBubble")]
    assert "generate(" not in menu
    # The search page still picks one.
    assert 'id="lengthBubble" onclick="openLengthMenu()"' in PAGE
    sheets = (ROOT / "ios/FAM/FAM/Views/PlayerSheets.swift").read_text()
    playback = sheets[sheets.index("struct PlaybackSheet"):sheets.index("// MARK: - The queue")]
    assert 'field("Length"' not in playback and 'field("Speed"' in playback
    view = (ROOT / "ios/FAM/FAM/Views/PlayerView.swift").read_text()
    assert "lengthItems" not in view and "Rebuilding at" not in view



def test_the_player_offers_no_voice():
    modal = PAGE[PAGE.index('id="speedLengthOverlay"'):PAGE.index('id="goDeeperOverlay"')]
    assert "Voice" not in re.sub(r"<!--.*?-->", "", modal, flags=re.S)
    assert "voiceModalVal" not in PAGE
    # A pick never starts the episode playing again.
    item = PAGE[PAGE.index("function voiceItem(v){"):PAGE.index("function openSpeedLengthModal")]
    assert "generate(" not in item and "Switching to" not in item
    sheets = (ROOT / "ios/FAM/FAM/Views/PlayerSheets.swift").read_text()
    playback = sheets[sheets.index("struct PlaybackSheet"):sheets.index("// MARK: - The queue")]
    assert 'field("Voice"' not in playback
    assert "func revoice" not in (ROOT / "ios/FAM/FAM/AppModel.swift").read_text()


# --- sources are there as the player opens ---------------------------------

def test_sources_are_asked_at_the_first_word():
    first = PAGE[PAGE.index("// And the sources, for the same reason and on the same clock."):
                 PAGE.index("askSourcesUntilShown(episodeStamp, 0);")]
    code = "\n".join(l for l in first.splitlines() if not l.strip().startswith("//"))
    assert code.strip().startswith("fetchEpisodeSources();")
    now = (ROOT / "ios/FAM/FAM/NowPlaying.swift").read_text()
    ask = now[now.index("private func askSources"):now.index("private func refreshSourcesOnce")]
    code = [l.strip() for l in ask.splitlines()[1:] if l.strip() and not l.strip().startswith("//")]
    assert code[0] == "await refreshSourcesOnce(mine)"
