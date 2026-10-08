# FAM — working context

Read this before making changes. It records where the product is going, which
constraints are load-bearing, and which decisions are already settled, so work
does not drift or re-litigate them.

## How this file works

This is the **core**: every rule, one or two lines each, loaded every session.
The reasoning, history and exact wording behind each rule live in
`docs/claude/*.md`, moved there verbatim (PROBLEMS.md §168); a sentence newer
rules supersede has a `> **Current:**` note at its rule. Every line below
ends in an ID; `grep -n 'rule:ID' docs/claude/*.md` jumps to the full text.

- **Read the full text before changing anything a rule covers**, and before
  reversing or narrowing one. A one-line summary is not permission to
  re-derive a rule from first principles.
- **Adding or changing a rule:** put the reasoning in the right
  `docs/claude/` file under a new `<!-- rule:ID -->` marker, and add one line
  here citing `[ID]`. `tests/test_claude_md.py` fails if the IDs here and there
  disagree, and if this file grows past its budget - history goes in the
  topic files and `PROBLEMS.md`, never here.
- **Finding history:** `PROBLEMS_INDEX.md` lists every PROBLEMS.md section with
  its line number (`python tools/problems_index.py` regenerates it; a test fails
  when it is stale). Read the section by offset rather than searching 700 KB.
- Other files that cite "CLAUDE.md" (a rule, an open problem by number) mean
  this core plus `docs/claude/`; the numbered open problems are in
  `open-problems.md`.

| Topic file | Holds |
|---|---|
| `docs/claude/product.md` | the surfaces, the spec and its amendments, what makes an episode, the writing problem |
| `docs/claude/open-problems.md` | the numbered open problems: voice, myFAM, taste, DailyFAM, social, profile, identity |
| `docs/claude/constraints.md` | every settled constraint, with its history |
| `docs/claude/next-phase.md` | the iOS app and the open decisions |
| `docs/claude/workflow.md` | shipping, picking up a session, the environment's traps |

## Where this is going  (`product.md`)

- **FAM is social information, not social media** (§199, above all else):
  keeping up takes work, so people miss the conversation; FAM turns what they
  care about into audio stories so they can be part of it. [social-information]
- Three surfaces over generated audio: **searchFAM** (ask; voice search via
  mic since §151; "hey FAM"/"what's up FAM" wake word, opt-in, since §158;
  stopping speech shows a five-second **Search now** with an X, never a silent
  search, §159; the heard words are editable in place and Search now
  sends the corrected words, §165; mic beside attach, §182), **myFAM** (four rails over the evergreen bank and a shared live
  story pool, plus **What you missed last week**), **DailyFAM** (named mixes of
  followed subjects `f:nfl` / `f:nfl~Eagles`, never audio; a 05:00 Eastern
  edition writes every episode ahead with EI; public mixes searchable, (+)
  copies one; the server owns the date and length a tap sends), and **Explore**
  (not a tab; the exploreFAM pill on search and beside Made for you, and search's swipe, §203; other listeners' *searched* episodes only, from the cache, `cached_only`,
  never generates; `scripts.author` excludes your own, `scripts.origin` excludes
  non-search surfaces). myFAM and DailyFAM are personalised. [three-surfaces]
- **Decouple script generation from synthesis in time**: pre-generate scripts
  and briefs for likely taps; synthesise on tap. Cached episodes keep their
  audio beside the script (§132); prefetch warms text, never audio. [decouple-script-audio]
- Matching happens at write time too: `CACHE_VECTOR` embeds once at store,
  **on by default** since §107 (`=0` restores the old cache). [match-at-write-time]
- **Latency is answered by starting earlier, never by filling the gap.** [latency-start-earlier]
- **Trending searches**: under the search bubbles, the five questions most
  searched in the last 2h whose episodes are still current; cache only (§190, §192). [trending-searches]
- **Search is Google's shape** (§190): mic and attach inside the bar's right;
  Length (1-5 min, §219) and Voice bubbles never print the choice; back to 2 min on
  every return to the app. [search-bar]
- A search is spell-corrected whole at send; its title never shows a
  misspelling (`autocorrect.correct_text`). [autocorrect-at-send]
- The from-knowledge cover half on search is deleted (§108); search's wait is
  in front of the first word and honest - five loading steps from the
  server's marks, each ≥2s, audio held to the fifth; a replay skips them (§148). [search-no-cover]
- **The spec: type a question, and within about a second audio starts giving
  the answer.** Seconds in front of the first word are wrong. [one-sentence-spec]
- **Amended for search only**, at the owner's direction (§82, §108, §129): EI
  runs before search; nothing is written before retrieval finishes; the writer
  reasons first (`EFFORT=low`). myFAM, DailyFAM and Explore pay none of it -
  their briefs/scripts are built before the tap. `EPISODE_INTELLIGENCE=0`
  restores old latency. No preamble to disguise a wait; no text before its
  material. [spec-amended-for-search]
