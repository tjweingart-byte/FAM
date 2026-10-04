# Product: where FAM is going and what an episode is

> Moved verbatim from `CLAUDE.md` (2026-09-28, PROBLEMS.md §168). `CLAUDE.md`
> keeps one line per rule and loads every session; this file keeps the
> reasoning and history and is read on demand. Each rule starts at a
> `<!-- rule:ID -->` marker, and `CLAUDE.md` cites the same ID in brackets -
> `grep -n 'rule:ID' docs/claude/*.md` jumps to it.
> `tests/test_claude_md.py` fails if the two sets of IDs ever disagree.

<!-- rule:social-information -->
## What FAM is, above all else

**FAM is social information, not social media.** *(The owner, 04/10,
PROBLEMS.md §199, "above all else".)* The problem it answers is not access -
the information is all out there - but that keeping up with it takes work
(searching, reading articles, scrolling feeds, an hour-long podcast for the
ten minutes you care about), so people accept they will miss things and then
stand on the outside of the next morning's conversation. Having access to
information and actually knowing something are two different things. FAM
turns the things a listener cares about into personalized audio stories, when
they want them and for however long they have, so they spend less time trying
to keep up and more time being part of the conversation - and have one more
thing to connect with someone over. It is not about consuming more
information: it makes the information already around us easier to
understand, remember and bring back into the real world. The origin: a
podcast company deciding which stories became episodes, and the question of
why a listener should wait for someone else to decide. Copy, onboarding and
features should be judged against this frame; the waitlist page's "What is
FAM" section tells it in full (`static/waitlist.html`).

<!-- rule:three-surfaces -->
## Where this is going

Three surfaces, all backed by generated audio:

1. **searchFAM** — ask anything, hear a briefing of a chosen length. *Working
   today.* This is the only surface that fully works.
   *Since §151 it can be asked by voice*: a mic under the box opens a screen
   that shows the words as they are heard. *Since §159, at the owner's
   direction*, stopping brings up a centred **Search now** that fills over
   five seconds and searches when full, with an X beside it that stops the
   count - speech ending starts a visible, refusable countdown, never a
   silent search. *Since §165 the heard words are editable in place* -
   tapping them stops the microphone and the count, and Search now sends the
   corrected words. Since §158 saying "hey FAM"
   or "what's up FAM" opens that screen from any tab (off until turned on in
   Settings, since it keeps the microphone open).
   > **Current:** the mic sits on the box's own line beside attach, the same
   > size *(§182, 10.1 packet)*.
2. **myFAM** — a browse page of trending / recommended / for-you episodes.
   Tapping a tile generates and plays that episode. *Finished (§102): four
   rails over two shared inventories - the evergreen bank and a live story
   pool refreshed in the background once for everybody. A fifth rail, **What
   you missed last week**, replaced the weekly recap popup.*
2b. **DailyFAM** (was playFAM) — named daily mixes. A mix holds topic ids or
   questions the listener typed, never audio, so it is fresh every morning.
   *Since §137 it **follows subjects**: catalogue entries (`f:nfl`), each
   optionally narrowed to one specific (`f:nfl~Eagles`), each specific its
   own briefing, and every play asks for that day's edition by date
   (`mixes.daily_prompt`). A mix has a name screen, a square cover and
   recommended subjects under it. Since §140 it is also where **other
   listeners' public mixes** are found - a search bar by mix name, topic or
   owner - and a (+) on anybody else's mix adds a copy to your own; a mix
   is shared whole from its ⋯ menu.* **Since §143 every DailyFAM episode is
   written in the background before it is tapped**: a daily edition
   (`daily_edition.py`, 05:00 Eastern) writes one episode per distinct
   subject across every mix, with EI on each, and a mix saved between
   editions is written at once. *A typed subject may be a kind of thing
   ("founders lesson of the day") rather than news, and each edition is told
   its earlier editions' titles so it is a new one (§149).* The server owns the date and the length a
   tap sends (`/api/mixes` serves `prompt` and `minutes`), so a tap is a
   cache hit.
