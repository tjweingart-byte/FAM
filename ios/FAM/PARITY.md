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
| Bottom bar: DailyFAM, myFAM, search (centre), Messages, YourFAM | Partial | DailyFAM, myFAM, search, YourFAM in five equal slots, Messages' left empty until it is built (`no-dead-controls`), so the search is centred as on the web; each icon has the web's tone fill (.16, .45 when on) |
| No tab bar or mini player on a rail's View more (`screen-section`) | Done | |
| Mini player above the bar, X stops (`transport`) | Done | |
| Leaving the player never stops it | Done | |

## Accounts and the waitlist

| Web | iOS | Note |
|---|---|---|
| Sign up (email + password, terms box unticked, §228) | Done | |
| Log in | Done | |
| Phone number on sign-up | Not yet | deliberately off for iOS 1.0 (APP_STORE.md gap 12) |
| Sign in with Apple / Google (`op-identity`, 4.8) | Done (iOS only) | the web has neither on purpose. Apple: nonce hashed to Apple, raw to the server, the one-time code sent so deletion revokes. Google: GoogleSignIn, client `FAM iOS` in Google Cloud project `fam-app-514301`. Both confirmed on a phone, linked to one account (2026-11-10). Both wait for the terms box on sign-up |
| Linking Apple / Google to an account (YourFAM → Sign-in methods) | Done (iOS only) | the server never joins accounts by email, so a provider used while signed out makes its own account; linking is a provider sign-in while signed in. Unlink when another way in remains |
| Delete the account from the waiting screen (5.1.1(v)) | Done | `/api/account` is open past the waitlist |
| Password reset (`/reset`, §297) | Not yet | |
| Identity step after sign-up (name, handle, photo) | Not yet | |
| Activation step (§411) | Not yet | `activation_needed` on `/api/auth/me` |
| Terms re-acceptance when the version changes | Not yet | `terms` on `/api/consent` |
| Waitlist: waiting screen plays today's samples | Done | |
| Intro / first-run screens | Not yet | |

## The loading screen

