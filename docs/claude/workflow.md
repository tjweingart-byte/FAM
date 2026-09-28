# Shipping, picking up a session, and the environment

> Moved verbatim from `CLAUDE.md` (2026-09-28, PROBLEMS.md §167). `CLAUDE.md`
> keeps one line per rule and loads every session; this file keeps the
> reasoning and history and is read on demand. Each rule starts at a
> `<!-- rule:ID -->` marker, and `CLAUDE.md` cites the same ID in brackets -
> `grep -n 'rule:ID' docs/claude/*.md` jumps to it.
> `tests/test_claude_md.py` fails if the two sets of IDs ever disagree.

<!-- rule:ship-loop -->
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
   lets the story card save inside the viewer (§138) - a declaration is the
   full set, so passing `db` alone revokes it. The fixture build
   (`preview/fam-artifact.html`) is still what `./dev.sh check` produces and
   smoke-tests; it is just no longer what the bookmarked link serves.
3. Reply with a short summary of what changed and the preview URL. Not a zip,
   not a wall of files.
4. If something genuinely cannot be automated, say the exact command to run.

<!-- rule:preview-fixtures -->
`DEVELOPMENT.md` documents the whole loop. The preview is the interface running
on fixtures - good for layout, flow and interaction on a real phone, useless
for writing quality or time-to-first-audio, which need the server.

<!-- rule:demo-sh -->
**To show or judge the product rather than change it, `./demo.sh`** (PROBLEMS.md
§50). It reports what the machine will actually do before it starts - canned
script with no API key, placeholder tone with no voice model, dead Explore tab
with an empty cache - refuses to start quietly broken, offers to seed, and then
says where to press on each tab. `python tools/seed_demo.py` writes the history
the browse surfaces need: Explore replays other listeners' episodes and by
design cannot generate one, so on a fresh database it stays empty however much
you tap it.

## Picking this up in a new session

<!-- rule:branch-no-pr -->
Everything is in the repo; nothing of consequence lives in a chat log. Branch:
`claude/search-podcast-audio-generator-ed4br1` — develop and push there, and do
not open a pull request unless asked.

