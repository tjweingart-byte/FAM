# Turning the live providers on

Step-by-step, per provider. **Every step here has to be run somewhere with
network** — the build container's egress policy blocks all of these hosts, so
nothing below has been executed against a live service.

## Before anything: confirm the block is not yours

    curl -sS "$HTTPS_PROXY/__agentproxy/status"

A `403` on `CONNECT` is an **organization egress-policy denial**, not a
credential or TLS problem. On a normal machine there is no proxy and this does
not apply. If you hit it on a deployment, allowlist the hosts rather than
routing around it:

    data.gdeltproject.org   (GDELT's export files, §211)
    gamma-api.polymarket.com
    api.balldontlie.io
    finnhub.io

`api.exa.ai` is blocked from the build container too, and Exa works in
production — which is how we know this is a container policy and not
provider-specific.

---

## 1. GDELT — free, no account, do this first

Nothing to sign up for. It is the only one of the four that can be fully
proven in one command.

**Step 1.** Prove it answers:

    GDELT=1 python tools/gdelt_probe.py

Since §211 FAM never calls GDELT's DOC search API (one request per five
seconds per address, on Render's shared address: 384 of 384 failed on 1/10).
One background job downloads GDELT's 15-minute export files into a local copy
(`GDELT_EXPORT_DB`), and everything else reads that copy. The probe does one
real sync into a scratch copy and reads it back: volume, and retrieval.

**Step 2.** Turn it on:

    GDELT=1

That is all a deployment needs: the background job keeps the copy (~200
requests a day, whatever the traffic), the story pool reads it, and it is the
last rung of the research ladder when the search comes back empty. No proxy and no
paid plan are needed.

**`GDELT_CROSS_CHECK`** (off by default) searches GDELT on every researched
episode *after* Exa has answered. It was left off on Render in §144: on 24/09
it was on, and every episode waited out a DOC API timeout for nothing. Since
§211 it searches the local copy, so it costs GDELT nothing and the episode a
local read. If you turn it on, watch `/api/sources` for `retrievers` showing
both.

**Step 3.** ~~`TRENDING_SOURCE=gdelt`~~ - **do not set this** (§139, §144).
The Trending row is built from GNews now (`trending_bank.py`). Set, it adds
the old GDELT theme sweep as a second input to the story pool, which already
reads GDELT's local copy directly.

**Step 4, and this is a judgement call rather than a config change.** Look at
the row. The tile questions are currently templated:

    what is actually driving the news about {subject} right now

That is only just on the right side of the rule that a tile carries a question
rather than a headline. If the row reads thin, the fix is one model call per
refresh window turning the top themes into real questions — one call for
everybody, which the shared clock makes affordable. **Decide this with the row
in front of you**, not in advance.

**Also decide:** connecting any source means the bank stops being entirely
hand-written, and CLAUDE.md treats "one bank for everyone" as settled. The 28
written topics have taste in them a generated row will not.

---

## 2. BALLDONTLIE — sports, every league on one key

BALLDONTLIE is FAM's only sports provider (PROBLEMS.md §277, §280): the
scoreboard, the myFAM sweep's live scores, standings, last and next games,
Formula 1, UFC, and player and injury reports (§275).

**Step 1.** Sign up at balldontlie.io. The free plan reads rosters, teams and
games at 5 requests a minute - enough to verify; standings, injuries and
season figures need ALL-STAR, soccer injuries and golf and tennis figures
GOAT.

**Step 2.**

    BALLDONTLIE_API_KEY=...
    BALLDONTLIE_TIER=free          # the plan you bought: free | all-star | goat
    python tools/verify_live.py --domain sports --query "Kansas City Chiefs game"

The key is the switch (`LIVE_SPORTS_PROVIDER` unset means `balldontlie` once
the key is present). A pass prints the resolved entity, the status, the age
against the sports limit, and the spoken facts. Read them — a pass means the
pipe works, not that the data is right.

**Step 3.** Watch for `status unknown`. That means a game's `status_state` was
not one `balldontlie_scores.status_of` maps, and **nothing downstream will
speak a result while it is true** — which is safe but useless.

**Step 4.** Upgrade when the plan bites: an endpoint above the plan is never
asked, and `/admin` counts every request with a row per league. Set
`BALLDONTLIE_TIER` to what was bought on balldontlie.io.

---

## 3. Finnhub — $0 to start, $11.99/mo when you monetise

**Step 1.** Sign up at finnhub.io. Free is 60 req/min.

**Step 2.**

    LIVE_MARKETS_PROVIDER=finnhub
    FINNHUB_KEY=...
    python tools/verify_live.py --domain markets --query "Apple"

**Step 3.** Confirm the delay is being told. The prompt must say *"delayed by
about twenty minutes"* and never *"current"*. `verify_live` prints
`delayed 1200s by design` when it is wired correctly.

**Step 4.** Check the session wording at three different times of day:

| market | the episode should say |
|---|---|
| open | "is trading at" |
| closed | "closed at" |
| unknown | "was most recently at" |

A closing price described as "is trading at" is a small lie a listener catches
immediately. This costs one extra call per lookup and is worth it.

**Step 5, the licensing one.** Finnhub's free tier is **personal,
non-commercial**. A monetised FAM needs the paid plan ($11.99/mo), then
`FINNHUB_PLAN=commercial`. Alpha Vantage, the old fallback, was removed
(PROBLEMS.md §262).

---

## 4. Polymarket — free, no account, and the one with a trap

**Step 1.** No signup. The public read API is keyless.

    LIVE_ELECTIONS_PROVIDER=polymarket
    python tools/verify_live.py --domain elections --query "the election"

**Step 2.** Confirm the guard is intact. Every fact must come back with:

    status   unknown
    kind     prediction-market

and the writer's block must say **THE ARTICLES WIN** rather than "the most
authoritative thing you have been given". A forecast is the newest thing in the
prompt and the least authoritative thing in it; the paragraph every other live
source earns is withdrawn for this one. See PROBLEMS.md §93.

**Step 3 — read this before wiring it to anything.**

A live in-game win-probability line moves with the score, so it *reads* like
the score. Somebody will look at "Chiefs at 94%" and want to shortcut to "so
they're winning". That is PROBLEMS.md §88 returning through a side door.

`status=unknown` forbids it structurally: `unknown` is the status in which no
result may be spoken, so a market can colour an episode and can never close
one. **Do not add a status mapping to this adapter.** If a future change makes
a market able to satisfy an outcome-dependent question, that is the bug.

**Step 3b — the routing this depends on.** Until PROBLEMS.md §93, everything
above passed and no episode ever reached Polymarket: `BRIEF_SCHEMA` kept a
hand-written copy of the routing vocabulary and it did not include `elections`,
so EI could not name the domain. The enum now reads `live_facts.LIVE_DOMAINS`
directly. If you add a domain, add it there and nowhere else — and give the EI
prompt a sentence saying when to choose it, or the model never will. Two tests
enforce both halves.

**Since §211** Polymarket is not only an elections source: `live_facts.forecast`
asks it beside the live lookup for every question EI marks `outcome_dependent`
(except elections already looked up, weather, and town questions). It finds
markets with Gamma's `/public-search` on the brief's subject words, falling
back to the 100 busiest markets. The forecast is rendered after the live
block, credited as "prediction market - a forecast, not a result", and never
counts as evidence (the `NoEvidence` refusal it once fed was deleted in §256).

**Step 4.** Polymarket covers election *interest*, never election *results*.
For results you need AP Elections or Decision Desk HQ — both sales-gated with
no public pricing and no open endpoint, which is why both are declared and
unimplemented. Contact them; there is nothing to configure until you have a
contract.

---

## And the same four again, for the browse page

Everything above connects a provider to **what an episode says** (`live_facts`)
or to the evidence behind it. The same four also feed **what myFAM offers** -
`stories.py`'s pool, which is a separate subsystem on a separate clock. See
`MYFAM.md`.

The good news is that it is almost free to turn on once the credentials above
exist, because the pool is the cheapest place in FAM to put live data: **one
sweep and one small model call per refresh window, shared by every listener.**

**Step 1.** Prove the sweep without spending a model call:

    python tools/stories_report.py --dry

It prints one line per source - `ok`, `off`, `wait`, `--`, `SLOW`, `FAIL` -
with the reason, then the pool and the rails a listener with no history would
see. A source reading `off` names the setting that turns it on.

**Step 2.** Let it compose:

    python tools/stories_report.py

One real model call. Read the tiles. What you are looking for is a **question
worth an episode** rather than a headline, an angle that would still be true
tomorrow, and `(templated)` on none of them - a templated tile means the
composer was unavailable and the pool fell back, which is a working product
and not the one you are turning on.

**Step 3.** The settings, in the order they are worth adding:

    STORIES=1                # already the default
    GDELT=1                  # the attention half, keyless, does the most work
    STORIES_POLYMARKET=1     # keyless; a betting product, so it is opt-in
    BALLDONTLIE_API_KEY=...  # today's games; already set if you did §2 above
    FINNHUB_KEY=...          # what moved; already set if you did §3 above

**What to watch afterwards.** `/api/health` carries `stories`, which reports
per source and includes `degraded` - how many tiles in the pool were templated
rather than written. A `degraded` count that is not zero on a deployment with
an API key means the composer is failing quietly and the browse page is
running at its floor.

**The one thing to check by eye**, because nothing can check it for you: a
pool of six tiles all from one source is a browse page about one corner of the
world. That is a configuration finding rather than a bug, and the fix is the
next provider in the list.

The sports sweep (`balldontlie_scores.make_signals`) follows only the leagues
the owner named (`balldontlie_scores.SWEPT`), only while one of their games is
on and somebody drew myFAM recently (§191); every other league is looked up
on demand.

## Recommended order, and why

1. **GDELT** — free, no account, and it is the only one that improves *every*
   episode rather than one domain. It also gives the sources panel something to
   prove: two retrievers rather than one.
2. **Polymarket** — free, no account, broadest topical coverage for the least
   work.
3. **BALLDONTLIE** — the motivating case, and the first one that costs money.
4. **Finnhub** — narrowest audience of the four; do it when someone asks about
   a price.

Total to run all four: **$0** until BALLDONTLIE's free plan bites, then its
flat plan for every league.

## After each one

    python tools/verify_live.py          # what is configured and whether it answers
    python tools/stories_report.py --dry # what myFAM would offer, without spending
    curl localhost:8000/api/health       # live_facts, live_sources, trending,
                                         # gdelt, stories

`/api/health` distinguishes three states on purpose — configured and
operational, configured but unavailable, and not configured. **None of them
means "there is no live information in the world."**
