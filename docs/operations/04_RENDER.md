# 4. Render: the web servers and the database

Render runs FAM's web server. Two web services and a Postgres database, in
one Render workspace.

| | `fam` (production) | `fam-staging` |
|---|---|---|
| Branch | `Main` | `staging` |
| Auto-deploy | On: every merge deploys | On |
| Plan | Standard (1 CPU, 2 GB) | Starter |
| `FAM_ENV` | `production` | `staging`, which removes every paid key and blocks all outbound connections (`spend_guard.py`) |
| Storage | **Render Postgres** via `DATABASE_URL`. No disk. | A 1 GB disk at `/data` (SQLite) |
| Voice | RunPod (`VOICE_BACKEND=remote`) | A placeholder tone |
| Episodes | Real: Claude, research, voice | The built-in sample script, or episodes copied from production |

**Important: both services were created by hand, not from `render.yaml`.**
That file documents what each service should have, with a comment on every
variable, but editing it changes nothing on Render. Every change is made in
the dashboard. If you change one there, make the same change in
`render.yaml` in a PR, so the file stays a true record (STAGING.md, "How it
was actually set up").

## A deploy

A merge into `Main` starts one automatically. To watch it:

**Render → `fam` → Events.** You will see "Deploy started for <commit>",
the Docker build, then "Deploy live". A deploy takes several minutes. With no
disk, Render starts the new instance before stopping the old one, so there
is no downtime.

**After every deploy, check:**

    curl -s https://<prod-host>/api/health | python3 -m json.tool | head -40

- `build.commit` is the commit you expected.
- `mode` is live (not demo), `database.backend` is `postgres` and
  `reachable` is true, `storage` names nothing `ephemeral`.
- Then play one new episode and one replay in the app.

**A failed deploy** leaves the previous version serving. Render emails the
workspace and Events shows "Deploy failed". Open it: the build log names
the error. Two known causes:

- **The server refuses to start because the database looks empty** while
  there is data elsewhere (§254, a guard against serving an app whose
  accounts seem to have vanished). Read the log line; it says what to do.
  Do not set `DATABASE_ALLOW_EMPTY=1` on production without the owner.
- A dependency failed to install. Re-run the deploy (**Manual Deploy →
  Deploy latest commit**). If it fails again, give the log to a Claude
  session.

**Manual deploy** (Render → `fam` → Manual Deploy): *Deploy latest commit*
re-deploys `Main` (useful after changing nothing but a variable, or after a
failed build). *Clear build cache & deploy* if a build is stuck on a bad
cached layer.

## Rollback

Render → `fam` → **Events** → find the last good deploy → **Rollback**.
It is live in a minute or two.

**A rollback is temporary.** The next merge into `Main` deploys what `Main`
holds, bad change included. Immediately revert the bad PR on GitHub
([02_GITHUB.md](02_GITHUB.md#undo-a-merge-revert)).

**A rollback does not roll back the database.** If the bad deploy changed a
table's shape, rolling back the code can leave old code reading new data.
Tell the owner, and do not try to fix it by hand.

## Environment variables

Render → `fam` → **Environment**. Every key and setting lives here (or in
the secrets manager `FAM_SECRETS` names, see `CREDENTIALS.md`).

- **Saving any change redeploys** the service. Change one thing at a time,
  and note what you changed and when (a line in the PR or a message to the
  owner).
- **Never copy production's values to staging.** Staging removes them
  anyway, and `/api/health` → `environment.credentials_removed` lists any
  that were tried.
- The full list, with a comment on what each does, is the `fam` block of
  `render.yaml`. The ones that matter most are in `docs/ONBOARDING.md` §7.2.

Switches you may be asked to flip:

| Variable | What it does | Notes |
|---|---|---|
| `FAM_ADMIN_ACCOUNTS` | Who may open `/admin` (emails, comma-separated) | Add the operator here |
| `FAM_ADMIN_TOKEN` | Token for admin endpoints from a terminal (`X-Admin-Token`) | Rotate it if it has been shared |
| `WAITLIST` | `1` closes the app to all but granted accounts | Owner decision |
| `ENFORCE_QUOTAS` | `1` enforces free-tier limits | On in production |
| `AGENTS`, `HOUSEKEEPER`, `HOUSEKEEPER_DRY_RUN`, `IT_AGENT`, `RETRIEVER` ... | Turn the agents on, and whether they only report (dry run) or act | Flip only when a session or the owner says to (e.g. `docs/TRAINING_NEXT_BRANCH.md`) |
| `ARCHIVE`, `ARCHIVE_RECALL` | `0` turns the Atlas archive (or its recall) off | Emergency off-switch if Atlas misbehaves |
| `AUDIO_LINKS` | `0` makes replays stream through the server instead of R2 links | Emergency off-switch if R2 links fail |
| `THUMBNAILS` | `1` paints new category pictures with Gemini | Costs money, inside a daily ceiling |

## The Shell

Render → `fam` → **Shell** opens a terminal inside the running production
server, in `/app`, with every environment variable already set. Use it to
run FAM's own tools against production. Examples:

    python tools/voice_doctor.py                  # is the voice reachable and speaking
    python tools/storage_doctor.py                # will a redeploy erase anything
    python tools/stories_report.py                # are the story sources answering
    python tools/usage_report.py --days 7         # what listeners cost this week
    python tools/consuela_report.py --merges      # an agent report a session asked for

**Rules for the Shell:**

- It is production. A command that writes changes real listeners' data.
- Run only commands you understand, or that a Claude session or this
  handbook gave you with a reason. Paste the output back to the session if
  you need it read.
- Never edit a file in the Shell. Changes vanish on the next deploy, and
  they bypass review.
- Anything with `--yes`, `--all`, `--replace`, `DROP` or `DELETE` waits
  for the owner.

## The database (Render Postgres)

Every production store (accounts, the script cache, mixes, social,
messages, saved, quotas, metering, moderation ...) is a schema in one
Postgres database. `POSTGRES.md` explains how; `DATABASE.md` explains what
each store holds.

- **Health:** `/api/health` → `database` (backend, reachable, size, largest
  tables). `/admin` → the live tracker counts.
- **Plan and size:** Render → the database → Info. Watch the size against
  the plan's storage. **TODO(owner):** note the database's name and plan
  here.
- **Connections:** each FAM process holds up to 20 (`DATABASE_MAX_CONNECTIONS`).
  Keep `fam` at **one instance**: some state still lives in process memory,
  so a second instance would run every background job twice (POSTGRES.md,
  "Not yet done").
- **Reading it directly:** the database's page → Connect → "PSQL Command",
  from your own computer. Read-only questions are fine. Do not write to it
  by hand. `/admin`'s question box is off on Postgres by design.

### Backups

Render Postgres keeps automatic backups on paid plans, and offers
point-in-time recovery on some (Render → the database → **Recovery**).
**TODO(owner):** confirm what the current plan gives, and write the
recovery window here.

**Before merging a PR that changes the shape of a table**, take a manual
export (Render → the database → **Recovery / Backups** → export, or
`pg_dump` from your computer with the external connection string), and
keep it until the change has run for a few days.

## Logs

Render → `fam` → **Logs**. Live, searchable. Useful searches:

| Search | Finds |
|---|---|
| `episode timing` | One line per step of each episode's wait (§128) |
| `ERROR` / `Traceback` | Failures |
| `voice` | The voice ladder: which worker, failures, recoveries |
| `agents held` | Agents held by the rule-book check (unsigned or changed book) |

Errors that matter are also on `/admin` (errors page) and counted on
`/api/health`. Logs never contain keys: the server redacts them
(`log_redaction.py`). If you ever see one, treat it as leaked
([10_RUNBOOKS.md](10_RUNBOOKS.md#a-key-has-leaked)).

## Maintenance mode

Render → `fam` → Settings → **Maintenance Mode**. Listeners see a
maintenance page; the instance and its Shell keep running. Use it only for
planned database work the owner has scheduled (POSTGRES.md describes one).

## Staging

`fam-staging` follows `staging`. Use it to try a batch before it reaches
`Main`:

1. Merge feature PRs into `staging`. Watch `fam-staging` deploy.
2. Run the staging checklist in `STAGING.md` ("Staging checklist").
3. To hear real episodes there for free, copy kept episodes from production:
   `python tools/replay_episodes.py --from https://<prod-host> --to https://<staging-host> --from-token <prod FAM_ADMIN_TOKEN> --to-token <staging FAM_ADMIN_TOKEN> --limit 25 --yes`
   (from your computer; without `--yes` it only lists what it would copy).
4. Open one PR from `staging` into `Main`.

Staging's `/api/health` must always say `environment.zero_spend: true` and
`network_guard: true`. If either is ever false, tell the owner.
