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
   ├─ 2. Gemini 3.1 Flash Image paints it (objects and places)  (~$0.067/image)
   ├─ 3. Claude looks at the result: logo? text? person? real product?
   │     on-subject? Any failure → paint again (up to 3 times)  (~$0.004/check)
   └─ 4. Cropped to 480×360 WebP (~30 KB) and stored in thumbnails.db
         clean + generic subject      → live
         clean + real named subject   → waits for a person (/admin/thumbnails)
         never clean / key problem    → failed, with the reason
```

A tile asks for the **deepest** node the tree finds in its question that has
a live picture. A node still waiting for review falls back to its parent, and
so on up to the facet; with no picture anywhere the tile draws the old line
icon, exactly as before.

A running server picks up pictures painted or approved from
`tools/thumbnails.py` against the same database within 30 seconds; no
restart is needed.

Pictures are painted **only** in the background (after the two-hourly category
sweep, when `THUMBNAILS=1`), from `/admin/thumbnails`, or from
`tools/thumbnails.py`. Nothing on a browse page can paint anything.

## Cost

| | |
|---|---|
| The 188 seed nodes, once | about **$12.70** (`tools/thumbnails.py plan` prints it) |
| Each new node the vocabulary grows | about $0.07 including retries |
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
   the image model for. If the look is wrong, the place to change it is `HOUSE_STYLE`
   in `thumbnails.py` - one preamble shared by every picture, since the image
   model is given no style reference or negative prompt.
6. **Paint a sample.** On `/admin/thumbnails` (sign in with an admin account
   from `FAM_ADMIN_ACCOUNTS`), paint 10. The first eight are the facets.
   Look at them; if the style is off, change `HOUSE_STYLE`, redeploy, and
   press Repaint on each.
7. **Paint the rest.** Either press Paint repeatedly (60 a day under the
   default ceiling, so about five days for the seed), or run once with no
   ceiling from a machine with the same database:
   `python tools/thumbnails.py run --limit 200 --ignore-daily-cap`.
   If nothing appears after a Paint, read the line under the button: it says
   what the last run did, including the reason it stopped (a Gemini key
   without billing, the Gemini API not enabled, a quota). A run stopped that
   way records no node and no spend, so the counters alone cannot show it.
8. **Review what is held.** The "Waiting for you" tab: Approve or Reject each
   picture of a real named thing. Reject also takes a live picture off tiles
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
| `THUMBNAILS_IMAGE_PRICE` | `0.067` | Estimated list price of one 1K image, for the spend record - check Google's pricing page |
| `THUMBNAILS_MODEL` | `MODEL` | Scene writer and checker |
| `THUMBNAILS_ATTEMPTS` | `3` | Paintings per node before it fails |
| `THUMBNAILS_DAILY_IMAGES` | `60` | Images per rolling 24h, whole deployment |
| `THUMBNAILS_PER_SWEEP` | `20` | Nodes one background sweep paints |
| `THUMBNAILS_REVIEW` | `flagged` | `flagged` / `all` / `none` - which clean pictures wait for a person |
| `THUMBNAILS_DB` | project root | `/data/thumbnails.db` in the Dockerfile |

## Imagen 4 is gone

Google shut down all three Imagen 4 models on 2026-08-17 and named Gemini 3.1
Flash Image as their successor. Every request to `imagen-4.0-generate-001`
then answered **404 NOT_FOUND**, which is what the first real Paint run hit
(PROBLEMS.md §163). Two differences that matter here:

- **It is a different request.** `:generateContent` with
  `responseModalities: ["IMAGE"]` and an `imageConfig`, rather than
  `:predict`. The painter picks the shape from the model name.
- **There is no switch for people.** Imagen's `personGeneration:
  "dont_allow"` has no equivalent. The house style already asks for objects,
  places and landscapes only, and the checker refuses any picture with a
  person in it and paints again, so a person costs an extra attempt rather
  than reaching a tile.

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
