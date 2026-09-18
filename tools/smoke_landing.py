"""Drive the share landing page in a real browser.

`/s/<id>` is the surface a stranger meets first, and it is the one page in FAM
whose whole value is a *restriction*: play works, and every other control is a
door to the App Store. A restriction is exactly the kind of property that
passes every unit test while being broken on the page - the button is there,
the handler is bound, and it does nothing, or it does something worse and
navigates into the app.

So this presses them. It runs against `preview/fam-share-landing.html`, which
is the shipped page with silence in place of a voice, and it fails the build
rather than printing a note - a check that exists is not a check that fails
(PROBLEMS.md §101).

    python preview/build_share_preview.py && python tools/smoke_landing.py
"""
from __future__ import annotations

import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT = ROOT / "preview" / "fam-share-landing.html"

APP_STORE = "apps.apple.com"


def launch_browser(pw):
    """Playwright's own download first; any installed Chromium after.

    Lifted from `smoke_preview.py` deliberately rather than imported: that
    module does its work at import time, and a browser launcher shared through
    a side effect is worse than eight duplicated lines.
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


def main() -> int:
    from playwright.sync_api import sync_playwright

    target = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    if not target.exists():
        print(f"no landing preview at {target} - run "
              f"python preview/build_share_preview.py", file=sys.stderr)
        return 1

    failures: list[str] = []
    ran: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        ran.append(name)
        print(("  ok    " if ok else "  FAIL  ") + name
              + (f"  [{detail}]" if detail and not ok else ""))
        if not ok:
            failures.append(name)

    with sync_playwright() as pw:
        browser = launch_browser(pw)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(target.resolve().as_uri())
        page.wait_for_timeout(700)

        check("The landing page runs with no script errors", not errors,
              "; ".join(errors[:2]))
        check("It says which episode was shared",
              "Who Makes the Chips" in page.inner_text("#title"))
        check("It says what was asked",
              "semiconductor" in page.inner_text("#ask"))
        check("It says how long the episode is", "3 min" in page.inner_text("#meta"))

        # The rule the page exists to keep.
        doors = page.query_selector_all("[data-door]")
        check("Every control that is not play is a door", len(doors) >= 4,
              f"{len(doors)} doors")

        page.click("#play")
        page.wait_for_timeout(1800)
        check("Pressing play starts the episode",
              page.evaluate("window.FamAudio.position()") > 0)
        check("The play button becomes a pause button",
              page.get_attribute("#play", "aria-label") == "Pause")
        check("The clock runs", page.inner_text("#now") != "0:00")
        check("The progress bar fills", page.evaluate(
            "parseFloat(getComputedStyle(document.getElementById('fill')).width)") > 0)
        check("Fifteen seconds back is reachable once it is playing",
              page.get_attribute("#back", "disabled") is None)

        page.click("#play")
        page.wait_for_timeout(300)
        check("Pressing it again pauses", page.evaluate("window.FamAudio.isPaused()"))

        # Intercepted at the network layer: `location.href` is not redefinable
        # in Chromium, so watching for the request is the honest way to ask
        # where a door actually goes.
        tried: list[str] = []
        page.route("**/*", lambda route: (tried.append(route.request.url),
                                          route.abort()))
        page.click("[data-door='get']")
        page.wait_for_timeout(600)
        check("A door goes to the App Store", any(APP_STORE in u for u in tried),
              str(tried[:3]))
        check("And never into the app itself",
              not any("/api/audio" in u or u.endswith("/index.html") for u in tried),
              str(tried[:3]))

        browser.close()

    if failures:
        print(f"\n{len(failures)} landing behaviour(s) FAILED")
        return 1
    # Counted, never written down. A number in prose does not fail when
    # somebody adds a behaviour - which is how the one in CLAUDE.md went wrong.
    print(f"\nall {len(ran)} landing behaviours passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
