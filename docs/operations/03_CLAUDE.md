# 3. Claude: writing and changing FAM with Claude Code

Almost all of FAM's code is written by Claude Code sessions working on the
repository. The owner directs them, reads what they did, and merges. As the
operator you do the same. Claude also runs *inside* FAM, through the
Anthropic API (writer, brief, titles, agents). That account is covered at
the end.

## Before your first session

1. **A Claude account** with Claude Code (see [01_ACCESS.md](01_ACCESS.md)).
2. **Connect GitHub**: at claude.ai, connect your GitHub account and make
   sure the Claude GitHub app is installed on `tjweingart-byte/fam`. Without
   it a session cannot push.
3. Read the headings of `CLAUDE.md` once. Claude reads all of it at the start
   of every session. It holds the owner's settled decisions, and Claude will
   refuse, or push back on, requests that break them. That is intended.

## Starting a session

At **claude.ai/code** (web, desktop or phone): new session → choose the
repository `tjweingart-byte/fam` → type the task.

- **Each session gets its own branch** (`claude/<something>`). It says which
  in its reply. It commits and pushes there, and does **not** open a pull
  request unless you ask. You open the PR ([02_GITHUB.md](02_GITHUB.md#open-a-pull-request-from-a-branch-claude-pushed)).
- **One task per session.** A new topic is a new session. Long sessions get
  summarised and lose detail.
- **A session is a cloud container.** It has no API keys and no voice, so it
  cannot judge writing quality or latency. It runs the full test suite and
  the browser smoke tests. Writing and voice must be checked on production
  with a key (`python write.py`, `tools/voice_doctor.py`).

## The ship loop, which Claude follows on its own

`CLAUDE.md` tells every session to finish every change the same way. Expect
to see each step in its reply. If one is missing, ask for it.

1. `./dev.sh check`: every test, the interface checks, the previews and
   the browser smoke runs. It ends `all checks passed` twice.
2. Rebuild and republish the phone preview to the same link:
   `https://claude.ai/code/artifact/c8bd86aa-e61e-4262-a1c8-b9c8d8d6645e`.
3. A short summary and the preview link.
4. The exact command for anything it could not do itself.

It also logs what it did in `PROBLEMS.md` (the engineering log) as a new
numbered section. That log is how the next session, the owner and you know
why something is the way it is.

## Prompts that work

Be concrete and say the outcome you want. Paste errors whole.

| You want | Say |
|---|---|
| Fix a failing PR | "CI fails on PR #NNN with this output: <paste>. Fix it on this branch, run `./dev.sh check`, push." |
| Resolve a conflict | "Branch `claude/xyz` conflicts with `Main`. Merge `origin/Main` into it, resolve the conflicts, run `./dev.sh check`, push." |
| Investigate production | "Production `/api/health` shows <paste the block>. What is wrong and what should I change on Render? Do not change code yet." |
| Explain something | "How does <thing> work? Answer from the code and cite files and lines. Do not change anything." |
| A small change | "<the change>. Follow the ship loop. Do not open a PR." |
| Undo a change | "Revert the change from PR #NNN on a new branch, run checks, push." (Or use GitHub's Revert button.) |
| An agent rule-book change | See [09_AGENT_RULE_SIGNOFF.md](09_AGENT_RULE_SIGNOFF.md). The session writes a *proposal*. It cannot sign. |

**Never paste a secret into a session.** No API key, password, connection
string or admin token. If a session needs to read production, give it the
`/api/health` output (which never contains secrets) or the log lines. If it
asks for a key, the answer is no; it can say which variable to set and you
set it on Render yourself.

## Checking Claude's work before you merge

Claude reports confidently whether it is right or wrong. Check:

1. **Did it run the checks?** Its reply should quote `all checks passed`. CI
   on the PR must be green, whatever the reply says.
2. **Did it change only what you asked?** Look at "Files changed" on the PR.
3. **Did it touch an owner-only area?** Rule books, `CLAUDE.md` rules,
   CODEOWNERS, a deletion, a plan ([01_ACCESS.md](01_ACCESS.md#owner-only-decisions)).
   If so, stop and leave it for the owner.
4. **Look at the preview** for anything visible: open the preview link on a
   phone.
5. **After merging**, check production `/api/health` → `build.commit`, and
   play an episode.

If you are not sure, ask a *new* session to review the PR ("Review PR #NNN
for bugs. Do not change anything."). A fresh session is not anchored on the
first one's reasoning.

## Things Claude cannot do from a session

- See Render, RunPod, Cloudflare or Atlas dashboards (it has no logins).
- Run anything on production. You run Render Shell commands it gives you.
- Hear the voice, or judge an episode's writing on real data.
- Sign a rule book.

## The Anthropic API account (Claude inside FAM)

Production calls Claude for the brief, the writer, titles, categories,
pictures' checks and the agents. One key, on Render `fam` as
`ANTHROPIC_API_KEY` (or via `FAM_SECRETS`). Staging has none.

| Task | Where |
|---|---|
| See spend | console.anthropic.com → Usage / Cost. FAM's own per-listener view: `python tools/usage_report.py --days 30` or `/api/usage` (`docs/ONBOARDING.md` §7.5). |
| Set a spend limit | Console → Limits. **TODO(owner):** set a monthly limit and an email alert before handover. |
| Rate limits | Console → Limits. Limits are per organisation, not per key: a second key adds no headroom (`CREDENTIALS.md`). |
| Rotate the key | Console → API Keys → create a new key → put it on Render `fam` → Environment → `ANTHROPIC_API_KEY` → Save (redeploys) → check `/api/health` → `mode` is live → disable the old key in the Console. |
| Which models | Set in code and on Render: `MODEL` (on Render), `WRITER_MODEL`, and each agent's pinned model in `agents/models.py`. Do not change these without the owner; the writing was tuned on them. |

**If the key stops working**, episodes do not fail: they quietly become the
built-in sample script. `/api/health` → `mode` and `api_key_configured` say
so. See [10_RUNBOOKS.md](10_RUNBOOKS.md#every-episode-is-the-same-sample-script).
