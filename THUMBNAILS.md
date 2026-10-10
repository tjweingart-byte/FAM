# Tile pictures

One generated picture per branch of the category tree, shown on every myFAM
tile and "View more" card whose episode falls under that branch. The word on
the picture is the branch's broad facet - SPORTS, TECH, MONEY - never the
branch itself. PROBLEMS.md §160 is the engineering log entry; this file is how
to run it.

## How it works

```
category tree node        e.g. sports > american football > college football
   │
   ├─ 1. Claude writes a scene with every team, league, company, product,
   │     person and country name taken out, and flags nodes that ARE a real
   │     named thing ("nfl", "formula one")                     (batched, ~$0.001/node)
   │     A node that is not a subject ("please", a slur) → skipped, $0
   ├─ 2. Gemini 3.1 Flash Image paints it, shown the reference
   │     pictures in thumbnail_style/ as the look to match      (~$0.067/image)
   ├─ 3. Claude looks at the result: logo? text? a real person? real
   │     product? pale border? obsolete equipment? on-subject?  (~$0.004/check)
   │     Pale border → cropped off in code, carries on as clean
   │     Anything else → held for you; NOT painted again by default
   └─ 4. Cropped to 480×360 WebP (~30 KB) and stored in thumbnails.db
         clean + generic subject      → live
         clean + real named subject   → waits for a person (/admin/thumbnails)
         failed a check               → waits for a person, with what it found
         no image / key problem       → failed, with the reason
```

**One paid image per node by default** (§169). A failed check used to throw
the picture away and paint again up to three times, so every failure cost
three images of the daily ceiling and three times the price. Now the prompt
is written to avoid the failures in the first place (see "The look"), a pale
border is cropped off rather than repainted, and any other failure keeps the
one picture for you to Approve (the checker was wrong) or Repaint. A held
picture is never on a tile until you approve it. `THUMBNAILS_ATTEMPTS=2` or
more brings back automatic repaints, for a logo, text, a real person or a
real product only.

**Every tile has a picture** (PROBLEMS.md §214, reversing the paragraph
below for rails): a tile on an unpainted node borrows one - nearest painted
ancestor, its facet, else a stable choice by its words - marked
`thumb_borrowed`, until its own is painted. The waitlist's samples take only
a tile's own picture.

**Every node shows its own picture and nobody else's** (PROBLEMS.md §178). A
tile's node is the deepest one the tree finds in its question, or its
declared facet when the tree finds none; the tile shows that node's live
picture or the old line icon - never a parent's or the facet's, which used to
put one picture on every subject under a branch. A node a tile asked for
without a picture is painted first by the next sweep. A new painting that is
the same picture as another node's (a difference hash within
`DUPLICATE_BITS` of 64) is held for review as "same picture as X", never put
live.

A running server picks up pictures painted or approved from
`tools/thumbnails.py` against the same database within 30 seconds; no
restart is needed.

Pictures are painted **only** in the background (after the two-hourly category
sweep, when `THUMBNAILS=1`), from `/admin/thumbnails`, or from
`tools/thumbnails.py`. Nothing on a browse page can paint anything.

## The look

A vintage-style watercolour of a **present-day** scene: mid-century
brushwork, ink linework and strong sun, fairly deep saturated colour (racing
green, deep sky blue, clay red, ochre, warm grey asphalt), painted edge to
edge. The objects in it are today's - current sports gear, stadiums, phones,
laptops - never an old radio or a wooden racket standing in for the topic.
Chosen by the owner from reference pictures (PROBLEMS.md §166, §167).

What carries it:

- **The picture in `thumbnail_style/`**, sent with every Gemini request
  ahead of the scene, with an instruction to take its medium, colour depth
  and light and not its subjects or period objects. This is the strong
  lever. Up to three are sent (sorted by file name); `THUMBNAILS_STYLE_DIR`
  points elsewhere, `0` sends none. Today it is one: the owner's airport
  watercolour, cropped to the terminal, tarmac and car, with the sign cut
  away and the bonnet crest painted out. **Its edges are cut away in code
  before it is sent** (`REFERENCE_INSET`, §177): the watercolour fades to
  paper on its right side and corners, and the model copied that fade onto
  nearly every picture.
