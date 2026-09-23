# FAM — working context

Read this before making changes. It records where the product is going, which
constraints are load-bearing, and which decisions are already settled, so work
does not drift or re-litigate them.

## Where this is going

Three surfaces, all backed by generated audio:

1. **searchFAM** — ask anything, hear a briefing of a chosen length. *Working
   today.* This is the only surface that fully works.
2. **myFAM** — a browse page of trending / recommended / for-you episodes.
   Tapping a tile generates and plays that episode. *Finished (§102): four
   rails over two shared inventories - the evergreen bank and a live story
   pool refreshed in the background once for everybody. A fifth rail, **What
   you missed last week**, replaced the weekly recap popup.*
2b. **DailyFAM** (was playFAM) — named daily mixes. A mix holds topic ids or
   questions the listener typed, never audio, so it is fresh every morning.
   *Since §136 it **follows subjects**: catalogue entries (`f:nfl`), each
   optionally narrowed to one specific (`f:nfl~Eagles`), each specific its
   own briefing, and every play asks for that day's edition by date
   (`mixes.daily_prompt`). A mix has a name screen, a square cover and
   recommended subjects under it.*
3. **explore** (was dailyFAM) — a vertical feed of episodes *other listeners
   have already generated*. It never writes a script: cards come from the
   shared cache and playing one sends `cached_only`, which the pipeline
   refuses to satisfy by generating. **And other listeners' only** — the cache
   records who first generated each entry (`scripts.author`, stamped from
   `_listener(request)`) and `recent(exclude_author=...)` keeps a listener's
   own episodes off their own feed. See the settled constraint below for why
   that id is nowhere near the cache key.

myFAM and dailyFAM are **personalised**, driven by a per-user model that updates
as they interact with the app.

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

The same "do it before the listener is waiting" logic is why matching happens
at write time too: `CACHE_VECTOR` embeds a question once when its script is
stored and compares locally on the next lookup, instead of `CACHE_SEMANTIC_KEY`'s
model call in front of every request. Off by default, and PROBLEMS.md §68 says
plainly what it is and is not currently buying.

Corollary: **latency is answered by starting earlier, never by filling the
gap.** The cold open tried to fill it and was removed. Prefetch on the browse
surfaces; on search, keep the work small enough that there is no gap to fill.

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
writer's own planning all happen before anything is spoken. Starting earlier
is still the answer to latency - it is just that on search there is nothing
left to start earlier than the tap, which is why the browse surfaces, where
there is, matter more than they did.

## The one-sentence spec

**Type a question, and within about a second audio starts giving the answer.**
Everything else is negotiable; this is not. Any change that puts seconds in
front of the first word is wrong, however clever the thing filling those
seconds is.

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

**A slow answer is a scheduling choice, not a property of the work.** The wait
only exists if generation starts when the button is pressed - measured, starting
earlier turned an 18.30s wait into 0.12s.

Prefetch-on-typing-pause was built to exploit that and then **removed** at the
user's request; it is not in the code. The same reasoning still applies to the
browse surfaces, where what someone might tap is known well before they tap it,
and where a speculative script is far more likely to be used than one triggered
by a keystroke pause. That is where to spend it.

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

The failure this rules out, which the ending rules could otherwise produce:
**the episode must never withhold.** Withholding is not momentum, it is a bait
and switch, and a listener spots it instantly. Close the question they came
with, completely — and then stop.

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

The aim is **annexation**: not inviting the listener in, but absorbing them
before they decide to come, so that leaving takes a deliberate act. Two
mechanics carry it:

* **Speak from inside.** No orienting, no justifying the topic. Begin as though
  continuing a conversation they were already in.
* **Land it and stop.** The last line is the most concrete thing in the piece,
  and then it ends, mid-stride. No summary, no recap, no "so, to sum up" — each
  hands the listener their coat.

**Endings do not tease. (Reversed — this used to say the opposite.)** The rule
was once "endings widen, they do not conclude": leave one named thread standing
and end pointed at it. On paper that is momentum. Heard back to back it is a
hook at the end of every single episode, which is a tease, and it was asked for
to be removed twice. So: no dangling hook, no "but that raises another
question", no rhetorical question at the end, no forecasting. Anything genuinely
unresolved is said *inside* the piece — plainly, as unresolved, and then the
piece carries on.

The guard: every sentence must carry information. Atmosphere alone is cut. The
point should be arriving continuously, from the first line, inside the story.

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

Note this **replaced an earlier rule** that said to open with the answer
immediately. That was news-writing — the inverted pyramid — and it is the
opposite of story structure. The opening should be concrete and open a question,
not state the conclusion.

## The problem that matters most right now

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

`python write.py "<query>" --minutes 3` prints a script in seconds without
generating audio. That is the loop for improving this, and it is a judgement
call rather than an engineering one.

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

**`write.py` now prints the brief above the script, and that split is the
point.** A weak episode is either a weak brief or a weak script written from a
good one, and those have fixes in different files. Read the EI block first.
`--no-ei` runs the pre-EI path for comparison, and `tools/ei_eval.py` is the
twenty-prompt milestone.

**`examples/` is the strongest lever on the writing, and it now holds one.**
Briefings dropped in there are shown to the model as the house voice. Rules
describe a style loosely; examples are matched closely, so two or three good
ones move the output more than any amount of further prompt wording. Prefer
adding an example over adding another rule. Nobody has written one yet, so the
strongest available lever on the remaining problem is untouched — and unlike
the rest of this list it needs taste rather than a key.

## Open problems, in the order they hurt

1. **Voice quality — answered, and unheard.** **Chatterbox is the production
   voice and the only one.** Piper is gone: engine class, configuration,
   `piper-tts` dependency, `setup_voices.py` and the interim slot itself. It
   was removed rather than switched off because it reached listeners three ways
   nobody chose — `build_engine` fell through to it, `engine_for_voice` fell
   back to it, `list_voices` offered it whenever the production slot was empty
   — and an app that quietly sounds worse than intended is the failure this
   project has lost the most time to. A knob left behind is an invitation to
   turn it back on, and this one turned itself.
   There are now exactly two honest states: Chatterbox speaks, or nothing does
   and everything says so — `/api/health` reports `interim: true`,
   `build_engine` logs why Chatterbox was unavailable, `demo.sh` refuses to
   start quietly broken, and playback is a **placeholder tone**, not a lesser
   voice. A tone cannot be mistaken for FAM; a flat neural voice can.
   Hosted neural voices were not adopted: WellSaid was removed after **two
   episodes exhausted a month's quota** — a seat product used as an API, not a
   voice that was too expensive (PROBLEMS.md §61) — and per-character billing
   breaks the "audio is nearly free" premise the prefetch plan rests on
   (`VOICE_OPTIONS.md` has the arithmetic). Chatterbox has open weights and no
   quota, and clones a reference recording in the same shared per-user folder
   (`~/.fam/voices`, see `voice_store.py`) that Piper's models used, for the
   same reason: a new copy of the app must find it already there.
   *Nobody has heard a FAM episode in this voice.* The build container has no
   GPU, so `RUNPOD_PRODUCTION.md` is the procedure that closes that gap and
   `python verify_voice.py` is the check that says whether a given machine can
   speak at all. **That listening test is the next move.**
2. ~~**Voice selection**~~ — *done*. `/api/voices` lists what the machine can
   speak; `voice=` on `/api/audio` selects one; the player has a picker.
   Note: voice is deliberately **not** part of the script cache key, because a
   voice changes the audio and not the words. Switching voice therefore reuses
   the cached script — measured at ~90 ms and zero API cost. The *audio* kept
   since §132 is keyed on the voice as well, so a new voice is voiced once and
   kept like any other.
3. ~~**The cold-open → script gap**~~ — *dissolved, not fixed* (PROBLEMS.md
   §55). Two sessions went into making the opener cover the research wait, and
   heard on a real machine it was 3-5 seconds of contentless speech in front of
   a 30-45 second silence. Five does not cover forty-five, and the opener was
   prompted to state no facts, so what it did cover was worthless. **The whole
   feature is deleted** - not switched off - along with `tools/gap_probe.py`,
   which existed only to measure it. The interface now shows an honest wait
   that names what it is waiting for and counts the seconds.
4. **myFAM is finished, and the bank is no longer the whole of it**
   *(PROBLEMS.md §102, `stories.py`, `MYFAM.md`).* The page was four rankings
   of twenty-eight evergreen topics, and none of them could answer what a
   browse page is actually asked - *what should I hear about today*. There is
   now a second, **live** inventory beside the bank: a shared story pool built
   from four live sources (GDELT, Finnhub, Polymarket, API-Sports), refreshed
   in the background once for everybody.
   **A tile is a title and an angle; the script is written on the tap.** That
   is the whole cost argument: deciding an episode is worth writing costs one
   small model call per refresh window for the entire deployment, and only the
   stories that are *new* since the last window are composed. myFAM's four
   rails are now **Made for you / Trending / What FAM can't stop listening to
   / What your friends are listening to**, in that order - the crowd row that
   always has something in it above the one that is empty until somebody
   follows anybody.
   **Made for you draws on both inventories** and Trending draws only on the
   live one, because answering "what is trending" with what FAM's listeners
   have already played would make it a laggier copy of the row below it. The
   two crowd rows lead with tiles whose script is already written - a sort and
   never a filter, since an expired cache would otherwise empty a row and call
   it a fact about what people are playing.
   **"What your friends are listening to" now reads the follow graph.** It
   was co-listener overlap under a heading that said "your circle", on a card
   that said "people you follow" about strangers; `social.circle_of` is
   friends first, then anyone they follow, and an empty circle returns nothing
   rather than strangers. The overlap ranking still tops up the post-episode
   popup, where nothing claims those people are friends.
   **How hard to push a story and for how long is written down** rather than
   left implicit in a sort order - `DOMAIN_WEIGHT`, `DOMAIN_SHELF_LIFE` and a
   36-hour cooldown after expiry - and `first_seen` is the clock, so a story
   that keeps being reported does not get to be new again. Variety is capped
   twice, in the pool and in each rail, and the cap is **a cap on what is
   available and never a quota on what is not**: a listener with one interest
   still gets a full rail.
   Nothing live is configured by default, the same way `live_facts` and
   `trending` ship; `GDELT=1` is the one line that makes it live, and
   `python tools/stories_report.py` is what says a machine can actually reach
   the sources. Nothing here has made a real request from the build container.
