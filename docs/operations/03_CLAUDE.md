# 3. Claude: asking Claude Code to make or fix a change

Almost all of FAM's code is written by Claude Code. You tell it what you
want, it makes the change on its own branch, and you move that branch
through staging to production ([02_GITHUB.md](02_GITHUB.md)).

**What Claude Code can do:** read and change the code, run every test, push
to a branch, and explain how anything works.

**What it cannot do:** see Render, RunPod, Cloudflare or Atlas; run anything
on production; hear the voice; judge how good an episode sounds; sign an
agent rule book.

---

## Task 1. Start a session and ask for a change

1. Open `https://claude.ai/code`. Click **New session** (or the **+**).
2. Choose the repository **`tjweingart-byte/fam`**.

   > **Check:** the repository name shows at the top of the session.
   >
   > **If not:** your GitHub is not connected. See
   > [01_ACCESS.md → Part B, step 2](01_ACCESS.md#part-b-the-operator-checks-each-login-works-once).

3. Type what you want, in plain words. Be specific about the outcome. Use
   one of the [prompts below](#prompts-to-copy) where one fits.
4. Press Enter, and wait. Small changes take a few minutes. Large ones can
   take an hour, mostly running tests.

   > **Check:** while it works, you see it reading files, editing, and
   > running commands. You can leave it and come back.

5. Read its final reply.

   > **Check:** the reply should include all of these. Ask for any that
   > are missing:
   > - **what it changed**, in a few sentences,
   > - **the branch name** it pushed to (`claude/…`),
   > - **`all checks passed`** from `./dev.sh check`,
   > - **anything it could not do**, with the exact command for you to run.
   >
   > **If it says tests failed:** reply "Fix the failing tests and run
   > ./dev.sh check again." Do not take a failing branch to staging.

6. Take the branch to staging: [02_GITHUB.md → Step 1](02_GITHUB.md#step-1-merge-a-feature-branch-into-staging).

### Rules for sessions

- **One task per session.** New topic → new session.
- **Never paste a password, key, connection string or admin token** into a
  session. If Claude asks for one, say no. It can tell you which setting to
  change, and you change it on Render yourself.
- **Never ask it to push to `staging` or `Main` directly.** It works on its
  own branch. You merge.
- If it says something goes against a rule in `CLAUDE.md` (the owner's
  decisions), **believe it**. That rule is the owner's to change.

---

## Prompts to copy

Replace the parts in `<angle brackets>`.

**Fix a failing check on a PR**
```
CI fails on PR #<number> (branch <branch name>) with this output:
<paste the red text>
Fix it on that branch, run ./dev.sh check, and push.
```

**Fix a merge conflict**
```
Branch <branch name> has conflicts with staging. Merge origin/staging into
it, resolve the conflicts, run ./dev.sh check, and push.
```

**Find out what is wrong in production (no changes)**
```
Production's /api/health shows this:
<paste the part of the page that looks wrong>
What is wrong, and what should I check or change on Render? Do not change
any code yet.
```

**Explain something (no changes)**
```
How does <thing> work? Answer from the code, cite file names, and do not
change anything.
```

**A small change**
```
<describe the change and why>. Follow the ship loop in CLAUDE.md. Do not
open a pull request. Tell me the branch name when you have pushed.
```

**A second opinion on a PR (no changes)**
```
Review PR #<number> for bugs and for anything that touches the owner-only
areas in docs/operations/01_ACCESS.md. Do not change anything.
```

**Update this handbook**
```
In docs/operations/<file>, <what is wrong or missing>. Fix it, keep the
step-by-step Check format, and push.
```

**A change to an agent's rule book:** see
[09_AGENT_RULE_SIGNOFF.md](09_AGENT_RULE_SIGNOFF.md#step-1-ask-for-a-proposal).
Claude writes a *proposal*. It cannot sign.

---

## Task 2. Check Claude's work before merging

Claude sounds just as confident when it is wrong. Before you merge its
branch anywhere, check:

1. **The reply says `all checks passed`.**
2. **The PR's checks are green**, whatever the reply says.
3. **Files changed** on the PR matches what you asked for, and nothing more.
4. **No owner-only files**: nothing in `docs/agent_rules/`,
   `.github/CODEOWNERS`, or a rule in `CLAUDE.md` / `docs/claude/`.
5. **For anything you can see in the app:** open the phone preview
   (`https://claude.ai/code/artifact/c8bd86aa-e61e-4262-a1c8-b9c8d8d6645e`).
   Claude republishes it after every change. Then test on staging.

> **If you are unsure about any of it:** start a **new** session with the
> "second opinion" prompt above. A new session has not seen the first one's
> reasoning, so it is a genuine second look.

---

## Task 3. The Anthropic API key (Claude inside FAM)

Separate from Claude Code: production uses an Anthropic API key to write
every episode. It is the setting `ANTHROPIC_API_KEY` on Render `fam`.
Staging has none.

### Check spend (weekly)

1. Open `https://console.anthropic.com` → **Usage** (or **Cost**).

   > **Check:** daily spend is in line with last week. A sudden doubling
   > goes to the owner ([10_RUNBOOKS.md → Costs jump](10_RUNBOOKS.md#costs-jump)).

2. **TODO(owner):** under **Limits**, set a monthly spend limit and an email
   alert, and write the amount here.

### Replace the key (only if it leaked, or the owner asks)

1. Console → **API Keys** → **Create Key**. Name it `fam-production-<date>`.
   Copy it. **Do not paste it anywhere except step 3.**
2. Render → **fam** → **Environment** → **Edit**.
3. Find `ANTHROPIC_API_KEY`. Replace its value with the new key.
   Click **Save, rebuild, and deploy**.

   > **Check:** Events shows `Deploy live` within 15 minutes.

4. Open `<production address>/api/health`. Find `"mode"`.

   > **Check:** `"mode": "live"`. Then play a new episode. It should be a
   > real episode, not the sample.
   >
   > **If not:** `"mode": "demo"` means the key was not accepted. Check
   > you copied all of it, with no spaces.

5. Back in the Console → **API Keys** → the old key → **Disable** (or
   **Delete**).

### If every episode is the same sample script

The key has stopped working. FAM does not crash. It quietly plays the
built-in sample instead.
See [10_RUNBOOKS.md → Every episode is the same sample script](10_RUNBOOKS.md#every-episode-is-the-same-sample-script).

**Do not change which Claude model FAM uses** (`MODEL`, `WRITER_MODEL`,
or anything in `agents/models.py`). The writing was tuned for these models.
That is the owner's decision.
