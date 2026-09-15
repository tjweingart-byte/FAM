# A single line connecting us all

Every eligible FAM episode gets **one** continuous-line illustration. The same
vector does both jobs it is asked to do: finished, it is the episode's
thumbnail; being drawn, it is the player.

There is deliberately no second artwork for the second job, and no video. One
episode, one drawing, one ordered path — and the audio is what draws it.

    progress = clamp(currentTime / duration, 0, 1)

That one line is the whole player. Pause, resume, seek, rewind, 1.5x, 2x, lock
the phone and come back: the reveal is a pure function of playback position, so
none of them needs a special case and none of them can drift out of step.

---

## The north star

FAM is **not** one simple icon drawn with one line.

FAM is **one rich idea → interpreted as one beautiful editorial illustration →
revealed through one continuous line.**

**The art comes first. The engineering preserves it.** That ordering is the
single most load-bearing sentence in this document, because every threshold in
the pipeline is a way to quietly invert it: skeletonising, routing and
animating are all *easier* on simpler artwork, so a system tuned for its own
convenience converges on pictograms without anybody deciding that it should.

Concretely, and in the order the work happens:

* The **Visual Director's** job is the strongest editorial interpretation of the
  episode. Not the most drawable one.
* The **image prompt's** job is to ask for that at full richness.
* The **source gate's** job is to refuse art that is not it.
* The **line processor's** job is to *preserve* that artwork and find a route
  through it — never to prefer artwork that happens to route easily.
* The **validator's** job is to notice when the vector stopped being the
  drawing.

Two consequences that read as rules elsewhere in this file, and are really this
one rule applied twice: no rung of the retry ladder may ask for a *simpler
picture* (the fix for "it will not route" is forms that **touch**, not fewer
forms), and a beautiful drawing that comes out ugly is a **line-processing
failure** — preserve the source, fix the processing.

### The priority order

Settled. When two of these pull against each other, the lower number wins.

1. **Preserve the artwork.**
2. **Preserve the no-scars rule.**
3. **Prefer visible new drawing over retracing** when both are valid.
4. **Retrace existing ink** when necessary.
5. **Do not let numeric heuristics override visual quality.**

Five is not a caveat on the other four, it is a standing correction to this
whole document: every threshold written down here is a *rejection aid*, not a
definition of good FAM art. A beautiful, sophisticated composition must not be
refused because it scores low on a density measure — and the approved
references are full of exactly that composition. Where a metric and the picture
disagree, **the metric is wrong.**

That is why the source gate has two tiers (hard bounds refuse, targets only
advise), why the one richness bound that *can* refuse is a structural fact
rather than a threshold somebody guessed, and why a badly paced reveal is
noticed and drawn rather than rejected.

Three and four together are the routing rule: retracing is what keeps the pen
off the negative space and is always preferred to a bridge — but among routes
that are equally safe, take the one that keeps new line arriving.

### What a FAM illustration is

Sophisticated editorial line illustration. Warm ivory ground, extremely fine
charcoal line, generous negative space, rich but restrained detail, thoughtful
composition, visual storytelling — and **enough detail that watching the image
emerge feels like discovery.** Scenes, environments, relationships and visual
metaphors, in preference to isolated symbols.

Never: pictograms, generic corporate iconography, clip art, infographic
layouts, logo-driven concepts, "A + B = partnership" imagery, text inside the
artwork, colour, gradients, shading, photorealism, poster design.

**The approved reference images in `visual_references/` are the visual source of
truth. Where the written instructions and the references disagree, follow the
references** — `visual_style.reference_preamble` says exactly that to the model,
because a description and a demonstration of one style never agree, and an
unranked pair gets averaged into the generic look all of this exists to rule
out.

---

## What happens, in order

