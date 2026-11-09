# FAM backend operations handbook

> **Something is broken right now?** Go straight to
> [10_RUNBOOKS.md](10_RUNBOOKS.md). It starts with who to call and a list of
> symptoms. Come back here later.

**Who this is for:** the person who looks after FAM's backend during the
work week, when the owner is not available. You do not need to know Git,
Render or FAM's code. You need a web browser (**Firefox** is easiest, see
below), the logins in [01_ACCESS.md](01_ACCESS.md), and this handbook.

**What you will be able to do:** ship a change Claude has made (through
staging, then to production), undo a bad change, check that everything is
healthy, and handle the common problems. You will also know what to leave
for the owner.

> **Draft.** Anything marked **TODO(owner)** is a fact only the owner knows.
> The owner fills those in before the handover. Until then, a step that
> depends on a TODO cannot be done: ask the owner.

---

## Opening this handbook

The handbook lives in GitHub, inside FAM's code. Open
`https://github.com/tjweingart-byte/fam/tree/Main/docs/operations` and
click a file name. GitHub shows it as a formatted page with working links.
Bookmark that address.

---

## How to read the instructions

Every task is a numbered list of steps. After the important steps there is
a box like this:

> **Check:** what you should see on the screen if the step worked.
>
> **If not:** what to do instead.

**Do not go on to the next step until the Check matches.** If it does not
match and the "If not" does not fix it, stop. Take a screenshot, and write
down the document, the task and the step number. A half-finished task is
easy to fix. One pushed through on a guess often is not.

- Words in **bold** are the exact words on a button, tab or menu item.
- Words in `this font` are text you type, or exact text you will see on
  the screen.
- **Anything in `<angle brackets>` is a placeholder.** Replace the whole
  thing, brackets included, with the real value. For example
  `<production address>/api/health` becomes
  `https://familiarize.net/api/health`.
