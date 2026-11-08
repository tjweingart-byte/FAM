# 10. Runbooks: incidents and routine checks

Each runbook is: how you notice, what to check, what to do, and when to
stop and call the owner. Keep a short incident note (what you saw, what you
changed, when) and send it to the owner afterwards. If the incident taught
something, ask a Claude session to add it to `PROBLEMS.md`.

`<prod-host>` below is production's address ([00_SYSTEM_MAP.md](00_SYSTEM_MAP.md#the-hosts)).

---

## Routine checks

### Every working day (5 minutes)

1. **`/api/health`**: `build.commit` is what you expect; `mode` live;
   `database.reachable` true; `storage` nothing `ephemeral`;
   `tts.remote.reachable.state` ok; `audio.check.ok` true; `archive.errors`
   0; `agents.lock.signed` true and `held` empty.
2. **`/admin`**: the errors list; **Outside services, requests per day**
   (anything near its limit or red); new accounts and top searches look
   sane.
3. **GitHub**: CI on `Main` is green (Actions tab, filter to `Main`).
4. **Inboxes**: anything from `SUPPORT_EMAIL`, `IT_ALERT_EMAIL`,
   `MODERATION_EMAIL`, Render or GitHub.
5. **Play one new episode and one replay** in the app.

### Every week

- **Spend:** Anthropic Console, RunPod Billing, Render Billing, Cloudflare
  R2, Atlas. Compare with last week. Anything doubled goes to the owner.
- **Per-listener cost and abuse flags:** in Render Shell,
  `python tools/usage_report.py --days 7 --flagged`.
- **Moderation:** `/admin` reports, and act on anything unresolved (Apple
  requires reports to be handled).
- **Waitlist:** `/admin/waitlist` → `outbox_pending` is 0 or draining.
- **Open PRs:** anything old, red or waiting for review.

---

## The site is down

**Notice:** the app does not load; `/api/health` does not answer; Render
emails.