5. **The taste model is crude, and the vocabulary is no longer the ceiling.**
   *(§121. Four changes, in the order they were asked for, and the fourth is
   the one that removes a limit rather than tuning under it.)*
   **Three signals were being collected and thrown away.** `app.py` has
   recorded a `share` event since the messages feature shipped; `EVENT_KINDS`
   never listed the kind, so `EventStore.record` dropped every row with a
   warning nobody read. A **vibe** and a **save** were never written at all.
   All three are what somebody does about an episode *after* hearing it -
   arguably the strongest taste signal in the app - and none of them reached
   the ranker. They now sit between a play and a completion, and `share` and
   `vibe` weigh the same because a rule making either worth more would need a
   number nobody can tune.
   **The data for "will they tap it" already existed and nothing read it.**
   Every impression carries the listener, the tile, the rail and
   `ALGO_VERSION`; every play carries the listener and the tile. That is a
   click-through rate thirty days deep. `tools/ctr_report.py` prints it and
   `ENGAGEMENT_WEIGHT` uses it - **global per tile, never per listener**,
   because per (listener, tile) there is nothing to measure and per
   (listener, facet) is a noisier copy of `taste`. Bounded to [0.6, 1.5],
   shrunk so a new tile scores exactly 1.0, and applied to two rails only:
   `rank_most_played` must never have it or that row would be what everybody
   plays twice, and `rank_missed` would count one passing-over from two
   angles.
   **Location is stored and drives two things.** `preferences` gained
   `city`/`region`/`country` as free text validated against nothing - there
   is no list of the world's towns both complete and short enough to ship.
   Two mechanisms, deliberately separate: a listener's place joins
   `familiar_words`, so a story about their own town stops being damped by
   `BROAD_MATCH_PENALTY` for being a subject they never typed (living
   somewhere answers that question), and `LOCAL_BOOST` lifts a live story
   that names it. **The country is stored and never ranks** - boosting every
   US story for every US listener is a different global sort order wearing
   personalisation's name. The best use of it is the cold start:
   `startup.LOCAL_TOPIC` is a ninth question about their own town, offered to
   somebody we otherwise know nothing about, and it goes *through* the
   startup sort rather than jumping it so fatigue still reaches it.
   **And the vocabulary grows itself now** *(`categories.py`, `CATEGORIES=1`).*
   `TAG_WORDS` was thirty-seven hand-written keyword lists two levels deep,
   and it was the binding constraint rather than the scoring - `SUBTAG_WEIGHT`,
   `BROAD_MATCH_PENALTY` and `familiar_words` all exist to work around what it
   cannot say, and the last of those gives up on it entirely and reads the raw
   search text. `categories.db` is a tree with **no depth limit**, minted from
   what listeners search for, the live pool's own subjects, and what people
   type into the catalogue: `sports -> american football -> nfl -> cincinnati
   bengals` is four levels and `topics.tag_weight` scores the leaf 5.4x the
   root, because it is that much more specific a claim about an episode.
   `SUBTAG_WEIGHT` turns out to be that at one level and is now defined as it.
   Two halves, and the keyless one always runs: a phrase's parent is the facet
   its own sightings were tagged with, deepened by containment, and **a model
   places it properly** in one call per sweep for the whole deployment -
   which is the only way the levels *nobody typed* ("American Football",
   "NFL") exist at all. No key means a real, shallower tree and every node
   says so, which is `episode_intelligence`'s rule applied to a vocabulary.
   The failure worth knowing about, because it is not obvious and it made the
   first tree useless: **n-gram explosion.** Every sub-span of a phrase is
   seen by exactly the people who saw the phrase, so a listener threshold
   alone mints ten nodes for one four-word run - thirty-nine nodes from eight
   seeded queries, mostly "reserve interest rate" and "league title". Two
   filters took the same eight queries to four real subjects: a phrase must
   appear in **more than one wording**, because people ask about a subject
   several ways and about a fragment only one; and a phrase whose support is
   identical to a longer phrase containing it is the same subject with a word
   missing. `taste` re-reads an event's own text against the current tree
   while keeping its stored tags, so a node minted today makes last month's
   history legible instead of taking a month to be worth anything.
   `python tools/categories_report.py --tree --dry-run` is what says whether
   a deployment's vocabulary is full of subjects or full of noise, and it
   spends nothing. **Nothing here has been run against a real event log** -
   the seeds above are synthetic, and what the tree looks like on real
   searches is the first thing to look at.
   **And it no longer starts from nothing** *(§126, `category_seed.py`).*
   Everything about growing a vocabulary out of real searches was right
   except its first day: `MIN_LISTENERS` is three, so a deployment with no
   traffic has **no vocabulary at all** and ranks on the eight facets this
   module exists because of. 180 hand-written nodes, two levels under each
   facet, applied at boot and after a wipe. It buys two things - resolution
   in a listener's *profile* from their first search rather than their
   third, and the intermediate levels containment can never invent, so a
   phrase a placer sees lands under "american football" instead of under
   "sports". **A floor, never a ceiling**: the sweep is untouched, `mint`
   still adds what nobody declared, a re-seed never reparents what a model
   has since moved and never refreshes `last_seen` on a node it did not add
   (which would make the whole tree immortal), and a seeded node claims zero
   listeners because `MIN_LISTENERS` is the spam control and inflating it
   would be lying about the one number that decides what gets in. `prune`
   exempts it: `NODE_TTL` asks whether an *observation* went quiet, and on
   the deployment a seed exists for every leaf of it looks stale by
   construction.
   **And the join that was missing** - `topics.topic_tags`. The tree could
   already see "college football" in a tile's question and `_affinity` read
   the tile's hand-written tuple, so a listener whose whole history was
   college football scored the bank's college-football tile *below* its golf
   tile. A tile is now scored as though somebody had typed onto it every tag
   the tree finds in its query - the same treatment a subtag already gets,
   numerator and denominator both - which is what makes a vocabulary visible
   on a browse page at all. A tile the tree says nothing about comes back as
   the identical tuple, so an empty tree ranks exactly as it did before any
   of this existed. Memoised on the tree's generation, because §122.
   **And `_is_broad_match` is the same join a second time**, which is the
   half that is easy to miss: `BROAD_MATCH_PENALTY` decided "specific" by
   looking for a subtag in the declared tuple, so a live story about college
   football was still cut to 0.3 for a listener whose profile says `college
   football` - by the mechanism that exists *because* the vocabulary could
   not say it, at the moment it could. `_is_specific` is now read by both
   that and `tag_weight`, and a test says they agree. **`diversify`
   deliberately keeps the declared tags**: it caps tiles per *heading*, and
   `facet_of` returns an unknown tag unchanged, so a category would arrive
   as a facet of its own and the variety cap would stop binding.
   **And §122 is what happened when the work was checked rather than
   re-read.** Four defects, three of them §121's own, none visible to any
   test in the suite, all four found by running the thing at a realistic
   size: `storage_doctor` did not list the new store (and the guard that
   should have caught it compared one hand-written list to another, which is
   §107's finding made inside the test enforcing it); the sweep's
   subsumption pass was 91 *seconds* on a full window, inside a
   `create_task`, so once an hour every request on that worker stopped;
   `taste` cost 134ms on the browse path because a property was re-splitting
   a phrase a million times; and the fix for the second made a latent race in
   `reload` reachable. All fixed, and three tests now assert a **bound**
   rather than a result, because nothing about correctness says how big the
   input gets. The lesson is §52's in a different register: a test that never
   runs at production scale is inspecting rather than verifying.

5a. **What a deployment with nothing in it shows** *(§124, measured on an
   emptied database rather than reasoned about).* Signed out with an empty
   log, `taste_source` is `startup` and the first rail is §116's
   time-anchored starter set under the heading **Start here**; Trending,
   "What you missed last week" and the friends rail *choose* nothing and have
   their own sentences, and Explore says "Nothing here yet". **Since §127 the
   first two are then topped up to `RAIL_MINIMUM`** at the owner's direction,
   so on screen only the friends rail is empty. Once there is
   listening, `taste_source` becomes `taste` and the rail is "Made for you",
   ranked. **The switch is on having a profile, not on being signed in** -
   §116's decision, not an oversight: a brand-new account has nothing to
   personalise on, so it gets the prior too, and one play retires it.
   The one row that over-claimed on a blank slate was
   **"What FAM can't stop listening to"**, which filled from the bank when
   nothing had been played ("a stable slice beats an empty section, and beats
   a random one"). §124 left it alone and said reversing a documented
   decision belonged in a change about that decision; **§125 is that change,
   at the owner's direction.** The row holds plays and nothing else now, an
   unplayed row is empty and says so, and `tools/seed_demo.py` is what fills
   it for a demo - the same bargain Explore already makes.

5b. **The taste model is crude, and less crude than it was.**
   *(§114 sharpened the scoring itself, which nothing before it had touched.
   Three changes, all in `topics.py`. `_affinity` was **blind to subtags** -
   `sports` and `sports-drama` counted the same, so the resolution §80 added
   to the *vocabulary* was being thrown away by the *ranking*; `SUBTAG_WEIGHT`
   is the other half of that change. `RELEVANCE_FLOOR` replaces `> 0` in
   `rank_from_history`, which every tile sharing one barely-touched facet
   cleared, so "Made for you" was very nearly the bank, sorted, under a
   heading claiming it had been chosen. And `BROAD_MATCH_PENALTY` answers the
   case the vocabulary **cannot express**: there is no tag for the NFL and
   none for college football, both are `sports`, and no weighting
   distinguishes them. What is knowable without inventing a vocabulary is
   whether this listener has ever *said* the words on the tile -
   `familiar_words` reads their own searches and plays out of the event log -
   so a live story matching only a whole facet, on a subject they have never
   been near, is damped. It damps and never excludes, and only live stories:
   a rule would empty a new listener's rail in the name of relevance, and the
   bank's twenty-eight subjects are broad on purpose.)*
   *(Two things on the surface changed in §95. The header's right-hand slot is
   now an **episode-length control of its own**, deliberately separate from the
   search player's: the length you want for a question you have just typed and
   the length you want for a tile you are scrolling past are different
   appetites, and one shared number meant setting either silently reset the
   other. And each rail ends in a chevron only while there is something left
   to scroll - at the end it becomes **View more**, which opens the whole of
   that section. That screen is the same ranking at full length, with the
   tiles whose script is already written marked and sorted to the front; it
   generates nothing, because the rail was showing six of something that
   already had twenty-eight.)*
   `topics.py` ranks a *shared* bank of ~28 topics **four** ways (history /
   exploration / co-listener / trending) from an append-only event log. Tags
   still come from keyword matching, not a classifier - but there are now
   **two levels** of them (PROBLEMS.md §80), and that was the binding
   constraint rather than the scoring. Eight tags over twenty-eight topics
   gave the bank twenty distinct signatures, so a listener with one facet of
   history got three *identically* scored candidates and a grid ordered by
   `topic.id`. Twenty-nine subtags under the same eight facets take the bank
   to 27 distinct signatures. **The eight facets are unchanged and are still
   the only pickable vocabulary** - the intro picker is built from
   `TAG_LABELS`, and the resolution is for the ranker, not for the listener.
   **There is no language picker** *(§100).* It was the first run's second
   page, was never wired to generation, and its only honest companion was a
   note saying so - a question in front of the product that answers to nothing
   is worse than no question. The *field* stays: still stored, still accepted
   on `/api/preferences`, still validated against `preferences.LANGUAGES`,
   because that is what per-language generation reads on the day it exists and
   dropping a column is a migration with no benefit. What went is the screen.
   A subtag always carries its facet, so nothing that matched before matches
   less. Anything a listener *reads* goes through `facets_only`.
   **The first run is a wheel** *(§99).* Six discs orbiting "View more",
   turning slowly counter-clockwise, selectable while they move. The ring
   rotates and each disc counter-rotates by exactly as much, so positions
   orbit while labels stay upright - both CSS animations on `transform`, which
   is what keeps them in lockstep with no code running per frame.
   `prefers-reduced-motion` stops the turning and keeps the wheel.
   **The two only cancel while they are at the same point in their cycle**
   *(§100, and this corrects §99's comment).* A *new element's* animation
   starts at zero, so a disc rebuilt mid-revolution counter-rotates from the
   wrong place and its label sits at an angle - which is what tapping one used
   to do to all six. Selecting therefore toggles a class in place and never
   rebuilds, and any rebuild that does happen ends in `syncWheelPhase`, which
   puts every disc's `Animation.currentTime` on the ring's. **Anything that
   redraws the wheel has to go through that**, and the smoke behaviour reads
   each label's net angle after a tap rather than trusting the claim.
   **There are two wheels, and they answer two questions** *(§100, revised by
   §107).* The first run draws `popular_facets` - somebody with no history, so
   the honest signal is what everybody plays. **Settings now draws what this
   listener *chose*** - their facets, plus the subjects they added from the
   catalogue or typed into its search - and nothing else. It used to draw
   `my_facets`: what they play, then what they chose, then the declared order
   as filler, so the wheel always had six discs. Three of those four sources
   are the app's answer rather than the listener's, and a screen called *Your
   interests* that shows a recommendation is answering a question nobody
   asked. The filler is gone and an empty wheel is possible; what fills it is
   the hub in the middle, which says **"Edit/add topics"** and is the only
   route to the catalogue now - the second door in Settings came off with it.
   `my_facets` is still computed and still served as `interests_yours`,
   because it is an honest signal and the answer to a real question; nothing
   draws it today.
   **And what somebody chose is stored now, which it was not.** Adding a
   catalogue topic wrote a `pick` event into the append-only log and kept no
   list, so the choice taught the ranker something and left nothing to show,
   nothing to remove, and nothing for a wheel meant to reflect it to draw
   from. Both happen now - `preferences.topics` is the statement, the event is
   the behaviour, and `topics.py` stays a pure query over the second. The
   catalogue's search can add **whatever was typed**, because seventy-three
   strings somebody wrote down is not the set of things a person can be
   interested in, and a search that can only fail is the worst control on the
   one screen whose job is collecting interests. `interests_yours_source` says
   which of the three decided it, and only the Settings wheel carries a line
   of copy - one that changes on its own without a word reads as the app
   having lost somebody's answer. **And there is no cap on how many
   interests somebody has** - it was six, it made a listener with seven pick
   which to lie about, and nothing counts them now. No cap is not no
   validation: every value must be a facet and duplicates collapse, so eight
   is the ceiling, as a fact about the vocabulary rather than a rule anybody
   is told.
   **The picker shows six of the eight, and they are the six being played**
   *(§98, `topics.popular_facets`).* Global play counts, like
   `rank_most_played` and for the same reason: the first run asks this of
   somebody with no history, so the only honest signal is everybody else's,
   and one count serves every listener. This narrows the *screen* and not the
   vocabulary - the two that miss out are carried by the catalogue's
   interests, and `interests_all` is served beside `interests_available` so
   Settings can still read back a stored interest that did not make the grid.
   `PICKER_DEFAULT_ORDER` is what an empty log gets, and `interests_source`
   says `"default"` rather than `"played"` when it does, because a declared
   order and a measurement look identical on screen and calling the first one
   "most popular" would be inventing a number.
   **Impressions now feed the ranking, in exactly one direction.** A tile
   shown on several separate occasions and never played is damped
   (`FATIGUE_WEIGHT`). The existing rule stands and is enforced: an impression
   must never become *taste* - that is a feedback loop where the feed teaches
   itself its own preferences. Fatigue is per-topic, never per-tag, and can
   only push a tile down, which is what makes it safe. Trending is exempt: it
   is the same list for everyone, which is what makes it cheapest to serve.
   **The second slot is now "Trending", and Explore New came off the page**
   *(PROBLEMS.md §96).* `rank_might_like` is still computed, still takes its
   turn in `FILL_ORDER`, and still serves the Explore New *screen* - it is in
   `topics.UNSHELVED`, which is the difference between a ranking that is not
   drawn and a ranking that was deleted. What moved is what the second rail
   *says*: the world row was at the bottom of four, under two forms of "what
   you already like", which is the worst place on the page for the one row
   that is about today. (§102 then reordered and renamed the two crowd rails
   and gave the top two a live inventory - see above.)
   The cost of it, stated so it is a known trade: adjacency is no longer
   offered unprompted, so the page is now two personal rows and two crowd
   rows, and nothing on it reaches outside an established taste until the
   listener opens Explore New themselves. `UNSHELVED` exists so putting it
   back is one tuple entry. Anything that iterates `FILL_ORDER` and then looks
   the key up in the drawn sections must skip `UNSHELVED`, or it is a
   `KeyError` rather than a finding.
   The intro's chosen interests now seed `taste`, so "Made for you" is no
   longer honestly empty on a listener's first open, and
   `INTEREST_CATALOGUE` is the long list behind "View more", which is the
   wheel's hub - **73 named subjects, not 73 new tags.** Each carries facets from the same eight, so
   the settled constraint above is untouched: an interest is something a
   listener recognises, a tag is what the ranker scores, and the picker is
   still built from `TAG_LABELS`.
   The cost design is the load-bearing part: **one bank for
   everyone, personalisation in the ordering, not the inventory** - so two
   people tapping a tile share one script through `cache.py`. That is
   unchanged by the live pool, which is shared for exactly the same reason.
6. **playFAM is built as its own tab.** *(§136 changed what a mix holds:
   followed catalogue subjects, narrowed or whole, rather than bank episodes -
   the picker below now lists subjects, and bank ids survive only in older
   mixes. The dated prompt is the server's, so prefetch warms what a tap
   sends.)* `mixes.py` stores named daily mixes -
   a mix holds *topic ids*, never audio, so "At the gym" is the same subjects
   every day and a different set of episodes. Members are validated against the
   same shared bank, which is what keeps the cost design intact.
   Two things the picker got wrong are now right. Its heading said
   **"Suggested topics"** over the bank in `topic.id` order, which is
   alphabetical by slug; `/api/topics?ranked=1` orders it with
   `topics.rank_bank`, the same taste model every rail uses. That ranking is
   **a sort and never a filter** and **does not exclude what they have
   played**, which is where it parts company with a rail: the picker *is* the
   list, and wanting a mix of subjects you already like is the entire point of
   a mix. The heading reads `personalised` rather than asserting, because a
   declared order and a measurement look identical on screen.
   And the **privacy switch means private**, with the lock on the knob: closed
   and lit for Private, open for Public. It used to mean public, so the lit
   position was the one where other people could see your mix - read backwards
   by everybody who has ever used a phone.
   **And the picker can be given a topic somebody typed again** *(§111).* It
   could not, for as long as the interests catalogue has existed: both screens
   declared a top-level `addTypedTopic`, the later one won, and every tap on a
   typed row in the picker ran the catalogue's function against a search box
   that is not on that screen. Legal JavaScript, so nothing threw and nothing
   failed - the button was simply inert. The names say which screen they serve
   now, and `tools/check_js.py` fails on any top-level name declared twice,
   because every control in this interface is an inline `onclick` naming a
   global and nothing else can see one being quietly replaced.
   **And it could not be given one logged out, for a different reason**
   *(§123).* `loadMixes` asks `/api/mixes` and `/api/topics` at once and
   returned on the 401 from the first **before** taking the bank off the
   second - which is not account-gated and had answered perfectly. An empty
   `topicBank` is what the picker reads as "Could not load the topic list",
   so searching it offered nothing and the (+) after a search had nothing to
   add. A fact about the listener's account, reported as a fact about the
   server: §89's rule about empty rows, one screen over. The bank is taken
   before the branch now, and a test reads `loadMixes`'s own source, because
   the failure renders a plausible sentence and throws nothing.
   **And the (+) that opened it is not offered to somebody who cannot keep a
   mix.** It was in the markup unconditionally, so it opened a naming modal
   and a whole picker and refused only at the save - two screens to say what
   the screen behind it already says with a Sign up button on it. Hidden
   until `/api/mixes` answers, shown by `renderMixList`, and hidden to start
   rather than shown and taken away, because a control that appears and then
   vanishes reads as a fault.
7. ~~**"What your followers are listening to" has no follow graph behind it.**~~
   - *done, on both halves* (`SHARING.md`, PROBLEMS.md §102). Follows are
   asymmetric, like the copy always said, and a **friend is the mutual case,
   derived and never stored** - no request, no accept, no pending state to get
   wrong. And the rail reads it now: `social.circle_of` is friends first, then
   anyone they follow, which is what stops a listener who has just followed six
   people getting an empty rail on the day it should have filled. The worry
   that kept this open - a new listener follows nobody, so a rail backed only
   by follows is empty on the day it matters most - was answered by admitting
   it rather than papering over it: the rail is **honestly empty and names the
   two taps that fix it**, because the alternative was what it did before,
   which was to show strangers under a heading that said "people you follow".
7b. **A friend's profile is its own screen, and it shows what they published.**
   *(§104, `SHARING.md`.)* The navigation bug the packet reported had four
   symptoms and one cause: somebody else's profile was drawn into
   `screen-profile` with a variable deciding whose it was. So back from the
   friend popped to Friends, back again showed that screen still holding the
   *friend's* DOM, back again fell through to search, and the Profile tab
   flashed them. **Two pages sharing one container is one bug, not a routing
   bug with four fixes** - `screen-person` is its own screen now, and
   `viewingProfile` decides only what is drawn there, never which screen is on.
   `/api/person` returns **only what they chose to publish**: public mixes,
   vibes (a vibe *is* the act of showing somebody an episode), and the
   interests they have not hidden. No play count, no completion total, no
   inferred subjects, no history - what somebody has listened to is theirs. The
   boundary is the graph: a handle resolves for anybody, a bare id only for
   somebody already in the asking listener's follows, and the response carries
   no id of its own. The interest choice is stored as the **hidden** set, so an
   existing row means "all of them" - storing the shared set would default
   every profile in the app to an empty pill row that reads as broken.
   **A new follower is announced, once, with a way to answer it.** "___ started
   following you", their picture, Follow back, and an X. `new_followers` is a
   query over the follow graph's own timestamps against one column saying when
   the listener last looked - not a second table with its own read state - and
   it is cleared by the **Friends tab**, never by the popup being drawn: a
   badge that cleared itself the moment something drew it is a count nobody got
   to read. `follows_back` rides along, because offering the button to somebody
   already followed is a control that cannot do anything.
   **And eighty-five per cent through is finished.** Waiting for the last
   sample counted almost nothing: the ending is the one part a listener skips,
   so an episode heard to the ninety-fifth percentile and closed was recorded
   as a *play* - worth 1.0 against a completion's 2.5. The strongest signal the
   taste model has was being thrown away by exactly the behaviour it should
   reward. A share rather than a number of seconds, because "the last thirty
   seconds" is most of a one-minute episode and nothing of a ten-minute one.
8. **The social layer generates nothing, and it updates itself now** *(§107).*
   Messages appeared only when the chat screen was opened, so two people
   talking had to leave the conversation and come back to see each other -
   which is not a slow chat, it is a chat that does not work. `/api/messages/
   thread?since=` tops one up from a cursor and `/api/notifications` answers
   both drop-downs - a message, a new follower - from one poll, because the
   interface asks both questions from the same timer.
   Two rules hold it up. **The cursor is a row id, never a timestamp**: two
   messages can share a `time.time()` and a `> at` cursor silently drops the
   second of any such pair, which on a chat is a lost message. And **the
   cursor is the client's**: whether a message has been *read* is a fact about
   a conversation somebody opened, whether it has been *announced* is a fact
   about a banner this client raised, and deriving the second from the first
   would mean opening one chat silenced every other's notifications. A
   `bootstrap` call establishes where "new" starts and announces nothing,
   without which opening the app raises a banner for every message ever sent
   to you. Sending draws the message before the round trip and swaps the
   written row in for it - it used to wait for the POST and then re-fetch the
   whole conversation, which is where "a few seconds of latency when I send"
   came from.
   None of this generates anything, which is the point of the section:
   `social.py` stores a **vibe** as a row pointing at a query whose script
   already exists. *(The product's word is VIBE!; the codebase's is `echo`,
   and they are the same row. `/api/vibe` and `/api/echo` are one handler over
   one table, because a phone that has not updated is still calling the old
   one - a rename that breaks it turns a copy change into an outage. The
   `data-echo` attribute and the `echoed` class keep their names for the same
   reason: a hook renamed for a copy change is a button that quietly stops
   being found.)* **VIBE! is on every real player and deliberately not on the
   mini bar** *(§97, reversing §95's "every player").* The bar is a strip with
   three things competing for one thumb - open, pause, close - and the only
   irreversible one of them was the one that posts to your friends. The smoke
   check asserts the absence, so it is a decision rather than a regression
   waiting to be helpfully undone. `messages.py` does the same for a
   *directed* share - one person, one
   episode - and `sharing.py` for a link posted outside FAM. All three cost one
   row: sending an episode to ten people costs ten rows and not ten episodes,
   because their taps are what synthesise audio, from one cached script,
   against their own allowances. Mixes are private by default and appear on the
   profile once made public.
9. **Profile is the personal hub now, and still invents nothing.** *(§95.)*
   **And it is called YourFAM** *(§133, the owner's handoff).* The last tab is
   one social hub - identity, story-style friend avatars, Messages as one
   card, the public shelf - with a Topic screen behind every interest chip.
   The "This month" card, the inline conversation list and the "From your
   FAM" feed are off it on purpose. Everything on it is read from the API:
   the handoff asked for a mock social module because its prototype had no
   backend, and this one has the real graph. The paragraphs below describe
   the page it replaced where they talk about tiles and the profile's own
   interests sheet.
   `/api/profile` returns only what the event log and the follow graph
   actually hold - started, finished, open threads, subjects, vibes, and a
   friend count that is real because the graph is built. The page is a hub
   over four shelves the listener owns (Save for Later, Downloads, My Vibe,
   Friends), a picture they can set **and then crop** - move-and-scale, with
   the preview and the export computed from the same three numbers, because a
   crop the listener cannot see is a guess about where a face is (§96) - and a
   **Settings** screen that gathers
   everything changeable in one place - it was spread across a modal, the
   first-run screens nobody sees twice, and an action sheet inside the player,
   so "where do I change that" had three answers and two of them were wrong.
   **The first run asks who they are, and Edit profile is a screen** *(§104).*
   After credentials and before the interests there is a step for a name, a
   username and a picture - in that order, because a name and a handle are
   about *them* and the interests are the last thing before the app, so asking
   for interests first would put a form between somebody and the episode they
   came for. It is asked **only after signing up**: a handle is for other
   people to find somebody by, which is worth nothing without an account to
   find, and "Skip for now" is one decision about setup rather than two.
   The Edit profile pill opens the same screen as an editor - `identityMode`
   decides whether there is an X, what the docked button says and where
   saving goes, exactly as `introMode` does, which is the trap the intro
   screen fell into three times. It carries the picture, the name, the
   username, Change password, and **which interests are shared**: stored as
   the *hidden* set, and hiding one changes the profile and never the ranker,
   because an interest is a statement about what to play and hiding it is a
   statement about a screen.
   What it replaces: two chained modals asking for a name and then a handle,
   with no way back between them, no picture at all, and placeholders reading
   "e.g. Ian Solomon" and "iansolomon" - a real-looking name and handle
   offered to every listener in the app.
   **A settings row is an editor, never the first run happening again**
   *(§98).* Interests and Language have no editor of their own and reuse the
   intro screen, and reusing the screen meant reusing the flow: Next chained
   on to the other setting and "Start listening" wrote `intro: "done"` and
   dropped the listener on myFAM. `introMode` decides three things and nothing
   else - whether there is an X, whether the docked button says Save, and
   where saving returns to. Everything a settings row opens closes back to
   Settings, by an X drawn the same way on all of them
   (`.sheet-close` on a screen, `.modal-x` on a modal).
   **The interests row is five, ranked by listening** *(§114; five and
   edited in Edit profile since §133).* It was twelve pills - chosen facets, then chosen subjects, then
   whatever the log had inferred - in that fixed order, so six words picked
   in thirty seconds on the first run outranked a month of listening for
   good, and the row grew with every episode until it listed everything
   somebody had been near. `topics.ranked_interests` ranks it by the same
   `taste` profile every myFAM rail uses, so it moves as they listen, and a
   declared interest is not lost by that - `taste` folds it in at
   `INTEREST_WEIGHT` before normalising, which is exactly a starting position
   behaviour outvotes. `topics.profile_interests` cuts it to five and says
   **which** decided it, because a pinned row and an automatic one look
   identical on screen and the copy under them is only true of one.
   The editor moved with it: "Shared on your profile" was inside Edit profile,
   three taps from the row it changed and phrased as hiding rather than
   choosing. §114 moved it onto the profile beside the pills; §133 moved it
   back into Edit profile as **Your interests - N of 5**, because the hub has
   no inline Edit. Toggling a suggestion is still about **showing** and never
   about liking - it writes `profile_interests` and not `interests` - and a
   topic somebody types there is also kept in `topics`, because it is a new
   statement of what they want to hear.
   The boundary that needed deciding: **their own page may be ranked off
   their listening and `/api/person` may not.** §104's rule is that what
   somebody has listened to is theirs, and an inferred pill row on a
   stranger's view of them publishes exactly that, in a form that reads as a
   statement they made. A pin is a statement; a declared interest is a
   statement; a history is not. So the public view is the pinned set, or
   declared-minus-hidden, capped at the same five - and pinning is how
   somebody's own page becomes their public one. `hidden_interests` is still
   honoured when nothing is pinned: somebody who turned an interest off
   before the editor moved did not ask for it back.
   The rule it was built under is unchanged: a profile page is the easiest
   place in an app to invent a number, and every invented one is a promise to
   keep later.
10. **Attachments are built** (`attachments.py`, PROBLEMS.md §47). A search can
   carry documents, photos and links. Extraction happens when the thing is
   attached, never on the generation path, because a round-trip in front of the
   first word is the one cost this product refuses. Every failure is a sentence
   the listener can act on, and an attached episode is **never cached**, so it
   cannot reach another listener or Explore. Only PDF needs a package (pypdf,
   optional); .docx is read with `zipfile`.
11. ~~**Personalisation needs state the app does not have**~~ - *identity is
   done; the recommender is still crude.* `accounts.py` gives every listener a
   server-minted session id in an HttpOnly cookie, and an account is *email and
   password attached to the id they already have* - so signing up keeps the
   same identity rather than starting a second listener beside it (and, since
   §127, starts its history: a guest's listening is never recorded), and
   logging in on a phone reaches the same data. **Listening still works with no account at
   all** - search, myFAM, DailyFAM's episodes, Explore and Go Deeper - which is
   the constraint that stopped this becoming a login screen in front of the
   product. What an account now buys is durability: mixes, chosen interests and
   language, and the weekly recap are gated on having one (PROBLEMS.md §70, and
   the constraint above). Sign-up is now **email or phone**, and **Sign in with
   Google or Apple** attach the same way - one account, several routes in,
   keyed on `(provider, subject)` because Apple sends an address on the first
   authorization only. What is genuinely missing is one capability behind three
   gaps: **delivery**. Without it there is no **password reset**, no verified
   address and no verified number - so a phone number is an identifier rather
   than a second factor, and a forgotten password is still a lost account. Say
   so before anyone relies on it; the provider sign-ins have no such gap, which
   is a real argument for making them the prominent buttons in the app.

## Constraints that are settled — do not undo without discussing

- **No MP3, no audio files.** Raw PCM streams from the TTS engine to the browser
  and is played as it arrives. This is the core of the product. Compression
  (Opus over a stream) is compatible with it and is the right answer at scale;
  writing a *file* is not.
  **The server keeps a cached episode's audio now, at the owner's direction**
  *(PROBLEMS.md §132, reversing "nothing anywhere keeps audio").* The voice
  runs on RunPod, and re-voicing a cached script on every play made the GPU
  the largest line on the bill. So the first time an episode is spoken in a
  production voice its PCM is kept in `scripts.db` (`episode_audio`, beside
  the script, keyed on script and voice), and every later play - a search
  hit, an Explore card, a shared link - is read from there and **never
  reaches the voice engine**; `/api/audio` does not even wake the GPU for
  one. What this does not change is the subject of the rule: it is raw PCM
  in a database row, zlib-compressed, streamed as raw PCM exactly as it was
  the first time. No audio *file* is written, and nothing is sent to the
  listener as one. Four rules keep it honest: the audio is readable only
  while its script is, and a re-written script drops it; only a production
  voice is ever kept (`TTSEngine.keeps_audio`), because a placeholder tone
  written there would outlive the outage that produced it (§51); only a
  whole episode is kept, never an abandoned stream; and it has a ceiling
  (`AUDIO_CACHE_MAX_MB`, 512 by default against a 1 GB disk), least recently
  played out first, evicting only audio - an evicted episode keeps its
  script and costs one re-voicing. **§134 widened what is kept, inside those
  rules**: a request that names no voice keys its audio as the default
  voice's own id (a shared link and the app were storing one episode twice);
  a script that makes no claim about a window of time (no recency window,
  no result-dependent question, no fixture yet to happen) has its life slide
  forward on each *play* - never on a look - up to `CACHE_MAX_AGE_SECONDS`, so a popular episode
  keeps its audio; a re-write with identical words keeps the audio; and a
  near match with kept audio does not wake the GPU. `AUDIO_CACHE=0` restores re-synthesis on
  every play exactly. Downloads - audio in the listener's own IndexedDB -
  are still **removed**, and `saved.py` still holds pointers and only
  pointers.
- **Duration is a ceiling, not a quota.** *(Revised.)* The selected length still
  caps the episode and over-runs are trimmed, but a script that runs out of
  substance now ends early instead of being padded. Enforcing the number in both
  directions is what produced filler: it made the model pad. `ALLOW_TOPUPS=1`
  restores the old behaviour.
- **Transport: two gestures, and both stay.** *(PROBLEMS.md §71.)* The
  progress bar is draggable on all three listening surfaces, and the
  fifteen-second buttons are untouched. They answer different questions - the
  buttons "say that again", the drag "get me to roughly there" - so neither is
  a replacement for the other, and removing either would be a regression. The
  drag clamps at what has actually been written, because the episode is still
  being generated while it plays.
  **And one episode has one transport** *(§97).* Four controls pause the same
  `FamAudio` - the search player, play-all, Explore's reel and the mini bar -
  and each used to keep its own boolean and redraw only its own icon, so
  pausing on one left the others drawing a pause button over stopped audio.
  `setPlayState` is the only thing that moves the audio now; everything else
  delegates to it and it redraws all four. Adding a fifth player is one line
  there, never a fifth idea of whether something is playing. The mini bar was
  the case that proved it: its button returned early whenever `isActive()` was
  false, which is exactly the state that bar is in most often - still on
  screen after the episode finished, with a play button that did nothing.
- **Nothing speaks before the thing it is about has arrived, and no setting
  buys that back.** *(PROBLEMS.md §108, at the owner's explicit direction.)*
  Twice now this product has tried to spend the opening on latency: the cold
  open filled the wait with contentless speech, and the answer-first cover
  filled it with an answer written by a call that had been given no brief and
  no evidence. Both were deleted rather than switched off, both had to be
  deleted twice because a knob left behind was turned back on - an example
  file the first time, `Dockerfile.gpu` the second.
  So the rule is on the *order*, not on any one mechanism: **the writer holds
  the brief and the evidence before its first token, and has the budget to
  decide what the whole episode is before it writes the first sentence of
  it.** Retrieval before writing on both backends, no tools on the call that
  speaks, episode intelligence on every episode. *(The writer's thinking
  budget is `EFFORT=low` since §129, at the owner's direction: §108 set it to
  `high` in the same commit that deleted the cover, so what effort bought was
  never separated from what the deletion bought, and `high` is expected to be the
  largest wait on search (unmeasured). It is a budget, not an order - the rule below stands.)* A change that
  reintroduces a text produced before its material is a regression however
  much time it saves, because the first ten seconds are the only part a
  listener uses to decide whether there will be an eleventh.
  What may still be spent on latency: anything **before** the tap. That is
  what prefetch is, and it is why the browse surfaces get to be instant while
  search waits.
- **No filler, ever, and no setting for it.** The cold open was deleted, not
  disabled - a knob left behind is an invitation to turn it back on, and this
  one was turned back on by an example file. Nothing plays until the real
  briefing does. The interface says what it is waiting for and how long it has
  been waiting; a wait you were warned about is a different experience from the
  same wait unexplained.
- **Every episode is researched. (Reversed — this used to say the opposite.)**
  *(PROBLEMS.md §76.)* `SEARCH_MODE=always` is the production default and the
  question no longer gets a vote. The old rule was "search is opt-in, and the
  question opts in": `auto` read the query with the cache's freshness keywords
  and answered everything else from memory. That was correct arithmetic against
  research that cost **10-25 seconds** — the model's own `web_search` tool.
  `RESEARCH_BACKEND=exa` retrieves in about **half a second**, and at that price
  the guess only ever loses: a question it gets wrong is answered from memory
  that may be a year stale, and one it gets right saves nothing a listener can
  hear. Production proved it on `49ers game last night`, logged as *"nothing in
  it reads as time-sensitive"*. A keyword list can always be widened by one more
  word, and the next question it misses is already written.
  `auto` and `never` are kept and are **not production** — offline `write.py`,
  `tools/compare_search.py`, a deployment with no Exa key. `search=1`/`search=0`
  on a request still wins. This no longer has to defend the one-sentence spec
  either way: §108 amended it, and research is now openly one of the things
  paid for in front of the first word.
  **And an empty search is a ladder, never a shrug** *(§109, §110).* The
  rungs, in cost order, stopping at the first with evidence: the configured
  backend, the same backend with the recency window dropped, then **GDELT**
  (keyless, one HTTP call - promoted from additive cross-check to a retriever
  of its own). **Nothing below GDELT** *(§135, at the owner's direction)*:
  the model's own search was the last rung and is **deleted**, not switched
  off - everything an episode is written from comes from **Exa, GDELT,
  API-Sports, Finnhub and Polymarket**, and the model never searches the web
  for FAM. A deployment still setting `RESEARCH_BACKEND=claude` runs on Exa
  and says so at startup and on `/api/health` - replaced rather than refused,
  so merging the removal cannot stop a server booting.
  `research.ladder()` is the **one** definition of that order and it lists
  only rungs that can actually serve - a GDELT switched off is not a rung -
  and the runtime, `/api/health` and the startup warning all read it rather
  than keeping a second copy. Every rung that runs is metered even when its
  packet is discarded, every rung gets the same sufficiency check, and
  evidence means a source was *read*: a model's prose about having found
  nothing is not a packet. **No rung may
  raise** - `research.retrieve` raises whatever the vendor raised, and with one
  stream that is the whole episode, so a 502 at a search vendor used to be a
  listener hearing nothing. Every fallback is recorded (`fell_back_from` on the
  packet, `notes.research` on the episode): the rule was never "do not fall
  back", it is **never fall back silently**.
  At the bottom, `research.NoEvidence` refuses the episode rather than writing
  it from memory - **and only when the question turns on something current**,
  by the precedence `cache.ttl_for` already uses: a live state ends the
  question, then `outcome_dependent`, then any recency window, then the keyword
  floor for a degraded brief. An evergreen question is never refused, because
  there the model's knowledge is accurate and a refusal is the worse answer.
  The check lives in `prepare`, not `research`, because that is the first point
  where the live lookup and the retrieval have both answered - a score from a
  provider answers the question whether or not an article exists yet. And a
  refused episode is refunded whatever was billed, the one exception to
  `_refund_if_unspent`'s rule: the spend was FAM's decision, not the
  listener's. An **attachment is evidence** and is never refused - the
  listener supplied the document the episode is built on.
  **And EI is where an empty search is cheapest to prevent** *(§110).* It is
  a model call that is already being made, so it writes a second, broader
  query in the same breath as the precise one (`Brief.broader`, which every
  rung after the first asks), and sets the recency window to the widest span
  that is still honest, with `RECENCY_FLOOR_DAYS` as the floor - the window
  is a filter, so one that is too narrow returns nothing while one that is
  too wide costs nothing. That is the only latency this layer may spend on
  the problem: a handful of output tokens, never a second call.
  **And the research finishes before the writing starts — on both backends**
  *(§108, replacing §77's "a tool is not an instruction").* When no evidence
  packet came back — `RESEARCH_BACKEND=claude`, or Exa finding nothing usable
  — the `web_search` tool used to be attached to the **writing** call, so the
  model searched while it wrote and the first sentence was composed before
  anything had been read. §77 made the prompt ask it to search first, which is
  a mitigation of an ordering. The ordering was fixed by making the model's
  own search a retrieval of its own, and §135 then deleted that too: **the
  call that speaks carries no tools at all**, and a packet from Exa or GDELT
  (plus a live fact) is the only way evidence reaches the writer.
- **Something decides what to search for, before the search.** *(PROBLEMS.md
  §82.)* `episode_intelligence.py` runs one model call between the typed
  question and Exa and produces a `Brief`: intent, resolved subject, a why-now
  hypothesis with its confidence, the query to actually run, what the evidence
  must establish, how fresh it has to be, the story shape, and what the writer
  must not assume. One call feeds both halves — `research` reads the retrieval
  fields, `build_prompt` reads the rest.
  **It is before retrieval because a gate after it is worthless**: a critique
  downstream of Exa can only judge an episode built on whatever the packet
  happened to hold, and if the query was wrong the evidence is already the
  wrong evidence. It is also why the brief **never asserts a fact** — nothing
  has been retrieved when it runs, so it produces cautions and questions, and
  event status is settled downstream from dated evidence.
  The rule it adds, which generalises: **a layer that adds quality must not be
  able to subtract availability.** Every failure — no key, a timeout, a
  refusal, unreadable JSON, a gate that trips — falls back to the raw query,
  which is exactly what FAM did before it existed, and says so in
  `Brief.degraded`, the log and `/api/health`. An EI that had quietly stopped
  running would look identical from outside to one that was working.
- **Recency filters; credibility sorts.** *(§82.)* Two mechanisms doing two
  jobs, rather than a weighted score nobody can reason about. The window
  (`start_published_date`, from the brief) decides what is eligible, so nothing
  stale is considered however well linked it is; `rank_results` then orders
  what survives by publisher grade, newest first within a grade. A question
  about last night is therefore answered from last night, by a wire service
  rather than by whoever published fastest.
  Two details are load-bearing. **The packet carries the date and the grade and
  never the hostname** — a domain in the packet is a domain the voice can read
  out, and the model needs to know it is reading a wire service in order to
  weigh it, not a way to say "reuters dot com" aloud. And **the relative phrase
  is computed in code**, not left to the model: "yesterday" is subtraction, and
  the failure it replaces was an episode asked to date events from evidence
  that carried no dates at all.
  A thin packet buys **one** more search — on the resolved subject, window
  dropped, never a model call to rephrase — and a retry that still misses
  returns its evidence anyway, with the gap *named* to the writer so it is said
  plainly rather than filled from memory in the same confident voice.
- **Started is not finished, and nothing upstream of the evidence may claim
  otherwise.** *(PROBLEMS.md §88.)* FAM wrote a final score for a game in its
  third quarter, and the chain that let it starts with a label: `recap` used to
  mean "something finished", which is a claim about the world made by the one
  layer forbidden to make claims, about the one thing it cannot know. What EI
  may decide is **whether the answer they want is a result** - a property of the
  request, decidable with nothing retrieved - and `Brief.outcome_dependent` is
  that. Whether the result *exists* is settled downstream, from dated evidence,
  and nowhere else.
  Three things follow and are load-bearing. **There are three states, not two**
  - not started, under way, finished - and the third shape (`in_progress`) has
  to be *named*, because "drop a beat you have nothing for" cannot apply to the
  result of a recap: that beat is what the shape is for, so dropping it deletes
  the episode and the model fills it instead. **A packet of previews is
  evidence of no outcome**, not thin evidence of one - odds, projected
  line-ups, "expected to", "how to watch" are all written before the thing
  happens. And **a contradiction is information**: an episode given a standing
  its own claimed result would have changed must take the smaller true reading,
  never invent a reconciliation, which is what "that's just a rounding
  artifact" was.
- **A search that missed something has established nothing about the world.**
  *(§88.)* `thin_on` used to instruct the writer to "say plainly that that part
  is not yet reported" - converting a fact about one retrieval into a claim
  about the world, and then usually into the last line. For a volatile thing
  the two nearly coincide; for a settled one they do not, and FAM told a
  listener next week's fixture "hasn't been pinned down" when it had been
  public for months. So the two are split by hand: something that **changes** is
  never supplied from memory, something **already settled** may be and is
  otherwise left out entirely, and the gap is never announced and never the
  thing an episode ends on.
- **Situate, never orient, and do it in the first two sentences.** *(§88.)*
  "Speak from inside" bans explaining why a subject matters; it was read as
  permission to open anywhere, and produced a 2017 draft anecdote in front of a
  live game. Situating is different from orienting: it says where the listener
  is standing - who, what, when, in particulars - and it is required. History
  earns its place by explaining the present rather than preceding it.
- **The opening is written last in the order that matters: nothing is spoken
  until the writer holds the whole picture.** *(PROBLEMS.md §94, and §108,
  which fixed its cause rather than its symptom.)* A researched episode's
  first words used to be produced while the sources were still being read - by
  the answer-first cover half, which had no packet by construction, or by a
  model that had not yet called the search tool. Asked what happened last
  night while holding nothing about last night, a lone answerer should say so,
  and one of them did, on air.
  §94 told it that it was not a lone answerer. **§108 stopped putting it in
  that position**: the cover is deleted, retrieval (Exa or GDELT - the
  model's own search went in §135) finishes first, and the writing call reasons before its first token
  (at `EFFORT=low` since §129) - so it decides what the whole episode is, from the brief
  and the evidence, and *then* opens. The prompt says exactly that, and adds
  the test the opening has to pass: read the first two sentences back against
  what the listener typed, and if they would also open an episode about
  something else, it has not started yet.
  The line this all respects, because §88 was paid for in a wrong final score:
  **where something stands in the world is the episode - "the game is in the
  seventh" - and where it stands in our notes never is.** Not having something
  is a fact about our own reading.
  And `OpeningGuard` stays, because a packet can still come back thin. It
  looks only at the head of a stream, switches off permanently once one real
  sentence is through, takes a dangling justification with the disclaimer it
  belongs to, ignores quoted speech, and **speaks a half that is nothing but
  disclaimer rather than leaving silence**. Every drop is logged, carried on
  `ScriptNotes.meta_openings` and printed by `write.py` - and now means
  something sharper than it did: the retrieval came back thin, not that the
  writer was guessing.
- **Live captions are live now, and so are the sources.** *(§107, revising
  §104's "captions read the cache", `live_captions.py`, `PROVENANCE.md`.)*
  §104 wired both panels to the script cache. That was right for a replay and
  wrong for a first listen, and the difference is when the cache is written:
  **once, at the end.** So on a fresh episode the sentences did not exist
  under that key until after the last word had been spoken, which is the one
  moment a caption panel is no use - and the interface papered over it by
  polling six times over twelve seconds and then saying "no transcript for
  this one", a sentence about attachments, under every researched episode
  anybody read along with.
  **The bug was not the poll count.** Polling a place the answer is not yet in
  cannot be fixed by polling it more, and the tempting one-line non-fix
  (raise the count) would have made it rarer and no less wrong.
  `live_captions.py` publishes each sentence as it is handed to the voice, so
  the transcript builds up while the episode plays, and `/api/transcript`
  reads that first and the cache behind it. Three things keep it small: it is
  **in-process with nothing behind it** (a live track describes a generation
  in *this* worker, and by the time another could read it the cache has it);
  it is keyed on the **cache key**, so a live track and the cached script are
  the same episode by construction and an attachment - which has no key - has
  no captions, one rule rather than a special case; and it says **`done`**,
  because "still being written" and "that is the whole thing" are different
  answers and a poll count was a guess at which.
  The rule that makes it affordable is unchanged: **it never generates.**
  Captions that could trigger a write would be a second full Claude call for
  every episode somebody chose to read along with. **Which sentence is shown
  is measured now** *(§127)*: the speaking path publishes where each sentence
  starts in the audio (`pipeline._sentence_starts` - the chunk's start and
  length are measured, only the split inside one synthesised chunk is by
  characters), `/api/transcript` returns `starts`, and the panel shows that
  one sentence alone, cross-fading to the next. The old estimate divided by
  the *planned* length, and an episode usually runs under its ceiling, so it
  ran about a sentence behind; it survives only as the fallback for a script
  read from the cache with no live track.
  The sources cluster shows **three** marks, overlapped, in the player's
  corner, an empty answer never clears a strip already showing publishers, and
  it is published to the same live track: on the retrieval path the evidence
  packet exists *before the first sentence*, so keeping it only in the cache
  meant a panel that could not appear until the episode had finished.
- **Every way an episode is researched records who it read.** *(§107.)*
  Provenance is built from the Exa packet, the GDELT packet, live facts and
  attachments - which since §135 is every way there is. §107's
  `provenance.from_web_search` read the model's own search results off the
  final message; it went with the search itself.
- **Nothing on the player generates an episode except a button.** *(§104.)*
  `.mini-stage` is `flex:1`, so it is most of the player, and it carried a tap
  handler that jumped to the next episode in the album or - with no album -
  generated a **random** myFAM topic: an episode nobody asked for, costing a
  model call and a GPU, in place of whatever was playing. Removed with nothing
  in its place; the swipe-up gesture still moves through an album and is the
  one the `next-hint` label actually advertises.
- **An article index is the wrong instrument for a scoreboard, and the seam is
  now built out.** *(§82, §89, `live_facts.py`, `live_sources.py`,
  `LIVE_FACTS.md`.)* A game ends and the scoreboard knows instantly; the recap
  saying so is written, published and indexed later, so in between a search
  returns the *preview*. Same shape, shorter fuse, for a price; longer, for a
  vote count.
  **The rule the whole subsystem keeps, and the one that outranks every other
  line in this file: FAM must never confidently invent a current or recent
  fact it has no authoritative, sufficiently fresh evidence for.** With the
  corollary almost every mechanism there is a form of — **never infer a
  current-world fact from the absence of current-world evidence.** Our not
  having a scores provider is not the game having no score; a search that
  missed next week's fixture is not the fixture being unannounced; a provider
  that timed out is not an event that did not happen.
  Four things follow and are load-bearing. **Status is a closed vocabulary**
  (`scheduled`/`in_progress`/`final`/`unknown`) mapped at the provider
  boundary, because everything downstream switches on it and a free string
  misses every comparison silently — and `unknown` is not "probably fine", it
  is the state in which no result may be spoken. **Only evidence sets it**: EI
  may say the *request* wants a result (`outcome_dependent`) and may never say
  the *event* is under way or finished, which is why `BRIEF_SCHEMA` has no
  status field and a test says it never will. **Resolution comes from the
  provider's own catalogue, never from a model** — a hallucinated game id or
  ticker does not fail, it returns somebody else's state, fresh and
  authoritative and wrong, which nothing downstream can catch. And **a lookup
  reports which of seven things happened**, because "no provider", "provider
  broke" and "no such game" are three different sentences and
  `Optional[LiveFacts]` made them one.
  **Freshness is a check, not a label**: per-domain maximum ages, enforced in
  code before the writer sees anything, and data past it is *withheld* rather
  than annotated — a stale score is worse than none, since it arrives with a
  timestamp and outranks the packet. `delayed` is separate and is about design
  rather than age; a delayed feed says delayed and never "current".
  **Still nothing real is configured**, and that is deliberately not the same
  as nothing being here: three domains are declared, each reports exactly what
  it would need, `/api/health` distinguishes *operational* from *configured but
  unavailable* from *not configured*, and `python tools/verify_live.py` makes a
  real request rather than confirming a credential exists. Do not say FAM
  supports live scores until a real provider is returning them.
- **The evergreen bank is for a listener with no account, and the crowd row
  claims only what it can back.** *(§125, `topics.browse_inventory`,
  `MYFAM.md`.)* Two rules from one instruction, both reversing something this
  file had recorded as deliberate.
  **Amended again by §134, at the owner's direction: every rail shows
  exactly four, and only the two that *choose* are topped up** - Made for
  you and What you missed. "What FAM can't stop listening to" and the friends
  rail never invent a tile (fewer than four is honest; four once they can),
  and **Trending holds live stories only** - ranked by popularity and the
  listener's country and nothing else of theirs (`topics.rank_world`),
  chosen before any personal rail, and never the bank or the startup set,
  which the owner calls dummy data. A deployment with no live source has an
  empty Trending row that says why; `render.yaml` turns GDELT on for that
  reason. "Different picks" is gone - View more holds the rest.
  **And Trending is the stories now, by place** *(§135, at the owner's
  direction).* GDELT used to hand the row fifteen fixed *themes* - "inflation",
  "sport" - so it read the same every day. The sweep now reads a few hundred
  recent headlines worldwide and from each region's own press
  (`gdelt.discover`), groups the ones that share their names and words into
  stories (`news_clusters`, no model), and counts the **distinct outlets**
  running each: that count is the popularity the row ranks on
  (`topics.trending_score`). `stories.corroborate` folds a matching news story
  into a game, a price or a market, so the ranking is across everything FAM
  reads rather than each feed alone. Every story knows **where it is
  trending** (`geography.scope_for` over its publishers' countries: worldwide,
  a region, or a country); the rail keeps up to two of its four places for
  the listener's own part of the world (`WORLD_LOCAL_SLOTS`) and each card
  says the place, and View more is the whole of it grouped Worldwide, then
  theirs, then everywhere else (`topics.trending_groups`). The pool refreshes
  itself every fifteen minutes with nobody looking
  (`STORIES_BACKGROUND_SECONDS`). Nothing here has made a real request from
  the build container.
  **Amended by §127, at the owner's direction: every drawn rail except the
  friends one now has a floor** (`topics.RAIL_MINIMUM` - six for Made for you
  and Trending, four for the other two), topped up *after* each rail has
  chosen from `_rail_fallback`, never by weakening a ranking. What follows is
  still how each rail *chooses*; the floor is what fills the gap under it.
  **"What FAM can't stop listening to" fills from plays and from nothing
  else.** It used to top itself up from the bank so it was never empty, which
  is a true statement about the *content* and beside the point: the heading is
  a claim about this deployment's listeners, and twenty-eight tiles nobody has
  played do not support it. §89's rule, on the most checkable fact on the
  page. An unplayed row is empty and says so; `tools/seed_demo.py` fills it
  for a demo, the same bargain Explore already makes.
  **And `browse_inventory` is the one definition of what a rail may offer**:
  no account gets the live pool plus the bank, an account gets the live pool
  plus the startup set. The bank is a first impression for somebody FAM knows
  nothing about and can keep nothing for - downloaded the app, has not signed
  up - and twenty-eight standing explainers are the right answer to "show me
  what this is" and the wrong answer to "what should I hear today".
  **It is a swap and never a subtraction**, and that is the load-bearing
  half: removing the bank alone would empty Made for you on any deployment
  with no live provider - every deployment today - and `WORLD_FLOOR` reserves
  its four tiles on the stated premise that rail has somewhere else to go. It
  is replaced with the fresher inventory, which is also the whole answer to
  "make it up to date": a startup query asks what changed recently and
  `SEARCH_MODE=always` researches it on the tap, so the tile is current
  because it was *retrieved* rather than because somebody edited a string.
  Rewriting the bank to be about today was the other reading and is the wrong
  one - evergreen is the property that lets twenty-eight tiles serve everybody
  from one shared script.
  This does **not** make the startup set warm inventory for a guest: a guest
  who has played something keeps the bank, and the set still leads only for
  somebody who has said and done nothing, which is what `startup.py` says it
  is for.
  **Two exemptions, and they are decisions**: the DailyFAM mix picker
  (`rank_bank`) and Explore New (`rank_might_like`) keep the whole bank,
  because the rule is about what FAM offers *unprompted* and both are places
  a listener went looking. Gating the picker would also empty it for exactly
  the listeners who can save a mix, which is §123 one screen over.
  Every surface reads the one function - `build_feed`, `build_section` and
  `rank_next_up`, the last on both passes, because the popup starts its first
  tile by itself and is the last place that should have a back door. The flag
  comes off `request.state.listener.is_authenticated` via `app._has_account`
  and never from a parameter. It defaults to **False**, the generous answer,
  because a caller that was never taught about accounts does not know - and
  defaulting the other way would silently empty a surface, which is the
  failure that looks like a design decision rather than a bug (§119).
  **And the gate is on what FAM *offers*, never on what it *reports*.** The
  two crowd rows are measurements over the play log, so an account holder
  legitimately sees a bank tile in "What FAM can't stop listening to" when
  listeners really played it - hiding the most-played episode in the app
  because of who is looking would be this change's own over-claim in
  reverse. The rails the rule governs are the ones that choose for you.
  Checking that also turned up a rail nobody is looking at starving that
  row: `might_like` is `UNSHELVED` and fills before `most_played`,
  reserving tiles so the drawn rails do not show what Explore New would -
  right for a rail that chooses, wrong for one that reports. It was
  invisible while the bank filler existed and data-dependent once it did
  not, so `most_played` now ignores that reservation while still avoiding
  every drawn rail.
  Nobody has heard one of these episodes; the freshness claim is about the
  mechanism, not yet about the writing.

- **A browse tile is a title and an angle; the script waits for the tap.**
  *(§102, `stories.py`, `MYFAM.md`.)* myFAM's inventory is now the evergreen
  bank **plus** a live story pool built from four sources, and the pool is the
  cheapest thing in the product per tile offered: one background sweep and
  **one small model call per refresh window, for every listener**, composing
  only what is new since the last one. Forty tiles cost one call, not forty.
  Four rules hold it up. **A signal is a measurement and never a result** -
  coverage volume, a traded price, a betting line, a fixture status - because
  a tile is written before anything is researched, so a result on one is §88
  with a larger audience. **One exception, at the owner's direction (§135)**:
  a game's score reaches the card as `live_line`, written in code from
  API-Sports' scoreboard on every sweep and drawn *beside* the title, never
  composed into it - a score re-read every fourteen minutes that says how old
  it is, not one frozen into a sentence. **How hard and how long to push** is written down
  (`DOMAIN_WEIGHT`, `DOMAIN_SHELF_LIFE`, a cooldown after expiry) rather than
  left implicit in a sort order, and `first_seen` is the clock, so a story
  that keeps being reported does not get to be new again. **Variety is capped
  twice** - in the pool and in each rail - and the cap is a cap on what is
  available, never a quota on what is not: a listener with one interest still
  gets a full rail. And **nothing on the page-load path awaits anything**, so
  there is no route from opening myFAM to generating anything; a test reads
  `build_feed`'s own source to keep it that way.
  One of those four has a general form worth carrying past this feature
  (§103): **an eviction that forgets is a creation.** The variety cap used to
  discard what it passed over, so the next sweep admitted the same subject as
  brand new, reset its clock, and paid for it again - a story that could never
  age, expire or cool off. The pool now keeps more than it offers and
  `_FIRST_SEEN` holds the clock independently of membership. Anything here
  with a lifecycle needs its clock kept somewhere that outlives its membership
  of the thing that shows it.
  What that costs, stated: a wrong guess about what is worth offering costs
  one composed tile nobody taps, which is a fraction of a cent. What it must
  never cost is availability - no key, a timeout, a refusal and a broken
  provider all fall back to a templated tile and say so, which is the same
  rule episode intelligence lives by.

- **A browse card's one line is about the episode, and a new listener's page
  is not four explanations.** *(§116, `startup.py`.)* Two halves of one
  mistake - a surface saying something true about *itself* where something
  useful about the *episode* belongs.
  The card said why the rail had chosen it ("Because of what you have
  played", "Playing across FAM now"). Accurate, and no help: somebody
  scrolling is deciding whether an episode is worth three minutes. The line
  is the episode's hook now - `angle`, then `subtitle` - and the bank's
  twenty-eight subtitles are rewritten from descriptions into hooks. The
  rail's reason survives only for a tile carrying neither, which nothing
  produces. **The rail and its "View more" screen draw it through one
  function**, because they were two and one tile had two different second
  lines depending on which drew it. It is clamped to two lines and the
  strings are budgeted in Python (`startup.MAX_HOOK`) rather than measured in
  a browser - a clamp is the one failure that hides itself, and §115 is what
  measuring text in this container buys.
  And a listener with no account and no chosen interests got **one row of
  content and four empty-state sentences**, because every rail is a query
  over an event log and there was nothing to rank. Not a bug in the ranker -
  a rail claiming relevance *should* be short rather than padded - so the fix
  is a third inventory rather than a weaker floor. `STARTUP_TOPICS` is **one
  time-anchored question per facet**, and the time-anchoring is the whole
  feature: `SEARCH_MODE=always` means the episode is researched on the tap,
  so the tile costs nothing at page load, needs **no live provider
  configured** (the story pool is off until `GDELT=1`, which no deployment
  has), and `research.NoEvidence` refuses rather than writing a stale one
  from memory. One per facet because the eight are the whole pickable
  vocabulary and a cold start knows nothing about which one this listener
  wants; which *leads* comes from `popular_facets`, like the first-run
  picker.
  Four rules hold it up. **A prior is not a taste**, so the heading is
  **Start here** rather than "Made for you" - the tiles are worth offering
  and the ordering is real, but it is what other people play; `taste_source`
  carries it and `build_section` carries it too, or "View more" would open on
  the empty list the rail exists to avoid. **Only that rail gets it** - the
  friends rail and "what went past you" are statements about a graph and an
  impression log that a prior cannot supply, so they stay empty and keep
  their sentences. **`cold` is derived, never stored** (`not profile`): a
  "they skipped the intro" flag cannot say whether behaviour has since
  arrived, and as it is, one play retires the whole thing - the algorithm
  *before* the algorithm, not a second one beside it. And **plays of the set
  never feed `popular_facets`**, or the prior would spend the deployment
  confirming its own opening guess, which is the feedback loop the impression
  rule already forbids arriving through a different door.
  A tile claims nothing about the world, because it is written before
  anything is retrieved - §88 and §102 one layer earlier, and a test bans
  results, outcomes and digits from the titles and hooks. **Nobody has heard
  one of these episodes**; there is no key here, and quality is the whole
  point of this set.

- **The weekly recap is a rail, not a popup.** *(`topics.rank_missed`,
  `MYFAM.md`.)* It was one episode *about* somebody's week, fired on the first
  open on or after Sunday - so a thin week produced an episode about having had
  a thin week, in front of somebody who opened the app to listen to something
  else. **What you missed last week** is a shelf of episodes they can still
  have: what FAM put in front of them in the last seven days and they did not
  take.
  **What counts as "missed" is wider than what FAM showed you** *(§114, at
  the owner's direction, reversing the line below it).* It used to be the
  impression log and nothing else, and that made the rail a report on our own
  delivery: a listener who did not open myFAM last week missed nothing, by
  construction, however much happened. Membership is now three things, any of
  which qualifies - **offered to them**, **played by other listeners this
  week**, or **in the live story pool**. All three genuinely went past this
  listener in the last seven days, which is what keeps the heading true.
  **The standing bank is still not a source**: an evergreen explainer nobody
  was offered and nobody played did not happen last week, and putting one
  here to make the row look full is the padding this rail was built against.
  *(§127, at the owner's direction: that is still what the rail **chooses**,
  and the floor then tops it up to four from the rest of the inventory -
  which is exactly that padding, asked for knowingly.)*
  **And relevance is a floor, not just a sort.** "Only the ABSOLUTE MOST
  RELEVANT" is the whole of the instruction, so a tile this listener has no
  affinity for is not offered at all - short beats padded. With **no profile
  at all** it falls back to what it always was, offered-newest-first:
  impressions never reach `taste`, so every score is zero, and a floor over
  nothing would empty the rail for exactly the listener it is most use to.
  Two rules survive the widening unchanged, and the fill order does not.
  **An impression still never becomes taste**: it decides *membership*, which
  is a fact about the feed, and `_affinity` decides the *order*. And **it can
  only offer what it can still resolve** - the bank plus what the pool still
  holds - because a tile invented to stand in for an expired story is §102
  with a heading on it. What moved is that **trending is the rail's last
  source rather than its first**: `missed` still fills before the crowd rows,
  with the live pool excluded, and is topped up from the leftovers afterwards,
  because a brand-new story nobody has been shown is one *Made for you* exists
  to offer and a tile already on the page is not one anybody missed.
  The empty sentence claims none of the three nothings: somebody who was not
  here last week was offered nothing, somebody who played everything missed
  nothing, somebody for whom nothing was relevant is shown nothing, and the
  rail cannot tell them apart.
  What went with the popup: `/api/recap`, `topics.weekly_recap`, the
  scheduling in `preferences.py`, the Settings row that switched it off, and
  the two tiles above Your FAM's threads. The `weekly_recap` and `recap_week`
  *columns* stay, read by nothing, on the same reasoning as the language field
  - they are what a scheduled digest would read on the day there is one.
- **Trending keeps four tiles, and takes them before anything else chooses.**
  *(§114, `topics.WORLD_FLOOR`.)* It was filled **last**, from what the four
  personal rails had not claimed - and Made for you draws on the same live
  pool, so on a day the pool held five stories and a listener's taste matched
  four of them the world row got one. The floor is reserved before the fill
  loop runs. The trade, stated rather than buried: on a thin pool Made for you
  loses its best live tile. That is the right way round **only** because Made
  for you draws on both inventories and can never be empty - the bank is
  twenty-eight topics - while Trending draws on the live pool alone and has
  nowhere else to go. And the reservation happens only when it *buys* the
  floor: a pool of one cannot fill the row however it is shared out, so
  holding that story back would cost the personal rail its tile and still
  leave Trending short.
- **Two rows on myFAM, two questions, and they are not blended.** *(§90,
  `trending.py`, `TRENDING.md`.)* **"What FAM can't stop listening to"** is this
  app's own play counts over its own bank — it already existed under the key
  `trending`, which is why that key is now `most_played`: it was always FAM's
  popularity, never the world's. **"Trending"** is what the world is paying
  attention to, from an outside feed. They can disagree, and a listener reads
  them differently, so blending them into one ranking would lose both signals.
  **It is a different subsystem from live facts on purpose.** `live_facts`
  resolves an entity and fetches its state, in seconds, with a status
  vocabulary; trending has no entity and no status and moves in minutes. One
  changes what an episode *says*, the other what is *offered*. They compose
  without coupling: a trending tile about a game becomes an ordinary question
  when tapped, and `live_facts` answers it. **Trending feeds the bank; live
  facts feed the evidence.**
  The cost design is why it is worth having, and it is the inverse of live
  facts': **one fetch serves every listener**, so this is the cheapest place in
  FAM to add live data rather than the most expensive. That dictates the rest —
  the cache is global rather than per listener, `build_feed` reads it
  *synchronously* so the ranker stays a pure function of the log plus the cache
  (one that fetched could not be tested offline), and `/api/myfam` *schedules* a
  refresh rather than awaiting one, because the browse surfaces are where the
  wait must be zero.
  Three rules on what a source may return. A tile carries **a question worth an
  episode, never a headline** — a headline query produces an episode that
  restates the headline, and a score belongs to `live_facts` anyway. `why_now`
  is **a subtitle and never evidence**: tapping a tile researches the question
  from scratch, which is what stops a stale blurb becoming a stale episode, and
  a test asserts `build_prompt` never mentions trending. And an item's id is
  hashed from the **subject** rather than the query, so a rephrasing between
  refreshes does not mint a new tile that fatigue can never damp.
  **An empty row is a fact about this deployment and never a claim about the
  world** — the browse-surface form of §89, with a different sentence for each
  of the four ways it can come up empty, and a test that none of them says
  "nothing is trending". Nothing real is connected yet.
- **How long a script keeps comes from what it was built on, never from the
  words of the question.** *(§89, `cache.ttl_for`.)* It used to be a keyword
  match, and `"Chiefs game"` — the reported case — contained no volatile word,
  so an episode about a game in progress was cached for **twenty-four hours**
  and, because `recent()` is the Explore feed, *published* as a finished one.
  **Do not fix this class of bug by adding keywords.** §76 settled that: a
  keyword list can always be widened by one more word, and "Chiefs", "game"
  and "score" would each have missed "how is the match going". The precedence
  is live status, then `outcome_dependent`, then the evidence window, then the
  keyword list as the floor for paths that have none of those — and `0` means
  do not cache, which is what `in_progress` returns, because no TTL is short
  enough for a score. Ordinary static content is untouched.
  The plumbing is the part that is not obvious: the pipeline holds the
  **unprepared** plan at the write site, so the volatility facts come home on
  `ScriptNotes` alongside `thread` and `research`.
  **Prefetch obeys two extra rules**: it never calls `live_lookup` at all (a
  warmed live fact is stale by definition, bought at full price), and it never
  warms a *script* for an outcome-dependent question — the brief is kept,
  because that is a claim about what is being asked and it keeps.
- **Duration buys depth, not words.** *(§82, and this sharpens "duration is a
  ceiling".)* `DEPTH_BANDS` says what each band of minutes is *for* —
  orientation, understanding, depth, the full arc — described as content and
  never as a word count, and the band reaches the prompt. A story shape per
  episode type reaches it too, and **as a shape, never as boxes**: the prompt
  before the rewrite imposed the same five beats on every topic, so a golf
  recap had to invent something for "the main debate or open question".
  `build_structure_note` names the shape and, in the same breath, says a beat
  with nothing real behind it is dropped rather than filled. A test pins that
  wording, because losing it turns a structure back into a template.
- **Prefetch writes into the same cache, under the same key, as a live
  episode.** *(PROBLEMS.md §83.)* That is the whole design: nothing on the tap
  path changes, and a tap on a warmed tile is an ordinary cache hit. Which
  makes the key the ball game - two implementations agree today and drift the
  first time one gains a field, and the failure is silent and total (every
  speculative script paid for and never read, with the feed looking exactly as
  it did). So `pipeline.key_for` and `bucket_for` are module-level, the
  pipeline delegates to them, and a test reads the pipeline's own source to
  keep it that way. **If you add a field that changes what an episode is, it
  goes in there and nowhere else.**
  **Two warm levels**, which is the honest shape of "how much to prefetch":
  `brief` runs contextual relevance only and is the default - it removes the
  seconds EI costs for one small call - and `script` writes the whole episode,
  so the tap pays nothing and a wrong guess costs a full one. A warmed brief
  expires, because a brief is a claim about *now* and a stale one would make
  the episode confidently about the wrong day; a degraded brief is never kept
  at all, since that would be a tap silently skipping EI after paying for it.
  Four things it may never do: **compete with a live listener** (the serving
  path marks itself, prefetch stands aside, one warm at a time), **spend past
  its ceiling** (episodes, briefs *and* dollars - a 10-minute researched
  episode costs several times a 1-minute one and a brief a fraction of
  either, so one count for all three binds on the wrong thing: §105 found 50
  *briefs* being a whole day's warming with the dollar budget untouched), **warm anything personal** (the same
  `is_shareable` rule a live episode obeys), and **pretend it is paying** -
  warmed and taken are counted separately per source, recorded from the
  serving path with the key that actually hit, and reported as `None` rather
  than `0` when there is no data, because zero out of zero reads as failure and
  is actually silence. **Briefs are counted beside scripts** (§105), or the
  shipped level would report "no data yet" forever - counted on every tap that
  uses one rather than once per key, because the store is in-process, and never
  counted when the brief was degraded and therefore dropped.
  **It ships on at `brief`, and myFAM is what schedules it** *(§105, reversing
  §83's "it ships off").* `/api/myfam` calls `prefetch.schedule_cycle` when
  the page is drawn and never awaits it, the same shape as the story sweep
  beside it - for as long as this module existed nothing called `run_once` at
  all, so every source, budget and ledger in it was inert. Three things that
  scheduling had to bring with it, each of which would otherwise have made the
  warming worthless or expensive: a **per-listener cooldown**
  (`PREFETCH_CYCLE_SECONDS`), because a browse page is drawn far more often
  than it is acted on; the **length the surface is showing**, passed down to
  `Prefetcher.plan`, because minutes are in both keys and myFAM has had a
  length control of its own since §95; and a **skip for a brief already held**,
  because a brief keeps for an hour and a cycle can come round every five
  minutes. Warming whole scripts stays opt-in (`PREFETCH_LEVEL=script`) and
  still wants the hit rate first. `python tools/prefetch_report.py` shows what
  a deployment would warm without spending anything; `--live` reads the hit
  rate off a server.
- **A candidate says why it is a candidate.** *(§83, `prefetch_sources.py`.)*
  Every guess carries a reason in words - "#2 in trending, the same tile for
  everyone", "in their 'At the gym' mix (typed, so shared with nobody)" - and
  it survives to the report, because the only way to judge a prefetcher is to
  see which *kinds* of guess get taken. The source ordering is a **cost design,
  not a ranking one**: trending and the live story pool are identical for
  everybody so one warm serves every tap, a bank mix member is shared where a
  typed one is a script a day for one person, and a feed rail is the most
  personal and least shareable. **The story pool is the one with most to gain**
  (§105): a tile there is a title and an angle, so the subject is still
  unresolved at the tap, which is exactly the call a warmed brief has already
  made.
  Sources are **forbidden to call a model or the network** - one that costs
  money to *ask* turns a speculative saving into a certain spend - and a test
  reads the module rather than trusting the rule.
- **An account gates what is kept, never what is heard.** *(PROBLEMS.md §70.)*
  Saved mixes, chosen interests and language, and Save for Later need an
  account; search, myFAM, DailyFAM's episodes, Explore, Go Deeper and the whole
  audio path do not. **The interaction log is inside the gate now** *(§127,
  at the owner's direction, reversing what this line used to say)*: everything
  the algorithm learns belongs to an account, a guest session is a device and
  not a person, so `app._remembers` keeps every event, play and impression of
  a guest out of the log, a guest's interests live in page memory only, and
  resume positions are a server table keyed on the account rather than
  `localStorage`. The cost, accepted: a guest's feed is the startup set every
  time.
  `ACCOUNT_REQUIRED` in `app.py` holds the reasoning beside the code that
  enforces it. Nothing is lost by signing up late: a mix made before the gate
  is still under the same id and appears the moment credentials are attached.
  **The app now *opens* on sign-up, and that is a trade rather than a
  contradiction** *(§107, at the owner's direction).* myFAM was the screen
  marked `active` in the markup, so it was drawn from the moment the page
  parsed - and who the listener is is not known until `/api/auth/me` answers,
  so somebody without an account saw it flash and be replaced. There is no
  default screen now; the splash is held until the answer arrives, and
  `bootToFirstScreen` sends a signed-in listener to myFAM and everybody else
  to the front door.
  What keeps the constraint above true is **the door**: "Continue as guest" is
  on that screen, it is one tap, and everything behind it still works with
  nothing signed in. What changed is which side of it the app opens on - not
  what an account is required for, which is unchanged. If it starts costing
  listeners, it is one branch in `bootToFirstScreen` and this is the line to
  read first.
  **The door is named for what it does** *(§114).* It said "Skip for now",
  which is still right on the two setup steps behind it - a step you skip
  comes back - and wrong here: this is a way of using the app, not a
  postponement, and somebody who takes it is a guest for as long as they like.
  **And the Profile tab is the one screen a guest does not get** *(§114, at
  the owner's direction).* It used to draw the whole page - name, counts,
  shelves, vibes - with a note at the bottom offering an account. Everything
  on it was true, which is why it lasted, and it is still the wrong screen: a
  profile is the one page that is *about* having an account, so drawing a
  full one for a guest invites them to furnish a room the app is about to say
  is not theirs. It is a door now, and it makes no request for a profile it
  is not going to draw. Nothing else moved behind the gate - everything in
  the paragraph above still works with nothing signed in.
  **And every gate in the app opens the same sign-up screen.** DailyFAM,
  messages, Settings and the profile all used to open `createAccount()` /
  `signIn()`, two chained modals asking for an address and then a password -
  a form that **cannot offer a phone number, Google or Apple**, all of which
  the real screen has. So a listener who reached an account from a gate and
  one who reached it from the front door were shown two different products.
  `gateActions()` is the one pair of buttons, `openAuth` already knew how to
  return to the screen it was opened from, and the makeshift pair is
  **deleted rather than left unused** - the Piper reasoning for the third
  time: a second sign-up form left standing is one somebody wires a new gate
  to by accident.
- **The tier system is built, and switched off.** *(PROBLEMS.md §81.)*
  `ENFORCE_QUOTAS=0` is the default: every tier, limit, counter, reservation,
  refund and refusal exists and is tested, and none of them refuses anybody.
  The reason is not that limits are wrong, it is that **nothing sells a
  listener a way past one** - there is no checkout, so an enforced free tier
  is a wall with no door. One setting turns the whole of it on the day that
  changes, and `/api/health` reports which state a deploy is in, because an
  unenforced tier system looks exactly like an enforced one until somebody
  reaches a limit. The exposure this accepts, so it is a known trade: spend
  per listener is **visible** (`metering.py` records every episode) and not
  **capped**. Generation is still paced per listener.
  Note the counters do not run while it is off, so `/api/entitlements` reads
  0 used - the numbers for setting real limits come from `usage_report.py`,
  which is where CLAUDE.md always said they should come from.
- **A refusal names what the listener was doing, not what the ledger calls
  it.** A resource is an accounting word: somebody told they are out of
  "episodes" goes looking for the episodes they apparently spent, where "your
  daily limit for searches" is about the thing they pressed.
  `entitlements.service_label` maps the surface the request already carries
  to that word, the verdict carries it, and the whole sentence is composed
  server-side so the web app and the iOS client cannot word it differently.
  The verdict travels in the response **body** as well as `X-FAM-Quota`,
  because a header is the one part of a response a client routinely cannot
  reach. The refusal raises a screen that says the limit, how much of it is
  gone, when it comes back **in the reader's own clock**, and opens the plans
  - a limit with no way past it is a dead end on a phone.
- **A tier is what you may spend, never what you may reach.** *(ACCOUNTS.md.)*
  Three tiers - `free`, `plus`, `unlimited` - and the free one is a **daily
  ceiling on episodes**, not a smaller product: `entitlements.FEATURES` gates
  capabilities and today every tier has every one of them. The mechanism ships
  on and the policy ships empty, because taking away something every listener
  has always had is the "quietly worse than intended" failure again, with the
  twist that here they notice and are right. Moving a feature behind a tier is
  one line plus the test that fails when you do, which exists to make it a
  decision somebody wrote down.
  Two things counted separately, because they cost differently: an **episode**
  may write a script, an **Explore replay** provably cannot. A cache hit is
  still an episode - the listener heard one and the GPU made it. And the free
  quota is **a budget shaped like a limit, not a security control**: an
  anonymous session can be thrown away and a fresh allowance started, which is
  the price of not putting a login in front of the first word.
- **Save for later is a pointer, and pressing save is the whole of it.**
  *(SHARING.md.)* Question, length, title - one row - and playing one needs
  the network like any other episode. It is a **toggle**: the icon turns
  green, pressing it again takes the episode off the shelf. Same shape as
  VIBE! and drawn the same way, by a `data-save` sweep rather than a list of
  ids, which is the mistake that once left the main player with no vibe
  button at all.
  **Download is gone**, and gone rather than switched off: the endpoints, the
  store methods, the tier field, the offline IndexedDB layer, the second view
  of the shelf and the Downloads tile. What it cost was the thing the shelf is
  for - pressing save raised a question instead of saving. Its three columns
  stay in the schema, written by nothing, because dropping a column is a
  migration with no benefit.
  The folder chips came off the shelf in an earlier change (PROBLEMS.md §96)
  and have not come back: nobody had ever made a folder, so every listener was
  shown a fixture named "Commute" as though it were theirs, and **a control
  with nothing behind it is worse than no control**. `saved.py`'s filing is
  untouched, so putting folders back costs nothing anybody filed.
- **A shared link lands on one episode, and play is the only thing that
  works.** *(§106, `static/listen.html`, SHARING.md.)* `/s/<id>` used to
  redirect into the web app, so somebody sent one episode arrived at a search
  box, myFAM, Explore and a sign-up with their episode reduced to a query
  string. It now serves a page whose every other control - the wordmark, "Ask
  your own question", "Browse episodes", Get FAM - is a `data-door` to the App
  Store, routed by one delegated listener so a control added later is a door
  by default.
  Three things hold it up. **It traces back with no new concept**: an episode
  is identified by its cache key, `key_for` builds that key from the question
  and the length, and a share row holds exactly those - so the page asking
  `/api/audio` for them gets the sharer's own script out of the shared cache,
  and a test asserts the two keys are equal so a new `key_for` field fails
  loudly rather than silently costing a model call per open. **The head is
  rendered server-side**, because Facebook and LinkedIn read the page for
  their preview and run no JavaScript - until this existed every FAM link
  posted anywhere previewed identically - and `og:image` is claimed only when
  the card URL is absolute. And **the open count is reported by the page**,
  never by the serve, because those same crawlers fetch the link and opens are
  the only number sharing produces; the alternative is a user-agent list,
  which is the shape §76 settled against.
  `APP_STORE_URL` unset draws **no** non-listening control at all - not one
  that 404s, not one rerouted into the app - and `/api/health` says which
  state a deploy is in. The payload carries no `user_id`: this is the one
  response in the app handed to people who are not listeners.
  **The link names a host now, and nobody has to set one** *(§114).* It read
  `PUBLIC_BASE_URL` and nothing anywhere prompts for it, so no deployment had
  it, so every share link was `/s/abc123` - a correct relative URL and a
  useless thing to send somebody. `app._public_base` reads the request
  instead, which is `/api/health`'s own rule: a request arrived, so this
  server has an address somebody outside it reached, and that address is in
  the request. `X-Forwarded-Proto` first, because behind Render's router the
  connection is plain HTTP and a link built from `request.url` would be
  `http://` on an HTTPS site. `PUBLIC_BASE_URL` still wins when set - it is
  the only way to name a host this server is *not* reached at - and a
  **loopback host is refused outright**: a link to `localhost` looks like a
  URL, so it gets posted, and it resolves on the recipient's machine to
  whatever they are running. `/api/health` reports `link_host` as `env`,
  `request` or `none`.
  **A story card is a file handed to the share sheet, never a tab.** *(§114.)*
  Instagram and Snapchat took `window.open(card)`, which is a blocked popup
  on every mobile browser, and when it did open it was an **SVG document in a
  tab** - neither platform accepts SVG, there is no "add to story" on a tab,
  and the wording was left behind in the page they came from. The card is
  fetched, rasterised to PNG in the page and passed to `navigator.share` as a
  file. In the page rather than on the server, because `sharing.story_card`
  is SVG precisely so it needs no image library and a browser already has
  one; through a `data:` URL rather than a blob URL, because Safari treats an
  SVG from a blob URL as cross-origin and taints the canvas, which is the one
  browser this feature is mostly used from.
- **FAM posts nothing to anybody's social account, and holds no token.**
  *(SHARING.md.)* Every external destination is reached from the phone: the
  share sheet, or a platform SDK hand-off where their app does the posting with
  the person watching. The server produces the link, the wording and - for
  Instagram and Snapchat, which cannot carry a link as text - the story card.
  This is the correct shape rather than a stage: no OAuth to maintain, no
  tokens to leak, and nothing that can post while somebody is asleep.
- **Authorship is provenance, and never identity.** *(PROBLEMS.md §95.)* The
  shared cache records who first generated each script, so Explore can leave a
  listener's own episodes off their own feed - and that is the *only* thing it
  is for. It is stamped by `PodcastPipeline.author`, set per request from
  `_listener(request)`, and deliberately **not** a field on `EpisodePlan`: the
  plan is what an episode *is*, `pipeline.key_for` is built from it, and a
  listener id one field away from the key is one refactor away from being in
  it. At that point every listener has their own cache, the shared-cost design
  the whole app rests on is gone, and nothing fails - so a test reads
  `key_for`'s own body and fails if the word appears there.
  Two more rules keep it honest. **The first writer keeps it**: a re-write that
  extends a TTL must not hand authorship to whoever triggered it, so the
  upsert only fills an empty one. And **prefetch writes none at all** - a
  warmed script was nobody's tap, so it belongs to everybody, which is also
  what rows written before the column existed do.
- **A speed change must not change the voice.** *(§95, `fam-audio.js`.)*
  `playbackRate` on a buffer source resamples, so 1.5x came back a fifth
  higher - the wrong trade on a voice this project spent a year choosing. The
  samples are time-stretched instead (WSOLA), and the buffer handed to Web
  Audio is already the right length, so the node plays at 1.
  The accounting above it is untouched, and that is the point: WSOLA advances
  its read pointer by exactly `rate * sampleRate` per second of output, so
  `positionSamples`, seek, the scrub bar and `TAIL_MARGIN` all keep working
  without knowing it exists. **At exactly 1x it is bypassed**, so the default
  costs nothing, and `setPitchLock(false)` restores the old behaviour for a
  device that cannot keep up with it.
  The default speed is **1x**, not 1.2x. A speed chosen on somebody's behalf,
  for a voice they have not heard yet, is a decision the product should not be
  making; once they change it, it follows them to the next episode.
- **A control with nothing behind it is worse than no control.** Two were
  deleted rather than repaired in §95 - a share sheet's Audio/Transcript
  toggle that only changed a word in a toast, and a "search messages" bar that
  toasted "demo only". The same rule took the three invented contacts out of
  `index.html`: a social surface that fabricates people is a profile
  fabricating numbers with a worse failure mode, because it says a message was
  sent when none was.
- **The intro screen is not on the navigation stack, and `goBack()` cannot
  reach it.** *(§96, §97, §98 - the same trap three times.)* It is drawn with
  `showScreen("intro")` by `afterAccount`, `restartFirstRun` and both settings
  entry points, and never pushed. So anything opened *on top* of it - the
  catalogue, a shelf - pops to whatever was underneath, which is SearchFAM,
  and the first run silently loses its remaining steps. Every one of those
  returns by *name* now, recorded when the screen was opened. If a fourth
  screen is ever shown that way, this is the line to read before wiring its
  back button.
- **A listener id is never accepted from the client.** It arrives from an
  HttpOnly session cookie the server minted, and `?user=` is ignored wherever
  it still appears. This replaced `famUserId()`, which made an id up with
  `Math.random()` and put it in every query string - so anyone who read or
  guessed one could take over that listener. Anonymous listeners still get a
  full identity, because requiring a login to hear an episode would break the
  one-sentence spec. If you add an endpoint that touches per-listener data,
  take the id from `_listener(request)` and never from a parameter.
  **Two carriers now, one rule.** A native client cannot rely on a cookie jar
  iOS clears without asking, so the same server-minted token is also accepted
  as `Authorization: Bearer` and stored in the Keychain. Nothing about the
  trust changes - a bearer token is the same unforgeable, revocable string the
  cookie holds - and a browser must never ask for one, because reading it in
  script is what HttpOnly exists to prevent.
- **A limit counts episodes, not requests.** The free tier's daily allowance
  was spent five times over on one episode, because every tap of it reserved a
  unit - and tapping the episode that is playing is something the interface
  invites (PROBLEMS.md §79). A spend carries the episode's cache key, the first
  one in a window takes the unit and the repeats ride on it. The same rule
  applies to the pace: a request that provably cannot spend a model call - an
  Explore replay, or an episode whose script is already cached, which is what
  makes a voice switch free - is not paced as a generation. And because this
  server has two reasons to answer 429, every refusal says which one it was.
- **A wipe enumerates what is *derived* from the thing it empties, not only
  what is stored beside it.** *(§124.)* `tools/wipe_demo_data.py --all` takes
  the script cache, the event log, the seeded listeners and the live story
  pool - and left the grown vocabulary (`categories.py`) standing, which is
  minted *from* that event log and is a ranking input. So a deployment taken
  back to a blank slate went on ranking its feed on subjects learned from
  episodes nobody could play any more, and nothing said so, because a tree is
  not a row anybody counts. Two smaller versions came with it: the
  module-level handle `topics.category_tree` caches per process, so clearing
  the table without dropping it is a destructive operation that reports
  success and changes nothing until the next restart; and the engagement
  table is held in process, computed from the impressions being deleted one
  line above. `demo_data._forget_what_the_log_taught` is the one place that
  knows this, it never raises, and the dry run counts the vocabulary because
  it is the item on the list nobody expects. Stores are the easy half -
  `storage_doctor` lists them and §107's test derives that list. What
  survives a wipe is whatever is neither a store nor a row.
  What it still does not remove, because none of it is an episode: a mix
  holds topic ids, a saved item and a vibe hold a question - pointers, which
  play again from a freshly written script. Accounts, credentials and the
  metering ledger are untouched in both scopes.
  **And what it puts back** *(§126)*: the starter vocabulary, re-applied
  immediately after the clear. That is this rule read the other way round
  rather than an exception to it - what goes is what the *log* taught, and a
  seed node was never taught by anything. A wiped deployment is exactly the
  deployment a seed exists for, and the next boot would mint it back anyway,
  so the only thing leaving it out would change is which page saw the tree
  half-built.
- **Failures must be visible.** Silent success (empty audio, a placeholder tone,
  demo mode mistaken for live) has caused more lost time on this project than
  any real bug. Every fallback must announce itself. *(PROBLEMS.md §51: demo
  mode did announce itself, in an 8.5px chip, and still cost a whole session -
  and it was writing its canned script into the shared cache, so the failure
  outlived the run. Announcing is not enough if the thing keeps a record.)*
- **A running server says which code it is running.** *(PROBLEMS.md §77.)*
  `/api/health` reports `build` (the commit, from `RENDER_GIT_COMMIT`,
  `FAM_COMMIT` or `git rev-parse`, and `"unknown"` rather than a guess) and
  `search_mode_source` (env var or code default). Both exist because a session
  went into inferring them: "the fix is pushed" and "the fix is live" are the
  same sentence from outside, and an env var beats a code default silently and
  outlives every push. Anything else that can be set in two places belongs
  here too.
- **Verify, do not inspect.** *(PROBLEMS.md §52.)* Four consecutive failures on
  a real machine all had the same shape: a check answered a cheaper question
  than the one being asked and then reported OK. "A key is set" is not "the key
  works"; "a cache is configured" is not "this generator may write to it". The
  server now asks Claude at startup whether the credential is actually accepted
  and says so on every tab. Anything that reports readiness must perform the
  real action, not confirm that it was configured.
- **The address of the voice is discovered, never written down twice.**
  *(PROBLEMS.md §112, `voice_control.py`, `REMOTE_VOICE.md`.)* `REMOTE_VOICE_URL`
  was a fact about somebody else's infrastructure kept in this app's
  environment, so every pod RunPod moved cost a chain nobody could shorten: a
  404 mid-episode, four consoles to bisect, an environment edit and a redeploy.
  FAM now walks a **ladder** - the pinned URL, then workers that registered
  themselves, then pods found by **name** through RunPod's API, then the
  serverless endpoint - and `voice_control.ladder()` is its one definition,
  read by the runtime, `/api/health`, the startup log and
  `tools/voice_doctor.py`.
  Four things hold it up. **A rung is used because a real call to it came back
  correct** (§52 again), held for `VOICE_VERIFY_TTL` so the synth path pays
  nothing, and the check is the *cheap* real call - waking a serverless worker
  to keep a health page green is a bill for a colour. **Failing over is not
  falling back**: every rung is the same worker image, the same weights and the
  same reference recording, a candidate whose sample rate disagrees with the
  header already written is refused rather than used, and when nothing can
  speak the episode still fails with the reason attached. **Every switch is
  recorded** - §109's rule, in a second place. And **the worker says where it
  is**, because only it can: RunPod gives the pod its own id and derives the
  proxy URL from it, so a replaced pod is back in service within one heartbeat
  with nothing edited. That registration is authenticated or it does not exist
  (`VOICE_REGISTRY_TOKEN` unset means the endpoint is absent), and it is a
  *claim* rather than a promotion - it makes a candidate, which is then
  verified like any other.
  The contract version the worker reports is **reported and never refused**: an
  older worker that still serves `/synth` is a working voice, and a version
  check that can take the voice away would be this layer causing the outage it
  exists to prevent.
  **And the mode is derived rather than remembered.** The worker image
  defaulted to the serverless handler, so a pod started without
  `VOICE_WORKER_MODE=http` opened no port at all - 404 on every path, which is
  indistinguishable from a missing route and is what §78 and this section were
  both paid for. `voice_worker/start.py` reads the platform instead
  (`RUNPOD_ENDPOINT_ID` means Serverless, `RUNPOD_POD_ID` alone means a pod and
  a port), the variable overrides it, and the first line of the pod's log says
  which half is running and why.
  What is automatic is the **address**. Nothing here starts, stops, resizes or
  pays for a pod, without exception: the nightly schedule that did is
  **deleted** at the owner's direction (§118) and **the pod runs
  continuously**. Deleted rather than disabled, on the Piper reasoning - a
  workflow with its cron commented out is one somebody re-enables by
  accident, and the voice vanishing at 23:00 for reasons nobody remembers is
  a day of diagnosis. A pod that is found and stopped is still *named* rather
  than skipped, and that note is sharper for the change: while the schedule
  existed "EXITED" was ambiguous between a clock and a fault, and now it is
  always a fault - RunPod evicted it, the account ran out, or somebody
  stopped it by hand.
  **And the address it finds has to be one a server can use** *(§117).* The
  ladder was discovering, registering and verifying
  `https://<pod>-<port>.proxy.runpod.net`, which is fronted by Cloudflare:
  it serves a browser and refuses a datacentre with a 403, so a pod that was
  healthy in a tab was unreachable from Render with nothing wrong on either
  machine, and the app reported the worker's 403 rather than the edge's.
  Discovering the wrong *kind* of address is still a fact about somebody
  else's infrastructure written into this app. A pod's **direct TCP
  mapping** - `RUNPOD_PUBLIC_IP` and `RUNPOD_TCP_PORT_<port>`, published to
  the container exactly as the pod id is - has nothing in front of it, and is
  now the preferred rung, with the proxy kept below it because failing over
  is not falling back. It is plain HTTP, so it is a **decision**, not a
  default: `VOICE_ALLOW_PLAIN_HTTP` is off, named in the 403's own message,
  reported on `/api/health`, and read by `voice_control.allow_plain_http()`
  from both the registry and the ladder - an address one accepts and the
  other refuses is a worker that registers successfully and is never used.
  Two things found with it that would have made the automation fail anyway:
  the worker announced **`$PORT`** rather than the port it was listening on,
  so on a pod running the app beside the voice it announced the app's
  address (§78 one layer up); and the nightly schedule carried the pod's
  **id** as a literal, which had gone stale for the second time, so it was
  starting and stopping a retired machine while the live pod ran unmanaged.
  Both are derived now - the port from what the process was told, the pod
  from its **name**.
  **And a mechanism is only built where every caller reads it** *(§119).*
  The ladder had one definition and four documented readers, and the fifth -
  the one that decides whether there is a voice at all - was still reading
  the variable the ladder replaced. `RemoteChatterboxEngine.available()`
  asked whether an address was *configured*, so a deployment set up exactly
  as `REMOTE_VOICE.md` documents (one `VOICE_REGISTRY_TOKEN`, pods that
  introduce themselves, deliberately no pinned URL) served a **placeholder
  tone by construction**: the code that walks the ladder lives inside the
  engine that had already been ruled out, so the registered rung could never
  serve anybody. Nothing failed, because falling back to a tone is a
  supported state - which is what made it survive two sessions about this
  seam. It asks the ladder now, and **being generous there costs no
  honesty**: `available()` has always meant *configured well enough to try*
  and never *reachable*, so §52's two questions stay two and the second is
  still answered by a real call. When nothing on the ladder can speak the
  episode fails with the reason attached, which is more honest than a tone -
  a tone is indistinguishable from a worker speaking badly.
  **And a refusal is told to somebody who can act on it.** Everything
  `POST /api/voice/register` rejects travelled only in the response body, to
  a GPU on somebody else's network, so a pod heartbeating every minute and
  being refused every minute looked from the app's logs exactly like no pod
  at all. Both refusals are logged now - the address and the reason for a
  422 (since §117, routinely `VOICE_ALLOW_PLAIN_HTTP=0` refusing the direct
  TCP address a pod correctly announced), and for a 401 the *name* of the
  variable whose two copies disagree and neither of the two strings.
  **And a discovered address is a host *and a port*** *(§120).* §112 got the
  host found rather than typed and §117 got the right *kind* of host; the
  port was still being guessed. `_http_port` ended in "the first http port
  the pod exposes", so on a pod running the app beside the voice - which is
  this project's, FAM on 8001 and Chatterbox on 8002 - FAM discovered **its
  own front door**, health-checked it through Cloudflare and reported the
  404 as a broken worker. Twice over: the default `VOICE_WORKER_PORT` is
  8001, so it matched the app by coincidence, and naming 8002 did not help
  because the fallback returned 8001 anyway. **A port chosen by something
  that cannot know is a guess**, and that one arrived dressed as a discovery,
  with a URL and a reason in words. It substitutes nothing now: a configured
  port the pod does not expose over HTTP yields no candidate and a sentence
  naming both numbers, while an *unconfigured* port still takes the single
  exposed one - "nothing was said" and "a port was named and the pod has not
  got it" are different states. And the direct rung **says when it passes an
  address over**: looking for 8001 on a pod that maps 22 and 8002 it used to
  return nothing silently, so the one address with no edge in front of it was
  skipped without a word. The honest fix for the port is the same as for the
  host and already exists - **a worker that registers itself announces the
  port it is listening on**, the only place that fact is known rather than
  declared. `RUNPOD_POD` plus `VOICE_WORKER_PORT` is the fallback for a pod
  that cannot reach this service, and it is declared in `render.yaml` now,
  because nothing anywhere prompted for it (§114's `PUBLIC_BASE_URL` finding
  in a second place).
  Nothing in it has made a real request to RunPod from the build container.
  `python tools/voice_doctor.py` against the running deployment is what turns
  that from careful into known.
- **A running server says whether a redeploy will erase its listeners.**
  *(§107.)* Every database is pinned to the mounted disk in the `Dockerfile`,
  and that list has now been incomplete twice - the second time it was
  messages, saved, shares and quotas, four stores added after it was written,
  so a deployment discarded every conversation, saved episode and share link
  on each push while the accounts beside them survived. Nothing said so,
  because from outside a wiped database and a new install look identical.
  Two things follow. The guard is **derived**: `tests/test_data_paths.py`
  reads every `data_path("VAR", ...)` call out of the modules rather than
  comparing the Dockerfile to a second hand-written list, because two lists
  somebody types agreeing with each other is the same mistake made twice and
  then compared to itself. That generalises past this feature: **a guard whose
  subject is enumerated by hand is decorative.**
  **And it is said out loud at boot now, because nobody reads a health page**
  *(§114).* The reported symptom was "the accounts and data are wiped every
  time a new Render deployment is made", which is exactly what the
  measurement below was built to answer - and it was answering to an empty
  room. `_announce_storage` logs it once at startup under a deliberately
  narrow condition: a store is ephemeral **and** its environment variable is
  set. That pair is the whole diagnosis - it means this deployment asked for
  a mounted disk and did not get one, which is a service created outside the
  blueprint, or a disk added without a redeploy. A laptop trips neither half,
  which is the point: a warning every developer sees on every run is a
  warning nobody reads, which is how this one got missed.
  `python tools/storage_doctor.py`, locally or `--url` against the running
  deployment, asks it on demand and prints the fix beside the answer.
  And `/api/health` reports `storage`, **measured rather than configured** -
  §52 applied to durability. A mounted volume is a different filesystem, so
  `st_dev` answers it: a database on the same device as the code is inside the
  image and goes when the image is replaced. Three states, because "could not
  tell" is real, and a store pointed at `/data` on a host with no disk
  actually attached reports `image` - which is exactly the case a settings
  check cannot see and the one somebody needs telling about.
- **Per-machine state lives in `~/.fam/`, never in the project.** Voice models
  (`~/.fam/voices`) and the API key (`~/.fam/env`, written by
  `python setup_key.py`) are set once and found by every later copy of the app.
  A key in a project `.env` is lost on every new copy, and the workaround for
  that is pasting it again somewhere it should not go. The key is never written
  into source: a commit keeps it in history after the line is deleted.
- **What a listener costs is recorded when it is spent** *(§73).* The provider
  only ever sees one account, so "which listener produced which request" has to
  be answered at the moment of spend or not at all. `metering.py` appends one
  row per episode, tagged from `_listener(request)`; `python tools/usage_report.py`
  and the admin-gated `/api/usage` read it back. The load-bearing part is that
  it never reports one blended cost per user: Claude and Exa are **marginal**,
  the GPU is a **fixed floor** that exists before the first listener, and the
  shared cache is a **discount that grows with listeners** - averaged together
  they describe how many listeners there are rather than what one costs. Every
  number says whether it is billed, priced or assumed, and the median is printed
  next to the p99 and the max because on a measured run the worst listener cost
  68x the median. `METERING.md` is the whole of it - including what it
  deliberately does not do: no quota, no enforcement, no billing, and no
  automatic block on an abuse signal.
- **A credential is never something a human types** *(extends the above; §72).*
  `~/.fam/env` solved this for one machine, and the demo does not run on one
  machine — a pod, a container and a CI runner each arrive with an empty
  `~/.fam`. `FAM_SECRETS` names a place the app fetches its own credentials
  from (`file:` or `cmd:`, so every secrets manager works and none becomes a
  dependency), and it is the one *non-secret* line a new machine needs. The
  order is process env > `FAM_SECRETS` > project `.env` > `~/.fam/env`, an
  explicit variable always wins, and a provider that is set and broken says so
  at startup, on `/api/health` and in the preflight rather than falling through
  to the canned script. `CREDENTIALS.md` is the whole of it — including what it
  deliberately does *not* buy: Anthropic's rate limits are per organisation, so
  a pool of keys is failover and not headroom, and the real ceiling on
  concurrency is the GPU, not the credential.

## There is an iOS app coming, and it changes how to write everything else

`IOS_APP.md` is the whole of it. The short version, because it constrains work
that has nothing to do with the app:

**The app is a native client of this API, not a web view around
`static/index.html`.** A wrapper is rejected under guideline 4.2, and worse, iOS
suspends a `WKWebView`'s `AudioContext` when the phone locks - so the episode
would stop the moment it is most wanted.

Three consequences for ordinary changes, starting now:

- **Every feature is an API before it is a screen.** Behaviour that exists only
  in the interface is behaviour that has to be written a second time.
- **Nothing new on the audio path may assume a browser** - not `AudioContext`
  semantics, not a cookie riding along on its own, not a relative URL.
  `static/fam-audio.js` is the port's specification: the retained `Int16`
  buffer, the clock-derived cursor and `TAIL_MARGIN` were all paid for in bugs.
- **A listener id still never comes from the client, and `_listener` must be
  satisfiable by a header**, not only by the cookie. A server-minted bearer
  token in the Keychain keeps that rule exactly; `?user=` never does.

The two settled constraints the app pressures, and how they resolve: **no MP3,
no audio files still holds** - Opus over a stream is decoded as it arrives and
writes nothing, so compression was always compatible and is now a prerequisite
rather than a scale question (26 MB for a ten-minute episode on cellular). And
**account deletion is missing**, which is a hard rejection for any app that
creates accounts - and an interesting decision here, because the script cache
is shared, so a deleted listener's scripts are other listeners' Explore feed.

**The server side of that is now built** - `ACCOUNTS.md` is the whole of it.
Email-or-phone sign-up, Sign in with Google and Apple, account settings,
in-app deletion, three tiers with enforced per-window limits, bearer sessions
beside the cookie, and every endpoint reachable at `/api/v1/...`. What is not
built, and is not an oversight: **payment** (nothing sets a paid plan yet, and
the enforcement path is worth trusting before money moves) and **delivery**
(no email or SMS, so no password reset and no verified address or number).

What has to happen, in order: hear an episode in the production voice (open
problem #1 - everything else is scaffolding around an unlistened product);
deploy the API somewhere with a GPU that is up when a phone asks; then a
throwaway Swift spike that plays one streamed episode with the phone locked,
which is the go/no-go for all of it.

## Decisions that will shape the next phase

- **Where does this deploy?** Bandwidth is 2.65 MB/min uncompressed; that is
  fine on localhost and expensive at scale.
- ~~**Is there a user account, and what does it entitle you to?**~~ *Answered
  twice: §66 for the shape, §70 for the boundary.* An identity is a session; an
  account is credentials attached to one; and what an account buys is
  **durability** - the things the server keeps for you. The constraint above
  says exactly which. Still open, and more visible than it was: **password
  reset**, which needs email delivery, and which the sign-up screen now says
  out loud rather than letting anyone find out the hard way.
- **Local or hosted voices?** Changes the cost model more than the model choice
  does.
- **How much to prefetch?** *Half answered (§105), and the half that is left is
  the expensive one.* What schedules a cycle is decided: myFAM, on the draw,
  per listener, at most one every `PREFETCH_CYCLE_SECONDS`. What is warmed is
  the **brief** - cheap enough that being wrong costs a fraction of a cent, and
  worth enough that being right removes the seconds EI costs a browse tap.
  **Still open: whether to warm scripts**, which is the question that actually
  costs money, and it is now a measurement rather than a guess - let it warm
  and read the per-source hit rate off `/api/health` or
  `tools/prefetch_report.py --live`. A source warmed often and taken rarely is
  paying for episodes nobody wanted; one taken nearly every time is worth
  warming deeper (`PREFETCH_LEVEL=script`). Two further things nothing warms
  yet, and both are one class each: DailyFAM's mixes are warmed only when
  their owner opens myFAM, and no cycle is scheduled for a listener who is not
  looking.
- **Is a local embedding model worth installing?** *Half answered (§107): the
  near-match cache is **on** now, and the embedding is still the part earning
  nothing.* The mechanism raises the share of re-phrasings that find an
  existing episode from 22% to 56% on a measured corpus - 9 of 41 to 23 of 41
  - with no false match at any threshold and 8.93 ms of local scanning, on the
  miss path only, with no model call anywhere. Fourteen episodes not written
  at roughly a cent each, so it ships on; `CACHE_VECTOR=0` restores the old
  behaviour exactly.
  The bench's control line is what is still open: **guards alone, with the
  cosine ignored, find the same 23.** Every must-not-collapse pair is refused
  by a guard - identical numbers, lexical overlap, agreement about needing
  today's facts - rather than by the threshold sitting above it, so the vector
  is carrying none of the gain. A real sentence model in `~/.fam/embed` is the
  only thing that changes that, and it is the same trade as the voices - ship
  a model with the app, or pay a service per call.
  **Answered for the cache, and the answer is "not by itself"** *(§131).*
  all-MiniLM-L6-v2 is installed by `tools/install_embed_model.py` and by the
  Dockerfile, and the bench with it finds the same 23 at the safe point: it
  reaches 37 of 41 with the overlap guard off, and serves "how old is the
  eiffel tower" for "how tall" at 0.879 on the way. Only a cross-encoder
  separates those, so the cache's guards and defaults are unchanged.
  **The model is used by the ranker instead** (`taste_vectors.py`), where a
  near miss costs a weaker tile rather than a wrong episode: a bounded
  additive term in Made for you for how close a tile is to what the listener
  has asked for, ~2 ms a warm page, `{}` and the old ranking with no model.
  And the order of that rail can now be **fitted to taps**
  (`learned_rank.py`, `python tools/learn_rank.py`): a logistic regression over
  the ranker's own six signals, rebuilt point in time from the impression
  log, stored only if it beats the hand-tuned order on held-out offers, and
  allowed to re-order what cleared the floor but never to admit a tile under it.

## How to ship a change (standing instruction)

The loop matters as much as the code. Every change ends the same way, without
being asked:

1. `./dev.sh check` — tests, the interface-parses check, the preview build and
   its browser smoke test. All of it, every time.
2. Rebuild the phone preview and **republish it to the same artifact URL** so
   the link never changes:

       https://claude.ai/code/artifact/c8bd86aa-e61e-4262-a1c8-b9c8d8d6645e

   Publishing to the same file path within one conversation updates it in
   place; **from a new conversation, pass that URL as `url`** or you will
   create a second artifact and the link the phone has bookmarked will go
   stale. Read it first, then publish to it.

   **What lives at that URL is now `preview/fam-live-artifact.html`**, built by
   `python preview/build_live_preview.py` - the same interface, but running on
   a real database rather than fixtures, with the store shown beside it.
   Publish it with `capabilities: {"db": {}, "downloads": true}`; without
   `db`, `claude.use("db")` resolves null in the viewer, the page falls back
   to memory, and the whole point of it is quietly gone. `downloads` is what
   lets the story card save inside the viewer (§137) - a declaration is the
   full set, so passing `db` alone revokes it. The fixture build
   (`preview/fam-artifact.html`) is still what `./dev.sh check` produces and
   smoke-tests; it is just no longer what the bookmarked link serves.
3. Reply with a short summary of what changed and the preview URL. Not a zip,
   not a wall of files.
4. If something genuinely cannot be automated, say the exact command to run.

`DEVELOPMENT.md` documents the whole loop. The preview is the interface running
on fixtures - good for layout, flow and interaction on a real phone, useless
for writing quality or time-to-first-audio, which need the server.

**To show or judge the product rather than change it, `./demo.sh`** (PROBLEMS.md
§50). It reports what the machine will actually do before it starts - canned
script with no API key, placeholder tone with no voice model, dead Explore tab
with an empty cache - refuses to start quietly broken, offers to seed, and then
says where to press on each tab. `python tools/seed_demo.py` writes the history
the browse surfaces need: Explore replays other listeners' episodes and by
design cannot generate one, so on a fresh database it stays empty however much
you tap it.

## Picking this up in a new session

Everything is in the repo; nothing of consequence lives in a chat log. Branch:
`claude/search-podcast-audio-generator-ed4br1` — develop and push there, and do
not open a pull request unless asked.

Read in this order: this file for where it is going and what is settled,
`PROBLEMS.md` for every problem hit and its cause (newest last — §108-§114 are
the most recent: the opening of every researched episode turned out to be
written by the half that knew least, what happens when the search comes
back empty turned out to be "write it from memory anyway", a DailyFAM mix
turned out to be unable to accept a topic anybody typed, one change on
RunPod turned out to cost a day because the address of the voice was a fact
about somebody else's infrastructure kept in this app's environment, a wake
was suppressed for the first minute of every machine's life by a sentinel the
clock could produce, and **§114** is six things that were each behaving
correctly against a question nobody was asking - a share link that was
correct and relative, a profile page full of true statements drawn for
somebody with no account, a Trending rail filled from leftovers, a ranker
blind to the subtags it was given, a seed nobody could take back out, and a
disk that was declared and never attached; and **§115**, found while getting
§114 ready to merge - CI had been red on `Main` for nine merges on one
assertion that was *right*, and the reason nobody saw it is that this
container has no webfonts and the runner does; and **§116**, two surfaces
that were each saying something true about themselves where something
useful about the episode belonged - a browse card that explained the ranking
instead of the episode, and a new listener's page that was one row of
content and four empty-state sentences; and **§117**, the voice after the
pod migration - the worker was healthy, the address was right, and the proxy
in front of it refuses a server while serving a browser; and **§118**, the
nightly pod schedule deleted at the owner's direction so the voice is up at
all times; and **§119**, the ladder built and the engine that walks it never
built - every commit through §118 was deployed and Render still served a
placeholder tone, because the question "is there a voice" was still being
answered by the variable §112 replaced; and **§120**, the pod found and the
port guessed - on a pod running the app beside the voice, FAM discovered its
own front door, health-checked it and read the 404 as a broken worker; and
**§121**, the recommender's inputs - three signals the app was collecting and
discarding, a click-through rate thirty days deep that nothing had ever read,
a location field the app had never had, and a ranking vocabulary whose
thirty-seven hand-written keyword lists turned out to be the binding
constraint rather than the scoring under them; and **§122**, what checking
that work found - four defects none of the 2,289 tests could see, because
every one of them only appears at a size no test runs at; and **§123**, three
controls that were each right about a question next to the one in front of
them - a picker that reported the account gate as a broken topic list, a (+)
that took two screens to say what the screen behind it already said, and a
search page that opened on a length its own menu disagreed with; and
**§124**, a blank slate that was not blank - the wipe took every store and
left the vocabulary those stores had taught, which is a ranking input that
outlived its own source; and **§125**, the generic episodes and who they are
for - twenty-eight evergreen tiles no wipe could ever have removed, because
they are compiled into `topics.py` rather than seeded, offered to everybody
including the listeners FAM knew enough about not to need them, under a
crowd-row heading making a claim about listening nobody had done; and
**§127**, eleven changes from one packet - the event log moved behind the
account gate, a floor under every rail but friends, titles and summaries
before the first word, captions measured against the audio, and audio that
plays with the ringer off; and **§126**, the vocabulary that started from nothing - `MIN_LISTENERS` is
three, so a deployment with no traffic had no grown vocabulary at all, and
the tree could already see "college football" in a tile's question while
the ranker scored that tile on `sports` alone; and **§128**, where the wait
in front of the first word goes, logged one step per line as `episode
timing`; and **§129**, the writer's hidden thinking back to `EFFORT=low` at
the owner's direction;
and **§131**, the embedding model finally installed and measured - no help
to the cache on its own, used by the ranker instead - and an order for Made
for you fitted to thirty days of taps that is only served if it beats the
hand-tuned one; and **§132**, the audio of a cached episode kept beside its
script so a replay never goes back to RunPod; and **§133**, the Profile tab
rebuilt as YourFAM from the owner's design handoff - on the real social API
rather than the mock module the handoff asked for; and **§134**, the
22/09 packet - four tiles a rail, a Trending row ranked on popularity and
country alone with no dummy tiles, DailyFAM playlists that play through
and stop, real play counts and thumbs on Explore, and four RunPod leaks;
and **§136**, DailyFAM mixes that follow subjects - narrowed to a team or
company, each its own dated daily briefing - with a cover; and **§135**, where an episode's information comes from - the model's own
web search deleted, API-Sports swept on its whole daily allowance with the
score on the card, and a Trending row of real stories ranked by how many
outlets run them, worldwide and region by region),
`MYFAM.md` for the browse page, the
live story pool and the startup set that fill it, `DATABASE.md` for what the
fourteen stores hold and the one path from a row in them to a tile on a
screen, `DEVELOPMENT.md` for the loop, `CREDENTIALS.md` for how a
machine gets its API keys without anybody typing one, `METERING.md` for
what a listener costs and how the report says so, `ACCOUNTS.md` for identity,
tiers, quotas and the public API, `SHARING.md` for friends, sharing, saving
and downloads, `LIVE_FACTS.md` for how FAM handles rapidly changing information
and what it does when it has none, `TRENDING.md` for the myFAM row that says
what the world is talking about, `PROVENANCE.md` for the sources panel and why
what the app displays is not what the writer reads, `PROVIDER_ROLLOUT.md` for
the step-by-step of turning each live provider on, and `IOS_APP.md` for the app version this is
now being written towards.

A fresh container has none of the dependencies installed. Setup is two lines,
and the second one is not optional:

    pip install -r requirements.txt
    pip install playwright        # or the browser smoke test skips itself

Then run `./dev.sh check` before changing anything, so you know the baseline is
green rather than assuming it. A complete run ends with `all checks passed`
**twice** - once per preview build - and **sixty-seven** named smoke
behaviours each time; anything less means something was skipped, and `dev.sh`
now says so out loud (PROBLEMS.md §49). The number is
`grep -c '^        check(' tools/smoke_preview.py`, so check it rather than
trusting this sentence: it has been wrong before, because a count written in
prose does not fail when somebody adds a behaviour. (It is 67 as of §136,
which added a new mix following narrowed subjects and a square mix cover. It
was 65 as of §134,
which added a DailyFAM playlist playing through and then stopping. It was
64 as of §127,
which replaced "Go Deeper fills for a new listener" and added the fixed myFAM
header, the loading screen's cancel, and faces and typing in messages. It was
61 as of §123,
which added the new-mix gate, the topic bank surviving a locked mix list, and
the search page opening on the length it will generate.)

**There is a third browser run, and it is not one of those two** (§106). The
share landing page is a different page from `static/index.html` - one episode,
and every other control a door to the App Store - so it has its own build
(`preview/build_share_preview.py`) and its own driver
(`tools/smoke_landing.py`), which prints `all N landing behaviours passed` and
counts them rather than naming a number. A complete run therefore ends with
that line as well. All three are in `.github/workflows/ci.yml` too: §101's
finding is that a check added to the local loop is not added to the gate, and
the two lists are still kept in step by hand.

What is true but not obvious from the code:

- **The gate runs a different Python from the build container**, and that gap
  hid a failing test for days (§106). `.github/workflows/ci.yml` pins 3.12;
  this container has 3.11. A green `./dev.sh check` is therefore not the same
  claim as a green CI, and the difference showed up as order-dependent state
  that only 3.12 exposed. If CI fails on something that passes here, build a
  3.12 venv and run the whole suite in it before concluding anything about the
  environment.
- **Check that CI is actually green before trusting it.** It was red on `Main`
  for at least ten merges (§106), always the same assertion, and a red board
  stops being read. `mcp__github__actions_list` on `ci.yml` filtered to `Main`
  answers it in one call.
  **It happened again** (§115): nine more merges, one assertion, and that note
  above was not enough on its own. Do the one call before trusting a green
  local run, and do it *before* starting work rather than at the end - the
  answer decides whether a failure you hit is yours.
- **The build container's fonts are part of the test environment, and nothing
  declares them.** *(§115.)* There is no route to Google Fonts here, so
  Chromium substitutes a narrower serif; the CI runner loads the real face.
  A smoke check that measures text therefore passes here and fails there on
  identical markup - which is how a genuinely clipped headline shipped and
  stayed. This is §106's "a green `./dev.sh check` is not a green CI" with
  nothing to do with the interpreter, and §113's "the container's uptime is
  part of the environment" in a third form. **When a layout assertion
  disagrees between here and the gate, suspect the environment before the
  markup, and read what the check says it measured** - `smoke_preview.py`
  prints the font, the size, the line height and both heights precisely
  because the answer is not in the source.
- There is **no API key** in the build container, so writing quality and
  time-to-first-audio cannot be verified here. Tests, the interface checks and
  the browser smoke test all run without one. Anything about *how the writing
  sounds* is unverified until someone runs it with a key.
- The checks answer "does it work", not "does it look right". `tools/shots.py`
  photographs all sixteen surfaces so a refactor can be proved neutral;
  `tools/stall_probe.py` measures browser stalls without a key, and
  `tools/compare_search.py` measures what research actually buys. Each exists
  because a claim was once made without it and was wrong.
- Deleting CSS from `static/index.html` has broken this app twice. Use
  `tools/check_css.py` and `tools/shots.py`, not judgement.
- **A name declared twice at the top level of `static/index.html` is a button
  that silently does somebody else's job** (§111). Every control here is an
  inline `onclick` naming a global, the later declaration wins, and nothing
  throws. `tools/check_js.py` fails on it now. The same section is the reason
  to distrust a smoke check that only asserts a control is *on screen*:
  `.typed-offer` was always on screen and had stopped doing anything.
- **A setting is settled only where it is copied.** `.env.example` shipped the
  cold open and web search *on* while `config.py` had them off with the
  reasoning attached (PROBLEMS.md §54), so following the documented setup
  configured the product against its own spec. `tests/test_env_example.py` now
  fails on any disagreement between the two.
  **The same crack, in the interface** *(§123).* `selectedLengthMinutes` is 2
  and said why; the markup printed `3 min` in five places, so search opened
  reading three minutes and its own menu ticked two - the interface
  disagreeing with itself about one setting, in two elements a tap apart.
  `paintLengthControls` writes all of them from the variable before the first
  screen is drawn, the length menu delegates to it rather than keeping its own
  list of where the number is printed, and
  `tests/test_search_length_and_mix_gate.py` pins each literal to the
  variable. The playback pills are deliberately **not** pinned to the default:
  they name the length of the episode that is *playing*, which is a different
  question and may honestly differ.

## Working notes

- `PROBLEMS.md` is the engineering log: every problem hit, its cause, its fix,
  and what is still open. Add to it rather than starting fresh notes.
- Tests run with no API key and no speech engine (`python -m pytest tests/ -q`).
- `diagnose_api.py` explains connection failures; `compare_models.py` compares
  cost, speed and output across models.
