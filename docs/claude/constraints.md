# Settled constraints

> Moved verbatim from `CLAUDE.md` (2026-09-28, PROBLEMS.md §168). `CLAUDE.md`
> keeps one line per rule and loads every session; this file keeps the
> reasoning and history and is read on demand. Each rule starts at a
> `<!-- rule:ID -->` marker, and `CLAUDE.md` cites the same ID in brackets -
> `grep -n 'rule:ID' docs/claude/*.md` jumps to it.
> `tests/test_claude_md.py` fails if the two sets of IDs ever disagree.

## Constraints that are settled — do not undo without discussing

<!-- rule:no-audio-files -->
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
  every play exactly. Downloads as a *button* are still **removed**, and
  `saved.py` still holds pointers and only pointers - **but the device now
  keeps what it finished** *(§161, at the owner's direction)*: an episode
  whose whole stream arrived is kept as raw PCM in the page's IndexedDB
  (`OfflineShelf`, bounded, emptied on log-out), so it plays offline, and
  `static/sw.js` keeps the app shell. Still no audio file and no MP3.
<!-- rule:duration-ceiling -->
- **Duration is a ceiling, not a quota.** *(Revised.)* The selected length still
  caps the episode and over-runs are trimmed, but a script that runs out of
  substance now ends early instead of being padded. Enforcing the number in both
  directions is what produced filler: it made the model pad. `ALLOW_TOPUPS=1`
  restores the old behaviour.
<!-- rule:transport -->
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
  **And leaving the player never stops it** *(§142).* The player's X
  minimises (`minimizePlayer`), and neither `goBack` nor `setTab` stops an
  episode that is already playing - both used to, a leftover from the
  prototype's speech player. The mini bar is still one element, moved by
  `placeNowBar` above the tab bar of whichever tab is showing (never
  Explore's), so the episode follows the listener and tapping it brings the
  full player back mid-sentence.
<!-- rule:nothing-before-material -->
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
<!-- rule:no-filler -->
- **No filler, ever, and no setting for it.** The cold open was deleted, not
  disabled - a knob left behind is an invitation to turn it back on, and this
  one was turned back on by an example file. Nothing plays until the real
  briefing does. The interface says what it is waiting for and how long it has
  been waiting; a wait you were warned about is a different experience from the
  same wait unexplained.
<!-- rule:slurs-only -->
- **Slurs are the one thing taken out; swearing stays and earns an E**
  *(PROBLEMS.md §171, at the owner's direction: "we don't want any censorship
  from any media sources or speech options. The only thing we want to filter
  out are any words that could be considered slurs, or were made to be
  hateful to a specific group of people ... Cuss words are ok to be in the
  episodes, but if they are in an episode, there should be an explicit
  symbol").* `content_filter.py` holds both lists. A slur becomes "a slur"
  in `clean_for_speech` (the one door every writer's sentences pass through),
  in the title, summary and `<<NEXT:>>` lines, and on the way out of the
  cache for rows written before the filter - whose kept audio is dropped and
  voiced again, since audio cannot be scrubbed. It is code, not a prompt
  rule: the prompt is at its size guard and the writing is unchanged. Swearing is never touched; `is_explicit` reads it off the script, and
  the player, the mini bar and Explore draw an E before the title
  (`explicit` on `/api/next` and `/api/explore`). **Overfiltering is the
  failure to guard against**: whole words only, never substrings; a word is
  listed only when its dominant use is contempt for a group; one that is
  also a surname, food, sport, clinical term or history goes in
  `DELIBERATELY_ABSENT` with why, and an idiom that contains a listed word
  goes in `EXCEPTIONS`. Mild words (damn, hell, crap, ass) carry no E. Add to
  the lists with a test of an innocent sentence beside each addition.
<!-- rule:always-researched -->
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
<!-- rule:ei-before-search -->
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
<!-- rule:recency-credibility -->
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
<!-- rule:started-not-finished -->
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
<!-- rule:missed-not-absent -->
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
<!-- rule:situate -->
- **Situate, never orient, and do it in the first two sentences.** *(§88.)*
  "Speak from inside" bans explaining why a subject matters; it was read as
  permission to open anywhere, and produced a 2017 draft anecdote in front of a
  live game. Situating is different from orienting: it says where the listener
  is standing - who, what, when, in particulars - and it is required. History
  earns its place by explaining the present rather than preceding it.
<!-- rule:opening-last -->
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
<!-- rule:live-captions -->
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
<!-- rule:provenance-all-paths -->
- **Every way an episode is researched records who it read.** *(§107.)*
  Provenance is built from the Exa packet, the GDELT packet, live facts and
  attachments - which since §135 is every way there is. §107's
  `provenance.from_web_search` read the model's own search results off the
  final message; it went with the search itself.
<!-- rule:player-no-autogen -->
- **Nothing on the player generates an episode except a button.** *(§104.)*
  `.mini-stage` is `flex:1`, so it is most of the player, and it carried a tap
  handler that jumped to the next episode in the album or - with no album -
  generated a **random** myFAM topic: an episode nobody asked for, costing a
  model call and a GPU, in place of whatever was playing. Removed with nothing
  in its place; the swipe-up gesture still moves through an album and is the
  one the `next-hint` label actually advertises.
<!-- rule:live-facts -->
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
<!-- rule:bank-for-guests -->
> **Current (PROBLEMS.md §173):** Trending cards no longer say the place (the card shows its subject like every rail), and View more is grouped by **continent** - Worldwide, the listener's own, then the rest busiest first; no Antarctica (`geography.continent_for`). "Each card says the place" below is history.

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
  (`STORIES_BACKGROUND_SECONDS`) - **but only sports and markets are asked
  that often** *(§156, at the owner's direction)*: GDELT's news sweep, the
  trending registry and Polymarket have a floor of two hours
  (`STORIES_NEWS_INTERVAL_SECONDS`), and what they found is carried between
  their sweeps. Nothing here has made a real request from
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
  **And a guest's whole page is the bank now** *(§154, at the owner's
  direction)*: every rail, dealt by facet (`topics.guest_feed`), marked as a
  sample, with an example DailyFAM playlist beside it
  (`/api/mixes/sample`). **It costs nothing**: no sweep, no prefetch, and a
  guest's tap on a sample tile plays only an episode whose audio is already
  kept - anything else is a 403 that opens sign-up, before the GPU is woken
  (`app._guest_play_gated`). `tools/warm_guest_bank.py` makes the bank's
  episodes once, paid by whoever runs it.
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

<!-- rule:tile-title-angle -->
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

<!-- rule:card-hook-startup -->
> **Current (PROBLEMS.md §173):** the eight startup questions are written ahead at every Trending edition slot (`trending_bank.write_startup`, `STARTUP_WRITE_AHEAD`, needs no GNews key), rewritten each slot because they ask about *this week*, so a card carries the episode's own title (§161's `_name_written_tiles`) before anybody taps. The ninth, local question is never written ahead: it is personal. An outcome-dependent brief is still left for the tap.

> **Current (PROBLEMS.md §168):** `render.yaml` now turns GDELT on, so "`GDELT=1`, which no deployment has" below is stale.

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

<!-- rule:missed-rail -->
- **The weekly recap is a rail, not a popup.** *(`topics.rank_missed`,
  `MYFAM.md`.)* It was one episode *about* somebody's week, fired on the first
  open on or after Sunday - so a thin week produced an episode about having had
  a thin week, in front of somebody who opened the app to listen to something
  else. **What you missed last week** is a shelf of episodes they can still
  have: what FAM put in front of them in the last seven days and they did not
  take.
  **Superseded by §141, at the owner's direction** - what follows is the
  history. The rail is now cached episodes other listeners played three to
  seven days ago that this one never heard, none written from a live feed,
  most-listened first, with no impressions, no story pool and no top-up.
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
<!-- rule:trending-floor -->
> **Current (PROBLEMS.md §168):** Since §134/§139 Trending is chosen first from its own GNews edition and never draws on the live pool, so the reservation below is history.

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
<!-- rule:two-crowd-rows -->
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
<!-- rule:cache-week -->
- **Every episode is kept a week and stamped with when it was sourced; how
  long it stays *current* is what `ttl_for` decides.** *(§143, at the owner's
  direction.)* `CACHE_LIFE_SECONDS` (a week) is how long a row lives, from
  `scripts.sourced_at`; `fresh_until` is how long a *new request* may be
  served it. Replay surfaces (Explore) play anything kept, labelled with its
  sourced time; every path that would write an episode asks `get(key)`,
  which is current-only, and writes a new one otherwise. So `0` below now
  means "never current", not "not written". Evergreen is current for the
  whole week. A shared link or a tile past its window is written again, as
  it was when the row simply expired. `CACHE_LIFE_SECONDS=0` with
  `CACHE_TTL_SECONDS=86400` restores the old cache exactly.
<!-- rule:ttl-from-evidence -->
- **How long a script stays current comes from what it was built on, never
  from the words of the question.** *(§89, `cache.ttl_for`.)* It used to be a keyword
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
  **The DailyFAM edition is the stated exception to the second** *(§143)*: a
  "last 24 hours as of today" prompt at 05:00 asks about things that have
  happened, and it runs the live lookup as a tap would. A game still in
  progress at write time is kept and never current, so the tap writes it.
<!-- rule:depth-not-words -->
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
<!-- rule:prefetch-same-key -->
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
<!-- rule:candidate-reason -->
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
<!-- rule:account-gates-kept -->
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
<!-- rule:tiers-off -->
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
<!-- rule:refusal-wording -->
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
<!-- rule:tier-spend -->
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
<!-- rule:save-pointer -->
- **Save for later is a pointer, and pressing save is the whole of it.**
  *(SHARING.md.)* Question, length, title - one row - and playing one needs
  the network like any other episode. It is a **toggle**: the icon turns
  green, pressing it again takes the episode off the shelf. Same shape as
  VIBE! and drawn the same way, by a `data-save` sweep rather than a list of
  ids, which is the mistake that once left the main player with no vibe
  button at all.
  **Download is gone** as a control *(§161 brought offline listening back
  automatically, with no button - see the audio constraint above)*, and gone rather than switched off: the endpoints, the
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
  **They came back in §162, at the owner's direction, as the listener's
  own**: folders are made, named, renamed and deleted by the listener on
  both Save for Later and My Vibes (`folders.kind`), each episode's folder is
  chosen from its row, and none is ever made for them.
<!-- rule:share-link -->
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
<!-- rule:no-social-posting -->
- **FAM posts nothing to anybody's social account, and holds no token.**
  *(SHARING.md.)* Every external destination is reached from the phone: the
  share sheet, or a platform SDK hand-off where their app does the posting with
  the person watching. The server produces the link, the wording and - for
  Instagram and Snapchat, which cannot carry a link as text - the story card.
  This is the correct shape rather than a stage: no OAuth to maintain, no
  tokens to leak, and nothing that can post while somebody is asleep.
<!-- rule:authorship-provenance -->
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
<!-- rule:speed-pitch -->
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
<!-- rule:no-dead-controls -->
- **A control with nothing behind it is worse than no control.** Two were
  deleted rather than repaired in §95 - a share sheet's Audio/Transcript
  toggle that only changed a word in a toast, and a "search messages" bar that
  toasted "demo only". The same rule took the three invented contacts out of
  `index.html`: a social surface that fabricates people is a profile
  fabricating numbers with a worse failure mode, because it says a message was
  sent when none was.
<!-- rule:intro-not-on-stack -->
- **The intro screen is not on the navigation stack, and `goBack()` cannot
  reach it.** *(§96, §97, §98 - the same trap three times.)* It is drawn with
  `showScreen("intro")` by `afterAccount`, `restartFirstRun` and both settings
  entry points, and never pushed. So anything opened *on top* of it - a
  shelf, and the catalogue before §142 made the list the page - pops to
  whatever was underneath, which is SearchFAM,
  and the first run silently loses its remaining steps. Every one of those
  returns by *name* now, recorded when the screen was opened. If a fourth
  screen is ever shown that way, this is the line to read before wiring its
  back button.
<!-- rule:listener-id-server -->
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
<!-- rule:limit-episodes -->
- **A limit counts episodes, not requests.** The free tier's daily allowance
  was spent five times over on one episode, because every tap of it reserved a
  unit - and tapping the episode that is playing is something the interface
  invites (PROBLEMS.md §79). A spend carries the episode's cache key, the first
  one in a window takes the unit and the repeats ride on it. The same rule
  applies to the pace: a request that provably cannot spend a model call - an
  Explore replay, or an episode whose script is already cached, which is what
  makes a voice switch free - is not paced as a generation. And because this
  server has two reasons to answer 429, every refusal says which one it was.
<!-- rule:wipe-derived -->
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
<!-- rule:failures-visible -->
- **Failures must be visible.** Silent success (empty audio, a placeholder tone,
  demo mode mistaken for live) has caused more lost time on this project than
  any real bug. Every fallback must announce itself. *(PROBLEMS.md §51: demo
  mode did announce itself, in an 8.5px chip, and still cost a whole session -
  and it was writing its canned script into the shared cache, so the failure
  outlived the run. Announcing is not enough if the thing keeps a record.)*
<!-- rule:server-says-build -->
- **A running server says which code it is running.** *(PROBLEMS.md §77.)*
  `/api/health` reports `build` (the commit, from `RENDER_GIT_COMMIT`,
  `FAM_COMMIT` or `git rev-parse`, and `"unknown"` rather than a guess) and
  `search_mode_source` (env var or code default). Both exist because a session
  went into inferring them: "the fix is pushed" and "the fix is live" are the
  same sentence from outside, and an env var beats a code default silently and
  outlives every push. Anything else that can be set in two places belongs
  here too.
<!-- rule:verify-not-inspect -->
- **Verify, do not inspect.** *(PROBLEMS.md §52.)* Four consecutive failures on
  a real machine all had the same shape: a check answered a cheaper question
  than the one being asked and then reported OK. "A key is set" is not "the key
  works"; "a cache is configured" is not "this generator may write to it". The
  server now asks Claude at startup whether the credential is actually accepted
  and says so on every tab. Anything that reports readiness must perform the
  real action, not confirm that it was configured.
<!-- rule:voice-address-discovered -->
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
<!-- rule:storage-durability -->
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
<!-- rule:fam-home-dir -->
- **Per-machine state lives in `~/.fam/`, never in the project.** Voice models
  (`~/.fam/voices`) and the API key (`~/.fam/env`, written by
  `python setup_key.py`) are set once and found by every later copy of the app.
  A key in a project `.env` is lost on every new copy, and the workaround for
  that is pasting it again somewhere it should not go. The key is never written
  into source: a commit keeps it in history after the line is deleted.
<!-- rule:metering -->
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
<!-- rule:credentials-fetched -->
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
<!-- rule:zero-spend-staging -->
- **Staging spends nothing, and cannot be configured to** *(§172, at the
  owner's direction).* `FAM_ENV=staging` turns on `spend_guard.py` and no
  other variable turns it off. Every paid credential is removed before
  `Settings` is built (`PAID_CREDENTIALS`, plus each one's plural pool form),
  and every paid switch is forced off (`FORCED`). That includes the keyless
  sources - GDELT and Polymarket cost money at scale, which was the owner's
  ruling. `FAM_SECRETS` is not consulted, in `config.py` or in a later
  `credentials.refresh()`. And **nothing leaves the machine**:
  `socket.socket.connect` refuses every non-loopback address, which is the
  layer that is not a list and so the real guarantee. uvloop connects around
  it, so staging runs `UVICORN_LOOP=asyncio` and `check_loop` reports
  `network_guard: false` if that ever lapses.
  `tests/test_spend_guard.py` derives every credential-shaped name from the
  code and fails on one that is neither scrubbed nor in `NOT_SPEND`, and it
  boots the whole app as staging with every key set and asserts an episode is
  still made. An episode on staging is the sample script in a placeholder
  tone. Real content arrives only by replay (`/api/admin/episodes`,
  `tools/replay_episodes.py`): an import is refused unless the target is zero
  spend, `author` never travels, and `sourced_at`/`fresh_until` travel
  unchanged, so a replayed answer is never presented as newer than it is.
<!-- rule:old-clients -->
- **Every installed client keeps working** *(§172).* Clients send
  `X-FAM-Client: <platform>/<version>`. `releases/registry.json` gives each
  shipped release a status: `supported`, `deprecated` (served, with
  `X-FAM-Client-Status`), or `retired` (426 with a sentence, except
  `/api/health` and `/api/client-status`). An unknown version or a missing
  header is always served - never refuse what the registry does not list.
  Each release has a contract cut by `tools/cut_release.py`: the `/api/`
  routes read out of its code, and the JSON shape of each bare GET, recorded
  in a fresh process on empty databases. `tests/test_client_contracts.py`
  replays every non-retired contract as a superset check. A failure there is
  fixed by keeping the old field or route (or by moving a new shape to
  `/api/v2`), **never by editing the contract**. Web releases are kept whole
  and checksummed in `releases/web/<version>/` and served at `/v/<version>/`
  (manifest files only, no service worker). iOS binaries are not kept in git.
