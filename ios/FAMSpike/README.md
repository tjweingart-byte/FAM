# FAMSpike — the lock-screen audio spike

`IOS_APP.md` stage 2, the go/no-go for the iOS app: **one throwaway app that
plays a FAM episode from the real server while it streams, and keeps playing
with the phone locked and the app in the background for ten minutes.** No
login, no design. If this works on a phone, everything else is ordinary app
work; if it does not, nothing else matters yet.

**Status: written on Linux, not yet compiled.** The first build happens on a
Mac. `tests/test_ios_spike_contract.py` checks in CI that the two requests it
makes are the ones the server answers for a guest.

## What it does

1. `GET /api/v1/welcome` - today's sign-up samples: the most-played episodes
   whose audio is kept. Open to a guest and past the waitlist.
2. Tap one: `GET /api/v1/audio?q=…&minutes=1.5&fmt=pcm&cached_only=true` - a
   replay, so nothing is written and no voice wakes. The server sends bare
   16-bit mono PCM at `X-Sample-Rate`; `StreamPlayer` turns it into buffers
   on an `AVAudioPlayerNode` as it arrives and starts once half a second is
   in hand.
3. **Keep going** plays the next sample when one ends, so one session can run
   past ten minutes on 1.5-minute episodes.

It handles what iOS does to audio: the lock screen and Control Center
(play/pause, title), a phone call or alarm (pause, resume when iOS says so),
headphones pulled out (pause), AirPods connecting (engine restarted), and a
dropped connection (reconnect up to three times, skipping what already
arrived). Every one of those writes a line to the log on screen, with
`[fg]`/`[bg]` for whether the app was in front.

## Build it

On the Mac, with Xcode 26:

```bash
brew install xcodegen
```

```bash
cd ios/FAMSpike && xcodegen && open FAMSpike.xcodeproj
```

Then in Xcode:

1. Select the **FAMSpike** target > **Signing & Capabilities** > Team:
   **Apali LLC**. Leave *Automatically manage signing* on.
2. Plug in an iPhone, pick it as the run destination, press **Run** (⌘R).
   The first time, the phone asks you to trust the developer: Settings >
   General > VPN & Device Management.

The bundle id is `net.familiarize.fam`, the app record you already made, so
the same build can go to TestFlight.

**Without Homebrew:** File > New > Project > iOS App (SwiftUI), name it
FAMSpike, bundle id `net.familiarize.fam`; delete its `ContentView.swift` and
`…App.swift`; drag in the four `.swift` files, `PrivacyInfo.xcprivacy` and
`Assets.xcassets` from `FAMSpike/`. Then under the target's **Signing &
Capabilities** add **Background Modes** > *Audio, AirPlay, and Picture in
Picture*, and under **Info** add `ITSAppUsesNonExemptEncryption` = NO.

## The test, on a real phone (not the simulator)

1. Open the app, **Load today's samples**, leave *Keep going* on, tap one.
2. Lock the phone. Put it in a pocket. Wait ten minutes.
3. Unlock: **This session** should read past 10:00, and the log should show
   `phone locked` followed by `finished` / `asked for` lines marked `[bg]`.
4. Then, one at a time, while it plays:
   * walk out of Wi-Fi range (cellular takeover) - look for `stream broke` /
     `reconnected`, and listen for a skip or repeat at the join;
   * Airplane mode for five seconds, then off;
   * have someone phone you; hang up - it should resume;
   * pull out wired headphones or take out AirPods - it should pause;
   * play/pause from the lock screen and Control Center.
5. Tap **Copy** and paste the log into PROBLEMS.md (or send it over).

**Pass** = ten minutes locked with no gap, and every interruption ends in
either playing or a log line saying why not. Anything that hangs silently is
the finding.

## To internal TestFlight (no review)

1. In Xcode: **Product > Archive** (destination *Any iOS Device*).
2. In the Organizer: **Distribute App > App Store Connect > Upload**.
3. 5-30 minutes later it is under App Store Connect > TestFlight > iOS
   Builds. Add an internal group, add yourselves, install from the
   **TestFlight** app.

The spike is version **0.1.0**. Every upload needs a higher build number:
bump `CURRENT_PROJECT_VERSION` in `project.yml` (then `xcodegen` again)
before each one. The real app's first build must be higher than the last
spike's.

## If there are no samples

`/api/welcome` returns only today's most-played episodes whose audio is kept,
with their own picture. On a quiet day that can be none: play an episode or
two in the web app on that server, then **Load** again.
