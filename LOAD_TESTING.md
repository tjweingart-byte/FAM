# Load testing: will FAM hold 1,000 listeners?

The question is "will FAM fail, or get slower, with 1,000 people using it at
once?". Nobody can answer that by looking. So this is a test that gives a
yes or a no: `tools/load_test.py` (Locust). It ends every run with **PASS**
or **FAIL** and an exit code. It is in `PROBLEMS.md` §237, and §221 started
it.

Short answer, from the first runs (below): **not yet.** With realistic
signed-in listeners, one server process meets every budget at 250, and
somewhere between 250 and 500 the first audio goes from 0.2 s to more than
2 s. At 1,000 every screen takes tens of seconds. Guests do better (fine to
500, failing at 1,000), but the app no longer has guests. All of this ran
on a container core, which is more CPU than the Render plan staging and
production use. The test now tells you where the limit is
and what uses it up. The fixes are at the end.

## 1. What "won't fail and won't be slower" means

Two checks, and both have to pass at full load.

**Budgets** (`BUDGETS` in `tools/load_test.py`). These are set from what a
listener notices, not from what the server does today:

| What | p95 | p99 | Why |
|---|---:|---:|---|
| `audio: first byte` (tap → first sample) | 1,000 ms | 2,000 ms | The spec: "within about a second audio starts". A kept replay has no model or voice in front of it, so the server can use the whole second. |
| `page: ` (app shell, player JS) | 2,000 ms | 4,000 ms | The app opening on a phone |
| `/api/notifications` | 300 ms | 1,000 ms | Every open app polls this every 3 s. It has to stay cheap or it becomes the load. |
| `/api/auth/signup` | 1,500 ms | 3,000 ms | scrypt is slow on purpose |
| any other `/api/` read | 500 ms | 1,500 ms | A screen's JSON |
| error rate, all requests | 1% | | **A 429 counts as an error.** Each simulated listener has its own session, so a 429 means the limiter refused someone using the app normally. |

**No slower than quiet.** Pass `LOAD_TEST_BASELINE=<a 10-listener run's
_stats.csv>`. The run then fails if any budgeted request's p95 at full load
is more than `SLOWDOWN` (2×) its quiet p95, plus `SLACK_MS` (50 ms). The
slack is there so that 4 ms becoming 9 ms doesn't count as a regression.

A run that played no audio **fails**, because it measured nothing that
matters. (This happens when Explore is empty. Replay episodes to staging
first; see §4.)

## 2. What one simulated listener does

Each simulated user is one person with their own session cookie. The weights
come from the client code, not from a guess:

1. **Opens the app**: `/` (the 1 MB `index.html`) and `/fam-audio.js`, with
   `Accept-Encoding: gzip, br`. The run reports whether the shell arrived
   compressed.
2. **Keeps it open**: `/api/notifications?bootstrap=1`, then
   `?since=<cursor>` every 3 s. This is `NOTIF_POLL_MS` in
   `static/index.html`, and a test keeps the two equal. **At 1,000 open apps
   this one timer is 80% of all requests.**
3. **Browses**: myFAM, Explore, trending searches, the A–Z catalogue, and,
   with an account, Saved, History, Friends and Mixes. Between taps it waits
   3–10 s.
4. **Listens**: plays a kept episode from Explore (`cached_only=1`, so
   nothing is ever generated), fetches `/api/next` and `/api/transcript`
   like the player does, then **stays on it** for 30–100% of its length
   before tapping anything else (`LOAD_TEST_LISTEN`). Without that pause, a
   simulated listener plays a new episode every few seconds, which is far
   busier than a real person. `LOAD_TEST_LISTEN=0` is the stress version.

At 1,000 signed-in listeners that works out to about 350–400 requests per
second. About 290 of those are the poll. Audio adds roughly 10 plays per
second at about 5.3 MB each (2 minutes of 24 kHz 16-bit PCM). So the server
sends **about 50 MB/s, roughly 400 Mbit/s, of audio**, plus about 5 MB/s of
app shells.

## 3. The runs, in order

| Shape (`LOAD_TEST_SHAPE`) | What it does | What it answers |
|---|---|---|
| `smoke` | 10 listeners, 2 min | The baseline the others are compared to. Run it first, with `--csv baseline`. |
| `ramp` | 10% → 25% → 50% → 75% → 100% of `LOAD_TEST_USERS` (default 1,000), 3 min a step | **The main test.** Judged at 1,000. Prints latency against listeners for every step. |
| `spike` | 10, then all 1,000 within about 20 s for 5 min, then back to 10 for 3 min | A launch or a push notification: everyone opens the app at once. Judged during the spike. The last step shows whether the server recovers. |
| `soak` | Ramps over 5 min, then holds 1,000 for `LOAD_TEST_HOLD` minutes (60) | Leaks and drift: memory, SQLite files growing, the limiter's dictionaries, caches. Watch the server's memory graph. |
| `breakpoint` | Adds 10% every 2 min, up to 2× | **Where it breaks.** Stops at the first step with more than 5% errors or p95 over 5 s, and reports that number. |

