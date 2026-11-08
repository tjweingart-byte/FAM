# 0. The system map

FAM is one Python web server (`app.py`, FastAPI) plus a handful of rented
services around it. Almost everything you will operate is one of these
boxes.

```
                         Listeners (web app today, iOS app next)
                                        |
                                        v
  GitHub  ----push to Main---->  Render: fam  (production web service)  ----> Render Postgres
  tjweingart-byte/fam             app.py, the interface, background jobs        every store (accounts, cache,
     |                            FAM_ENV=production, real keys                 messages, mixes, ...)
     |                                  |   |   |   |
     |                                  |   |   |   +--> Cloudflare R2 "fam-audio": kept audio + pictures
     |                                  |   |   +------> MongoDB Atlas: the history archive
     |                                  |   +----------> Anthropic (Claude): briefs, scripts, titles
     |                                  |                Perplexity / Parallel / GDELT: research
     |                                  |                GNews, BALLDONTLIE, Tiingo, Polymarket,
     |                                  |                NWS / Open-Meteo, Wikimedia, Gemini, SMTP
     |                                  v
     +----push to Main---->  RunPod Serverless endpoint "FAM": the voice (Chatterbox on a GPU)
     |                       rebuilt by RunPod from Main's Dockerfile.voice
     |
     +----push to staging->  Render: fam-staging  (zero spend: no keys, no outbound network)
```

## The pieces, in one table

| Piece | What it is | Where it is configured | Where to look when it is wrong |
|---|---|---|---|
| **GitHub** `tjweingart-byte/fam` | All the code and docs. Branch `Main` is production. | GitHub → Settings (branch protection, collaborators) | Actions tab (CI), Pull requests |
| **Render `fam`** | Production web server. Docker, Standard plan (1 CPU, 2 GB). Auto-deploys every merge into `Main`. No disk. | Render dashboard → `fam` → Environment / Settings. `render.yaml` describes it, but **the service was made by hand, so editing `render.yaml` changes nothing** (STAGING.md). | Render → `fam` → Logs, Events; `/api/health`; `/admin` |
| **Render `fam-staging`** | The test copy. Follows branch `staging`. Spends nothing, cannot reach the internet. | Render → `fam-staging` | Same as above, on the staging host |
| **Render Postgres** | Production's database. Every store (accounts, script cache, messages, mixes, social, quotas, metering ...) is a schema in it. | Render → the database → Info / Backups; `DATABASE_URL` on `fam` | `/api/health` → `database`; `/admin` |
| **RunPod endpoint `FAM`** (id `g65qlmwz4sc02u`) | The voice. A Serverless GPU endpoint running `voice_worker/`. Scales to zero; billed per second of speaking. | RunPod console → Serverless → FAM | Builds tab, Requests tab, `python tools/voice_doctor.py` |
| **Cloudflare R2 `fam-audio`** | Kept audio (Opus) and every picture (profiles, mixes, categories). | Cloudflare dashboard → R2 | `/api/health` → `audio` |
| **MongoDB Atlas** | The history archive: past articles and facts that feed the "earlier reporting" packet. | cloud.mongodb.com → project FAM | `/api/health` → `archive` |
| **Anthropic** | Claude: the brief (EI), the writer, titles, categories, the agents. Also Claude Code, which writes FAM's code. | console.anthropic.com (API key, spend limits); claude.ai (Claude Code) | `/api/health` → `mode`, `credentials`; `/admin` errors |
| **Everything else** | Research, news, sports, markets, weather, pictures, email, waitlist, Apple sign-in. | Each vendor's dashboard; a key on Render | [08_OTHER_SERVICES.md](08_OTHER_SERVICES.md) |

## One episode, crossing the services

Knowing this order tells you which service to suspect from a symptom.

1. A listener taps search. The browser calls `fam` on Render.
2. `fam` checks the account and quota in **Postgres**.
3. **Claude** (Episode Intelligence) decides what to search for.
4. **Perplexity** (then Parallel, then GDELT) searches. Sports go to
   **BALLDONTLIE**, markets to **Tiingo**, weather to **NWS / Open-Meteo**.
   **MongoDB Atlas** adds earlier reporting.
5. **Claude** writes the script, sentence by sentence.
6. Each sentence goes to the **RunPod** voice, which sends back audio.
7. `fam` streams the audio to the listener, and keeps it in **R2** and the
   script in **Postgres** for a week (longer if saved or shared).
8. A replay is a signed link straight to **R2**: no Claude, no RunPod.

| Symptom | Suspect first |
|---|---|
| The site does not load at all | Render `fam` (Events, Logs), then Postgres |
| Loads, but sign-in fails or accounts look empty | Postgres (`/api/health` → `database`, `storage`) |
| Episodes play a placeholder tone, or stall at "Generating the audio" | RunPod ([05_RUNPOD.md](05_RUNPOD.md)) |
| Episodes are the same canned sample script | The Anthropic key (`/api/health` → `mode`) |
| Episodes are thin or say nothing current | Research keys (Perplexity / Parallel), see [08](08_OTHER_SERVICES.md) |
| Replays fail but new episodes play | R2 ([06_CLOUDFLARE.md](06_CLOUDFLARE.md)) |
| Trending rail is empty | GNews |
| Pictures missing | R2, then Gemini |

## Scheduled work inside `fam`

Nothing external runs on a timer (there is no cron service). These run
inside the production server, so a stopped server stops them too. Times are
US Eastern.

| When | What |
|---|---|
| 05:00 and 17:00 | Trending edition (GNews, ten episodes written ahead); myFAM rails edition. (The weather sweep also runs at 05:00 and 17:00, but in each place's own time.) |
| 05:00 | DailyFAM edition: every active mix written |
| Every 15 min | GDELT export files downloaded |
| Every 2 h | Markets (Tiingo), news story pool, vocabulary sweep |
| While a followed game is on | Sports sweep (BALLDONTLIE) |
| Hourly | R2 audio sweep (upload, move to `kept/`, delete expired) |
| Every `IT_WATCH_SECONDS` | Keith (the IT agent) checks for incidents, if `IT_AGENT=1` |

The full list, with the code that runs each one, is the background-jobs
table in `docs/BACKEND.md`.

## The hosts

| | URL |
|---|---|
| Production | **TODO(owner)**: the Render URL for `fam` and the public domain (`familiarize.net`?) |
| Staging | **TODO(owner)**: the Render URL for `fam-staging` |
| Health | `<host>/api/health` (no sign-in needed) |
| Admin | `<host>/admin`, `<host>/admin/waitlist`, `<host>/admin/thumbnails` (admin email and password every time) |
| The phone preview | `https://claude.ai/code/artifact/c8bd86aa-e61e-4262-a1c8-b9c8d8d6645e` (layout only; no real audio or writing) |
