# 4. Render: the servers and the database

Open `https://dashboard.render.com`. You will see:

| Name | What it is | Follows branch | Updates when |
|---|---|---|---|
| **fam** | **Production.** Real listeners. | `Main` | Anything is merged into `Main`, or a setting is saved |
| **fam-staging** | The test copy. Cannot spend money. | `staging` | Anything is merged into `staging`, or a setting is saved |
| **the database** | Production's data: accounts, episodes, messages, mixes… | - | - |

Click a service's name to open it. The **left-hand menu** has everything you
need: **Events**, **Logs**, **Environment**, **Shell**, **Settings**.

> **Before you change anything, look at the service name at the top of the
> page.** `fam` is production. `fam-staging` is the test copy. They look
> exactly alike.

> **Good to know:** a file in the repository called `render.yaml` describes
> both services, but **editing it changes nothing on Render**. Both services
> were set up by hand on the website. If a Claude session tells you a setting
> is changed "by editing render.yaml", it is mistaken here: change it on the
> Render website (Task 4). Afterwards, ask Claude to update `render.yaml` to
> match, so it stays an accurate record.

---

## Task 1. Watch a deploy

A "deploy" is Render putting a new version live. It happens on its own after
every merge ([02](02_GITHUB.md)), and after every saved setting.

1. Click the service → **Events**.

   > **Check:** the newest line says `Deploy started for <code>`. After a
   > merge, the code matches the 7-character one GitHub showed when you
   > merged. (After a setting change, it stays the same as before. That is
   > normal.)