```
          QUERY / TOPIC / RECOMMENDATION
                       │
               RESEARCH / RETRIEVAL          research.py
                       │
                RESEARCH PACKET
                       │
             EPISODE UNDERSTANDING           episode_intelligence.py
                  ╱          ╲               understanding.py  ← the fork
                 ╱            ╲
        EPISODE WRITER    VISUAL DIRECTOR    visual_director.py
                │                │
          SCRIPT CHUNKS     VISUAL BRIEF
                │                │
               TTS         IMAGE GENERATION  visual_provider.py
                │                │
           AUDIO PLAYS      LINE PROCESSOR   line_processor.py
                                 │
                          CANONICAL SVG      visuals.py (stored)
                          ╱           ╲
                    THUMBNAIL        PLAYER  index.html / fam-line.js
```

**The fork is the design.** The illustration and the script are *siblings*, both
reading the same understanding, and the illustration never waits for the
finished script. It cannot: on search the audio starts before the script is
finished, so an illustrator downstream of the writer could only ever begin after
the listener already had sound.

`understanding.py` is that fork as code. `ScriptGenerator.prepare` announces what
it worked out — the EI brief and the research packet — and carries on; the
drawing job, which is already running, picks it up. One-directional on purpose:
if nothing is listening, nothing happens and nothing is slower.

---

## The five things that must stay true

**1. Nothing here is ever in front of a listener.** `visuals.request` starts a
background task and returns; every CPU-bound step runs in a thread, because the
skeletoniser is heavy enough to be *heard* if it ran on the loop that is
streaming the episode. There is no code path on which audio waits for a picture.

**2. exploreFAM is excluded, in the server and not only in the interface.**
Explore replays finished episodes and is built on the promise that it cannot
spend. A picture is a spend. `visuals.eligible` refuses a replay-only request,
so even a client that asked would get nothing and cause nothing. The interface
half is structural too: Explore's player calls `FamAudio` directly rather than
going through `speakText`, so there is no flag to forget.

**3. One episode is one drawing.** The visual key is the idempotency token. A
tap, a re-tap, a retry, a duplicate event and a server restart produce one
illustration between them.

**4. A layer that adds quality must not subtract availability.** No key, a
refused request, unreadable art, a validator that says no — every one of them
ends as a state on a record, and the episode plays exactly as it did before this
feature existed. **The blank ivory square is a designed state, not an error
state.**

**5. Nothing personal is drawn.** An episode with attachments is that listener's
alone; `pipeline` refuses to cache it, so this refuses to draw it.

**6. The engineering never dictates the art.** No threshold here may be
satisfied by making the illustration worse. When one of them starts rejecting
rich artwork, the threshold is wrong — twice already: `CONTIGUOUS_TOLERANCE`
was a constant that only suited a single figure on an empty page, and
`MAX_RETRACED` was set at 35% when the honest figure for a scene is 30%.

---

## The key

`visuals.key_for(query, context)` — module-level, one implementation, the same
doctrine as `pipeline.key_for`. Warming a drawing before a tap and looking one
up on the tap must compute the same string, and two implementations drift the
first time one gains a field. **If you add something that changes what the
picture is, it goes in there and nowhere else.**

What is in it: the normalised question, the follow-up context, the style name
and the style version.

What is deliberately **not** in it:

* **the length.** A three-minute episode and a ten-minute one about the same
  question are different scripts and the same subject. The same reasoning that
  keeps voice out of the script cache key.
* **the voice, the model, the listener.** None of them changes what the picture
  is of.

The style version *is* in it, so bumping `visual_style.STYLE.version` retires
every stored drawing rather than leaving a feed that is half one style and half
another — which is worse than either style alone.

---

## The four surfaces

| surface | when it draws | what the listener sees |
|---|---|---|
| **search** | the moment the episode starts, in parallel | ivory square, then the line joins at the current playback position |
| **myFAM** | ahead of the tap, when the feed is opened | a finished drawing on the tile; the player redraws it from 0% |
| **DailyFAM** | ahead of the tap, when the mixes load | the same |
| **Go Deeper** | with the follow-up episode | its own drawing — a follow-up is a different episode |
| **exploreFAM** | **never** | unchanged, no canvas, no drawing |

**Search is the hard case and it is the interesting one.** The audio is already
playing when the vector arrives, thirty seconds in. There is no catch-up path
and no restart: the first frame after it arrives is drawn at 30/180 = 17%, and
it continues from there. The visual joins the episode where the episode is.

