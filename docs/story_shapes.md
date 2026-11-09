# Story shapes - draft for the owner (§503)

**Status: partly decided (§504).** The owner (2026-11-09): "Add profile
and comparison, let Shakespeare change the shape" - questions 2 and 3
below. Both are built: `profile` and `comparison` are in
`episode_intelligence.STRUCTURES` for the brief to pick, and Shakespeare
names a shape ahead of the tap (`EditorAnswer.shape`). The rest - the
rewording of the other shapes, and questions 1 and 4 - is still a draft;
nothing in the code reads this file. Once the owner approves it (edited as they like), the shapes move into
the journalism playbook's Part 3, which the owner signs (the rule-book
manifest covers the playbook), and the code follows the signed text.

## What exists today

FAM already has a closed list of shapes: `episode_intelligence.STRUCTURES`.
The brief (C3P0) picks one per episode inside its existing call, so choosing
costs no wait. If it picks one outside the list, the intent decides
(`INTENT_STRUCTURE`). `in_progress` is never picked: the writer reaches it
only when the evidence shows no finished result (§88). The writer reads the
chosen shape as one line ("A sports recap story usually goes: ..."), and is
told to drop a part with nothing behind it (`build_structure_note`).

Each shape is one line of parts, which is less than a professional's form.
Five problems with what is there:

1. **Three endings break FAM's own rules.** `breaking` ends on "what happens
   next" and `business` on "what it implies": both invite a forecast
   ([endings-do-not-tease]). `explainer` ends on "the way to think about it
   afterwards", which invites a summary ([speak-from-inside]).
2. **No shape says how to open or land.** The rules most often broken
   (concrete opening, concrete landing) are left to the general prompt.
3. **No shape says how its parts connect.** A list of parts, written in
   order, comes out as a list. That is the storytelling score (1.5-1.9 of 5,
   §437).
4. **Two common kinds of story have no shape:** a person ("who is X") and a
   comparison ("X vs Y", which is filed under explainer).
5. **The examples aren't tied to shapes.** Nothing shows the writer one
   example episode per shape.

## The proposed list

Each shape has six parts:
- **chosen**: when it is picked;
- **opens on**: what the first two sentences hold;
- **moves**: the parts and the because / therefore / but between them;
- **lands on**: the last line;
- **never**: what it must not do;
- **example**: the house example that shows it, or a gap to fill.

Every shape keeps the standing rule: **a part with nothing behind it is
dropped, never filled.** Keys stay as they are wherever a shape survives, so
nothing stored changes; two keys are new.

### 1. The news - `breaking`
- **Chosen:** something happened and they want to know what (intent
  `recap`, `causal`, `update`, outside sports and business figures).
- **Opens on:** the event itself - who did what, when - in particulars.
- **Moves:** what happened → *because* what led to it → *but* what is
  disputed or unsettled in the world (said as the world's open question,
  never as something FAM could not find) → *therefore* what it has already
  changed.
- **Lands on:** the most concrete consequence that already exists - a
  number, a decision, a person's move.
- **Never:** end on what happens next, a forecast or a "we'll see".
- **Example:** none yet - a gap (the most common shape in Trending).

### 2. The number - `business`
- **Chosen:** results, a market move, an economic figure.
- **Opens on:** the figure against what was expected.
- **Moves:** the number → *but* the gap and what is surprising in it →
  *because* the explanation, attributed to whoever gave it → *therefore* how
  the market or the people affected took it.
- **Lands on:** the concrete figure that shows the effect (a price move, a
  rate, a count).
- **Never:** advice, "investors should", or a projection as the last line.
- **Example:** `2-fed-raises-rates` is close (an explainer of a decision);
  a results story is a gap.

### 3. The result - `sports_recap`
- **Chosen:** a game or race, when the evidence reports it final.
- **Opens on:** the result and the reason it happened, in one or two
  sentences.
- **Moves:** what was at stake → *but* the turn the game hinged on →
  *because* who or what decided it → *therefore* what it changes (table,
  standings, a run), as fact.
- **Lands on:** the most concrete name or stat - the player, the margin.
- **Never:** a score not checked against the scoreboard ([score-guard]);
  a preview as the result.
- **Example:** `2-singapore-sprint`.

### 4. What's at stake - `sports_preview`
- **Chosen:** a game or event that has not happened (intent `preview`).
- **Opens on:** what is at stake and when it happens.
- **Moves:** what is at stake → where each side stands, by its record →
  *but* the matchup that decides it → (beside it, never as an outcome) what
  the forecast markets expect ([forecast-beside]).
- **Lands on:** the one fact the game turns on, or when and where it is.
- **Never:** a prediction stated as fact; "should be a good one".
- **Example:** none - a gap.

### 5. State of play - `in_progress` (reached from evidence only, as today)
- **Chosen:** never by the brief; by the writer, when the evidence shows no
  finished result for a question that needs one (§88).
