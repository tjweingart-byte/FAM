# FAM — Scaling timeline: when to change each service

| | |
|---|---|
| **Status** | Official documentation, v1 |
| **As of** | 2026-09-30 (§179) |
| **Audience** | Founders, anyone approving spend or planning a launch |
| **Companion docs** | [`FINANCIAL.md`](FINANCIAL.md) (the unit costs and the arithmetic behind every number here), [`BACKEND.md`](BACKEND.md) (what calls what) |

This is the plan for each outside service FAM uses: what it is on today, the
exact point at which to change it, what to change it to, and what that costs.
Every number is an **estimate** from `FINANCIAL.md` §4 unless it says otherwise.
Re-check list prices before buying.

## How to read it

A change is triggered by whichever comes first:

- **a stage**, which is about the product (before launch, at launch);
- **a number you can see**, which is about real use. Most of these are on the
  admin page (`/admin`, **Outside services, requests per day**, §179), in
  `python tools/usage_report.py --days 30`, or on `/api/health`;
- **a date**, when a provider is retiring something.

The MAU figure beside each trigger is where the model in `FINANCIAL.md` §4
expects the number to be crossed. **The number wins over the MAU** - the MAU is
a forecast; the number is what happened.

## The stages

| Stage | Means | Rough MAU |
|---|---|---|
| **0 · Now** | Private testing. No public sign-ups, no money taken | < 100 |
| **1 · Before launch** | The last things to do before the public can tap. Required whatever the traffic | — |
| **2 · Launch** | The public can use it; the first real month | 100 – 1,000 |
| **3 · Growing** | Real, steady daily traffic | 1,000 – 10,000 |
| **4 · Scale** | One server and one disk stop being enough | 10,000 – 100,000 |

## Dated deadlines (do not depend on users)

| Date | Service | What happens | Action | Status |
|---|---|---|---|---|
| 2026-10-27 | RunPod GraphQL | Limited to 43,200/day, 3,600/hour, 180/minute | None: FAM makes at most ~1,440/day per server, and only as a fallback | Covered |
| **2026-11-15** | RunPod REST v1 | `rest.runpod.io/v1` answers 410 Gone | Pod lookup asks REST v2 first (`RUNPOD_API_URL`) | **Done (§179)** |
| January 2027 | RunPod GraphQL | Retired | Delete the v1 and GraphQL rungs in `voice_control.py` (`_pods_over_rest`, `_pods_over_graphql`) and their settings | To do in January |

---

## 1. RunPod — the voice (Chatterbox on a GPU)

**Today:** serverless, pay-per-second flex workers (`REMOTE_VOICE_TRANSPORT=runpod`),
24 GB class at ~$0.69/h. $0 while idle; the first episode after a quiet spell
pays a cold start (boot plus a ~10 s model load). **The owner's decision on
2026-09-30: stay on pay-per-second for now.**

| When | Trigger | Action | Cost change |
|---|---|---|---|
| **Stage 0 · now** | Nobody has heard an episode in the production voice | Run the listening test (`RUNPOD_PRODUCTION.md`), and measure the speed on the card you would rent: `python verify_voice.py`. Every GPU-hour figure below assumes 4.6x realtime (measured on an RTX 4090) | $0 |
| **Done (§207)** | `usage_report.py` under-reported the GPU about 70x | Defaults are now `SYNTHESIS_REALTIME_FACTOR=4.6`, `GPU_USD_PER_HOUR=0.69`; override on Render if the rented card measures differently | $0 |
| **Stage 1 · before launch** | A peak of more than ~4 people hearing new episodes at once outruns one card | Set the endpoint's **Max workers** to at least 3, and turn **FlashBoot** on | $0 until used |
| **Stage 2 · launch day** | The public can tap, and a cold start puts 10+ s in front of the first word - against the one-second spec | **Add one active worker** (always warm, ~$0.47/h) and keep flex workers for peaks | **+~$343/mo** |
| **Stage 2, cost-only alternative** | If launch is private enough to live with cold starts: the day GPU time reaches **~16 hours a day** (~7k MAU) | Add the active worker then: at that point it is cheaper than flex as well ($11.28/day vs $0.69/h) | net saving from then on |
| **Stage 3 · ~10k MAU** | Average ~1 card busy, peak ~3 | Keep **active workers = average cards busy**, flex covers peaks. Compare committed pods or reserved pricing against the measured GPU line | ~$500-600/mo total GPU |
| **Stage 4 · ~100k MAU** | ~7 cards busy on average, ~15-20 at peak | Active workers at the average, flex for peaks. Re-quote RunPod against Modal (~$0.80/h L4) with real numbers | ~$3,000-3,500/mo GPU |
| **Any stage** | RunPod reliability disappoints (failed synth, long queues) | Move to **Modal**: similar price, the worker is one small container (days of work) | ≈ $0 |
| **Never** | — | A hosted per-character voice (~$25-50 per million characters): 5-10x the GPU bill at every size (`VOICE_OPTIONS.md`) | — |