3. **explore** (was dailyFAM) — a vertical feed of episodes *other listeners
   have already generated*. It never writes a script: cards come from the
   shared cache and playing one sends `cached_only`, which the pipeline
   refuses to satisfy by generating. **And other listeners' only** — the cache
   records who first generated each entry (`scripts.author`, stamped from
   `_listener(request)`) and `recent(exclude_author=...)` keeps a listener's
   own episodes off their own feed. See the settled constraint below for why
   that id is nowhere near the cache key. **And searched episodes only**
   *(§147)*: `scripts.origin` records which surface wrote each entry, and a
   myFAM tile, a DailyFAM edition, a Trending episode or a warmed guess is
   never on Explore.

   > **Current:** since §181 Explore is not on the tab bar. It is reached
   > from myFAM's "What users are searching" rail (one "Start scrolling"
   > tile, right under Made for you); Messages has its tab.

<!-- rule:interface-181 -->
**The 9.30 interface packet (§181), at the owner's direction.** The sign-up
screen says "Listen to anything you want to know about" and rotates today's
three most-played episodes, kept audio only (`/api/welcome`), each playable
there. myFAM's header button is **search within myFAM**: what is typed finds
the cached episodes other listeners made that are most like it
(`/api/myfam/search`, `cache.rank_similar`, replay only); the "Search FAM"
bar is gone from myFAM. Messages is a tab, in Explore's old place, and
carries the unread count. Explore is the "What users are searching" rail
under Made for you. The player's (+) (top right; sources moved top left)
adds the episode's **topic** - a followed subject from
`topics.episode_subject`, else its title typed - to a DailyFAM mix, or to a
new one (`/api/episode/topic`).

myFAM and dailyFAM are **personalised**, driven by a per-user model that updates
as they interact with the app.

<!-- rule:names-swapped -->
**The listener sees myFAM and DailyFAM with their names swapped (§185), at
the owner's direction.** The rails screen (the first tab, `screen-myfam`) is
labelled **DailyFAM**; the mixes screen (`screen-playfam`) is labelled
**myFAM** - the tab labels, wordmarks, history tabs, toasts, sheets and the
server's listener-facing sentences ("That mix is already in your myFAM.").
Only what a listener reads changed: ids, routes, `data-tab`/`data-hist`
values, history `surface` keys, function and module names, and these docs
keep the old names, so "myFAM" in code and in `docs/claude/` still means the
rails and "DailyFAM" the mixes. A new listener-facing string uses the
swapped names.

<!-- rule:decouple-script-audio -->
## The architectural consequence that matters most

Today every episode is generated **on demand**. There used to be a fast-model
"cold open" covering that wait; it is gone (PROBLEMS.md §55). Nothing is spoken
until the real episode is.

**On the browse surfaces, that whole problem is avoidable.** myFAM and dailyFAM
know what the listener might tap *before* they tap it. So:

> **Decouple script generation from speech synthesis in time.**
> The script is the expensive part (~$0.03, several seconds, cacheable text).
> The audio is nearly free (~330x realtime, milliseconds).
>
> *(Amended by §132: nearly free per episode, but not per replay once the
> voice is a rented GPU - so the audio of a cached episode is now kept beside
> its script and played from there. Prefetch still warms scripts and briefs,
> never audio: a speculative guess should still cost only text.)*
> Pre-generate *scripts* for likely-next episodes; synthesise audio on tap.

That yields instant playback with no wait at all, and wastes only cheap
text when a prediction is wrong — not audio compute or bandwidth. The existing
script cache (`cache.py`) is already the right place to put pre-generated
scripts; it stores scripts, not audio, for exactly this reason.

<!-- rule:match-at-write-time -->
> **Current (PROBLEMS.md §168):** `CACHE_VECTOR` is **on** by default since §107 - "Off by default" below is stale (see [dec-embedding]).

The same "do it before the listener is waiting" logic is why matching happens
at write time too: `CACHE_VECTOR` embeds a question once when its script is
stored and compares locally on the next lookup, instead of `CACHE_SEMANTIC_KEY`'s
model call in front of every request. Off by default, and PROBLEMS.md §68 says
plainly what it is and is not currently buying.

<!-- rule:latency-start-earlier -->
Corollary: **latency is answered by starting earlier, never by filling the
gap.** The cold open tried to fill it and was removed. Prefetch on the browse
surfaces; on search, keep the work small enough that there is no gap to fill.