- **`HOUSE_STYLE`** in `thumbnails.py`, the same look in words, appended to
  every scene.
- **The scene writer** is told contents are modern and given a list of
  obsolete stand-ins it may not use.
- **The checker** flags a pale border or faded edges, which is cropped off
  in code at no second painting (§169), or obsolete equipment as the
  subject, which holds the picture for review.
- **The resize trims unpainted paper** off any edge before the 4:3 crop -
  a strip that is bright *and* grey, so a pale blue sky is kept.

**To change the look, change the pictures first.** A reference must have no
text, sign, logo, crest, signature, recognisable product or detailed face -
the image model copies what it is shown, and the checker then throws the
result away at a paid attempt each. A light or pastel reference makes light
pictures, and one with paper showing at its edges invites a border. Use
pictures you own or generated yourself; a reference ships in the image.

People may be part of a picture, facing any way, as ordinary adults with
calm, expressionless faces (§169) - **only when the topic is about people
doing something** (§177). A phone, laptop or screen likewise appears only
when the topic is about that technology, and many pictures are a place,
nature, food or one object with neither. The style sent with every
painting names no people, screens or sunlight, because a noun in every
prompt is in every picture; the writer chooses a setting, viewpoint and
light per topic and varies them across a batch. A real or famous person, a child, or anyone in
team kit still fails the checker.

**How the prompt avoids failures rather than catching them** (§169):

