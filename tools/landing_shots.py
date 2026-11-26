"""Retake the pictures of the app on the waitlist page's homepage.

The page shows the real interface rather than a drawing of it (PROBLEMS.md
§198, §698). Four of the six screens are photographs of the preview build -
the shipped `static/index.html` answered from its fixtures - at phone size,
in the dark theme the app opens in, with the preview's own banner removed
and a sample account signed in. These things are changed for the camera, at
the owner's direction:

* **Only tiles with a painted picture are shown.** The fixtures carry no
  paintings, so DailyFAM's tiles are dressed in real cards cut from screens
  of the app (`tools/landing/tiles/`, one file per card, named for the rail
  it came from). `SKIP_TILES` leaves out the cards about a death, an
  execution and a strike: not what a first visitor should meet.
* **Explore's thumbnails and the profile's vibes are painted** from
  `tools/landing/home/art/`, paintings cut from the owner's screenshots.
* **The friends are four real people, shown with their photos and first
  names** (`tools/landing/home/people/`; the owner, 11.26: "replace the
  profiles shown through the app with people's that have images attached").
  Until then the fixtures' people were given names nobody has (§198).
  The account whose profile is shown is Brady's.
* **The Morning mix has a cover**, `tools/landing/morning-cover.jpg`.

Retake them whenever a screen they show changes:

    python preview/build_preview.py
    python tools/landing_shots.py

Writes `static/landing/home/{dailyfam,search,messages,profile}.jpg`. The
rest of that folder is cut from the owner's own screenshots and is not
retaken here: `explore.jpg` and `mixes.jpg` (the two myFAM tabs, the status
bar painted out), the three episode cards, the mix covers and the `art-`
paintings. `static/landing/player.jpg` is `STILLS`, as before.
"""
from __future__ import annotations

import asyncio
import base64
import io
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGE = ROOT / "preview" / "fam-artifact.html"
OUT = ROOT / "static" / "landing" / "home"
STILL_OUT = ROOT / "static" / "landing"
CHROME = "/opt/pw-browsers/chromium"
COVER = ROOT / "tools" / "landing" / "morning-cover.jpg"
TILES = ROOT / "tools" / "landing" / "tiles"
PEOPLE = ROOT / "tools" / "landing" / "home" / "people"
ART = ROOT / "tools" / "landing" / "home" / "art"
#: Cards the homepage does not open on (see the docstring).
SKIP_TILES = {"foryou-2", "trending-1", "trending-3"}

#: The fixtures' sample people -> the people the page shows (first names
#: only; whole words; the initials are whole text nodes, the avatar letters).
FAKE_NAMES = {
    "Beth Solomon": "Beth", "Rachel Solomon": "Elizabeth", "Mike Solomon": "Ian",
    "Rachel": "Elizabeth", "Mike": "Ian", "RACHEL": "ELIZABETH", "MIKE": "IAN",
    "Nadia Okoro": "Ava Kim", "Nadia": "Ava",
    "@rachels": "@elizabeth", "@rachel": "@elizabeth", "@mike": "@ian", "@nadia": "@ava",
}
FAKE_INITIALS = {"RS": "E", "MS": "I", "NO": "AK"}

#: file name -> the script that puts the interface on that screen, and the
#: theme it is shown in: all dark, the look the app opens in, beside the
#: owner's own dark screenshots (§698; §250's some-light-some-dark went with
#: the section it was for).
SHOTS = [
    ("dailyfam", "openMyFamTab()", "dark"),
    ("search", "setTab('home')", "dark"),
    ("messages", "openMessages()", "dark"),
    ("profile", "openProfile()", "dark"),
]

#: name -> cut from a screenshot of the app, not photographed: the preview
#: cannot show the player on an episode with a painted picture.
STILLS = [
    ("player", "the player on a real episode, from the owner's phone"),
]

#: Whatever the last screen left open, and anything that is the preview
#: talking about itself rather than the app.
TIDY = """
document.querySelectorAll('.modal-overlay.active')
  .forEach(function(o){ o.classList.remove('active'); });
document.querySelectorAll('body > div').forEach(function(d){
  if(d.style.position === 'fixed' && /Preview build/.test(d.textContent)) d.remove();
});
var f = document.getElementById('followerOverlay');
if(f){ f.classList.remove('active'); f.hidden = true; }
"""

#: Each rail's tiles become real painted cards from the rail they came from:
#: Made for you's from Made for you, Trending's from Trending, and the rest
#: from the friends rail. A rail with no cards of its own is left alone (and
#: so loses its tiles below).
PAINT_TILES = """(cards) => {
  var used = {};
  document.querySelectorAll('.feed-section').forEach(function(sec){
    var head = (sec.querySelector('.feed-title') || sec).textContent;
    var kind = /made for you/i.test(head) ? 'foryou'
             : /trending/i.test(head) ? 'trending'
             : /friend|missed|most played/i.test(head) ? 'friends' : '';
    if(!kind) return;
    sec.querySelectorAll('.seed-card').forEach(function(card){
      var list = cards[kind] || [], at = used[kind] || 0;
      if(at >= list.length) return;
      used[kind] = at + 1;
      card.innerHTML = '<img class="seed-img" alt="" src="' + list[at] + '"'
        + ' style="position:static;display:block;width:100%;height:auto">';
      card.style.cssText += ';padding:0;border:0;background:none;border-radius:13px;overflow:hidden';
    });
  });
}"""

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