1. Render → `fam` → **Events**. Was there a deploy just now?
   - **Yes, and it failed:** the old version should still be serving. If it
     is not, **Rollback** to the last good deploy.
   - **Yes, and it is live but broken:** **Rollback**, then revert the PR on
     GitHub ([02_GITHUB.md](02_GITHUB.md#undo-a-merge-revert)).
2. Render → `fam` → **Logs**: the last lines before it stopped. Look for
   `Traceback` and for a database error.
3. Render status page (status.render.com) for a platform outage.
4. Render → the Postgres database: is it **Available**? Is its storage
   full?
5. Still down after rollback, and Render and the database look healthy:
   **call the owner.**

## A deploy failed

The previous version keeps serving, so this is not urgent for listeners.

1. Render → `fam` → Events → the failed deploy → read the log.
2. If it is the start-up guard about an empty database (§254): **stop and
   call the owner.** Do not set `DATABASE_ALLOW_EMPTY`.
3. Otherwise try **Manual Deploy → Clear build cache & deploy** once.
4. Still failing: give the log to a Claude session on a new branch ("This
   Render deploy of commit X fails with <log>. Find the cause and fix it.").
   Merge the fix through a PR as normal.

## Episodes play a placeholder tone, or stall at "Generating the audio"

The voice. Work through [05_RUNPOD.md](05_RUNPOD.md#when-episodes-stall-at-generating-the-audio).
The short version:

1. `/api/health` → `tts`: `interim`, `remote.reachable` (with the reason).
2. Render Shell: `python tools/voice_doctor.py`.
3. RunPod → FAM endpoint → Builds (a failed build? roll back), Workers
   (throttled?), Requests (delay times), Billing (credit?).
4. Was there a merge to `Main` in the last hour? The first worker on a new
   build is slow. Wait ten minutes before acting.

Call the owner if the voice is down for more than an hour and none of the
above explains it.

## Every episode is the same sample script

Claude is not being reached.

1. `/api/health` → `mode` and `api_key_configured`, and `credentials`
   (where the key came from, and whether a secrets provider failed).
2. Anthropic Console: is the key still active? Has the organisation hit its
   **spend limit** or been rate-limited? Is there an outage
   (status.anthropic.com)?
3. If the key was revoked or the limit hit: raise the limit (if the owner
   has agreed a ceiling) or rotate the key ([03_CLAUDE.md](03_CLAUDE.md#the-anthropic-api-account-claude-inside-fam)).

## Episodes are thin, out of date, or say they found nothing

Research.

1. `/admin` → Outside services: is Perplexity or Parallel failing or at its
   limit?
2. `/api/health` → `research`, `gdelt`.
3. Each provider's dashboard: credit, key status.
4. FAM falls back automatically (Perplexity → Parallel → GDELT), so this is
   quality, not an outage. Top up the account if it is out of credit and the
   owner has agreed the spend; otherwise tell the owner.

## Trending is empty

1. `/api/health` → `trending`: last edition time and any error.
2. GNews dashboard: is the key active, the plan Essential, the daily limit
   reached?
3. Trending is rebuilt at 05:00 and 17:00 ET. After a fix, it fills at the
   next edition. There is deliberately no fallback source.

## Accounts look missing, or sign-in fails for everyone

**Treat this as serious and do not change anything that writes.**

1. `/api/health` → `database` (`backend: postgres`, `reachable: true`) and
   `storage` (anything `ephemeral`?).
2. Render Shell: `python tools/storage_doctor.py`.
3. Render → the Postgres database: Available? A recent restore or plan
   change?
4. Has anyone changed `DATABASE_URL` on Render? (Render → `fam` → Events
   lists environment changes.)
5. **Call the owner** before any restore. A wrong restore loses everything
   written since.

## Replays fail but new episodes play

R2. See [06_CLOUDFLARE.md](06_CLOUDFLARE.md#is-it-working).

1. `/api/health` → `audio`: `check.ok`, `links.fallbacks`.
2. If links fail everywhere: set `AUDIO_LINKS=0` on Render (replays stream
   through Render), tell the owner, and investigate the R2 token and CORS.

## Pictures missing everywhere

R2 again (pictures share the bucket), then `/api/health` → `thumbnails`.

## CI is red on `Main`

1. Actions → the failing run on `Main` → the failing step. Copy the error.
2. Did it start with a specific merge? Revert that PR if the cause is
   obvious.
3. Otherwise start a Claude session: "CI on `Main` fails since commit X
   with: <paste>. Find the cause and fix it on this branch." Note that CI
   runs Python 3.12 and a session's container runs 3.11. Claude knows to
   reproduce in 3.12.
4. Hold other merges until it is green.

## A key has leaked

A key was pasted in a chat, committed to the repository, printed in a log,
or sent somewhere it should not be. **Do this immediately, without waiting
for the owner:**

1. **Revoke and replace it** at the vendor (Anthropic Console, Render, RunPod,
   Cloudflare, Atlas, or the vendor in [08](08_OTHER_SERVICES.md)).
2. Put the new value on Render `fam` → Environment (or the secrets
   manager), Save, and check `/api/health`.
3. If it was **committed to GitHub**, rotating is the fix. Deleting the
   commit is not enough (it stays in history and forks). Ask a session to
   remove it from the current files too.
4. Tell the owner what leaked, where, and when it was rotated.

For `FAM_ADMIN_TOKEN`: set a new random value on Render (e.g. the output of
`python3 -c "import secrets; print(secrets.token_urlsafe(32))"`).

## A listener reports abuse, or Apple asks about a report

1. `/admin` → moderation reports.
2. Act on the report: block, remove the content (`moderation.py`, `/admin`).
3. Anything legal (threats, a court order, a law-enforcement request, a
   copyright notice): **forward to the owner the same day**, and do not
   reply yourself.

## Costs jump

1. Which service? Its own dashboard, and `/admin` → Outside services.
2. `python tools/usage_report.py --days 1 --flagged` in Render Shell: one
   listener, or everyone?
3. One abusive account: move it to the free plan, which is limited:

       curl -s -X POST "https://<prod-host>/api/admin/plan" -H "X-Admin-Token: $TOKEN" \
         -H 'Content-Type: application/json' -d '{"who": "<email or id>", "plan": "free"}'

4. Everyone: is `ENFORCE_QUOTAS=1` still set on Render? Did a merge change a
   schedule or a ceiling? Tell the owner.

## An agent is held, or misbehaving

- **Held:** [09_AGENT_RULE_SIGNOFF.md](09_AGENT_RULE_SIGNOFF.md#if-an-agent-is-held-and-nobody-changed-anything). Safe; FAM runs without it.
- **Misbehaving** (bad categories from Nando, Mclovin dropping good sentences,
  Consuela merging the wrong facts): its switch on Render can turn it off or
  back to dry run (`AGENTS`, `HOUSEKEEPER_DRY_RUN`, `IT_AGENT` ...). Write
  down what you saw with examples, and tell the owner. A fix to *what the
  agent is allowed to do* is a rule-book change ([09](09_AGENT_RULE_SIGNOFF.md)).

## When to call the owner straight away

- Production down after a rollback.
- Anything that looks like lost or exposed listener data.
- A deploy refusing to start because of the database.
- A legal or law-enforcement contact.
- Spend running away and you cannot find the cause.

**TODO(owner):** how to reach you in an emergency during the week, and what
counts as worth interrupting you for.
