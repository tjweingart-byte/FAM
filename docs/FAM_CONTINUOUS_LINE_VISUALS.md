# The continuous-line episode visual

*A single line connecting us all.*

An eligible episode can carry one continuous-line illustration. It starts as a
blank ivory square and is revealed, stroke by stroke, as the episode plays. When
the episode ends the drawing is complete, and that completed frame is the
episode's thumbnail. Different topics, same line.

The line is not decoration. It is an idea becoming understandable: ambiguity
first, then exploration, then forms, then a subject you recognise — arriving at
the same moment the episode does.

This document is the whole of Phase 1: what the app does, what it deliberately
does not do, and where the future generation service plugs in.

---

## What Phase 1 is, and is not

**The app renders artwork. It never makes any.** Assets are produced elsewhere —
today by hand, later by the service in *Phase 2* below — and dropped into a
folder. The app's entire responsibility is to find one, check it is really a
single line, load it, cache it, reveal it in step with playback, show the
finished frame on tiles, and be completely unbothered when there is no asset at
all, which is the case for every episode today.

Not in Phase 1, and not by omission: image generation, vectorisation, visual
metaphor selection, video rendering, and anything at all that makes playback
wait for a picture.

---

## Player scope, and why exploreFAM is excluded

FAM has three listening surfaces, all driven by the same `FamAudio` engine:

| Surface | Screen | Continuous line |
|---|---|---|
| Now Playing | `#screen-player` | **yes** |
| Play All (segmented album) | `#screen-playall` | not in Phase 1 — see below |
| **exploreFAM** (the reel) | `#screen-explore` | **never** |

**exploreFAM is excluded by product decision, and the exclusion is structural
rather than conditional.** Explore is a reel: episodes are replays from the
shared cache, one fills the screen, and a swipe deals the next. A drawing that
starts blank on every swipe would be a square of nothing for most of the time
anyone spends there — and Explore's design is not being changed for this.

Three things enforce it, in descending order of how hard they are to break:

1. **There is no element to draw into.** `#lineCanvas` exists exactly once in
   `static/index.html`, inside `#screen-player`. Explore's markup is untouched.
2. **Explore's code never calls the renderer.** Explore plays through
   `playReel()` and paints through `refreshReelProgress()`; neither mentions
   `FamLine`. `tests/test_episode_visuals.py` reads those function bodies and
   fails if one ever does.
3. **A named gate.** `CONTINUOUS_VISUAL_SURFACES = ["player"]`, checked by
   `continuousVisualAllowed(surface)` before anything is armed.

And a fourth, for the leak that would actually happen: `speakText()` — the one
function every path into playback goes through — calls `endLineVisual()` first,
so a square from the previous episode can never survive into the next one.
Only `populatePlayer()` re-arms it.

**Play All** is eligible in principle and deferred deliberately. Its layout is
built around the circular `.art` element, and putting a square there is a
redesign of a player this change was told not to redesign. Wiring it up later is
two lines: a `.line-canvas` element in `#screen-playall` and `"playall"` added
to `CONTINUOUS_VISUAL_SURFACES`.

---

## Architecture

```
  artwork produced elsewhere
            |
            v
  ~/.fam/visuals/<visual_id>.svg      (+ optional index.json)
            |
   episode_visuals.py                 find it, validate it, describe it
            |
            +--> GET /api/visual?q=...          the record
            +--> GET /api/visual/asset/<name>   the bytes
            |
   static/fam-line.js                 fetch, parse, measure, reveal
            ^
            |  progress = position / duration
   refreshProgressNow()               the existing progress ticker
```

Five files carry it:

| File | Job |
|---|---|
| `episode_visuals.py` | Finds an asset for a query, validates it, describes it. Makes nothing. |
| `app.py` | `/api/visual`, `/api/visual/asset/{name}`, the `visuals` block on `/api/health`, and `_attach_tile_visuals` for feed tiles. |
| `static/fam-line.js` | `FamLine`: fetch, cache, parse, measure once, reveal. No clock. |
| `static/index.html` | The square, the surface gate, and the three calls that drive it. |
| `tools/seed_visual.py` | Puts a fixture asset where the server will find it, so the feature can be seen working. |

### Identity: keyed on the subject, not the episode

`visual_id(query)` is a hash of `cache.normalize_query(query)` and nothing else.
Length is deliberately not part of it, for the same reason voice is not part of
the script cache key: **a longer episode about a subject is the same subject, and
the drawing illustrates the subject.** So "NFL week 5 recap" and "recap: week 5,
NFL" resolve to one drawing, and changing the length pill reuses it.

