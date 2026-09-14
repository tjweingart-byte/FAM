"""The continuous-line episode visual: what it finds, what it refuses, and
where it is allowed to appear.

Two halves, because the feature has two halves that fail differently.

The Python half is about *finding and judging an asset*. The rule that carries
the whole visual language is "exactly one continuous path", and the failure it
guards against is the quiet one: a two-path file rendered as two strokes
appearing one after another looks like a feature working and is a different
product. So the interesting tests here are the rejections.

The interface half is about *where the drawing may appear*. exploreFAM is
excluded, and that exclusion is asserted against the shipped `static/index.html`
rather than described in a comment - the gate leaking into Explore is the one
regression this change could cause that nobody would notice from a green suite.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import episode_visuals  # noqa: E402
from app import app  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
INTERFACE = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
FIXTURES = ROOT / "tools" / "visual_fixtures"

ONE_PATH = (FIXTURES / "sample-line.svg").read_text(encoding="utf-8")
TWO_PATHS = (FIXTURES / "two-paths.svg").read_text(encoding="utf-8")


@pytest.fixture
def visuals(tmp_path, monkeypatch):
    """An empty visuals folder this test owns."""
    directory = tmp_path / "visuals"
    directory.mkdir()
    monkeypatch.setenv("FAM_VISUALS_DIR", str(directory))
    return directory


@pytest.fixture
def client():
    return TestClient(app)


def seed(directory: pathlib.Path, query: str, svg: str) -> str:
    vid = episode_visuals.visual_id(query)
    (directory / f"{vid}.svg").write_text(svg)
    return vid


# ------------------------------------------------------------- identity


def test_the_id_is_the_subject_not_the_episode():
    """Length is not part of it, deliberately - the same reason voice is not
    part of the script cache key. A ten-minute episode about a subject is the
    same subject as a three-minute one, and the drawing illustrates the
    subject. It is also what makes a feed tile resolvable: a tile has no length
    yet, because nobody has chosen one."""
    assert (episode_visuals.visual_id("NFL week 5 recap")
            == episode_visuals.visual_id("recap: week 5, NFL"))
    assert (episode_visuals.visual_id("why the fed moved")
            != episode_visuals.visual_id("why the boj moved"))


# ----------------------------------------------------------- validation


def test_one_path_is_accepted():
    ok, reason = episode_visuals.validate_svg(ONE_PATH)
    assert ok, reason


def test_two_paths_are_refused_and_say_so():
    """The rule the visual language rests on. A silent multi-stroke reveal is
    the failure mode this whole check exists to prevent."""
    ok, reason = episode_visuals.validate_svg(TWO_PATHS)
    assert not ok
    assert "one continuous path" in reason


@pytest.mark.parametrize("svg, expected", [
    ("", "empty"),
    ("<svg><path d='M0 0 L1 1'", "parseable"),
    ("<html><body/></html>", "root element"),
    ("<svg xmlns='http://www.w3.org/2000/svg'></svg>", "no drawable path"),
    ("<svg xmlns='http://www.w3.org/2000/svg'><circle cx='5' cy='5' r='4'/></svg>",
     "not a <path>"),
    ("<svg xmlns='http://www.w3.org/2000/svg'><path d='M0 0 L9 9' fill='#000'/></svg>",
     "filled"),
])
def test_everything_else_is_refused_with_a_reason(svg, expected):
    """Every refusal names what is wrong with the file, because the person who
    has to fix it is upstream and cannot see this process."""
    ok, reason = episode_visuals.validate_svg(svg)
    assert not ok
    assert expected in reason


def test_an_empty_path_does_not_count_as_a_second_stroke():
    """Vector editors leave these behind. One real path plus a `d`-less stub is
    still one drawing, and refusing it would reject usable artwork."""
    svg = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'>"
           "<path d=''/><path d='M0 0 L9 9' fill='none'/></svg>")
    ok, reason = episode_visuals.validate_svg(svg)
    assert ok, reason


# --------------------------------------------------------------- records


def test_no_folder_is_none_rather_than_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("FAM_VISUALS_DIR", str(tmp_path / "nothing-here"))
    record = episode_visuals.record_for("anything at all")
    assert record["status"] == "none"
    assert record["vector_url"] == ""


def test_a_good_asset_is_ready_and_describes_itself(visuals):
    seed(visuals, "why teams move cities", ONE_PATH)
    record = episode_visuals.record_for("why teams move cities")
    assert record["status"] == "ready"
    assert record["vector_url"].startswith(episode_visuals.ASSET_ROUTE)
    # The finished line is the thumbnail, so one file does both jobs and there
    # is no second artefact to keep in step with the first.
    assert record["thumbnail_url"] == record["vector_url"]
    assert record["view_box"] == "0 0 1000 1000"
    assert record["background_color"] == episode_visuals.DEFAULT_BACKGROUND
    assert record["stroke_width"] == episode_visuals.DEFAULT_STROKE_WIDTH


def test_a_bad_asset_is_failed_and_never_ready(visuals):
    seed(visuals, "two strokes", TWO_PATHS)
    record = episode_visuals.record_for("two strokes")
    assert record["status"] == "failed"
    assert "one continuous path" in record["reason"]
    assert record["vector_url"] == ""


def test_the_manifest_can_say_processing_before_any_file_exists(visuals):
    (visuals / "index.json").write_text(json.dumps(
        {"still being drawn": {"status": "processing"}}))
    assert episode_visuals.record_for("still being drawn")["status"] == "processing"


def test_the_manifest_can_override_the_palette(visuals):
    vid = seed(visuals, "custom colours", ONE_PATH)
    (visuals / "index.json").write_text(json.dumps(
        {vid: {"version": 3, "stroke": "#402010", "stroke_width": 2.2,
               "background": "#FFFFFF"}}))
    record = episode_visuals.record_for("custom colours")
    assert record["status"] == "ready"
    assert (record["version"], record["stroke_color"], record["stroke_width"],
            record["background_color"]) == (3, "#402010", 2.2, "#FFFFFF")


def test_a_broken_manifest_does_not_take_the_folder_with_it(visuals):
    seed(visuals, "still fine", ONE_PATH)
    (visuals / "index.json").write_text("{ this is not json")
    assert episode_visuals.record_for("still fine")["status"] == "ready"


def test_the_feature_can_be_switched_off(visuals, monkeypatch):
    seed(visuals, "switched off", ONE_PATH)
    monkeypatch.setenv("EPISODE_VISUALS", "0")
    record = episode_visuals.record_for("switched off")
    assert record["status"] == "none"
    assert "switched off" in record["reason"]


def test_the_example_file_agrees_with_the_code():
    """§54, with a new setting to get wrong. `.env.example` is the file people
    are told to copy, so a value in it that disagrees with the default
    configures the product against itself."""
    values = {}
    for line in (ROOT / ".env.example").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, _, value = line.partition("=")
            values[name.strip()] = value.split("#")[0].strip()
    assert values.get("EPISODE_VISUALS") == "1"
    os.environ.pop("EPISODE_VISUALS", None)
    assert episode_visuals.enabled() is True


# ------------------------------------------------------------- the paths


def test_the_asset_is_served(visuals, client):
    vid = seed(visuals, "servable", ONE_PATH)
    res = client.get(f"{episode_visuals.ASSET_ROUTE}{vid}.svg")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("image/svg+xml")
    assert "<path" in res.text


@pytest.mark.parametrize("name", [
    "../../etc/passwd", "..%2F..%2Fapp.py", "app.py", "index.json",
    "nope.svg", "", "a b.svg",
])
def test_nothing_outside_the_folder_is_servable(visuals, client, name):
    """Two gates - a rule about names and a rule about locations - because a
    name can pass the first while a symlink fails the second. `index.json` is
    in the folder and still refused: it is not an image."""
    res = client.get(f"{episode_visuals.ASSET_ROUTE}{name}")
    assert res.status_code in (404, 405, 307)


def test_the_endpoint_answers_with_the_record(visuals, client):
    seed(visuals, "endpoint answers", ONE_PATH)
    body = client.get("/api/visual", params={"q": "endpoint answers"}).json()
    assert body["visual"]["status"] == "ready"
    assert body["visual"]["vector_url"].endswith(".svg")


def test_an_episode_with_no_artwork_is_not_an_error(visuals, client):
    """The ordinary case, today for every episode. It must be a shrug, not a
    404 - the player draws nothing and the audio is untouched."""
    res = client.get("/api/visual", params={"q": "nobody has drawn this"})
    assert res.status_code == 200
    assert res.json()["visual"]["status"] == "none"


def test_health_says_whether_this_deploy_has_artwork(client):
    report = client.get("/api/health").json()["visuals"]
    assert set(report) >= {"enabled", "dir", "vectors", "counters"}


def test_tiles_carry_the_finished_line_when_there_is_one(visuals, client):
    """The other half of "the completed visual is the thumbnail": a feed tile
    shows the finished drawing, still, while the player shows it arriving."""
    import topics as topics_mod

    # The whole bank, so which tiles this listener happens to be shown does not
    # decide whether the test passes.
    for topic in topics_mod.TOPIC_BANK:
        seed(visuals, topic.query, ONE_PATH)
    feed = client.get("/api/myfam").json()
    tiles = [t for section in feed["sections"] for t in section["topics"]]
    assert tiles, "the feed came back empty"
    assert all(t["visual"]["status"] == "ready" for t in tiles)
    assert all(t["visual"]["thumbnail_url"].endswith(".svg") for t in tiles)


def test_a_tile_with_no_artwork_is_unchanged(client):
    """No `visual` key at all rather than twenty-four records saying "none".
    A tile has nothing to show for the other states, so sending them would be
    five kilobytes per feed describing a decision no tile makes."""
    feed = client.get("/api/myfam").json()
    assert not any("visual" in t for section in feed["sections"]
                   for t in section["topics"])


# ------------------------------------------------- where it may be drawn


def function_body(name: str) -> str:
    """The text of one top-level function in the interface."""
    start = INTERFACE.index("function " + name + "(")
    rest = INTERFACE[start:]
    end = rest.index("\n  function ", 1)
    return rest[:end]


def test_the_canvas_exists_only_on_the_player():
    """The strongest form the exploreFAM exclusion can take: there is no
    element to draw into anywhere else. Explore's markup is untouched."""
    assert INTERFACE.count('id="lineCanvas"') == 1
    player = INTERFACE[INTERFACE.index('id="screen-player"'):
                       INTERFACE.index('id="screen-playall"')]
    assert 'id="lineCanvas"' in player

    explore = INTERFACE[INTERFACE.index('id="screen-explore"'):
                        INTERFACE.index('id="screen-profile"')]
    for token in ("lineCanvas", "line-canvas", "FamLine", "LineVisual"):
        assert token not in explore, f"{token} reached the exploreFAM markup"


