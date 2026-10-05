# FAM — Product history: everything the owner has asked for, and where it stands

| | |
|---|---|
| **Status** | Official documentation, v1 |
| **As of** | 2026-10-05, covering `PROBLEMS.md` §1–§204 |
| **Audience** | Anyone being trained on the product who needs to know every decision the owner made, what was tried and undone, and what is true today |
| **Companion docs** | `CLAUDE.md` and [`claude/`](claude/) (the settled rules), `PROBLEMS.md` and `PROBLEMS_INDEX.md` (the log), [`BACKEND.md`](BACKEND.md), [`DATA.md`](DATA.md), [`FINANCIAL.md`](FINANCIAL.md), [`SCALING_TIMELINE.md`](SCALING_TIMELINE.md), [`algorithm/ALGORITHM.md`](algorithm/ALGORITHM.md) |

---

## 1. How to read this

**`PROBLEMS.md` is the engineering log.** Every change of any size gets a
numbered section: what was reported or asked for, what was built, what was
found in review, and what is still unverified. It is about 15,600 lines and
is never rewritten, so an old section can describe something a later one
undid. `PROBLEMS_INDEX.md` lists every section with its line number; read a
section by offset rather than searching.

**A § number is a `PROBLEMS.md` section.** Three quirks:

- Two sections are numbered **64** and two **65**. In this document the second
  pair (line 3232 "A smoke pass over everything forked in today" and line 3295
  "The validated configuration and the shipping default had diverged") are
  written **§64b** and **§65b**.
- **§84–§87 do not exist.** They recorded a continuous-line illustration
  subsystem that was removed from `Main` in its entirety; the gap is kept so
  code comments citing §88 onward stay correct.
- Several sections were renumbered on merge; the number in the file is the
  one that counts.

**The rules in `CLAUDE.md` are the settled version.** Each line there ends in
a rule ID such as `[transport]`; the full text and its history live in
`docs/claude/*.md` under `<!-- rule:ID -->`. Where an older sentence in those
files has been superseded, it carries a `> **Current:**` note. When this
document and a rule disagree, the rule wins (and this document is the thing
to fix). Rule IDs are given in the tables below so you can jump to them with
`grep -n 'rule:ID' docs/claude/*.md`.

**What counts as an owner request.** The log marks them with phrases such as
"at the owner's direction", "the owner's decision", "asked for", "the user's
request", "reported", "packet" (a batch of numbered asks, usually a dated PDF
or note, e.g. "the 9.30 packet") and "reversed". Early sections say "the
user"; it is the same person.

**Two naming traps.**

| What it is | Name in code, ids and these docs | Name on screen today |
|---|---|---|
| The browse page of rails (first tab) | `myfam`, `screen-myfam`, `/api/myfam` | **DailyFAM** |
| The mixes / daily playlists screen | `playfam`, `dailyfam`, `/api/mixes` | **myFAM** |

The two on-screen names were **swapped at the owner's request in §185**;
code, ids, stored history keys and documentation kept the old names, so a
listener's "DailyFAM" is a developer's myFAM. This document uses the
**code names** and says "the rails screen" and "mixes" where it helps.

Earlier renames, for reading old sections: the mixes screen was **playFAM**
until §35; the feed of other people's cached episodes was **dailyFAM** until
§34, when it became **Explore**; "echo" became **VIBE!** on screen in §95
(`echo` in code); the Profile tab became **YourFAM** in §133.

**Status column vocabulary.** *Current* = true today. *Changed by §N* = still
exists in a different form. *Reversed by §N* = undone. *Removed (§N)* =
deleted from the code. *Open* = decided but not built, or waiting on the
owner.

---

## 2. By area

### 2.1 Product vision, the spec, and navigation