## 2. Claude (Anthropic) — the brief and the writer

**Today:** the writer on `claude-sonnet-5` ($2 in / $10 out per million
tokens); the brief, tile composer, category placer and thumbnail calls on
`claude-haiku-5-5` ($0.10 / $0.50, §227). About $0.03 per new episode, $0 per
replay. Prompt caching on the writer's instructions and
batch pricing on both editions are **on (§179)**.

| When | Trigger | Action | Cost change |
|---|---|---|---|
| **Done (§179)** | — | Prompt caching on the writer (`PROMPT_CACHE=1`) | about -11% (~$0.0045) per episode once traffic keeps it warm |
| **Done (§179)** | — | Batch pricing for the Trending and DailyFAM editions (`EDITION_BATCH=1`) | -50% on those writer calls |
| **Stage 0 · now** | Writer calls are rarer than one per five minutes (pre-launch) | Optional: `PROMPT_CACHE_TTL=1h`, so a sparse call reads rather than re-writes the cache | ± cents |
| **Stage 1 · before launch** | Rate limits are per organisation and a pool of keys adds nothing | Check the usage tier in the Anthropic console; request the next tier | $0 |
| **Done (§207)** | One listener could spend ~$2/min | `ENFORCE_QUOTAS=1` on production (free tier: 5 episodes/day, ≤ $0.50/day each); admins unlimited, `/api/admin/plan` for anybody else | caps the worst listener at ~$15/mo |
| **Stage 2 · after the first real month** | Real miss rate and cost per episode exist in `metering.db` | `python tools/usage_report.py --days 30`; replace every estimate in `FINANCIAL.md` §4 | $0 |
| **Stage 3 · ~1k MAU** | Search miss rate above ~60% | Tune the near-match threshold; trial the canonical key (`CACHE_SEMANTIC_KEY=1`, ~$0.0002/request) | -10 points of misses ≈ -$50/mo at 1k, -$5,100/mo at 100k |
| **Done (§227)** | — | The brief, composer, placer and thumbnail calls on Haiku 5.5 (`SMALL_MODEL`); the writer unchanged. **Confirm with `tools/ei_eval.py` once a key is available** - quality comes from the brief (§82); `EI_MODEL=claude-sonnet-5` restores it | about -30% of the Claude bill at every size (estimate) |
| **Stage 4 · ~100k MAU** | Writer output tokens per minute near the tier's limit (~100 new episodes/min at peak) | Next usage tier, or Priority capacity | per Anthropic |
| **Never** | — | Shorten the style example or lower the writer's effort to save money: the writing is the product, and caching already makes the example nearly free | — |

## 3. Exa — every episode's research

**Today:** pay-as-you-go (~$0.015 per search, 1-2 per new episode). ~10
requests/s per key. Counted on `/admin`.

| When | Trigger | Action | Cost change |
|---|---|---|---|
| **Stage 1 · before launch** | Exa stops when the balance runs out (research falls back to GDELT and says so) | Card on file with auto top-up | $0 |
| **Done (§207)** | The `research.COST_PER_SEARCH` fallback ($0.005) under-reported | Raised to 0.015 (only used when Exa omits `cost_dollars`) | $0 |
| **Stage 4 · ~100k MAU** | `/admin` shows Exa above ~5 requests/s at peak (~430k/day), or 429s in *failed* | Add a second key (unlike Anthropic, a second Exa key adds headroom) | $0 fixed |

## 4. Render — the web host, its disk and its bandwidth

**Today:** Starter ($7/mo: 512 MB, 0.5 CPU, one process), a 1 GB disk, the
workspace plan's included bandwidth (Hobby: 5 GB/month).