def test_the_gate_names_the_player_and_not_explore():
    match = re.search(r"var CONTINUOUS_VISUAL_SURFACES = \[([^\]]*)\]", INTERFACE)
    assert match, "the surface gate is gone"
    surfaces = re.findall(r'"([a-z]+)"', match.group(1))
    assert surfaces == ["player"]


@pytest.mark.parametrize("name", ["playReel", "refreshReelProgress",
                                  "startReelProgress", "turnReel",
                                  "paintReelScrub"])
def test_no_explore_function_touches_the_visual(name):
    """Explore drives the same FamAudio as the player, so the separation that
    matters is which functions call the renderer. None of Explore's do - and
    this fails the moment one starts to."""
    body = function_body(name)
    for token in ("FamLine", "LineVisual", "lineCanvas"):
        assert token not in body, f"{name} reached the continuous-line visual"


def test_every_path_into_playback_clears_the_square_first():
    """Play All, Explore New, a saved episode - each goes through `speakText`,
    and each must not inherit the previous episode's drawing. Only
    `populatePlayer` re-arms it."""
    assert "endLineVisual();" in function_body("speakText")
    assert 'beginLineVisual("player"' in function_body("populatePlayer")
    assert "endLineVisual();" in function_body("stopSpeech")


def test_the_reveal_is_driven_by_playback_position():
    """No animation clock anywhere: the renderer is fed from the same ticker
    that paints the progress bar, with the same two numbers. That is what makes
    pause, seek, rewind and 1.5x work without any of them being handled."""
    renderer = (ROOT / "static" / "fam-line.js").read_text(encoding="utf-8")
    assert "setInterval" not in renderer
    assert "requestAnimationFrame" not in renderer
    assert "paintLineVisual(seconds, total)" in function_body("refreshProgressNow")
