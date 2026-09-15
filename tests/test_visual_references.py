"""The approved illustrations actually reach the image model.

This is a wiring test, and it exists because every part of this path fails
silently. `visual_references/` can be empty, or gitignored, or full of files the
loader skips, or read by a loader whose result nobody passes on - and in every
one of those cases the app still produces illustrations, still reports healthy,
and simply stops looking like FAM. Nothing raises. The only way to know is to
watch the bytes leave.

So these tests put real files on disk, run the real provider against a mock
transport, and assert on the actual HTTP request: the right endpoint, the file
bytes in the body, and the sentence that makes the images outrank the words.

The rule this is the test for: **a description and a demonstration of the same
style never agree exactly**, so the prompt has to say which one wins. Without
that, the model averages them, and the average is the generic look the style
block exists to rule out.
"""
from __future__ import annotations

import base64
import dataclasses
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import line_processor  # noqa: E402
import visual_director  # noqa: E402
import visual_provider  # noqa: E402
import visual_style  # noqa: E402
import visuals  # noqa: E402
from config import settings  # noqa: E402


def a_png(seed: int) -> bytes:
    """A real, distinguishable PNG. Distinguishable matters: the assertion is
    that *these* bytes were sent, so two references must not be identical."""
    import numpy as np

    image = np.zeros((8, 8, 3), dtype=np.uint8)
    image[:, :] = (248, 244, 234)
    image[seed % 8, :] = (23, 24, 32)
    return line_processor.encode_png(image)


@pytest.fixture
def approved(tmp_path, monkeypatch):
    """Three approved illustrations on disk, where the loader looks."""
    folder = tmp_path / "visual_references"
    folder.mkdir()
    written = {}
    for index, stem in enumerate(visual_style.ACTIVE_REFERENCES):
        name = f"{stem}.png"
        data = a_png(index + 1)
        (folder / name).write_bytes(data)
        written[name] = data
    # Deliberately ignored: a folder people drop files into collects files that
    # are not illustrations, and an image model handed one is a wasted request.
    (folder / "notes.txt").write_text("not an illustration")
    (folder / ".DS_Store").write_bytes(b"\x00\x01")
    monkeypatch.setattr(visual_style, "REFERENCE_DIR", folder)
    return written


class Recorder:
    """A transport that answers like the image API and keeps the request."""

    def __init__(self) -> None:
        self.requests: list = []

    def install(self, monkeypatch, reply_png: bytes | None = None) -> None:
        reply = reply_png if reply_png is not None else a_png(4)
        payload = {"data": [{"b64_json": base64.b64encode(reply).decode("ascii")}]}
        recorder = self

        def handler(request: httpx.Request) -> httpx.Response:
            request.read()          # materialise the body before it is kept
            recorder.requests.append(request)
            return httpx.Response(200, json=payload)

        # The real class, captured before the name is replaced.
        # `visual_provider.httpx` is the global module, so patching the
        # attribute rebinds `httpx.AsyncClient` everywhere - including inside
        # this factory, which then calls itself.
        real = httpx.AsyncClient

        def factory(*args, **kwargs):
            return real(transport=httpx.MockTransport(handler))

        monkeypatch.setattr(visual_provider.httpx, "AsyncClient", factory)

    @property
    def only(self) -> httpx.Request:
        assert len(self.requests) == 1, f"{len(self.requests)} requests, expected 1"
        return self.requests[0]

    @property
    def body(self) -> bytes:
        return self.only.content


def a_brief():
    return visual_director.from_payload({
        "subject": "AI infrastructure expansion",
        "primary_form": "semiconductor chip",
        "visual_metaphor": "chip circuitry opening out into a city grid",
    })


def provider() -> visual_provider.OpenAIImageProvider:
    return visual_provider.OpenAIImageProvider(api_key="sk-test-not-a-real-key")


# --------------------------------------------------------------------------
# The loader
# --------------------------------------------------------------------------
def test_the_loader_finds_the_illustrations_and_skips_everything_else(approved):
    found = visual_style.references()
    # The manifest's order, which is not alphabetical - that is the point.
    assert [ref.name for ref in found] == \
        [f"{stem}.png" for stem in visual_style.ACTIVE_REFERENCES]
    for ref in found:
        assert base64.b64decode(ref.data_b64) == approved[ref.name], \
            f"{ref.name} was loaded as different bytes than are on disk"
        assert ref.media_type == "image/png"


def test_the_folder_is_read_every_time_rather_than_cached(approved):
    """Swapping a reference has to take effect without a restart - most of what
    makes this folder a usable lever is being able to try one."""
    assert len(visual_style.references()) == 3
    (visual_style.REFERENCE_DIR / f"{visual_style.ACTIVE_REFERENCES[0]}.png").unlink()
    assert len(visual_style.references()) == 2


