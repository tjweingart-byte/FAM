# 2. GitHub: branches, pull requests, CI, merging and reverting

Repository: **`tjweingart-byte/fam`**. The default branch is **`Main`**
(capital M). Whatever is on `Main` is what production runs, within a few
minutes of it landing there: Render redeploys the web server and RunPod
rebuilds the voice worker from it.

## The words

| Word | Means |
|---|---|
| **Branch** | A named line of work. `Main` is production. Every change is made on its own branch first. |
| **Commit** | One saved set of changes on a branch, with a message saying why. |
| **Push** | Sending commits from a computer (or a Claude session) up to GitHub. |
| **Pull request (PR)** | A request to merge one branch into another. Where you read the change, CI runs, and the merge happens. |
| **CI** | The automatic checks (`.github/workflows/ci.yml`): every test, the interface checks, the preview build and three browser smoke runs. Green tick = passed. Red cross = do not merge. |
| **Merge** | Applying a PR's changes to the target branch. Merging into `Main` deploys. |
| **Revert** | A new commit that undoes an earlier one. How a bad merge is taken back out. |

## How changes flow

**What `STAGING.md` describes** (the intended flow):

```
feature branch --PR--> staging --(batched PR)--> Main
                          |                        |
                    fam-staging (zero spend)   fam (production)
```

**What has actually been happening** (every recent merge, e.g. #229-#231):
Claude works on a `claude/<name>` branch, the owner opens a PR from it
**straight into `Main`**, and `staging` is kept level with `Main`.

**TODO(owner):** choose which one the operator follows. The recommendation
is the intended flow while you are away: PRs into `staging` during the week,
the operator tries them on `fam-staging`, and one `staging` → `Main` PR when
the batch is good. It puts a test step between a Claude change and
listeners.

## Everyday tasks in the GitHub website

You do not need git on your computer for any of this.

### See what is waiting

**Pull requests** tab. Each open PR shows its branch, its target (the
"base", `Main` or `staging`), and a green tick or red cross for CI.

### Open a pull request from a branch Claude pushed

1. **Pull requests → New pull request.**
2. **base:** `staging` (or `Main`, see above). **compare:** the
   `claude/...` branch the session named.
3. **Create pull request.** Title: what it does, in a sentence. Body: paste
   Claude's summary.
4. Wait for CI. It takes a few minutes. The PR page shows the checks under
   the description.

### Read a PR before merging

- **Files changed** tab: skim it. You are checking that it touches what was
  asked and nothing else. A docs change that also edits `app.py` is a
  question to ask.
- **Look for files in `docs/agent_rules/`, `CODEOWNERS`, `agents/rulebooks.py`,
  `tools/rulebook_manifest.py` or `tests/test_agent_rulebooks.py`.** Those
  need the owner ([09_AGENT_RULE_SIGNOFF.md](09_AGENT_RULE_SIGNOFF.md)).
- **Look for a changed rule in `CLAUDE.md` or `docs/claude/`.** That is an
  owner decision ([01_ACCESS.md](01_ACCESS.md#owner-only-decisions)).
- **Look for a changed database shape** (a new or renamed column or table
  in a `*.py` store). Take a database backup before merging it
  ([04_RENDER.md](04_RENDER.md#backups)).
- **Checks must be green.** Never merge red. Never "fix" red by deleting or
  skipping a test.

### Merge

**Merge pull request → Confirm merge.** Use the default "Create a merge
commit" (that is what the history uses). Then:

1. Watch the deploy in Render → `fam` → Events ([04_RENDER.md](04_RENDER.md#a-deploy)).
2. Open `<prod-host>/api/health` and check `build.commit` is the new merge
   commit.
3. Play one new episode and one replay.

Leave the branch in place after the merge; old `claude/*` branches are
harmless.

### Undo a merge (revert)

The fastest safe undo. It does not rewrite history.

1. Open the merged PR.
2. **Revert** (button near the bottom). GitHub makes a new branch and a new
   PR that undoes it.
3. Merge that PR once CI is green. Render deploys the old behaviour.

If the site is down and you cannot wait for CI, use Render's rollback
first ([04_RENDER.md](04_RENDER.md#rollback)), then do the revert so `Main`
matches what is running. **A rollback on Render is not permanent:** the next
merge into `Main` deploys whatever `Main` holds. Always follow it with the
revert.

### "This branch has conflicts"

Two branches changed the same lines. Do not resolve it in the web editor.
Start a Claude session on that branch and ask it to merge `Main` in and
resolve the conflicts ([03_CLAUDE.md](03_CLAUDE.md)). This repository's
`PROBLEMS.md` and `PROBLEMS_INDEX.md` conflict often. Claude knows how to
fix them.

### "This branch is out of date with the base branch"

Click **Update branch**. CI runs again.

### CI is red on a PR

1. Click **Details** next to the red check, and open the failing step.
2. Copy the error into a Claude session on that branch: "CI fails with
   this; fix it." ([03_CLAUDE.md](03_CLAUDE.md#prompts-that-work))
3. Never merge it red.

### CI is red on `Main` itself

Something merged broke, or something outside changed. Treat it as urgent:
nothing else should merge until it is green, because the next PR will
inherit the red and nobody will be able to tell whose fault it is. The
repository has been red on `Main` for nine merges in a row before
(`PROBLEMS.md` §106, §115). See
[10_RUNBOOKS.md](10_RUNBOOKS.md#ci-is-red-on-main).

## The workflows

| Workflow | Runs on | Does |
|---|---|---|
| `CI` (`ci.yml`) | Every push and PR | The whole test suite on Python 3.12, interface checks, the previews and the browser smoke runs. Gate for merging. |
| `Voice worker` (`voice-worker.yml`) | Pushes touching the voice | Checks that the app and the worker still agree. Builds the worker image to GHCR only when asked. (RunPod builds its own copy from `Main`.) |
| `Load test` (`load-test.yml`) | By hand only (Actions → Load test → Run workflow) | Load-tests **staging**. See `LOAD_TESTING.md`. Never point it at production. |

## Branch protection and CODEOWNERS

`.github/CODEOWNERS` says that changes to the agent rule books, their
signature, the code that checks the signature and CODEOWNERS itself need
the owner's review. GitHub enforces that only when branch protection on
`Main` (and `staging`) has **Require a pull request** and **Require review
from Code Owners** turned on.

**TODO(owner):** confirm in GitHub → Settings → Branches (or Rules) that
`Main` and `staging` require a PR, require the `CI` check to pass, and
require Code Owner review. If the operator should be able to approve
non-rule-book PRs on their own, leave "required approvals" at 0 or add
them as a reviewer. Only CODEOWNERS paths need the owner.

## If you do want git on your computer

Not needed, but useful for reading the code:

    git clone https://github.com/tjweingart-byte/fam.git
    cd fam
    git checkout Main && git pull                 # the latest production code
    git checkout -b my-fix                        # a new branch
    # ...edit...
    git add -A && git commit -m "What and why"
    git push -u origin my-fix                     # then open a PR on GitHub

Never `git push --force` to `Main` or `staging`.
