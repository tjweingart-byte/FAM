# 0. The system map

Read this once before anything else. It explains what the pieces are, so
the rest of the handbook makes sense.

## The pieces

FAM is one web server program plus a few rented services around it.

```
  GitHub (the code)
     |
     |-- branch "staging" --> Render: fam-staging   (the TEST copy: free, no real keys)
     |
     |-- branch "Main" -----> Render: fam           (PRODUCTION: real listeners)
     |                           |-- Render Postgres     (the database: accounts, episodes, messages...)
     |                           |-- Cloudflare R2       (audio files and pictures)
     |                           |-- MongoDB Atlas       (the history archive)
     |                           |-- Anthropic / Claude  (writes the episodes)
     |                           |-- research, news, sports, markets, weather services
     |
     |-- branch "Main" -----> RunPod: the voice      (turns scripts into speech on a GPU)
```

| Piece | In one sentence | You will use it to |
|---|---|---|
| **GitHub** | Where the code lives. The branch called `Main` is what production runs. | Move changes into `staging`, then `Main`. Undo changes. |
| **Render `fam-staging`** | A test copy of FAM. It has no paid keys and cannot reach the internet, so it is safe and free. | Try out changes before production. |
| **Render `fam`** | Production: the FAM real listeners use. | Watch deploys, roll back, change settings. |
| **Render Postgres** | Production's database. | Check it is healthy. You should almost never touch it. |
| **RunPod** | Rents a GPU to make the voice. | Check the voice when episodes go silent. |
| **Cloudflare R2** | Stores audio files and pictures. | Check it when replays or pictures fail. |
| **MongoDB Atlas** | Stores past news articles so episodes can mention earlier reporting. | Check it when the health page says the archive has errors. |
| **Anthropic** | Claude. It writes every episode, and Claude Code writes FAM's code. | Ask Claude Code to make changes. Watch API spend. |

**How a change travels:** a Claude session pushes it to its own branch →
you merge it into `staging` → Render updates `fam-staging` → you test →
you merge `staging` into `Main` → Render updates production, and RunPod
rebuilds the voice. [02_GITHUB.md](02_GITHUB.md) is the step by step.

## When something is wrong, which piece to look at first

| What you notice | Look at first | Then |
|---|---|---|
| The app does not load at all | Render `fam` | [10_RUNBOOKS: site down](10_RUNBOOKS.md#the-site-is-down) |
| The app loads, but nobody can sign in, or accounts look empty | The database | [10_RUNBOOKS: accounts missing](10_RUNBOOKS.md#accounts-look-missing-or-sign-in-fails-for-everyone) |
| Episodes are a beeping tone, or stuck on "Generating the audio" | RunPod | [05_RUNPOD.md](05_RUNPOD.md) |
| Every episode is the same canned sample | The Anthropic key | [10_RUNBOOKS: sample script](10_RUNBOOKS.md#every-episode-is-the-same-sample-script) |
| Episodes are vague or out of date | Research services | [08_OTHER_SERVICES.md](08_OTHER_SERVICES.md) |
| A replayed episode will not play, but new ones do | Cloudflare R2 | [06_CLOUDFLARE.md](06_CLOUDFLARE.md) |
| The Trending row is empty | GNews | [08_OTHER_SERVICES.md](08_OTHER_SERVICES.md) |
| Pictures are missing | Cloudflare R2 | [06_CLOUDFLARE.md](06_CLOUDFLARE.md) |

## What the server does on a timer

These run inside the production server. If the server is down, they do
not run. Times are US Eastern.

| When | What |
|---|---|
| 05:00 and 17:00 | Trending row rebuilt (from GNews); myFAM rows rebuilt |
| 05:00 | Every DailyFAM mix written |
| Every 15 minutes | World news files downloaded (GDELT) |
| Every 2 hours | Market prices, news story pool |
| While a followed game is on | Sports scores |
| Every hour | Old audio files tidied in Cloudflare R2 |

So if Trending is empty at 06:00, the 05:00 run failed. If it is empty at
16:00, it may be waiting for 17:00.

## The addresses

| What | Address |
|---|---|
| Production app | **TODO(owner)** (the public address, e.g. `https://familiarize.net`, and the Render address `https://….onrender.com`) |
| Production health page | `<production address>/api/health` |
| Production admin page | `<production address>/admin` (asks for an admin email and password every time) |
| Staging app | **TODO(owner)** (`https://….onrender.com`) |
| Staging health page | `<staging address>/api/health` |
| GitHub | `https://github.com/tjweingart-byte/fam` |
| Render | `https://dashboard.render.com` |
| RunPod | `https://www.runpod.io/console/serverless` |
| Cloudflare | `https://dash.cloudflare.com` |
| MongoDB Atlas | `https://cloud.mongodb.com` |
| Anthropic Console | `https://console.anthropic.com` |
| Claude Code | `https://claude.ai/code` |
| Phone preview (layout only, no real audio) | `https://claude.ai/code/artifact/c8bd86aa-e61e-4262-a1c8-b9c8d8d6645e` |