def test_the_active_set_is_named_rather_than_whichever_files_sort_first(approved):
    """The whole change. A selection that depended on alphabetical order would
    move the moment somebody added a file, renamed one, or copied the folder
    onto a filesystem that sorts differently - silently, and taking the whole
    product's look with it."""
    assert visual_style.ACTIVE_REFERENCES == (
        "meditation", "profile-globe-city", "runner")
    assert len(visual_style.ACTIVE_REFERENCES) == visual_style.MAX_REFERENCES

    # A file that sorts before every one of them is still not used.
    (visual_style.REFERENCE_DIR / "0000-interloper.png").write_bytes(a_png(7))
    found = [ref.name for ref in visual_style.references()]
    assert "0000-interloper.png" not in found
    assert found == [f"{stem}.png" for stem in visual_style.ACTIVE_REFERENCES]


def test_the_whale_is_held_in_reserve(approved):
    """Recorded as a decision rather than inferred from an absence: it is the
    densest of the four and would bias every episode toward more line than the
    style wants."""
    assert "whale" in visual_style.RESERVE_REFERENCES
    assert "whale" not in visual_style.ACTIVE_REFERENCES

    (visual_style.REFERENCE_DIR / "whale.png").write_bytes(a_png(9))
    found = [ref.name for ref in visual_style.references()]
    assert "whale.png" not in found
    report = visual_style.report()
    assert report["references_reserve"] == ["whale.png"]
    assert report["reference_note"] == "", "a reserve file is not a problem"


def test_a_named_reference_that_is_not_on_disk_is_loud(approved, caplog):
    """The failure that matters. FAM would otherwise draw in two thirds of the
    style it was told to draw in, and report healthy while doing it."""
    import logging

    (visual_style.REFERENCE_DIR / "runner.png").unlink()
    with caplog.at_level(logging.WARNING):
        found = visual_style.references()
    assert len(found) == 2
    assert "runner" in caplog.text
    assert "partial style" in caplog.text

    report = visual_style.report()
    assert report["references_missing"] == ["runner"]
    assert "partial style" in report["reference_note"]
    # And still drawable: a missing reference costs style, never the picture.
    assert found


def test_it_does_not_matter_which_format_they_were_saved_as(approved):
    """The manifest names stems. Somebody saving a JPEG instead of a PNG is not
    a configuration error."""
    (visual_style.REFERENCE_DIR / "runner.png").unlink()
    (visual_style.REFERENCE_DIR / "runner.jpg").write_bytes(a_png(3))
    found = [ref.name for ref in visual_style.references()]
    assert "runner.jpg" in found
    assert len(found) == 3
    assert visual_style.missing() == []