**The browse surfaces pay none of that**, which is the whole point of them: what
somebody might tap is known before they tap it, so the drawing is finished and
sitting on the tile and the tap pays nothing. Bounded by
`VISUAL_WARM_PER_CYCLE`, and a warm stands aside while a listener is waiting on
a real episode (`VISUAL_QUIET_SECONDS`) — prefetch's rule, for prefetch's
reason.

**Readiness and reveal are different things.** A tile whose picture is finished
still starts its player at 0% and draws it again with the audio. Watching it
appear is the feature.

---

## The line processor

An image model asked for continuous-line art returns something that *reads* as
one stroke and is, as geometry, a few hundred disconnected marks. The player
does not need a picture of a line, it needs an ordered route — there is no such
thing as the first 17% of a pile of strokes.

    decode → isolate ink → despeckle → skeletonise → graph
           → bridge small gaps → eulerise → traverse
           → smooth → simplify → fit curves → one SVG path

Four decisions in there are places the obvious answer is wrong:

* **Retracing is allowed; jumping is not.** A graph with odd-degree vertices has
  no Euler path, and the route-inspection ("Chinese postman") answer is to
  duplicate the cheapest existing edges until one exists. Going back over a line
  already drawn is invisible. A straight line across empty ivory to reach the
  next piece is a scar, and this module will never draw one. It is a hard
  product-quality rule rather than a tuning preference — see **The no-scars
  rule** below.
* **Skeletonise, do not trace the outline.** A stroke has two sides; its outline
  is a long thin loop that draws every line twice and looks like it.
* **A diagonal neighbour does not count when an orthogonal one reaches it.**
  Without that rule a plain staircase — which is what every curve looks like
  after thinning — registers as a T-junction, and one smooth line comes back as
  nine hundred junctions.
* **Smooth, then simplify, then fit.** The obvious order is wrong: simplifying
  first *locks the pixel staircase in*, because every step deviates by about a
  pixel — more than any tolerance small enough to keep real detail — and the
  curve fitting then faithfully draws a tremor.

Badly connected art is **refused, not rescued**: a gap is bridged only when one
side is a loose end and the other is within a few percent of the canvas, and
anything worse fails validation and is drawn again. The answer to a picture in
three pieces is a different picture.

### The no-scars rule

A listener watching the line travel must never see it strike out across the
negative space. This was not theoretical: a real animation was ruined by one
final artificial connector drawn across the blank canvas to force continuity,
and the rest of that drawing was good. So the pen's options, in order:

1. Follow the intended linework.
2. If continuity would otherwise need a new visible bridge, **retrace an
   existing segment instead** — the pen appears to travel back along a stroke
   rather than cutting across open space.
3. Keep retracing constrained to already-drawn geometry.
4. Prefer the shortest existing-path retrace back to undrawn work.
5. Where several retraces exist, prefer least visual disruption and least added
   travel. `BRIDGE_RETRACE_PENALTY` makes this concrete: retracing a *bridge* is
   priced at six times its length, because redrawing FAM's own repair through
   empty ivory is the most visible mark in the picture, while redrawing the
   artist's ink is free in the only currency that matters.
6. Bridge only a genuinely tiny accidental gap between endpoints that clearly
   should connect — `BRIDGE_SHARE` is 2% of the working image, about fourteen
   pixels, roughly a few stroke widths.
7. Never draw a long straight or curved connector across negative space.

In graph terms: retracing is allowed, duplicating existing edges is allowed,
introducing a new long edge through blank canvas is not. The route may
therefore be a Chinese-postman traversal that duplicates existing edges as
needed.

**The validator's numbers are a safety net, not an artistic allowance.**
`MAX_BRIDGE_UNITS` (18) and `MAX_BRIDGED_SHARE` (2%) are maximum *rejection
boundaries* — the point past which a mark is provably a scar. They are not
permission to bridge anything shorter, and a route must never prefer a
synthetic bridge on the grounds that it would pass them. The rule routing
actually obeys is stricter and lives beside the code that obeys it:

