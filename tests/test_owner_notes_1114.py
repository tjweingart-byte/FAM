"""The owner's 11.14 notes (PROBLEMS.md §616), the parts that are interface.

* "Which one do you mean?": a cleaner card and a pill to type your own.
* The tour's card covered the whole screen on some iPhones.
* The message box was too big.
* Return acts as a submit button on every page.

(Today's order of a mix - the rest of the notes - is
`tests/test_mix_today_616.py`.) The Swift is pinned by its source: nothing
here compiles it; `ios.yml` does.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text()
IOS = ROOT / "ios" / "FAM" / "FAM"


def swift(rel: str) -> str:
    return (IOS / rel).read_text()


def js_function(name: str) -> str:
    body = re.search(r"function " + re.escape(name) + r"\([^)]*\)\{(.*?)\n  \}", INDEX, re.S)
    assert body, name
    return body.group(1)


# --- "Which one do you mean?" -------------------------------------------------


def test_the_reading_card_has_choices_and_a_type_your_own_pill():
    card = INDEX.split('id="readingOverlay"', 1)[1].split("</form>", 1)[0]
    assert 'id="readingChoices"' in card and 'id="readingOwnBtn"' in card
    assert "Type your own" in card
    assert 'id="readingOwnInput"' in card and 'maxlength="200"' in card
    # Return in the field sends it: the field is in a form.
    assert 'onsubmit="event.preventDefault(); sendReadingOwn();"' in card
    # Each reading is a choice row, not a copper pill that wraps centred.
    ask = js_function("askReading")
    assert 'b.className = "reading-choice"' in ask
    assert "modal-btn primary" not in ask


def test_typed_words_are_sent_as_the_reading():
    send = js_function("sendReadingOwn")
    assert ".slice(0, 200)" in send and "answerReading(words)" in send
    # Each time the card opens the field starts closed and empty.
    ask = js_function("askReading")
    assert 'getElementById("readingOwnRow").hidden = true' in ask
    assert "input.value = \"\"" in ask


def test_the_pill_hides_when_the_field_opens():
    """`.reading-own` is a flex box, which beats the `hidden` attribute
    unless the stylesheet says otherwise."""
    assert ".reading-own[hidden], .reading-own-row[hidden]{ display:none; }" in INDEX


def test_the_iphone_reading_card_matches():
    card = swift("Views/PlayerParts.swift").split("struct ReadingCard: View {", 1)[1] \
        .split("/// `showLimitReached`", 1)[0]
    assert 'Text("Type your own")' in card
    assert ".onSubmit { sendOwn() }" in card
    assert "prefix(200)" in card
    assert "model.answerReading(words)" in card
    # Focus waits until the field is on screen, or the keyboard may not open.
    assert "DispatchQueue.main.async { focused = true }" in card
    assert "static let pencil: Path" in swift("Theme.swift")


# --- the tour's card ----------------------------------------------------------


def test_the_iphone_tour_card_is_as_tall_as_its_words():
    tour = swift("Tour.swift")
    card = tour.split("struct TourCard: View {", 1)[1]
    # Pinned to its own height and measured; it scrolls only when the words
    # outgrow the room, and then at exactly the room's height.
    assert "measuredInner.fixedSize(horizontal: false, vertical: true)" in card
    assert "if measured > maxHeight && maxHeight > 0 {" in card
    assert ".frame(height: maxHeight)" in card
    # Never a scroll view under a bare maximum, which some phones stretched.
    assert "ScrollsWhenTall { inner }" not in card
    assert ".frame(maxHeight: maxHeight)" not in card


def test_the_web_tour_card_was_already_its_own_height():
    css = INDEX.split(".tour-card{", 1)[1].split("}", 1)[0]
    assert "max-height:calc(100% - 32px)" in css and "height:" not in css.replace("max-height", "")


# --- the message box ----------------------------------------------------------


def test_the_iphone_message_box_is_as_tall_as_what_is_typed():
    chat = swift("Views/ChatView.swift")
    composer = chat.split("private var composer: some View {", 1)[1].split("static func returnPressed", 1)[0]
    assert ".lineLimit(1...5)" in composer
    assert ".fixedSize(horizontal: false, vertical: true)" in composer
    assert ".frame(maxHeight: 112)" not in composer


# --- Return submits -----------------------------------------------------------


def test_one_handler_reads_data_enter():
    handler = INDEX.split("**Return submits, everywhere**", 1)[1].split("});", 1)[0]
    for need in ('e.key !== "Enter"', "e.shiftKey", "e.isComposing", "e.defaultPrevented",
                 'getAttribute("data-enter")', "btn.disabled", "btn.click()", "el.blur()"):
        assert need in handler, need


def test_every_field_without_a_form_or_its_own_enter_says_what_return_does():
    """Every text field in the page is in a form, has its own Enter handler,
    or names what Return does - except Messages' box (Return sends, §590,
    its own handler) and the feedback report, a long text where Return is a
    new line."""
    tags = re.findall(r"<(?:input|textarea)\b[^>]*>", INDEX)
    allowed = {"feedbackText", "passwordFirstEmail"}
    missing = []
    for tag in tags:
        kind = re.search(r'type="([^"]+)"', tag)
        if kind and kind.group(1) in ("file", "checkbox", "range", "radio", "hidden", "date", "time"):
            continue
        ident = re.search(r'id="([^"]+)"', tag)
        ident = ident.group(1) if ident else tag[:60]
        if ident in allowed or "data-enter" in tag or "onkeydown" in tag or "data-st" in tag:
            continue
        # Inside a form? Look back for an unclosed <form.
        at = INDEX.find(tag)
        before = INDEX[:at]
        if before.rfind("<form") > before.rfind("</form>"):
            continue
        if ident in ("modalInput",):            # its own listener
            continue
        missing.append(ident)
    assert not missing, missing


def test_data_enter_names_buttons_that_exist():
    for target in set(re.findall(r'data-enter="([^"]+)"', INDEX)) - {"blur"}:
        assert f'id="{target}"' in INDEX, target


def test_the_iphone_fields_press_their_buttons():
    field = swift("Views/SocialParts.swift").split("struct ModalField: View {", 1)[1].split("\n}\n", 1)[0]
    assert "var onReturn: (() -> Void)? = nil" in field and ".onSubmit { onReturn?() }" in field
    assert swift("Views/MessagesView.swift").count("onReturn: { if !working { confirm() } }") == 2
    auth = swift("Views/AuthView.swift")
    assert auth.count(".onSubmit { if !working { Task { await go() } } }") == 2
    profile = swift("Views/ProfileEdit.swift")
    # The name, the handle and the code; and where you are, which became
    # one box (`PlaceField`) that presses the same button on Return.
    assert profile.count(".onSubmit { if !saving { save() } }") >= 2
    assert profile.count("PlaceField(place: $place) { if !saving { save() } }") == 2
    assert ".onSubmit(onReturn)" in profile
    assert ".onSubmit { if dateSet && !working { submit(confirmed: false) } }" in \
        swift("Views/SettingsView.swift")
    # The story reply already sent on Return; it still does.
    assert ".onSubmit" in swift("Views/Stories.swift").split('Text("Send message...")', 1)[1][:1200]