- A slow answer is a scheduling choice (starting earlier: 18.30s → 0.12s). [slow-is-scheduling]
- Prefetch-on-typing-pause was removed at the user's request; do not re-add. [no-typing-prefetch]
- myFAM drives prefetch (§83, §105): drawing the page schedules a cycle, never
  awaited, warming **briefs**; warming whole scripts stays opt-in. [myfam-warms-briefs]
- **9.30 interface packet** (§181): sign-up rotates today's top three kept
  episodes; myFAM's header button (a bookshelf, §203) opens the A-Z of others'
  cached episodes; Messages is a tab where Explore was; the player's (+) adds
  the episode's topic to a mix. [interface-181]

- **The player is Spotify's layout** (§190): down arrow, picture (4:3, §192;
  centred between GO DEEPER and the title, never missing - `pick_for_player`
  borrows, as do tiles, §214 - and on the mini player, sides cropped, §193), sources by it, GO
  DEEPER pill at the top, share/vibe/save on the right (§192), captions sheet
  slid up, a ⋯ menu; no "exclude from taste" (§171); the searcher shown only
  if `searches_public`. [player-layout]
- **Search DailyFAM opens on an A to Z catalogue** of others' cached episodes,
  lettered (`/api/myfam/catalog`); typing shows the closest (§193). [dailyfam-catalogue]
- **A queue** (§190): Add to / Go to Queue from the player's and a DailyFAM
  tile's ⋯; client state only; plays before the grid. [queue]

- **Explore is a reel** (§203): picture behind, title bottom left, like /
  comment / vibe / share / save down the right, the player's ⋯; no dislike
  anywhere, captions slide up; **comments** keyed `(query, minutes)`,
  read by anyone, written with an account, one level of replies; the
  exploreFAM pill opens it from search and beside Made for you. [explore-reel]
- **VIBE! is a story editor** (§213): a clamped layout, never an image;
  Your story or Close Friends; holding pauses; your story rings your face. [vibe-caption]
- **Group chats** (§213): one row per message to a `g:` thread; graph only. [group-chats]

- **Names swapped for the listener** (§185): the rails screen *shows*
  "DailyFAM", the mixes screen *shows* "myFAM"; code, ids and these docs keep
  the old names. [names-swapped]

## What an episode is  (`product.md`)

- **Satisfied first, curious second**: meet the want that brought them, fully. [satisfy-first]
- **Never withhold** - close the question they came with, then stop. [never-withhold]
- **It is a story**: narrative as structure (because/therefore/but), never
  storytelling as decoration ("picture this", atmosphere). [story-not-decoration]
- **Speak from inside** (no orienting, no justifying) and **land it and stop**
  (last line most concrete; no summary, recap or "to sum up"). [speak-from-inside]
- **Endings do not tease** (reversed): no dangling hook, no rhetorical question,
  no forecasting; say anything unresolved inside the piece. [endings-do-not-tease]
- Every sentence carries information; atmosphere alone is cut. [every-sentence-informs]
- **Titled by what it turned out to be about**: a `<<TITLE: ...>>` line, stripped,
  no extra call; the player opens on a derived title and swaps; a listener's or
  bank tile's title (`titleOverridden`) is never replaced; cached in its own column.
  A *written* live-story tile takes the writer's title, summary and
  `<<CATEGORY:>>` for every later listener (§189). [title-from-content]
- `<<NEXT: ...>>` is a *prediction* of the likeliest follow-up, stored beside the
  script, served free by `/api/next`, offered by Go Deeper; the script never
  gestures at it. Every Go Deeper (Explore included) shows a suggestion - that
  line, else one built from the title - a box, and a 1-5 minute length (§170);
  opening it never stops the episode, and one ending under it starts nothing (§186). [next-is-prediction]
- After an episode: four recommendations, first auto-starts in **15s**; the lead
  is Go Deeper's own suggestion, played as its follow-up (§178), then album next,
  then `topics.rank_next_up` over both inventories; a search box above Back
  stops the countdown; not on Explore / Explore New. [post-episode-grid]
- **One episode, one category** (§209): the writer's `<<CATEGORY:>>` is read
  through `app._episode_category` by tile, player picture, ranking and logged
  tags; events name the heard episode; a written live story ranks under it;
  the writer sees its branch's names; nodes match as in-order phrases; every
  write logs each categoriser's view (`categories_report.py --audit`). [one-category]
- Openings are concrete and open a question - not inverted-pyramid news style. [no-inverted-pyramid]

## The writing problem  (`product.md`)

- **The scripts are not good enough; the writing is the product.** The ending
  rewrite (§48) and the §108 opening changes are unheard - listen for them first. [writing-is-the-problem]