| Asked (date if given) | What was decided / built | § | Status today |
|---|---|---|---|
| "It converted the search to an MP3 file" — the problem the owner hit with an earlier build | Never materialise a file: model tokens → sentences → TTS → raw PCM → HTTP chunks → Web Audio. No encoder anywhere. | 1 | Current `[no-audio-files]`. Amended by §132 (cached episodes keep zlib PCM in `scripts.db`) and §161 (the device keeps finished episodes in IndexedDB); still no audio file and no MP3. |
| Length control ("write a 6-minute podcast" varied wildly) | Word budget, pacing controller, trim and top-up | 2–4 | Changed by §23: duration is a **ceiling**, never padded (`ALLOW_TOPUPS=1` restores top-ups) `[duration-ceiling]`. |
| The product in one sentence | "Type a question, and within about a second audio starts giving the answer." Seconds in front of the first word are wrong. | 24 | Current `[one-sentence-spec]`; deliberately amended for **search only** by §82, §108, §129 `[spec-amended-for-search]`. |
| Make the wait go away rather than disguise it | Latency is answered by starting earlier, never by filling the gap; web search made opt-in; faster default model; preamble banned | 24, 25 | Current `[latency-start-earlier]`, `[slow-is-scheduling]`, `[no-filler]`. |
| The thesis: an episode should be a **story** (the user's idea) | Narrative as structure (because/therefore/but), never storytelling as decoration | 29 | Current `[story-not-decoration]`. |
| A demo where every page can be prompted and generate episodes, to judge writing in the app | `tools/seed_demo.py` + `demo.sh`; "listeners may be invented, episodes may not" | 50 | Current `[demo-sh]`. |
| "Would it be embedded into the code so I don't have to paste [the key] every time?" | No: the key lives in `~/.fam/env` (`setup_key.py`), later fetched by `FAM_SECRETS`; a key is never written into source | 53, 72 | Current `[fam-home-dir]`, `[credentials-fetched]`. |
| Episode intelligence before search, accepting a model call in front of the first word | First amendment to the spec, for search only; browse surfaces build briefs before the tap | 82 | Current `[spec-amended-for-search]`, `[ei-before-search]`. |
| Default episode length | Three minutes in ~20 places → `DEFAULT_MINUTES` 2 | 104 | Current. Every non-search episode is 2 min (`BROWSE_MINUTES`, §147). |
| Official documentation of finances, backend and data | `docs/FINANCIAL.md`, `BACKEND.md`, `DATA.md` | 152 | Current. `SCALING_TIMELINE.md` added in §179. |
| "Above all else" (04/10): FAM is **social information, not social media** | Keeping up takes work so people miss the conversation; FAM turns what they care about into audio stories so they can be part of it | 199 | Current `[social-information]`; later copy and features are judged against it. |
| A self-updating document of the algorithm | `tools/algorithm_docs.py` writes a PDF, a PPTX and `ALGORITHM.md` from the code; a test fails when they go stale | 202 | Current `[algorithm-docs]`. |
| A complete financial breakdown of every outside service (05/10) | Published as an artifact; five fixes followed (see 2.15) | 204 | Current. |

**Navigation and the tab bar**

| Asked (date if given) | What was decided / built | § | Status today |
|---|---|---|---|
| A tab bar built around search | Five tabs: myFAM, DailyFAM, search (raised on a disc in the middle, no label), Explore, Messages | 36 | Changed by §38, §133, §181. |
| Messages was a detour | Profile took the fifth tab; Messages became a sheet from the myFAM header | 38 | Changed: Profile became YourFAM (§133); Messages became a tab again (§181). |
| Design packet `yourfam-tab-spec.md` | Profile tab replaced by **YourFAM** (people icon, unread badge) | 133 | Current `[op-profile-hub]`. |
| 9.30 interface packet (30/09) | **Messages is a tab where Explore was**; Explore is no longer a tab | 181 | Current `[interface-181]`. |
| Swap the names of myFAM and DailyFAM | On screen only; code keeps old names | 185 | Current `[names-swapped]`. |

### 2.2 searchFAM

| Asked (date if given) | What was decided / built | § | Status today |
|---|---|---|---|
| Web search was making every episode wait 10–25 s | Off by default, opt-in per request | 24 | Reversed in stages: §55 the question opts in (`SEARCH_MODE=auto`); §76 every episode researched (`SEARCH_MODE=always`) after "49ers game last night" was answered from memory `[always-researched]`. |
| **Prefetch the script while the person pauses typing** (the user's own idea) | 800 ms after typing stops, write the script into the cache (18.30 s → 0.12 s) | 25, 26 | **Removed at the user's request (§27)**, deleted not disabled. Do not re-add `[no-typing-prefetch]`. §182's pause pre-asks only a spell check. |
| Searching from home "just shows the searchFAM page and the user questions whether their search was received" | One full-screen loading screen for every surface ("FAMiliarizing…", chevrons, honest status line) | 58 | Changed by §148. |
| The wait should say what it is waiting for | Honest wait: names the step, counts seconds | 55 | Changed by §148. |
| Loading screen with five steps | Contextualizing / Retrieving / Verifying / Finalizing / Generating, each checked off by a **server mark**; each on screen ≥ 2 s (the owner's floor); audio held to the fifth; a replay skips them | 148 | Current `[search-no-cover]`. |
| A way out of the loading screen | X cancels the request and returns to where the tap came from | 127 | Current. |
| Attach documents, photos and links to a search | Extracted at attach time, never on the generation path; failures are actionable sentences; an attached episode is never cached | 47, 95 | Current `[op-attachments]`. Attaching is paced by the read limit, not the generation one (§95). |
| Voice search, from a sketch | Mic → voice screen (FAM mark, moving lines, words at top); **speech ending is not a request**: a send button | 151 | Changed by §159. |
| "Hey FAM" / "what's up FAM" opens voice search | A wake-word listener, **off until switched on** in Settings, per device; never searches by itself | 158 | Current `[three-surfaces]`. |
| A Google-Maps-style "Go now" | When speech stops, **Search now** fills for 5 s then searches; an X stops the automatic send (never a silent search) | 159 | Current — **reverses §151** at the owner's direction. |
| Fix a misheard word without saying it again | Heard words editable in place; Search now sends the corrected words | 165 | Current. |
| Mic placement | 36 px button beside attach | 182 | Changed by §190 (inside the bar). |
| Search like Google (10.1.26ii, 01/10) | One rounded bar, mic and attach **inside it on the right**, go arrow only when something is typed; **Length** and **Voice** bubbles that never print the choice; length back to **2 min on every return** to the app | 190 | Current `[search-bar]`. |
| A long question should use the whole bar (10.5, 05/10) | Bar turns tall; buttons drop under the text | 203 | Current. |
| The keyboard lets typos through | Keyboard autocorrect turned on; `autocorrect.py` corrects each finished word, conservatively (never proper nouns) | 142 | Changed by §182. |
| Autocorrect that works | The **whole question is corrected at send** (waits ≤ 700 ms); titles never show a misspelling | 182 | Current `[autocorrect-at-send]`. |
| Trending searches | Up to eight chips on focus: searched, cached, current episodes, most played | 182 | Changed by §190, §192. |
| Trending searches shown always, ranked by real searching | One full-width bubble each whenever the box is empty; ranked by **distinct listeners searching it in the last 2 h**; cache only | 190 | Changed by §192: **five**, in a smaller bubble. Current `[trending-searches]`. |
| A voice choice | Search is the **only** surface where the listener picks a voice (chip / Voice bubble, Settings row) | 147 | Current `[op-voice-bank]`. |
| Explore reachable from search | Arrow "Explore?" top right plus a right-to-left swipe that peeks Explore | 195 | Changed by §203: the **exploreFAM pill** replaces the arrow. |

### 2.3 The player

| Asked (date if given) | What was decided / built | § | Status today |
|---|---|---|---|
| Pause, speed, ±15 s skip, length change from the player, a working Go Deeper | Player keeps every sample and drives a cursor; length change regenerates | 16 | Current (transport) — see below. |
| 29/08 field notes: skipping past the written edge froze | Cursor stops 2 s short of what is written; a "written" track on the bar | 17 | Current. |
| The bar should be draggable | Drag (coarse, clamped to what is written) **and** ±15 s (precise) — both stay | 71 | Current `[transport]`. |
| Speed | Default **1x**, remembered; 0.5x and 0.8x added; time-stretched (WSOLA) so pitch never changes; bypassed at 1x | 95 | Current `[speed-pitch]`. |
| Mini bar's pause button did nothing; four players disagreed about playing | One transport: `setPlayState` alone moves audio and redraws all players | 97 | Current `[transport]`. |
| VIBE! on every player (§95) | Removed from the mini bar (the irreversible action beside open/pause/close) | 97 | Current — **reverses §95** for the mini bar. |
| Small fixes from notes | ±15 arrowheads at the side; play triangle centred by centroid; settings icon a gear (was a sun) | 97 | Current. |
| Player icons unlabelled | Labelled | 104 | Changed by §190 layout. |
| A tap on the stage generated a random topic | Removed — nothing on the player generates except a button | 104 | Current `[player-no-autogen]`. |
| Live captions showed a placeholder; sources only at the end | Real captions (`/api/transcript`, never generates); sources fetched while playing | 104, 107 | Current `[live-captions]`. |
| Captions lag | Sentence start times measured; one sentence at a time | 127 | Current. |
| Audio silent with the ringer switch off (iOS) | `audioSession` playback / silent `<audio>` fallback | 127 | Current; unverified on an iPhone. |
| Player X stopped the episode | X minimises; leaving or switching tab never stops a playing episode; mini bar follows every tab (`placeNowBar`) | 142 | Current; X became a down arrow in §190. |
| "Not for me" on the player while a myFAM episode plays | Added | 161 | **Removed by §162** at the owner's direction. |
| Explicit-language marker | An **E** before the title when the script swears | 171 | Current `[slurs-only]`. |
| Add the episode's topic to a mix | Player (+) top right; sources moved to top left | 181 | Changed by §190: (+) is "Add to playlist" in the ⋯ menu. |
| The player like Spotify (01/10, two screenshots) | Down arrow top left; picture behind the title; sources by the title; GO DEEPER pill; share/queue bottom right; captions in a slide-up sheet; ⋯ menu (Share, Captions, Add to playlist, Remove from this playlist, Add to / Go to Queue) | 190 | Current `[player-layout]`, refined by §192–§193. |
| Spotify's "Exclude from your taste profile" | **Not built**: it conflicts with §171's removal of "not interested"; left until the owner says which rule wins | 190 | Open (owner decision); rule `[player-layout]` records "no exclude from taste". |
| Show who searched an episode | Only if that person turned on `searches_public` (new Settings row, **off by default**) | 190 | Current. |
| The 10.2 packet: player rearranged | GO DEEPER at the top where "Now playing" was; picture 4:3; share/vibe/save stacked right; ⋯ adds vibe and save | 192 | Current. |
| A searched episode played over an empty screen; the picture sat too high; the mini player showed headphones | Every episode has a picture (`pick_for_player` borrows); centred between GO DEEPER and the title; mini player shows it, sides cropped | 193 | Current `[player-layout]`. |
| A queue | Add to / Go to Queue from the player's ⋯ and a rails tile's ⋯; client state only; plays before the post-episode grid | 190 | Current `[queue]`. |
| Choppy audio in the background | Queue up to 30 s ahead while hidden | 174 | Current; unverified on a phone. |
| Offline listening | Finished episodes kept on the device (IndexedDB, ≤ 40 / 250 MB); `sw.js` keeps the shell | 161 | Current — **reverses "downloads are removed"** (§104) in a different shape. |

### 2.4 The rails screen (code `myfam`; on screen "DailyFAM" since §185)

| Asked (date if given) | What was decided / built | § | Status today |
|---|---|---|---|
| A personal browse page | **One bank for everyone, personalised order**; four rails on four signals; rails not grids | 32 | Principle current `[op-taste-scoring]`; rails changed many times below. |
| Unfinished business first | "Go Deeper" section at the top: part-heard episodes and open threads; headings that speak ("Made for you, Monday evening") | 37 | Changed — see "Pick up where you left off" below. |
| "A little sideways from that" (exploration rail) | Removed from the page | 43 | Ranker reused as Explore New (§70), which came off the page again in §96 (`UNSHELVED`). |
| "4 threads" — what does it mean? | Internal word removed; row reads **Pick up where you left off** | 45 | Current `[pick-up-rail]`. |
| Pick up where you left off — contents | Topped up from the bank (§45) → empty until there is something, with a one-line `<<SUMMARY:>>` (§127) → **last 7 days**, part-heard plus finished episodes' Go Deeper prompts, no "similar" (§154) → **under 60% heard, last 24 hours** (§174) | 45, 127, 154, 174 | Current per §174. Each tile has a permanent **X** (§146). |
| "Trending" meant two things | `most_played` (FAM's plays) and `world_trending` (the world) are two rows, never blended | 90 | Current `[two-crowd-rows]`. |
| View more on each rail | Opens the same ranking at full length, ready-first | 95 | Changed by §165: **eight at a time, Refresh deals the next eight** of the same ranking. |
| Packet: four rails, live data, variety, zero queue | Live story pool beside the bank; **a tile is a title and an angle, scripted on tap**; one model call per refresh window for everyone; no results on tiles; shelf life and cooldown; variety caps; nothing on page load awaits; friends rail reads the follow graph (**empty rather than strangers**) | 102 | Current `[tile-title-angle]`, `[op-myfam]`. |
| Weekly recap popup (§70) | Replaced by the **What you missed last week** rail | 104 | Changed by §114 and §141. |
| What you missed — widen it (owner's direction) | Offered, or played by others this week, or in the live pool | 114 | Changed by §141. |
| What you missed — verify against stated rules | **Cached episodes other listeners played 3–7 days ago, never heard by this listener, no live-feed provenance, no top-up** | 141 | Current `[missed-rail]`. |
| Trending showed one tile | Trending reserved first (`WORLD_FLOOR`) | 114 | History since §134/§139 `[trending-floor]`. |
| The card's line said how the rail chose it | The line is the **episode's hook**; bank subtitles rewritten as hooks | 116 | Current `[card-hook-startup]`. |
| Populate the app for somebody with no account or interests | Eight time-anchored "startup" questions, one per facet, under **Start here** | 116 | Current `[card-hook-startup]`, `[op-empty-deployment]`; written ahead at every Trending slot since §174 so cards carry names. |
| The generic episodes in every first open (owner, three parts) | (1) the bank **stays** as guardrails; (2) "What FAM can't stop listening to" only real plays; (3) the bank is **for people with no account**; an account gets live + startup set | 125 | Current `[bank-for-guests]`. |
| 9.21.26 II packet (21/09) | Floors on every rail but friends | 127 | **Narrowed by §134**: only Made for you is topped up; the crowd fill removed (it invented plays). |
| 22/09 packet | **Four on every rail** (`SECTION_SIZE`); Trending is the **world's**: chosen first, by popularity and country only, never the bank; "Different picks" deleted | 134 | Current `[two-crowd-rows]`, `[trending-floor]`. |
| Trending from real stories by place | GDELT clusters by outlet count, grouped by region | 135 | Changed by §139. |
| Trending said "The live sources didn't answer in time" | Trending is an **edition**: GNews at 05:00 and 17:00 Eastern, ten stories, ten episodes written ahead; **GNews only, no fallback** (owner); **never the live pool** (owner ruled it out) | 139 | Current `[op-myfam]`. |
| Order of operations: taste → cached first → no repeats (23/09) | `ready_first` is a sort, never a filter; heard tiles are not re-offered | 136 | Current `[op-myfam]`. |
| No repeats on Trending either (owner, same day) | A heard story becomes "What's new with X since …" after 6 h of continued coverage, else the next story | 136 | Current. |
| The three crowd rails (owner verification) | Most played: cached only, by listens; friends: cached episodes friends listened to or created | 141 | Current; most played window changed by §181. |
| A guest's page should cost nothing | Guest sees the bank on every rail, a sample note, "Start here"; a guest tap plays only **kept audio** (403 → sign-up) | 154 | Current `[bank-for-guests]`. |
| No episode-length control on browse | Non-search episodes are 2 min; the header length control removed | 147 | Current. |
| Not interested and save on each card (27/09) | Both added | 161 | Not interested **removed by §171**; save stays. |
| Trending cards and View more | No place on cards; View more by **continent**, Worldwide first, no Antarctica; owner's aim of four per continent **not enforced** | 174 | Current `[op-myfam]`. |
| "Most played episodes today" | Replaces "What FAM can't stop listening to"; window 24 h | 181 | Current. |
| myFAM header button | Messages button became search ("Search myFAM") | 181 | Changed by §193 (opens on an A–Z catalogue) and §203 (a **bookshelf** icon). Current `[dailyfam-catalogue]`. |
| Explore as a rail | "What users are searching" / "Start scrolling" under Made for you | 181 | **Removed by §203**; the exploreFAM pill sits beside Made for you instead. |
| Find new friends from the friends rail | Pill at the end of the rail | 182 | Current. |
| Friends first | YourFAM's face row sits at the top of the rails screen | 195 | Current. |
| Whole titles on tiles (10.5) | No two-line clamp; long titles step down a size | 203 | Current. |

**Tile pictures**

| Asked (date if given) | What was decided / built | § | Status today |
|---|---|---|---|
| Replace line icons with generated pictures, one per category branch, no logos or people | Imagen 4, scene rewritten to drop names, checker, admin review page | 160 | Model changed by §164. |
| Admin page showed nothing painted | `THUMBNAILS=1` is needed for background painting (off by design); Imagen 4 was shut down 2026-08-17 → Gemini 3.1 Flash Image with model discovery | 164 | Current. |
| The owner's look (six reference images) | **Watercolour**, small anonymous figures allowed; references sent with each request | 166 | Refined by §167, §169, §177. |
| No pale border, deeper colour, today's equipment | Full-bleed, deeper tones, modern objects | 167 | Current. |
| A failed picture cost three images; people may face the viewer | One attempt; border cropped in code; doubtful pictures **held for review**; calm expressionless faces allowed | 169 | Current. |
| Every picture the same | Scene's own light and setting; no default props | 177 | Current. |
| Every node its own picture | A tile never wears an ancestor's picture; duplicates held for review | 178 | Current on tiles; the player borrows (§193). |

### 2.5 Mixes (code `dailyfam`/`playfam`; on screen "myFAM" since §185)

| Asked (date if given) | What was decided / built | § | Status today |
|---|---|---|---|
| Daily playlists | A mix holds **topics, never audio**; fresh episodes each day | 33 | Current `[op-dailyfam-mixes]`. |
| Rename and allow typed topics | playFAM → DailyFAM; a mix may hold a typed question | 35 | Current. |
| Visibility | Mixes private by default | 39 | **Reversed by §153**: new mixes are **public by default**; copies start private. |
| Keeping a mix needs an account | Gated (see 2.14) | 70 | Current. |
| Two "add" buttons | Inline "New mix" removed; + top right | 70 | Current; (+) is a circled 34 px button since §184. |
| Suggested topics in id order | Ranked by taste (`rank_bank`), a sort not a filter, includes played | 104 | Current. |
| Privacy switch meant public | Fixed meaning | 104 | Changed by §153: "on" is Public. |
| Typed topic could not be added | Duplicate function name fixed (§111); bank taken before the 401 branch; new-mix (+) hidden until `/api/mixes` answers (§123) | 111, 123 | Current `[js-dup-names]`, `[op-dailyfam-mixes]`. |
| Play all played one episode | A mix is a **queue**: every item in order, ends "You have finished listening to your <name> playlist for today" | 134 | Current. |
| Prototype handoff `dailyfam-mixes-handoff` v118 | Mixes **follow subjects** (`f:nfl`, `f:nfl~Eagles`); naming screen with cover photo; "Anything specific?" up to three specifics; recommended topics; daily prompt built on the server | 137 | Current `[three-surfaces]`. |
| Starter mixes | Follow subjects | 138 | Removed from the page by §184 (still sent to old clients). |
| 9.23 packet | Share a mix (public only, `/m/<id>`); search other people's public mixes; (+) adds a **copy** | 140 | Current; the (+) on an added copy removes it (§145). |
| DailyFAM episodes paid the full wait (owner's direction) | A **05:00 Eastern edition** writes every followed subject ahead; the server owns the date and length a tap sends | 143 | Current `[three-surfaces]`. |
| "Founders lesson of the day" was searched as a blog name | A typed topic is worded as a kind of thing; earlier editions are named so each day is new | 149 | Current. |
| A sample for guests | An example playlist from four bank topics | 154 | Current. |
| No suggested mixes; a listen time | Empty mixes screen has no suggestions; each mix has a **listen time**, when its "ready" push is sent (own zone, only once the edition is written); never changes when episodes are written | 184 | Current `[mix-listen-time]`. Push needs VAPID keys — **no deployment has them**, so no notification is sent anywhere yet. |
| 10.5 packet | Drag topics into order; Edit topics stacks what is chosen at the top | 203 | Current. |

### 2.6 Explore

| Asked (date if given) | What was decided / built | § | Status today |
|---|---|---|---|
| A surface for other listeners' episodes | dailyFAM became **Explore**; it **never generates** (`cached_only`, enforced in the pipeline) | 34 | Current `[three-surfaces]`. |
| Reels | One episode a screen, shuffled deal | 35 | Changed by §195, §203. |
| Explore New | Exploration ranking as its own surface | 70 | Off the page since §96 (`UNSHELVED`); endpoint kept. |
| Explore showed people their own episodes | `scripts.author` excludes your own | 95 | Current `[authorship-provenance]`. |
| Real play count; like / dislike | `scripts.plays`; `/api/rate` | 134 | Dislike **removed everywhere by §203** (still accepted from old clients). |
| Explore was partly browse tiles (owner) | **Searches only** (`scripts.origin`) | 147 | Current. |
| Go Deeper on Explore | Pill under the words | 170 | Current. |
| Not a tab | A rail on the rails screen | 181 | Changed by §195/§203. |
| 10.2 packet | Whole page turns; **"Interested?"** — ✕ records a skip, ✓ a pick (no hiding) | 195 | Current `[no-not-interested]`. |
| Explore as an Instagram-style reel with comments (10.5) | Picture behind; title bottom left; like / comment / vibe / share / save down the right; player's ⋯; ±15 and bar kept; captions slide up; **comments** keyed `(query, minutes)`, read by anyone, written with an account, one level of replies, slurs scrubbed | 203 | Current `[explore-reel]`. |
| The exploreFAM pill | On search and beside Made for you | 203 | Current. |

### 2.7 Go Deeper and the post-episode grid

| Asked (date if given) | What was decided / built | § | Status today |
|---|---|---|---|
| A working Go Deeper | Follow-up carries the parent topic as context (part of the cache key) | 16, 17 | Current. |
| A follow-up the listener does not have to compose | `<<NEXT: …>>` line written by the model, never spoken, offered as a chip | 30 | Meaning changed by §48. |
| Remove the tease (asked twice) | `<<NEXT:>>` is a **prediction** of the likeliest follow-up; the script never gestures at it | 48 | Current `[next-is-prediction]`. |
| What plays next (interface packet, 2026) | Four tiles in a grid, first auto-starts (5 s); feed's own ranking (`rank_next_up`); album next, then NEXT; not on Explore / Explore New | 70 | Countdown is now **15 s** (recorded in rule `[post-episode-grid]`, no separate section). Changed by §178. |
| What algorithm picks the four tiles, and could it be better | Subtags and impression fatigue (see 2.12) | 80 | Current. |
| Popup was bank-only | Draws on the live pool too | 104 | Current. |
| Go Deeper everywhere, with a length | Every Go Deeper shows a suggestion (NEXT, else one built from the title), a box, and **1–5 min** | 170 | Current `[next-is-prediction]`. |
| 9.30 packet | The lead tile is Go Deeper's own suggestion, played as its follow-up; album second; a search box above "Back" stops the countdown | 178 | Current `[post-episode-grid]`. |
| GO DEEPER styling | Yellow pill, capitals | 182 | Current. |
| Go Deeper stopped the episode | Opening it never stops the episode; one ending under it starts nothing | 186 | Current. |
| A queue | A queued episode plays instead of the grid | 190 | Current `[queue]`. |

### 2.8 Writing quality

| Asked (date if given) | What was decided / built | § | Status today |
|---|---|---|---|
| "The first thirty seconds were 'I'm telling you what I'm going to tell you'"; scripts "poor, sometimes inaccurate, and not interesting" | Opener off; prompt rewritten around what makes a briefing good; length is "the listener's time, not a quota" | 23 | Current principle `[writing-is-the-problem]`. |
| Narrated timestamps ("as of Sunday…") | Banned | 25 | Current. |
| Would sample scripts help? (the user) | `examples/` read at import as the house voice | 28 | Current — "the strongest lever" `[examples-lever]`. |
| A story, not decoration (the user) | Opening concrete and opens a question; reverses §23's "open with the answer" (inverted pyramid) | 29 | Current `[no-inverted-pyramid]`. |
| Endings | "Do not end. Widen." — one named thread | 30 | **Reversed by §48** (asked twice): land on the most concrete thing and stop; no hook, no rhetorical question `[endings-do-not-tease]`. |
| Withholding risk | Satisfied first, curious second; never withhold | 31 | Current `[satisfy-first]`, `[never-withhold]`. |
| Quality, consistency, relevance (from use) | Episode intelligence; dated, graded packet; `DEPTH_BANDS`; story shapes, never boxes | 82 | Current `[inputs-not-prompt]`, `[depth-not-words]`. |
| A final score for a game in its third quarter | Situate, never orient; an in-progress shape; three states | 88 | Current `[situate]`, `[started-not-finished]`. |
| A perfect episode that opened by apologising | Opening brief tells the writer research is coming; `OpeningGuard` drops disclaimers | 94 | Current `[opening-last]`. |
| The title was the question re-cased | Model writes `<<TITLE: …>>`, stripped, cached | 104 | Current `[title-from-content]`. |
| "The opening first couple of sentences … confusing" | The from-knowledge cover half deleted; nothing written before retrieval; decide the whole piece first | 108 | Current `[third-cause-opening]`. |
| Summary on cards; a title before the first word | `<<SUMMARY:>>`; provisional title from the brief | 127 | Current. |
| Titles too vague | **Clear first, curious second** | 142 | Current. |
| Numbers and abbreviations read badly | `spoken_text.speakable` rewrites only what the voice is handed | 161 | Current. |
| Startup tile titles named nothing | Card takes the written episode's title | 161, 174 | Current. |
| Hard names said wrong (28/09) | `Brief.pronounce`, `<<SAY: Name = respelling>>`, admin lexicon | 165 | Current `[op-voice]`; unheard. |
| Censorship (owner's words) | **Only slurs removed** ("a slur"); swearing stays with an **E** | 171 | Current `[slurs-only]`. |
| An NFL episode argued with its sources | Factual disagreements resolved silently; sim-league pages screened | 178 | Current. |
| Titles misspelled | EI and writer titles spelled correctly | 182 | Current `[autocorrect-at-send]`. |
| A written live-story tile should name and categorise itself (owner) | `<<CATEGORY:>>`; later listeners see the writer's title and summary | 189 | Current `[title-from-content]`. |

### 2.9 Research and truth

| Asked (date if given) | What was decided / built | § | Status today |
|---|---|---|---|
| Should search be on? | Off (§24) → question decides (§55, widened §57; a bug meant it never ran, §62) → **always** (§76) | 24–76 | Current `[always-researched]`. |
| "The model doesn't seem to be searching" | Attached web-search tool now instructed; `/api/health` reports `build` and `search_mode_source` | 77 | Tool later deleted (§135); health fields current `[server-says-build]`. |
| Quality/relevance (see 2.8) | EI decides what to search **before** the search; recency filters, credibility sorts; dates computed in code; hostnames never in the packet | 82 | Current `[ei-before-search]`, `[recency-credibility]`. |
| Live scores / prices | `live_facts.py` seam; closed status vocabulary; seven lookup outcomes; freshness enforced | 82, 88, 89 | Current `[live-facts]`. |
| Sources visible to the listener | Provenance panel; hostnames never reach a prompt | 91 | Current `[provenance-all-paths]`. |
| Providers | API-Sports, SportsDataIO, Finnhub, Alpha Vantage, Polymarket (always "a forecast, never an outcome"); AP Elections and Decision Desk declared, unimplemented | 91–93 | Current. |
| Nothing written before retrieval | Both research backends retrieve before the writing call; the writing call has no tools | 108 | Current `[nothing-before-material]`. |
| What if search comes back empty? | A ladder; a narrow refusal (`NoEvidence`) only for current-dependent questions, refunded (at the owner's direction) | 109, 110 | Current; ladder changed by §135. |
| Owner's four instructions | **Claude's web search is deleted**; information comes only from Exa, GDELT, Polymarket, Finnhub and API-Sports; API-Sports uses the full daily allowance and shows **the score** (as a live line, never in a title); providers switched on by their keys | 135 | Current `[always-researched]`. Sweep cadence changed by §156, §191. |
| "Sunday Night Football recap" got the wrong game | Named broadcast slots resolved to one game by kick-off | 163 | Current. |
| Is API-Sports enough? (9.30) | Yes for scores, schedules, records (now counted in code from the schedule); not for injuries and lineups. Options listed: SportsDataIO, MySportsFeeds, Sportradar | 178 | Open (owner decision). |
| Ten sports, each on its own plan, counted on /admin | Soccer, American football, basketball, baseball, hockey, rugby, volleyball, F1, MMA, AFL; `API_SPORTS_TIERS`; per-sport rows on `/admin` | 180 | Current `[api-sports-per-sport]`. |
| The listener's clock | `X-FAM-TZ` from the device; Settings can pin a zone; editions use Eastern | 186 | Current `[listener-clock]`. |
| Sweeps every 15 min (owner first asked 2 h for all, then narrowed: sports must stay fresh) | Sports/markets 15 min, news and Polymarket 2 h | 156 | Changed by §191. |
| Outside services asked all day with nobody looking (owner's direction) | Sports: **followed leagues only**, only while their games are on, only if somebody drew the rails recently; Finnhub every 2 h; a refused catalogue not re-asked that day | 191 | Current `[sweep-on-demand]`. |
| Small-town news was invented; no weather (owner's decisions 02/10) | Local news from the town's outlets' RSS → county → Exa on known outlets, **never GDELT**; an empty town opens with a code-composed gap line, then weather, then county news; weather NWS then Open-Meteo, fetched on demand then swept 05:00/17:00 local | 194 | Current `[local-news-ladder]`, `[weather]`. |
| GDELT failing from Render | Breaker and an optional static-IP proxy (`GDELT_PROXY_URL`) | 204 | Current code; **proxy not bought**. |

### 2.10 Voice

| Asked (date if given) | What was decided / built | § | Status today |
|---|---|---|---|
| Choose a voice | `/api/voices`; voice is **not** in the script cache key | 19 | Current. |
| The voice must ship with the app | Piper as a dependency; hosted per-character voices not adopted | 20 | Piper **deleted** when Chatterbox became the only voice (recorded in rule `[op-voice]`, no separate section). |
| A/B two WellSaid voices (Chase J, Kai M) in the product | Built as an alternate engine | 59, 60 | **Removed (§61)**: two episodes exhausted a month's quota. |
| The GPU was 99% idle | Voice moved off the app's machine to RunPod; Resemble's hosted API priced and rejected | 75 | Current. Remote vs in-process kept as a deliberate knob. |
| The voice address kept breaking | Discovered, verified by a real call, workers register themselves | 78, 112, 117, 119, 120 | Current `[voice-address-discovered]`. |
| Stop the nightly pod schedule | Deleted; the voice runs continuously (GPU billed 24 h) | 118 | Current. |
| The GPU is the largest bill (owner) | A cached episode's audio is kept and replayed, never re-voiced | 132 | Current `[no-audio-files]`, `[decouple-script-audio]`. |
| A bank of voices | `voice_bank.py`; search picks; every other surface draws one per episode and keeps it | 147 | Current `[op-voice-bank]`. |
| Manage the bank without a terminal; "Ian" | Voice bank card on `/admin`; `reference_3` is shown as **Ian** | 161 | Current. |
| "Sometimes has a noticeable southern twang" | Investigated (§175) → "Make the fixes": seeded chunks, conditionals prepared once, recording fingerprint, `chatterbox-tts==0.1.7` pinned, recording checks, `verify_voice.py --fingerprint` | 175, 176 | Current. Weights not pinned; **which reference take is canonical is the owner's call**. |
| RunPod GraphQL rate limits (email 30/09) | REST v2 first; owner decision: **stay on pay-per-second workers**; first active worker on launch day | 179 | Current; delete old pod-lookup paths in January 2027. |

### 2.11 Caching and prefetch

| Asked (date if given) | What was decided / built | § | Status today |
|---|---|---|---|
| Share output between users | Shared script cache; personal queries never stored | 11 | Current. |
| Prefetch on typing pause | Built | 25 | **Removed at the user's request (§27)** `[no-typing-prefetch]`. |
| Near-match cache | Built, off | 68 | Turned **on by default in §107** when the owner asked how to reuse topics and titles `[match-at-write-time]`. |
| Prefetch for the browse surfaces | Framework, shipped off | 83 | **On at the owner's direction (§105)**, briefs only; scripts opt-in `[myfam-warms-briefs]`, `[prefetch-same-key]`. |
| Real sentence model | Installed (§131); it did not earn its place in the cache; used by the ranker instead | 131 | Current `[dec-embedding]`. |
| GPU bill | Cached audio kept (see 2.10) | 132 | Current. |
| Every episode lives a week, stamped with when it was sourced (owner's rule) | **Kept** a week; **current** per `ttl_for` | 143 | Current `[cache-week]`. |
| The same question twice was two episodes; history played a third (owner, from a phone) | Volatile window 2 h; every episode has an id; history replays it by id, never writing; pins two weeks | 173 | Current `[heard-is-kept]`. |
| No cached scores | A sports score or update short of final is never current | 182 | Current `[ttl-from-evidence]`. |
| Cheaper writing | Prompt caching; editions batched at 50% | 179 | Current `[writer-savings]`. |

### 2.12 Taste and ranking

| Asked (date if given) | What was decided / built | § | Status today |
|---|---|---|---|
| Interests on the first run | Up to six of eight facets, as tags | 70 | Cap **removed by §99** ("Remove the whole six topic limit - don't acknowledge that at all"). |
| Interest picker | Six most-played facets (§98) → a turning wheel (§99) → Settings wheel = what you listen to (§100) → no filler (§107) → **the wheel is gone; the long list is the page** (§142, owner) → **A to Z** (§170) | 98–170 | Current per §170 `[op-taste-scoring]`. |
| Language page | Stored, inert | 70 | Screen **removed in §100** (field kept). |
| What picks the four tiles, and could it be better? | Two-level vocabulary (subtags); impression fatigue that only damps | 80 | Current. |
| 85% through counts as finished | — | 104 | Current `[op-friend-profile]`. |
| "A random small school college football matchup" (20/09) | Subtag-aware affinity, familiar words, broad-match penalty, relevance floor | 114 | Tightened by §155, §187. |
| Four signals asked in order | Share, vibe, save are taste signals; click-through and `ENGAGEMENT_WEIGHT`; typed **location** (city/region join familiar words; country never boosts); a category tree grown from searches | 121 | Current `[op-taste-vocab]`. Weights changed by §187, §202. |
| An initial topic tree in the database | 180-node seed, a floor never a ceiling | 126 | Current; 90 team nodes added in §202. |
| Everything learned belongs to an account (21/09) | A guest's events are never logged | 127 | Current `[account-gates-kept]` — reverses §70's "interaction log outside the gate". |
| Meaning in Made for you; learn the order from taps | Semantic term; a logistic re-order that may never admit below the floor | 131 | Current `[dec-embedding]`. |
| "No random division II college football game on my Made for you" | Off-subject live stories excluded; top-up from evergreen tiles only, never the live pool | 155 | Current `[op-taste-scoring]`. |
| Vocabulary sweep every 2 h; place sports games in code | Sweep 2 h; the in-code placement was not built (the estimate behind it was wrong); league tagging put to the owner | 157 | Open (league tagging undecided). |
| "Not interested" (27/09) | Hide event | 161 | **Removed by §171**: "The algorithm should work naturally" `[no-not-interested]`. |
| One search about Israel filled Made for you with Middle East sport (owner's weights and rules) | Search 2, play 1, finish 2, skip −0.5, save 2, vibe 2.5; markets reach Made for you; ≤ 2 per heading; no far minor league; subjects vouch only in their own field | 187 | Current `[mfy-field-variety]`; weights changed by §202. |
| Taste as a subject tree; interests never fade (owner) | Specific subjects lead, headings inherit 0.6 per level; chosen interests weigh 2.0, added after normalisation, never decay; search 1.5, finish 1.5, pick 2.2, mix add 2.0 | 202 | Current `[taste-tree]`, `[interests-constant]`. |

### 2.13 Social

| Asked (date if given) | What was decided / built | § | Status today |
|---|---|---|---|
| A social layer | **Echo**: a row pointing at an existing episode | 39 | Renamed **VIBE!** in §95 (`echo` in code) `[op-social-live]`. |
| Echo on every player | Every player | 42 | Mini bar excluded since §97. |
| Interface packet: messages and share | Three invented contacts **removed**; real friends and messages; nine share destinations; Audio/Transcript toggle **removed** | 95 | Current `[no-dead-controls]`. |
| Profile photo crop | Move-and-scale editor | 96 | Current. |
| Friend's profile and followers (owner's 22 items) | Own screen; new followers announced; `/api/person` shows only what they published; 85% = finished | 104 | Current `[op-friend-profile]`. |
| A share link landed on the whole app | `/s/<id>` lands on one episode; everything else is a door | 106 | Current `[share-link]`. |
| Chat didn't update | Polling by row id; optimistic send | 107 | Current `[op-social-live]`. |
| Share link was relative; stories didn't post | Host from the request; story cards are PNG files | 114 | Current. |
| Faces, typing dots, banner | Built | 127 | Current. |
| YourFAM design packet | Hub, messages, chats, friend profiles, Topic screen; interest cap **5** (was 4) | 133 | Current `[op-profile-hub]`. |
| 9.23 second packet | Inbox refresh; return makes a newline; **Delete chat** (one side); share sheet selects then confirms; follower popup once ever | 142 | Current. |
| VIBE as a story for 24 hours (27/09) | Rings and a story viewer | 161 | Current; **§162** keeps vibes on the profile for good. |
| Folders | On Save for Later and My Vibes, made only by the listener | 162 | Current `[save-pointer]`. |
| Own interests on own page | Removed (§174) | 174 | **Reversed by §178**: one sideways-scrolling line. |
| iMessage share; Join FAM | Bare link; "Join FAM for free" always drawn | 175 | Current `[share-link]`. |
| 10.1 third set | Invite new users; no (+) in YourFAM header; friends with vibes first; swipe between friends; picture opens profile | 184 | Current `[friends-vibes-first]`. |
| Invites name familiarize.net | `APP_HOME_URL` | 186 | Current. |
| A caption on a vibe; comments (10.5) | Optional caption on a vibe; comments on Explore | 203 | Current `[vibe-caption]`, `[explore-reel]`. |
| Posting to social accounts | Never | — | Current `[no-social-posting]`. |

### 2.14 Accounts, identity, waitlist, tiers

| Asked (date if given) | What was decided / built | § | Status today |
|---|---|---|---|
| Accounts | Identity is a server-minted session; an account is credentials attached to it; no password reset (no delivery) | 66 | Current `[op-identity]`. |
| What does "Skip for now" cost? (owner's answer) | **Anything kept needs an account; anything heard does not** | 70 | Current `[account-gates-kept]`; event log brought inside the gate by §127. |
| Google and Apple sign-in | Drawn, not connected (§70) → the form is the screen; Google/Apple are the iOS app's (§161) | 70, 161 | Current; Apple/Google wait on the Apple Developer Program (§183). |
| The app should open on sign-up | Opens on sign-up with a guest door | 107 | Current; guest door later withdrawn. |
| One sign-up screen | `gateActions()`; "Skip for now" renamed **Continue as guest** | 114 | "Continue as guest" **withdrawn until public (§197)**. |
| Quota counted requests (production 429s) | Counts episodes, refunds failures | 79 | Current `[limit-episodes]`. |
| Tiers | Built; enforcement **off** (no checkout) | 81 | **Switched on in production by §204** (`ENFORCE_QUOTAS=1` in `render.yaml`); admins unlimited; `/api/admin/plan` moves anyone else `[tiers-off]`. |
| Pre-launch waitlist (owner's spec, `WAITLIST.md`) | App closed until an account is active; email + password; Viral Loops sends email | 183 | Current `[waitlist-gate]`; shared episodes stay open (owner, 01/10). |
| familiarize.net gated | Nothing to build; the owner sets `WAITLIST=1` and `PUBLIC_BASE_URL` in the Render dashboard | 186 | Open (dashboard action). |
| A second account from one browser was refused | An account is its email and password, **never the device** | 190 | Current `[op-identity]`. |
| A waitlist join made an ordinary account | A `/waitlist` join is always waitlisted; the app's Sign Up goes there; profile gains birth date, location, phone | 192 | Current. |

### 2.15 Operations

| Asked (date if given) | What was decided / built | § | Status today |
|---|---|---|---|
| Per-listener cost | `metering.py`; marginal cost never blended with the fixed floor | 73 | Current `[metering]`; GPU line fixed in §204. |
| "The accounts that were created are erased" on redeploy | Every store derived onto `/data`; health measures durability | 107, 114 | Current `[storage-durability]`. Needs a Render disk attached. |
| Remove demo data | `tools/wipe_demo_data.py`, `/api/admin/wipe`; derived state cleared too | 114, 124 | Current `[wipe-derived]`. |
| An admin tracker that is up to date | `/admin` reads the real stores and answers questions | 150 | Current. |
| Admin asks for a password | Email + password, own cookie (§153); every visit (§161) | 153, 161 | Current. |
| A staging deployment that spends nothing, compatible with older app versions | `fam-staging` (zero spend, GDELT and Polymarket off too); client registry, contracts, kept web releases at `/v/<version>/` | 172 | Current `[zero-spend-staging]`, `[old-clients]`, `[staging-flow]`. |
| Instant feedback | Feedback button and `/admin` inbox; reports from the player carry the episode | 175, 178 | Current. |
| Cost changes (RunPod email) | Prompt caching, batched editions, provider counts on `/admin`, `SCALING_TIMELINE.md` | 179 | Current. |
| Five fixes from the financial breakdown (05/10) | Quotas on in production; GPU priced at 4.6× realtime, $0.69/h; Exa fallback $0.015; GDELT breaker + proxy option; licence flags (`GNEWS_PLAN`, `FINNHUB_PLAN`, `commercial_ready`) | 204 | Current; plans and proxy not bought. |

### 2.16 Landing page, intro and brand

| Asked (date if given) | What was decided / built | § | Status today |
|---|---|---|---|
| Fake desktop status bar (9:41, dots) | Removed | 170 | Current. |
| The demo is the phone | At phone width the page is the app | 163 | Current. |
| Sign-up screen | Rotating samples of today's three most-played kept episodes; heading "Listen to anything you want to know about" | 181 | Current; drawn as the rails' card (§186); play in place (§197). |
| App icon | The double chevron | 195 | Current. |
| Entry hand-off | Wordmark folds into the chevron, slides to the rails screen | 196 | Changed by §201: chevron **charges** (copper → yellow) instead of turning; plays **every time the app opens**; glow removed at the owner's ask; a sound effect is planned. |
| Waitlist page explains FAM | "What is FAM", real app screens, founders Ian Solomon and TJ Weingart | 198 | Rewritten by §199, §200. |
| Social information | Hero line "The social information network" | 199 | Current. |
| The real problem | "Being in the know shouldn't be a full-time job"; founders' photo; "© 2026 APALI. All rights reserved." | 200 | Current. |

---

## 3. Reversals and removals

Each was tried, then undone. Deleted rather than disabled is the house habit:
"a knob left behind is an invitation to turn it back on" (it happened with
the cold open twice).

| What | Tried in | Undone in | Why |
|---|---|---|---|
| Prefetch on typing pause | §25 (the user's idea) | §27 (the user's request) | A speculative model call for every abandoned query; better on browse surfaces. |
| Cold open / adaptive opener | §9, §15, §17, §21 | Off §23; **deleted §55** | Eighteen contentless words cannot cover a 30–45 s wait. |
| From-knowledge "answer first" cover | §56 | **Deleted §108** | It wrote the confusing first ten seconds with no brief and no evidence. |
| "Open with the answer" | §23 | §29 | Inverted pyramid leaves a story nowhere to go. |
| "Do not end. Widen." endings | §30 | §48 (asked twice) | Heard episode after episode, it was a tease. |
| Web search off by default | §24 | §55 → §76 | Retrieval became half a second; guessing wrong meant stale answers. |
| Claude's own web search | §77 (attached), §109 (last rung) | **Deleted §135** (owner) | Information comes only from the named providers. |
| `EFFORT=high` | §108 | §129 (owner's explicit direction) | Hidden thinking was the largest wait on search. |
| WellSaid voices | §59 | **Removed §61** | Two episodes exhausted a month's quota. |
| Piper | §20 | Deleted when Chatterbox became the only voice (`[op-voice]`) | It reached listeners by fallbacks nobody chose. |
| Nightly pod schedule | before §112 | **Deleted §118** (owner) | The product was unavailable nine hours a day. |
| "Might like" rail / Explore New on the page | §32, §70 | §43, §96 | Off the page; ranker kept. |
| Weekly recap popup | §70 | §104 | An interruption about nothing; became a rail. |
| Downloads | before §96 | **Removed §104** | Save is a toggle; later offline listening returned in another shape (§161). |
| Language page | §70 | **Removed §100** | Never wired to anything; field kept. |
| Six-interest cap | §70 | §99 (owner) | It made people lie about a seventh. |
| Interests wheel | §99 | **Deleted §142** (owner) | The long list is the page. |
| Mixes private by default | §39 | §153 (owner) | Mixes should be found in search. |
| VIBE! on the mini bar | §95 | §97 | The one irreversible action on a crowded strip. |
| "What FAM can't stop listening to" filled from the bank | §32, §124 | §125 (owner) | The heading claimed plays that never happened. |
| Floors on every rail | §127 | §134 | Topping up the crowd row invented plays; only Made for you is topped up. |
| Trending reserved before personal rails (`WORLD_FLOOR`) | §114 | §134/§139 | Trending is chosen first from its own edition. |
| Trending from the live pool / GDELT | §102, §135 | §139 (owner: GNews only, no fallback) | GDELT could not answer from Render. |
| Not interested (card) and "not for me" (player) | §161 | §162, **§171** (owner) | "The algorithm should work naturally." |
| Dislike on Explore | §134 | §203 (owner) | No dislike anywhere. |
| Explore as a tab | §34–§36 | §181 | Messages took the slot. |
| Explore rail "What users are searching" | §181 | §203 | Replaced by the exploreFAM pill. |
| Voice search requires pressing send | §151 | §159 (owner) | A visible countdown with an X instead. |
| "Continue as guest" | §114 | §197 (owner) | Withdrawn until FAM is public; code kept. |
| Interaction log outside the account gate | §70 | §127 (owner) | Everything learned belongs to an account. |
| Own interests off own profile | §174 | §178 (owner) | Back as one sideways line. |
| Starter mix suggestions | §107, §138 | §184 | Removed from the page (still sent to old clients). |
| Tiers unenforced | §81 | §204 (production only) | One listener could spend ~$2 a minute. |
| Continuous-line illustrations | §84–§87 | Reverted from `Main` | Whole subsystem removed. |
| Share and vibe weigh the same | §121 | §187 (owner's weights), §202 | Owner set the weights. |
| Imagen 4 | §160 | §164 | Google shut it down 2026-08-17. |

---

## 4. Open items

**Unverified — nobody has observed it with real credentials or hardware**

- **Nobody has heard a FAM episode in the production (Chatterbox) voice.** The
  listening test is the first move (`RUNPOD_PRODUCTION.md`,
  `verify_voice.py --fingerprint`) `[op-voice]`, `[ios-order]`.
- **Writing quality and latency are unverified** in the build container: no
  API key. Unheard changes include the ending rewrite (§48), the §108 opening,
  `EFFORT=low` (§129), titles (§142), pronunciation (§165), the startup set
  (§116) and the daily-edition prompts (§137, §149) `[no-api-key]`.
- Most outside providers (GDELT, GNews, API-Sports, Finnhub, Polymarket, NWS,
  Open-Meteo, Viral Loops, RunPod REST v2) have never been called from the
  build container; shapes come from documentation and recorded replies.
- Nothing has been tried on a real iPhone: the silent-switch fix (§127),
  background audio (§174), wake word (§158), Web Push (§184).
- No real month of usage: the scaling model in `FINANCIAL.md` is illustrative.

**Waiting on the owner (decisions)**

- **GDELT**: 384 of 384 requests failed from Render (§191); options put to
  the owner, nothing decided. §204 added a breaker and an optional static-IP
  proxy (QuotaGuard), not bought.
- Whether to add a paid sports-news provider for injuries and lineups
  (SportsDataIO, MySportsFeeds, Sportradar) (§178).
- Spotify's "Exclude from your taste profile" versus `[no-not-interested]` (§190).
- Tagging sports games with their league in code (widens Made for you) (§157).
- Which of the two `reference_3` recordings is canonical (§176).
- Whether to warm whole scripts, once hit rates exist `[dec-prefetch]`.
- Where to deploy for bandwidth, local versus hosted voices `[dec-deploy]`, `[dec-voices]`.

**Waiting on purchases or dashboard settings**

- **Licences to buy before charging money**: GNews' free plan is for
  development, Finnhub's for personal use, Open-Meteo's keyless endpoint is
  non-commercial; `commercial_ready` on `/api/health` reports it (§204).
  API-Sports plans per sport are on the free tier (§180).
- **Payment / checkout is not built**; "See plans" says so (§81, §204).
  **Delivery** (email/SMS) is not built, so no password reset or verification
  `[ios-server-built]`.
- Render dashboard: `WAITLIST=1` and `PUBLIC_BASE_URL=https://familiarize.net`
  on production (§186); a persistent disk attached at `/data` (§114, §119).
- Rotate the Finnhub key that was printed in Render's logs (§130, §144); the
  GNews account needed activating (§144).
- VAPID keys for push notifications (§184); `OPEN_METEO_API_KEY` and outlet
  registry runs for local news coverage (§194); `THUMBNAILS=1` for background
  painting (§164).
- The founders' photo shipped in §200; a sound effect for the intro is planned (§201).
- RunPod: delete the v1 and GraphQL pod-lookup paths in January 2027 (§179).

**Built but waiting on the iOS app**

- The iOS app is a native client of this API, not a web view
  `[ios-native-client]`; Opus over a stream, APNs push, `SFSpeechRecognizer`
  for voice search, and offline listening are all still to be built there.
