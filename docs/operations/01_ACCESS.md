# 1. Access: your accounts, and what stays with the owner

You get **your own login** on every service, never a shared password. That
way your access can be removed in one click later, and every dashboard
records who did what.

## Part A. The owner grants access (once, before the handover)

The owner works down this table and ticks each row.

| Service | What to grant | Done? |
|---|---|---|
| **GitHub** `tjweingart-byte/fam` | Settings → **Collaborators** → **Add people** → role **Write** (not Admin) | **TODO(owner)** |
| **Claude** (claude.ai, Claude Code) | A seat on the owner's Claude organisation, or the operator's own plan. The operator then connects GitHub (Part B, step 2). | **TODO(owner)** |
| **Anthropic Console** | Settings → **Members** → invite → role **Developer** (or **Billing** to see invoices) | **TODO(owner)** |
| **Render** | Workspace → **Settings** → **Members** → invite → role **Developer** (not Admin) | **TODO(owner)** |
| **RunPod** | Team settings → invite → role **Dev** (billing stays with the owner) | **TODO(owner)** |
| **Cloudflare** | **Manage Account** → **Members** → invite → role **R2 Admin** only | **TODO(owner)** |
| **MongoDB Atlas** | Project FAM → **Access Manager** → invite → **Project Data Access Admin** | **TODO(owner)** |
| **Production `/admin`** | Add the operator's FAM email to `FAM_ADMIN_ACCOUNTS` on Render `fam` (Part C) | **TODO(owner)** |
| **Staging `/admin`** | The same on `fam-staging` | **TODO(owner)** |
| **Alert emails** | Make sure the operator receives them (Part D) | **TODO(owner)** |
| **Secrets manager** (only if `FAM_SECRETS` is set on Render) | Read/write on the `fam` secret | **TODO(owner)** |
| **Other vendors** (GNews, BALLDONTLIE, Tiingo, Perplexity, Parallel, Gemini, Open-Meteo, Viral Loops, the email provider, Apple Developer) | Usually nothing. Read-only access where the vendor offers it. | **TODO(owner)** |

## Part B. The operator checks each login works (once)

**Step 1. Accept every invitation email.**

> **Check:** each service's dashboard opens when you sign in.

**Step 2. Connect Claude to GitHub.**
1. Go to `https://claude.ai/code`, sign in.
2. Start a new session. Choose the repository `tjweingart-byte/fam`. If it is
   not listed, follow the prompt to connect GitHub and install the **Claude**
   GitHub app on that repository.

> **Check:** the session shows `tjweingart-byte/fam` as its repository.
>
> **If not:** ask the owner to approve the Claude app on the repository
> (GitHub → Settings → GitHub Apps).

**Step 3. See the two Render services.**
1. Open `https://dashboard.render.com`.

> **Check:** you see `fam` and `fam-staging` in the list of services, and a
> Postgres database.
>
> **If not:** you are in the wrong workspace. Use the workspace switcher at
> the top left.

**Step 4. Open production's admin page** (after Part C is done).
1. Open `<production address>/admin`.
2. Sign in with your FAM account's email and password.

> **Check:** you see the live tracker: counts of accounts, episodes and
> searches, and a list of errors.
>
> **If not:** a "not found" page means your email is not in
> `FAM_ADMIN_ACCOUNTS` yet, or the change has not finished deploying. Wait
> five minutes and try again.

## Part C. Adding someone to the admin list

On Render, these settings are called "environment variables". Saving one
restarts the server, which takes a few minutes and causes no downtime.

1. Render → click **fam** → in the left menu, **Environment**.
2. Find `FAM_ADMIN_ACCOUNTS`. Click **Edit** (top right of the list).
3. Add the new email at the end, separated by a comma, with no spaces:
   `owner@example.com,operator@example.com`
4. Click **Save, rebuild, and deploy** (or **Save and deploy**).

> **Check:** the left menu → **Events** shows a new deploy starting, then
> `Deploy live` a few minutes later.

5. Repeat on **fam-staging** with the person's staging account email (they
   sign up on staging once, separately).

## Part D. Alert emails

FAM emails when something breaks. Each address is set on Render `fam` →
Environment. The simplest setup is one shared address (for example
`ops@familiarize.net`) that forwards to both the owner and the operator.

| Setting | What arrives there |
|---|---|
| `SUPPORT_EMAIL` | **The voice is down.** Sent within minutes, then daily while it lasts, then when it is back. |
| `IT_ALERT_EMAIL` | Urgent problems found by Keith, the IT agent (when `IT_AGENT=1`). |
| `MODERATION_EMAIL` (or `SUPPORT_EMAIL`) | A listener reported someone or something. Must be acted on. |

Also turn on:
- **Render:** your account (top right) → **Account Settings** →
  **Notifications** → email on deploy failure.
- **GitHub:** your profile → **Settings** → **Notifications** → Actions:
  notify on failed workflows.

> **Check:** on the production app's sign-in screen, tap **Forgot
> password?** for your own account. The reset email arrives within a few
> minutes. That proves FAM can send email, so the alerts can reach you too.
>
> **If not:** check your spam folder. If it is not there either, email
> sending is broken. Tell the owner, because no alert would reach you.

## Owner-only decisions

You keep things running. These change what FAM is, spend money nobody
planned, or cannot be undone. **They wait for the owner, even if a Claude
session offers to do them.**

| Leave for the owner | Why |
|---|---|
| **Signing an agent rule book** | It needs the owner's private key. You review the proposal; the owner signs. See [09](09_AGENT_RULE_SIGNOFF.md). |
| Changing a rule in `CLAUDE.md` or `docs/claude/` | These are the owner's product decisions. |
| Buying, upgrading or cancelling any plan | Spending money. |
| Deleting data: anything with `--all`, `--yes` or `--replace`, dropping the database, emptying the bucket | It cannot be undone. |
| Turning the waitlist off, or letting large batches in | A launch decision. |
| Branch protection, CODEOWNERS, who has access | Security. |
| The voice recording and its consent record | It is a real person's voice. |

**The one exception: a leaked key.** If a password or key has been exposed,
replace it straight away without waiting
([10_RUNBOOKS.md → A key has leaked](10_RUNBOOKS.md#a-key-has-leaked)), then
tell the owner.

## When the operator leaves

1. Remove them from every service in Part A.
2. Remove their email from `FAM_ADMIN_ACCOUNTS` on both Render services (Part C).
3. Change `FAM_ADMIN_TOKEN` on both services, and any key they may have copied.