It also makes a feed tile resolvable, which it would not be if the id needed a
length nobody has chosen yet.

---

## The asset contract

### Where assets live

`~/.fam/visuals/`, overridable with `FAM_VISUALS_DIR` (or `VISUALS_DIR`). Per-
machine state, beside the voice models and for the same reason: artwork changes
far less often than the code, and a new copy of the app must find it already
there. A deployment points the variable at its mounted disk.

```
~/.fam/visuals/
  e2b49160122c5135.svg      the vector, named by visual_id
  e2b49160122c5135.png      optional raster of the finished frame
  index.json                optional manifest
```

A bare `<visual_id>.svg` is a complete installation. The manifest only exists
for things a file cannot say:

```json
{
  "why do NFL teams move cities": { "status": "processing" },
  "e2b49160122c5135": {
    "version": 2,
    "vector": "nfl-cities-v2.svg",
    "thumbnail": "nfl-cities-v2.png",
    "background": "#F8F4EA",
    "stroke": "#171820",
    "stroke_width": 1.4
  }
}
```

Keys may be a raw query or a visual id; a query is normalised on load, so
whoever writes the file uses whichever they have. A manifest that is not
readable JSON is logged and ignored — the bare-file convention still works.

### What a valid asset is

One `<path>`, no fill, on a square canvas:

```svg
<svg viewBox="0 0 1000 1000">
  <path id="fam-line" d="…" fill="none" stroke="#171820" stroke-width="1.4"
        stroke-linecap="round" stroke-linejoin="round"/>
</svg>
```

`episode_visuals.validate_svg` refuses anything else and names what is wrong:

* not parseable SVG, or not an `<svg>` root
* no drawable path
* **more than one drawable element** — a second `<path>`, or a `<circle>`,
  `<rect>`, `<polyline>`, anything. This is the rule the visual language rests
  on: the feeling is that the pen never leaves the page, and two drawables
  reveal as two strokes appearing in sequence, which is a different product.
* a drawable that is not a `<path>`
* a filled path — a silhouette reveals as a growing blob, not a travelling pen

An empty `<path d="">` is ignored rather than counted; vector editors leave
those behind and refusing them would reject usable artwork.

**Refusal is never silent, and never a repair.** The record comes back
`failed` with the reason, the server logs it, and the client refuses the same
file again for itself rather than trusting that the server did. Repairing
upstream is Phase 2's job; doing it here would mean the app quietly shipping a
drawing nobody approved.

### The record

`GET /api/visual?q=<what the listener asked>`

```json
{"visual": {
  "id": "e2b49160122c5135",
  "status": "ready",
  "version": 1,
  "vector_url": "/api/visual/asset/e2b49160122c5135.svg",
  "thumbnail_url": "/api/visual/asset/e2b49160122c5135.svg",
  "view_box": "0 0 1000 1000",
  "background_color": "#F8F4EA",
  "stroke_color": "#171820",
  "stroke_width": 1.4,
  "reason": ""
}}
```

`status` is one of:

| status | meaning | the player shows |
|---|---|---|
| `none` | no asset, none expected — today's normal case | no square at all |
| `processing` | an asset is coming | blank ivory square |
| `ready` | an asset is here and passed validation | the reveal |
| `failed` | an asset is here and is not usable; `reason` says why | no square at all |

The completed line *is* the thumbnail, so `thumbnail_url` falls back to the
vector when no raster was supplied: one file, two jobs, and no second artefact
to keep in step with the first.

The endpoint is **not** gated on surface. Which players draw this is a client
decision — and exploreFAM's answer is never — because the same endpoint has to
serve the iOS app, whose surfaces this server has never heard of.

### Why no square for `none` and `failed`

This was a judgement call and it is worth stating, because the brief left it
open. The Now Playing screen has **never had artwork**. An ivory square that
appears on every episode and stays empty forever would read as something broken
rather than something absent — and an episode with no drawing is the ordinary
case, not a fault. So:

* `none` and `failed` → no square. The player is exactly what it has always
  been. The two are told apart in telemetry, not on screen.
* `processing` → the square, because there *is* something coming and an empty
  frame is a promise being kept.

---

## The animation contract

```
progress = clamp(position / duration, 0, 1)
```

