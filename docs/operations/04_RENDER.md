# 4. Render: the servers and the database

Open `https://dashboard.render.com`. You will see:

| Name | What it is | Follows branch | Updates when |
|---|---|---|---|
| **fam** | Production. Real listeners. | `Main` | Anything is merged into `Main` |
| **fam-staging** | The test copy. Free; no real keys; cannot reach the internet. | `staging` | Anything is merged into `staging` |
| **the Postgres database** | Production's data: accounts, episodes, messages, mixes… | - | - |

Click a service's name to open it. The **left-hand menu** is where
everything is: **Events**, **Logs**, **Environment**, **Shell**, **Settings**.

> **Good to know:** a file in the repository called `render.yaml` describes
> both services, but **editing it changes nothing on Render**: both
> services were set up by hand. Every change is made on the Render website.
> When you change a setting, ask a Claude session to update `render.yaml`
> to match, so it stays an accurate record.

---

## Task 1. Watch a deploy

A "deploy" is Render putting new code live. It happens on its own after
every merge ([02_GITHUB.md](02_GITHUB.md)), and after every saved setting.

1. Click the service (**fam** or **fam-staging**) → **Events**.

   > **Check:** the newest line says `Deploy started for <commit>`, and the
   > commit matches the one GitHub showed when you merged.

