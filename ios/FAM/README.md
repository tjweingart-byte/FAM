# FAM for iOS

The native app (`IOS_APP.md` stage 4): a client of the same API the web uses,
built on the player that passed the lock-screen spike (`ios/FAMSpike`,
PROBLEMS.md §537).

**Stage 1 (this):** sign in / make an account (terms ticked, §228), the
waitlist's waiting screen with today's samples, **DailyFAM** (the rails, a
tap plays), **Search** (1-5 minutes, the AI question asked first), the
player (picture, draggable bar over what has arrived, ±15 s, lock screen),
the mini player, and **You** (privacy and safety, sign out, delete account).

**Everything else** is listed, row by row against the web app, in
[`PARITY.md`](PARITY.md). Update it in the same change that moves a row.

**Status: written on Linux, first compiled on the owner's Mac.**
`tests/test_ios_app_contract.py` checks in CI that every request routes,
every decoded field is sent, and the waitlist still lets the samples through.

## Build it

```bash
cd ios/FAM && xcodegen && open FAM.xcodeproj
```

Signing & Capabilities: Team **Apali, LLC**. Product > Run with the iPhone
plugged in. The bundle id is `net.familiarize.fam`, the same as the spike,
so it **replaces the spike** on the phone. Build number 2; raise
`CURRENT_PROJECT_VERSION` before every TestFlight upload.
