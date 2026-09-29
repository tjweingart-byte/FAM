# Open problems, in the order they hurt

> Moved verbatim from `CLAUDE.md` (2026-09-28, PROBLEMS.md §168). `CLAUDE.md`
> keeps one line per rule and loads every session; this file keeps the
> reasoning and history and is read on demand. Each rule starts at a
> `<!-- rule:ID -->` marker, and `CLAUDE.md` cites the same ID in brackets -
> `grep -n 'rule:ID' docs/claude/*.md` jumps to it.
> `tests/test_claude_md.py` fails if the two sets of IDs ever disagree.

## Open problems, in the order they hurt

<!-- rule:op-voice -->
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
   **Hard names are respelled for the voice** *(§165, `pronunciation.py`)*:
   EI's brief and the writer's `<<SAY: Name = respelling>>` lines (written
   *before* the script) feed one lexicon in the voice bank's database, an
   admin fixes any name on /admin and is never overridden, and only the text
   handed to the voice changes - captions, cache and titles keep the real
   spelling. Unheard.
<!-- rule:op-voice-bank -->
2. ~~**Voice selection**~~ — *done, and a bank now* (§147). `voice_bank.py`
   keeps several cloned voices, each a recording plus its rights record, in
   the app's database; a worker without one is sent it once. **Only searchFAM
   picks a voice** - a chip on the search page and a row under Listening in
   Settings, kept per listener. Every other surface draws one from the bank
   per episode and keeps it beside the script, so replays hit kept audio.
   Note: voice is deliberately **not** part of the script cache key, because a
   voice changes the audio and not the words. Switching voice therefore reuses
   the cached script — measured at ~90 ms and zero API cost. The *audio* kept
   since §132 is keyed on the voice as well, so a new voice is voiced once and
   kept like any other.
<!-- rule:op-cold-open-gone -->
3. ~~**The cold-open → script gap**~~ — *dissolved, not fixed* (PROBLEMS.md
   §55). Two sessions went into making the opener cover the research wait, and
   heard on a real machine it was 3-5 seconds of contentless speech in front of
   a 30-45 second silence. Five does not cover forty-five, and the opener was
   prompted to state no facts, so what it did cover was worthless. **The whole
   feature is deleted** - not switched off - along with `tools/gap_probe.py`,
   which existed only to measure it. The interface now shows an honest wait
   that names what it is waiting for and counts the seconds.
