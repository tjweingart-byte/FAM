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
| Sign in with Apple / Google (`op-identity`, 4.8) | Done (iOS only) | the web has neither on purpose. Apple: nonce hashed to Apple, raw to the server, the one-time code sent so deletion revokes. Google: GoogleSignIn, drawn only once `GOOGLE_IOS_CLIENT_ID` is set in `project.yml`. Both wait for the terms box on sign-up |
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

Checked against a specification written from `static/index.html` (the web's
`screen-myfam`), item by item.

| Web | iOS | Note |
|---|---|---|
| Header: the centred dailyFAM wordmark, nothing else (the bookshelf moved to search) | Done | |
| Tap DailyFAM again: scroll to top; arriving from another tab refetches | Done | |
| Friends' stories row: your story, friends with fresh/seen rings, Invite | Done | seen state per device |
| Story viewer: 6 s a story, bars, tap back/on, hold, swipe between friends, play the episode, reply, heart, Report | Partial | the note composer, reshare, "Delete vibe" and a friend's profile wait for those screens |
| Pick up where you left off: resume (time left, bar) and thread cards, X dismisses for good | Done | resumes seek once the audio has arrived. **Server bug found:** progress keeps `minutes` as an integer, so a 1.5-minute resume is asked as 1 minute (a different episode) - on the web too |
| Five rails in the server's order, "Made for you, {Day} {part}" / "Start here", the others' headings | Done | |
| Each rail's empty sentence from the server; friends rail silent with nobody followed | Done | |
| Card: picture, subject chip, ready badge, save bookmark, ⋯, friends pill and "N friends listened", title size steps (>48, >72), live line with its age, hook with each rail's fallback | Done, differs on purpose | the owner's call for iOS: the picture at its full 4:3 (the web crops rail pictures to a 96 pt strip), the whole hook (the web clamps it to two lines), and the friends' faces on the subject chip's line, centred on it (the web lifts them above the chip) |
| Card ⋯: Share, Add to Queue, Go to Queue, Not interested | Done | |
| Card ⋯: Add to myFAM Playlist | Not yet | waits for myFAM |
| VIEW MORE at a rail's end → View more: 16 at a time, Refresh, the reason line, wrap notes, caught up, Trending by continent | Done | VIEW MORE always shows (the web shows an arrow until the rail is scrolled to its end) |
| Find new friends pill after the friends rail | Not yet | waits for the Friends screen |
| Guest sample note | n/a | the iOS app requires an account |
| Offline: the bar, dimmed cards, the remembered page | Not yet | |
| Loading skeleton; the error sentence | Done | |
| Library (bookshelf on search): A-Z with sticky letters, day labels, search as you type | Done | "Search FAM for it" on no results not yet |
| Arrival animation of rails | Not yet | |
| Pull to refresh | iOS only | the web has none; kept as iOS convention |

## The player

| Web | iOS | Note |
|---|---|---|
| Full-screen player: Home mark, picture (4:3, faded), title with the explicit E and the writer's title swap, scrubber (requested length until complete, arrived drawn, clamped 2 s short), ±15, copper play | Done | the picture's blur-up and the title's wipe are simple fades |
| Lock screen: title, play/pause, ±15, scrub | Done | |
| GO DEEPER pill and sheet (`next-is-prediction`, §170) | Done | thread from `/api/next`, typed = own, 1-5 lengths |
| Share / VIBE! / save on the right (§192) | Partial | save and share done (iOS share sheet for the web's targets); VIBE! posts with a caption - the web's full story editor and "send inside FAM" not yet; the burst animation not yet |
| Sources by the title, and the Sources sheet | Done | favicons over initials |
| Live captions sheet (`live-captions`) | Done, differs on purpose | the owner's call for iOS: the whole script, scrollable, the current sentence highlighted and followed (the web shows the current sentence alone); tap a sentence to jump there |
| ⋯ menu: Share, VIBE!, Save, Captions, Add to/Go to Queue, Report (1.2) | Partial | "Add to myFAM Playlist" and "Remove from this playlist" wait for myFAM |
| Speed 0.5-2x, keep the voice's pitch (`speed-pitch`) | Done | time-pitch unit; length for searches rebuilds |
| Queue (§190), next-in-queue button, badge | Done | |
| After an episode: four recommendations, first starts in 15 s (`post-episode-grid`) | Done | `/api/nextup`; album tile n/a |
| The searcher shown if `searches_public` | Done | in the ⋯ menu header |
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
