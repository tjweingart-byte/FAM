# 2. GitHub: getting a change to production, through staging

Everything here is done in the GitHub website. You do not need anything
installed.

Repository: `https://github.com/tjweingart-byte/fam`

## The words you need

| Word | What it means |
|---|---|
| **Branch** | A separate copy of the code where a change is made. FAM has three kinds: **feature branches** (one per change; Claude's are named `claude/something`), **`staging`** (the test copy), and **`Main`** (production, with a capital M). |
| **Pull request (PR)** | A request to copy the changes from one branch into another. You read it, wait for the checks, then press merge. |
| **Base** and **compare** | In a PR, **base** is the branch being changed (where the work goes *to*). **compare** is where the work comes *from*. |
| **Checks / CI** | Automatic tests GitHub runs on every PR. They take **about 30-40 minutes**. Green tick = passed. Red cross = failed. |
| **Merge** | Doing it: the changes are copied into the base branch. |
| **Revert** | A new PR that undoes an old one. |

## The route every change takes

```
  claude/some-change  ──PR #1──►  staging  ──PR #2──►  Main
                                     │                   │
                              fam-staging updates   production updates
                              (you test here)       (listeners get it)
```

1. Merge the feature branch into **`staging`** (PR #1).
2. Render puts it on **fam-staging**. You test it there.
3. Merge **`staging`** into **`Main`** (PR #2).
4. Render puts it on **production**. You check it.
5. Bring **`staging`** level with **`Main`** again (a quick PR #3).

Several feature branches can go into `staging` before you do steps 3-5
once for all of them. That is a "batch".

> **TODO(owner):** recent PRs (#229-#231) went straight from `claude/...`
> into `Main`. This handbook uses the route above while the owner is away,
> because it puts a test between Claude's change and listeners. Confirm.

---

## Before you start: two quick checks

**Check 1. Is `Main` healthy?**
1. Open the repository → **Actions** tab.
2. On the left, click **CI**. Click the **Branch** filter and choose `Main`.

> **Check:** the top run has a **green tick**.
>
> **If not:** a red cross means production's code is already failing its
> tests. Do not merge anything else. Go to
> [10_RUNBOOKS.md → CI is red on Main](10_RUNBOOKS.md#ci-is-red-on-main).

**Check 2. Is `staging` level with `Main`?**
1. Open `https://github.com/tjweingart-byte/fam/compare/staging...Main`

> **Check:** it says `There isn't anything to compare` or
> `staging and Main are identical`. That means `staging` already has
> everything production has.
>
> **If not:** it lists commits that are on `Main` but not `staging`. Do
> [Step 6](#step-6-bring-staging-level-with-main) first, then come back.

---

## Step 1. Merge a feature branch into `staging`

You need the branch name. A Claude session names it at the end of its
reply, e.g. `claude/backend-operations-docs-jzbx0p`.

1. Open the repository. Click the **Pull requests** tab.
2. Click the green **New pull request** button.
3. Set the two drop-downs at the top:
   - **base:** `staging`
   - **compare:** the feature branch (type part of its name to find it)

   > **Check:** you see `Able to merge` in green, and below it a list of
   > commits and changed files.
   >
   > **If not:**
   > - `Can't automatically merge` in red means the branch clashes with
   >   `staging`. You can still create the PR; then see
   >   [Conflicts](#this-branch-has-conflicts).
   > - `There isn't anything to compare` means the base is wrong, or the
   >   branch is already merged. Check the two drop-downs.

4. Click **Create pull request**.
5. **Title:** one sentence about what it does. **Description:** paste
   Claude's summary. Click **Create pull request** again.

   > **Check:** a page for the new PR opens, with a number (`#232`) and,
   > near the bottom, a box with a yellow dot that says
   > `Some checks haven't completed yet`.

6. **Read the change** while the checks run. Click the **Files changed**
   tab. You are not reviewing code. You are checking three things:
   - It only touches what was asked for. (A documentation change that also
     edits `app.py` is a question for Claude.)
   - It does **not** change anything in `docs/agent_rules/`,
     `.github/CODEOWNERS`, or a rule in `CLAUDE.md` or `docs/claude/`.
     Those are the owner's ([01_ACCESS.md](01_ACCESS.md#owner-only-decisions)).
   - Claude's reply said `all checks passed`.

   > **If any of those fail:** stop. Do not merge. Leave a comment on the PR
   > saying what you saw, and tell the owner or ask Claude.

7. **Wait for the checks**, about 30-40 minutes. Refresh the page.

   > **Check:** the box at the bottom turns green: `All checks have passed`.
   >
   > **If not:** `Some checks were not successful` (red) means do not merge.
   > See [CI is red on a PR](#ci-is-red-on-a-pr).

8. Click **Merge pull request**, then **Confirm merge**.

   > **Check:** a purple **Merged** badge appears at the top, and the
   > message `Pull request successfully merged and closed`.

Repeat Step 1 for every feature branch in this batch.

---

## Step 2. Watch staging update

1. Open `https://dashboard.render.com` → click **fam-staging** → in the left
   menu click **Events**.

   > **Check:** within a few minutes, a line `Deploy started for <code>`
   > appears, and 5-15 minutes later `Deploy live for <code>`.
   >
   > **If not:** `Deploy failed` means staging did not update. Click it to
   > read why, and give that text to a Claude session
   > ([03_CLAUDE.md](03_CLAUDE.md)). Do not go on to Step 4.

2. Open `<staging address>/api/health`. Find (Ctrl+F) `"commit"`.

   > **Check:** the first 7 characters match the merge commit. The PR page
   > shows it: `… merged commit abc1234 into staging`.

3. On the same page, find `zero_spend`.

   > **Check:** `"zero_spend": true`. Also find `network_guard` →
   > `true`. This proves staging cannot spend money.
   >
   > **If not:** stop and tell the owner. Staging must never be able to spend.

---

## Step 3. Test on staging

Open the staging app on your phone or computer and sign in with your
staging account.

**Staging cannot make real episodes.** Every episode there is the same
sample script, read in a beeping placeholder tone. That is normal. You are
testing that the app *works*, not how episodes sound.

Go through this list. Tick each one.

| # | Do this | You should see |
|---|---|---|
| 1 | Open the app | It loads on the sign-in screen or your home screen |
| 2 | Sign out, then sign in | You are back in |
| 3 | Search for anything | Five loading steps tick off, then the sample plays as a tone, with captions moving |
| 4 | Drag the playback bar; press ±15s | The position moves |
| 5 | Open myFAM, tap a tile | It plays |
| 6 | Tap **Go Deeper** | A suggestion and a box appear |
| 7 | Open DailyFAM, create a mix | The mix appears |
| 8 | Send a message to another staging account | It appears in the chat |
| 9 | **Whatever this batch changed.** Read each PR's description and try it. | It does what the PR says |

> **If something fails:** do not go to Step 4. Write down which row and what
> you saw (a screenshot helps). Ask a Claude session to fix it on the
> feature branch, then repeat Step 1 for the fix.

The full list is `STAGING.md` → "Staging checklist".

---

## Step 4. Open a pull request from `staging` into `Main`

1. **Pull requests** tab → **New pull request**.
2. **base:** `Main`. **compare:** `staging`.

   > **Check:** `Able to merge` in green, and the commits listed are exactly
   > the ones from this batch.
   >
   > **If not:** if you see commits you did not expect, someone else merged
   > into `staging`. Find out what they are before going on.

3. Click **Create pull request**. **Title:** `Staging to Main: <short
   list of what is in it>`. **Description:** list the PR numbers in the
   batch (`#232, #233`) and write `Tested on staging: yes`.
4. Click **Create pull request**.
5. Wait for the checks (30-40 minutes).

   > **Check:** `All checks have passed` (green).

6. **If any table in the database changes shape** in this batch (a PR
   description says "new column", "new table" or "migration"), take a
   database backup first ([04_RENDER.md → Backups](04_RENDER.md#take-a-database-backup)).
   If you are not sure, ask a Claude session: "Does PR #NNN change any
   database table's shape?"

---

## Step 5. Merge into `Main`, and check production

1. On the `staging` → `Main` PR, click **Merge pull request** →
   **Confirm merge**.

   > **Check:** the purple **Merged** badge. Note the commit code in
   > `merged commit abc1234 into Main`.

2. Render → **fam** → **Events**.

   > **Check:** `Deploy started`, then `Deploy live` 5-15 minutes later.
   > Production stays up the whole time.
   >
   > **If not:** `Deploy failed`. The old version is still serving, so
   > listeners are fine. Go to [04_RENDER.md → A deploy failed](04_RENDER.md#a-deploy-failed).

3. Open `<production address>/api/health` and check each of these (Ctrl+F
   for each word):

   | Find | It should say |
   |---|---|
   | `"commit"` | starts with the merge commit from step 1 |
   | `"mode"` | `live` (not `demo`) |
   | `"reachable"` under `"database"` | `true` |
   | `"interim"` | `false` (the real voice, not the tone) |

   > **If any is wrong:** go to [10_RUNBOOKS.md](10_RUNBOOKS.md) for that
   > symptom. If the site is broken, [roll back](04_RENDER.md#roll-back-to-the-last-good-version)
   > first and investigate after.

4. **Play one new episode** (search something new) and **one replay**
   (something from your history) in the production app.

   > **Check:** both play in the real voice.

5. RunPod also rebuilds the voice from `Main`. If this batch changed the
   voice (files in `voice_worker/`), watch it: [05_RUNPOD.md → After a merge](05_RUNPOD.md#after-a-merge-to-main).

---

## Step 6. Bring `staging` level with `Main`

Merging in Step 5 adds one small "merge" commit to `Main` that `staging`
does not have. Copy it back so the two branches match again.

1. Open `https://github.com/tjweingart-byte/fam/compare/staging...Main`

   > **Check:** it lists one or a few commits, the merge from Step 5 (and
   > any urgent fix made directly to `Main`).

2. Click **Create pull request**. (Here **base** is `staging` and
   **compare** is `Main`, the opposite of Step 4.) Title: `Main back into
   staging`. Click **Create pull request**.
3. Wait for the checks, then **Merge pull request** → **Confirm merge**.
4. Open the compare link from step 1 again.

   > **Check:** `There isn't anything to compare`. The two branches match.
   > `fam-staging` redeploys. That is expected.

**The batch is done.**

---

## An urgent fix that cannot wait for staging

Only for production being broken, and only after a rollback has not
fixed it.

1. Do Step 1, but with **base: `Main`** instead of `staging`.
2. Wait for green checks. Merge. Check production (Step 5).
3. Then do **Step 6** straight away, so `staging` gets the fix too.

   > **Check:** the compare link shows nothing to compare.

---

## Undoing a change (revert)

### The change is already in production

1. Find the **`staging` → `Main` PR** that brought it in: **Pull requests**
   → **Closed** → open it.
2. Scroll to the bottom. Click **Revert**.

   > **Check:** GitHub opens a new PR titled `Revert "…"`, with **base:
   > `Main`**.

3. Click **Create pull request**. Wait for green checks. **Merge** →
   **Confirm merge**.
4. Check production as in [Step 5](#step-5-merge-into-main-and-check-production), points 2-4.
5. Do **[Step 6](#step-6-bring-staging-level-with-main)** so `staging` loses
   the bad change too.

If production is broken *right now*, first
[roll back on Render](04_RENDER.md#roll-back-to-the-last-good-version)
(that takes two minutes, while the revert's checks take 30), then do the
revert. **A Render rollback alone is not enough**: the next merge into
`Main` would bring the bad change back.

### The change is only on staging

1. Open the feature → `staging` PR that brought it in. Click **Revert** at
   the bottom.

   > **Check:** the new PR's base is `staging`.

2. Create it, wait for green checks, merge.

---

## When something goes wrong in a PR

### "This branch has conflicts"

Two changes edited the same lines. **Do not use GitHub's conflict editor.**
1. Start a Claude session ([03_CLAUDE.md](03_CLAUDE.md)) and say:
   `Branch <name> conflicts with staging. Merge origin/staging into it,
   resolve the conflicts, run ./dev.sh check, and push.`
2. When it says it has pushed, refresh the PR.

> **Check:** `This branch has no conflicts with the base branch`, and the
> checks start again.

`PROBLEMS.md` and `PROBLEMS_INDEX.md` conflict often. That is normal.
Claude knows how to fix them.

### "This branch is out-of-date with the base branch"

Click **Update branch**.

> **Check:** the checks start again. Wait for green.

### CI is red on a PR

1. In the checks box, click **Details** beside the red cross.
2. Click the step with the red cross to open it. Scroll to the red text.
3. Copy that text (from `FAILED` or `Error` to the end).
4. In a Claude session: `CI fails on PR #NNN with this output: <paste>. Fix
   it on branch <name>, run ./dev.sh check, push.`

> **Check:** after Claude pushes, the checks re-run and turn green.

**Never** merge a red PR. **Never** accept a "fix" that deletes or skips a
test.

---

## Settings only the owner changes

**TODO(owner):** confirm, in GitHub → **Settings** → **Branches** (or
**Rules**), that both `Main` and `staging`:
- require a pull request before merging,
- require the **CI** check to pass,
- require review from Code Owners (this is what makes rule-book changes need
  the owner, see [09](09_AGENT_RULE_SIGNOFF.md)).

The other workflows (in the **Actions** tab) need nothing from you:
**Voice worker** checks voice changes automatically. **Load test** runs only
when someone starts it by hand, and only against staging.