| When | Trigger | Action | Cost change |
|---|---|---|---|
| **Stage 1 · before launch** | 512 MB with a ~313 MB peak leaves room for ~20-40 simultaneous streams | Upgrade to **Standard** ($25, 2 GB) | +$18/mo |
| **Stage 1 · before launch** | A 1 GB disk holds ~250 minutes of kept audio, and a full disk stops every database | Grow the disk to **10 GB** and raise `AUDIO_CACHE_MAX_MB` to ~8000 | +$2.25/mo |
| **Stage 2 · ~100 MAU** | Uncompressed PCM is 2.65 MB/min; 5 GB is ~1,900 listening-minutes a month | Pay $0.15/GB overage, or a workspace plan with more included (Pro: 25 GB) | ~$3-35/mo |
| **Stage 3 · ~1-10k MAU** | Memory or CPU pressure on Standard | **Pro** ($80, 4 GB). Do **not** add `--workers`: API-Sports budgets, the story pool and prefetch ledgers are per process | +$55/mo |
| **Stage 3 · before 10k MAU** | Bandwidth becomes the second-largest line (~$360/mo at 10k) | Ship Opus over the stream (`IOS_APP.md`): ~13x less bandwidth | -~90% of the bandwidth line |
| **Stage 4 · past one Pro instance** | A service with a disk runs as one instance | The database move in section 5 | see section 5 |

## 5. The database (SQLite on the Render disk)

**Today:** eighteen SQLite files on one disk; kept audio inside `scripts.db`.
RunPod holds no database.

| When | Trigger | Action | Cost change |
|---|---|---|---|
| **Stage 1 · before launch** | — | Grow the disk (section 4) | +$2.25/mo |
| **Stage 3 · ~5-10k MAU** | Render Pro is the largest single instance; plan the move **before** it is needed | Design SQLite → **Render Postgres** (Pro-4gb, $35/mo), in-process caches → Redis, kept audio → object storage | $0 until built |
| **Stage 4 · ~10k MAU** | One instance is no longer enough, or the disk exceeds ~50 GB | Migrate: Render Postgres + **Cloudflare R2** for audio ($0.015/GB, $0 egress). Serving audio *from* R2 touches the no-audio-files rule and needs that discussion first | +$40-65/mo |
| **Stage 4 · ~100k MAU** | Postgres CPU/RAM | A larger Postgres plan | ~$100-200+/mo |

## 6. API-Sports — live scores and score lines on cards

**Today:** ten sports (American football, soccer, basketball, baseball,
hockey, rugby, volleyball, AFL, Formula 1, MMA), **each on its own free plan:
100 requests/day per sport** (PROBLEMS.md §180). Each sport is its own
API-Sports subscription on the same key, billed and limited separately, so
each has its own day and its own row under API-Sports on `/admin`. Since
§191 the myFAM sweep reads a followed sport's card once a day and then only
while one of its followed leagues' games is on and somebody is looking (every
15 minutes at most); volleyball, rugby and AFL are never swept, only looked up
by the episodes that ask.

| When | Trigger | Action | Cost change |
|---|---|---|---|
| **Stage 1 · before launch** | The sports the product leads with (NFL, soccer) should not have 14-minute-old scores | **Pro, $19/mo per sport: 7,500/day** for those sports only | +$19/mo per sport |
| **Any stage, earlier** | A sport's row on `/admin` shows *today* above ~80 on most days, or its lookups are refused late in the day | That sport to Pro | +$19/mo |
| **Stage 3-4** | A sport above ~6,000/day | That sport to Ultra ($29, 75k/day) or Mega ($39, 150k/day) | +$10-20/mo |

**Changing a sport's plan** - the whole procedure:

1. **Buy it** on API-Sports' dashboard (dashboard.api-football.com), on the
   product for that sport. The key does not change.
2. **Tell FAM**: set `API_SPORTS_TIERS` on the service in Render, naming only
   the sports that are not on `API_SPORTS_TIER` (the default, `free`):
   `API_SPORTS_TIERS=american-football=pro,football=pro`. Tiers are `free`,
   `pro`, `ultra`, `mega`. Never on staging, which spends nothing.
3. **Redeploy** (Render restarts on a changed variable).
4. **Check** `/admin`: the sport's row says the new plan and allowance, and
   once it has made a request it shows what API-Sports itself reports left.
   A plan API-Sports does not report is flagged on that row in red.
   `python tools/verify_live.py --domain sports` asks every sport's `/status`
   and prints each plan as API-Sports has it.
5. **Downgrading** is the same in reverse: change the plan on the dashboard,
   then remove the sport from `API_SPORTS_TIERS`.