* `BRIDGE_SHARE` is **1%** of the working image — about seven pixels, ten
  viewBox units, under three screen pixels of travel on the player. Well inside
  the backstop, and meant to be.
* `BRIDGE_ALIGNMENT` is the half a distance threshold cannot express: **the gap
  must point the way the pen was already going.** Distance alone cannot tell a
  stroke that was interrupted mid-curve from two unrelated ends that happen to
  pass near each other — and the second is a mark across the picture that is
  merely short enough to sneak through. A line that stops mid-bend is
  continued; a line that would have to turn a corner to reach its neighbour is
  not. That is what "endpoints that belong to the same intended stroke" means
  as code.

So: **retrace existing ink whenever possible.** A synthetic bridge exists only
to repair a genuinely tiny accidental gap between endpoints that visually and
semantically belong to the same stroke. Never choose one because it passes the
validator.

### ...but prefer new drawing to retracing when both are safe

Priority three sits between "never scar" and "retrace when necessary", and it
is about *time* rather than ink. The drawing is revealed against the audio, so
an unbroken run of retracing is a run of seconds in which the episode plays on
and the picture does not change.

`reveal_stall` measures the longest such run as a share of the journey — the
total retraced share is the wrong number, because a route that retraces 30% in
short bursts is fine and one that retraces 20% all at once is not.

`best_route` then tries several orderings of the same drawing and keeps the one
that paces it best. `euler_route(edges, variant)` rotates each vertex's
adjacency list before the walk, so every candidate traverses **exactly the same
multiset of edges** — the ink, the retraced share and the fidelity are
identical whichever wins, and only the order the listener meets it in changes.
That property is what makes this safe to optimise at all under priority one,
and a test pins it. Measured, it earns its place: on one figure the first
ordering stalls for 12.9% of the reveal and the best for 6.5%.

A route that still stalls is **advisory, never a refusal** — the artwork is not
what went wrong, since the same picture in a different order does not stall.

**Three mechanisms enforce it, because one would be a single point of failure.**

* The route carries its **own edge identities** out of Hierholzer rather than
  being reconstructed from the vertex sequence afterwards. A duplicating route
  is full of parallel edges, and reconstruction can pick the wrong one — which
  is exactly how the scar got drawn: the wrong pick left a stroke unmatched, the
  concatenation joined two non-adjacent chains, and smoothing turned the
  discontinuity into a graceful curve across blank ivory. It looked deliberate.
* `_assemble` refuses to **build** a route whose consecutive strokes do not
  meet. The tolerance is not zero and is not a constant: a chain begins and
  ends on an actual *pixel* of a junction cluster while its node is that
  cluster's centroid, so chains meet slightly apart by construction, bounded by
  the cluster's own size. `node_tolerance` measures that from the drawing in
  hand, because **a denser picture has larger clusters** — and a constant four
  pixels, calibrated on a single figure on an empty page, rejected rich artwork
  for being rich. A gap that size is inside a junction cluster and therefore
  inside ink; anything larger is a `discontinuous` failure and a regeneration.
* `visual_validator` refuses to **ship** one. Bridges are measured *after*
  `fit_to_canvas`, in final viewBox units, because the fit can scale a drawing
  **up** and a bridge that was small in a cornered subject is not small once
  that corner fills the frame. `MAX_BRIDGE_UNITS` is 18 — 1.8% of the canvas
  width — for any single bridge, and `MAX_BRIDGED_SHARE` caps all of them
  together at 2% of the drawing, because several individually invisible repairs
  still add up to a picture that is partly invention.

`LineArt` therefore reports `max_bridge` and `bridged_length` beside
`retraced`, and both reach the stored metrics — so "was this drawing partly
invented" is a number rather than a judgement call.

**The thumbnail is rendered from the path, never from the source artwork.** The
player's last frame and the tile have to be the same picture; rendering one from
the vector and the other from the raster guarantees that they are not.

---

## Three ladders, and why they are three