<!-- rule:reading-order -->
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
and **§135**, where an episode's information comes from - the model's own
web search deleted, API-Sports swept on its whole daily allowance with the
score on the card, and a Trending row of real stories ranked by how many
outlets run them, worldwide and region by region; and **§136**, myFAM's
order of operations - taste, then cached first, then no repeats; and
**§137**, DailyFAM mixes that follow subjects - narrowed to a team or
company, each its own dated daily briefing - with a cover; and **§138**, a
smoke check that sampled a 450ms window, and a story card that said "saved"
inside a viewer that had saved nothing; and **§139**, Trending rebuilt as
a twice-daily GNews edition with its ten episodes written ahead and no
fallback source, after the row said "The live sources didn't answer in
time" to everybody who opened myFAM; and **§140**, DailyFAM as a place to
find other people's mixes - search, (+) to add a copy, share a whole mix - and
a narrowing chip that no longer opens the keyboard; and **§141**, the three
crowd rails made cached-only and ranked by listens; and **§142**, the 9.23
second packet - chats that appear when you send, return as a new line,
autocorrect that leaves names alone, Delete chat for one side, two weeks of
listening history, titles that name their subject, a share that asks first, a
follower announced once, the interests wheel replaced by its list, and a
player X that minimises instead of stopping; and **§143**, DailyFAM
written in the background as a daily edition with EI on every episode, and
every cached episode kept a week with the time it was sourced; and
**§144**, the Render failures of 24/09 - an unactivated GNews account, topics
sent to Finnhub's ticker lookup with the key printed in the logs, and GDELT
refusing a boot that asked it dozens of questions in one minute, now paced
one request at a time with episodes first; and **§145**, an added DailyFAM
mix taken back out by tapping its button again; and **§146**, an X on every
"Pick up where you left off" tile that dismisses it for good; and **§147**,
Explore made searches only, a bank of voices chosen on search and drawn at
random everywhere else, and every episode that is not a search fixed at two
minutes; and **§148**,
the loading screen's five steps checked off from the server's own marks via
`/api/progress`, each held at least two seconds, with the audio held until
the last; and **§149**, a typed DailyFAM topic asking for a *kind* of thing -
"Founders lesson of the day" - worded as news about a named thing and searched
as a blog post, now worded so EI can tell, and every daily edition told what
its earlier editions were called so it picks a new one;
and **§150**, `/admin` - a live tracker over every store, derived
from the code so none is left off, with a question box that answers "how many
accounts have over 5 friends" by recipe or by Claude writing one read-only
SELECT, open only to `FAM_ADMIN_ACCOUNTS` or `FAM_ADMIN_TOKEN` - since §153
the page asks for the admin account's email and password itself, and being
signed in to the app no longer opens it; and **§151**, voice
search on searchFAM - the browser's recogniser, the words shown as heard, and
nothing searched until send; and **§154**, the evergreen bank as a guest's
whole myFAM page and an example DailyFAM playlist, neither able to write an
episode or wake RunPod, and Go Deeper cut to this week's unfinished episodes
and this week's Go Deeper prompts; and **§155**, Made for you offering a
live story only when it names something the listener follows - one Eagles
question had been buying every football fixture in the world; and **§156**,
the story pool's sports and markets on a fifteen-minute tick with the news
sources on two hours; and **§157**, the vocabulary sweep every two hours,
and the finding that a game's subject has never reached the tree at all; and **§158**, "hey FAM" opening voice search,
opt-in because it keeps the microphone open; and **§159**, voice search's
send replaced by a "go now" Search now that counts down five seconds and an
X that stops it; and **§160**, tile pictures - one Imagen 4 picture per
branch of the category tree, scenes written with no names in them and every
result checked by Claude for logos and people, `THUMBNAILS.md` for setup; and **§166**, those pictures in the owner's
look - vintage watercolour, carried by reference pictures in
`thumbnail_style/` sent with every Gemini request, with small anonymous
figures now allowed; and **§161**, the 27/09 packet - reference_3 shown as
"Ian" and the voice bank managed on /admin, numbers and abbreviations
rewritten as spoken before the voice (`spoken_text.py`), "not interested"
and save on every myFAM card, /admin signing in on every load, the app's
descriptions rewritten for listeners, a sign-up screen that is the form,
VIBE as 24-hour stories, startup cards named by the episode they became,
and offline listening for finished episodes; and **§162**, the player's
"not for me" removed, vibes kept on the profile for good (only the story
lasts 24 hours), and folders the listener makes on Save for Later and My
Vibes; and **§163**, "Sunday Night Football" resolved in code as the name
of one game (`named_slots.py`) after a recap of it was about an afternoon
game, and the demo page reduced to the phone - full-screen on a phone, with
the follow banner dropping inside it; and **§165**, the 28/09 packet - hard
names respelled for the voice from EI's brief, the writer's `<<SAY:>>` lines
and /admin, voice search's heard words corrected in place, and View more
showing eight with a Refresh that deals the next eight of the same ranking),
`docs/` for the official reference set - `FINANCIAL.md` (unit costs,
scaling, every provider's limits), `BACKEND.md` (latency, where each section
comes from, every external call site) and `DATA.md` (stores, retention,
guardrails, the algorithm) - written from the code, every number labelled,
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

<!-- rule:setup-deps -->
A fresh container has none of the dependencies installed. Setup is two lines,
and the second one is not optional:

    pip install -r requirements.txt
    pip install playwright        # or the browser smoke test skips itself

