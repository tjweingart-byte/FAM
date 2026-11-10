# 8. Every other outside service

FAM uses about a dozen other services for research, news, sports, markets,
weather, pictures, email and sign-in. Each one has a key stored on Render
**fam** → **Environment**. **None of them are on staging.**

**FAM is built so that losing any one of these makes episodes worse, never
absent.** It records every failure, so the quickest way to find a failing
service is the admin page.

---

## Task 1. Find which service is failing

1. Open `<production address>/admin` and sign in.
2. In the menu down the left side (in a narrow window: the three-line
   button, top left) → under **Running costs**, **Outside service requests**.

   > **Check:** each service shows today's number of requests, and none is
   > marked in red or shown at its daily limit.
   >
   > **If not:** a service in red, at its limit, or stuck at zero when it is
   > normally busy is the failing one. Find it in the table below.

3. Menu → **Pipeline errors**.

   > **Check:** no error repeating many times.
   >
   > **If not:** a repeating error usually names the service it came from.
   > Find that service in the table below.

---

## The services

| Service | Setting(s) on Render | What it does for FAM | What listeners notice if it fails |
|---|---|---|---|
| **Anthropic** | `ANTHROPIC_API_KEY` | Writes every episode | New episodes fail with an error. (If the setting is **empty**, every episode is the sample script instead.) See [03 → Task 3](03_CLAUDE.md#task-3-the-anthropic-api-key-claude-inside-fam). |
| **Perplexity** | `PERPLEXITY_API_KEY` | First choice for research | Episodes less current; FAM falls back to Parallel |
| **Parallel** | `PARALLEL_API_KEY` | Second choice for research | FAM falls back to GDELT |
| **GDELT** | `GDELT` = `1` (no key) | World news; the last research fallback | Fewer live stories |
| **Wikimedia** | `WIKIMEDIA_ENTERPRISE_USERNAME`, `…_PASSWORD` | Facts about named people and organisations | Falls back to free Wikipedia |
| **GNews** | `GNEWS_KEY`, `GNEWS_PLAN` | The **Trending** row, rebuilt at 05:00 and 17:00 Eastern | Trending is empty (no fallback, on purpose) |
| **BALLDONTLIE** | `BALLDONTLIE_API_KEY`, `BALLDONTLIE_TIER` | Every sports score and schedule | Sports episodes say they have no score. FAM never makes one up. |
| **Tiingo** | `TIINGO_API_KEY`, `TIINGO_PLAN` | Every market price | Market episodes have no figures |
| **Polymarket** | `LIVE_ELECTIONS_PROVIDER`, `STORIES_POLYMARKET` (no key) | Election and event forecasts | The forecast line is missing |
| **NWS** | none | US weather | Falls back to Open-Meteo |
| **Open-Meteo** | `OPEN_METEO_API_KEY` | Weather outside the US | No weather outside the US |
| **Gemini** (Google) | `GEMINI_API_KEY` | Paints category pictures | New categories show a plain icon |
| **Email** (**TODO(owner):** which provider) | `SMTP_HOST`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `MAIL_FROM` | Password resets, **and every alert email** | "Forgot password?" disappears, **and you stop getting alerts** |
| **Google sign-in** | `GOOGLE_CLIENT_IDS` | Sign in with Google | That button fails; email sign-in still works |
| **Apple** | `APPLE_CLIENT_IDS`, `APPLE_TEAM_ID`, `APPLE_KEY_ID`, `APPLE_PRIVATE_KEY`, `IOS_BUNDLE_ID` | Sign in with Apple; the iOS app's links | Apple sign-in fails |
| **Web push** | `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` | "Your mix is ready" notifications | No notifications |
| **Viral Loops** | `VIRAL_LOOPS_CAMPAIGN_ID`, `VIRAL_LOOPS_API_TOKEN` | The waitlist's referral programme | Nothing is lost: sign-ups queue up and are sent when it is back |

**If it has been suspiciously quiet** (no alerts for a long time), check
email first ([01 → Part D, step 2](01_ACCESS.md#part-d-alert-emails)). If
email is broken, no alert can reach you.

---

## Task 2. A service is failing: what to check at the vendor

**TODO(owner):** for each vendor, the login page and who holds the login.
Until that is filled in, send the problem to the owner instead.

1. Sign in to that vendor's website.
2. Look at these, in this order:
   - **Is the account active and paid?** (billing or subscription page)
   - **Is the daily or monthly limit used up?** (usage page)
   - **Is the key still active?** (API keys page)
   - **Is the vendor down?** (their status page)

   > **If the limit is used up:** FAM slows itself near each limit and
   > recovers when it resets (usually midnight UTC). Meanwhile listeners get
   > thinner episodes on that subject. Usually the right move is to wait, and
   > tell the owner if it keeps happening.
   >
   > **If payment failed or the plan lapsed:** tell the owner. Paying is
   > their decision.
   >
   > **If the key was revoked:** do [Task 3](#task-3-replace-a-services-key).

---

## Task 3. Replace a service's key

**Only for the services in the table above**, and **not** for web push. For
these, tell the owner within the hour instead of replacing them yourself:
`VAPID_PRIVATE_KEY` (every listener's notifications depend on it),
`RUNPOD_API_KEY`, `DATABASE_URL`, `APPLE_PRIVATE_KEY`, `SMTP_PASSWORD`,
`VOICE_REGISTRY_TOKEN`.

Do this with nobody watching your screen.

1. On the vendor's website, create a new key. Name it with today's date if
   it lets you. Copy it.
2. Render → **fam** → **Environment** → **Edit**. Find the setting from the
   table above. Delete the whole old value, paste the new key, and click
   **Save, rebuild, and deploy**.

   > **Check:** **Events** shows `Deploy live` within 15 minutes.

3. Check the service is being used again. When depends on the service:

   | Service | How to check |
   |---|---|
   | GNews | Within about 30 minutes of the deploy, the Trending row fills (straight away if the key had been missing) |
   | BALLDONTLIE | Search a current sports question in the app |
   | Tiingo | Search a question about a stock price |
   | Open-Meteo | Search the weather in a city outside the US |
   | Others | `/admin` → **Outside service requests**: its count rises within an hour |

   > **Check:** that works, and `/admin` → **Pipeline errors** shows no new
   > errors from it.

4. On the vendor's website, delete or disable the **old** key.
5. Tell the owner which key you replaced, and why.

---

## Task 4. A vendor emails about a limit, price or renewal

1. Open `/admin` → **Outside service requests**, and note how close FAM is to
   that service's limit.
2. Forward the email to the owner with that number. **Buying or changing a
   plan is the owner's decision.** (`docs/SCALING_TIMELINE.md` lists when
   each upgrade is due.)

---

## Not used any more

If an old document or a vendor suggests these, **do not add them back**:
API-Sports, Finnhub, Piper, Exa, any hosted text-to-speech voice, and the AI
model's own web search. Each was removed on purpose.
