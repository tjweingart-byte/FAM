# The iOS app against the web app

The web app (`static/index.html`) is the reference: the iOS app is meant to
match it, screen for screen and rule for rule. This is the list of what the
web does and where the iOS app stands on each, so nothing is left out
silently. **Update it in the same change that moves a row.** Rules cited in
backticks are `CLAUDE.md` IDs (`grep -n 'rule:ID' docs/claude/*.md`).

Status: **Done** matches the web · **Partial** works, differs from the web in
the way the note says · **Not yet** missing · **Server** nothing to build in
the app.

## Look

| Web | iOS | Note |
|---|---|---|
| Light theme colours (`:root` light) | Done | `Theme.swift` |
| Bricolage Grotesque / Geist / Geist Mono (`typefaces`) | Done | bundled, static instances |
| Drawn wordmark (daily/my/yourFAM, the chevron A) | Done | from the web's SVG paths |
| Dark mode (Settings) | Not yet | web default is light (§250) |
| Larger phone text never breaks a screen (`large-text`) | Partial | sizes scale; not yet checked at the largest sizes |
| Motion (§292: screen pushes, the burst on save/vibe) | Not yet | |

## Navigation

| Web | iOS | Note |
|---|---|---|
| Bottom bar: DailyFAM, myFAM, search (centre), Messages, YourFAM | Partial | DailyFAM, search, YourFAM; myFAM and Messages when built (`no-dead-controls`) |
| Mini player above the bar, X stops (`transport`) | Done | |
| Leaving the player never stops it | Done | |

## Accounts and the waitlist

| Web | iOS | Note |
|---|---|---|
| Sign up (email + password, terms box unticked, §228) | Done | |
| Log in | Done | |
| Phone number on sign-up | Not yet | deliberately off for iOS 1.0 (APP_STORE.md gap 12) |
| Sign in with Apple / Google (`op-identity`, 4.8) | Not yet | **required for App Store** - server built |
| Password reset (`/reset`, §297) | Not yet | |
| Identity step after sign-up (name, handle, photo) | Not yet | |
| Activation step (§411) | Not yet | `activation_needed` on `/api/auth/me` |
| Terms re-acceptance when the version changes | Not yet | `terms` on `/api/consent` |
| Waitlist: waiting screen plays today's samples | Done | |
| Intro / first-run screens | Not yet | |

## The loading screen

| Web | iOS | Note |
|---|---|---|
| Five steps checked off from `/api/progress`, each ≥2 s (first ≥0.5 s), audio held to the fifth (§148, §263) | Done | timings pinned to the web's by a test |
| A replay shows all five done and starts after 0.45 s | Done | |
| Rising chevrons, FAMiliarizing…, X cancels (§127) | Done | |
| "No API key" / "key rejected" notes from `/api/health` | Not yet | |
| Timeout handling when the voice is slow (`voice_wait`, §497) | Not yet | the web extends its quiet timer |

## searchFAM

| Web | iOS | Note |
|---|---|---|
| Rounded bar, copper go, Length bubble (1-5, back to 1.5) | Done | |
| Trending searches (five, `/api/searches/trending`) | Done | |
| AI consent asked before the first search (5.1.2(i)) | Done | FAMSafety sheet |
| Voice bubble (pick a voice, `op-voice-bank`) | Not yet | |
| Private search (charcoal, nothing kept, §282) | Not yet | `private=1` |
| Mic / voice search (§151), the editable "Search now" (§159, §165) | Not yet | `SFSpeechRecognizer` on iOS |
| "Hey FAM" wake word (§158) | Not yet | leave out of 1.0 (background mic) |
| Attach a file or photo | Not yet | `/api/attach` |
| Autocorrect at send (§247) | Server | |
| A name with two meanings asks which (300, §426) | Not yet | `ask_reading` |
| The bookshelf: A-Z catalogue of others' episodes (`/api/myfam/catalog`) | Not yet | |

## DailyFAM (the rails)

| Web | iOS | Note |
|---|---|---|
| Rails from `/api/myfam`, cards with picture, subject chip, title, hook | Done | |
| Heading with the time of day ("Made for you, Monday evening") | Not yet | |
| Pick up where you left off (§174) | Not yet | needs history |
| Category filter chips (§229/§500) | Not yet | `/api/categories` |
| A card's ⋯: save, Not interested (§499), add to queue | Not yet | |
| Save bookmark on the card | Not yet | |
| Friends' faces on cards (§393) | Not yet | |
| View more (16, Refresh, §363) | Not yet | |
| "ready" badge on cached tiles | Not yet | `cached` |
| Play/finish/skip events for the taste model (`mfy-field-variety`) | **Not yet** | **without these, Made for you never learns from iOS listening** |

## The player

| Web | iOS | Note |
|---|---|---|
| Picture (4:3, never missing), title, scrubber over what arrived, ±15, copper play | Done | |
| Lock screen: title, play/pause, ±15, scrub | Done | |
| GO DEEPER pill and sheet (`next-is-prediction`, §170) | Not yet | `/api/next` |
| Share / VIBE! / save on the right (§192) | Not yet | |
| Sources by the picture | Not yet | |
| Live captions sheet (`live-captions`) | Not yet | |
| ⋯ menu: Report (1.2), Add to queue | Not yet | **Report is required for App Store** |
| Speed (WSOLA, `speed-pitch`) | Not yet | |
| Queue (§190) | Not yet | |
| After an episode: four recommendations, first starts in 15 s (`post-episode-grid`) | Not yet | |
| The searcher shown if `searches_public` | Not yet | |
| Opus stream (stage 3) and signed replay links (§252) | Not yet | PCM today |
| Kept episodes replay by `X-FAM-Episode` (§173) | Not yet | |

## myFAM (mixes)

| Web | iOS | Note |
|---|---|---|
| The mixes screen, Create new mix, a mix's page, joint mixes, (+) to copy | Not yet | `/api/mixes` |

## Social

| Web | iOS | Note |
|---|---|---|
| Messages tab, threads, group chats (§213) | Not yet | `/api/messages` |
| Friends: follow, friends' vibes first, find friends | Not yet | |
| A friend's profile screen (`screen-person`) | Not yet | `/api/person` |
| VIBE! story editor, your story, replies (§213, §282) | Not yet | |
| Report and Block on comments, chats, profiles, stories (1.2) | Not yet | **required for App Store**; FAMSafety has the screens |
| Push notifications (a mix's "ready", §448) | Not yet | APNs not built on the server either |

## YourFAM (profile) and Settings

| Web | iOS | Note |
|---|---|---|
| Account details, privacy and safety, sign out, delete account (5.1.1(v)) | Done | |
| Profile hero, picture, interests line, Vibes/Mixes tiles (§341) | Not yet | |
| Saved for later, folders (`save-pointer`) | Not yet | |
| Listening history | Not yet | |
| Edit profile, interests, place, time zone | Not yet | |
| Blocked people | Done | FAMSafety |
| Feedback report | Not yet | |

## Sharing

| Web | iOS | Note |
|---|---|---|
| Share an episode (`/s/<id>` links, story-card PNGs) | Not yet | |
| Universal links open the app | Not yet | needs the associated-domains file and entitlement |

## App Store requirements still open in the app

Sign in with Apple, Report and Block on user content, the AI consent sheet
(done), account deletion (done), the privacy manifest (in place), no
"beta/demo" wording, an iPad that does not break. See `APP_STORE.md` Part B.