<!-- rule:op-myfam -->
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
   **Every choosing rail runs one path** *(§136)*: taste, then the tiles
   whose script is already written first (`ready_first`, `READY_REACH` deep,
   never a filter), then **no repeats** against everything the listener has
   ever heard on any surface (`topics.is_repeat`). A heard evergreen tile is
   replaced by the next-best topic; a heard startup question may come back
   only as a different episode - a day later, and remade or unwritten. **A
   heard live story is never offered as itself on any rail, Trending
   included** (the owner's direction): it becomes a "what's new since you
   listened" follow-up if it is still being reported six hours on
   (`topics.trending_for`, applied to the pool before any rail sees it), and
   otherwise the next story takes its place. Trending's order is still
   popularity and country alone.
   **Trending is an edition now** *(§139, at the owner's direction)*: built
   at 05:00 and 17:00 Eastern from **GNews** (`trending_bank.py`,
   `gnews.py`), ten stories with their ten episodes written into the shared
   cache before anybody taps, **no fallback source**, and **never the
   live pool** - that is Made for you's, and GNews is never spent on it. A
   bank episode keeps for as long as its edition can be shown, which is the
   one place `cache.ttl_for` is overridden - see
   TRENDING.md, "The trending bank".
   **Made for you draws on both inventories** and Trending draws on neither
   of them since §139 - it was the live pool alone before, because answering "what is trending" with what FAM's listeners
   have already played would make it a laggier copy of the row below it. The
   two crowd rows lead with tiles whose script is already written - a sort and
   never a filter, since an expired cache would otherwise empty a row and call
   it a fact about what people are playing.
   **The three crowd rails hold cached episodes only** *(§141, at the owner's
   direction)*: "What FAM can't stop listening to" ranks by total listens
   (thirty days, a finished listen counted once, by question so a search
   counts); the friends rail is what friends listened to *or created*
   (`scripts.author`); and "What you missed last week" is what other
   listeners played three to seven days ago that this one never heard, with
   no live-feed episode (Polymarket, Finnhub, API-Sports), nothing from the
   story pool or Trending, and no top-up.
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
<!-- rule:op-taste-vocab -->
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

<!-- rule:op-empty-deployment -->
> **Current (PROBLEMS.md §168):** Since §134 only Made for you is topped up (`RAIL_MINIMUM`); "the first two are then topped up" below is stale.

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

<!-- rule:pick-up-rail -->
- **"Pick up where you left off" is a day, and under 60%** *(PROBLEMS.md
  §173, the 9.29 packet, at the owner's direction: "If more than 60% of the
  episode is finished, then don't display it in Pick up where you left off.
  Additionally, only display an episode there for no more than 24 hours.")*
  Its two sources are unchanged - episodes started and not finished, and the
  Go Deeper prompt of an episode finished - but both now come from the last
  24 hours (`app.GO_DEEPER_WINDOW_SECONDS`, a week before), and a part-heard
  episode more than `SavedStore.RESUME_MAX_FRACTION` through is finished: its
  position is deleted on write and never returned on read, which covers rows
  written before the rule. "Through" is measured against the episode's real
  length when the player has all of it (`/api/progress` `duration`), because
  the requested minutes are a ceiling and an episode that ran short would
  otherwise never reach 60%. A tile leaves after a day and the next one that
  qualifies takes its place; nothing tops the section up.

<!-- rule:no-not-interested -->
- **There is no "not interested"** *(PROBLEMS.md §171, at the owner's
  direction: "This button and function should not be a feature. The
  algorithm should work naturally to put episodes the user is interested
  in").* The card button, `notInterested()` and `EventStore.hidden` are gone;
  `hide` is no longer an event kind, so a new one is refused. Rows written
  while it was one stay in the table and are read by nothing that ranks
  (`for_user` skips them). What a listener does not want is shown the way
  everything else is: skips, plays never made, and impression fatigue. Do
  not add a dismiss-a-tile control back to any surface without asking. (Go
  Deeper's X, which closes a suggested follow-up, is a different thing and
  stays.)

<!-- rule:op-taste-scoring -->
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
   *(**Reversed by §155, at the owner's direction: it excludes now.** One
   Eagles question still put *Wofford vs Mercer* and a Division II fixture on
   Made for you - the 0.3 cut cleared the floor, the floor's top-up put back
   what the ranking dropped, and `football` counted as both "specific" (a
   depth-1 seed node) and "familiar" (a word they typed). A live story is
   offered only when it names something they follow: a subtag or a category
   at least two levels deep (`SUBJECT_DEPTH`), a word they used that is not a
   field's own name, their place, or a near paraphrase
   (`SEMANTIC_NEAR_COSINE`). The floor since §127 is what makes excluding
   safe - it tops up from evergreen tiles, never from the live pool.)*
   *(**Superseded by §147**: there is no length control on myFAM any more -
   every episode that is not a search is `BROWSE_MINUTES`, two. What follows
   is the history.)* *(Two things on the surface changed in §95. The header's right-hand slot is
   now an **episode-length control of its own**, deliberately separate from the
   search player's: the length you want for a question you have just typed and
   the length you want for a tile you are scrolling past are different
   appetites, and one shared number meant setting either silently reset the
   other. And each rail ends in a chevron only while there is something left
   to scroll - at the end it becomes **View more**, which opens the whole of
   that section. That screen is the same ranking at full length, with the
   tiles whose script is already written marked and sorted to the front; it
   generates nothing, because the rail was showing six of something that
   already had twenty-eight. *Since §165 it shows eight at a time, and
   Refresh deals the next eight of that ranking not yet shown, starting
   again from the top when they run out.*)*
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
   **There is no wheel any more** *(§142, at the owner's direction).* §99
   drew the first run as six facet discs orbiting a "View more" hub, §100
   fixed their labels tilting, and §107 made the Settings copy a reflection of
   what the listener chose. All of it is deleted - the orbit, `syncWheelPhase`,
   the separate catalogue screen behind the hub. **The interests page is the
   long list itself**, on the first run and from Settings: search it, tick
   what you like, add anything typed. Facets chosen on the old wheel are still
   stored and are shown as removable pills above the list rather than kept or
   dropped in silence. From Settings it is an editor: Save keeps, the X puts
   back what was there (`introSnapshot`). `popular_facets` still orders the
   facets `/api/preferences` serves, and `my_facets` is still served as
   `interests_yours`; nothing draws either today.
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
   which of the three decided it, and only the Settings editor carries a line
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
   `INTEREST_CATALOGUE` is the long list that is the interests page itself
   since §142 - **73 named subjects, not 73 new tags.** Each carries facets from the same eight, so
   the settled constraint above is untouched: an interest is something a
   listener recognises, a tag is what the ranker scores, and the picker is
   still built from `TAG_LABELS`.
   The cost design is the load-bearing part: **one bank for
   everyone, personalisation in the ordering, not the inventory** - so two
   people tapping a tile share one script through `cache.py`. That is
   unchanged by the live pool, which is shared for exactly the same reason.
<!-- rule:op-dailyfam-mixes -->
6. **playFAM is built as its own tab.** *(§137 changed what a mix holds:
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
   And the **privacy switch means public** *(§153, at the owner's direction,
   reversing the old "means private")*: on is Public with an open lock, off is
   Private with a closed one, and **a new mix is public by default** so other
   listeners can find it in DailyFAM search. A copy added from somebody else's
   mix still starts private - republishing another person's mix is not the
   copier's call to have made for them.
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
<!-- rule:op-follow-graph -->
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
<!-- rule:op-friend-profile -->
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
   **Once means once** *(§142)*: which followers the popup or banner has
   already shown is a table of its own (`social.announced`), because the badge
   and the popup answer different questions - "who is new since you looked"
   and "who have you been told about" - and the popup had been remembering
   the second in page memory, so every fresh open showed the latest follower
   again. `/api/friends` carries `announce` beside `new_followers`.
   **And eighty-five per cent through is finished.** Waiting for the last
   sample counted almost nothing: the ending is the one part a listener skips,
   so an episode heard to the ninety-fifth percentile and closed was recorded
   as a *play* - worth 1.0 against a completion's 2.5. The strongest signal the
   taste model has was being thrown away by exactly the behaviour it should
   reward. A share rather than a number of seconds, because "the last thirty
   seconds" is most of a one-minute episode and nothing of a ten-minute one.
<!-- rule:op-social-live -->
> **Current (PROBLEMS.md §168):** New mixes are **public** by default since §153 - "Mixes are private by default" below is stale (see [op-dailyfam-mixes]).

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
<!-- rule:op-profile-hub -->
> **Current (PROBLEMS.md §173):** your own YourFAM page no longer draws the interest pills - the owner found them clutter, and Edit profile ("Your interests - N of 5") is where they are seen and changed. `interests_shown` is still what the profile *shares*: a friend's page shows it. "Find new friends" is a gold pill with a plus rather than a text link. Where the text below says the hub shows five pills, read "the profile shares five".

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
<!-- rule:op-attachments -->
10. **Attachments are built** (`attachments.py`, PROBLEMS.md §47). A search can
   carry documents, photos and links. Extraction happens when the thing is
   attached, never on the generation path, because a round-trip in front of the
   first word is the one cost this product refuses. Every failure is a sentence
   the listener can act on, and an attached episode is **never cached**, so it
   cannot reach another listener or Explore. Only PDF needs a package (pypdf,
   optional); .docx is read with `zipfile`.
<!-- rule:op-identity -->
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