<!-- rule:search-no-cover -->
**On search that once meant two calls at once, and no longer does**
*(PROBLEMS.md §56, reversed by §108).* A question that needed today's facts
started a from-knowledge call that began speaking immediately and a researched
call that was still reading, and handed over mid-episode. It covered the wait
with an answer rather than with filler, which is more than the cold open ever
managed - and the half a listener heard **first** was the half that had been
given no brief, no evidence and no idea what the episode was about. Heard on a
real machine that is a confusing opening in front of a good episode, which is
exactly what came back from listening.

So the whole mechanism is deleted. **On search, the wait is now in front of
the first word and is honest about it**: the brief, the retrieval and the
writer's own planning all happen before anything is spoken. *(Since §148 the
loading screen shows that wait as five steps - contextualizing, retrieving,
verifying, finalizing, generating the audio - each checked off when the
server's marks say it finished, each on screen for at least two seconds, and
the audio held until the fifth. A replay skips them.)* Starting earlier
is still the answer to latency - it is just that on search there is nothing
left to start earlier than the tap, which is why the browse surfaces, where
there is, matter more than they did.

<!-- rule:one-sentence-spec -->
## The one-sentence spec

**Type a question, and within about a second audio starts giving the answer.**
Everything else is negotiable; this is not. Any change that puts seconds in
front of the first word is wrong, however clever the thing filling those
seconds is.

<!-- rule:spec-amended-for-search -->
> **Amended for search, deliberately and at the owner's explicit direction**
> *(PROBLEMS.md §82).* Episode intelligence runs one model call between the
> typed question and the search, so **searchFAM now starts a few seconds after
> the tap rather than half a second after it.** This was decided with the trade
> stated in both directions and chosen anyway: the writing is the product, and
> a fast episode about the wrong thing is worth less than a slower one about
> the right thing. What follows below is still true of everything else, and the
> rest of this section is the reasoning that made the amendment a decision
> rather than a drift.
>
> **The amendment is for search only.** myFAM and DailyFAM must pay none of it
> — there, what someone might tap is known before they tap it, so the brief and
> the script are built ahead of the tap and the wait is zero. That is the same
> "start earlier" argument this file has always made, and EI is the thing that
> makes prefetching worth more than it used to be: a pre-built brief is the
> expensive half. **Explore never pays it either** — it replays finished
> episodes and generates nothing.
>
> `EPISODE_INTELLIGENCE=0` restores the old latency exactly, and with it the
> old behaviour: the raw query goes to Exa, with no structure, no why-now and
> no temporal cautions.
>
> **Amended again, on the same terms and at the same direction**
> *(PROBLEMS.md §108).* Every guardrail that traded the opening's quality for
> time to first audio is gone: nothing is written before the retrieval
> finishes, on either backend, and the writing call reasons before its
> first token rather than starting to talk and deciding as it goes. **So
> search now waits for the whole picture, and says so while it waits.**
> *(The thinking budget for that went back from `EFFORT=high` to `low` at the
> owner's direction in §129 - the hidden thinking is expected to be the largest
> wait on search, not yet measured. The order is unchanged: brief and evidence first, no tools.)* The thing that was bought with those seconds was an opening written
> by a model that had been told nothing, in front of an episode that was fine
> - and a listener judges the product on the first ten seconds. Again: the
> writing is the product.
>
> **This amendment is also for search only**, for exactly the reason above -
> a browse tap's brief is warmed before the finger lands, and Explore
> generates nothing.

What that rules out, learned the hard way: any form of preamble used to
disguise a wait, and any text spoken before the material it is about has
arrived. Both were tried; the cold open (§55) filled the wait with nothing and
the from-knowledge cover (§108) filled it with the wrong thing.

Measured before §82 and §108 put research and planning in front of the first
word: **0.5s to first audio, no gaps.** Nobody has measured it since, and it
is now several seconds by construction.

<!-- rule:slow-is-scheduling -->
**A slow answer is a scheduling choice, not a property of the work.** The wait
only exists if generation starts when the button is pressed - measured, starting
earlier turned an 18.30s wait into 0.12s.

