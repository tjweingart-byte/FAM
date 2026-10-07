# Getting FAM into the App Store

`IOS_APP.md` says what the app *is*. This file is how it gets **through
Apple**: the paperwork in the order Apple checks it, what the server must
have before a reviewer touches it, the answers App Store Connect will ask for
(drafted from what the code actually stores), and how to talk to Apple so
each exchange is one round, not three.

Apple's rules move. Every guideline number here was checked in October 2026;
re-read the current [App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/)
the week of the first submission.

---

## Where things stand

| | Item | State |
|---|---|---|
| Paperwork | Legal entity, EIN, D-U-N-S, Apple Developer (organization) | **Owner** - Part A |
| Server | In-app account deletion, 5.1.1(v) | Built (`DELETE /api/account`) |
| Server | Sign in with Apple, 4.8 | Built (`oauth.py`); needs `APPLE_CLIENT_IDS` |
| Server | Bearer sessions, `/api/v1`, client versions, `426` | Built (`ACCOUNTS.md`, `STAGING.md`) |
| Server | Universal links (`/.well-known/apple-app-site-association`) | Built; needs `APPLE_TEAM_ID`, `IOS_BUNDLE_ID` |
| Server | Load test before review traffic | Built (`tools/load_test.py`) |
| **To build** | Report content and block a user, 1.2 | **Missing - hard rejection** |
| **To build** | Consent before data goes to a third-party AI, 5.1.2(i) | **Missing - hard rejection** |
| **To write** | Privacy policy, terms of use (EULA), support page | **Missing - required URLs** |
| To build | APNs push for the app (today's push is Web Push) | Only if 1.0 sends pushes |
| To build | The iOS app itself (`IOS_APP.md` stages 2-4) | Not started |

The two **Missing** rows are the likeliest rejections. Both are feature work,
not paperwork; Part B says what each needs.

---

## Part A - Paperwork, in the order Apple checks it

Elapsed time is mostly waiting. Start step 4 the day step 1 is done.

1. **A legal entity.** An LLC or a corporation (a Delaware C-corp if you are
   also raising money). Apple refuses DBAs, trade names and sole
   proprietorships for an organization account. *Days.*
2. **An EIN** from the IRS (irs.gov, online, free). Needed for tax forms in
   App Store Connect and for the bank. *Same day.*
3. **The company's public face**: a working website on a domain the company
   owns (`familiarize.net`), and an email address **on that domain** for the
   person enrolling (e.g. `founders@familiarize.net`). Apple rejects
   enrollments from `@gmail.com` addresses and sites that do not load.
4. **A D-U-N-S number.** Use Apple's own lookup
   (developer.apple.com/enroll/duns-lookup). It finds an existing number or
   requests one, free. *Up to 5 business days; up to 30 for a brand-new
   entity.* Dun & Bradstreet may phone to verify; answer.
5. **Enroll in the Apple Developer Program as an organization**
   (developer.apple.com/programs/enroll, $99 a year). Sign in with an Apple ID
   that uses the company-domain email and has two-factor authentication on. The
   person enrolling must be able to sign contracts for the company (founder or
   officer). Apple usually phones that person to confirm; it fails if they
   cannot say they have that authority. *A few days.*
6. **In App Store Connect: Agreements, Tax and Banking.** Accept the Free Apps
   agreement now. The Paid Apps agreement, W-9 and a bank account are needed
   only when something is sold (3.1.1); do them early anyway, since bank
   verification is slow.
7. **Reserve the name.** Create the app record as soon as enrollment clears;
   it holds the name. "FAM" alone is very likely taken, so have the
   alternates in Part C ready.
8. **Identifiers** (Certificates, Identifiers & Profiles). Register the
   bundle id, e.g. `net.familiarize.fam`; it can never change after the first
   upload. Turn on: *Sign in with Apple*, *Associated Domains*
   (`applinks:familiarize.net`, `webcredentials:familiarize.net`), and
   *Push Notifications* only if 1.0 sends them. Background audio is set
   in Xcode, not here.
9. **Server settings** once 7 and 8 exist (production and staging both, each
   its own values - `render.yaml`):

   ```
   APPLE_TEAM_ID=<ten characters, Membership page>
   IOS_BUNDLE_ID=net.familiarize.fam
   APPLE_CLIENT_IDS=net.familiarize.fam      # Sign in with Apple audience
   APP_STORE_URL=https://apps.apple.com/app/id<number>   # only once live
   ```

   Check `https://familiarize.net/.well-known/apple-app-site-association`
   returns JSON with the right app id. Apple's CDN caches it for a long time,
   so set it before the first TestFlight build that uses universal links.

### The one rule that prevents most enrollment delays

**Every record says the company's name and address exactly the same way.**
Apple compares the enrollment to D-U-N-S character for character. Fill this
in once and copy from it every time:

| Field | Value (fill in) |
|---|---|
| Legal entity name, exactly as on formation documents | |
| Entity type and state | |
| Registered street address (no PO box) | |
| Company phone number | |
| D-U-N-S number | |
| EIN | |
| Website | https://familiarize.net |
| Enrolling person, title, company email | |
| Apple Team ID (after enrollment) | |

If D-U-N-S has the name or address different from the formation documents,
fix it **at Dun & Bradstreet first** (their update process takes days) and
only then enroll. Enrolling with a mismatch stalls without a clear error.

---

## Part B - What the server must have before review

### 1.2 User-generated content - **build before TestFlight's external beta**

FAM has user-generated content in at least six places: comments (§203),
messages, vibe captions, display names and handles, avatars and cover photos,
and **Explore, which replays episodes other listeners searched**. Apple
requires *all* of:

* a way to **report** offensive content: from a comment, a message, a profile,
  and an episode on the player and Explore;
* a way to **block** an abusive user, after which their comments, messages,
  vibes and profile no longer reach the blocker;
* **terms of use** the listener agrees to that say objectionable content and
  abusive users are not tolerated;
* a **filter** for objectionable material;
* **published contact information**, and acting on reports (Apple's wording is
  within 24 hours: remove the content and eject the user).

What exists: `/api/feedback` keeps bug reports with episode details, and
`/admin` can resolve them. That is an inbox, not a report button for content,
and there is no block. Note the filter conflicts with a settled rule: content
is filtered for **slurs only** (`content_filter.py`, `slurs-only`). Whether
that is enough for Apple is the owner's decision, not a code change; expect
the reviewer to test it with an obviously offensive comment.

### 5.1.2(i) Third-party AI - **build before the first build is uploaded**

Since November 2025 an app must **say in the app** where personal data goes
to a third-party AI and **get an explicit yes before it goes**. A typed
question, an attached document or image, and the listener's taste and place
reach Anthropic's API to write the episode. Needed:

* a consent screen before the first search or attachment, naming Anthropic,
  saying what is sent (what you typed or attached) and why (to write your
  episode), with a clear **Allow** - and a way to say no that still leaves
  myFAM, DailyFAM and Explore (their episodes are written before the tap and
  carry nothing of this listener);
* the answer stored server-side with the account, and asked again if what is
  sent changes;
* the same named in the privacy policy.

This screen costs no latency on search: it is answered once, before the first
search, never in front of an episode.

### Everything else

| Guideline | What it needs | State |
|---|---|---|
| 2.1 Completeness | A demo account and a **warm, keyed, funded backend** for the whole review (`IOS_APP.md`) | Part C, "Review notes" |
| 2.1 | The demo account is `active` (waitlist) and `unlimited` (`/api/admin/waitlist/grant`, `/api/admin/plan`), so the reviewer never sees a waitlist or quota screen | Do before submitting |
| 2.3 Accurate metadata | Screenshots of the real app, no prices or features it lacks | Part C |
| 3.1.1 Payments | Nothing in the app may sell or point to a purchase until in-app purchase exists. Quota refusals (`refusal-wording`) must not say "upgrade on the web" in the iOS app | Check refusal text when the app exists |
| 4.2 Minimum functionality | Native client, not a web view | `IOS_APP.md` |
| 4.8 Login services | Sign in with Apple beside Google | Built |
| 5.1.1 Privacy | Privacy policy link in the app and the listing; sign-up asks only what it needs; account deletion | Policy to write; deletion built |
| 5.1.1(v) | Account deletion inside the app | Built |
| 5.2 Intellectual property | FAM speaks about news it read. Expect "do you have rights to third-party content?"; the answer is that episodes are original writing and name their sources, never reproduce articles | Have the answer ready |
| Export compliance | HTTPS through the system only: `ITSAppUsesNonExemptEncryption = NO` in Info.plist, so the question is not asked per build | Set in the Xcode project |

---

## Part C - The answers App Store Connect asks for

### Listing (drafts)

* **Name** (30 characters): `FAM: Audio Stories`. Alternates:
  `FAM - Keep Up in Audio`, `FAM Audio`.
* **Subtitle** (30): `Keep up, in short audio`
* **Category**: primary **News**, secondary **Entertainment**. A News app
  needs a licence in mainland China; leave the China storefront off.
* **Keywords** (100, commas, no spaces):
  `news,audio,podcast,briefing,sports,stories,daily,explainer,catchup,trending,listen,current events`
* **Promotional text** (170): `Ask about anything and hear the story in a
  couple of minutes. Follow what you care about and FAM has it ready every
  morning.`
* **Description**: what it does in the first two lines ("Type a question,
  and FAM tells you the story in audio, researched and current"), then the
  three surfaces, then accounts and privacy. No prices, no "best", no other
  apps' names, no claims of live scores until a provider returns them
  (`live-facts`).
* **Support URL**, **Marketing URL**, **Privacy policy URL**: pages on
  `familiarize.net` - to be written.

### Age rating

Apple's questionnaire now has 4+, 9+, 13+, 16+ and 18+, and counts AI
features, user-generated content and messaging. Answer honestly:
user-generated content **yes**, messaging **yes**, unrestricted web content
**no** (FAM does not show web pages), profanity **infrequent** (swearing is
kept, `slurs-only`), mature themes **infrequent** (news). Expect **16+ or
18+**. Answering low and being corrected in review costs a round.

### App privacy ("nutrition labels"), from what the code stores

Tracking: **No.** FAM shows no ads, shares nothing with data brokers and
combines nothing with other companies' data.

| Apple's type | What FAM stores | Linked to the user | Purposes |
|---|---|---|---|
| Contact Info: Name | display name | Yes | App functionality |
| Contact Info: Email address | account email | Yes | App functionality |
| Contact Info: Phone number | account phone | Yes | App functionality |
| User Content: Photos | avatar, cover photo, attached images | Yes | App functionality |
| User Content: Other | comments, messages, vibe captions, attached documents | Yes | App functionality |
| User Content: Customer support | feedback reports | Yes | App functionality |
| Search history | `events.text` for `search` | Yes | App functionality, product personalization |
| Identifiers: User ID | the server-minted listener id | Yes | App functionality |
| Usage data: Product interaction | plays, finishes, skips, picks, shares, saves, vibes | Yes | Product personalization, analytics |
| Location: Coarse location | the typed town or region (never sensed, `preferences.clean_place`) | Yes | App functionality, product personalization |
| Diagnostics: Other | build, screen and device string on a feedback report | Yes | App functionality |

Revisit this table whenever a store is added (`DATABASE.md`). Purchases is
added the day in-app purchase is.

### Info.plist purpose strings (drafts)

* `NSMicrophoneUsageDescription`: "FAM listens when you tap the mic so you can
  ask a question out loud."
* `NSSpeechRecognitionUsageDescription`: "FAM turns what you say into the
  question you are asking."
* Photos: use `PHPickerViewController`, which needs no permission prompt.
* `UIBackgroundModes`: `audio`.

### Review notes (template)

> FAM writes a short audio story in answer to a question. Episodes are
> written by AI (Anthropic's Claude) from current sources and spoken by our
> own voice model; sources are shown on the player.
>
> Demo account: `<email>` / `<password>`. It is a full member, so it skips the
> pre-launch waitlist that new sign-ups see.
>
> To try it: type a question on Search (e.g. "how does a heat pump work") and
> audio starts after a short loading screen. myFAM and DailyFAM are written
> ahead of time and start immediately. Explore shows episodes other listeners
> searched.
>
> User content: comments, messages and profiles can be reported from their
> ⋯ menu and users can be blocked from their profile. Reports reach us at
> `<address>` and are acted on within 24 hours.
>
> Before the first search the app asks permission to send the question to
> Anthropic to write the episode.
>
> Account deletion: `<where Delete account is in the iOS app>`.

Fill the bracketed parts, keep every sentence true on the day it is sent, and
keep the backend warm from submission until approval.

---

## Part D - Talking to Apple

**One person owns the Apple relationship**: the Account Holder from Part A.
Apple's team roles let others upload builds, but enrollment, agreements and
appeals go through that person.

### The channels

| For | Where |
|---|---|
| Enrollment, D-U-N-S, account, agreements | developer.apple.com/contact - choose the topic; "Request a call" is usually fastest |
| D-U-N-S records themselves | Dun & Bradstreet, via the link in Apple's lookup |
| A rejection or a reviewer's question | **App Review** in App Store Connect, on the submission (replying there keeps the thread with the build) |
| Disagreeing with a rejection | The appeal form, developer.apple.com/contact/app-store |
| A launch or fix that cannot wait | Expedited review request, same contact page; use rarely |
| Technical questions | Developer Forums, or a code-level support request (two a year come with membership) |

### How to write to them

* **Quote the guideline number** they cited and answer that guideline only.
* **Say what changed, in the build, with a screenshot or screen recording.**
  "We added a Report option to the ⋯ menu on comments (see attached)" is
  resolved in one round; "we believe the app complies" is not.
* **Ask, don't argue.** If the rejection looks wrong, ask which screen
  triggered it before appealing. Appeals are for genuine misreadings.
* **Reply within a day.** A submission waiting on you loses its place.
* Keep every exchange in `PROBLEMS.md` under the build's version, so the
  next submission starts from what Apple already asked.

### Drafts

**D-U-N-S name mismatch (enrollment support):**

> Our enrollment for `<legal name>` (D-U-N-S `<number>`) is not completing.
> Our formation documents give the legal name as `<exact name>` at
> `<address>`. We updated our D-U-N-S record on `<date>` to match. Could you
> re-check the enrollment, or tell us which field still differs?

**Answering a 1.2 rejection:**

> Thank you. Under 1.2 we have added in build `<n>`: a Report option on every
> comment, message, profile and episode; Block on every profile, which hides
> that person's comments, messages and vibes; terms of use accepted at
> sign-up; and our contact `<address>`, where reports are acted on within 24
> hours. A screen recording is attached.

**Expedited review (only for a real break):**

> Version `<x>` fixes a crash that stops playback for every listener on iOS
> `<y>` (`<what happens>`). The fix is limited to `<files/area>`. We'd be
> grateful for an expedited review.

---

## The order, end to end

1. Part A steps 1-6 (paperwork, in parallel with the work below).
2. Hear an episode in the production voice (`IOS_APP.md` stage 0).
3. The audio spike, then the app (`IOS_APP.md` stages 2-4).
4. 1.2 report and block, 5.1.2(i) consent, privacy policy, terms, support
   page (Part B).
5. Run `tools/load_test.py` against staging at the tester count planned for
   the beta; fix what it finds.
6. TestFlight internal (no review), then external (reviewed - Part B must be
   in).
7. Part A step 9 settings, demo account, warm backend, review notes, submit.
