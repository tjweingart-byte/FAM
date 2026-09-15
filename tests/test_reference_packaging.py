"""The house style has to reach the machine that draws.

`tests/test_visual_references.py` proves the references reach the image model
from the disk they are on. This proves they reach the disk: it is the step in
front of that one, and it is the step that was missing.

`visual_references/README.md` has said "they are committed" since the day the
folder was named, and `.gitignore` carries a paragraph explaining why the
folder is deliberately not ignored. Both were true. Neither was checked, and
the three approved illustrations were never actually added to the repository -
so every deployment built from it drew in words-only style, said so in one log
line, and looked healthy from every other angle.

That is the shape of failure this project keeps paying for: a rule written
down, a runtime warning nobody is standing next to, and a working laptop. The
laptop has the files; the container gets a git clone, and a clone carries what
was committed and nothing else.

So this asserts the deployable state rather than the local one:

* In a checkout, every named reference must be **tracked by git**. Present on
  disk is not enough - that is exactly the state that shipped words-only art.
* In an image or a tarball, where there is no git to ask, every named
  reference must be **on disk**, which is the same question asked of the only
  evidence there is.

It fails until the artwork is committed, which is correct: while it is red,
the repository genuinely cannot produce a deployment that draws in FAM's
style.

It catches a second macOS-shaped trap on the way past, for free: git records
the name it was given, and this compares it case-sensitively, so `Runner.png`
- which resolves perfectly on a case-insensitive Mac and not at all in a Linux
container - fails here rather than in a deployment log.
"""
from __future__ import annotations

import subprocess

import pytest

import visual_style


def _tracked_reference_files() -> set[str] | None:
    """Filenames git is tracking in `visual_references/`, or None if it cannot
    be asked - an image, a tarball, an export."""
    try:
        done = subprocess.run(
            ["git", "ls-files", "--", "visual_references"],
            cwd=visual_style.PROJECT_ROOT, capture_output=True, text=True,
            timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return {line.rsplit("/", 1)[-1] for line in done.stdout.split("\n") if line.strip()}


def _names_for(stem: str) -> list[str]:
    return [stem + suffix for suffix in visual_style.REFERENCE_SUFFIXES]


def test_every_active_reference_ships_with_the_code():
    """The set named in ACTIVE_REFERENCES must travel with a deploy.

    The failure message is the fix, because the machine that hits this is the
    one holding the artwork.
    """
    tracked = _tracked_reference_files()
    on_disk = set(visual_style.available())

    missing = []
    for stem in visual_style.ACTIVE_REFERENCES:
        wanted = _names_for(stem)
        if tracked is not None:
            if not any(name in tracked for name in wanted):
                where = "on disk but not committed" if any(
                    name in on_disk for name in wanted) else "nowhere in the repository"
                missing.append(f"{stem} ({where})")
        elif not any(name in on_disk for name in wanted):
            missing.append(f"{stem} (not in visual_references/)")

    assert not missing, (
        "FAM's house-style references are not in the repository, so every "
        "deployment built from it describes the style in words instead of "
        "being shown it: " + ", ".join(missing) + ".\n"
        "A container is built from a git clone, and a clone carries what was "
        "committed. From the machine that holds the artwork:\n"
        "    git add -f visual_references/*.png\n"
        "    git commit -m 'Ship the approved references'\n"
        "    git push\n"
        "Then confirm on the deployed server:\n"
        "    curl -s https://<host>/api/health | python -m json.tool"
        "  # visuals.style.references_missing must be []")


def test_a_reference_on_disk_is_actually_an_image():
    """What is committed has to be the artwork, not a stand-in for it.

    Two ways a tracked file passes every other check here and still reaches the
    image model as nothing: a Git LFS pointer (about 130 bytes of text, on a
    machine where LFS is not installed to smudge it back), and an empty or
    truncated file from an interrupted copy. `visual_style.resolve` only asks
    whether a path is a file, and the provider attaches whatever bytes are
    there - so the request goes out with a text file where an illustration
    should be and the model draws from the words alone again, which is the
    failure this whole module exists to close.
    """
    magic = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"RIFF")
    for stem in visual_style.ACTIVE_REFERENCES:
        path = visual_style.resolve(stem)
        if path is None:
            continue          # not on this machine; the check above owns that
        head = path.read_bytes()[:8]
        assert head.startswith(magic), (
            f"{path.name} is tracked but is not an image - it starts "
            f"{head!r}. A Git LFS pointer or a truncated copy reaches the "
            f"image model as bytes it cannot read, and FAM falls back to "
            f"drawing from the words. Commit the artwork itself, not a "
            f"pointer to it.")


def test_a_reserve_reference_is_not_required_to_be_present():
    """The reserve is a record of a decision, not a shipping requirement.

    Held-back artwork may or may not be in the folder; what it must never do
    is fail a build, because it is by definition not being drawn with.
    """
    assert set(visual_style.RESERVE_REFERENCES).isdisjoint(
        visual_style.ACTIVE_REFERENCES)
    visual_style.missing()  # names the active set only


def test_nothing_in_the_ignore_rules_hides_the_references():
    """The folder ships, and no ignore rule may quietly take it back.

    Both files carry a paragraph saying so. This is that paragraph as a check,
    asked of git itself rather than of the wording - a pattern added later for
    some other reason (`*.png`, say) would otherwise remove the house style
    from every future deployment without a line of code changing.
    """
    if _tracked_reference_files() is None:
        pytest.skip("no git here - nothing to ask about ignore rules")
    for stem in visual_style.ACTIVE_REFERENCES:
        for name in _names_for(stem):
            done = subprocess.run(
                ["git", "check-ignore", "-v", f"visual_references/{name}"],
                cwd=visual_style.PROJECT_ROOT, capture_output=True, text=True,
                timeout=30, check=False)
            assert done.returncode != 0, (
                f"visual_references/{name} is ignored by "
                f"{done.stdout.strip()} - it can never be committed, so it can "
                f"never reach a deployment")