<!-- rule:no-typing-prefetch -->
Prefetch-on-typing-pause was built to exploit that and then **removed** at the
user's request; it is not in the code. The same reasoning still applies to the
browse surfaces, where what someone might tap is known well before they tap it,
and where a speculative script is far more likely to be used than one triggered
by a keystroke pause. That is where to spend it.

<!-- rule:myfam-warms-briefs -->
**The framework for spending it there is now built, and myFAM now drives it**
*(PROBLEMS.md §83 for the framework, §105 for the driving).* Drawing the page
schedules a cycle - never awaited - and what it warms is the **brief**: the one
model call episode intelligence puts between a tap and its retrieval, paid
before the finger lands instead of in front of the first word. So the amendment
above is honoured where it said it would be: search pays EI, myFAM does not.
Warming whole *scripts* is a separate level and stays opt-in, because a
speculative brief costs a fraction of a cent and a speculative episode costs an
episode. See the settled constraint below.

## What makes a FAM episode different

<!-- rule:satisfy-first -->
**First, it satisfies the thing that brought them.** Someone searched, or tapped
a tile, or decided to keep listening in dailyFAM — each of those is a want, and
the episode's first duty is to meet it. They should finish knowing what they came
to find out, well enough to say it back in their own words. **Satisfied first,
curious second.** Everything below is about how that answer arrives and is worth
nothing without it.

That ordering is load-bearing, not a pleasantry: the curiosity is what makes
someone want another episode, but the satisfaction is what makes them believe
another episode is worth having. Get them the wrong way round and the second one
never gets tapped.

<!-- rule:never-withhold -->
The failure this rules out, which the ending rules could otherwise produce:
**the episode must never withhold.** Withholding is not momentum, it is a bait
and switch, and a listener spots it instantly. Close the question they came
with, completely — and then stop.

<!-- rule:story-not-decoration -->
**And it is a story; that is the product.** Not a briefing with storytelling
added — the narrative is how the information arrives. A listener asking about a
simple concept or a routine update should find themselves pulled along without
noticing why.

The distinction that matters, because getting it wrong is what produces the two
failure modes seen so far:

* **Narrative as structure** (right): facts arrive in an order that opens a
  question and closes it. Because / therefore / but. Invisible.
* **Storytelling as decoration** (wrong): "picture this", scene-setting,
  atmosphere. This is what makes a listener think *get to the point*.

<!-- rule:speak-from-inside -->
The aim is **annexation**: not inviting the listener in, but absorbing them
before they decide to come, so that leaving takes a deliberate act. Two
mechanics carry it:

* **Speak from inside.** No orienting, no justifying the topic. Begin as though
  continuing a conversation they were already in.
* **Land it and stop.** The last line is the most concrete thing in the piece,
  and then it ends, mid-stride. No summary, no recap, no "so, to sum up" — each
  hands the listener their coat.

<!-- rule:endings-do-not-tease -->
**Endings do not tease. (Reversed — this used to say the opposite.)** The rule
was once "endings widen, they do not conclude": leave one named thread standing
and end pointed at it. On paper that is momentum. Heard back to back it is a
hook at the end of every single episode, which is a tease, and it was asked for
to be removed twice. So: no dangling hook, no "but that raises another
question", no rhetorical question at the end, no forecasting. Anything genuinely
unresolved is said *inside* the piece — plainly, as unresolved, and then the
piece carries on.

<!-- rule:every-sentence-informs -->
The guard: every sentence must carry information. Atmosphere alone is cut. The
point should be arriving continuously, from the first line, inside the story.

<!-- rule:title-from-content -->
> **Current (PROBLEMS.md §189):** a *live story* tile (Trending or the pool) is not protected the way a bank tile is: once its episode is cached and current, every later listener sees the writer's title and summary on the card and in the player, and its picture, facet word and logged tags come from the writer's `<<CATEGORY:>>` line (`app._name_written_tiles`). The first listener of an unwritten story still hears it under the composer's title.