- **Opens on:** how it stands right now, with its time.
- **Moves:** what is at stake → how it stands → *because* what has already
  been settled → *but* what is still open. It ends there, because it has not
  finished.
- **Lands on:** the latest concrete fact, with when it was true.
- **Never:** an outcome; a guess at one.
- **Example:** none - a gap.

### 6. How it works - `explainer`
- **Chosen:** how or why something works (intent `explainer`, `teaching`).
- **Opens on:** the one idea that makes it surprising or simple - concrete,
  never "it's complicated".
- **Moves:** the idea → *because* the mechanism underneath → one real case of
  it working → *therefore* what follows from it.
- **Lands on:** the concrete consequence the mechanism leads to (replaces
  "the way to think about it afterwards").
- **Never:** a summary, "so next time you...", a recap of the mechanism.
- **Example:** `2-passkeys`, `1-blue-sky`, `4-noise-cancelling`,
  `2-autoimmune`, `2-fed-raises-rates`; "why it matters" questions too:
  `2-suez-canal`, `2-flight-cancellations`.

### 7. The story from history - `historical`
- **Chosen:** something that happened in the past, told for its own sake.
- **Opens on:** the moment it turned - a date, a place, a person.
- **Moves:** the setup, briefly → *but* the catalyst → *therefore* the turn →
  *therefore* the consequence.
- **Lands on:** the image or fact that closes it.
- **Never:** "to this day" filler; history told as how things stand now.
- **Example:** `2-apollo-13`, `2-house-music`, `5-financial-crisis`,
  `2-marathon-distance`.

### 8. Why now - `trend`
- **Chosen:** something everyone is suddenly talking about (intent
  `discovery`).
- **Opens on:** the specific thing that set it off, and when.
- **Moves:** what set it off → how big it actually is, as a fact (numbers,
  who) → *but* the competing explanations, each attributed → *therefore*
  which part genuinely matters.
- **Lands on:** the concrete part that matters.
- **Never:** "it's taken the internet by storm"; attention claimed with no
  number behind it.
- **Example:** none - a gap.

### 9. Who they are - `profile` (new)
- **Chosen:** a question about a person ("who is X", "what has X done").
- **Opens on:** why they are being talked about now - what they just did.
- **Moves:** who they are, situated → *because* the two or three turns that
  got them here → *therefore* where they stand now, from dated evidence.
- **Lands on:** the most concrete current fact about them.
- **Never:** a biography from memory where it can change ([missed-not-absent]);
  their private life unless that is the story.
- **Example:** none - a gap.

### 10. One against the other - `comparison` (new)
- **Chosen:** X versus Y (intent `comparison`, today filed under explainer).
- **Opens on:** the difference that decides it.
- **Moves:** each side in its own terms → *but* where they actually differ →
  *because* why the difference exists.
- **Lands on:** the concrete fact that separates them.
- **Never:** which one the listener should choose (the advice rule, §425).
- **Example:** none - a gap.

### No shape - `general`
Kept as today: when nothing fits, the house voice carries it.

**Left out on purpose:** a town's news and the weather. Their order is
already composed in code (§194: the town's news, then the weather, then the
county), and a shape would compete with it.

## How it would work once signed

1. **The signed text is the source.** Each shape's six parts go into the
   playbook's Part 3. `STRUCTURES` is read from the signed text (or pinned to
   it by a test), so the two cannot drift apart.
2. **Choosing stays where it is, at no wait.** The brief keeps picking from
   the closed list inside its call. `profile` and `comparison` join the list
   it may pick from, and intent `comparison` defaults to `comparison`. `in_progress`
   stays downstream only.
3. **Shakespeare may correct it ahead of the tap.** His answer gains a
   `shape` field, an enum of the same keys (no fact can be written into it),
   for when the packets show a better fit, for example a recap whose packet
   shows no final result → `in_progress`. On searchFAM the brief's pick stands.
4. **The writer reads more than a line.** `build_structure_note` gives the
   shape's opening, moves and landing, with the same "drop a part with
   nothing behind it" sentence. A shape's text sits in the writer's request,
   never the cached system block ([writer-savings]).
5. **One example per shape.** `examples/facets.json` gains a shape per
   example, and the writer's request names the example of its shape as well
   as its facet (§501). The gaps above are filled from listening
   (`tools/example_candidates.py`) or pairs made before launch
   (`tools/teaching_pairs.py`), approved by hand.
6. **Measured per shape:** `tools/editor_eval.py` before and after on the
   frozen packets; `tools/drop_off.py` grouped by shape (`structure` is
   already on every quality row).

## For the owner to decide

1. The list: keep, cut, rename or add (for example, a separate "why it
   matters" shape instead of keeping it under explainer).
2. Whether `profile` and `comparison` are added.
3. Whether Shakespeare may change the shape ahead of the tap (point 3), or
   only the brief chooses.
4. The wording of each shape's opening, moves and landing. It becomes
   signed text the writer reads word for word.