- `python write.py "<query>" --minutes 3` is the loop for improving writing. [write-py-loop]
- Quality came from inputs, not the prompt (§82): EI decides the query, the
  packet dates and grades sources, `DEPTH_BANDS` says what minutes are for. [inputs-not-prompt]
- The third cause (§108): two texts under two conditions; everything trading
  the opening for latency is gone. [third-cause-opening]
- `write.py` prints the brief above the script - diagnose brief vs script
  separately; `--no-ei` compares; `tools/ei_eval.py` is the milestone. [brief-above-script]
- **`examples/` is the strongest lever** - prefer adding an example over a rule. [examples-lever]

## Open problems  (`open-problems.md`)

- **Voice**: Chatterbox is the only voice; Piper deleted. Two honest states -
  Chatterbox speaks, or a placeholder **tone** and everything says so. No hosted
  per-character voices. Nobody has heard an episode in it; that listening test
  is the next move (`RUNPOD_PRODUCTION.md`, `verify_voice.py`). Hard names are
  respelled for the voice (§165, `pronunciation.py`): EI's brief and the
  writer's `<<SAY: Name = respelling>>` lines feed one lexicon; an admin fix on
  /admin is never overridden; only the text handed to the voice changes -
  captions, cache and titles keep the real spelling. Chunks are seeded from
  voice + words; workers are refused on `VOICE_REFERENCE_FINGERPRINT`;
  `verify_voice.py --fingerprint` measures drift (§176). [op-voice]
- **Voice bank** (§147): only searchFAM picks a voice; other surfaces draw one
  per episode and keep it. Voice is not in the script key; audio is keyed on it. [op-voice-bank]
- The cold open is deleted, not disabled; the interface shows an honest wait. [op-cold-open-gone]
- **myFAM**: a tile is a title and an angle, scripted on tap; rails are Made
  for you / Trending / Most played episodes today (24h, §181) / friends. Every choosing
  rail: taste → `ready_first` (a sort, never a filter) → no repeats
  (`topics.is_repeat`); a heard live story is never re-offered as itself (a
  "what's new" follow-up after 6h). Trending is a twice-daily GNews edition
  (05:00/17:00 ET), ten stories written ahead, no fallback, never the live pool;
  it is ranked by `topics.rank_world` on popularity and country only - no
  taste, fatigue, engagement or learned order. Its cards carry no place; its
  View more is grouped by continent, Worldwide first, no Antarctica (§174).
  Crowd rails hold cached episodes only (§141); friends rail reads the follow
  graph and is empty rather than strangers, and ends in "Find new friends" (§182). `DOMAIN_WEIGHT`/`DOMAIN_SHELF_LIFE`,
  `first_seen` is the clock, variety caps are caps not quotas. [op-myfam]
- **Taste model** (§121/§122/§126): share, vibe and save are signals;
  `ENGAGEMENT_WEIGHT` is global per tile, bounded, never on `rank_most_played`
  or `rank_missed`; location joins `familiar_words` and `LOCAL_BOOST`, country
  never ranks; `categories.py` grows the vocabulary (multi-wording and
  subsumption filters against n-gram explosion); `category_seed.py` is a floor
  never a ceiling; `topics.topic_tags` and `_is_specific` are shared by
  `tag_weight` and broad-match; `diversify` keeps declared tags; tests assert
  bounds at realistic size. [op-taste-vocab]
- **Empty deployment**: `taste_source` is `startup` ("Start here") until one
  play; only Made for you is topped up to its floor; the most-played row holds plays only
  and `tools/seed_demo.py` fills it for a demo. [op-empty-deployment]
- **Scoring** (§114, §155): subtag-aware `_affinity`, `RELEVANCE_FLOOR`; a live
  story reaches Made for you **only when it names something followed** (§155
  excludes, reversing the 0.3 damp; `SUBJECT_DEPTH`, `SEMANTIC_NEAR_COSINE`) -
  the floor tops up from evergreen tiles, **never the live pool**. No length control on myFAM - non-search
  episodes are `BROWSE_MINUTES` (2), except a Go Deeper follow-up's own 1-5 (§170). View more shows eight at a time; Refresh
  deals the next eight of the same ranking, then starts again (§165). No language picker (field kept). No wheel
  (§142) - interests page is the searchable list, A to Z (§170); `preferences.topics` stores
  choices; no interest cap. Impressions only ever damp (`FATIGUE_WEIGHT`), never
  become taste; Trending exempt. `UNSHELVED` rankings must be skipped when
  iterating `FILL_ORDER`. One bank for everyone - personalise the order, not
  the inventory. [op-taste-scoring]