| Web | iOS | Note |
|---|---|---|
| Five steps checked off from `/api/progress`, each ≥2 s (first ≥0.5 s), audio held to the fifth (§148, §263) | Done | timings pinned to the web's by a test. Search, Go Deeper, Pick up and Search DailyFAM rows use them, as on the web |
| A DailyFAM tile's tuning dial (§509, `browse-loaders`): DAILYFAM · {rail}, the rail's neighbouring tags with the tile's own, the needle's sweep, overshoot and settle, the label locking copper, "Tuning in to {facet}", the title, "Getting your episode ready"; no steps, audio never held | Done | rails and View more; `LoadingModel.Dial` beside the glide, released like it (0.45 s minimum). The facet's full name comes from `/api/preferences`' `tag_labels`, else the category tree, else the tag capitalised (`dialWord`) |
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
| The bookshelf (top left, the web's own bookshelf icon): Search DailyFAM, the A-Z catalogue of others' episodes (`/api/myfam/catalog`) | Done | see the DailyFAM table's Library row |

## DailyFAM (the rails)

Checked against a specification written from `static/index.html` (the web's
`screen-myfam`), item by item.

| Web | iOS | Note |
|---|---|---|
| Header: the centred dailyFAM wordmark, nothing else (the bookshelf moved to search) | Done | |
| Tap DailyFAM again: from View more back to the rails and reloaded; scrolled down, back to the top only; at the top, refetched; arriving from another tab refetches | Done | |
| Friends' stories row: your story with its (+) badge, friends with fresh (gold) / seen (grey) rings, 60 pt faces in a 72 pt ring, Invite (dashed) | Partial | seen state per device. Your face with no story, its (+) and a friend with no story do nothing: on the web they open the note composer and the friend's profile, neither built yet (`no-dead-controls`). Invite shares the referral link from `/api/waitlist/me` while the waitlist runs, else the app's address |
| Story viewer: 6 s a story, bars, opens on the first unseen (also on a swipe to another friend), tap back/on (back past the first goes to the previous friend's last), hold (hides the chrome), swipe between friends, the title and Play episode open the episode (or the player, when it is the one loaded), reply with the send inside the box, heart (the first like also sends a ❤️ message; on notes too), VIBE! to your story; the ⋯ list (Save for later, Share, Add to Queue, Add topic to playlist, Report, Block; on your own, Delete vibe / Remove from your story) | Partial | VIBE! posts the episode with a caption (`/api/vibe`) - the web opens its full story editor, not ported. The header opens no profile (none built). A note is drawn from its layout (background, words, stickers, @mentions, not tappable); a vibe laid out in the web's editor is drawn as the plain title page, not its canvas |
| Pick up where you left off: resume (time left, bar) and thread cards, X dismisses for good | Done | resumes seek once the audio has arrived. **Server bug found:** progress keeps `minutes` as an integer, so a 1.5-minute resume is asked as 1 minute (a different episode) - on the web too |
| Five rails in the server's order, "Made for you, {Day} {part}" / "Start here", the others' headings | Done | |
| Each rail's empty sentence from the server; friends rail silent with nobody followed | Done | |
| Card: picture (with none, the web's `seedThumbSVG` drawing by `icon`), subject chip (`thumb_facet`, else the first tag), ready badge, the web's save bookmark (an outline, on sage once saved) and ⋯, friends pill (three 22 pt faces from the page's own face book, "+N" in copper; a tap lists who listened) or the words alone (not tappable), title size steps (>48, >72), live line (copper and dotted only while in progress and under 20 min old) with its age, hook with each rail's fallback | Done, differs on purpose | the owner's call for iOS: the picture at its full 4:3 (the web crops rail pictures to a 96 pt strip), the whole hook (the web clamps it to two lines), and the friends' faces on the subject chip's line, centred on it (the web lifts them above the chip) |
| Card ⋯ in the web's order with its icons: Share, Add to myFAM Playlist, Add to Queue, Go to Queue, Not interested (the frown); Not interested takes the card off every rail and any open View more | Done | |
| "N friends listened": a centred card, bordered round close, a row a friend | Partial | the web's rows open each profile; none built, so the rows carry no chevron and do nothing |
| A rail ends in a faint chevron until scrolled to its end, then VIEW MORE → View more: 16 at a time, Refresh (back to the top each page), the reason line, wrap notes (never "The last of this rail" on Made for you, whose first page is the rail's own tiles and never caught up), caught up, Trending by continent, faces from the page's own `friend_faces` | Done | |
| Find new friends pill after the friends rail (alone when you follow nobody) | Partial | the web opens the Friends screen; that is not built, so the pill opens the invite share sheet (the Invite face's) |
| Guest sample note | n/a | the iOS app requires an account |
| Offline: the bar, dimmed cards, the remembered page | Not yet | |
| Loading skeleton; the error sentence | Done | |
| Library (bookshelf on search): A-Z with sticky letters, rows inset 36 pt, day labels, plays left out at none, search as you type, the web's sentences (centred, faint), "Search FAM for it" puts the words in searchFAM's box | Done | |
| Saved toast's "Open" | Not yet | opens Save for Later on the web; no such screen here, so the toast has no action |
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
| ⋯ menu in the web's order: Share, VIBE!, Save, Captions, Add to myFAM Playlist ("myFAM" drawn as the wordmark), Remove from this playlist, Add to/Go to Queue, Report (1.2) | Done | "Remove from this playlist" only for your own mix, as a `remove` operation (the web sends the whole list, §498); the playing order is spliced as the web does so the next item still follows; the rows' other icons are SF Symbols, not the web's `MENU_ICONS` |
| Speed 0.5-2x, keep the voice's pitch (`speed-pitch`) | Done | time-pitch unit; length for searches rebuilds |
| Queue (§190), next-in-queue button, badge | Done | |
| After an episode: four recommendations, first starts in 15 s (`post-episode-grid`) | Done | `/api/nextup`; album tile n/a |
| The searcher shown if `searches_public` | Done | in the ⋯ menu header |
| Opus stream (stage 3) and signed replay links (§252) | Not yet | PCM today |
| Kept episodes replay by `X-FAM-Episode` (§173) | Not yet | |

## myFAM (mixes)

Checked against phone-size screenshots of the web and measured sizes, colours
and icons (2026-11-10), and a specification written from `static/index.html`
(`screen-playfam`, `screen-newmix`, `screen-mixdetail`, `screen-addtopics`,
`screen-publicmix`), item by item.

| Web | iOS | Note |
|---|---|---|
| The myFAM tab: wordmark, Create new mix (shown once `/api/mixes` answers), subtitle, your mixes (cover, meta with public/private, from, joint; preview), empty and error sentences | Done | every arrival opens on the list and asks afresh; the tab icon carries the web's filled tone under its chevrons |
| Public-mix search: Cancel, 180 ms debounce, out-of-order answers dropped, the four sentences, result cards with (+) | Done | the (+) reads the loaded list once it has loaded; a tap on it never opens the mix |
| Category filter chips on the myFAM tab (§500): All, followed (starred), facets, More; inside a category the X, root and sub chips; the 16:9 hero in the facet's colour (surface-2 for an unknown facet, the E badge on an explicit title), Play all · N min, Follow/Following, episode rows; Find a category | Done | plays replay-only with no surface, and only the episodes after the one tapped are queued, each still replay-only; the web's oversized row picture (a bug the shots found) is not copied |
| New mix: X, the step label, cover, live name preview, 40-character name, the client's duplicate check, Create opens the empty mix | Done | |
| A mix's page: hero, Change, people line (your own face and name on a mix that is yours alone, from `/api/profile`) and Who's in it (a sheet; rows open nothing until the profile screen exists), Share and Invite, Add topics, Play today's N, Today's edition date, topics with the adder's face | Done | updates in place (the web pushes the page again); Back asks for the list afresh |
| Drag to reorder (one whole-list write) | Done | the new order is drawn at once and kept until the server answers; a refusal says the server's sentence and puts the old order back |
| Recommended topics: five, Shuffle, personalised order or local ranking (your profile's `interests_shown`, else every facet, then the mix's facets), (+) adds | Done | |
| Listen time: the time pill ("--:--" when none), picker, Clear, the toasts | Partial | the picker is a wheel in a small sheet and writes once, on Done; saved for the server; the "ready" notification is Web Push only - APNs is not built on the server, and the row says so |
| Public / Private switch | Done | |
| ⋯ menu, owner and member, each action and sentence | Done, differs on purpose | Delete asks first (the web deletes in one tap); Rename starts from the current name |
| Joint mixes: invite link, make it yours alone, leave, member permissions | Partial | joining from a `/j/` link needs universal links (not yet); invites go to iOS's share sheet (no title on it) |
| Covers: choose, change, remove, the AI-check note before a photo is used (new mix and mix page) | Partial | cut square from the middle; the web's position-and-zoom editor not ported |
| Sharing a mix (make public first): "Share this mix" with the server's destinations (copy, iMessage, Email, WhatsApp, X, Facebook, LinkedIn) and the no-public-address note | Partial | sending to a person or group inside FAM (`/api/messages`) waits for Messages - the episode share has none either; the Instagram/Snapchat story cards not ported |
| Add topics: recommended, typed topic first (short-named by the server), teams and companies, topics, (+)/✓ as operations; the web's 48 subject icons | Done | icons generated from `CAT_ICONS` by `tools/ios_icons.py`; a test fails if they drift |
| Somebody else's public mix: owner, Add to myFAM / In your myFAM, play, remove a copy | Done | the owner's profile link waits for the profile screen |
| Add to myFAM Playlist sheet (player, card and story ⋯) | Done | with nothing playing it says "Play something first" and opens nothing |
| Playing a mix: the playlist, today's edition prompts, the glide loading screen, skip (which starts the failure count again), failures (two in a row, or no connection, stop it), an allowance refusal ends it in the server's words, Next from {mix} in the Queue, the finished overlay | Partial | the glide's status line never carries the server's "cannot write" note: the app does not read `/api/health`, where the web gets it |
| Guest sample mix | n/a | the iOS app requires an account |

## Social

| Web | iOS | Note |
|---|---|---|
| Messages tab, threads, group chats (§213) | Not yet | `/api/messages` |
| Friends: follow, friends' vibes first, find friends | Not yet | |
| A friend's profile screen (`screen-person`) | Not yet | `/api/person` |
| VIBE! story editor, your story, replies (§213, §282) | Partial | your story plays, replies / likes / VIBE! with a caption work from DailyFAM's story viewer; the editor and the note composer are not built |
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
