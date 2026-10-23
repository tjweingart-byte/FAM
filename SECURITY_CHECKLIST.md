# Second factor everywhere: the outside services checklist (§301)

FAM's own sign-in now has a second factor (ACCOUNTS.md, "Two-step sign-in").
That protects FAM's front door. It does nothing for the dashboards *behind*
FAM: whoever signs in to Render can read every environment variable, and
whoever signs in to Cloudflare can read every kept episode. Each of those is
its own sign-in, and each one needs a second factor turned on by a person -
no code in this repository can do it for them.

This is that list, taken from what FAM actually holds credentials for
(`.env.example`, `render.yaml`). Work down it once, fill in the last two
columns, and keep this file current when a service is added or dropped.

**The company vault is 1Password** (chosen 2026-10-23; Bitwarden Teams was
the cheaper alternative considered). "The password manager" below means it.
Four vaults: *Infrastructure* (GitHub, Render, Cloudflare, registrar, email -
founders only), *Vendors & API keys*, *Recovery codes* (founders only), and
*FAM Production* - the server's own keys, read by FAM through a service
account (`tools/onepassword_secrets.py`, CREDENTIALS.md) and by nothing else.

**The rule for every row:**

1. Turn on two-step sign-in with an **authenticator app or a passkey**. Use
   SMS only where nothing else is offered: a number can be moved to another
   SIM by a phone call to the carrier.
2. Save the service's **recovery codes** in 1Password's *Recovery codes*
   vault, not on one person's laptop.
3. Where the service has **teams or organisations**, require two-step
   sign-in for every member (most call it "enforce 2FA").
4. Sign in with a **company address** (`@familiarize.net`), never a personal
   one, so access survives somebody leaving.
5. Where a service offers **no** second factor, use a unique long password
   from the password manager and rotate its API key if anyone with access
   leaves. Say so in the table, so it is a known gap, not a forgotten one.

## Where FAM runs and stores everything (highest stakes first)

| Service | What a stolen sign-in exposes | Org-wide enforcement | Who holds it | 2FA on (date) |
|---|---|---|---|---|
| **1Password** (the company vault itself) | Every login and key on this page | Admin console → require two-step for every member; keep the Emergency Kit offline | | |
| **GitHub** (`tjweingart-byte/FAM`) | All source code; can push to `Main`, which Render deploys | Organisation settings → require 2FA | | |
| **Render** (`fam`, `fam-staging`, Postgres) | Every environment variable - so every key below - and the production database | Workspace settings → require 2FA | | |
| **Cloudflare** (R2 `AUDIO_BUCKET`; DNS if hosted there) | Every kept episode's audio; the domain's traffic | Members → require 2FA | | |
| **Domain registrar** for `familiarize.net` | The domain itself: email, sign-in links and the app's address | Account security | | |
| **Email provider** (company mailboxes, and the `SMTP_*` sender) | Password-reset codes go out through it; the founders' inboxes reset everything else on this list | Admin console → enforce 2-step | | |
| **Anthropic Console** (`ANTHROPIC_API_KEY`) | The writing model; billing | Organisation → require 2FA / SSO | | |
| **RunPod** (`RUNPOD_API_KEY`, the voice GPU) | Start/stop the voice; billing | Account security | | |
| **Apple Developer / App Store Connect** | The iOS app's releases, Sign in with Apple keys, payments | Apple requires it on the Apple ID (APP_STORE.md step 5) | | |
| **Google Cloud** (Sign in with Google client) | The OAuth client listeners sign in through | Organisation → enforce 2-step | | |
| **Secrets manager** (whatever `FAM_SECRETS` points to, if used) | Every credential at once | Per provider | | |

## Data and research providers (an API key each)

A stolen sign-in here mostly costs money or quota and lets somebody read the
key. Turn on a second factor where the dashboard offers one; otherwise
rule 5.

| Service | Key in FAM | Who holds it | 2FA on, or "not offered" (date) |
|---|---|---|---|
| Parallel | `PARALLEL_API_KEY` | | |
| Perplexity | `PERPLEXITY_API_KEY` | | |
| Google Gemini (thumbnails) | `GEMINI_API_KEY` | | |
| GNews | `GNEWS_KEY` | | |
| BALLDONTLIE | `BALLDONTLIE_API_KEY` | | |
| API-Sports | `API_SPORTS_KEY` | | |
| Tiingo | `TIINGO_API_KEY` | | |
| Finnhub | `FINNHUB_KEY` | | |
| Viral Loops (waitlist referrals) | `VIRAL_LOOPS_API_TOKEN` | | |
| Wikimedia Enterprise | `WIKIMEDIA_ENTERPRISE_USERNAME` / `_PASSWORD` | | |
| Open-Meteo (if on a paid plan) | `OPEN_METEO_API_KEY` | | |
| AP Elections / DDHQ (if used) | `AP_ELECTIONS_KEY` / `DDHQ_KEY` | | |

GDELT, Polymarket and the National Weather Service need no account, so
there is nothing to protect.

## FAM's own credentials (no outside sign-in, but the same care)

* **Keep the most important codes out of the vault.** For GitHub, Render,
  the email/domain admin and FAM's admin accounts, keep the two-step code
  in an authenticator on your phone (Apple Passwords) or on a hardware
  key - not on the same 1Password item as the password, or one stolen vault
  gives up both halves.
* **The server's 1Password token** (`OP_SERVICE_ACCOUNT_TOKEN`, on Render
  only): it reads the *FAM Production* vault and nothing else. Make a new one
  and revoke the old if anybody who could read it leaves.
* **Admin accounts** (`FAM_ADMIN_ACCOUNTS`): `/admin` now refuses any admin
  without two-step sign-in. Turn it on in the app (Settings → Two-step
  sign-in) *before* the deploy that carries §301, or the dashboard will
  refuse you until you do.
* **`FAM_ADMIN_TOKEN`**: a header that opens every admin endpoint with no
  second factor, for scripts. Leave it unset unless a script needs it, and
  rotate it when anybody who knew it leaves.
* **`VOICE_REGISTRY_TOKEN`, `REMOTE_VOICE_TOKEN`, `VAPID_PRIVATE_KEY`**:
  generated secrets with no dashboard; rotate them on the same rule.

## Why this is also the legal answer

A second factor on every account that can reach listeners' data is widely
treated as part of "reasonable security" - the phrase privacy laws, cyber
insurers and security questionnaires tend to use. Whether it is enough for
FAM's obligations is a question for counsel, not this file. What this file
can be is the record that it was done, by whom and when - which is why the
last columns are worth filling in rather than just doing the work.