With a shape, statistics are **reset at each step**, so every row of the
table measures only its own load. The verdict uses the full-load step.

**"Confirmed for 1,000" means all four of these:**

- `ramp` passes at 1,000, with the baseline comparison;
- `spike` passes;
- a 60-minute `soak` passes, and server memory is flat;
- `breakpoint` reaches at least 1,500, which gives 50% headroom.

## 4. How to run it

Use **staging, never production.** Staging spends nothing (`FAM_ENV=staging`,
`spend_guard.py`). The test reads `/api/health` and won't start against a
server that doesn't report `zero_spend` (`LOAD_TEST_ALLOW_SPEND=1` overrides
that, deliberately). Production would also pay for every new episode, and
its waitlist and quotas would turn the crowd away. Staging and production
use the same Render plan (`starter` in `render.yaml`), so staging's limit is
production's limit. **If either plan changes, change both, or the test
measures the wrong machine.**

First, give staging something to play. Explore only lists cached episodes,
and an empty Explore means no audio, which means a FAIL:

```
python tools/replay_episodes.py --from https://fam.onrender.com \
    --to https://fam-staging.onrender.com --limit 25 --with-audio-only --yes
```

Then, from any machine with enough bandwidth for about 400 Mbit/s in:

```
pip install -r requirements-loadtest.txt
H=https://fam-staging.onrender.com

# baseline
LOAD_TEST_SHAPE=smoke LOAD_TEST_SIGNUP=1 \
  locust -f tools/load_test.py --host $H --headless --csv baseline

# the main test
LOAD_TEST_SHAPE=ramp LOAD_TEST_SIGNUP=1 LOAD_TEST_BASELINE=baseline_stats.csv \
LOAD_TEST_REPORT=ramp.md \
  locust -f tools/load_test.py --host $H --headless --processes -1 \
         --csv ramp --html ramp.html
```

Or use the **Load test** workflow on GitHub (Actions → Load test → Run
workflow). It runs the baseline and then the shape you pick, puts the
verdict and the step table on the run's summary page, and attaches the CSVs
and Locust's HTML report.

Notes:

- **`LOAD_TEST_SIGNUP=1` is the realistic setting.** The app opens on
  sign-up and "Continue as guest" is withdrawn (§197), so real listeners
  are signed in. For a signed-in listener the poll runs about six SQLite
  queries; for a guest it returns before touching the database. Each run
  adds one `loadtest+…@example.invalid` account per simulated listener to
  staging.
- **One Locust process can't drive 1,000 listeners.** Use `--processes -1`
  (one per core), or `--master` plus `--worker` on several machines. Locust
  prints a CPU warning when the load generator is the bottleneck, and a run
  with that warning measures nothing.
- The machine running Locust needs about 400 Mbit/s of inbound bandwidth at
  full load, because every play downloads the whole episode.
  `LOAD_TEST_LISTEN_BYTES=0` reads only the first byte. That's useful when
  the generator's bandwidth is the limit, but it then understates the
  server's outbound traffic.
- `LOAD_TEST_NO_POLL=1` leaves the poll out. Comparing a run with and
  without it shows how much of the load it is.

**What to watch on the server during a run.** The test sees only response
times, so watch these too:

- Render's CPU and memory graphs for `fam-staging`. With one process,
  "CPU at 100% of what the plan gives" is the limit.
- `/api/health` before and after: `storage`, the size of each database.
- The logs, for `database is locked`, `Task was destroyed but it is
  pending`, and 429s with `X-FAM-Refused-By`.

**What this does not test:** the model and the voice. On staging every
episode is a replay or the zero-spend sample. Real generation is limited by
the GPU worker (Chatterbox at about 4.6× realtime per worker) and by model
rate limits, not by this server. That needs its own capped test:
`LOAD_TEST_FRESH=1 LOAD_TEST_ALLOW_SPEND=1` at a few dozen listeners,
against a deployment with its own capped key, decided and budgeted
beforehand. Never at 1,000.

## 5. What the first runs found

These ran in this repo's 4-core development container against a local
`FAM_ENV=staging` server: one uvicorn process, as the Dockerfile runs it, with
12 replayed 2-minute episodes. Locust ran in 2 processes on the same machine.
Localhost has unlimited bandwidth, so network cost is understated. The server
also had a whole core to itself, which is **more CPU than Render's
`starter` plan gives**, so expect staging's limit to be lower than these
numbers.

Realistic guests (listening pause on, poll on), ramp to 1,000:

| listeners | req/s | errors | p95 all | p95 first audio | p95 page | p95 notifications |
|---:|---:|---:|---:|---:|---:|---:|
| 100 | 41 | 0.00% | 25 ms | 20 ms | 57 ms | 14 ms |
| 250 | 106 | 0.00% | 42 ms | 39 ms | 110 ms | 20 ms |
| 500 | 210 | 0.00% | 110 ms | 95 ms | 300 ms | 74 ms |
| 750 | 306 | 0.00% | 530 ms | 640 ms | 3,700 ms | 440 ms |
| 1,000 | 371 | 0.01% | 1,400 ms | 1,200 ms | 9,600 ms | 850 ms |

**Realistic signed-in listeners** (`LOAD_TEST_SIGNUP=1`), the case that
matters. Each step's first 30 s also includes that step's sign-ups:

| listeners | req/s | errors | p95 all | p95 first audio | p95 page | p95 notifications |
|---:|---:|---:|---:|---:|---:|---:|
| 100 | 45 | 0.00% | 69 ms | 72 ms | 110 ms | 34 ms |
| 250 | 112 | 0.00% | 160 ms | 200 ms | 420 ms | 110 ms |
| 500 | 190 | 0.01% | 3,400 ms | 2,300 ms | 8,700 ms | 1,400 ms |
| 750 | 235 | 0.05% | 11,000 ms | 15,000 ms | 15,000 ms | 2,600 ms |
| 1,000 | 245 | 0.04% | 21,000 ms | 29,000 ms | 30,000 ms | 2,500 ms |

Throughput stops growing at about 250 req/s, and from then on extra
listeners only add waiting.

The same ramp **without the poll** (`LOAD_TEST_NO_POLL=1`) reached 1,000 with
88 req/s and first audio at p95 430 ms, still within budget. The other
screens were 3–10× slower than quiet, though, and the page shell's p95 was
2.7 s. Stress mode (`LOAD_TEST_LISTEN=0`) topped out at about 285 req/s. By
750 listeners, every request was waiting 14 s.

During the top step the server process sat at 100% of one core. There were
no `database is locked` errors. The limit is **one Python process doing all
the work on one core**, not SQLite locking.

What uses up that core, most important first:

1. **One process, one core, everything on the event loop.** The Dockerfile
   runs a single `uvicorn` with no `--workers`. Most handlers are `async
   def` and call SQLite synchronously, so one slow query delays every other
   listener. Workers or more instances aren't a quick fix: the limiter, the
   caches and every store are in-process or SQLite on one disk (see
   STAGING.md, "Know the ceiling going in"). The cheapest real step up is a
   **bigger Render plan for both services**, then re-run the ramp. The
   lasting fix is a database server and more than one process.
2. **The notification poll is 80% of requests.** At 1,000 open apps, 286
   req/s, almost all of them answering "nothing new". For a signed-in
   listener each answer is about six queries. Options, all of them the
   owner's call because §127 chose 3 s for "appear instantly": slow the
   poll while nothing is happening (back off to 10–15 s, and snap back to
   3 s after a message), answer the empty case from an in-memory "latest
   message id per listener", or push instead of poll (SSE or a WebSocket).
3. **Raw PCM is about 400 Mbit/s at 1,000 listeners.** That's bandwidth
   (Render's plan and bill) and CPU (one Python process copying about
   50 MB/s). No-MP3 is a settled constraint (`no-audio-files`).
   `ios-pressures` already records that Opus over a stream is compatible
   with it, and that would be roughly a tenth of the bytes. This is the
   owner's decision; the test now measures it.
4. **The 1 MB app shell is sent uncompressed and without `Cache-Control`.**
   The run prints how many shells arrived without `Content-Encoding`. Check
   it on staging, because Render's edge may compress. If it doesn't, gzip
   for `text/html`/`js` (but never for `/api/audio`, where buffering would
   cost first-byte latency) makes each app open about a fifth of the size.
5. **Sign-up and sign-in run scrypt (about 50 ms of CPU) on the event
   loop.** `auth_signup` is `async def` and hashes inline, so every sign-up
   freezes every listener for that long. A launch-day sign-up wave is
   exactly the `spike` shape. Moving the hash to a thread
   (`asyncio.to_thread`) costs nothing and keeps the same scrypt settings.

## 6. Keeping it honest

- `tests/test_load_test.py` checks: every path the test requests is a real
  route; the poll interval matches the app's; every shape reaches full load
  and is judged there; the verdict passes and fails where it should; Locust's
  CSV reads back. It runs in CI without Locust installed.
- When the app gains a timer, a screen or an endpoint a listener hits often,
  add it to `Listener`. A load test that doesn't do what the app does
  measures a quieter server than the real one, which is the mistake §221's
  first version made by leaving out the poll.
