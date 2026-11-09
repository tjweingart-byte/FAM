# 0. The system map

Read this once before anything else. It explains what the pieces are, so
the rest of the handbook makes sense.

## The pieces

FAM is one web server program plus a few rented services around it.

```
  GitHub (the code)
     |
     |-- branch "staging" --> Render: fam-staging   (the TEST copy: cannot spend money)
     |
     |-- branch "Main" -----> Render: fam           (PRODUCTION: real listeners)
     |                           |-- Render Postgres     (the database: accounts, episodes, messages...)
     |                           |-- Cloudflare R2       (audio files and pictures)
     |                           |-- MongoDB Atlas       (the history archive)
     |                           |-- Anthropic / Claude  (writes the episodes)
     |                           |-- research, news, sports, markets, weather services
     |
     |-- branch "Main" -----> RunPod: the voice      (turns scripts into speech on a GPU;
                                                       rebuilt after every merge into Main)
```

| Piece | In one sentence | You will use it to |
|---|---|---|
| **GitHub** | Where the code lives. The branch called `Main` is what production runs. | Move changes into `staging`, then `Main`. Undo changes. |
| **Render `fam-staging`** | A test copy of FAM. You can open it in your browser like the real app, but it has no paid keys and the server cannot call outside services, so it cannot spend money. | Try out changes before production. |
| **Render `fam`** | Production: the FAM real listeners use. | Watch deploys, roll back, change settings. |
| **Render Postgres** | Production's database. | Check it is healthy. You should almost never touch it. |
| **RunPod** | Rents a GPU (a graphics card) to make the voice. | Check the voice when episodes go silent. |
| **Cloudflare R2** | Stores audio files and pictures. | Check it when replays or pictures fail. |
| **MongoDB Atlas** | Stores past news articles so episodes can mention earlier reporting. | Check it when the health page says the archive has errors. |
| **Anthropic** | Claude. It writes every episode, and Claude Code writes FAM's code. | Ask Claude Code to make changes. Watch API spend. |

**TODO(owner):** the exact names, on Render, of production's database and
of any staging database, so the operator never opens the wrong one.

**How a change travels:** a Claude session pushes it to its own branch →
you merge it into `staging` → Render updates `fam-staging` → you test →
you merge `staging` into `Main` → Render updates production, and RunPod
rebuilds the voice. [02_GITHUB.md](02_GITHUB.md) gives every step.

## A quick tour of the app

Some checks ask you to "play a replay" or "open a mix". Before your first
day, spend ten minutes in the production app with the owner.

**TODO(owner):** a 10-minute walkthrough (or screenshots) showing: where to
search, where your listening history is (to replay an episode), myFAM and a
tile, Go Deeper, DailyFAM and creating a mix, messages, and Profile.

## When something is wrong, which piece to look at first

| What you notice | Look at first | Then |
|---|---|---|
| The app does not load at all | Render `fam` | [10: The site is down](10_RUNBOOKS.md#the-site-is-down) |
| The app loads, but nobody can sign in, or accounts look empty | The database | [10: Accounts missing](10_RUNBOOKS.md#accounts-look-missing-or-sign-in-fails-for-everyone) |
| Episodes are a beeping tone, or stuck on "Generating the audio" | RunPod | [05_RUNPOD.md](05_RUNPOD.md) |
| Every episode is the same canned sample | The Anthropic key setting is empty | [10: Sample script / episodes fail](10_RUNBOOKS.md#every-episode-is-the-same-sample-or-new-episodes-fail) |
| New episodes fail with an error message | The Anthropic key (refused or over its limit) | [10: Sample script / episodes fail](10_RUNBOOKS.md#every-episode-is-the-same-sample-or-new-episodes-fail) |
| Episodes are vague or out of date | Research services | [08_OTHER_SERVICES.md](08_OTHER_SERVICES.md) |
| A replayed episode will not play, but new ones do | Cloudflare R2 | [06_CLOUDFLARE.md](06_CLOUDFLARE.md) |
| The Trending row is empty | GNews | [10: Trending is empty](10_RUNBOOKS.md#the-trending-row-is-empty) |
| Pictures are missing | Cloudflare R2 | [06_CLOUDFLARE.md](06_CLOUDFLARE.md) |
| No alerts for a suspiciously long time | Email sending | [08_OTHER_SERVICES.md](08_OTHER_SERVICES.md#the-services) (the Email row) |

## What the server does on a timer

These run inside the production server. If the server is down, they do
not run.

| When (US Eastern) | When (UTC, which Render's logs use) | What |
|---|---|---|
| 05:00 and 17:00 | 10:00 and 22:00 in winter; 09:00 and 21:00 in summer | Trending row rebuilt (from GNews); myFAM rows rebuilt |
| 05:00 | 10:00 winter / 09:00 summer | Every DailyFAM mix written |
| Every 15 minutes | | World news files downloaded (GDELT) |
| Every 2 hours | | Market prices, news story pool |
| While a followed game is on | | Sports scores |
| Every hour | | Old audio files tidied in Cloudflare R2 |

So if Trending is empty at 06:00 Eastern, the 05:00 run failed. If it is
empty at 16:00, it may be waiting for 17:00.

## The addresses

| What | Address |
|---|---|
| Production app (this is `<production address>`) | **TODO(owner):** the public address, e.g. `https://familiarize.net`. (Render also gives it an `https://….onrender.com` address; use the public one.) |
| Staging app (this is `<staging address>`) | **TODO(owner):** `https://….onrender.com` |
| Health page | `<production address>/api/health` or `<staging address>/api/health` |
| Admin page | `<production address>/admin` (asks for an admin email and password every time) |
| This handbook | `https://github.com/tjweingart-byte/fam/tree/Main/docs/operations` |
| GitHub | `https://github.com/tjweingart-byte/fam` |
| Render | `https://dashboard.render.com` |
| RunPod | `https://www.runpod.io/console/serverless` |
| Cloudflare | `https://dash.cloudflare.com` |
| MongoDB Atlas | `https://cloud.mongodb.com` |
| Anthropic Console | `https://console.anthropic.com` |
| Claude Code | `https://claude.ai/code` |
| Phone preview (layout only, no real audio) | `https://claude.ai/code/artifact/c8bd86aa-e61e-4262-a1c8-b9c8d8d6645e`. **TODO(owner):** share it with the operator, or delete this row. |