A reader meets three "try again differently" mechanisms in this feature. They
are orthogonal, each answers a different settled rule, and confusing them is
how one gets used for the other's job:

| ladder | what it changes | answers |
|---|---|---|
| `visuals.RETRY_LADDER` | **different artwork** — a new image generation | the art is unusable: not one line, an icon, coloured |
| `line_processor.DETAIL_LADDER` | **the same artwork, vectorised more gently** | the vector lost the drawing. *Never* answered with different art |
| `line_processor.ROUTE_TRIALS` | **the same vector, drawn in a different order** | the reveal stalls. Cannot change the picture at all |

Cost falls by an order of magnitude down the table: a retry is a paid image, a
detail rung is a few milliseconds of curve fitting, a route trial is a linear
walk. So does blast radius — only the first can change what the listener sees.

---

## What is stored

    fam-visuals.db                  the index: status, path, metrics, cost
    episode-visuals/{visual_id}/
        source.png                  what the image model returned (diagnosis only)
        visual.svg                  the canonical asset
        thumbnail.png               rendered from the vector, 1536px
        metadata.json               so a folder of these is readable without this code

The `d` attribute lives in the database as well, because it is what every
request wants and it is a few kilobytes of text. The files exist so a drawing is
a thing on disk. If the PNG is deleted it is re-rendered from the path; the
vector is the canonical asset and the raster is a convenience.

---

## The API

| endpoint | what it does |
|---|---|
| `GET /api/visual?q=&context=` | the drawing's state, with the geometry inline when ready. **Never generates** — which is what makes it safe to poll |
| `GET /api/visual/{id}.svg` | the canonical asset: one path, no script, nothing external |
| `GET /api/visual/{id}.png` | the finished square as pixels, from the vector |
| `POST /api/visual/event` | what only the client knows: loaded, cache hit, failed, parse failed |
| `POST /api/visual/regenerate` | admin only; the current drawing survives a failed redo |
| `GET /api/health` → `visuals` | provider, director, style, counts, events, latency, spend |

Every player surface is an API before it is a screen (`IOS_APP.md`), so an iOS
client needs no new server work: it reads `d` from `/api/visual` and strokes it
the same way, or takes the PNG if it would rather have an image.

---

## The source gate

**Before the vectoriser touches it.** `visual_validator.screen_source` looks at
the raster the image model returned and asks one question:

> Would this feel premium enough to appear as a finished myFAM episode
> thumbnail?

If no, it is regenerated rather than vectorised. It is in front of the work
rather than inside it for a reason that is about the product and not about CPU:
**FAM's line processor is good enough to turn almost anything into one ordered
path**, which means a pipeline left to itself will faithfully rescue an icon
into the feed. The gate is where "is this art" gets asked while the answer
still costs nothing.

| measured | refused when | what it catches |
|---|---|---|
| **crossings** — separate runs of ink a straight scan meets, averaged | `< 3` | **the icon test.** A plain circle — the shape of every pictogram — scores 2.0: almost every scan line crosses it exactly twice. A scene with a figure, a desk and a window behind it scores 8 |
| | `> 26` | clutter: hatching, pattern fill, scribble |
| **extent** — how much of the square the drawing spans | `< 45%` | a small symbol floating in space rather than a composition |
| **ink share** | `< 0.4%` | a sketch, not an illustration |
| | `> 16%` | the negative space is gone; a fill, a wash or a photograph |
| **colour** | `> 2%` saturated | the model ignored the style |

**Hard bounds refuse; targets only advise.** Only the rows above that are
*provable* can reject a picture — blank, a speck (under 25% of the square), a
fill (over 35% ink), scribble (over 60 marks per scan), colour, and the
structural icon test below. Everything else is recorded in `advisories`, shown
on the record, in the trace and on `/api/health`, and costs the picture
nothing. `report()` prints the two sets separately, so which numbers can
actually refuse a drawing is visible from outside.

**The icon test is structural, and it had to be.** The first version of it was
the density measure with a hard floor set just above a plain circle, at 2.4 —
and a spare figure study, one gesture with the page left empty around it,
scores **2.3**. That is the exact composition the approved references are full
of, and it would have been refused for the number. Density cannot tell
restraint from a pictogram.