- **DailyFAM mixes**: hold subjects/topic ids, never audio; the picker is ranked
  (`rank_bank`, a sort not a filter, does not exclude played); new mixes are
  **public by default**, copies start private; `tools/check_js.py` fails on
  duplicate top-level names; `loadMixes` takes the bank before the 401 branch;
  the new-mix (+) is hidden until `/api/mixes` answers. [op-dailyfam-mixes]
- A mix's **listen time** is when its "ready" push is sent (own zone, only once
  the edition is written), never when it is written; kept even where push is
  off; never shown to others. No suggested mix names. [mix-listen-time]
- YourFAM's row puts friends with vibes up first; a swipe in the viewer goes
  to the next friend's vibes, the picture opens the profile; the Friends page
  has "Invite new users +"; no (+) in YourFAM's header. [friends-vibes-first]
- **Made for you** (§187): subjects and familiar words vouch only in their own
  field; variety never gives way (≤2 per heading, capped top-up); no
  minor-league game from another continent unless followed; market moves on
  any money taste; ranks every held story. Weights (§202): search 1.5, play 1,
  finish 1.5, skip -0.5, pick 2.2, share 2, save 2, vibe 2.5, mix add 2. [mfy-field-variety]
- **Taste is a subject tree** (§202): the most specific subject takes the
  whole signal, each heading above `ANCESTOR_SHARE` per level; major-league
  teams seeded under their leagues, never one whose name is also an everyday
  phrase (`LEFT_TO_GROW`); `taste_tree` draws it. [taste-tree]
- **Chosen interests never fade** (§202): added after normalisation at
  `INTEREST_WEIGHT` (2.0), never decayed or lowered by a skip; facets and
  named subjects both count. [interests-constant]
- **No "not interested"** anywhere (§171): the ranking learns from plays,
  skips and fatigue; `hide` is not an event kind; old rows are ignored.
  Explore's "Interested?" ✕ is a skip, ✓ a pick (§195). [no-not-interested]
- **Pick up where you left off** (§174): episodes under **60% heard** (the real
  length once the player has it, else the minutes) and finished episodes'
  Go Deeper prompts, from the **last 24 hours** only; fewer than four is honest. [pick-up-rail]