- `<production address>` and `<staging address>` are always in
  [00_SYSTEM_MAP.md → The addresses](00_SYSTEM_MAP.md#the-addresses). They
  already start with `https://`, so do not type it twice.

### No terminal needed

Everything in this handbook is done by clicking in a web browser. The one
exception is the **Render Shell**, which is a text box on the Render website
where you paste a command the handbook gives you
([04_RENDER.md → Task 5](04_RENDER.md#using-the-shell)).

If anything (a Claude session, an old document, a vendor's help page) asks
you to run a command **on your own computer**, do not. Ask Claude "Is there
a way to do this without a terminal?", or send it to the owner.

---

## Words you will meet

| Word | Means |
|---|---|
| **Repository** ("repo") | The folder on GitHub holding all of FAM's code and documents. FAM's is `tjweingart-byte/fam`. |
| **Branch** | A separate copy of the code where a change is made. `Main` is production, `staging` is the test copy, and each change has its own (Claude's are named `claude/something`). |
| **Commit** | One saved set of changes, with a 40-character code. People use only the first 7 characters, for example `a5ffa80`. |
| **Pull request (PR)** | A request on GitHub to copy changes from one branch into another. Each has a number, like `#232`. |
| **Merge** | Doing a pull request: the changes are copied into the other branch. |
| **Checks** / **CI** | The automatic tests GitHub runs on every pull request ("continuous integration"). They take **35-60 minutes**. |
| **Deploy** | Render putting a new version of FAM live. It happens by itself after a merge, or after a setting is saved. Takes **5-15 minutes**. |
| **Rollback** | Telling Render to go back to an earlier version. |
| **Revert** | A pull request that undoes an earlier one. |
| **Environment variable** ("setting") | A named value the server reads when it starts, like `FAM_ADMIN_ACCOUNTS`. Keys and passwords are stored this way. On Render they are on each service's **Environment** page. |
| **Key** / **token** | A password a program uses to talk to a service. Never share or paste one except where this handbook says. |
| **Secrets manager** (`FAM_SECRETS`) | An optional separate vault for keys. If Render's Environment page has a setting called `FAM_SECRETS`, some keys live there instead. If not, every key is on Render. |
| **Production** | The real FAM that listeners use: Render service `fam`, branch `Main`. |
| **Staging** | The test copy: Render service `fam-staging`, branch `staging`. |
| **Health page** | `<address>/api/health`, a page where the server describes its own state. See below. |
| **Admin page** | `<address>/admin`: counts, errors, reports from listeners, and outside services. |
| **JSON** | The layout of the health page: names and values inside curly brackets `{ }`. |

---

## Reading the health page

You will open `<production address>/api/health` (and sometimes the staging
one) more than anything else. It is very long, and the same word appears in
many places. Here is how to find a value reliably.

**1. Use Firefox.** Open the address in Firefox.

> **Check:** you see tabs **JSON · Raw Data · Headers** at the top, and a
> **Filter JSON** box. The page is a list you can fold and unfold.
>
> **If not:** you are not in Firefox, or the page did not load. A page that
> will not load at all means the site is down:
> [10_RUNBOOKS.md → The site is down](10_RUNBOOKS.md#the-site-is-down).
> (First check the address against 00, and try your phone on mobile data,
> in case it is your own internet.)

**2. Type the field's name into Filter JSON, without quote marks.** Firefox
hides every line that does not match, and keeps the section names above
the ones that do.

**3. Pick the match under the right section.** The handbook always writes a
field as its path, for example `database → reachable`, which means "the
`reachable` that sits inside `database`". Filtering for `reachable`
shows several. Choose the one whose section heading is `database`. (Not
`databases`, which is a different list.)

> **If you cannot tell which section a match is in:** clear the filter box.
> Click the little triangle beside each top-level name to fold everything,
> then open just the section you want (for example `database`) and read
> inside it.

The fields the handbook uses:

| Path | Healthy value | Means |
|---|---|---|
| `build → short` | the 7-character commit you expect | which version is running |
| `mode` | `live` | `demo` = no Anthropic key is set at all |
| `credentials → state` (the one directly under `credentials`, **not** under `credentials → secrets`) | `ok` | the Anthropic key worked when the server started. `rejected` = that start-up check failed: usually a refused key, sometimes an Anthropic outage at that moment (`detail` beside it says which). |
| `database → backend` | `postgres` | |
| `database → reachable` | `true` | the database answers |
| `storage → ephemeral` | `[]` (empty) | nothing will be wiped by the next deploy |
| `tts → interim` | `false` | `true` = listeners hear a placeholder tone instead of the voice |
| `tts → remote → reachable → state` | `ok` (or `unknown` just after a deploy) | the voice answered |
| `audio → check → ok` | `true` | the audio store (Cloudflare) worked when the server started. A later failure shows as `audio → links → fallbacks` rising. |
| `archive → enabled` | `true` | the history archive is **set up**. Whether it is **working** is `archive → errors` (low) and `archive → last_error` (empty). |
| `agents → lock → signed` | `true` | the agent rule books are signed |
| `agents → lock → held` | `{}` (empty) | no agent is stopped by its rule-book check |
| `environment → zero_spend` (staging only) | `true` | staging cannot spend money |

**Counters start again from 0 at every deploy.** Numbers like
`archive → errors`, `archive → intake_articles` and
`audio → links → issued` count since the server last started. So compare
them within the same day, never with yesterday.

**The health page never contains keys or passwords.** It is safe to copy
any part of it into a Claude session or an email.

---

## Wait times (the same everywhere in this handbook)

| Thing | How long | Worry after |
|---|---|---|
| GitHub checks on a pull request | 35-60 minutes | 75 minutes |
| A Render deploy | 5-15 minutes | 30 minutes |
| A Render rollback | 2-5 minutes | 15 minutes |
| The voice rebuilding after a merge into `Main` | up to 30 minutes | 45 minutes |
| The voice waking up after a quiet spell (first episode) | 60-90 seconds | 2 minutes |

---

## Read in this order

| # | Document | What it teaches you |
|---|---|---|
| 0 | [00_SYSTEM_MAP.md](00_SYSTEM_MAP.md) | The pieces of FAM, the addresses, and a quick tour of the app. |
| 1 | [01_ACCESS.md](01_ACCESS.md) | The accounts you need, and what you must leave for the owner. |
| 2 | [02_GITHUB.md](02_GITHUB.md) | **How a change gets to production:** a feature branch into `staging`, tested, then `staging` into `Main`. Undoing a change. |
| 3 | [03_CLAUDE.md](03_CLAUDE.md) | Asking Claude Code to make or fix a change, and checking its work. |
| 4 | [04_RENDER.md](04_RENDER.md) | The servers: deploys, rollback, changing a setting, the Shell, the database. |
| 5 | [05_RUNPOD.md](05_RUNPOD.md) | The voice: checking it, and what to do when it stops. |
| 6 | [06_CLOUDFLARE.md](06_CLOUDFLARE.md) | Where audio and pictures are stored. |
| 7 | [07_MONGODB.md](07_MONGODB.md) | The history archive. |
| 8 | [08_OTHER_SERVICES.md](08_OTHER_SERVICES.md) | Every other outside service, and what breaks when each fails. |
| 9 | [09_AGENT_RULE_SIGNOFF.md](09_AGENT_RULE_SIGNOFF.md) | Signing off on a change to an agent's rule book. |
| 10 | [10_RUNBOOKS.md](10_RUNBOOKS.md) | Daily and weekly checks, and what to do in each kind of emergency. |

There is also a slide deck that walks through all of this:
`FAM_Backend_Operations.pptx`, in the same folder. Click it on GitHub, then
**Download raw file**.

---

## The five rules of the job

1. **Look before you change anything, and look again after.** The health
   page and the admin page tell you what the server thinks is true.
2. **Changes reach production only through GitHub.** First into `staging`,
   tested, then into `Main`. Nobody edits code on the server.
3. **Never type a password or key into a chat, a document, an email, a
   commit or a Claude session.** Keys live only on Render's Environment page
   (or in the secrets manager, if there is one).
4. **Staging is safe; production is real listeners and real money.** Staging
   cannot spend on outside services. Try things there first. On production,
   change one thing at a time, and write down what you changed and when.
5. **Some decisions are the owner's.** The list is in
   [01_ACCESS.md → Owner-only decisions](01_ACCESS.md#owner-only-decisions).
   When in doubt, wait for the owner.

---

## When the handbook is wrong

If this handbook and a dashboard disagree, **the dashboard is right**.
Do not change the dashboard to match the handbook. Write down what you saw,
then ask a Claude session to correct the handbook (the "Update this
handbook" prompt in [03_CLAUDE.md](03_CLAUDE.md#prompts-to-copy)).

## Changing the slide deck

The deck is built from a plain-text file, `slides.md`, in this folder. Ask a
Claude session: `I want to change the operations deck: <what to change>.
Edit docs/operations/slides.md, rebuild the deck with
docs/operations/build_deck.py, and push.` Then take its branch through
[02_GITHUB.md](02_GITHUB.md) like any change.

## Where the deeper material is

You do not need these day to day. They explain *why* things are as they are:

- [`docs/ONBOARDING.md`](../ONBOARDING.md): what FAM is, and a course for learning the code.
- [`docs/BACKEND.md`](../BACKEND.md): every part of the server.
- [`docs/FINANCIAL.md`](../FINANCIAL.md), [`docs/SCALING_TIMELINE.md`](../SCALING_TIMELINE.md): costs, and when to upgrade.
- At the top of the repository: `STAGING.md`, `DEPLOY.md`, `CREDENTIALS.md`, `POSTGRES.md`, `REMOTE_VOICE.md`, `AUDIO_STORE.md`, `ARCHIVE.md`: how each service was set up.
- `CLAUDE.md` (top of the repository): the owner's settled decisions. Claude reads it every session.
- `PROBLEMS.md` (top of the repository): the engineering log.
