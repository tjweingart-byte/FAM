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

    api.gdeltproject.org
    gamma-api.polymarket.com
    v1.american-football.api-sports.io   (and the sibling sport hosts)
    api.sportsdata.io
    finnhub.io
    www.alphavantage.co

`api.exa.ai` is blocked from the build container too, and Exa works in
production — which is how we know this is a container policy and not
provider-specific.

---

## 1. GDELT — free, no account, do this first

Nothing to sign up for. It is the only one of the four that can be fully
proven in one command.

**Step 1.** Prove it answers:

    GDELT=1 python tools/gdelt_probe.py

The probe checks both modes FAM uses — `artlist` for retrieval and
`timelinevolraw` for the Trending row — and warns on the two things most likely
to be silently wrong: dates not parsing, and URLs not arriving in the field
`research.host_of` reads.

**Step 2.** Turn on the second index for evidence:

    GDELT=1
    GDELT_CROSS_CHECK=1

Every researched episode now asks GDELT as well as Exa. Additive only: it never
replaces the Exa packet, and `gdelt.retrieve` returns `[]` on any failure by
contract. Watch `/api/sources` on a few episodes — `retrievers` should show
both, and the panel should show more outlets than before.

**Step 3.** Turn on the Trending row:

    TRENDING_SOURCE=gdelt

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

## 2. API-Sports — $0 to start, $19–39/mo at scale

**Step 1.** Sign up at api-sports.io. Free tier is 100 requests/day, which is
enough to verify but not to serve.

**Step 2.** Pick the sport this deployment mostly serves. API-Sports is four
separate APIs wearing one brand — different hosts, response shapes and status
codes:

    API_SPORTS_SPORT=american-football   # or football, basketball, baseball

**Step 3.**

    LIVE_SPORTS_PROVIDER=api-sports
    API_SPORTS_KEY=...
    python tools/verify_live.py --domain sports --query "Chiefs game"

A pass prints the resolved entity, the status, the age against the 120-second
sports limit, and the spoken facts. Read them — a pass means the pipe works,
not that the data is right.

**Step 4.** Watch for `status unknown`. That means API-Sports returned a code
`live_sources.SPORTS[...].statuses` does not map, and **nothing downstream will
speak a result while it is true** — which is safe but useless. Add the code to
that sport's table.

**Step 5.** Upgrade when the free tier bites: $19/mo for 7,500 req/day, $29 for
75,000, $39 for 150,000.

**Known limitation to plan around.** "Chiefs game" names no sport, so it falls
to `API_SPORTS_SPORT`. Team-name routing would need a maintained roster of
every team in every league, and a stale one sends an NFL question to a soccer
endpoint — worse than a default you chose. Resolving sport from team wants the
provider's own team search across sports, which is N requests rather than one.
That is the next step if you serve more than one sport seriously.

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
non-commercial**. A monetised FAM needs the paid plan ($11.99/mo) — or switch
to Alpha Vantage, which is the only real argument for it:

    LIVE_MARKETS_PROVIDER=alpha-vantage
    ALPHA_VANTAGE_KEY=...

Alpha Vantage is worse on both axes that matter (25 req/**day** free against
60/min; $49.99/mo entry against $11.99), so take it only if the terms force it.

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

**Step 4.** Polymarket covers election *interest*, never election *results*.
For results you need AP Elections or Decision Desk HQ — both sales-gated with
no public pricing and no open endpoint, which is why both are declared and
unimplemented. Contact them; there is nothing to configure until you have a
contract.

---

## Recommended order, and why

1. **GDELT** — free, no account, and it is the only one that improves *every*
   episode rather than one domain. It also gives the sources panel something to
   prove: two retrievers rather than one.
2. **Polymarket** — free, no account, broadest topical coverage for the least
   work.
3. **API-Sports** — the motivating case, and the first one that costs money.
4. **Finnhub** — narrowest audience of the four; do it when someone asks about
   a price.

Total to run all four: **$0** until API-Sports' free tier bites, then **$19/mo**.

## After each one

    python tools/verify_live.py          # what is configured and whether it answers
    curl localhost:8000/api/health       # live_facts, live_sources, trending, gdelt

`/api/health` distinguishes three states on purpose — configured and
operational, configured but unavailable, and not configured. **None of them
means "there is no live information in the world."**
