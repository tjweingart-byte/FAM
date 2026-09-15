# Approved FAM illustrations

Drop the approved continuous-line illustrations in here as `.png`, `.jpg`,
`.jpeg` or `.webp`. Anything else in this folder is ignored rather than sent —
an image model handed a `.DS_Store` is a wasted request.

**These are shown to the image model as the house style.** Where the written
style in `visual_style.py` and these images disagree, the images win, and the
prompt says so. That is the whole reason this folder is the strongest lever on
how FAM's illustrations look: rules describe a style loosely, and a model that
is *shown* one matches it closely.

The first three files in alphabetical order are used
(`visual_style.MAX_REFERENCES`). More than three is not better — a model given
many references averages them into something that looks like none of them.

**So name them to choose.** `01-`, `02-`, `03-` for the three in force, and
anything later for spares:

    01-meditation.png     used
    02-thinking.png       used
    03-running.png        used
    04-whale.png          in reserve — swap a number to bring it in

A file that is present and not used is never silent: the server logs which are
in force and which are not on every request, and `/api/health` carries
`references_available` and `references_unused` beside `references`.

They are read from disk on every request, so adding or swapping one takes
effect without a restart.

**They are committed.** A deployment without them describes the style in words
instead of showing it, produces visibly different art, and says so only as an
empty `references` list on `/api/health` — which is not loud enough to catch.
They ship with the code for the same reason the prompts do.

`python tools/visual_trace.py "<question>"` runs one real episode and keeps
every intermediate, so you can see what the references actually bought.