What can, categorically: a pictogram is *structurally* a closed outline —
nothing meets anything, nothing stops anywhere. `line_processor.structure`
counts junctions and loose ends, and a plain circle is `(0, 0)` where the spare
figure is `(1, 5)` and a full scene is `(81, 18)`. One line meeting another
line, or one loose end, clears it. It costs a thinning pass, about twenty
milliseconds, and that is the price of being able to tell those two apart.

Two honest limits, stated rather than papered over. **"Generic" and
"inconsistent with the references" are taste** and are not measured here — they
belong to the image model, the style block and `visual_references/`, and a gate
that pretended to measure them would be worse than one that says it does not.
And **the colour check reports `None` rather than `0` when Pillow is absent**,
because the built-in PNG reader only returns luminance: "we did not look" and
"there was nothing to find" are different answers.

---

## Vector fidelity

**After vectorisation, the drawing is compared against the artwork it came
from.** `line_processor.fidelity` places the skeleton and the finished curve on
the same canvas, through the same transform, and returns two numbers:

* **`fidelity`** — how much of the artwork the curve still passes near. Below
  `MIN_FIDELITY` (90%) the picture a listener would see is not the picture that
  was approved.
* **`invented`** — how much of the curve passes nowhere near the artwork. Above
  `MAX_INVENTED` (4%) the vectoriser is drawing line the artist did not.

Two numbers rather than one average, because they are opposite failures: a
vector that lost a figure's hands scores badly on the first and perfectly on
the second, and a vector that struck out across the page scores the reverse.

**And a failure here is answered by redoing the processing, never by
simplifying the art.** `DETAIL_LADDER` re-runs the vectorisation on the *same*
source artwork — the same decode, the same skeleton, the same route — with
progressively gentler smoothing and simplification, and keeps the first pass
that clears the bar. It is cheap, because only the curve fitting is repeated
and not the skeletonisation. Reaching the validator with a fidelity failure
means even the gentlest pass could not hold the picture, which is a bug in this
code rather than a fact about the artwork.

---

## Validation

Before a listener is shown anything, three separate jobs:

* **Structural** — one ordered path, a real `d`, a square viewBox, no fills, one
  moveto. If not, the vectoriser produced something the player cannot reveal.
* **Safe** — an SVG is a document. No script, no event handlers, no external
  references, no embedded HTML, no DOCTYPE. FAM generates every byte of these
  itself, which is exactly the reasoning that would let a hole through
  unnoticed. The endpoint adds a `Content-Security-Policy` on top.
* **Good enough to show** — not mostly blank, not a scribble, the subject is not
  a speck, the line is not pressed against the edges, retracing is under 60%,
  **no single bridge is longer than 18 viewBox units and bridging is under 2%
  of the drawing** (the no-scars rule above), **the vector still contains the
  artwork** (fidelity above), the thumbnail decodes and has both paper and ink
  in it.

A failure is a regeneration, up the ladder: standard direction → insist on
continuity → **the same scene, composed so its parts touch**. Three attempts,
then `failed`, and the episode plays.

That third rung used to read "ask for a simpler picture", and it was the
clearest example of the inversion this document warns about: a failure in FAM's
vectoriser was answered by making the illustration worse, on exactly the
subjects that had already failed twice — so the feed's hardest topics were
systematically its most icon-like tiles. The constraint being answered is real
and has a free answer: **the forms need to touch, not to be fewer.** A scene
whose elements overlap and flow into one another is exactly as rich and is
drawable in one stroke. `SIMPLIFY_INSISTENCE` is deleted rather than disabled,
and a test fails if it comes back.

---

## Configuration

Everything is in `.env.example` with the reasoning beside it. The short version:

```
VISUALS=1                        # on; with no key every path ends in `unconfigured`
VISUAL_IMAGE_PROVIDER=openai     # openai | synthetic | none
VISUAL_IMAGE_MODEL=gpt-image-1
VISUAL_IMAGE_QUALITY=high        # quality IS the feature; ~$0.17 an image
VISUAL_DAILY_BUDGET_USD=5.0      # a ceiling, not a quota
VISUAL_DIRECTOR=1                # one small Claude call: what the picture is OF
```