- The style describes the edges positively ("the paint runs off all four
  edges, like a cropped detail") - naming a border, even to forbid it, is
  how an image model with no negative prompt comes to draw one.
- The scene writer describes only what is there. "A chalkboard-free stall"
  names a chalkboard, and the model paints it with writing on it.
- Nothing that carries writing is put in a scene (signs, chalkboards,
  newspapers, price tags, scoreboards), and every screen shows only abstract
  colour - a laptop "open to colourful data charts" was the commonest source
  of text failures.
- The checker judges "on subject" against the node's whole path
  (`business > price`), not the bare word, which on its own fits nothing.

## Cost

| | |
|---|---|
| The 188 seed nodes, once | about **$12.70** (`tools/thumbnails.py plan` prints it) |
| Each new node the vocabulary grows | about $0.07 - one image, no retries by default |
| Serving | nothing: one indexed read, cached for a year per device |
| Ceiling | `THUMBNAILS_DAILY_IMAGES` images a day (60 by default, about $2.40) |

These are list prices, not bills. Spend is recorded per image in
`thumbnails.db` and shown on `/admin/thumbnails`, `/api/health` and
`tools/thumbnails.py status`.

## Setting it up

1. **Get a Gemini API key with billing on.** In Google AI Studio
   (aistudio.google.com) create an API key, and attach a billing account to
   its Google Cloud project - image generation is not on the free tier.
2. **Add it to the deployment.** On Render: Environment → add
   `GEMINI_API_KEY`. Locally: `~/.fam/env` or your shell. `ANTHROPIC_API_KEY`
   must already be set; the scene writer and the checker use it.
3. **Deploy this branch.** Needs `Pillow` (in `requirements.txt`, so a normal
   build installs it) and `THUMBNAILS_DB=/data/thumbnails.db` (already in the
   `Dockerfile`, so the pictures survive redeploys).
4. **Check it can paint.** `/api/health` → `thumbnails.configured` should be
   `true`. If it says why not, that sentence is the fix.
5. **Look at scenes before paying for pictures** (optional, costs ~1¢):
   `python tools/thumbnails.py scenes --limit 20` prints what Claude would ask
   the image model for. If the look is wrong, change the reference pictures
   in `thumbnail_style/` and then `HOUSE_STYLE` - see "The look" above.
6. **Paint a sample.** On `/admin/thumbnails` (sign in with an admin account
   from `FAM_ADMIN_ACCOUNTS`), paint 10. The first eight are the facets.
   Look at them; if the style is off, change the references or
   `HOUSE_STYLE`, redeploy, and press Repaint on each. **After a change of
   look, every existing picture keeps the old one until repainted**:
   `python tools/thumbnails.py run --regenerate --limit 200 --ignore-daily-cap`.
7. **Paint the rest.** Either press Paint repeatedly (60 a day under the
   default ceiling, so about five days for the seed), or run once with no
   ceiling from a machine with the same database:
   `python tools/thumbnails.py run --limit 200 --ignore-daily-cap`.
   If nothing appears after a Paint, read the line under the button: it says
   what the last run did, including the reason it stopped (a Gemini key
   without billing, the Gemini API not enabled, a quota). A run stopped that
   way records no node and no spend, so the counters alone cannot show it.
8. **Review what is held.** The "Waiting for you" tab: Approve or Reject each
   picture of a real named thing, and each picture that failed a check (its
   reason says what the checker found - approve it if the checker was wrong,
   Repaint if not). Reject also takes a live picture off tiles
   at once. **Repaint never takes a live picture away on its own**: a repaint
   that needs approval waits beside the live one (tiles keep the old picture
   until you approve the new), and a repaint that fails keeps the old one.
9. **Turn on painting for new branches.** Set `THUMBNAILS=1`. Every sweep
   then paints up to `THUMBNAILS_PER_SWEEP` new nodes inside the daily
   ceiling, and they appear on tiles as they are approved.

## Settings

| Variable | Default | |
|---|---|---|
| `THUMBNAILS` | `0` | Background painting after each category sweep |
| `GEMINI_API_KEY` | – | Google AI Studio key (`GOOGLE_API_KEY` also read) |
| `THUMBNAILS_IMAGE_MODEL` | `gemini-3.1-flash-image` | Any Gemini image model, or an `imagen-*` name; a name the key cannot call is swapped at run time for the best one it can, and the log says which |
| `THUMBNAILS_STYLE_DIR` | `thumbnail_style` | Reference pictures sent with every Gemini request; `0` sends none |
| `THUMBNAILS_IMAGE_PRICE` | `0.067` | Estimated list price of one 1K image, for the spend record - check Google's pricing page |
| `THUMBNAILS_MODEL` | `MODEL` (§249) | Scene writer and checker; `THUMBNAILS_CLAUDE_INPUT_PER_MTOK` / `_OUTPUT_PER_MTOK` (2.0 / 10.0) are its prices |
| `THUMBNAILS_ATTEMPTS` | `1` | Paid paintings per node. 1 holds a failing picture for review; more repaints on a logo, text, a real person or product |
| `THUMBNAILS_DAILY_IMAGES` | `60` | Images per rolling 24h, whole deployment |
| `THUMBNAILS_PER_SWEEP` | `20` | Nodes one background sweep paints |
| `THUMBNAILS_REVIEW` | `flagged` | `flagged` / `all` / `none` - which clean pictures wait for a person |
| `THUMBNAILS_DB` | project root | `/data/thumbnails.db` in the Dockerfile |

## Imagen 4 is gone

Google shut down all three Imagen 4 models on 2026-08-17 and named Gemini 3.1
Flash Image as their successor. Every request to `imagen-4.0-generate-001`
then answered **404 NOT_FOUND**, which is what the first real Paint run hit
(PROBLEMS.md §164). Two differences that matter here:

- **It is a different request.** `:generateContent` with
  `responseModalities: ["IMAGE"]` and an `imageConfig`, rather than
  `:predict`. The painter picks the shape from the model name.
- **There is no switch for people.** Imagen's `personGeneration` has no
  equivalent. People with expressionless faces are allowed since §169; the
  checker refuses a real or famous person, a child or team kit, and the
  picture waits for review rather than reaching a tile.
- **It takes pictures as well as words**, which Imagen never did - the
  reason the look now comes from `thumbnail_style/` (§166).

## What is not done

- **Nobody has seen a picture.** There is no Gemini key in the build
  container. Every part of the loop is tested with fakes, and the request
  shape follows Google's published `:predict` format. The first real run is
  the test.
- **Explore and DailyFAM covers are unchanged.** This covers myFAM rails and
  their "View more" screens, which are the surfaces that drew the old icons.
  DailyFAM mixes have their own square covers; Explore is a full-screen reel.
- **A live game's subject never reaches the tree (§157),** so game cards show
  the facet's picture until that is fixed.
