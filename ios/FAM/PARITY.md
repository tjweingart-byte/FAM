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
| Dark mode (Settings) | Not built, on purpose | light only (`typefaces`, §250, the owner's call); Settings draws no Dark mode row rather than a dead one (`no-dead-controls`) |
| Larger phone text never breaks a screen (`large-text`, §499): a fixed height on anything holding words is a floor; a heading never squeezed by a control on its row; the wordmark on one line | Partial | every size scales (`relativeTo`); every fixed height round words - pills, buttons, search fields, the Pick up cards, the friends pill, the story's reply box, the profile's tabs - is a floor (`frame(minHeight:)`), identical at the default size; the wordmarks never wrap (`fixedSize`); a View more heading wraps rather than being cut to two lines by Refresh. Not yet looked at on a phone at the accessibility sizes. Left as on the web: the mix glide's 96 pt cards (the web's are fixed too) |
| Motion (§292: screen pushes, the burst on save/vibe) | Partial | the player's save / VIBE! burst (squeeze, ring, six sparks, the SAVED / VIBED lozenge, VIBED 300 ms after the composer), the title wipe, the picture's blur-up, the What's next rise, the modals' fade and lift and the mini bar's rise are drawn with SwiftUI animations at the web's timings; screen pushes are iOS's own |

## Navigation

