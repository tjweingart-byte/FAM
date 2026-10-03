"""Retake the pictures of the app on the waitlist page's "What is FAM" section.

The landing page shows the real interface rather than a drawing of it
(PROBLEMS.md §198). These are photographs of the preview build - the shipped
`static/index.html` answered from its fixtures - at phone size, with the
preview's own banner removed and a sample account signed in so the mixes
screen has something on it. Retake them whenever a screen they show changes:

    python preview/build_preview.py
    python tools/landing_shots.py

Writes `static/landing/*.jpg`, the files `static/waitlist.html` names.
"""
from __future__ import annotations

import asyncio
import io
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGE = ROOT / "preview" / "fam-artifact.html"
OUT = ROOT / "static" / "landing"
CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"

#: file name -> the script that puts the interface on that screen. In order:
#: the mix detail opens from the mixes screen the shot before it leaves open.
SHOTS = [
    ("search", "setTab('home')"),
    ("dailyfam", "setTab('myfam')"),
    ("dailyfam-rails", "setTab('myfam');"
                       " var sc = document.querySelector('#screen-myfam .scroll');"
                       " if(sc) sc.scrollTop = 560;"),
    ("myfam-mixes", "openPlayFAM()"),
    ("myfam-mix", "document.querySelectorAll('.mix-card')[0].click()"),
    ("friends", "openProfile(); setTimeout(function(){ openFriends(); }, 400)"),
    ("explore", "openExplore()"),
]

#: Whatever the last screen left open, and anything that is the preview
#: talking about itself rather than the app.
TIDY = """
document.querySelectorAll('.modal-overlay.active')
  .forEach(function(o){ o.classList.remove('active'); });
if(window.stopNextUpTimer) stopNextUpTimer();
document.querySelectorAll('body > div').forEach(function(d){
  if(d.style.position === 'fixed' && /Preview build/.test(d.textContent)) d.remove();
});
var f = document.getElementById('followerOverlay');
if(f){ f.classList.remove('active'); f.hidden = true; }
"""

SIGN_UP = """() => fetch("/api/auth/signup", {
  method: "POST", headers: {"Content-Type": "application/json"},
  body: JSON.stringify({email: "landing@example.com", password: "a-long-enough-password"})
}).then(function(){ return refreshAuth(); })"""


async def capture() -> int:
    from PIL import Image
    from playwright.async_api import async_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        kwargs = {"executable_path": CHROME} if pathlib.Path(CHROME).exists() else {}
        browser = await p.chromium.launch(**kwargs)
        page = await browser.new_page(viewport={"width": 390, "height": 844},
                                      device_scale_factor=2)
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(PAGE.as_uri())
        await page.evaluate("finishEntry()")
        await page.wait_for_timeout(1500)
        await page.evaluate(SIGN_UP)
        await page.wait_for_timeout(1500)
        for name, script in SHOTS:
            await page.evaluate(TIDY)
            try:
                await page.evaluate(script)
            except Exception as exc:  # a screen that will not open is news
                errors.append(f"{name}: {exc}")
                continue
            await page.wait_for_timeout(2500)
            await page.evaluate(TIDY)
            target = await page.query_selector(".phone") or await page.query_selector("body")
            png = await target.screenshot()
            Image.open(io.BytesIO(png)).convert("RGB").save(
                OUT / f"{name}.jpg", quality=80, optimize=True, progressive=True)
            print(f"  {name:15} captured")
        await browser.close()
    if errors:
        print("errors:", errors)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(capture()))
