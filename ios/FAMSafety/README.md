# FAMSafety — the App Store safety screens for the iOS app

What Apple rejects an app like FAM for, as Swift the app target links
(`APP_STORE.md` Part B, PROBLEMS.md §222-§223):

| Guideline | Screen | Server |
|---|---|---|
| 5.1.2(i) third-party AI | `ConsentSheet`, driven by `ConsentModel` and `.aiConsentSheet(_:)` | `GET/POST /api/v1/consent`; `403` + `X-FAM-Consent: ai` on a refused generation |
| 1.2 report | `ReportSheet(api:subject:done:)` | `GET/POST /api/v1/report` |
| 1.2 block | `.blockConfirmation(...)`, `BlockedPeopleView` | `POST/DELETE /api/v1/block`, `GET /api/v1/blocks` |
| Settings | `PrivacyAndSafetySection` (AI toggle, blocked people, terms, privacy, support) | `/terms`, `/privacy`, `/support` |

Every word on these screens - the notice, the reasons, the "we review within
24 hours" - is the server's, so the app and the web cannot say different
things.

**Status: written, not yet compiled.** The session that wrote it ran on Linux
with no Swift toolchain (the download was blocked), so the first `swift build`
happens on a Mac. `tests/test_ios_safety_contract.py` checks, in CI, that every
path here is a route and every field these types decode is in the server's
answer - the part a compiler could not check anyway.

## Wiring it into the app

There is no Xcode project in this repository yet (`IOS_APP.md`: the
lock-screen audio spike comes first). When there is:

1. **File > Add Package Dependencies > Add Local...**, pick `ios/FAMSafety`.
2. Make one `SafetyAPI` with the server, `ios/<version>+<build>`, and the
   Keychain token; one `ConsentModel(api:)`; call `consent.load()` at launch.
3. Put `.aiConsentSheet(consent)` on the root view.
4. Before a search, a typed Go Deeper question, an attachment or the "where
   you live" tile: `guard await consent.ensure() else { return }`. Send
   `own=1` on `/api/v1/audio` for a Go Deeper question the listener typed
   (not FAM's suggestion kept as it was): the server holds it to the answer.
5. When `/api/v1/audio` answers `isConsentRefusal(response)`:
   `consent.forget()`, then `if await consent.ensure() { retry }` - the web's
   `onGenerationFailed` does exactly this.
6. Add **Report** to the ⋯ of a comment, a chat, a profile, a story vibe, the
   player and the Explore reel (`ReportSheet` with the matching
   `ReportSubject`); remove the item when the receipt says `hidden`.
7. Add **Block** to a profile, a chat and a story (`.blockConfirmation`).
8. Put `PrivacyAndSafetySection` in Settings.