**An episode is titled by what it turned out to be about.** *(§104.)* It used
to be the typed question, so somebody who asked `what happened with the fed
yesterday` got an episode called *What Happened With The Fed Yesterday* -
their own words handed back with capital letters, which tells them nothing
they did not just type. The model writes a `<<TITLE: ...>>` line beside the
`<<NEXT:>>` one, stripped before synthesis and never spoken, so it **costs no
second call and no latency**: naming an episode with its own model call would
be the expensive half of an episode spent on a label.
It is only known once the script is finished, which is after the first word is
playing - so the player opens on a title *derived* from the question (small
words kept small, so it reads as a title rather than a transcript of a search
box) and swaps the real one in when `/api/next` answers. A title the listener
set, or a bank tile's own, is never replaced: `titleOverridden` marks both. It
is cached in its own column beside the script for the same reason `thread` is
- a replayed episode has no `notes`, so an Explore card would otherwise carry
whoever-asked-first's wording - and a re-write keeps the title it has.

<!-- rule:next-is-prediction -->
> **Current (PROBLEMS.md §186):** opening Go Deeper no longer stops the episode playing (the player's or an Explore card's). If it ends while the sheet is up, the sound stops and the sheet stays: no album advance, no What's next countdown, no next Explore card, until a prompt is sent, the suggestion taken, or the sheet closed.

**Go Deeper did not lose its suggestion — it stopped coming from the script.**
The model still writes a trailing `<<NEXT: ...>>` line, stripped before
synthesis and never spoken, but it is now a *prediction* rather than a promise:
having heard this episode, what would this listener most likely ask next, read
off what was actually covered. The likeliest follow-up, not the most obscure
one. The pipeline stores it beside the script, `GET /api/next` returns it for
free, and Go Deeper offers it as a one-tap chip — so the suggestion is waiting
afterwards for anyone who wants it, and costs nothing to anyone who does not.
The script is explicitly barred from gesturing at it.

Note what this costs, so it is a known trade rather than a surprise: the
browse surfaces no longer get their "the ending of one is the entry to the
next" pull for free. dailyFAM's infinite swipe and myFAM's tiles now have to
earn the next tap on their own, which is what the predicted follow-up and the
prefetch plan are for.

<!-- rule:post-episode-grid -->
> **Current (PROBLEMS.md §190):** an episode on the full player that ends with something in the listener's queue plays the first of it at once instead of the grid; a myFAM playlist still plays through first.
> **Current (PROBLEMS.md §178):** the lead tile - the one the countdown is on - is always Go Deeper's own suggestion (the `<<NEXT:>>` line, else the title-built follow-up) and plays as a Go Deeper follow-up at the episode's length; the album's next episode, when there is one, is second; the ranking fills the rest. A search box sits above "Back to myFAM": touching it stops the countdown for that card, and what is typed plays as a follow-up on the episode that ended.

**Part of that is now paid back after the episode rather than inside it**
(PROBLEMS.md §70). When one finishes on the player, four recommendations appear
in a grid and the first starts itself in fifteen seconds - it was five, which
is long enough to notice a card and not long enough to read four titles and
choose, so the thing meant to offer a choice was making it. The countdown tile
is **the most likely next listen** and says so: the album's next episode when
one is playing (inside an album that is not a recommendation, it is the thing
the listener already chose), then the predicted `<<NEXT:>>` follow-up, which
answers exactly this question off what was actually covered, then the ranking's
own first pick - so the pull is there without a word of it being spoken, and
declining it is one tap. The tiles are the *feed's* ranking
(`topics.rank_next_up`), not a second one, so the popup and the shelves cannot
give a listener two different answers to the same question - and the ranking
draws on **both inventories now, like Made for you**. It was bank-only, so
somebody who had just heard about today's news was offered four standing
explainers, because the one place today's stories live was not in its
candidate list. It deliberately
does not fire on Explore or Explore New, which are already continuous.

<!-- rule:no-inverted-pyramid -->
Note this **replaced an earlier rule** that said to open with the answer
immediately. That was news-writing — the inverted pyramid — and it is the
opposite of story structure. The opening should be concrete and open a question,
not state the conclusion.

## The problem that matters most right now

<!-- rule:writing-is-the-problem -->
**The scripts are not good enough.** Not the voice - the writing. That is the
product, and it had received almost no attention next to the plumbing.

The prompt was the cause. It told the model that hitting a word count was "the
most important requirement", asked for "a one-line hook (about 146 words)", and
imposed the same five beats on every topic - so for a golf recap it had to
invent something to fill "the main debate or open question". Padding and
invention were being requested. Both prompts have been rewritten around what
makes a briefing worth hearing; that rewrite is untested against real output.

