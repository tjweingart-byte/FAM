# 8. Every other outside service

Each one is a key on Render `fam` → Environment (or in `FAM_SECRETS`).
**None of them is on staging**: staging removes every paid key.

For each: what it does for FAM, what a listener notices when it fails, and
where to look. Costs and rate limits are in `docs/FINANCIAL.md`; when to
upgrade each one is in `docs/SCALING_TIMELINE.md`. `/admin` → **Outside
services, requests per day** shows each one's daily use against its limit,
with licence problems in red.

**A general rule:** FAM is built so that losing any one of these makes
episodes worse, never absent. Every failure is recorded and shown on
`/admin` and `/api/health`. Find the service there before assuming the
worst.

## Writing and research

| Service | Variables | Does | When it fails |
|---|---|---|---|
| **Anthropic** (Claude) | `ANTHROPIC_API_KEY`, `MODEL` | Brief, writer, titles, categories, agents | Every episode becomes the built-in sample script. See [03_CLAUDE.md](03_CLAUDE.md#the-anthropic-api-account-claude-inside-fam). |
| **Perplexity** | `PERPLEXITY_API_KEY` | First rung of research | Research drops to Parallel, then GDELT. Episodes thinner on current events. |
| **Parallel** | `PARALLEL_API_KEY` | Second look and backup research | Drops to GDELT. |
| **GDELT** | `GDELT=1` (keyless) | World news story pool; last research rung. Read from 15-minute export files, never its API. | Story pool goes stale. `/api/health` → `gdelt.failures_in_a_row`. Check with `python tools/gdelt_probe.py`. |
| **Wikimedia Enterprise** | `WIKIMEDIA_ENTERPRISE_USERNAME`, `..._PASSWORD` | Reference facts on named people and organisations; pronunciation | Falls back to Wikipedia's free summary. |

## Live facts and the rails

| Service | Variables | Does | When it fails |
|---|---|---|---|
| **GNews** | `GNEWS_KEY`, `GNEWS_PLAN=essential`, `GNEWS_DAILY_REQUESTS=950`, `GNEWS_MAX_ARTICLES=25` | The **only** source of the Trending rail (editions at 05:00 and 17:00 ET) | Trending is empty and says so. No fallback, by design. |
| **BALLDONTLIE** (ALL-ACCESS) | `BALLDONTLIE_API_KEY`, `BALLDONTLIE_TIER` | Every sports score, schedule and player report | Sports episodes say they have no score. FAM never invents one. |
| **Tiingo** | `TIINGO_API_KEY`, `TIINGO_PLAN` | Every market figure, swept every 2 h | Market episodes carry no figures. |
| **Polymarket** | `LIVE_ELECTIONS_PROVIDER=polymarket`, `STORIES_POLYMARKET=1` (keyless) | Forecasts beside outcome-dependent questions; story signals | The forecast line is missing. |
| **NWS** | none | US weather and warnings | Falls back to Open-Meteo. |
| **Open-Meteo** (API Standard) | `OPEN_METEO_API_KEY` | Weather outside the US, the NWS fallback, place names | Weather missing outside the US. |

`/api/health` → `licences.commercial_ready` must be `true` before FAM
charges anyone. It turns red when a plan is set to a non-commercial tier
(GNews free, Tiingo short of redistribution, Open-Meteo keyless).

## Pictures

| Service | Variables | Does | When it fails |
|---|---|---|---|
| **Google AI Studio / Gemini** (Imagen) | `GEMINI_API_KEY`, `THUMBNAILS` | Paints a picture for each new branch of the category tree, inside a daily ceiling. Pictures await a decision at `/admin/thumbnails`. | New categories show their old icon. Nothing else. |

## Accounts, email and notifications

| Service | Variables | Does | When it fails |
|---|---|---|---|
| **SMTP provider** (**TODO(owner):** which one: Resend, Postmark, SES, Gmail?) | `SMTP_HOST`, `SMTP_PORT=587`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `MAIL_FROM` | Password-reset codes; every alert email (voice down, incidents, moderation reports) | "Forgot password?" disappears from sign-in, and **alerts stop arriving**, so you would not hear about other failures. Check this first if it has been oddly quiet. |
| **Sign in with Google** | `GOOGLE_CLIENT_IDS` | Google sign-in | That button fails; email and phone sign-in still work. |
| **Apple** (Developer account) | `APPLE_CLIENT_IDS`, `APPLE_TEAM_ID`, `IOS_BUNDLE_ID`, `APPLE_KEY_ID`, `APPLE_PRIVATE_KEY` | Sign in with Apple, universal links, revoking a deleted account's Apple sign-in | Apple sign-in fails. App Review requirements: `APP_STORE.md`. |
| **Web push** | `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` | "Your mix is ready" notifications | No notifications. **Never rotate the VAPID keys**: every listener's push subscription is tied to them. |
| **Viral Loops** | `VIRAL_LOOPS_CAMPAIGN_ID`, `VIRAL_LOOPS_API_TOKEN` | The waitlist's referral campaign. FAM sends sign-ups through an outbox, so none are lost. | `/api/health` → `waitlist.outbox_pending` grows. It drains when the service is back. |

## Not used any more

Do not re-add these if a vendor or an old document suggests them:
**API-Sports** and **Finnhub** (replaced by BALLDONTLIE and Tiingo),
**Piper** (deleted voice), **Exa**, the model's own web search (deleted on
purpose, §135), any hosted text-to-speech voice.

## When a vendor emails about a limit, a price or a renewal

1. Find it in `docs/FINANCIAL.md` (limits) and `docs/SCALING_TIMELINE.md`
   (when to upgrade).
2. Check `/admin` → Outside services for how close FAM actually is.
3. Forward it to the owner with those two numbers. Buying or changing a
   plan is the owner's decision ([01_ACCESS.md](01_ACCESS.md#owner-only-decisions)).
4. If a limit is hit *now* and listeners are affected, the safe response is
   usually to wait for the daily reset. FAM paces itself against each
   limit and does not re-ask a refused endpoint that day.
