"""§687: the Go Deeper popup has an X, top right, on the web and the iPhone."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def test_the_web_popup_draws_the_modal_x_that_closes_it():
    html = _read("static", "index.html")
    card = html.split('id="goDeeperOverlay"', 1)[1].split('id="shareOverlay"', 1)[0]
    x = card.split('id="goDeeperX"', 1)[0].rsplit("<button", 1)[1] \
        + card.split('id="goDeeperX"', 1)[1].split(">", 1)[0]
    assert 'class="modal-x"' in x and 'onclick="closeGoDeeper()"' in x
    assert 'aria-label="Close"' in x
    # Before the title, like every other modal's X.
    assert card.index('id="goDeeperX"') < card.index('class="modal-title"')


def test_the_iphone_sheet_draws_an_x_that_shuts_it():
    swift = _read("ios", "FAM", "FAM", "Views", "PlayerSheets.swift")
    sheet = swift.split("struct GoDeeperSheet", 1)[1].split("\nstruct ", 1)[0]
    assert ".overlay(alignment: .topTrailing)" in sheet
    x = sheet.split(".overlay(alignment: .topTrailing)", 1)[1][:400]
    assert 'Image(systemName: "xmark")' in x and "shut()" in x
    assert '.accessibilityLabel("Close")' in x