2. Wait 5-15 minutes. Refresh.

   > **Check:** `Deploy live for <commit>`. The new version is running.
   > Production stays up throughout: Render starts the new version before
   > stopping the old one.
   >
   > **If not:** `Deploy failed`: see [Task 2](#a-deploy-failed).
   > Still `in progress` after 30 minutes: click it to watch the log.

3. Open `<address>/api/health`, find `"commit"`.

   > **Check:** it starts with the same 7 characters as the commit in step 1.

---

<a id="a-deploy-failed"></a>
## Task 2. A deploy failed

**Listeners are not affected**: the previous version keeps running.

1. **Events** → click the `Deploy failed` line. A log opens.
2. Scroll to the bottom and read the last 20 lines.

   > **If you see** `refusing to start` or a sentence about the database
   > being empty: **stop and call the owner.** This is a safety guard that
   > protects listeners' accounts. Do not try to get round it.

3. Otherwise, try once more: top right, **Manual Deploy** → **Clear build
   cache & deploy**.

   > **Check:** `Deploy live` this time.

4. Still failing? Copy the last 50 lines of the log into a new Claude
   session:
   `This Render deploy fails with: <paste>. Find the cause and fix it.`
   Send its fix through staging as usual ([02_GITHUB.md](02_GITHUB.md)).

---

<a id="roll-back-to-the-last-good-version"></a>
## Task 3. Roll back to the last good version

Use this when a deploy went live and broke production. It takes about
two minutes.

1. **fam** → **Events**.
2. Find the **last `Deploy live` line from before** the bad one. Beside it,
   click **Rollback** (sometimes under the **⋯** menu), then confirm.

   > **Check:** a new line `Rollback started`, then `Deploy live` for the
   > older commit within a few minutes.

3. Open `<production address>/api/health`, find `"commit"`.

   > **Check:** it now shows the **older** commit.

4. Play an episode in the app.

   > **Check:** the app works again.

5. **Now revert the change on GitHub**:
   [02_GITHUB.md → Undoing a change](02_GITHUB.md#undoing-a-change-revert).

   **This step is not optional.** A rollback only lasts until the next
   merge. The next merge into `Main` would put the broken code straight back.

**A rollback does not undo database changes.** If the bad version changed
a table, tell the owner. Do not try to fix data by hand.

---

## Task 4. Change a setting (environment variable)

Every key and setting is on the **Environment** page. Saving one restarts
the service (a deploy, about 5-15 minutes, no downtime).

1. Click the service → **Environment**.
2. Click **Edit**.
3. To change one, edit its value. To add one, click **Add Environment
   Variable** and fill in the **Key** (the name, exactly as written in this
   handbook, capitals included) and the **Value**.
4. Click **Save, rebuild, and deploy** (or **Save and deploy**).

   > **Check:** Events shows a new deploy, then `Deploy live`.

5. Check whatever the setting was for on `/api/health` (each document says
   which word to look for).
6. Write down what you changed, from what, to what, and when. Send it to
   the owner.

**Rules:**
- **Change one setting at a time** on production, and check it, before the next.
- **Never copy a value from `fam` to `fam-staging`.** Staging must have no
  real keys.
- **Never put a key anywhere but here.**

Settings you may be asked to change:

| Setting | What it does |
|---|---|
| `FAM_ADMIN_ACCOUNTS` | Who can open `/admin` (emails, comma-separated, no spaces) |
| `FAM_ADMIN_TOKEN` | A password for the admin tools run from a terminal |
| `AUDIO_LINKS` | `0` = replays stream through Render instead of Cloudflare. An emergency switch ([06](06_CLOUDFLARE.md)). |
| `ARCHIVE`, `ARCHIVE_RECALL` | `0` = the MongoDB archive off. An emergency switch ([07](07_MONGODB.md)). |
| `AGENTS`, `HOUSEKEEPER`, `HOUSEKEEPER_DRY_RUN`, `IT_AGENT` | Turn the agents on, off or report-only. **Only when the owner or a written instruction says so.** |
| `WAITLIST`, `ENFORCE_QUOTAS`, any `..._MODEL`, any `..._PLAN` | **Owner only.** |

---

<a id="using-the-shell"></a>
## Task 5. Use the Shell

The Shell is a command line *inside* the running production server.
FAM's diagnostic tools are run from here.

1. **fam** → **Shell**. Wait for the prompt (a line ending in `$` or `#`).
2. Type or paste one command. Press Enter. Wait for the prompt to come back.

   > **Check:** the command prints its answer and the prompt returns.

Safe commands (they only *read*):

| Command | Tells you |
|---|---|
| `python tools/voice_doctor.py` | Whether the voice works, and what to fix if not |
| `python tools/storage_doctor.py` | Whether the database is safe from a redeploy (should end with nothing marked ephemeral) |
| `python tools/stories_report.py` | Whether the news sources are answering |
| `python tools/usage_report.py --days 7 --flagged` | What listeners cost this week, and any account using far too much |

**Rules for the Shell:**
- **This is production.** A command that writes changes real data.
- **Only run commands from this handbook**, or ones a Claude session gave you
  with a reason. Paste the output back to the session if you need it read.
- **Anything containing `--yes`, `--all`, `--replace`, `DROP` or `DELETE`
  waits for the owner.**
- **Never edit files** here. The changes vanish at the next deploy.

---

## Task 6. Read the logs

1. **fam** → **Logs**. New lines appear at the bottom as they happen.
2. Use the search box at the top to filter.

| Search for | Shows |
|---|---|
| `Traceback` or `ERROR` | Failures |
| `episode timing` | How long each step of each episode took |
| `voice` | Messages about finding and using the voice |
| `agents held` | An agent stopped by its rule-book check ([09](09_AGENT_RULE_SIGNOFF.md)) |

Logs never show keys: FAM hides them automatically. If you ever see one,
treat it as leaked ([10_RUNBOOKS.md](10_RUNBOOKS.md#a-key-has-leaked)).

The important errors are also listed on `/admin`, which is easier to read.

---

## The database

All of production's data is in Render Postgres.

### Check it is healthy (daily)

1. Open `<production address>/api/health`. Find `"database"`.

   > **Check:** `"backend": "postgres"` and `"reachable": true`.

2. Find `"storage"`.

   > **Check:** the `ephemeral` list is empty `[]`. (Anything listed there
   > would be wiped by the next deploy. Tell the owner immediately.)

3. Render → click the database → **Info**: look at **Storage** used against
   the plan's size.

   > **Check:** below 80%. Above that, tell the owner (a bigger plan is
   > their decision).

**TODO(owner):** write the database's name and plan here.

<a id="take-a-database-backup"></a>
### Take a database backup

Do this **before merging a batch that changes a database table**
([02_GITHUB.md, Step 4](02_GITHUB.md#step-4-open-a-pull-request-from-staging-into-main)).

1. Render → click the database → **Recovery** (or **Backups**).
2. **TODO(owner):** confirm what the plan offers (automatic daily backups,
   point-in-time recovery, and how many days back), and the exact button
   for a manual export. Write it here.

   > **Check:** a new export appears in the list with today's date.

**Restoring a backup is always the owner's job.** A restore wipes everything
written since the backup.

### Do not

- Write to the database by hand.
- Add a second instance of `fam` ("scaling"). FAM must run as **one**
  instance for now, or every scheduled job would run twice.

---

## Staging: copying real episodes so you can hear them for free

Staging cannot make real episodes, but it can replay ones copied from
production. Run this from a computer with the repository and Python, not
from the Shell:

    python tools/replay_episodes.py --from <production address> --to <staging address> \
        --from-token <production FAM_ADMIN_TOKEN> --to-token <staging FAM_ADMIN_TOKEN> --limit 25

> **Check:** without `--yes` it only *lists* what it would copy. If the list
> looks right, run it again with `--yes` added at the end.

---

## Maintenance mode

**fam** → **Settings** → **Maintenance Mode** shows listeners a "down for
maintenance" page. Only the owner uses it, for planned database work.
