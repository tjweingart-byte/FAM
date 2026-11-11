import BackgroundTasks
import Foundation

/// **The remembered DailyFAM page, kept fresh while the app is closed**
/// (iOS only, §544; the owner asked for a faster first open). iOS now and
/// then wakes the app in the background (`BGAppRefreshTask`, registered in
/// `FAMApp` with `.backgroundTask(.appRefresh(...))`); it asks for the page
/// exactly as the app does (`FAMAPI.railsData()`: the same `/api/v1/myfam`
/// query, the Keychain's bearer token, the read pace's one patient retry)
/// and keeps it with the same `rememberFeed` a successful draw uses, so the
/// next open draws a recent page at once (`showRememberedFeed`).
///
/// Only for an account this phone last saw let in (`AppModel.readyKey`)
/// with a token in the Keychain; never signed out (a sign-out cancels the
/// next request too). One read and nothing else: nothing on the audio
/// path, no episode written, no event. A failure - refused, offline,
/// unreadable - keeps nothing, and the last page stays as it was.
///
/// On the main actor, beside `AppModel` and the shelf; the request itself
/// runs wherever URLSession runs it.
@MainActor
enum FeedRefresh {
    /// Listed under `BGTaskSchedulerPermittedIdentifiers` (project.yml).
    static let identifier = "net.familiarize.fam.feed"
    /// The earliest the next one may run; iOS decides when it does.
    static let every: TimeInterval = 60 * 60

    /// A token and an account last let in.
    static var allowed: Bool {
        guard UserDefaults.standard.bool(forKey: AppModel.readyKey) else { return false }
        guard let token = Keychain.read("session"), !token.isEmpty else { return false }
        return true
    }

    /// Ask iOS for the next one, about an hour from now: when the app goes
    /// to the background and after every run. Nothing when not allowed.
    static func schedule() {
        guard allowed else { return }
        let request = BGAppRefreshTaskRequest(identifier: identifier)
        request.earliestBeginDate = Date(timeIntervalSinceNow: every)
        do {
            try BGTaskScheduler.shared.submit(request)
        } catch {
            // The simulator, Background App Refresh off, or too many
            // requests: the app simply opens on the page it last kept.
        }
    }

    /// Signed out: no page is fetched for nobody.
    static func cancel() {
        BGTaskScheduler.shared.cancel(taskRequestWithIdentifier: identifier)
    }

    /// One run: the next one asked for first, then the page.
    static func run() async {
        schedule()
        guard allowed else { return }
        let data: Data
        do {
            data = try await FAMAPI().railsData()
        } catch {
            return
        }
        // Kept only if it would draw, as the app keeps only a page that drew.
        guard (try? FAMAPI.decodeRails(data)) != nil else { return }
        await keep(data)
    }

    /// Asked again, so a sign-out while the page was on its way keeps
    /// nothing.
    private static func keep(_ data: Data) async {
        guard allowed else { return }
        let shelf = OfflineShelf()
        shelf.rememberFeed(data)
        await shelf.settled()
    }
}