- Follows are asymmetric; a friend is the mutual case, derived never stored. [op-follow-graph]
- A friend's profile is its own screen (`screen-person`); `/api/person` returns
  only what they published, a given place included (§219) (a handle resolves for anyone, a bare id only within
  the asker's follows; the response carries no id); new followers announced once (`social.announced`),
  badge cleared by the Friends tab; **85% through counts as finished**. [op-friend-profile]
- Social updates itself: message cursor is a **row id, never a timestamp**,
  and belongs to the client; `bootstrap` announces nothing; send draws
  optimistically. VIBE! = `echo` in code (`/api/vibe` and `/api/echo` are one
  handler; keep `data-echo`/`echoed`); **not on the mini bar**. Shares cost a row,
  not an episode. [op-social-live]
- **YourFAM** (profile) reads only real data; Settings gathers everything
  changeable; identity step after sign-up only; `identityMode`/`introMode` decide
  X, button label and return target; a settings row is an editor, never the
  first run again; interests are **one sideways-scrolling line** under Edit
  profile / Saved for Later on yours and a friend's page (§178) - Edit profile
  holds the five, ranked by `taste`; "Find new friends" is a pill; showing ≠ liking;
  `/api/person` shows pinned or declared-minus-hidden, never history. [op-profile-hub]
- Attachments: extracted at attach time, never on the generation path; failures
  are actionable sentences; an attached episode is **never cached**. [op-attachments]
- Identity: server-minted session; account = credentials on that id, never
  the device - a signed-in browser's sign-up gets a fresh id (§190); listening
  works with no account; email/phone/Google/Apple; **no delivery** means no
  password reset or verification - say so. [op-identity]

## Settled constraints — do not undo without discussing  (`constraints.md`)

Audio and the opening
- **No MP3, no audio files.** Raw PCM streams to the browser. Cached episodes
  keep zlib PCM in `scripts.db` (§132): readable only while its script is,
  production voice only (`keeps_audio`), whole episodes only, capped by
  `AUDIO_CACHE_MAX_MB`, LRU evicting audio only; §134's widening rules;
  `AUDIO_CACHE=0` restores. No download button; the device keeps finished
  episodes in IndexedDB (`OfflineShelf`) and `sw.js` keeps the shell (§161). [no-audio-files]
- **Duration is a ceiling, not a quota** - end early rather than pad (`ALLOW_TOPUPS=1` restores). [duration-ceiling]
- **Two transport gestures, both stay**: draggable bar (clamped to what is
  written) and ±15s. One transport: `setPlayState` alone moves audio and redraws
  all four players. Leaving the player never stops it; the down arrow minimises; `placeNowBar`. [transport]
- **Nothing speaks before its material has arrived, and no setting buys that
  back**: the writer holds brief and evidence before its first token; no tools
  on the speaking call; EI on every episode. Only work *before* the tap may be
  spent on latency. [nothing-before-material]
- **No filler, ever, and no setting for it.** [no-filler]
- **Slurs are the only words removed** ("a slur", `content_filter.py`, §171);
  swearing stays and puts an E before the title. Whole words only;
  `DELIBERATELY_ABSENT` says why a word is not listed. [slurs-only]
- **Situate, never orient**, in the first two sentences: who, what, when. [situate]
- **The opening is decided last**: the writer decides the whole piece before
  opening; first two sentences must answer what was typed. Where something
  stands in the world is the episode; where it stands in our notes never is.
  `OpeningGuard` stays and logs every drop. [opening-last]

Research and truth
- **Every episode is researched** (`SEARCH_MODE=always`). Empty search is a
  ladder - backend, backend without window, GDELT - defined once in
  `research.ladder()`; **no model web search** (deleted §135); no rung raises;
  every fallback recorded; sim-league pages and (on sports) unknown outlets
  are screened out (§178); `NoEvidence` refuses only current-dependent questions,
  refunds, never refuses evergreen or attachments; EI writes `Brief.broader`. [always-researched]
- **EI decides what to search for, before the search**; the brief never asserts
  a fact; **a layer that adds quality must not subtract availability** - every
  failure falls back to the raw query and says so (`Brief.degraded`). [ei-before-search]
- **Recency filters; credibility sorts.** The packet carries date and grade,
  **never the hostname**; relative dates are computed in code; one retry on a
  thin packet, gap named to the writer. [recency-credibility]
- **Started is not finished**: EI may only say a request is `outcome_dependent`;
  status comes from dated evidence; three states incl. `in_progress`; previews
  are evidence of no outcome; take the smaller true reading on contradiction. [started-not-finished]
- **A missed search establishes nothing about the world**: volatile things are
  never supplied from memory; settled things may be or are omitted; the gap is
  never announced or ended on. [missed-not-absent]
- **Live facts** (`live_facts.py`): **never confidently invent a current fact
  without authoritative, fresh evidence; never infer one from absence.** Closed
  status vocabulary; only evidence sets it; resolution from the provider's
  catalogue, never a model; seven lookup outcomes; freshness enforced in code,
  stale data withheld; NFL records, last and next games are counted from the
  provider's schedule (§178). Don't claim live scores until a provider returns them. [live-facts]
- **API-Sports is a plan per sport**: a budget, a tier (`API_SPORTS_TIERS`,
  all free until bought) and an `/admin` row per sport; the provider's own
  count bounds ours; ten products wired (§180). [api-sports-per-sport]
- **Ask outside services when somebody is looking and there is something to
  see** (§191): API-Sports sweeps only `SWEPT_LEAGUES`, only while one of
  their games is on, only if myFAM was drawn recently; other leagues and
  sports are looked up on demand; Finnhub every 2h, round the clock; a
  refused catalogue is not re-asked that day. [sweep-on-demand]
- **The listener's clock, never the server's** (§186): `listener_clock` from
  the device's `X-FAM-TZ` (Settings can pin one), never a location; prefetch
  and editions use the edition's zone; the zone is not in `key_for`. [listener-clock]
- **Local news** (§194): a town's question goes town outlets (RSS, collected
  ahead) → county → Exa on known outlets, never GDELT; only what names the
  place is evidence; an empty town opens with `local_news.gap_line`, composed
  in code, then weather, then county news. [local-news-ladder]
- **Weather** (§194): NWS first, Open-Meteo second; fetched on demand, then
  swept at 05:00/17:00 in each asked place's own time; US warnings asked live;
  worded as a forecast, never an outcome; never prefetched. [weather]
- **GDELT is read from its 15-minute export files, never its search API**
  (§211): one job downloads them; every reader uses the copy on disk;
  nothing a listener does reaches GDELT; polls on GDELT's clock, a named
  file is waited for (§220). [gdelt-exports]
- **A forecast beside every outcome-dependent question** (§211):
  `live_facts.forecast` asks Polymarket by subject; `plan.forecast`, never
  `plan.live`, never evidence. [forecast-beside]
- Every research path records who it read (provenance). [provenance-all-paths]
- **Live captions** publish each sentence as it is voiced (`live_captions.py`),
  keyed on the cache key, say `done`, never generate; sentence timing is measured
  (`starts`). [live-captions]
- Nothing on the player generates an episode except a button. [player-no-autogen]

Caching and prefetch
- **Kept a week** (`CACHE_LIFE_SECONDS`, from `sourced_at`); **current** per
  `ttl_for`; replay surfaces play anything kept, writers ask `get(key)`. [cache-week]
