# Approved FAM illustrations

**The active set is named in `visual_style.ACTIVE_REFERENCES`, not inferred
from this folder.** Three files, in this order:

    meditation.png            a figure, alone
    profile-globe-city.png    a figure carrying an idea
    runner.png                a figure in motion

and one approved illustration deliberately held back:

    whale.png                 in reserve — see below

The extension does not matter: `.png`, `.jpg`, `.jpeg` and `.webp` are all
tried, so `runner.jpg` is found just as well. The **stem** has to match.

## Why a manifest rather than "the first three"

A selection that depends on alphabetical order moves the moment somebody adds
a file, renames one, or copies the folder onto a filesystem that sorts
differently — silently, and taking the whole product's look with it. Naming
them makes the set a decision that can be read, reviewed and changed on
purpose.

To change the set, edit `ACTIVE_REFERENCES`. It is capped at
`MAX_REFERENCES` (3): a model given many references averages them into
something that looks like none of them, so bringing one in means taking one
out. That is what the reserve is for.

## Why these three, and why not the whale

The three cover what a FAM episode is usually about — a person, an idea,
motion — and between them they demonstrate the full range of the line: a pure
figure, a figure with architecture, a figure with landscape.

The whale is the most beautiful of the four and the least representative. The
baleen striping and the wave curls are far denser than anything the style block
asks for, and as a reference it would bias every episode toward more line than
the style wants. Kept, approved, and out of the set until there is a baseline
to compare a swap against.

## What these are for

They are **shown** to the image model, not described to it. Where the written
style in `visual_style.py` and these images disagree, the images win, and the
prompt says so. That is why this folder is the strongest lever on how FAM's
illustrations look: rules describe a style loosely, and a model that is shown
one matches it closely.

Read from disk on every request, so swapping one takes effect without a
restart.

## Nothing here is silent

* A named reference that is **not on disk** is a warning on every request and
  a sentence on `/api/health` — FAM would otherwise be drawing in two thirds
  of the style it was told to draw in and reporting healthy.
* A file that is present and **not in the set** is logged as held in reserve.
* `/api/health` carries `references` (in force), `references_active` (the
  manifest), `references_missing` and `references_reserve`.

## They are committed

A deployment without them describes the style in words instead, produces
visibly different art, and says so only in a log line and a health field. They
ship with the code for the same reason the prompts do.

## Seeing what they bought

    python tools/visual_trace.py "<question>"

One real episode with every intermediate kept.