**The credential**, which is the one thing that cannot be done from here:

```
VISUAL_IMAGE_API_KEY=sk-...      # or OPENAI_API_KEY, accepted under its own name
```

It goes wherever the other credentials go — `~/.fam/env`, a `FAM_SECRETS`
provider, or the deployment's environment. Never in a project file: a key in a
`.env` is lost on the next copy of the app and pasted back somewhere it should
not go. See `CREDENTIALS.md`.

`VISUAL_IMAGE_API_KEY` exists separately from `OPENAI_API_KEY` so the drawings
can be billed to a different account from the writing; either is accepted, and
`/api/health` says which one it found.

---

## Placeholder art is never a fallback

`VISUAL_IMAGE_PROVIDER=synthetic` draws a real continuous line locally, with no
key and no network. It exists so the whole pipeline can be run and seen end to
end, and so the tests exercise real geometry rather than a fixture.

It is **selected explicitly or not at all.** With `openai` configured and no
key, an episode has no illustration and everything says so — it does not quietly
fall through to something that looks like FAM and is not. That is this project's
oldest rule applied to pixels, and the same choice as a placeholder tone
standing in for a voice rather than a lesser voice standing in for Chatterbox.

Where synthetic art does appear it is labelled `placeholder art` on the canvas,
`placeholder` on the tile, `placeholder: true` in the API and
`[PLACEHOLDER ART]` on `/api/health`.

**The phone preview draws under the same rule.** A published page has no image
model, exactly as it has no Claude and no Chatterbox, so both preview builds
carry a drawing layer of their own (`FamPreviewArt`, built in
`preview/build_preview.py`) that answers `/api/visual` and puts a finished
drawing on every browse tile. The figure is `visual_provider._figure` ported to
the browser; `fam-line.js`, the reveal and the tile are the shipped ones and
are not touched. Everything it produces carries `placeholder: true`, so the
label is on the picture. It is there to check the interface — that the line is
revealed by the audio, and that a tile carries the finished drawing — and it
says nothing about how a real illustration looks. That still needs a server, a
key, `tools/visual_trace.py` and `tools/visual_probe.py`.

---

## `visual_references/` is the strongest lever

What `examples/` is to the writing, `visual_references/` is to the drawing.
Rules describe a style loosely; a provider that supports reference conditioning
matches an example closely.

With approved illustrations in that folder, a `gpt-image-1` request goes to
`/images/edits` with the files attached instead of `/images/generations` with
the style described in words. Three things about that are deliberate:

* **The images outrank the words.** `visual_style.reference_preamble` says so
  explicitly — "where the words and the images disagree, FOLLOW THE IMAGES".
  A description and a demonstration of the same style never agree exactly; line
  weight and the amount of negative space are the two that always differ, and
  without an ordering the model averages them into the generic look the style
  block exists to rule out.
* **They are named in the prompt**, so the record beside a finished drawing
  says which images governed it. "Why does this one look different" is only
  answerable if the record knows what it was shown.
* **They ship with the code.** The folder was briefly gitignored, which was
  wrong in a way nobody would have caught: the references would have worked on
  the machine they were added to and silently stopped working on every
  deployment, which still draws, still reports healthy, and simply stops
  looking like FAM.

  Un-ignoring it was not enough, and that is worth stating plainly, because
  the failure it was meant to prevent happened anyway (PROBLEMS.md §88): the
  three approved illustrations were in a working copy and in no commit, so a
  Render container built from a clone had a README and nothing else and drew
  every episode from words. `tests/test_reference_packaging.py` asks git
  whether each named stem is **tracked** — present on the disk of the machine
  running the suite is exactly the state that shipped words-only art — and
  asserts that no ignore rule can quietly take the folder back.

**Which three is a manifest, not an accident.** `visual_style.ACTIVE_REFERENCES`
names them, in order:

    meditation             a figure, alone
    profile-globe-city     a figure carrying an idea
    runner                 a figure in motion

