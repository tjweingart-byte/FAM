"""Retake the pictures of the app on the waitlist page's "What is FAM" section.

The landing page shows the real interface rather than a drawing of it
(PROBLEMS.md §198). These are photographs of the preview build - the shipped
`static/index.html` answered from its fixtures - at phone size, with the
preview's own banner removed and a sample account signed in so the mixes
screen has something on it. Three things are changed for the camera, at the
owner's direction:

* **Only tiles with a painted picture are shown.** A tile still on its
  placeholder drawing is taken off the screen (and a rail left with none
  goes with it). The fixtures carry no paintings, so today that is every
  tile; once tiles have pictures, they appear.
* **Friends have made-up names** (`FAKE_NAMES`): the fixtures' sample
  people are named after real people.
* **The Morning mix has a cover**, `tools/landing/morning-cover.jpg`, set
  through the same PATCH the app's cover editor sends.

Retake them whenever a screen they show changes:

    python preview/build_preview.py
    python tools/landing_shots.py

Writes `static/landing/*.jpg`, the files `static/waitlist.html` names.
"""
from __future__ import annotations

import asyncio
import base64
import io
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGE = ROOT / "preview" / "fam-artifact.html"
OUT = ROOT / "static" / "landing"
CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
COVER = ROOT / "tools" / "landing" / "morning-cover.jpg"

#: The fixtures' sample people -> names nobody has. Whole words only; the
#: initials are whole text nodes (the avatar letters).
FAKE_NAMES = {
    "Beth Solomon": "Maya Brooks", "Rachel Solomon": "Jordan Reyes",
    "Mike Solomon": "Sam Patel", "Nadia Okoro": "Ava Kim",
    "Beth": "Maya", "Rachel": "Jordan", "Mike": "Sam", "Nadia": "Ava",
    "@beth": "@maya", "@rachels": "@jordan", "@rachel": "@jordan",
    "@mike": "@sam", "@nadia": "@ava",
}
FAKE_INITIALS = {"BS": "MB", "RS": "JR", "MS": "SP", "NO": "AK"}

#: file name -> the script that puts the interface on that screen. In order:
#: the mix detail opens from the mixes screen the shot before it leaves open.
SHOTS = [
    ("search", "setTab('home')"),
    ("dailyfam", "setTab('myfam')"),
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

#: Tiles still on the placeholder drawing come off the screen, and so does a
#: rail that is left with nothing on it.
DESIGNED_TILES_ONLY = """
document.querySelectorAll('.seed-card').forEach(function(c){
  if(!c.querySelector('.seed-img')) c.remove();
});
document.querySelectorAll('.feed-rail, .sec-grid').forEach(function(r){
  if(r.querySelector('.seed-card')) return;
  var sec = r.closest('.feed-section') || r;
  sec.remove();
});
"""

RENAME = r"""(names) => {
  // Longest first, so "Beth Solomon" is taken before "Beth". The names are
  // letters, spaces and "@", so none needs escaping.
  var whole = names.whole, initials = names.initials;
  var keys = Object.keys(whole).sort(function(a, b){ return b.length - a.length; });
  var re = new RegExp("(^|[^\\w@])(" + keys.join("|") + ")(?!\\w)", "g");
  var walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  var node;
  while((node = walk.nextNode())){
    var t = node.nodeValue, trimmed = t.trim();
    if(initials[trimmed]){ node.nodeValue = t.replace(trimmed, initials[trimmed]); continue; }
    var swapped = t.replace(re, function(_, pre, name){ return pre + whole[name]; });
    if(swapped !== t) node.nodeValue = swapped;
  }
}"""

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
        cover = "data:image/jpeg;base64," + base64.b64encode(COVER.read_bytes()).decode()
        await page.evaluate("""(cover) => fetch("/api/mixes").then(function(r){ return r.json(); })
          .then(function(d){
            var m = (d.mixes || []).filter(function(x){ return x.name === "Morning"; })[0];
            if(!m) throw new Error("no Morning mix to give a cover");
            return fetch("/api/mixes/" + m.id, { method: "PATCH",
              headers: {"Content-Type": "application/json"},
              body: JSON.stringify({ cover: cover }) });
          })""", cover)
        names = {"whole": FAKE_NAMES, "initials": FAKE_INITIALS}
        for name, script in SHOTS:
            await page.evaluate(TIDY)
            try:
                await page.evaluate(script)
            except Exception as exc:  # a screen that will not open is news
                errors.append(f"{name}: {exc}")
                continue
            await page.wait_for_timeout(2500)
            await page.evaluate(TIDY)
            await page.evaluate(DESIGNED_TILES_ONLY)
            await page.evaluate(RENAME, names)
            await page.wait_for_timeout(200)
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
