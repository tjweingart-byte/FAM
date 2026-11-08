# 10. Runbooks: routine checks and emergencies

**In any emergency:**
1. Write down the time and what you saw (a screenshot is best).
2. Follow the runbook below for that symptom, step by step, checking each
   step before the next.
3. Write down everything you changed, and when.
4. Send the owner a short note afterwards: what happened, what you did, and
   whether it is fixed.

---

## Routine: every working day (about 5 minutes)

**1. The health page.** Open `<production address>/api/health`. Use Ctrl+F
for each word:

| Find | Should say |
|---|---|
| `"short"` (under `"build"`) | the commit you expect (the latest merge into `Main`) |
| `"mode"` | `"live"` |
| `"reachable"` (under `"database"`) | `true` |
| `"ephemeral"` | `[]` (empty) |
| `"interim"` | `false` |
| `"ok"` (under `"audio"` → `"check"`) | `true` |
| `"errors"` (under `"archive"`) | `0`, or the same as yesterday |
| `"held"` (under `"lock"`) | `{}` (empty) |

> **If any differs:** find that symptom below.

**2. The admin page.** Open `<production address>/admin`.

> **Check:** the errors list has nothing new and repeating, and under
> **Outside services** nothing is red or at its limit.

**3. GitHub.** **Actions** → **CI** → branch `Main`.

> **Check:** the newest run has a green tick.

**4. Email.** Look for anything from FAM's alert addresses, Render or GitHub.

**5. Play two episodes** in the production app: one new search, one replay.

> **Check:** both speak in the real voice.

---

## Routine: every week (about 20 minutes)

1. **Spending.** Look at the billing or usage page of Anthropic, RunPod,
   Render, Cloudflare and MongoDB Atlas.

   > **Check:** roughly the same as last week. **Anything that has doubled
   > goes to the owner.**