def test_the_folder_ships_with_the_code():
    """It was gitignored, and that was wrong in a way nobody would have noticed.

    The references are what the model is *shown*; a deployment without them
    describes the style in words instead, produces visibly different art, and
    reports nothing louder than an empty list on a health page. Ignoring the
    folder meant they worked on one laptop and silently stopped working
    everywhere else.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ignored = [line.strip() for line in
               open(os.path.join(root, ".gitignore")).read().splitlines()
               if line.strip() and not line.strip().startswith("#")]
    assert "visual_references/" not in ignored
    assert "visual_references" not in ignored
    assert os.path.isdir(os.path.join(root, "visual_references")), \
        "the folder people are told to drop files into does not exist"


# --------------------------------------------------------------------------
# The request
# --------------------------------------------------------------------------
def test_the_reference_bytes_are_in_the_request(approved, monkeypatch):
    """The whole point, asserted on the wire rather than on the call.

    Not "the provider was handed references" - that is the cheaper question,
    and it is the one that was already true while the bytes went nowhere.
    """
    import asyncio

    recorder = Recorder()
    recorder.install(monkeypatch)
    asyncio.run(provider().generate(a_brief(), references=visual_style.references()))

    assert str(recorder.only.url).endswith("/images/edits"), \
        "references were loaded but the request did not go to the endpoint that takes them"
    body = recorder.body
    for name, data in approved.items():
        assert data in body, f"{name} was not in the request body"
        assert name.encode() in body, f"{name} was not named in the multipart form"


def test_the_prompt_says_the_images_outrank_the_words(approved, monkeypatch):
    import asyncio

    recorder = Recorder()
    recorder.install(monkeypatch)
    asyncio.run(provider().generate(a_brief(), references=visual_style.references()))

    body = recorder.body.decode("utf-8", "replace")
    assert "THE ATTACHED IMAGES ARE THE STYLE" in body
    assert "FOLLOW THE IMAGES" in body
    # Named, so the record beside the finished artwork says what governed it.
    for name in approved:
        assert name in body
    # And the subject still gets there - the preamble is added to the prompt,
    # never instead of it.
    assert "semiconductor chip" in body
    assert "city grid" in body
    # A strip of three is a perfectly reasonable thing to hand over, and the
    # model has to be told not to answer with one.
    assert "ONE illustration on ONE square canvas" in body


def test_the_request_asks_for_an_opaque_png(approved, monkeypatch):
    """Both matter downstream. A transparent background lets whatever is behind
    the image become the ivory, and the ivory is part of the product; a format
    other than PNG is undecodable on a deployment without Pillow, which is
    supported."""
    import asyncio

    recorder = Recorder()
    recorder.install(monkeypatch)
    asyncio.run(provider().generate(a_brief(), references=visual_style.references()))

    body = recorder.body.decode("utf-8", "replace")
    assert "opaque" in body
    assert "png" in body


def test_the_recorded_prompt_is_the_one_that_was_sent(approved, monkeypatch):
    """It was not, and that is the kind of bug you only meet while debugging
    something else: the style prompt was stored and the reference preamble was
    added later, so the record described a request nobody made."""
    import asyncio

    recorder = Recorder()
    recorder.install(monkeypatch)
    image = asyncio.run(
        provider().generate(a_brief(), references=visual_style.references()))

    body = recorder.body.decode("utf-8", "replace")
    assert image.prompt in body, \
        "the prompt kept beside the artwork is not the prompt that was sent"
    assert image.references == sorted(approved)


def test_with_no_references_it_falls_back_to_plain_generation(monkeypatch, tmp_path):
    """Not a failure - it is the behaviour before the folder existed, and it
    still produces an illustration. It is simply the weaker of the two."""
    import asyncio

    monkeypatch.setattr(visual_style, "REFERENCE_DIR", tmp_path / "nothing-here")
    recorder = Recorder()
    recorder.install(monkeypatch)
    asyncio.run(provider().generate(a_brief(), references=visual_style.references()))

    assert str(recorder.only.url).endswith("/images/generations")
    # JSON here rather than multipart - there are no files to send - so the
    # prompt is read back as a field rather than searched for in the raw body.
    import json

    sent = json.loads(recorder.body)["prompt"]
    assert "THE ATTACHED IMAGES ARE THE STYLE" not in sent
    assert visual_style.style_block() in sent


# --------------------------------------------------------------------------
# The wiring above the provider
# --------------------------------------------------------------------------
def test_a_real_episode_sends_the_references(approved, monkeypatch):
    """End to end from `visuals.request`, because the provider being correct is
    only half of it - something has to hand it the references, and that is a
    separate line of code in a separate module."""
    import asyncio

    monkeypatch.setattr(visuals, "settings",
                        dataclasses.replace(settings,
                                            visual_image_provider="openai",
                                            visual_director=False,
                                            visual_understanding_wait_seconds=0.1))
    monkeypatch.setattr(visual_provider, "settings",
                        dataclasses.replace(settings,
                                            visual_image_provider="openai"))
    monkeypatch.setattr(visual_provider, "_image_key", lambda: "sk-test-not-a-real-key")

    # Real line art back from the "model", so the whole pipeline runs on it -
    # and rich enough to be art rather than an icon, because that is now a
    # thing FAM checks before it vectorises anything (visual_validator.
    # screen_source). A plain ring is exactly what that gate exists to refuse.
    reply = line_processor.rasterise_polyline(
        visual_provider._figure(4242, 4), 512, 2.2)

    recorder = Recorder()
    recorder.install(monkeypatch, reply_png=reply)

    async def run():
        visual_id = visuals.request("what is happening with ai data centres",
                                    surface="search")
        assert visual_id
        for _ in range(400):
            await asyncio.sleep(0.05)
            record = visuals.store().get(visual_id)
            if record and record.status in ("ready", "failed", "unconfigured"):
                return record
        raise AssertionError("the drawing never finished")

    record = asyncio.run(run())
    assert record.status == "ready", record.error
    assert str(recorder.only.url).endswith("/images/edits")
    for data in approved.values():
        assert data in recorder.body
    # And the record remembers which references drew it.
    assert record.metrics["provider"]["references"] == sorted(approved)


def test_switching_references_off_is_possible_and_visible(approved, monkeypatch):
    """One setting, for comparing what the references buy. It changes the
    endpoint, which is the honest way to tell the two runs apart."""
    import asyncio

    monkeypatch.setattr(visuals, "settings",
                        dataclasses.replace(settings,
                                            visual_use_references=False))
    assert visuals.settings.visual_use_references is False
    recorder = Recorder()
    recorder.install(monkeypatch)
    asyncio.run(provider().generate(a_brief(), references=[]))
    assert str(recorder.only.url).endswith("/images/generations")


# --------------------------------------------------------------------------
# What health says
# --------------------------------------------------------------------------
def test_health_names_the_references_that_are_in_force(approved):
    report = visual_style.report()
    assert report["references"] == sorted(approved)
    assert report["reference_note"] == "", \
        "with references present there is nothing to warn about"


def test_health_says_so_loudly_when_there_are_none(monkeypatch, tmp_path):
    monkeypatch.setattr(visual_style, "REFERENCE_DIR", tmp_path / "nothing-here")
    report = visual_style.report()
    assert report["references"] == []
    assert "visual_references/" in report["reference_note"]