`API_SPORTS_SPORTS` limits which sports are used at all (empty is all ten);
`API_SPORTS_DAILY_REQUESTS` caps each sport below its plan for several
workers sharing one; `API_SPORTS_LOOKUP_RESERVE` keeps a share of each day
from the sweep for episode lookups (0 by default, as §135 directs).

## 7. GNews — the Trending edition

**Today:** free (development use), 100/day, 10 articles per call. FAM spends
~52/day under a guard of `GNEWS_DAILY_REQUESTS=90`. No fallback: over quota
means an empty Trending row.

| When | Trigger | Action | Cost change |
|---|---|---|---|
| **Stage 1 · before charging money** | The free plan's licence is for development | **Essential, €49.99/mo: 1,000/day, 25 articles**. Set `GNEWS_DAILY_REQUESTS=950` | +~$54/mo |
| **Any stage, earlier** | `/admin` shows GNews *today* above ~80, or a third edition / more countries is wanted | The same Essential plan | +~$54/mo |

## 8. Finnhub — market facts and market-move cards

**Today:** free, 60 requests/minute, **personal / non-commercial** use only.

| When | Trigger | Action | Cost change |
|---|---|---|---|
| **Stage 1 · before charging money** | The licence, not the rate | A commercial plan (quote-based; ~$11.99/mo quoted in `PROVIDER_ROLLOUT.md`) | +~$12/mo |
| **Any stage** | `/admin` shows Finnhub failures (429) | The paid plan's higher rate | per quote |

## 9. GDELT — the fallback research rung and story discovery

**Today (§211):** free, keyless, read from GDELT's 15-minute export files by
one background job (~2 requests per 15 min, ~200/day, whatever the traffic)
into a 24-hour copy on the data disk. Everything else reads that copy; no
listener's tap reaches GDELT. The DOC API's 1 request per 5 s per shared IP
no longer applies.

| When | Trigger | Action | Cost change |
|---|---|---|---|
| ~~**Now (§191: 384 of 384 failed on 1/10)**~~ | ~~`gdelt.paused_until` set~~ | ~~A static-IP proxy (QuotaGuard Static)~~: not needed since §211, which moved GDELT to its export files | $0 |
| **Any stage** | `/api/health` → `gdelt.failures_in_a_row` rising, or `newest_age_seconds` over an hour (the copy is stale and reported as an outage) | Check `last_error`, and run `python tools/gdelt_probe.py` for one real sync. No paid plan; none needed | $0 |

## 10. Polymarket — election and prediction facts

**Today:** free, keyless, no published cap; asked every 30 minutes, lookups
cached 5-15 minutes. Since §211 an episode asks it for every question EI marks
`outcome_dependent` (a forecast beside the live lookup, never evidence), not
only elections.

| When | Trigger | Action | Cost change |
|---|---|---|---|
| **Any stage** | `/admin` shows Polymarket *failed* > 0 for a day | Check for an unannounced API change; the episode already continues without the fact | $0 |

## 11. Google image model — tile pictures

**Today:** `gemini-3.1-flash-image` at ~$0.067 per image, capped at
`THUMBNAILS_DAILY_IMAGES=60` (≤ ~$120/mo). One picture per category branch,
shared by everybody, so it does not grow with listeners.

| When | Trigger | Action | Cost change |
|---|---|---|---|
| **Any stage** | The category tree grows faster than 60 new branches a day | Raise the cap for a day, or let it catch up | ≤ $4/day |

---

## Launch checklist (stage 1, all of it)

1. Hear an episode in the production voice; measure the speed on the rented card.
2. RunPod: Max workers ≥ 3, FlashBoot on; plan the active worker for launch day.
3. Render: Standard plan, 10 GB disk, `AUDIO_CACHE_MAX_MB` raised.
4. Anthropic: next usage tier requested. (`ENFORCE_QUOTAS=1` is in `render.yaml`, §207.)
5. Exa: card on file, auto top-up.
6. Licences before charging money: GNews Essential, Finnhub commercial, API-Sports Pro for the sports the product leads with (per sport, §180).
7. Licences: `/api/health` → `licences.commercial_ready` is true (GNEWS_PLAN, FINNHUB_PLAN, OPEN_METEO_API_KEY set after buying, §207).

Fixed monthly cost of the checklist (estimate): ~$150/mo before launch day,
then ~$490/mo once the active worker is added.