2. **Heavy users.** Render → **fam** → **Shell**, run:

       python tools/usage_report.py --days 7 --flagged

   > **Check:** no account listed as flagged. If one is, see
   > [Costs jump](#costs-jump).

3. **Reports from listeners.** `/admin` → reports or moderation. Act on each
   one (see [A listener reports abuse](#a-listener-reports-abuse)).
4. **Waitlist.** `/admin/waitlist`.

   > **Check:** `outbox_pending` is 0, or lower than last week.

5. **Open pull requests** on GitHub. Anything red, old, or waiting for you?
6. **Staging is level with Main.** Open
   `https://github.com/tjweingart-byte/fam/compare/staging...Main`.

   > **Check:** `There isn't anything to compare`. If not, do
   > [02_GITHUB.md, Step 6](02_GITHUB.md#step-6-bring-staging-level-with-main).

---

## The site is down

*The app will not load, and `/api/health` does not answer.*

1. Render → **fam** → **Events**. Read the top few lines.
   - **A deploy went live in the last hour?** Probably that. Go to step 2.
   - **A deploy failed?** The old version should still be running, so this
     is not the cause. Go to step 3.
   - **Nothing recent?** Go to step 3.
2. **Roll back** ([04_RENDER.md, Task 3](04_RENDER.md#roll-back-to-the-last-good-version)).

   > **Check:** within a few minutes `/api/health` loads and shows the older
   > commit, and the app works. **Then revert on GitHub** (same task, step 5).

3. Render → **fam** → **Logs**. Scroll to the bottom.

   > **Look for:** `Traceback`, `ERROR`, or anything mentioning the database.
   > Copy the last 50 lines.

4. Is Render itself down? Check `https://status.render.com`.
5. Render → click the database.

   > **Check:** its status is **Available**, and storage is not full.

6. Still down after all of this: **call the owner** and send them the log
   lines.

---

## A deploy failed

Listeners are fine: the previous version keeps running. Follow
[04_RENDER.md, Task 2](04_RENDER.md#a-deploy-failed).

---

## Episodes are a beeping tone, or stuck on "Generating the audio"

The voice. Follow [05_RUNPOD.md, Task 2](05_RUNPOD.md#task-2-episodes-are-stuck-on-generating-the-audio-or-very-slow-to-start),
then [Task 3](05_RUNPOD.md#task-3-the-voice-is-completely-down) if it is
completely down.

**First:** was there a merge into `Main` in the last 30 minutes? If so, the
voice is probably still updating. **Wait 15 minutes and try again** before
doing anything.

---

## Every episode is the same sample script

*Claude is not being reached, so FAM plays its built-in sample.*

1. Open `/api/health`, find `"mode"`.

   > **Check:** if it says `"demo"`, FAM has no working Anthropic key. Go on.

2. Open `https://console.anthropic.com`:
   - **API Keys**: is the production key still **Active**?
   - **Limits** / **Usage**: has the spend limit been reached?
   - `https://status.anthropic.com`: is there an outage?
3. Act on what you found:
   - **Outage:** wait. It recovers on its own.
   - **Spend limit reached:** tell the owner. Raising it is their decision.
   - **Key disabled or deleted:** replace it
     ([03_CLAUDE.md, Task 3](03_CLAUDE.md#replace-the-key-only-if-it-leaked-or-the-owner-asks)).

   > **Check:** `"mode": "live"`, and a new search plays a real episode.

---

## Episodes are vague, out of date, or say they found nothing

Research services. This lowers quality; it is not an outage.
Follow [08_OTHER_SERVICES.md, Tasks 1 and 2](08_OTHER_SERVICES.md#task-1-find-which-service-is-failing).
Look at **Perplexity** and **Parallel** first.

---

## The Trending row is empty

1. What time is it? Trending is rebuilt at **05:00 and 17:00 Eastern**.
2. Open `/api/health`, find `"trending"`, and read the last edition time
   and any error.
3. Check the GNews account ([08_OTHER_SERVICES.md, Task 2](08_OTHER_SERVICES.md#task-2-a-service-is-failing-what-to-check-at-the-vendor)).

> **Check:** after a fix, the row fills at the next 05:00 or 17:00. There is
> no backup source, on purpose.

---

## Accounts look missing, or sign-in fails for everyone

**Serious. Do not change anything that writes data.**

1. Open `/api/health`:

   > **Check:** `"database"` → `"backend": "postgres"`, `"reachable": true`;
   > `"ephemeral": []`.

2. Render → **fam** → **Environment**.

   > **Check:** `DATABASE_URL` is there. (Do not open or change it.)

3. Render → **fam** → **Events**: did anyone change a setting recently?
4. Render → the database: **Available**? Any recent restore or change?
5. **Call the owner** with what you found, whatever it is. Do not restore
   anything yourself: a restore deletes everything written since the backup.

---

## Replays will not play, but new episodes do

Cloudflare R2. Follow [06_CLOUDFLARE.md, Task 1](06_CLOUDFLARE.md#task-1-check-it-is-working).

If replays fail for everyone and you cannot fix it quickly: set
`AUDIO_LINKS` = `0` on Render ([06_CLOUDFLARE.md, Emergency switches](06_CLOUDFLARE.md#emergency-switches))
and tell the owner.

---

## CI is red on Main

*The newest run of **CI** on `Main` has a red cross.*

1. Hold all other merges into `Main` until this is fixed.
2. Click the red run → the job → the red step. Copy the red text.
3. Find which merge broke it: the first red run in the list. Open its
   commit; it names the PR.
4. Choose:
   - **The cause is obvious** (that PR broke it): revert that PR
     ([02_GITHUB.md](02_GITHUB.md#undoing-a-change-revert)).
   - **Not obvious:** start a Claude session:

     ```
     CI on Main has failed since commit <code> with:
     <paste>
     Find the cause and fix it on this branch. CI runs Python 3.12;
     reproduce it in 3.12. Run ./dev.sh check and push.
     ```

     Then take the fix through the [urgent fix route](02_GITHUB.md#an-urgent-fix-that-cannot-wait-for-staging).

   > **Check:** the next CI run on `Main` has a green tick.

---

## A key has leaked

*A key or password was pasted into a chat, committed to GitHub, shown in a
log, or sent somewhere it should not go.*

**Do this straight away. Do not wait for the owner.**

1. **Make a new key and cancel the old one** at the service it belongs to:
   - Anthropic: [03_CLAUDE.md, Task 3](03_CLAUDE.md#replace-the-key-only-if-it-leaked-or-the-owner-asks)
   - Cloudflare: [06_CLOUDFLARE.md, Task 3](06_CLOUDFLARE.md#task-3-replace-the-cloudflare-key)
   - MongoDB: [07_MONGODB.md, Task 3](07_MONGODB.md#task-3-change-a-database-password)
   - Any other: [08_OTHER_SERVICES.md, Task 3](08_OTHER_SERVICES.md#task-3-replace-a-services-key)
   - `FAM_ADMIN_TOKEN`: set a new random value on Render. Make one by
     running `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`
     on any computer.

   > **Check:** the old key no longer works (the service shows it as
   > disabled or deleted), and `/api/health` is healthy with the new one.

2. **If it was committed to GitHub:** replacing the key is the fix. Deleting
   the commit is not enough, because it stays in GitHub's history. Ask a
   Claude session to remove the key from the current files as well.
3. Tell the owner: which key, where it leaked, and when it was replaced.

---

## A listener reports abuse

1. `/admin` → reports. Read the report.
2. If it breaks the rules, use the admin tools there to block the person or
   remove the content.

   > **Check:** the report shows as handled.

3. **Anything legal** (threats of harm, a court order, police, a copyright
   notice): **forward it to the owner the same day, and do not reply
   yourself.**

---

## Costs jump

1. Which service? Check each billing page and `/admin` → Outside services.
2. Render Shell: `python tools/usage_report.py --days 1 --flagged`

   > **Check:** is it **one account** (flagged), or **everyone**?

3. **One account:** move it to the limited free plan. Run this on any
   computer with a terminal, replacing the parts in angle brackets
   (`TOKEN` is the production `FAM_ADMIN_TOKEN` from Render → Environment):

       curl -s -X POST "<production address>/api/admin/plan" -H "X-Admin-Token: <TOKEN>" \
         -H 'Content-Type: application/json' -d '{"who": "<their email>", "plan": "free"}'

   > **Check:** the reply ends `"plan": "free"}`.

4. **Everyone:** on Render → **fam** → **Environment**, check that
   `ENFORCE_QUOTAS` is still `1`. Did a recent merge change something? Tell
   the owner either way.

---

## An agent is held, or behaving badly

- **Held** (`/api/health` → `"held"` lists it): see
  [09_AGENT_RULE_SIGNOFF.md](09_AGENT_RULE_SIGNOFF.md#an-agent-is-held-and-nobody-changed-anything).
  FAM works without it. Not an emergency.
- **Behaving badly** (wrong categories, good sentences dropped, wrong facts
  merged): write down three examples, with times. Send them to the owner. If
  it is harming episodes and the owner agrees, its switch on Render can turn
  it off ([04_RENDER.md, Task 4](04_RENDER.md#task-4-change-a-setting-environment-variable)).

---

## When to call the owner straight away

- Production is still down after a rollback.
- Anything that looks like listeners' data is lost or exposed.
- A deploy refuses to start because of the database.
- Police, a court, or a lawyer gets in touch.
- Spending is climbing fast and you cannot find why.

**TODO(owner):** how to reach you in an emergency during the week, and what
is worth interrupting you for.
