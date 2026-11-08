# 1. Access: the accounts you need, and what stays with the owner

Give the backend operator **their own login** on every service, never a
shared password. Then removing access later is one click, and every
dashboard's audit log shows who did what.

## The accounts

Fill in the last two columns during the handover.

| Service | What the operator needs | Role to grant | Granted? | Notes |
|---|---|---|---|---|
| **GitHub** `tjweingart-byte/fam` | Review and merge PRs, read CI, revert | Collaborator with **Write** (not Admin) | **TODO(owner)** | Admin can change branch protection and CODEOWNERS. Keep that with the owner. |
| **Claude** (claude.ai, Claude Code) | Start cloud sessions on the repo, publish the preview | A seat on the owner's Team / Enterprise org, or the operator's own Claude plan with the GitHub app connected | **TODO(owner)** | The GitHub connection is per person: they connect at claude.ai and install the Claude GitHub app on the repo. See [03_CLAUDE.md](03_CLAUDE.md). |
| **Anthropic Console** (API) | See spend, see rate limits, rotate the key in an emergency | Member of the organisation; **Developer** is enough for keys, **Billing** to see invoices | **TODO(owner)** | The production key's org. Set a spend limit before handing over. |
| **Render** | Deploys, logs, Shell, environment variables, the database | Workspace member, **Developer** role | **TODO(owner)** | Admin role can delete services and change billing. Keep that with the owner. |
| **RunPod** | Endpoint builds, requests, worker logs | Team member (RunPod "Teams") with **Dev** access | **TODO(owner)** | If the account is personal, not a team, it needs converting first. Billing stays with the owner. |
| **Cloudflare** | R2 bucket, CORS, lifecycle; DNS if the domain is here | Account member, role **R2 Admin** (add **DNS** only if the operator should touch the domain) | **TODO(owner)** | Never the account's Super Administrator. |
| **MongoDB Atlas** | Cluster metrics, network access, database users | Project role **Project Data Access Admin** or **Project Owner** on project FAM | **TODO(owner)** | Organisation owner stays the owner. |
| **Production `/admin`** | The live tracker, waitlist, thumbnails | Add the operator's FAM account email to `FAM_ADMIN_ACCOUNTS` on Render | **TODO(owner)** | Comma-separated. Saving redeploys. Admin accounts are also `unlimited` on quotas. |
| **Staging `/admin`** | The same, on staging | Add them to staging's `FAM_ADMIN_ACCOUNTS` | **TODO(owner)** | Staging has its own accounts: they sign up there once. |
| **Secrets manager** (if `FAM_SECRETS` is set) | Rotate a key | Read/write on the `fam` secret | **TODO(owner)** | Check Render → `fam` → Environment for `FAM_SECRETS`. If it is not there, every key is on Render's Environment page. |
| **Other vendors** (GNews, BALLDONTLIE, Tiingo, Perplexity, Parallel, Gemini, Open-Meteo, Viral Loops, the SMTP provider, Apple Developer) | Usually nothing day to day. Usage pages if a limit is hit. | Read-only / member where the vendor offers it | **TODO(owner)** | Listed in [08_OTHER_SERVICES.md](08_OTHER_SERVICES.md). |

## Alert inboxes to join

FAM emails when something breaks. Make sure the operator receives these.
Each is a variable on Render `fam` → Environment. The cleanest setup is a
shared address (for example `ops@familiarize.net`) that forwards to both
of you.

| Variable | What arrives there |
|---|---|
| `SUPPORT_EMAIL` | **Voice down**: within minutes of the voice failing, daily while it lasts, and when it is back. Also the published support contact. |
| `IT_ALERT_EMAIL` | Urgent incidents from Keith, the IT agent (only while `IT_AGENT=1`): a store not writing, a provider failing, a rule-book breach. |
| `MODERATION_EMAIL` (falls back to `SUPPORT_EMAIL`) | A listener reported a message, a person or a vibe. Apple requires these to be acted on. |

Also make sure the operator gets Render's own deploy-failure emails
(Render → Account Settings → Notifications) and the CI-failure emails from
GitHub (they come to whoever pushed or merged).

## Owner-only decisions

The operator keeps things running. These change what FAM is, cost money in
a way nobody planned, or cannot be undone, so they wait for the owner,
even if a Claude session offers to do them.

| Leave for the owner | Why |
|---|---|
| **Signing an agent rule book** | Needs the owner's private key, and that is the point of it. The operator *prepares and reviews*: [09_AGENT_RULE_SIGNOFF.md](09_AGENT_RULE_SIGNOFF.md). |
| Changing a rule in `CLAUDE.md` or `docs/claude/` (the settled constraints) | These are the owner's product decisions. A PR that reverses one waits. |
| Buying, upgrading or cancelling any plan | Spend. The upgrade triggers are in `docs/SCALING_TIMELINE.md`; flag them, do not act on them. |
| Deleting data: `wipe_demo_data.py --all`, dropping a database, emptying the bucket, deleting an Atlas cluster | Cannot be undone. |
| Turning `WAITLIST` off, or granting large batches from the waitlist | A launch decision. |
| Changing branch protection, CODEOWNERS, or who has access | Security. |
| Rotating the rule-book signing key | Only the owner holds it. |
| Changing the voice (`reference_3.wav`, the rights record, `VOICE_REFERENCE_FINGERPRINT`) | It is somebody's voice, under a signed consent. |

**Exception: a leaked key.** If a key is exposed (pasted in a chat,
committed, shown in a log), rotate it immediately without waiting
([10_RUNBOOKS.md](10_RUNBOOKS.md#a-key-has-leaked)), then tell the owner.

## When the operator leaves

Remove them from each service in the table, take their email out of
`FAM_ADMIN_ACCOUNTS` on both services, and rotate `FAM_ADMIN_TOKEN` and any
key they may have copied.