- **A heard episode is kept** (§173): history stores `episode_id` (key +
  sourced, `X-FAM-Episode`) and replays it with `?episode=`, never writing; a
  re-written key archives the old row and audio; history pins for two weeks;
  the volatile/scheduled window is 2h so a repeat search is one episode. [heard-is-kept]
- **TTL comes from what the script was built on, never from the question's
  words** - live status → `outcome_dependent` → window → keyword floor;
  `in_progress` = never current, nor a sports score or update (`recap`/`update`) short of `final` (§182). Do not fix by adding keywords. Prefetch never
  calls `live_lookup` and never warms an outcome-dependent script (DailyFAM
  edition is the stated exception). [ttl-from-evidence]
- **Duration buys depth, not words** (`DEPTH_BANDS`; evidence grows with minutes, §219); shapes, never boxes; a beat
  with nothing behind it is dropped - a test pins that wording. [depth-not-words]
- **Prefetch writes the same cache under the same key**: `pipeline.key_for` /
  `bucket_for` are the only key; a new field that changes an episode goes there
  and nowhere else. Two levels (`brief` default, `script` opt-in); never compete
  with a live listener, never exceed episode/brief/dollar ceilings, never warm
  anything personal, report `None` not `0`. [prefetch-same-key]
- A prefetch candidate carries a reason in words; sources never call a model or
  the network. [candidate-reason]
- **Writer savings change no word and cost no episode** (§179): its
  instructions are one cacheable block (`PROMPT_CACHE`); the Trending and
  DailyFAM editions batch their writers (`EDITION_BATCH`), read by the same
  `_ScriptReader`, and whatever a batch does not answer in time is written
  live from what was already prepared. Nothing a listener waits on is batched. [writer-savings]

Browse surfaces
- **The evergreen bank is for a listener with no account**: `browse_inventory`
  gives a guest live + bank and an account live + the startup set - a swap,
  never a subtraction. Every rail shows four (`SECTION_SIZE`); only Made for you
  is topped up (`RAIL_MINIMUM`) - most-played, missed, Trending and friends
  never invent a tile. Trending is the GNews edition (§139) ranked by
  `rank_world` (outlet count and place: `trending_score`, `WORLD_LOCAL_SLOTS`);
  the live pool's sports sweep on demand while a followed game is on, markets and news every 2h (§191); a
  guest's page is the bank and a guest tap plays only kept audio (403 → sign-up,
  before the GPU); picker and Explore New keep the whole bank; the gate governs
  what is *offered*, not what is *reported*; `_has_account` defaults False. [bank-for-guests]
- **A browse tile is a title and an angle**; one model call per refresh window
  for everyone; a signal is never a result (except a game's `live_line`, drawn
  beside the title); nothing on the page-load path awaits; **an eviction that
  forgets is a creation** - keep lifecycle clocks outside membership. [tile-title-angle]
- A card's one line is the episode's hook, drawn by one function, budgeted in
  Python (`MAX_HOOK`); `STARTUP_TOPICS` is one time-anchored question per facet
  under **Start here**, written ahead at every Trending slot so a card names
  its subject (`STARTUP_WRITE_AHEAD`, §174); `cold` is derived; startup plays never feed
  `popular_facets`; tiles claim no results or digits. [card-hook-startup]
- **What you missed last week** is a rail, not a popup (now: cached episodes
  others played 3-7 days ago that this listener never heard, no live-feed, no
  top-up, §141). [missed-rail]
- Trending is chosen before any personal rail, from its own edition, so it never
  competes with Made for you for tiles (the §114 `WORLD_FLOOR` reservation is history). [trending-floor]
- **Two crowd rows, not blended**: `most_played` is FAM's plays; Trending is the
  world. Trending feeds the bank, live facts feed the evidence; one fetch serves
  everyone; `why_now` is never evidence; ids hash the subject; an empty row is
  a fact about the deployment, never "nothing is trending". [two-crowd-rows]