<!-- rule:baseline-check-counts -->
Then run `./dev.sh check` before changing anything, so you know the baseline is
green rather than assuming it. A complete run ends with `all checks passed`
**twice** - once per preview build - and **eighty-four** named smoke
behaviours each time; anything less means something was skipped, and `dev.sh`
now says so out loud (PROBLEMS.md §49). The number is
`grep -c '^        check(' tools/smoke_preview.py`, so check it rather than
trusting this sentence: it has been wrong before, because a count written in
prose does not fail when somebody adds a behaviour. (It is 84 as of §165,
which added correcting voice search's words and View more's refresh. It was
82 as of §162,
which added filing episodes in folders on both shelves. It was 81 as of §161,
which added a myFAM card being saved or waved off, a friend's vibes playing
as stories, and offline fading what is not on the device. It was 78 as of §158,
which added "Hey FAM" opening voice search. It was 77 as of §151,
which added voice search hearing you and waiting for send. It was 76 as of §148,
which added the loading screen checking off its five steps. It was 75 as
of §147, which added the search page's voice chip. It was 74 as of §146,
which added the X on a Go Deeper tile. It was 73 as of §145, which added
removing an added mix by tapping its button again. It was 72 as of §142,
which replaced the two wheel checks with the player minimising, the Settings
interests list, sharing asking first, listening history's tabs, and deleting
a chat. It was 69 as of §140,
which added DailyFAM's search with its (+) and sharing a mix from its menu.
It was 67 as of §137,
which added a new mix following narrowed subjects and a square mix cover. It
was 65 as of §134,
which added a DailyFAM playlist playing through and then stopping. It was
64 as of §127,
which replaced "Go Deeper fills for a new listener" and added the fixed myFAM
header, the loading screen's cancel, and faces and typing in messages. It was
61 as of §123,
which added the new-mix gate, the topic bank surviving a locked mix list, and
the search page opening on the length it will generate.)

<!-- rule:landing-smoke -->
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

<!-- rule:ci-python -->
- **The gate runs a different Python from the build container**, and that gap
  hid a failing test for days (§106). `.github/workflows/ci.yml` pins 3.12;
  this container has 3.11. A green `./dev.sh check` is therefore not the same
  claim as a green CI, and the difference showed up as order-dependent state
  that only 3.12 exposed. If CI fails on something that passes here, build a
  3.12 venv and run the whole suite in it before concluding anything about the
  environment.
<!-- rule:ci-green-first -->
- **Check that CI is actually green before trusting it.** It was red on `Main`
  for at least ten merges (§106), always the same assertion, and a red board
  stops being read. `mcp__github__actions_list` on `ci.yml` filtered to `Main`
  answers it in one call.
  **It happened again** (§115): nine more merges, one assertion, and that note
  above was not enough on its own. Do the one call before trusting a green
  local run, and do it *before* starting work rather than at the end - the
  answer decides whether a failure you hit is yours.
<!-- rule:fonts-env -->
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
<!-- rule:no-api-key -->
- There is **no API key** in the build container, so writing quality and
  time-to-first-audio cannot be verified here. Tests, the interface checks and
  the browser smoke test all run without one. Anything about *how the writing
  sounds* is unverified until someone runs it with a key.
<!-- rule:shots-probes -->
- The checks answer "does it work", not "does it look right". `tools/shots.py`
  photographs every surface it lists so a refactor can be proved neutral;
  `tools/stall_probe.py` measures browser stalls without a key, and
  `tools/compare_search.py` measures what research actually buys. Each exists
  because a claim was once made without it and was wrong.
<!-- rule:css-deletion -->
- Deleting CSS from `static/index.html` has broken this app twice. Use
  `tools/check_css.py` and `tools/shots.py`, not judgement.
<!-- rule:js-dup-names -->
- **A name declared twice at the top level of `static/index.html` is a button
  that silently does somebody else's job** (§111). Every control here is an
  inline `onclick` naming a global, the later declaration wins, and nothing
  throws. `tools/check_js.py` fails on it now. The same section is the reason
  to distrust a smoke check that only asserts a control is *on screen*:
  `.typed-offer` was always on screen and had stopped doing anything.
<!-- rule:settings-copied -->
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

<!-- rule:problems-log -->
- `PROBLEMS.md` is the engineering log: every problem hit, its cause, its fix,
  and what is still open. Add to it rather than starting fresh notes.
<!-- rule:tests-no-key -->
- Tests run with no API key and no speech engine (`python -m pytest tests/ -q`).
<!-- rule:diag-tools -->
- `diagnose_api.py` explains connection failures; `compare_models.py` compares
  cost, speed and output across models.
