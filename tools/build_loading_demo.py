"""A standalone page for looking at the loading screen.

The screen only exists while an episode is being written, which on a cache
hit is 450ms and against fixtures is less. That is correct behaviour and
useless for judging how it looks, so this lifts it out and holds it still.

Everything here is *extracted* from static/index.html rather than copied:
the markup, the CSS, the palette and the fonts all come from the shipped
file at build time. A second hand-written copy would drift, and then the
thing being judged would not be the thing that ships.

    python tools/build_loading_demo.py     ->  preview/loading-screen.html
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE = ROOT / "static" / "index.html"
OUT = ROOT / "preview" / "loading-screen.html"


def slice_between(text: str, start: str, end: str, what: str) -> str:
    a = text.find(start)
    if a == -1:
        sys.exit(f"could not find the start of {what} in static/index.html")
    b = text.find(end, a + len(start))
    if b == -1:
        sys.exit(f"could not find the end of {what} in static/index.html")
    return text[a:b]


def build() -> str:
    src = SOURCE.read_text()

    fonts = "\n".join(
        line for line in src.splitlines()
        if "fonts.googleapis.com" in line or "fonts.gstatic.com" in line
    )
    palette = slice_between(src, "  :root{", "  }", "the palette") + "  }"
    css = slice_between(
        src, "  /* -------- the loading screen -------- */", "  .demo-badge{",
        "the loading screen's CSS")
    gen_text = slice_between(src, "  .gen-text{", "  }", ".gen-text") + "  }"
    markup = slice_between(
        src, '      <div class="fam-loading" id="famLoading"', "\n      <!--",
        "the loading screen's markup")

    if "famRise" not in css or "GEN_MIN" in css:
        sys.exit("the extracted CSS does not look like the loading screen")

    # The states the screen actually takes. A written episode walks five
    # steps (§147), each checked off from the server's own marks; `shown` is
    # how many are checked. A machine that cannot write says so underneath.
    states = [
        ("Contextualizing", 0, ""),
        ("Retrieving", 1, "3s"),
        ("Verifying", 2, "5s"),
        ("Finalizing", 3, "13s"),
        ("Generating audio", 4, "15s"),
        ("No API key", 0, "Playing the built-in sample script…"),
        ("Key rejected", 0, "The server's API key was rejected…"),
    ]
    buttons = "\n".join(
        f'      <button onclick="setStatus({i})">{label}</button>'
        for i, (label, _, _) in enumerate(states)
    )
    status_js = ",\n".join(f'    [{shown}, {text!r}]' for _, shown, text in states)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FAM — the loading screen</title>
{fonts}
<style>
{palette}
  *{{ box-sizing:border-box; -webkit-tap-highlight-color:transparent; }}
  body{{
    margin:0; min-height:100vh; background:var(--page-bg); color:var(--paper);
    font-family:'Space Grotesk', sans-serif;
    padding:34px 18px 60px; display:flex; flex-direction:column; align-items:center; gap:22px;
  }}
  .lead{{ max-width:430px; text-align:center; }}
  .lead h1{{ font-family:'Fraunces', serif; font-weight:500; font-size:25px;
    color:var(--copper); margin:0 0 8px; }}
  .lead p{{ font-size:12.5px; line-height:1.6; color:var(--text-muted); margin:0; }}
  /* The same 300x620 frame the preview uses, so proportions are honest. */
  .phone{{ width:300px; height:620px; position:relative; }}
  .frame{{
    position:absolute; inset:0; border-radius:34px; overflow:hidden;
    background:var(--bg); border:1px solid var(--border-strong);
    box-shadow:0 30px 70px -24px rgba(0,0,0,0.7);
  }}
  .controls{{ display:flex; flex-wrap:wrap; gap:7px; justify-content:center; max-width:430px; }}
  .controls button{{
    background:var(--surface); color:var(--text); border:1px solid var(--border-strong);
    border-radius:20px; padding:8px 13px; font-family:'JetBrains Mono', monospace;
    font-size:10.5px; cursor:pointer;
  }}
  .controls button:active{{ transform:scale(0.96); }}
  .controls button.on{{ background:var(--copper); color:var(--ink); border-color:var(--copper); }}
  .note{{ max-width:430px; text-align:center; font-family:'JetBrains Mono', monospace;
    font-size:10px; line-height:1.7; color:var(--muted); }}
  .note b{{ color:var(--copper); font-weight:500; }}

{css}
{gen_text}
</style>
</head>
<body>
  <div class="lead">
    <h1>The loading screen</h1>
    <p>Held still so it can be looked at. In the app it is shown the moment a
    search, a tile or a mix is tapped, and cleared the instant the first audio
    arrives — so it lasts as long as the wait does, and no longer.</p>
  </div>

  <div class="phone"><div class="frame">
{markup}  </div></div>

  <div class="controls" id="controls">
{buttons}
      <button onclick="walk()">Play a real-looking wait</button>
      <button onclick="flash()">Show a cache hit (450ms)</button>
  </div>

  <div class="note">
    Each circle is checked off when the server says that step has finished
    (<b>PROBLEMS.md §147</b>), and each is on screen for at least two seconds,
    so a slow step reads as slow and a fast one still reads. The real-looking
    wait uses uneven step times on purpose. A replay has nothing to write: it
    shows all five done and lasts 450ms.
  </div>

<script>
  var STATUSES = [
{status_js}
  ];
  var el = document.getElementById("famLoading");
  var line = document.getElementById("famLoadingStatus");
  var buttons = document.getElementById("controls").querySelectorAll("button");

  var items = el.querySelectorAll(".fam-steps li");
  var walking = [];

  function paint(shown){{
    items.forEach(function(li, n){{
      li.classList.toggle("done", n < shown);
      li.classList.toggle("active", n === shown);
    }});
  }}

  function stopWalk(){{ walking.forEach(clearTimeout); walking = []; }}

  function setStatus(i){{
    stopWalk();
    el.classList.add("active", "stepped");
    paint(STATUSES[i][0]);
    line.textContent = STATUSES[i][1];
    buttons.forEach(function(b, n){{ b.classList.toggle("on", n === i); }});
  }}

  // Uneven on purpose - the brief, a fast retrieval held to its two-second
  // floor, the writer's long planning, the first sentence, the first audio.
  function walk(){{
    stopWalk();
    el.classList.add("active", "stepped");
    line.textContent = "";
    buttons.forEach(function(b){{ b.classList.remove("on"); }});
    var at = 0;
    paint(0);
    [3100, 2000, 7800, 2000, 2000].forEach(function(ms, n){{
      at += ms;
      walking.push(setTimeout(function(){{ paint(n + 1); }}, at));
    }});
  }}

  // What a replay actually looks like: all five done, held to the floor, and
  // cleared. Anything shorter is the flash the floor exists to prevent.
  function flash(){{
    stopWalk();
    el.classList.remove("active");
    paint(5);
    setTimeout(function(){{ el.classList.add("active", "stepped"); }}, 260);
    setTimeout(function(){{ el.classList.remove("active"); }}, 260 + 450);
  }}

  setStatus(0);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    OUT.write_text(build())
    print(f"{OUT}  ({OUT.stat().st_size // 1024} KB)")