- A composed story names its **category** (resolved against the tree in code;
  it decides picture and facet word, and corrects a news story's tags) and its query names the cluster's
  **anchors** (`pin_query`, names never headlines) (§188). [trending-category-pin]

Accounts, tiers, sharing
- **An account gates what is kept, never what is heard**; a guest's events are
  never logged (`app._remembers`); the app opens on sign-up; "Continue as
  guest" withdrawn until public (§197); the Profile tab is a door for guests; every gate uses
  `gateActions()` - one sign-up screen. [account-gates-kept]
- **Waitlist** (§183, `WAITLIST.md`): `WAITLIST=1` closes the app to all but
  `active` accounts, enforced server-side; new accounts start `waitlisted`;
  one account, granting flips `status`; place counted in FAM; Viral Loops via
  the outbox, never loses a signup; waitlisted hidden from discovery except
  friends; a shared episode plays for anyone (only what was shared); the landing
  page plays the three sign-up samples as replays (§190), own picture only (§214); a `/waitlist` join is
  always waitlisted (never an admin account, which previews it, §216) and the app's Sign Up goes there (§192);
  a guest at the address goes to `/waitlist`, whose foot signs a member in (§219). [waitlist-gate]
- **Tiers are built; enforced in production only** (`ENFORCE_QUOTAS=1` in `render.yaml`, §207);
  admins are `unlimited`, `/api/admin/plan` moves anyone else; no checkout yet. [tiers-off]
- A refusal names what the listener was doing (`service_label`), composed
  server-side, in the body as well as `X-FAM-Quota`, in the reader's clock. [refusal-wording]
- A tier is what you may spend, never what you may reach: every tier has every
  feature (`entitlements.FEATURES`; moving one is a line plus its test); episodes
  and Explore replays counted separately; a cache hit still counts as an episode. [tier-spend]
- **Save for later is a pointer and a toggle**; download is gone; folders are
  the listener's own (§162), never made for them. [save-pointer]
- **A shared link lands on one episode** (`/s/<id>`): other controls are
  `data-door`s; the key equals the sharer's; head rendered server-side; opens
  counted by the page; "Join FAM for free" always drawn - App Store, else the
  front door (§175) - other doors need `APP_STORE_URL`; no `user_id`; host from the request (`_public_base`,
  `X-Forwarded-Proto`, loopback refused); story cards are PNG files to the share
  sheet. [share-link]
- FAM posts nothing to anyone's social account and holds no token. [no-social-posting]
- **App review** (§222): Report everywhere, block both ways (`moderation.py`, `/admin`);
  a listener's words reach Anthropic only after a yes (`consent.py`). [app-review-safety]
- **Authorship is provenance, never identity**: `PodcastPipeline.author`, never
  on `EpisodePlan` or in `key_for`; first writer keeps it; prefetch writes none. [authorship-provenance]
- **Type: Bricolage Grotesque (headings), Geist (body), Geist Mono (labels)**
  (§204), one font link; Fraunces/Space Grotesk/JetBrains Mono are gone. [typefaces]
- **A speed change must not change the voice** (WSOLA; bypassed at 1x; default 1x). [speed-pitch]
- **A control with nothing behind it is worse than no control**; never fabricate people. [no-dead-controls]
- **The intro screen is not on the navigation stack**; screens opened over it
  return by name. [intro-not-on-stack]
- **A listener id is never accepted from the client** - take it from
  `_listener(request)` (cookie or `Authorization: Bearer`), never a parameter;
  `?user=` is ignored; a browser never requests or reads the bearer token. [listener-id-server]
- A limit counts episodes, not requests; a request that cannot spend is not paced. [limit-episodes]

Operations and honesty
- **A wipe enumerates what is derived** from what it empties: register it in
  `demo_data._forget_what_the_log_taught` (drop cached handles like
  `topics.category_tree`, not just rows); it re-applies the seed; accounts,
  credentials and metering are never wiped. [wipe-derived]
- **Failures must be visible**; announcing is not enough if the thing keeps a record. [failures-visible]
- `/api/health` reports `build` and `search_mode_source`; anything settable in
  two places belongs there. [server-says-build]
- **Verify, do not inspect** - readiness checks perform the real action. [verify-not-inspect]
- **The voice address is discovered** (`voice_control.ladder()`): verified by a
  real call, failing over is not falling back, every switch recorded, worker
  registers itself (host and port), direct TCP preferred behind
  `VOICE_ALLOW_PLAIN_HTTP`, contract version reported never refused, nothing
  starts/stops a pod (runs continuously), `available()` asks the ladder, refusals
  are logged; never substitute a port the pod does not expose; a sample-rate
  mismatch is refused; registration exists only with `VOICE_REGISTRY_TOKEN`. [voice-address-discovered]
- **A server says whether a redeploy erases its listeners** (`storage` in health,
  measured by `st_dev`; `_announce_storage` at boot); the data-path guard is
  derived - **a guard whose subject is enumerated by hand is decorative**. Open
  every new store via `data_path("VAR", ...)` so the test and the Dockerfile's
  disk list cover it. [storage-durability]
- **Staging spends nothing and cannot be configured to** (`FAM_ENV=staging`,
  `spend_guard.py`): paid keys removed, GDELT/Polymarket off, `FAM_SECRETS`
  unread, no connection leaves the machine; real content only by replay. [zero-spend-staging]
- **Every installed client keeps working**: `X-FAM-Client`, `releases/registry.json`
  (supported / deprecated / retired → 426), a contract per release replayed in
  CI; fix a break by keeping the old field, never by editing the contract;
  web releases kept at `/v/<version>/`. [old-clients]
- Per-machine state lives in `~/.fam/`; a key is never written into source. [fam-home-dir]
- Metering records cost at spend time per listener; never one blended cost. [metering]
- A credential is never typed by a human (`FAM_SECRETS`, precedence env >
  FAM_SECRETS > .env > ~/.fam/env). [credentials-fetched]

## iOS and the next phase  (`next-phase.md`)

- The iOS app is a **native client of this API**, not a web view. [ios-native-client]
- Every feature is an API before it is a screen. [api-before-screen]
- Nothing new on the audio path may assume a browser; `fam-audio.js` is the spec. [audio-no-browser]
- `_listener` must be satisfiable by a header (bearer token in the Keychain). [listener-header]
- Opus over a stream is compatible with no-files; account deletion is required. [ios-pressures]
- Server side built (`ACCOUNTS.md`); payment and delivery are not. [ios-server-built]
- Order: hear the production voice → deploy with a GPU → Swift lock-screen spike. [ios-order]
- Open decisions: where to deploy (bandwidth) [dec-deploy]; accounts - answered
  [dec-accounts]; local vs hosted voices [dec-voices]; whether to warm scripts -
  measure hit rates first [dec-prefetch]; embeddings help the ranker, not the
  cache, and `learned_rank` may reorder what cleared the floor, never admit
  below it [dec-embedding].

## How to ship a change (standing instruction)  (`workflow.md`)

Every change ends the same way, without being asked [ship-loop]:

1. `./dev.sh check` — all of it, every time.
2. Rebuild and **republish the preview to the same URL**:
   `https://claude.ai/code/artifact/c8bd86aa-e61e-4262-a1c8-b9c8d8d6645e`.
   It serves `preview/fam-live-artifact.html` (`python preview/build_live_preview.py`),
   published with `capabilities: {"db": {}, "downloads": true}` - both, since
   passing `db` alone revokes `downloads`. From a new
   conversation, read that URL first, then publish with it as `url`.
3. Reply with a short summary and the preview URL.
4. If something cannot be automated, give the exact command.

- Previews - the fixture build `dev.sh check` smoke-tests (`fam-artifact.html`)
  and the live-DB build at the bookmarked URL - are good for layout and flow,
  useless for writing quality or latency. [preview-fixtures]
- **The algorithm documents itself**: `tools/algorithm_docs.py` writes the
  PDF, deck and `ALGORITHM.md` in `docs/algorithm/` from the code; a test fails
  when they are stale; `dev.sh check` rebuilds them. Never hand-edit. [algorithm-docs]
- `./demo.sh` to show or judge the product; `tools/seed_demo.py` fills browse history. [demo-sh]
- Feature branches → `staging` (`fam-staging`, zero spend) → batched PR into
  `Main` (`fam`); both services from `render.yaml`, sharing nothing (`STAGING.md`). [staging-flow]

## Picking up a session  (`workflow.md`)

- Develop and push on the branch the session assigns (the full text names the
  older `claude/search-podcast-audio-generator-ed4br1`, now superseded by
  per-session branches); do not open a PR unless asked. [branch-no-pr]
- Reading order and the reference docs (`docs/`, `MYFAM.md`, `DATABASE.md`,
  `ACCOUNTS.md`, `SHARING.md`, `LIVE_FACTS.md`, ...) are listed in `workflow.md`;
  recent history is in `PROBLEMS_INDEX.md`. [reading-order]
- Setup: `pip install -r requirements.txt` and `pip install playwright`. [setup-deps]
- Run `./dev.sh check` first; it ends `all checks passed` twice with the smoke
  count from `grep -c '^        check(' tools/smoke_preview.py`. [baseline-check-counts]
- The share landing page has its own smoke run (`tools/smoke_landing.py`); CI
  runs all three; a check added to `dev.sh` must be added to
  `.github/workflows/ci.yml` by hand. [landing-smoke]
- CI runs Python 3.12, this container 3.11 - reproduce in 3.12 before blaming the environment. [ci-python]
- **Check CI on `Main` before starting work** (`mcp__github__actions_list` on `ci.yml`). [ci-green-first]
- Fonts differ between here and CI; suspect the environment on layout disagreements. [fonts-env]
- No API key here: writing quality and latency are unverified. [no-api-key]
- `tools/shots.py`, `stall_probe.py`, `compare_search.py` exist because claims were wrong without them. [shots-probes]
- Deleting CSS broke the app twice: use `tools/check_css.py` and `tools/shots.py`. [css-deletion]
- A top-level name declared twice in `static/index.html` silently wins; `tools/check_js.py` catches it. [js-dup-names]
- **A setting is settled only where it is copied** - `.env.example` must agree
  with `config.py`; interface literals pinned to their variable. [settings-copied]
- `PROBLEMS.md` is the engineering log - add to it rather than starting new notes. [problems-log]
- Tests run with no API key and no speech engine. [tests-no-key]
- `diagnose_api.py` for connection failures; `compare_models.py` compares models. [diag-tools]