**There is no animation clock anywhere in this feature.** `fam-line.js` contains
no `setInterval` and no `requestAnimationFrame`; a test asserts that it never
gains one. The renderer is fed from `refreshProgressNow()` — the ticker that was
already painting the progress bar — with the same two numbers it paints the bar
from, and from `paintPlayerScrub()` while a finger is on the bar.

Every transport behaviour then falls out rather than being handled:

| Gesture | Why it works |
|---|---|
| pause | position stops advancing (the `AudioContext` is suspended), so the drawing freezes |
| resume | position advances again |
| seek / scrub | position jumped, so the drawing jumps with it |
| rewind | position went down, so the line un-draws |
| 1.5× | a normalised position does not care about rate |
| duration unknown | `paintLineVisual` does nothing until `total > 0`; the square stays blank |
| episode ends before the asset loads | the asset arrives and draws at the position already reached |

Rendered with `stroke-dasharray` / `stroke-dashoffset`: geometry is parsed and
measured once, and a tick writes one CSS property on one element. No re-parse, no
re-measure, no layout.

---

## Caching

Assets are cached in their own IndexedDB database, `fam-visuals`, for seven
days — long enough that a daily listener never refetches one, short enough that
a corrupted row is never permanent. A row that no longer parses is dropped, so
the next play refetches instead of failing forever.

**Its own database, not a second store inside `fam-offline`.** That one holds
downloaded episodes — the audio a listener explicitly asked to keep — and the
rule for this feature is that it cannot interfere with playback. Sharing a
connection and a version number with the audio store is exactly how it would.
Everything about IndexedDB here is best-effort: a browser that refuses to open
it simply fetches the asset each time.

Server side, `/api/visual/asset/...` is served with `Cache-Control: public,
max-age=86400`, and the URL carries `?v=<version>` — an asset is immutable for
a given id, so new artwork for the same subject arrives as a new version.

**Preloading** is the metadata request in `beginLineVisual`, fired when the
player screen opens — *after* `FamAudio.play` has already been asked for, never
before it. It is one small GET that the first word does not wait on.

---

## Fallback behaviour

Every failure at every stage resolves to *draw nothing*. None of them reaches
the listener as an error, and none of them touches audio.

| Failure | What happens |
|---|---|
| no visual metadata | no square |
| `/api/visual` unreachable or slow | no square; audio unaffected |
| malformed or dead `vector_url` | `visual_asset_failed`, no square |
| SVG will not parse | `visual_parse_failed`, cached row dropped, no square |
| more than one path | server says `failed`; the client refuses it again anyway |
| path measures zero length | `visual_render_error`, no square |
| `duration == 0` or not yet known | square stays blank until it is |
| listener moved on mid-flight | the stale response is discarded (`lineVisualStamp`) |
| corrupt cached asset | dropped on the parse failure, refetched next play |
| IndexedDB unavailable | fetched from the network every time |
| `fam-line.js` failed to load | `continuousVisualAllowed` is false; nothing is armed |

---

## Telemetry

FAM has no client analytics pipeline. `/api/event` is the **taste log** — it
feeds the recommender — and putting render failures into it would teach the
ranker about SVGs. So the client half lands in a ring buffer and the console,
and the server counts its own half.

Client (`FamLine.telemetry()`, `FamLine.counts()`, and `console.debug`):
`visual_asset_request`, `visual_asset_loaded`, `visual_asset_cache_hit`,
`visual_asset_failed`, `visual_parse_failed`, `visual_ready_latency_ms`,
`visual_first_draw_ms`, `visual_render_error`.

Server (`/api/health` → `visuals`): whether the feature is on, where it looks,
how many vectors are installed, whether there is a manifest, and counts of
lookups by outcome plus assets served and refused. `record()` in `fam-line.js`
is the one function to change when there is a pipeline to send these to.

---

## Feature gating

```
showContinuousVisual =
      EPISODE_VISUALS is on (server)
  AND surface is in CONTINUOUS_VISUAL_SURFACES   (never "explore")
  AND visual.status == "ready"
```

`EPISODE_VISUALS=1` is the default and is reported on `/api/health`. On with an
empty folder is today's behaviour to the last pixel, so the flag exists to switch
the whole feature off in a hurry rather than to opt into it. FAM has no feature-
flag framework; this is an environment variable, like every other setting, and
`.env.example` carries it so the file people copy agrees with the code.

---

## Manual test procedure

```bash
python tools/seed_visual.py "why do NFL teams move cities"
./dev.sh                       # then open the printed address
```

Search for *exactly* that query and open the player.

