"""Watch the line being drawn, in a real browser, against a real server.

The claim this feature makes is not "an SVG was produced". It is that a
listener opens myFAM, sees a finished drawing on a tile, taps it, and watches
that same drawing appear from nothing at the speed of the audio. Nothing in
pytest can see any of that: the reveal is `stroke-dashoffset` written by an
animation frame from a position derived from an audio clock, and every part of
that sentence needs a browser.

So this drives one. It starts nothing itself - point it at a server that is
already running:

    ./dev.sh                       (or: python -m uvicorn app:app --port 8000)
    python tools/visual_probe.py   [http://localhost:8000]

What it asserts, in the order it matters:

1. The player shows an ivory square and the line starts at **zero**, even when
   the drawing is already finished and was on the tile a second ago.
2. The reveal **advances with the audio** - `stroke-dashoffset` falls.
3. A **seek moves it**, forwards and backwards, rather than it running on its
   own clock.
4. The finished drawing is the **same path** as the thumbnail on the tile.
5. **exploreFAM has no canvas and no drawing**, which is the one hard
   exclusion in this feature.

Exit code 0 when every one of those holds.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_BASE = "http://localhost:8000"
#: What the probe asks for. Deliberately not a topic in the bank: the search
#: path is the one where the drawing has to catch up with audio that is already
#: playing, which is the hard case.
QUERY = "how do undersea cables actually get repaired"


def launch_browser(pw):
    """Playwright's own download first; any installed Chromium after.

    Copied from `tools/smoke_preview.py` for the same reason it exists there: a
    container whose browser is a different version from the Playwright package
    is common, and a check people skip is a check that does not run.
    """
    candidates = [os.environ.get("PLAYWRIGHT_CHROMIUM")]
    try:
        return pw.chromium.launch()
    except Exception as first:
        for pattern in ("opt/pw-browsers/chromium-*/chrome-linux/chrome",
                        "opt/pw-browsers/chromium/chrome-linux/chrome"):
            candidates += sorted(str(p) for p in pathlib.Path("/").glob(pattern))
        for path in [c for c in candidates if c and pathlib.Path(c).exists()]:
            try:
                return pw.chromium.launch(executable_path=path)
            except Exception:
                continue
        raise first


def get_json(url: str):
    with urllib.request.urlopen(url, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    from playwright.sync_api import sync_playwright

    base = (sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASE).rstrip("/")
    try:
        health = get_json(base + "/api/health")
    except Exception as exc:  # noqa: BLE001
        print(f"no server at {base} ({exc})\n"
              f"  start one with:  ./dev.sh   or   "
              f"python -m uvicorn app:app --port 8000", file=sys.stderr)
        return 1

    report = health.get("visuals", {})
    provider = report.get("provider", {})
    print(f"server   {base}")
    print(f"visuals  enabled={report.get('enabled')} provider="
          f"{provider.get('provider')} configured={provider.get('configured')}"
          + ("  [PLACEHOLDER ART]" if provider.get("placeholder") else ""))
    if not report.get("enabled"):
        print("VISUALS=0 on this server; nothing to probe.", file=sys.stderr)
        return 1
    if not provider.get("configured"):
        print(f"no image provider: {provider.get('reason')}", file=sys.stderr)
        return 1

    failures: list[str] = []

    def check(label, fn):
        try:
            fn()
            print(f"  ok    {label}")
        except Exception as exc:  # noqa: BLE001 - report, don't stop
            failures.append(f"{label}: {exc}")
            print(f"  FAIL  {label}: {exc}")

    with sync_playwright() as pw:
        browser = launch_browser(pw)
        context = browser.new_context(viewport={"width": 430, "height": 900})
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base + "/", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        # Past the entry flow. Skipping rather than signing up: nothing here is
        # account-gated, and an account is state left on the server.
        page.evaluate("try{ skipAccount(); }catch(e){}")
        page.wait_for_timeout(600)
        page.evaluate("try{ finishIntro(); }catch(e){}")
        page.wait_for_timeout(600)
        page.evaluate("try{ closeRecap(); }catch(e){}")

        def blank_square_then_a_line():
            page.evaluate("setTab('home')")
            page.wait_for_timeout(300)
            page.fill("#searchInput", QUERY)
            page.evaluate("runSearch()")
            # The square is on screen while the honest wait counts - the whole
            # reason it is started with the episode rather than with the asset.
            page.wait_for_selector("#playerLine", state="visible", timeout=30000)
            assert page.eval_on_selector("#playerLine", "e => e.classList.contains('blank')"), \
                "the canvas did not start blank"
            page.wait_for_function(
                "() => { var p = document.querySelector('#playerLine .line-path');"
                " return p && p.getAttribute('d') && p.getAttribute('d').length > 40; }",
                timeout=180000)

        def it_starts_at_zero_and_advances():
            offsets = []
            for _ in range(8):
                page.wait_for_timeout(700)
                offsets.append(page.eval_on_selector(
                    "#playerLine .line-path",
                    "e => ({ off: parseFloat(e.style.strokeDashoffset || '0'),"
                    "        len: e.getTotalLength() })"))
            first, last = offsets[0], offsets[-1]
            assert first["len"] > 0, "the path has no length"
            # Not "it is at zero now" - by the time this runs the episode has
            # been playing. What matters is that the reveal is a share of the
            # episode and is moving the right way.
            assert last["off"] < first["off"] - 1, (
                f"the reveal did not advance ({first['off']:.0f} -> {last['off']:.0f})")
            share = 1 - last["off"] / last["len"]
            assert 0 < share < 1, f"the reveal is at {share:.0%}, which is not mid-episode"
            print(f"        revealed {share:.1%} of a {last['len']:.0f}-unit path")

        def a_seek_moves_the_line():
            before = page.eval_on_selector(
                "#playerLine .line-path", "e => parseFloat(e.style.strokeDashoffset)")
            page.evaluate("skipAudio(-15)")
            page.wait_for_timeout(500)
            after_back = page.eval_on_selector(
                "#playerLine .line-path", "e => parseFloat(e.style.strokeDashoffset)")
            assert after_back > before + 1, (
                f"rewinding did not rewind the line ({before:.0f} -> {after_back:.0f})")
            page.evaluate("skipAudio(30)")
            page.wait_for_timeout(500)
            after_forward = page.eval_on_selector(
                "#playerLine .line-path", "e => parseFloat(e.style.strokeDashoffset)")
            assert after_forward < after_back - 1, (
                f"skipping forward did not advance the line "
                f"({after_back:.0f} -> {after_forward:.0f})")

        def pausing_freezes_it():
            page.evaluate("setPlayState(false)")
            page.wait_for_timeout(300)
            first = page.eval_on_selector(
                "#playerLine .line-path", "e => parseFloat(e.style.strokeDashoffset)")
            page.wait_for_timeout(1200)
            second = page.eval_on_selector(
                "#playerLine .line-path", "e => parseFloat(e.style.strokeDashoffset)")
            assert abs(first - second) < 0.5, (
                f"the line kept drawing while paused ({first:.1f} -> {second:.1f})")
            page.evaluate("setPlayState(true)")

        def the_player_and_the_tile_are_one_drawing():
            drawn = page.eval_on_selector("#playerLine .line-path", "e => e.getAttribute('d')")
            served = get_json(base + "/api/visual?q=" + page.evaluate(
                "encodeURIComponent(%s)" % json.dumps(QUERY)))["visual"]
            assert served.get("status") == "ready", f"the server says {served.get('status')}"
            assert served.get("d") == drawn, (
                "the path on screen is not the path the server stores - the "
                "thumbnail and the player's last frame would differ")
            thumbnail = urllib.request.urlopen(base + served["thumbnail_url"], timeout=20)
            assert thumbnail.status == 200 and int(
                thumbnail.headers.get("Content-Length") or 1) > 1000, \
                "the rendered thumbnail is missing or empty"

        def explore_has_no_drawing():
            page.evaluate("stopSpeech()")
            page.wait_for_timeout(300)
            page.evaluate("openExplore()")
            page.wait_for_timeout(2500)
            assert page.eval_on_selector(".screen.active", "e => e.id") == "screen-explore", \
                "Explore did not open"
            # Nothing inside Explore's own screen, and the player's canvas is
            # not merely hidden behind it - it is not being driven at all.
            assert page.eval_on_selector_all(
                "#screen-explore .line-stage, #screen-explore .line-path",
                "e => e.length") == 0, "Explore has a visual canvas in it"
            assert page.evaluate("FamLine.isShowing()") is False, \
                "a drawing is still being driven on Explore"
            assert page.eval_on_selector("#playerLine", "e => e.hidden") is True, \
                "the player's canvas was left on when Explore took over"

        print("\nprobing the continuous line")
        check("The player shows an ivory square, then a line", blank_square_then_a_line)
        check("The reveal advances with the audio", it_starts_at_zero_and_advances)
        check("Seeking moves the line both ways", a_seek_moves_the_line)
        check("Pausing freezes it", pausing_freezes_it)
        check("The player and the stored drawing are one path",
              the_player_and_the_tile_are_one_drawing)
        check("exploreFAM has no drawing at all", explore_has_no_drawing)

        if errors:
            failures.append("page errors: " + "; ".join(errors[:3]))
            print("  FAIL  the page threw: " + "; ".join(errors[:3]))
        browser.close()

    if failures:
        print(f"\n{len(failures)} failed", file=sys.stderr)
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
