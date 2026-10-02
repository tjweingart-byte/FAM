# Local news and weather: switching them on

PROBLEMS.md §193 has the history; `docs/claude/constraints.md`
(`rule:local-news-ladder`, `rule:weather`) has the rules. This file is the
checklist for turning on the three sources fully: **RSS** (local news),
**NWS** (US weather and warnings) and **Open-Meteo** (weather everywhere
else, the NWS fallback, and place names).

The code is built and tested on recorded replies. **None of it has made a
real request yet**: the build container blocks every host involved. Steps
3-6 are the ones that prove it works.

## What runs, and when

| Source | Asked when | Costs |
|---|---|---|
| RSS feeds | Collector sweep every 5 min, polling only outlets of places asked about in the last 30 days, each paced by its own rhythm (≥30 min). First question about a town reads its unread feeds inline (≤2.5 s). | Free |
| NWS | A place's forecast on its first question, then at 05:00 and 17:00 in the place's own time while asked about in the last 30 days. US warnings on every US question (at most every 10 min per place). | Free |
| Open-Meteo | Weather outside the US and when NWS fails, on the same schedule. Place names (county and coordinates) the first time a town is asked about; kept for good. | $29/month to 1M calls, $99 to 5M |

Order on a local question: town news → county news → Exa on the known
outlets → "We couldn't find any recent news reports out of ___. Here's the
weather there, and the closest news we have, from across ___ County." Never
GDELT.

## Step by step

### 1. Open-Meteo (needed before towns outside the outlet list get a county or weather)

1. Subscribe to **API Standard** at <https://open-meteo.com/en/pricing>.
2. Copy the API key from the account page.
3. Render → service **`fam`** (production only, never `fam-staging`) →
   Environment → set `OPEN_METEO_API_KEY` to the key. `render.yaml` already
   declares it (`sync: false`), so Render prompts for it on the next blueprint
   sync. Or put it in the secrets manager `FAM_SECRETS` points at
   (CREDENTIALS.md). Never in the repo.
4. Leave `OPEN_METEO_KEYLESS=0`. Its free endpoint is non-commercial.

### 2. NWS

Nothing to buy and no key. `FAM_CONTACT_EMAIL` (default in `config.py`) goes
in every request's User-Agent, as NWS asks. To use a different address, set
`FAM_CONTACT_EMAIL` on the `fam` service.

### 3. Prove the weather works (on a machine that can reach the internet)

```
pip install -r requirements.txt
OPEN_METEO_API_KEY=<key> python tools/verify_weather.py "San Anselmo, California, US"
OPEN_METEO_API_KEY=<key> python tools/verify_weather.py "Paris, Ile-de-France, FR"
```

Each prints the place lookup, NWS (US), Open-Meteo, and the exact facts an
episode would be given. Exit code 0 means every part that should answer did.
Anything marked FAILED says why.

### 4. File each county's outlets (RSS)

For every county you want covered, run where the database lives (on the
server's shell, or with `LOCAL_NEWS_DB` pointing at the deployment's disk):

```
python tools/local_outlets.py "Marin County" California --dry-run   # look first
python tools/local_outlets.py "Marin County" California
```

This reads Wikidata's newspapers for the county and files each one for its
town, or for the county. Feeds are found automatically on the first poll.
To add an outlet Wikidata does not have (a Patch page, a town weekly):

```
curl -X POST https://<host>/api/admin/local-news/outlets \
  -H "Authorization: Bearer $FAM_ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"Ross Valley Reporter","homepage":"https://example.com/","town":"San Anselmo","county":"Marin","region":"California","scope":"town"}'
```

`scope` is `town` (every story counts for that town) or `county` (every story
counts for the county).

### 5. Measure what the feeds actually give

```
python tools/measure_local_news.py "San Anselmo, California, US" "Fairfax, California, US"
```

Per town: each outlet's feed state (healthy, blocked, no_feed, ...), full text
or teasers, and how many stories in the last 7 days name the town or county,
with word counts. A town that reports "weather only" needs outlets filed.

### 6. Deploy and watch

1. Merge to `Main` (production redeploys).
2. `/api/health` → `local_news` (outlet states, items, places asked) and
   `weather` (`ready`, `open_meteo.ready`, `place_lookup.ready`).
3. `/api/admin/local-news` → every feed, troubled ones first, with the reason.
4. `/admin` → request counts for `nws`, `open_meteo` and `local_feeds`.
5. Ask "what's going on in San Anselmo" and "weather in San Anselmo today".
   The first opens with the gap sentence when the town has nothing.

### 7. Ongoing

* A publisher who objects:
  `POST /api/admin/local-news/exclude {"host":"example.com","reason":"asked"}`
  - never read again.
* Feeds that break are looked for again from the homepage; a month broken
  retires them. Nothing to do unless the admin list shows a pattern.
* Staging (`FAM_ENV=staging`) forces `LOCAL_NEWS=0` and `WEATHER=0` and scrubs
  `OPEN_METEO_API_KEY`. It is zero spend and makes none of these calls.

## Settings

| Variable | Default | What it does |
|---|---|---|
| `LOCAL_NEWS` | `1` | The local ladder and the collector |
| `LOCAL_NEWS_POLL_MINUTES` | `30` | Floor between polls of one feed |
| `LOCAL_NEWS_WINDOW_DAYS` | `14` | How old a story may be and still count |
| `LOCAL_NEWS_DB` | `/data/local_news.db` | Outlets, stories, places, kept forecasts |
| `WEATHER` | `1` | Weather at all |
| `WEATHER_SWEEP_HOURS` | `5,17` | Sweep hours, in each place's own time |
| `WEATHER_LIVE_ALERTS` | `1` | US warnings asked at question time |
| `OPEN_METEO_API_KEY` | - | Production only |
| `OPEN_METEO_KEYLESS` | `0` | Free endpoint; non-commercial, never production |
| `FAM_CONTACT_EMAIL` | owner's | Contact in every request's User-Agent |