The endings were then rewritten again (PROBLEMS.md §48) to stop teasing the
next episode. **Also untested against real output** - it was written without an
API key, so nobody has yet heard an episode that ends under the new rules. This
is the first thing to check.

<!-- rule:write-py-loop -->
`python write.py "<query>" --minutes 3` prints a script in seconds without
generating audio. That is the loop for improving this, and it is a judgement
call rather than an engineering one.

<!-- rule:inputs-not-prompt -->
**And a second cause has since been found and fixed, which was never a prompt
problem at all** *(PROBLEMS.md §82).* Three of the four complaints about
quality came from what the prompt was *given*, not from what it said: the
question was searched verbatim, so `Nvidia` returned a company explainer on a
day the company had done something; the evidence packet carried no dates, so an
episode was asked to choose between "last night" and "two days ago" with
nothing to choose on; and duration was a word count, so more minutes bought
more words on the same material. **No amount of prompt work would have fixed
any of those.** `episode_intelligence.py` decides what to search for before Exa
is called, `research.build_packet` now dates and grades every source, and
`DEPTH_BANDS` says what each band of minutes is *for*.

<!-- rule:third-cause-opening -->
**And a third cause, which was the largest of the three and was also not the
prompt** *(PROBLEMS.md §108).* The reported failure was specific: the first
couple of sentences make no sense as a response to what was typed, and the
rest of the episode is almost perfect. That shape is two texts written under
two conditions, and FAM was producing exactly that - a from-knowledge cover
half with no brief and no evidence, speaking while the researched half read.
**Everything that traded the opening for time to first audio is now gone**:
the cover, the search tool on the writing call, and the EI skip on
unresearched episodes (`EFFORT=low` went too, and came back in §129 at the
owner's direction). Nothing is written until the writer holds the
brief and the evidence, and the prompt asks it to decide the whole piece
before it opens. Unheard - there is no key here - and it is the first thing
to listen for.

<!-- rule:brief-above-script -->
**`write.py` now prints the brief above the script, and that split is the
point.** A weak episode is either a weak brief or a weak script written from a
good one, and those have fixes in different files. Read the EI block first.
`--no-ei` runs the pre-EI path for comparison, and `tools/ei_eval.py` is the
twenty-prompt milestone.

<!-- rule:examples-lever -->
**`examples/` is the strongest lever on the writing, and it now holds one.**
Briefings dropped in there are shown to the model as the house voice. Rules
describe a style loosely; examples are matched closely, so two or three good
ones move the output more than any amount of further prompt wording. Prefer
adding an example over adding another rule. Nobody has written one yet, so the
strongest available lever on the remaining problem is untouched — and unlike
the rest of this list it needs taste rather than a key.

<!-- rule:trending-searches -->
> **Current (PROBLEMS.md §192):** five are shown (`TREND_SHOWN`), in a smaller bubble, so all five fit on the screen.
> **Current (PROBLEMS.md §190):** shown under the search page's two bubbles whenever the box is empty and nothing is attached (not only on focus), as one full-width bubble per search, and ranked by how many listeners searched the question in the last two hours (`TRENDING_SEARCHES_WINDOW`, distinct listeners, plays break ties) - a question nobody searched in that window is not offered. Still current episodes only, still cache-only.
- **Trending searches** *(§182, at the owner's direction)*: focusing the empty
  search box draws five to ten other listeners' searches whose episodes are
  still **current** (`/api/searches/trending`: searched episodes only, most
  played first, one per question, no explicit episode), so a tap lands on an
  episode already written. It reads the cache and nothing else - it never
  generates, and an empty list is drawn as nothing. A chip shows the
  episode's title (or the question through the speller) and sends the
  question at the length its episode was written for.

<!-- rule:autocorrect-at-send -->
- **A search is corrected as a whole when it is sent** *(§182)*. The
  word-by-word pass (§142) only saw a word once something followed it, so
  the last word of every search was never corrected. `runSearch` asks
  `/api/spell` for every word not already answered (waiting at most
  `AC_SEND_WAIT_MS`; a pause pre-asks the word being typed - a spell lookup,
  never a prefetch). The question keeps a word the listener put back; the
  **title never shows a misspelling** - it takes every correction, EI and the
  writer are told to spell titles correctly, and a title made from a
  question goes through `autocorrect.correct_text`.

<!-- rule:search-bar -->
- **The search page is Google's shape** *(PROBLEMS.md §190, the owner's 10.1
  packet with a Google screenshot)*: one rounded bar holding the question,
  with the mic and attach on its right and the go arrow only once something
  is typed or attached; under it two bubbles, **Length** and **Voice**, which
  open their menus and **never print the current choice** (the menus tick
  it); under those, trending searches (`trending-searches`). **The length is
  two minutes again every time somebody comes back into the app** - the page
  loading, or returning from the background (`DEFAULT_LENGTH_MINUTES`,
  `visibilitychange`); a length is chosen for one search, not kept.

<!-- rule:player-layout -->
> **Current (PROBLEMS.md §193):** the picture is centred in the space between GO DEEPER and the title, not pinned to the top under the arrow. **Every episode has one**: `/api/episode/card` answers from `thumbnails.pick_for_player`, which borrows - the nearest painted ancestor, the words' facet, else one approved facet picture chosen by a hash of the words - where a rail would show the drawing (9.30 #7 is about many tiles wearing one picture; the player shows one episode). A borrowed picture (`fallback`) is asked for again when the writer's title lands. The mini player shows the same picture, its whole height with the sides cropped.
> **Current (PROBLEMS.md §192):** GO DEEPER is at the top where "Now playing" was (nothing says now playing), a fifth shorter; the picture is its own 4:3 shape at the top, never stretched over the whole screen; share, vibe and save stand one above another on the right above the transport, and the ⋯ menu offers vibe and save too; the queue stays bottom right.
- **The player is Spotify's layout** *(PROBLEMS.md §190, the owner's, with
  Spotify screenshots)*: a down arrow top left (the old X, top right) that
  minimises and never stops (§142); the three dots top right open the menu;
  the episode's picture is behind the title (`/api/episode/card`'s `thumb`, a
  tile's own picture first); the title, with **nothing** where Spotify names
  the artist; the sources where Spotify's green button is, right of the
  title; the transport (±15s stays, `transport`), the next button when a
  playlist or the queue has a next; **GO DEEPER** where Spotify's mood chip
  is, bigger, in its pill; share and the queue bottom right; the closed
  captions in a sheet at the bottom, shut until slid or tapped up (open is
  on). **The menu** is Spotify's, with the owner's changes: a header of the
  title over the searcher, Share, Closed captions · On/Off, Add to playlist
  (a myFAM mix, `/api/episode/topic` as the old (+) did), Remove from this
  playlist (only while a myFAM playlist plays), Add to Queue, Go to Queue.
  Spotify's "Exclude from your taste profile" is **not** offered - it is the
  "not interested" control `no-not-interested` (§171) rules out, so it waits
  on the owner. **The searcher** is a handle only when that listener turned on
  `searches_public` (Settings, off by default) and is not the one listening;
  the response carries no id.

<!-- rule:dailyfam-catalogue -->
- **Search DailyFAM opens on an A to Z catalogue** *(PROBLEMS.md §193, the
  owner's sketch)*: before anything is typed, the screen behind myFAM's
  header button lists every cached episode other listeners made
  (`/api/myfam/catalog`: the same rows and rules as `/api/myfam/search` -
  your own left out, archived never, replay only), alphabetically by title
  under a sticky letter per run; case and leading punctuation ignored,
  anything not starting with a letter under "#" at the end; one row per
  title, the most played. Typing swaps it for the closest matches
  (`rank_similar`, unchanged); clearing the box brings it back.

<!-- rule:queue -->
- **The queue** *(PROBLEMS.md §190)*: Add to Queue from the player's menu or a
  DailyFAM tile's three dots (top right, beside save; the same menu less what
  needs a playing episode); Go to Queue lists Now playing, the playlist's
  rest, then the queue, each removable or playable. It is client state for
  this visit, never stored - a "next", not a shelf. A full-player episode
  that ends with something queued plays it at once; the next button skips to
  it; a myFAM playlist plays through first.

