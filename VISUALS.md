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
  next piece is a scar, and this module will never draw one.
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

**The thumbnail is rendered from the path, never from the source artwork.** The
player's last frame and the tile have to be the same picture; rendering one from
the vector and the other from the raster guarantees that they are not.

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

## Validation

Before a listener is shown anything, three separate jobs:

* **Structural** — one ordered path, a real `d`, a square viewBox, no fills, one
  moveto. If not, the vectoriser produced something the player cannot reveal.
* **Safe** — an SVG is a document. No script, no event handlers, no external
  references, no embedded HTML, no DOCTYPE. FAM generates every byte of these
  itself, which is exactly the reasoning that would let a hole through
  unnoticed. The endpoint adds a `Content-Security-Policy` on top.
* **Good enough to show** — not mostly blank, not a scribble, the subject is not
  a speck, the line is not pressed against the edges, retracing is under a
  third, the thumbnail decodes and has both paper and ink in it.

A failure is a regeneration, up the ladder: standard direction → insist on
continuity → ask for a simpler picture. Three attempts, then `failed`, and the
episode plays.

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
`placeholder: true` in the API and `[PLACEHOLDER ART]` on `/api/health`.

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