#: Explore's thumbnails, the profile's vibes, the friends' faces and the
#: name on the profile. `{art, people}` are data URIs by file stem.
DRESS = """(d) => {
  var art = d.art, ppl = d.people;
  var byCat = {sport:'stadium', business:'office', money:'trading', technology:'lab', health:'plates', culture:'carpet', science:'harbour', world:'street'};
  document.querySelectorAll('.xp-card').forEach(function(c, i){
    var img = c.querySelector('.xp-thumb img'); if(!img) return;
    var cat = (c.innerText || '').toLowerCase(), pick = null;
    Object.keys(byCat).forEach(function(k){ if(!pick && cat.indexOf(k) === 0) pick = byCat[k]; });
    img.src = art[pick || ['tennis','hall','newsroom','podium'][i % 4]];
    img.style.objectFit = 'cover'; img.style.width = '100%'; img.style.height = '100%';
  });
  var grid = ['harbour','plates','lab','office','street','tennis','carpet'];
  document.querySelectorAll('#screen-profile .yf-pic').forEach(function(b, i){
    b.style.backgroundImage = 'linear-gradient(to top, rgba(20,17,27,.82), rgba(20,17,27,0) 62%), url(' + art[grid[i % grid.length]] + ')';
    b.style.backgroundSize = 'cover'; b.style.backgroundPosition = 'center';
  });
  function face(av, who){
    if(!av) return;
    av.innerHTML = '<img alt="" src="' + ppl[who] + '" style="width:100%;height:100%;border-radius:50%;object-fit:cover;display:block">';
    av.style.padding = '0'; av.style.overflow = 'hidden';
  }
  document.querySelectorAll('.yf-friend').forEach(function(f){
    var l = f.getAttribute('aria-label') || '';
    var who = /Your story/.test(l) ? 'brady' : /Beth/.test(l) ? 'beth' : /Rachel/.test(l) ? 'elizabeth' : /Mike/.test(l) ? 'ian' : null;
    if(who) face(f.querySelector('.av'), who);
  });
  face(document.querySelector('.thread-row[data-thread="u_beth"] .av'), 'beth');
  face(document.querySelector('.thread-row[data-thread="u_mike"] .av'), 'ian');
  face(document.querySelector('#screen-profile .yf-photo .av'), 'brady');
  var swap = {'Set up your profile':'Brady', 'Set up profile':'Edit profile'};
  var walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT), n;
  while((n = walk.nextNode())){
    var t = n.nodeValue.trim();
    if(swap[t]) n.nodeValue = n.nodeValue.replace(t, swap[t]);
    else if(/no handle yet/.test(n.nodeValue)) n.nodeValue = n.nodeValue.replace('no handle yet', '@brady');
  }
}"""

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
        cards: dict[str, list[str]] = {}
        def uris(folder):
            return {p.stem: "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode()
                    for p in sorted(folder.glob("*.jpg"))}
        dress = {"art": uris(ART), "people": uris(PEOPLE)}
        kept = [p for p in TILES.glob("*.jpg") if p.stem not in SKIP_TILES]
        for path in sorted(kept, key=lambda p: (p.stem.split("-")[0],
                                                                int(p.stem.split("-")[1]))):
            cards.setdefault(path.stem.split("-")[0], []).append(
                "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode())
        for name, script, theme in SHOTS:
            await page.evaluate("(t) => setTheme(t)", theme)
            await page.evaluate(TIDY)
            try:
                await page.evaluate(script)
            except Exception as exc:  # a screen that will not open is news
                errors.append(f"{name}: {exc}")
                continue
            await page.wait_for_timeout(2500)
            await page.evaluate(TIDY)
            await page.evaluate(PAINT_TILES, cards)
            await page.evaluate(DESIGNED_TILES_ONLY)
            await page.evaluate(DRESS, dress)
            await page.evaluate(RENAME, names)
            await page.wait_for_timeout(200)
            target = await page.query_selector(".phone") or await page.query_selector("body")
            png = await target.screenshot()
            Image.open(io.BytesIO(png)).convert("RGB").save(
                OUT / f"{name}.jpg", quality=80, optimize=True, progressive=True)
            print(f"  {name:15} captured")
        await browser.close()
    for name, _why in STILLS:
        still = Image.open(ROOT / "tools" / "landing" / "stills" / f"{name}.jpg").convert("RGB")
        still = still.resize((780, round(780 * still.height / still.width)))
        still.save(STILL_OUT / f"{name}.jpg", quality=82, optimize=True, progressive=True)
        print(f"  {name:15} from its still")
    if errors:
        print("errors:", errors)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(capture()))
