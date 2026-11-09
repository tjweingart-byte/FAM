# 1. Access: your accounts, and what stays with the owner

You get **your own login** on every service, never a shared password. That
way your access can be removed in one click later, and every dashboard
records who did what.

## Part A. The owner grants access (once, before the handover)

The owner works down this table. The **Needed for** column lists the pages
the handbook sends the operator to. The role granted must be able to open
them. Role names differ between vendors and change over time, so **check
each one in the trial week** (Part B, step 6).

| Service | What to grant | Needed for | Done? |
|---|---|---|---|
| **GitHub** `tjweingart-byte/fam` | Settings → **Collaborators** → **Add people** → role **Write** (not Admin) | Opening, merging and reverting pull requests (02) | **TODO(owner)** |
| **Claude** | A seat on the owner's Claude organisation, or the operator's own plan. The owner also approves the Claude GitHub app on the repository. | Claude Code sessions (03) | **TODO(owner)** |
| **Anthropic Console** | Invite to the organisation, with a role that can see **Usage/Cost** and **Limits**, and create and disable API keys | 03 Task 3 | **TODO(owner)** |
| **Render** | Invite to the workspace as **Developer** (not Admin) | Events, Logs, Environment, Shell, Rollback, the database page (04) | **TODO(owner)** |
| **RunPod** | Invite to the team, with a role that can **edit the FAM endpoint** and **see Billing** | 05 Tasks 2-4 | **TODO(owner)** |
| **Cloudflare** | **Manage Account → Members** → invite, with R2 access only (R2 settings and API tokens) | 06 Tasks 2-3 | **TODO(owner)** |
| **MongoDB Atlas** | Project FAM → **Access Manager** → invite, with a role that can edit **Network Access** and **Database Access** (usually **Project Owner**) | 07 Tasks 2-3 | **TODO(owner)** |
| **A production FAM account** | The operator signs up in the production app. FAM has a waitlist, so new accounts wait to be let in. The owner lets this one in: `/admin/waitlist` → type the email in **Search email or @handle** → **Grant**. | Playing episodes in every check | **TODO(owner)** |
| **Production `/admin`** | Add that account's email to `FAM_ADMIN_ACCOUNTS` on Render `fam` (Part C) | Admin page (08, 09, 10) | **TODO(owner)** |
| **A staging FAM account, plus a second test account** | The operator signs up on the staging app, and the owner makes a second account so messages can be tested | 02 Step 3 | **TODO(owner)** |
| **Staging `/admin`** | The same as production, on `fam-staging` | | **TODO(owner)** |
| **Alert emails** | Make sure the operator receives them (Part D) | Hearing about problems | **TODO(owner)** |
| **Secrets manager** (only if Render's Environment page has a setting named `FAM_SECRETS`) | Access to the vault it names | Replacing a key (10) | **TODO(owner)** |
| **Other vendors** (GNews, BALLDONTLIE, Tiingo, Perplexity, Parallel, Gemini, Open-Meteo, Viral Loops, the email provider) | A login, or read-only access, to each one's usage and billing pages | 08 Task 2 | **TODO(owner)** |

## Part B. The operator checks each login works (once)

**Step 1. Accept every invitation email.**

> **Check:** each service's dashboard opens when you sign in.

**Step 2. Check Claude can see the repository.**
1. Open `https://claude.ai/code`, sign in.
2. Start a new session. Choose the repository `tjweingart-byte/fam`.

> **Check:** the session shows `tjweingart-byte/fam` as its repository.
>
> **If not:** you may need to connect your GitHub account. Follow the
> prompt to do that. If it then asks you to **install the Claude GitHub
> app**, stop: only the owner can approve that. Ask them.

**Step 3. See the Render services.**
1. Open `https://dashboard.render.com`.

> **Check:** you see `fam` and `fam-staging` in the list, and the database
> (or databases) named in 00.
>
> **If not:** you may be in the wrong workspace. Click the workspace name at
> the top left and choose the other one.

**Step 4. Sign in to the production app** with your production FAM account.

> **Check:** you reach the home screen and can play an episode.
>
> **If not:** a waitlist screen means the owner has not let your account in
> yet (Part A).

**Step 5. Open production's admin page** (after Part C is done).
1. Open `<production address>/admin`.
2. Sign in with your production FAM account's email and password.

> **Check:** you see the admin page, with a menu button at the top left
> (three lines). Its menu lists **Overview**, **Pipeline errors**,
> **Reported content**, **Outside service requests** and more.
>
> **If not:**
> - `That email and password are not an admin's.`: your email is not in
>   `FAM_ADMIN_ACCOUNTS` yet, or the change has not finished deploying.
>   Wait 15 minutes and try again.
> - A **Not Found** page: no admin is set up on this service at all. Tell
>   the owner.

**Step 6. The trial week.** With the owner watching, open every page in the
**Needed for** column above once. Tick each in a list. Anything you cannot
open means the role must change. Also do one full change through staging
to production ([02](02_GITHUB.md)) and one rollback on staging
([04](04_RENDER.md#roll-back-to-the-last-good-version)).

## Part C. Adding someone to the admin list

On Render, settings are called "environment variables". Saving one
restarts the server: a deploy of 5-15 minutes, with no downtime.

1. Render → click **fam** → in the left menu, **Environment**.

   > **Check:** the page title shows `fam`, not `fam-staging`.

2. Find `FAM_ADMIN_ACCOUNTS` in the list.

   > **If not:** if it is not there, **stop and ask the owner. Do not
   > create it**: a new, wrong value could lock every admin out.

3. Click **Edit** (top right of the list). The value may be hidden as dots.
   Click the eye icon to show it.
4. **Do not delete any email already there.** Click at the very end of the
   value, type a comma, then the new email, with no spaces:
   `owner@example.com,operator@example.com`
5. Click **Save, rebuild, and deploy** (or **Save and deploy**). **Not**
   **Save only**, which stores the change without applying it.

   > **Check:** in the left menu → **Events**, a new deploy starts, then
   > `Deploy live` within 15 minutes.

6. Repeat on **fam-staging** with the person's staging account email.

## Part D. Alert emails

FAM emails when something breaks. The simplest setup is one shared address
(for example `ops@familiarize.net`) that forwards to both the owner and the
operator.

**Step 1. See where alerts go.** Render → **fam** → **Environment**. Note
the value of each:

| Setting | What arrives there |
|---|---|
| `SUPPORT_EMAIL` | **The voice is down.** Sent within minutes, then daily while it lasts, then when it is back. |
| `IT_ALERT_EMAIL` | Urgent problems found by Keith, the IT agent. Only if the agent is switched on (the setting `IT_AGENT` is `1`). |
| `MODERATION_EMAIL` (or, if it is missing, `SUPPORT_EMAIL`) | A listener reported someone or something. Must be answered within 24 hours. |

> **Check:** every one of these is an address you receive. **If not:** ask
> the owner to change it, or to forward it to you.

**Step 2. Prove FAM can send email.** In the production app's sign-in
screen, sign out, then tap **Forgot password?** and enter your own email.

> **Check:** the reset email arrives within a few minutes. Note the sender
> address (**TODO(owner):** write it here), so you recognise alerts.
>
> **If not:** check your spam folder. If it is not there either, email
> sending is broken, and no alert can reach you. Tell the owner.

**Step 3. Get Render's and GitHub's own alerts.**
- Render: your account picture (top right) → **Account Settings** →
  **Notifications** → turn on email for failed deploys.
- GitHub: your picture (top right) → **Settings** → **Notifications** →
  **Actions** → notify on failed workflows.

## Owner-only decisions

You keep things running. These change what FAM is, spend money nobody
planned, or cannot be undone. **They wait for the owner, even if a Claude
session offers to do them.**

| Leave for the owner | Why |
|---|---|
| **Signing an agent rule book** | It needs the owner's private key. You review the proposal; the owner signs. See [09](09_AGENT_RULE_SIGNOFF.md). |
| Changing a rule in `CLAUDE.md` or `docs/claude/` | These are the owner's product decisions. |
| Buying, upgrading or cancelling any plan | Spending money. |
| Deleting data. Anything with `--all`, `--yes` or `--replace` in it; dropping the database; emptying the storage bucket; restoring a backup. | It cannot be undone. |
| Turning the waitlist off, or letting large batches in | A launch decision. |
| Switching an agent on or off | The agents are still being trained. |
| Branch protection, CODEOWNERS, who has access | Security. |
| The voice recording and its consent record | It is a real person's voice. |
| Copying episodes to staging (`tools/replay_episodes.py`) | It needs a terminal and admin tokens. |

**Things you may do without asking, then tell the owner:**
- **Roll back** a broken deploy ([04 Task 3](04_RENDER.md#roll-back-to-the-last-good-version)).
- **Revert** a pull request that broke something ([02](02_GITHUB.md#undoing-a-change-revert)).
- Use an **emergency switch** named in 06 or 07.
- **Replace a leaked key** ([10](10_RUNBOOKS.md#a-key-has-leaked)).

## When the operator leaves

1. Remove them from every service in Part A.
2. Remove their email from `FAM_ADMIN_ACCOUNTS` on both Render services
   (Part C, deleting instead of adding).
3. Change `FAM_ADMIN_TOKEN` on both services
   ([10 → A key has leaked](10_RUNBOOKS.md#a-key-has-leaked)), and any key they may have copied.