| Web | iOS | Note |
|---|---|---|
| Bottom bar: dailyFAM, myFAM, search (centre), Messages, yourFAM (the 11.9 packet's labels, §519) | Done | five equal slots; each icon has the web's tone fill (.16, .45 when on); Messages carries the coral unread badge ("9+" above nine) from the notifications poll; tapping Messages opens its list read afresh |
| No tab bar or mini player on a conversation, a profile or Friends | Done | the web's pushed screens have none; opened from another tab they come up over it with a stack of their own |
| No tab bar or mini player on a rail's View more (`screen-section`) | Done | |
| Mini player above the bar, X stops (`transport`) | Done | 58 tall, inset 12, the strong border; a 38 pt crop of the picture (headphones when none), the title with its E, "m:ss left", the copper play and a bare X ("Hide the player"), the 2 pt progress along the bottom; shown from the first word until the episode ends, fails or is stopped, as the web's `showNowBar` / `hideNowBar`; rises and drops with the web's timing. Its title is the full player's title - the web's bar takes the writer's title even over a tile's own (`titleOverridden`); the owner chose the player's for both |
| Leaving the player never stops it | Done | |
| No exploreFAM (§499): no reel, no pill on search or beside Made for you, no swipe from search; a category plays in the player | Done | the iOS app never had the reel; nothing reaches `/api/explore` or the comment endpoints. "Explore New" (`might_like`) is a different screen and keeps its name, as on the web |

## Accounts and the waitlist

| Web | iOS | Note |
|---|---|---|
| Sign up (email + password, terms box unticked, §228) | Done | |
| Log in | Done | |
| Phone number on sign-up | Deliberately off | off for iOS 1.0, the owner's decision (APP_STORE.md gap 12): neither the sign-up form nor the identity step asks for it, and "required" is on the name alone |
| Sign in with Apple / Google (`op-identity`, 4.8) | Done (iOS only) | the web has neither on purpose. Apple: nonce hashed to Apple, raw to the server, the one-time code sent so deletion revokes. Google: GoogleSignIn, client `FAM iOS` in Google Cloud project `fam-app-514301`. Both confirmed on a phone, linked to one account (2026-11-10). Both wait for the terms box on sign-up |
| Linking Apple / Google to an account (YourFAM → Sign-in methods) | Done (iOS only) | the server never joins accounts by email, so a provider used while signed out makes its own account; linking is a provider sign-in while signed in. Unlink when another way in remains |
| Delete the account from the waiting screen (5.1.1(v)) | Done | `/api/account` is open past the waitlist |
| Password reset (`/reset`, §297) | Done | on the log-in screen, as the web's: "Forgot password?" only when `/api/auth/reset` says `available`; email → "Email me a code" → the 6-digit code and a new password ("Set new password", the eye, "Send a new code", "Remembered it? Log in"); the server's `message` becomes the note, its refusals show under the fields, over the button; done, the phone is signed in (`want_token`) and told "Password changed. You're signed in." |
| Identity step after sign-up (name, handle, photo) | Done | after a sign-up, a log-in (email, Apple, Google) or a password reset, as the web's `afterAccount` - which the web's log-in and reset run too, so an existing account the server has no `intro_done` for is asked, there as here: interests already done (`intro_done`) - straight in; a name, a handle and a place - the interests; otherwise "Who are you?" (the three step marks, no X, "required" on the name, Next, Skip for now dimmed and refused while the name is missing, the required-fields popup that puts you back on the name), then the interests ("Start listening" / "Skip for now"), then DailyFAM. The phone field is off (row above) |
| Activation step (§411) | Partial | the editor is built ("Your details" from Settings' Date of birth / Gender: the date, one place box, the gender chips, the counting notice with its link, the under-13 confirm, the deleted message); the first-time owed modal at launch (`activation_needed`) is not shown on iOS |
| Terms re-acceptance when the version changes | Not yet | `terms` on `/api/consent` |
| Waitlist: waiting screen plays today's samples | Done | |
| Intro / first-run screens | Partial | the identity step and the interests list (above) are built; the boot intro animation and the welcome screen are not |

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
| Friends' stories row: your story with its (+) badge, friends with fresh (gold) / seen (grey) rings, 60 pt faces in a 72 pt ring, Invite (dashed) | Partial | seen state per device. A friend with no story opens their profile (no handle: the conversation, for a friend only); your face with no story and its (+) do nothing - the note composer is not built (`no-dead-controls`). Invite shares the referral link from `/api/waitlist/me` while the waitlist runs, else the app's address |
| Story viewer: 6 s a story, bars, opens on the first unseen (also on a swipe to another friend), tap back/on (back past the first goes to the previous friend's last), hold (hides the chrome), swipe between friends, the title and Play episode open the episode (or the player, when it is the one loaded), reply with the send inside the box, heart (the first like also sends a ❤️ message; on notes too), VIBE! to your story; the ⋯ list (Save for later, Share, Add to Queue, Add topic to playlist, Report, Block; on your own, Delete vibe / Remove from your story) | Partial | VIBE! posts the episode with a caption (`/api/vibe`) - the web opens its full story editor, not ported. The header opens the friend's profile once the story closes; a report the server hides closes the story; Block surfaces the server's sentence. Share opens "Share this FAM" for the vibe's episode (friends and groups rows, then the destinations) while the story holds. The app's toast is drawn over the story ("Sent to Beth" after a reply), as it is over the share sheets and New message. A note is drawn from its layout (background, words, stickers, @mentions - a mention opens that person's profile once the story closes, `storyOpenTag`); a vibe laid out in the web's editor is drawn as the plain title page, not its canvas |
| Pick up where you left off: resume (time left, bar) and thread cards, X dismisses for good | Done | resumes seek once the audio has arrived. **Server bug found:** progress keeps `minutes` as an integer, so a 1.5-minute resume is asked as 1 minute (a different episode) - on the web too |
| Five rails in the server's order, "Made for you, {Day} {part}" / "Start here", the others' headings | Done | |
| Each rail's empty sentence from the server; friends rail silent with nobody followed | Done | |
| Card: picture (with none - since §507 an episode nothing places has none, never a hashed one - the web's `seedThumbSVG` drawing by `icon`), subject chip (`thumb_facet`, else the first tag), ready badge, the web's save bookmark (an outline, on sage once saved) and ⋯, friends pill (three 22 pt faces from the page's own face book, "+N" in copper; a tap lists who listened) or the words alone (not tappable), title size steps (>48, >72), live line (copper and dotted only while in progress and under 20 min old) with its age, hook with each rail's fallback | Done, differs on purpose | the owner's call for iOS: the picture at its full 4:3 (the web crops rail pictures to a 96 pt strip), the whole hook (the web clamps it to two lines), and the friends' faces on the subject chip's line, centred on it (the web lifts them above the chip) |
| Card ⋯ in the web's order with its icons: Share, Add to myFAM Playlist, Add to Queue, Go to Queue, Not interested (the frown); Not interested takes the card off every rail and any open View more, records `not_interested` and says "Got it — you’ll see less like this" (§499; the server keeps it off every rail, Trending and What's next from then on); no button on the card | Done | Share opens "Share this FAM" for that card's episode (`openShareModal(ep)`): its friends and groups rows, then the destinations |
| "N friends listened": a centred card, bordered round close, a row a friend | Done | a row with a handle opens that profile once the card has closed |
| A rail ends in a faint chevron until scrolled to its end, then VIEW MORE → View more: 16 at a time, Refresh (back to the top each page), the reason line, wrap notes (never "The last of this rail" on Made for you, whose first page is the rail's own tiles and never caught up), caught up, Trending by continent, faces from the page's own `friend_faces` | Done | |
| Find new friends pill after the friends rail (alone when you follow nobody) | Done | opens Friends |
| Guest sample note | n/a | the iOS app requires an account |
| Offline: the bar, dimmed cards, the remembered page | Not yet | |
| Loading skeleton; the error sentence | Done | |
| Library (bookshelf on search): A-Z with sticky letters, rows inset 36 pt, the day each was made (§499: TODAY, YESTERDAY, N DAYS AGO to six, then OCT 28 - ", 2025" once not this year - counted in calendar days on the phone, the month in English as the web's `MONTHS_SHORT`), plays left out at none, search as you type, the web's sentences (centred, faint), "Search FAM for it" puts the words in searchFAM's box | Done | |
| Saved toast's "Open" | Done | opens Save for Later on yourFAM (`openSaved`), the player and any cover taken down first |
| Arrival animation of rails | Not yet | |
| Pull to refresh | iOS only | the web has none; kept as iOS convention |

## The player

Checked against the 11.9 packet (§519) web player: phone-size screenshots and
measured sizes (`player-layout`, `transport`, `queue`, `post-episode-grid`,
`next-is-prediction`, `live-captions`, `speed-pitch`).

| Web | iOS | Note |
|---|---|---|
| Full-screen player: Home mark, GO DEEPER, ⋯ (the web's three dots); picture 40 pt wider than the screen, 4:3, faded, blurring up as it loads; title (Bricolage 22, three lines) with the 17 pt explicit E and the writer's title wiped in over 320 ms (`titleOverridden` keeps a tile's) | Done | |
| Share / VIBE! / save on the right (§192), 34 pt up (+12) and above the captions card (§519); the web's glyphs; captions hung 43 pt below; VIBE! on copper, save on sage, each caption's words in that colour | Done | |
| Save / VIBE! burst when turned on (`Motion.celebrate`) | Done | ring, six sparks and the lozenge as plain SwiftUI shapes at the web's timings |
| Share sheet ("Share this FAM") | Partial | sends inside FAM to a group or a friend (friends only, §520; with the heard episode's id, which the web's sheet drops; "Sent to Beth"), each row's search filtering by name or @handle, and lists the server's destinations; the same sheet opens from a card's ⋯ and a story's ⋯; it is a bottom sheet, not the web's centred card |
| VIBE! story editor (`openVibeComposer`) | Not yet | VIBE! posts the episode with a caption; the web's editor (layouts, fonts, stickers, @tags) is not ported |
| Sources pill by the title ("from knowledge" when known and none), and the Sources card | Done | the web's centred card with its round X; favicons over initials; green live rows, copper attachments, ↗ when it opens; "Cross-checked across …" |
| Scrubber: requested length until complete then the real one, the arrived range, the knob's ring, a drag clamped 2 s short of what arrived, sought on release, "Caught up with the writing — it will keep going" landing short | Done | |
| Transport (§519): five-slot grid at most 330 wide, empty first slot, bare 48 pt ±15 with the web's 34 pt arrows and a Geist 10.5 bold "15", the 66 pt copper play with its shadow and icon swap, the solid 26 pt next in slot five; bottom padding 22 | Done | ±15 with nothing playing says "Nothing playing yet" |
| Next: shows for a playlist or a non-empty queue; "Skip to the next episode in this playlist" | Done | the web's label even when it is the queue's next |
| Speed · length pill, Captions pill, queue (32 pt, "QUEUE" under it, the badge popping when something is queued) | Done | |
| Captions (§519): the Captions pill, the dark card over the picture - one sentence at a time, Bricolage 24 semibold, cross-fading - with copy ("Transcript copied ✓") and X, a swipe down closes, remembered on the device (`fam.captions`) and turned back on for each new episode; `/api/transcript` at once and every 1.2 s until done | Done | replaces the whole-script captions sheet iOS had (an earlier owner call, reversed) |
| ⋯ menu in the web's order with the web's icons: Share, VIBE!, Save, Closed captions · On/Off, Add to myFAM Playlist ("my" in the row's type, F A M the wordmark's glyphs, muted like the row icons), Remove from this playlist, Add to / Go to Queue, Report; the header's picture (only when there is one), title and the searcher if `searches_public` | Done | "Remove from this playlist" only for your own mix and only while it plays its own entry (hidden during a detour), as a `remove` operation (the web sends the whole list, §498) |
| Go Deeper (`next-is-prediction`, §170): the centred modal - "Pick up the thread" / "Follow the thread" chip, the box, 1-5 lengths on the playing length, Cancel / Continue; the episode keeps playing, and one ending under it starts nothing (no queue, playlist or What's next) | Done | a typed follow-up is not run through the server's spelling fix at send (`acCorrectQuestion`, `/api/spell`) - not built on iOS for searches either |
| Playback modal: Speed (action sheet 0.5-2x, "Voice" · "Keep the voice's pitch ✓", `speed-pitch`), Length for a search ("Rebuilding at N min"), Done | Partial | the search's Voice row is not built: iOS searches have no voice choice yet (`/api/voices`) |
| Queue (§190, §519): "Queue", Now playing (picture or ▶; "Going deeper · {mix} carries on after" during a detour), "Next from {mix}" numbered (during a detour the interrupted episode heads it, since it resumes first - the owner's call), "Next in queue" with pictures or places, "Queued", 40 pt ×, Undo; a tap plays it and ends a playlist; "Added to Queue · Open" opens it | Done | |
| A playlist (§134) and Go Deeper (§519): a follow-up started from the player during a playlist - Go Deeper, What's next's lead or search, nested follow-ups - is a detour, not the end; anything else started ends the playlist | Done, differs on purpose | the owner's call - the interrupted episode resumes: when the detour ends, is skipped or fails ("That follow-up couldn’t play — back to your playlist."), the playlist's episode it interrupted replays by its episode id and seeks back to where it was, then the playlist carries on after it; if that episode had already finished, the playlist goes on to the next. A replay the server no longer keeps (409) is asked again by its question behind the same glide, and still seeks back. The web's §519 goes straight on to the next episode |
| After an episode: four recommendations, the first starting in 15 s with its emptying bar, the search box pausing it, Back to search / Back to DailyFAM (`post-episode-grid`); only on the full player, never under the mini bar, an open Go Deeper, a playlist or its detour, or with something queued | Done | `/api/nextup`; the album tile is n/a (no albums on iOS); the search box's follow-up is not run through the server's spelling fix (`/api/spell`), as Go Deeper's is not |
| Polling: `/api/next` every 2 s before the first word for a title the writer's may replace (`titleWatch`, so the player opens on the provisional title), at the first word, +6 s, +20 s, at the end and on Go Deeper; `/api/sources` +6 s, +20 s, then every 8 s while playing (75 tries), on the writer's final title, at the end and on Go Deeper; `/api/transcript` while captions are on; progress every 15 s while playing, at once on pause, at the end and when the app goes to the background (the web's page hide); complete at 85 %, skip when left after 5 s | Done | a playlist's episode a Go Deeper interrupts is not reported as a skip: its position is sent instead, because it resumes (the owner's call) |
| Failures: the server's sentence or "Could not generate that briefing", the player taken down first; a playlist's failures as the web words them, read from what was being made (`lastGenerate`), not what is on the player | Done | |
| "not your episode" badge over the picture (`.p-demo`, a server with no model key) | Not yet | a development server's mark; production always has a key |
| Album swipe and "scroll up for next in {album}" (`#nextHint`) | n/a | no albums (Play-all) on iOS |
| "Which one do you mean?" (§426): `ask_reading` on any tap that may write; the server's readings (up to four), the choice joining the question | Done | |
| Limit screen (`showLimitReached`) | Partial | the server's title and sentence, the meter, "Resets … in your time", Not now; no "See plans" (no plans screen or checkout on iOS). Drawn only on the server's 429 quota refusal, which production no longer sends: everyone is unlimited (`ENFORCE_QUOTAS=0` in `render.yaml`, §519); the app shows no meter, plan or allowance anywhere else |
| Toasts and snacks: the plain line with the copper rule (§299), 2.2 s / 3.5 s with an action | Done | "Saved for Later · Open" (player, a card's bookmark, a message's card) opens Save for Later on yourFAM, the player and any cover taken down first; "Vibed to your story · View" has no action (the story view is not opened from here) |
| Offline: play an episode kept on the device, the offline bar | Not yet | no OfflineShelf on iOS |
| Lock screen: title, play/pause, ±15, scrub | iOS only | the web has no Media Session; kept |
| Opus stream (stage 3) and signed replay links (§252) | Not yet | PCM today |
| Kept episodes replay by `X-FAM-Episode` (§173) | Done | a message's Listen replays the sender's exact episode (`episode=`), asking the question when it is gone (409); a playlist's interrupted episode resumes the same way; a listening history row replays the episode that was heard, under the tab it was first heard on |

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
| A mix's page: hero, Change, people line (your own face and name on a mix that is yours alone, from `/api/profile`) and Who's in it (a sheet; a row with a handle opens the profile), Share and Invite, Add topics, Play today's N, Today's edition date, topics with the adder's face | Done | updates in place (the web pushes the page again); Back asks for the list afresh |
| Drag to reorder (one whole-list write) | Done | the new order is drawn at once and kept until the server answers; a refusal says the server's sentence and puts the old order back |
| Recommended topics: five, Shuffle, personalised order or local ranking (your profile's `interests_shown`, else every facet, then the mix's facets), (+) adds | Done | |
| Listen time: the time pill ("--:--" when none), picker, Clear, the toasts | Partial | the picker is a wheel in a small sheet and writes once, on Done; saved for the server; the "ready" notification is Web Push only - APNs is not built on the server, and the row says so |
| Public / Private switch | Done | |
| ⋯ menu, owner and member, each action and sentence | Done, differs on purpose | Delete asks first (the web deletes in one tap); Rename starts from the current name |
| Joint mixes: invite link, make it yours alone, leave, member permissions | Partial | joining from a `/j/` link needs universal links (not yet); invites go to iOS's share sheet (no title on it) |
| Covers: choose, change, remove, the AI-check note before a photo is used (new mix and mix page) | Partial | cut square from the middle; the web's position-and-zoom editor not ported |
| Sharing a mix (make public first): "Share this mix" with the server's destinations (copy, iMessage, Email, WhatsApp, X, Facebook, LinkedIn) and the no-public-address note | Partial | sends inside FAM to a group or a friend (the link's words as a message); destinations are rows, not the web's grid; the Instagram/Snapchat story cards not ported |
| Add topics: recommended, typed topic first (short-named by the server), teams and companies, topics, (+)/✓ as operations; the web's 48 subject icons | Done | icons generated from `CAT_ICONS` by `tools/ios_icons.py`; a test fails if they drift |
| Somebody else's public mix: owner, Add to myFAM / In your myFAM, play, remove a copy | Done | the owner line opens their profile |
| Add to myFAM Playlist sheet (player, card and story ⋯) | Done | with nothing playing it says "Play something first" and opens nothing |
| Playing a mix: the playlist, today's edition prompts, the glide loading screen, skip (which starts the failure count again), failures (two in a row, or no connection, stop it), an allowance refusal ends it in the server's words, Next from {mix} in the Queue, the finished overlay | Partial | the glide's status line never carries the server's "cannot write" note: the app does not read `/api/health`, where the web gets it |
| Guest sample mix | n/a | the iOS app requires an account |

## Social

| Web | iOS | Note |
|---|---|---|
| Messages tab: list (1:1 and group, unread dot, previews as the web words them), Search messages in memory, Loading / error / empty / no-match notes (the empty note's bold **Friends** inline, opening Friends), re-read on every return | Done | no guest gate (the app needs an account); one unreadable preview line no longer costs the whole list |
| New message: pick friends (one = chat, two or more = group with an optional name), or find by name/@handle | Done, differs on purpose | friends only (§520, the server refuses anyone else), so the label says "Pick one or more friends" (the web's still says "people you follow"); the graph is asked afresh on every open; typed lookups show the results to pick from (the web opens the first hit and says "Name or phone number", quirk 6); a typed non-friend gets the server's sentence before a conversation opens |
| A conversation: header (View profile / N people · See who's here, interest chips, N in common), a group's "You’re not friends with …" warning from the server (§520), day separators (every row, quirk 4), bubbles, episode cards (Listen replays the sender's episode, GO DEEPER on it, save), story replies and the big ❤️, "You finished it" receipts, group system lines and sender names (once, quirk 5), typing dots, pop-in of new rows, 2 s poll on `head` (row id) deduped by id, pending at 55%, a refused send put back with the server's sentence, composer to 112 pt where Return is a new line, the (+) with playing / saved / vibed episodes, typing notes every 2.5 s, an open that failed retried quietly on the poll's beat | Done | FAM's own `/api/spell` autocorrect behind the keyboard is not ported; chips do not open the Topic screen (none on iOS) |
| Thread ⋯: View profile, Delete chat, Report, Block; group: See who's here, Name the group, group photo (library, camera, file, remove), Leave, Delete, Report this chat | Done | confirmations are dialogs with normal-case messages (quirk 3); rename is an alert with a field |
| Report a single message (held down, with Copy beside it) and Report / Block a group member; See who's here marks members you are not friends with (`friend`, §520) | Done, iOS only | the server takes both; the web has no control for either (quirks 1, 2) and draws no `friend` mark |
| Notifications: bootstrap then a 3 s poll while in front, banners (message, group episode, follow) one at a time 3 s apart 0.42 s, in the web's words (an episode message - a share, a vibe reply or like - reads "Shared: <title>"; a follow "<Name|Someone> started following you"), swipe up / X / hold, the follower popup with Follow back (or the sage "You already follow them — you are friends."), `announced` once ever | Done | no APNs (none on the server); banners also drop over the full-screen player, and a tap takes the player down before opening the conversation |
| Friends: search by handle, Invite new users + (referral link), counts, Friends / Following / Follows you, Follow / Following (either clears the search and re-reads the graph), opening clears new followers | Done, differs on purpose | Unfollow asks first (quirk 7); faces are initials on the person's colour everywhere (quirk 8b); reached from YourFAM's friends count (with the new-followers badge), Find new friends, a follow banner, Messages' empty note and a profile's ⋯ |
| A friend's profile (`screen-person`): hero (story ring under 48 h opens their story), place · @handle, standing, friends / vibes / mixes counts (friends a button only when their list is shown), Message + Follow/Following ("✓ Following" on the tap, put back if refused; by handle when there is no id), interests with N in common, Vibes / Mixes tabs (a vibe plays; a mix opens its public page, (+) adds it - "Added to your myFAM as <name>" with Open - and ✓ asks "Remove <name> from your myFAM?" first), their friends (only when mutual) with View more, the privacy note, ⋯ Unfollow / Your friends / Report / Block | Done, differs on purpose | Message only for a friend (§520: the web still draws it for anyone with an id and lets the server refuse the send); Unfollow asks first; interest chips do not open the Topic screen (none on iOS) |
| VIBE! story editor, your story, replies (§213, §282) | Partial | your story plays, replies / likes / VIBE! with a caption work from DailyFAM's story viewer; the editor and the note composer are not built |
| Report and Block on chats, messages, group members, profiles, stories, the player (1.2) | Done | Report is the web's action sheet (`FAMReportSheet`: the server's reasons under "Why are you reporting this?", the receipt as a toast); Block is FAMSafety's confirmation with a normal-case message (quirk 3); a block refusal shows the server's sentence; comments are gone from the web |
| Push notifications (a mix's "ready", §448) | Not yet | APNs not built on the server either |

## YourFAM (profile) and Settings

Checked against phone-size screenshots of the web, measured sizes and the
YourFAM specification (`screen-profile`, `screen-settings`, `screen-identity`,
`screen-location`, `screen-intro`, `screen-saved`, `screen-history`,
`screen-myvibe`), item by item. Guests never reach the app, so no guest gate
or guest rows are built.

| Web | iOS | Note |
|---|---|---|
| The hub's header: yourFAM wordmark, the 36 pt gear (Settings), no (+) (`friends-vibes-first`) | Done | the wash scrolls with the page under an opaque strip; the header stays on a failed load, with Try again (the web's error page has neither, Q2) |
| Hero: 92 pt face, name ("Set up your profile" unnamed), "place · @handle" / "no handle yet" | Done | faces are initials on the person's colour, as everywhere on iOS (quirk 8b) |
| Your story ring on your face (copper while unseen, faint once seen) opens your story | Done | from `/api/profile` `stories`, seen state shared with the faces row |
| Your face with no story and the (+) badge open the note composer | Not yet | the composer is not built, so the face is not a button and no (+) is drawn (`no-dead-controls`) |
| friends / vibes / mixes (singular and plural, 0 drawn), friends opens Friends, vibes / mixes switch the tab and scroll to it | Done | friends carries the new-followers count (Q10: the web fetches it and draws nothing), cleared by opening Friends |
| Edit profile ("Set up profile" unnamed) and Saved for Later (paper, bookmark) | Done | |
| Interests line: `interests_shown`, at most `interests_max`, one sideways line cut at the edge; "Add interests in Edit profile" when empty | Partial | the server chooses and orders (`op-profile-hub`); chips open no Topic screen (none on iOS), so they are not buttons; the empty chip opens Edit profile |
| Vibes / Mixes tabs (kept across redraws), 3:4 grid, titles never clamped, See all after twelve, the two empty notes | Done | a vibe plays by its question and length; a mix opens its page if it is yours, else its public page |
| Hold a vibe on your own grid: 500 ms (a move past 10 pt cancels), the sheet's head and "Remove vibe from profile" → `DELETE /api/vibe` | Done | the vibe goes whole - grid, story, My Vibe, folder - the profile, faces row and chat (+) re-read; "Vibe deleted" or "Could not delete that vibe"; VoiceOver has it as an action |
| Settings, YOU: Name and handle, Profile picture, Where you are, Date of birth, Gender, Time zone | Done | each opens its editor and returns to Settings (Q3 not copied); Where you are reads the profile's place before preferences answer (Q13) |
| Time zone: Automatic (zone) or one of the twelve, "Episodes use … time" (`listener-clock`) | Done | kept on this phone only; the pin goes out as `X-FAM-TZ` on every request, else the phone's own zone |
| Settings, LISTENING: Your interests (the whole list, X / Save), Listening history, My Vibes, Save for Later, Default speed (with the pitch under "Voice"), Keep the voice's pitch, Voice for your searches | Done | speed and pitch are the player's own device settings; the voice is kept on the server (§147) and so governs iOS searches too; the row is drawn only when the server reports voices. the row says "My Vibes" and the screen "My Vibe", as on the web |
| Dark mode, Say "Hey FAM" | Not built | light only (§250); no voice search on iOS - neither row is drawn |
| Settings, PRIVACY AND SAFETY: Send my questions to AI (Allowed / Off / Not asked yet; Turn off sheet, or the consent sheet), Count me in FAM's totals, Blocked people (count, "You haven't blocked anyone", the tap-to-unblock sheet), Terms / Privacy / Help | Done | toggle rows are rows whose value reads On / Off, as the web draws them (Q7 left to the owner); the three pages open in Safari's sheet |
| Settings, ACCOUNT SETTINGS: Subscription (the plans sheet), Private search, Show my name on episodes I searched, Signed in as, Change password, Log out, Delete account | Done | the plans sheet lists the server's tiers with "YOUR PLAN", the limits in the refusal's words and the note from `enforced` - no checkout (`tiers-off`); private search is honoured by the server for every search, so it works with no bubble on iOS's search; Delete is the web's sheet ("This deletes everything FAM holds about you, now. It cannot be undone." / "Delete my account"); the note under Delete account is the web's, word for word ("Password reset isn't available yet, …"), the owner's call over Q1. Sign-in methods (iOS only) sits under this group; there is no other group the web lacks |
| Change / set a password (`has_password`): Current → New, or Set a password | Done | the server ends every session on a change, this one too, so this phone signs in again with the new password - held only for that call, the token to the Keychain as any log-in's; "Password changed — other devices signed out". If that sign-in fails the dead token is dropped and the phone is back on the log-in screen |
| Sign-in methods: Apple / Google link and unlink | Done (iOS only) | a row under ACCOUNT SETTINGS opening its own screen |
| Edit profile: X, "Edit profile", the 78 pt picture with the camera badge (TAP TO CHANGE / ADD A PHOTO), YOUR NAME, USERNAME (the @ inside, cleaned as typed, the note under it - "A handle needs at least three characters.", the server's "@x is taken."), YOUR INTERESTS (N OF 5, shown → ranked → every facet, dim when full, add any topic), WHERE YOU ARE, ACCOUNT (Signed in as, Change password), Save | Done | `/api/me` always gets the name and handle together, the picture left out to keep it; interests written only if touched, typed topics added to the stored ones; a place that did not save is said (Q12); returns to where it was opened from |
| Profile picture: no picture - library / camera / file; with one - Move and scale (when chosen on this phone), Choose a different photo, Remove photo; "This photo was set on another device…" | Done | the editor: 270 pt stage, the circle and its dim, drag, 1-3x about the middle, always covering the circle, the AI-check sentence before Save; 256 px JPEG 0.85; a refusal is the server's sentence; "Set your name and handle first" from Settings opens Edit profile |
| Where you are: X, the three fields, Save, "Saved" / "Could not save that" | Done | the web's words, the note "Only you can see this." included (the owner's call over Q6) |
| Save for Later: All (no count), your folders with counts, + New folder, Rename / Delete folder; rows play by question and length and post `/played`; folder button (Put this episode in a folder, ✓, No folder, + New folder files it at once); X off at once, "Removed" / back with "Could not remove that"; the player's save goes off | Done | one 18 pt gutter (Q8); "Loading…" while the shelf loads (Q16); deleting a folder unfiles its episodes; no folder is ever made for the listener |
| Recent listening history: All / dailyFAM / myFAM / searchFAM (ids swapped, `names-swapped`), TODAY / YESTERDAY / FRI, NOV 6, "2 MIN · 6:58 · dailyFAM", the loading, error and empty sentences | Done | a row replays its episode id (`heard-is-kept`); a reply for a tab left mid-flight is dropped; nothing clears it, as on the web |
| My Vibe: folders, cards ("2 MIN · VIBED · BEST OF THE WEEK"), play, file, take back | Done | the same act as the grid's hold, in the web's words for this list: "Vibe taken back" / "Could not take that back" (the grid says "Vibe deleted") |
| Feedback report | n/a | the web's "Instant feedback" is demo chrome outside the phone frame, not a YourFAM control |

## Sharing

| Web | iOS | Note |
|---|---|---|
| Share an episode (`/s/<id>` links, story-card PNGs) | Not yet | |
| Universal links open the app | Not yet | needs the associated-domains file and entitlement |

## App Store requirements still open in the app

Sign in with Apple, Report and Block on user content, the AI consent sheet
(done), account deletion (done), the privacy manifest (in place), no
"beta/demo" wording, an iPad that does not break. See `APP_STORE.md` Part B.
