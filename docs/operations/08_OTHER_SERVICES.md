# 8. Every other outside service

FAM uses about a dozen other services for research, news, sports, markets,
weather, pictures, email and sign-in. Each one has a key stored on Render
**fam** → **Environment**. **None are on staging.**

**FAM is built so that losing any one of these makes episodes worse, never
absent.** It records every failure, so the quickest way to find a failing
service is the admin page.

---

## Task 1. Find which service is failing

1. Open `<production address>/admin` and sign in.
2. Scroll to **Outside services, requests per day**.

   > **Check:** every service has a recent number, and none is shown in red
   > or at its daily limit.
   >
   > **If one is red, at its limit, or has stopped counting:** that is the
   > failing service. Find it in the table below.

3. On the same page, look at the **errors** list.

   > **Check:** no error repeating many times. A repeated error names the
   > service it came from.

---

## The services

| Service | Setting(s) on Render | What it does for FAM | What listeners notice if it fails |
|---|---|---|---|
| **Anthropic** | `ANTHROPIC_API_KEY` | Writes every episode | Every episode is the same sample script ([03_CLAUDE.md](03_CLAUDE.md#task-3-the-anthropic-api-key-claude-inside-fam)) |
| **Perplexity** | `PERPLEXITY_API_KEY` | First choice for research | Episodes less current; FAM falls back to Parallel |
| **Parallel** | `PARALLEL_API_KEY` | Second choice for research | FAM falls back to GDELT |
| **GDELT** | `GDELT` = `1` (no key) | World news; last research fallback | Fewer live stories |
| **Wikimedia** | `WIKIMEDIA_ENTERPRISE_USERNAME`, `..._PASSWORD` | Facts about named people and organisations | Falls back to free Wikipedia |
| **GNews** | `GNEWS_KEY`, `GNEWS_PLAN` | The **Trending** row, rebuilt 05:00 and 17:00 ET | Trending is empty (no fallback, on purpose) |
| **BALLDONTLIE** | `BALLDONTLIE_API_KEY`, `BALLDONTLIE_TIER` | Every sports score and schedule | Sports episodes say they have no score (FAM never makes one up) |
| **Tiingo** | `TIINGO_API_KEY`, `TIINGO_PLAN` | Every market price | Market episodes have no figures |
| **Polymarket** | `LIVE_ELECTIONS_PROVIDER`, `STORIES_POLYMARKET` (no key) | Election and event forecasts | The forecast line is missing |
| **NWS** | none | US weather | Falls back to Open-Meteo |
| **Open-Meteo** | `OPEN_METEO_API_KEY` | Weather outside the US | No weather outside the US |
| **Gemini** (Google) | `GEMINI_API_KEY` | Paints category pictures | New categories show a plain icon |
| **Email (SMTP)** **TODO(owner):** which provider | `SMTP_HOST`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `MAIL_FROM` | Password resets, **and every alert email** | "Forgot password?" disappears, **and you stop getting alerts** |
| **Google sign-in** | `GOOGLE_CLIENT_IDS` | Sign in with Google | That button fails; email sign-in still works |
| **Apple** | `APPLE_…` (five settings) | Sign in with Apple; the iOS app's links | Apple sign-in fails |
| **Web push** | `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` | "Your mix is ready" notifications | No notifications. **Never change these keys**: every listener's notifications are tied to them. |
| **Viral Loops** | `VIRAL_LOOPS_CAMPAIGN_ID`, `VIRAL_LOOPS_API_TOKEN` | The waitlist's referral programme | Nothing lost: sign-ups queue up and are sent when it is back |

**If it has been suspiciously quiet** (no alerts for a long time), check
email first. If email is broken, no alert can reach you.

---

## Task 2. A service is failing: what to check at the vendor

1. Sign in to that vendor's website (**TODO(owner):** add each vendor's
   login page and who holds the login).
2. Look at, in this order:
   - **Is the account active and paid?** Billing / subscription page.
   - **Is the daily or monthly limit used up?** Usage page.
   - **Is the key still active?** API keys page.
   - **Is the vendor down?** Their status page.

   > **If the limit is used up:** FAM slows itself down near each limit and
   > recovers when it resets (usually at midnight UTC). Usually the right
   > move is to wait, and tell the owner if it happens again.
   >
   > **If payment failed or the plan lapsed:** tell the owner. Paying is
   > their decision.
   >
   > **If the key was revoked:** do [Task 3](#task-3-replace-a-services-key).

---

## Task 3. Replace a service's key

1. On the vendor's website, create a new key. Copy it.
2. Render → **fam** → **Environment** → **Edit** → find the setting from
   the table above → paste the new key as its value → **Save, rebuild, and
   deploy**.

   > **Check:** Events shows `Deploy live`.

3. Open `/admin` → Outside services.

   > **Check:** within an hour, that service's count starts rising again,
   > with no new errors from it.

4. On the vendor's website, delete or disable the old key.
5. Tell the owner which key you replaced, and why.

---

## Task 4. A vendor emails about a limit, price or renewal

1. Open `/admin` → Outside services, and note how close FAM is to that
   service's limit.
2. Forward the email to the owner with that number. **Buying or changing a
   plan is the owner's decision.** (`docs/SCALING_TIMELINE.md` lists when each
   upgrade is due.)

---

## Not used any more

If an old document or a vendor suggests these, **do not add them back**:
API-Sports, Finnhub, Piper, Exa, any hosted text-to-speech voice, and the AI
model's own web search. Each was removed on purpose.