| Step | Expected |
|---|---|
| 0:00 | blank ivory square |
| ~0:18 of 3:00 | a small portion of the path |
| ~1:19 | roughly the first half |
| ~2:37 | nearly complete |
| 3:00 | the whole illustration |
| pause at 1:00 | the line stops |
| resume | it continues from there |
| drag from 0:30 to 2:30 | it jumps to about 83% |
| rewind to 0:45 | it returns to about 25% |
| 1.5× | still in step; the same fraction at the same position |
| reload with the network off | the cached asset still draws |
| `--broken` seed | audio plays, no square, `failed` with the reason |
| open exploreFAM | no square, layout and behaviour unchanged |

Other states:

```bash
python tools/seed_visual.py "still being drawn" --status processing   # blank square
python tools/seed_visual.py "two strokes" --broken                    # refused
python tools/seed_visual.py --list
```

Automated: `python -m pytest tests/test_episode_visuals.py -q` covers identity,
validation, records, the routes, the tile payload and the exploreFAM exclusion;
`python tools/smoke_preview.py` drives the reveal in a real browser off real
playback (*"The line is revealed by playback"*, *"Explore never draws the
line"*).

---

## Performance

* The vector is parsed and measured **once** per asset. A playback tick writes
  one CSS property on one element and does no measuring, parsing or layout.
* Sub-`PROGRESS_EPSILON` changes are skipped, so a paused player writes nothing.
* `vector-effect="non-scaling-stroke"` keeps a hairline a hairline at any size,
  so one asset serves every screen and nothing is re-rendered on resize.
* Feed tiles cost one `is_file()` per tile against a folder that is usually not
  there at all — and `record_for` returns immediately when the folder is
  missing, which is one stat rather than a hundred and twenty.
* Only `ready` records are attached to feed tiles. A tile has no state to show
  for the others, so sending them would be five kilobytes per feed describing a
  decision no tile makes.
* Nothing here is awaited by the audio path, and the metadata request goes out
  after `FamAudio.play` has been asked for.

---

## Risks

* **Nobody has seen this with real artwork.** The fixture is a Lissajous figure,
  chosen because it has a length to reveal and could not be mistaken for a FAM
  visual. Whether a *real* single-line drawing reads well while half-revealed is
  a judgement call that needs real assets — and it is the same shape of gap as
  open problem #1 in `CLAUDE.md`: built, tested, unheard.
* **Reveal order is the path's own order.** A drawing whose path starts in the
  middle of the subject will reveal from the middle. That is an upstream
  constraint on how assets are drawn, not something the app can fix, and it
  belongs in the Phase 2 validation list.
* **Path length is not time.** The reveal is uniform in *length*, so a dense
  region takes as long as an empty sweep. Good enough, and worth revisiting
  with real artwork before it is worth complicating.
* **`getTotalLength()` on a very long path** is measured once, but a
  pathological asset could still make that measurement slow. The validator has
  no size ceiling yet; one belongs in the Phase 2 list.
* **The shared-cache/thumbnail overlap.** Scripts are shared between listeners,
  and so are drawings — two people asking the same question get the same line.
  That is the intent, and it is also why the folder must never hold artwork
  derived from anything personal.

---

## Phase 2: the generation service

Nothing below is built. Phase 1 is shaped so that it can be plugged in without
touching a player.

```
Episode Intelligence
   |
   +--> episode / audio generation
   |
   +--> visual brief
            |
            v
      Visual Service   POST /episode-visuals
            |          { episodeId, title, visualBrief: {subject,
            v            visualMetaphor, tone, complexity} }
      single-line vector
            |
            v
      validation / repair
            |
            v
      CDN / object storage
            |
            v
      episode.visual   { status, version, vectorUrl, thumbnailUrl }
```

The integration points already exist:

* **`episode_visuals.record_for`** is the only thing that knows where assets
  come from. Pointing it at object storage instead of a folder changes one
  function; the record it returns, the endpoints, the client and the players are
  unaffected.
* **`status: "processing"`** already exists and already renders, so an
  asynchronous service has a state to report while it works.
* **`version`** is already the cache key end to end, so regenerated artwork
  invalidates itself.
* **`validate_svg`** is already the gate. The future checks — exactly one
  drawable path (done), no disconnected strokes, no fills (done), approved
  stroke thickness, square canvas, safe margins, no embedded text, subject
  recognisability, strong negative space, a final frame that works as a
  thumbnail — belong beside it, and the ones that need a model belong in the
  service rather than here.
* **The app never learns which model drew anything.** It consumes a validated
  asset contract, and that is the whole interface.