with `whale` approved and held in `RESERVE_REFERENCES` — the densest of the
four, and it would bias every episode toward more line than the style wants.

Entries are stems, so `.png` or `.jpg` both resolve. A selection that depended
on alphabetical order would move the moment somebody added a file or renamed
one, silently, taking the product's whole look with it. Three at most
(`MAX_REFERENCES`) — a model given many averages them into something that looks
like none of them — so bringing the whale in means taking one out.

Nothing about it is silent. A named reference that is not on disk warns on
every request and appears in `references_missing` on `/api/health`, because FAM
would otherwise draw in two-thirds of the style it was told to and report fine;
a file present and not named is logged as held in reserve. Read from disk per
request, so a swap takes effect without a restart.

`tests/test_visual_references.py` asserts all of this **on the wire**: the file
bytes in the multipart body, the endpoint, the precedence sentence, and the
same thing again through a whole episode. "The provider was handed references"
is the cheaper question, and it was already true while the bytes went nowhere.

---

## Checking it

```
python -m pytest tests/test_line_processor.py tests/test_visuals.py \
                 tests/test_visual_director.py tests/test_visual_endpoints.py -q
./dev.sh check                       # includes the exploreFAM exclusion, in a browser
```

**And the one for judging the pictures themselves**, which is a different
question from whether the code works:

```
python tools/visual_trace.py "how do undersea cables get repaired"
```

One real episode — real EI, real research, the real director, the real image
model — with every intermediate written to `visual-traces/<run>/` and never
cleaned up: the director's brief, the exact prompt sent, the artwork as it came
back, the ink mask, the skeleton, the route before smoothing, the finished
vector, and an overlay of the finished line on the ink it was traced from. That
last one is the frame that separates "the model drew something weak" from "the
vectoriser lost it", which are indistinguishable from the finished picture and
have fixes in completely different files. `--dry-run` proves the harness on the
synthetic provider without spending anything.

It costs one image per attempt (~$0.17 at `high`). Nothing about it is cached.

And the one that needs a running server, because the reveal is a dash offset
written by an animation frame from an audio clock and nothing in pytest can see
any of that:

```
./dev.sh                             # in one terminal
python tools/visual_probe.py         # in another
```

It asserts the square starts blank, the reveal advances with the audio, seeking
moves it both ways, pausing freezes it, the drawing on screen is the drawing the
server stores, and exploreFAM has no canvas at all.

To watch it work without an image credential:

```
VISUAL_IMAGE_PROVIDER=synthetic ./dev.sh
```

---

## Known limits

* **Nobody has seen a real FAM illustration.** The build container has no image
  credential, so every drawing verified so far is synthetic. The pipeline,
  the store, the endpoints, the thumbnails and the reveal are all proven
  end to end; what is unproven is how `gpt-image-1`'s line art survives
  skeletonisation, and how often it survives at all. The first real key will
  answer both — run `tools/visual_trace.py` and look at the six stages — and
  `visual_processing_failed` / `visual_validation_failed` on `/api/health` are
  where the rate shows up.
* **A reference that is a strip of several illustrations is untested.** The
  prompt tells the model to answer with one illustration on one square canvas
  whatever the references are arranged as, which is the right instruction and
  not a guarantee. If the first trace comes back as a triptych, the fix is
  three single-subject reference files rather than a prompt change.
* **An attached episode has no illustration.** It is personal, so it is never
  drawn — the square stays ivory for the whole episode.
* **A downloaded episode played offline has no illustration**, because
  `playStored` does not go through the same path. The drawing is on the server;
  the audio is on the device.
* **No cycle scheduler.** Warming happens when a browse surface is loaded, not
  on a timer. Same open question as prefetch's, and it wants the same hit rate
  to answer it.
* **Cost is visible, not capped per listener.** `VISUAL_DAILY_BUDGET_USD` is a
  server-wide ceiling. A per-listener limit belongs in `entitlements.py` the day
  drawings are worth rationing.