2. Wait up to 15 minutes. Refresh.

   > **Check:** `Deploy live for <code>`. The new version is running.
   > Listeners are not interrupted: Render starts the new version before
   > stopping the old one.
   >
   > **If not:** `Deploy failed`: see [Task 2](#a-deploy-failed). Still in
   > progress after 30 minutes: click it to watch the log, and tell the owner.

3. Open the health page of the same service (`<production address>/api/health`
   for `fam`, `<staging address>/api/health` for `fam-staging`). Filter for
   `short` and look under `build` ([how](README.md#reading-the-health-page)).

   > **Check:** the same 7 characters as in step 1.

---

<a id="a-deploy-failed"></a>
## Task 2. A deploy failed

**Listeners are not affected.** The previous version keeps running.

1. **Events** → click the `Deploy failed` line. A log opens.
2. Scroll to the bottom and read the last 20 lines.

   > **If you see** `refusing to start` or a sentence about the database
   > being empty: **stop and call the owner.** This is a safety guard that
   > protects listeners' accounts. Do not try to get round it.

3. Otherwise, try once more: top right, **Manual Deploy** → **Clear build
   cache & deploy**. This is safe. If it fails again, the old version still
   keeps serving.

   > **Check:** `Deploy live` this time.

4. Still failing? Copy the last 50 lines of the log into a new Claude
   session: `This Render deploy fails with: <paste>. Find the cause and fix
   it. Explain what was wrong for someone who does not code.` Send its fix
   through staging as usual ([02](02_GITHUB.md)).

---

<a id="roll-back-to-the-last-good-version"></a>
## Task 3. Roll back to the last good version

Use this when a deploy went live and broke production. Write down **the
time things broke** first.

> **If what broke it was a setting you changed** (not a merge), do not roll
> back. Change the setting back (Task 4). That is the fix.

1. **fam** → **Events**.
2. Find the most recent `Deploy live` line from **before** the time things
   broke. Beside it, click **Rollback** (it may be under a **⋯** menu), then
   confirm.

   > **Check:** a new line `Rollback started`, then `Deploy live` for that
   > older code within 5 minutes.

3. Open `<production address>/api/health`, filter `short`, look under `build`.

   > **Check:** it now shows the **older** code.

4. Play an episode in the app.

   > **Check:** the app works again.
   >
   > **If not:** the deploy you rolled back to was not the cause. Do not roll
   > back further on a guess. Go to [10 → The site is down](10_RUNBOOKS.md#the-site-is-down).

5. **Now revert the change on GitHub**:
   [02 → Undoing a change](02_GITHUB.md#undoing-a-change-revert).
   **This is not optional.** A rollback only lasts until the next merge,
   which would put the broken code straight back.
6. **Check Auto-Deploy.** Render may switch off automatic deploys after a
   rollback, so that your rollback is not undone. **fam** → **Settings** →
   find **Auto-Deploy**.

   > **Check:** once the revert from step 5 is merged into `Main`, Auto-Deploy
   > must be **On** (it may say **On Commit** or **Yes**). If it is off, turn
   > it back on, then **Manual Deploy** → **Deploy latest commit**. Otherwise
   > no merge will ever reach production again.

**A rollback does not undo database changes.** If the bad version changed
a table, tell the owner. Do not try to fix data by hand.

---

## Task 4. Change a setting (environment variable)

Every key and setting is on the service's **Environment** page. Saving one
restarts the service: a deploy of up to 15 minutes. Production has no downtime; staging is unavailable for a minute or so.

1. Click the service → **Environment**.

   > **Check:** the name at the top of the page is the service you mean
   > (`fam` = production).

2. Click **Edit**.
3. To change one, edit its value (click the eye icon to see a hidden value).
   To add one, click **Add Environment Variable** and type the **Key** (the
   name exactly as written in this handbook, capitals included) and the
   **Value**.
4. Click **Save, rebuild, and deploy** (or **Save and deploy**). **Not**
   **Save only**, which stores the change without applying it.

   > **Check:** **Events** shows a new deploy, then `Deploy live` within 15
   > minutes.

5. Check the result in the place given in the table below.
6. Write down what you changed, from what, to what, and when. Send it to
   the owner.

**Rules:**
- **On production, change one setting at a time** and check it before the
  next.
- **Never copy a value from `fam` to `fam-staging`.** Staging must have no
  real keys.
- **Never put a key anywhere but here.**

Settings you may meet:

| Setting | What it does | How to check after |
|---|---|---|
| `FAM_ADMIN_ACCOUNTS` | Who can open `/admin` (emails, comma-separated, no spaces) | Sign in at `/admin` ([01 Part B step 5](01_ACCESS.md#part-b-the-operator-checks-each-login-works-once)) |
| `FAM_ADMIN_TOKEN` | A password that scripts use to act as an admin. Never paste it anywhere. | - |
| `AUDIO_LINKS` | `0` = replays stream through Render instead of Cloudflare. An emergency switch ([06](06_CLOUDFLARE.md#emergency-switches)). | Replay something |
| `ARCHIVE`, `ARCHIVE_RECALL` | `0` = the MongoDB archive off. An emergency switch ([07](07_MONGODB.md#emergency-switches)). | health: `archive → enabled` |
| Agent switches (table below) | Turn an agent on (`1`) or off (`0`) | health: `agents` |
| `WAITLIST`, `ENFORCE_QUOTAS`, anything ending `_MODEL` or `_PLAN` | **Owner only.** | - |

**The agents and their switches.** The health page and `/admin` show an
agent by its **role**; this handbook usually uses its **name**:

| Name | Role (on the health page) | Switch setting |
|---|---|---|
| Netenyahu | `overlord` | `AGENTS` (turns all of them off when `0`) |
| C3P0 | `contextualizer` | `CONTEXTUALIZER` |
| Nando | `categorizer` | `CATEGORIZER` |
| Sonic | `retriever` | `RETRIEVER` |
| Shakespeare | `editor` | `EDITOR_AGENT` |
| Mclovin | `supervisor` | `SUPERVISOR` |
| Consuela | `housekeeper` | `HOUSEKEEPER` (and two report-only switches, `1` = report only: `HOUSEKEEPER_DRY_RUN` for filing facts, `HOUSEKEEPER_DEDUPE_DRY_RUN` for merging duplicates) |
| Keith Wong | `it` | `IT_AGENT` |

Switching an agent on or off is the owner's decision
([01](01_ACCESS.md#owner-only-decisions)), unless the owner has told you
in writing.

---

<a id="using-the-shell"></a>
## Task 5. Use the Shell

The Shell is a text box on the Render website that runs commands *inside*
the production server. FAM's diagnostic tools are run from here. It is the
only place in this handbook where you type a command.

1. **fam** → **Shell**.

   > **Check:** a black box appears. After a few seconds it shows a line
   > ending in `$` or `#`. That means it is ready for a command.
   >
   > **If not:** if there is no **Shell** item in the menu, your Render role
   > does not allow it. Tell the owner.

2. Copy one command from the table below, paste it into the box, and press
   Enter. Wait until the line ending in `$` or `#` comes back.

These commands only **read** (as written here, flags included):

| Command | Tells you | Healthy answer |
|---|---|---|
| `python tools/voice_doctor.py` | Whether FAM can find and reach the voice. Free and quick. | Steps 1-5 each say `ok`, and it ends `Not asking it to speak.` |
| `python tools/voice_doctor.py --speak` | The same, then makes real audio. It wakes a GPU (costs a few cents), so it can take 90 seconds. | Step 6 says `ok … bytes, … s of audio at 24000 Hz` |
| `python tools/storage_doctor.py` | Whether the next deploy could wipe anything | The line `ephemeral  0` |
| `python tools/stories_report.py --dry` | Whether the news sources are answering. **Always add `--dry`**: without it, the tool writes to the live story pool. | Under **Sources**, each line starts `ok`, `off` (not set up), `wait` (not due yet) or `--` (answered, found nothing). `FAIL` or `SLOW` names a failing source. |
| `python tools/usage_report.py --days 7 --flagged` | What listeners cost over 7 days, and who is spending most | **Most expensive listeners** lists the week's top spenders. **Worth a look** covers only **the last hour**: `nobody crossed a threshold in the last hour` is the healthy answer. |

**Rules for the Shell:**
- **This is production.** A command that writes changes real data.
- **Only run commands from this handbook,** or ones a Claude session gave you
  with a clear reason. Paste the answer back into the session if you need it
  explained.
- **Never run `env`, `printenv`, or anything with `.env` in it.** They print
  every key on screen.
- **Anything with `--yes`, `--all`, `--replace`, `DROP` or `DELETE` waits
  for the owner.**
- **Never edit files** here. Edits vanish at the next deploy.

---

## Task 6. Read the logs

1. **fam** → **Logs**. New lines appear at the bottom as they happen. Times
   are UTC.
2. Use the search box at the top to filter.

| Search for | Shows |
|---|---|
| `Traceback` or `ERROR` | Failures |
| `episode timing` | How long each step of each episode took |
| `voice` | Messages about the voice |
| `held` | An agent stopped by its rule-book check ([09](09_AGENT_RULE_SIGNOFF.md)) |

Logs never show keys: FAM hides them. If you ever see one, treat it as
leaked ([10](10_RUNBOOKS.md#a-key-has-leaked)).

The important errors are also on `/admin` → menu → **Pipeline errors**,
which is easier to read.

---

## The database

All of production's data is in a Render Postgres database.
**TODO(owner):** its name and plan.

### Check it is healthy (daily)

1. Open `<production address>/api/health`. Filter for `reachable`, and look
   under `database` (not `databases`, and not `tts`).

   > **Check:** `true`. Under the same `database`, `backend` is `postgres`.

2. Filter for `ephemeral` (under `storage`).

   > **Check:** `[]` (empty). Anything listed would be wiped by the next
   > deploy. **Tell the owner immediately.**

3. Render → click the database (the name above) → **Info**. Look at storage
   used against the plan's size.

   > **Check:** below 80%. Above that, tell the owner (a bigger plan is
   > their decision).

<a id="take-a-database-backup"></a>
### Take a database backup

Do this **before merging a batch that changes a database table**
([02 → Step 4](02_GITHUB.md#step-4-open-a-pull-request-from-staging-into-main)).

**TODO(owner):** write the exact steps here: where on the database's page
(**Recovery** or **Backups**), the button for a manual export, what the plan
keeps automatically, and how many days back it can restore. **Until this is
filled in, do not merge any batch that changes a table.**

> **Check:** a new export appears in the list with today's date and time.

**Restoring a backup is always the owner's job.** A restore wipes everything
written since the backup.

### Do not

- Write to the database by hand.
- Change the number of copies the service runs (**fam** → **Settings** →
  **Instances** / **Scaling**). It must stay at **1**, or every scheduled job
  would run twice.

---

## Maintenance mode

**fam** → **Settings** → **Maintenance Mode** shows listeners a "down for
maintenance" page. Only the owner uses it, for planned database work.
