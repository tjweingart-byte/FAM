import FAMSafety
import Foundation
import SwiftUI
import UIKit

/// The screens pushed on the YourFAM tab (`navigate` on the web): Settings,
/// Save for Later, the listening history, My Vibe, and - iOS only - the
/// sign-in methods. Edit profile, Where you are and the interests editor
/// are never pushed: they come up over whatever opened them and go back
/// there (`intro-not-on-stack`, quirk Q3 not copied).
enum YouRoute: Hashable {
    case settings, saved, history, myVibe, signInMethods
    /// One of the hub's interest chips (`openInterestAt`).
    case topic(TopicOpen)
    /// A vibe tapped on your own grid: your vibes as posts (§547).
    case vibePosts(VibePostsOpen)
}

/// The steps after an account is made or signed in (`afterAccount`): who
/// you are, then your interests. `checking` while the server is asked.
enum EntryStep: Equatable {
    case checking, identity, interests
    /// Let in off the waitlist with the email alone (§604): a password
    /// first, before who you are (`password_needed`).
    case password
}

/// What YourFAM knows about the listener: the hub's profile, the
/// preferences Settings edits, the account's details, and the small
/// answers Settings shows on its rows. Everything is the server's; this
/// holds the last answer and asks again when it may have changed.
@MainActor
final class YouModel: ObservableObject {
    @Published private(set) var profile: MyProfile?
    @Published private(set) var profileState: LoadState = .idle
    @Published private(set) var prefs: MyPreferences?
    @Published private(set) var account: AccountInfo?
    @Published private(set) var activation: Activation?
    /// The plan's label, or nil until `/api/plans` answers (the web's "—").
    @Published private(set) var planLabel: String?
    @Published private(set) var voices: VoicesPage?
    /// nil while loading (the row shows nothing), else the count.
    @Published private(set) var blockedCount: Int?
    /// Vibes (0) or Mixes (1) on the hub; kept across redraws (`profileShelf`).
    @Published var shelfTab = 0

    let api: FAMAPI
    let safety: SafetyAPI
    /// A toast or a snack: the words, an action's label and what it does.
    var say: (String, String?, (() -> Void)?) -> Void = { _, _, _ in }
    /// A vibe was deleted whole: the faces row and the chat's (+) re-read.
    var vibeDeleted: () -> Void = {}
    /// The name, handle or picture changed: whoever shows them re-reads.
    var meChanged: () -> Void = {}
    /// The preferences were read: private search follows the stored switch.
    var prefsLoaded: (MyPreferences) -> Void = { _ in }

    init(api: FAMAPI, safety: SafetyAPI) {
        self.api = api
        self.safety = safety
    }

    /// Signing out forgets everything about the listener.
    func reset() {
        profile = nil
        profileState = .idle
        prefs = nil
        account = nil
        activation = nil
        planLabel = nil
        voices = nil
        blockedCount = nil
        shelfTab = 0
        AvatarSource.clear()
    }

    // MARK: - Loading

    /// `loadProfile`: nothing is cleared first - a revisit keeps the old
    /// page until the new one lands; a first open is blank until then.
    func loadProfile() async {
        if profile == nil { profileState = .loading }
        do {
            profile = try await api.myProfile()
            profileState = .loaded
        } catch {
            if profile == nil { profileState = .failed }
        }
    }

    func loadPrefs() async {
        if let found = try? await api.preferences() {
            prefs = found
            prefsLoaded(found)
        }
    }

    func loadAccount() async {
        if let found = try? await api.account() { account = found }
    }

    /// Everything Settings' rows show, asked together; a failure leaves a
    /// row's value as it was, never a made-up one.
    func loadSettings() async {
        async let p: Void = loadPrefs()
        async let a: Void = loadAccount()
        async let r: Void = loadSmallAnswers()
        _ = await (p, a, r)
    }

    private func loadSmallAnswers() async {
        if let page = try? await api.plans() {
            let current = page.current ?? "free"
            let tier = page.tiers.first { $0.name == current }
            planLabel = tier?.label ?? tier?.name ?? current
        }
        if let found = try? await api.activation() { activation = found }
        if let found = try? await api.voices() { voices = found }
        if let people = try? await safety.blocked() { blockedCount = people.count }
    }

    /// The voice bank again, for Settings' "Voice for your searches" tapped
    /// before it had loaded (`openVoiceMenu('settings')`).
    func reloadVoices() async {
        if let found = try? await api.voices() { voices = found }
    }

    func loadActivation() async -> Activation? {
        if let found = try? await api.activation() {
            activation = found
            return found
        }
        return nil
    }

    func refreshBlocked() async {
        if let people = try? await safety.blocked() { blockedCount = people.count }
    }

    // MARK: - What the rows say

    /// The place: the preferences' label, else the profile's - never "Not
    /// set" while the hero shows one (Q13).
    var placeLabel: String {
        if let label = prefs?.location?.label, !label.isEmpty { return label }
        if let label = profile?.location, !label.isEmpty { return label }
        return "Not set"
    }

    var name: String { profile?.name ?? "" }
    var handle: String { profile?.handle ?? "" }
    var avatar: String { profile?.avatar ?? "" }

    /// "Formula 1, Science, Jazz +2", or "None chosen" (`chosenSummary`).
    var interestsSummary: String {
        let labels = facetLabels
        var names: [String] = (prefs?.interests ?? []).map { labels[$0] ?? $0 }
        for t in prefs?.topicsChosen ?? [] { names.append(t.label ?? t.id) }
        if names.isEmpty { return "None chosen" }
        let shown = names.prefix(3).joined(separator: ", ")
        return names.count > 3 ? shown + " +\(names.count - 3)" : shown
    }

    var facetLabels: [String: String] {
        var out: [String: String] = [:]
        for f in prefs?.interestsAll ?? [] { out[f.id] = f.label ?? f.id }
        return out
    }

    // MARK: - Writes

    /// Delete a vibe whole (`DELETE /api/vibe`): off the grid, the story,
    /// My Vibe and its folder. Spliced here at once on success, then the
    /// profile is read again quietly.
    func deleteVibe(query: String, minutes: Double) async -> Bool {
        do {
            try await api.deleteVibe(query: query, minutes: minutes)
        } catch {
            return false
        }
        await loadProfile()
        vibeDeleted()
        return true
    }

    /// `saveAvatar`: `/api/me` writes all three, so the name and handle ride
    /// along. Returns the refusal's sentence, or nil once saved.
    func saveAvatar(_ avatar: String, name: String, handle: String) async -> String? {
        do {
            try await api.setMe(name: name, handle: handle, avatar: avatar)
        } catch {
            return SocialModel.sentence(error, else: "Could not save that picture")
        }
        await loadProfile()
        meChanged()
        return nil
    }

    /// A setting written to the server (`savePreferences`); the answer is
    /// kept beside what the GET said, never in place of it.
    func savePreference(_ body: [String: Any]) async -> Bool {
        do {
            try await api.savePreferences(body)
        } catch {
            return false
        }
        await loadPrefs()
        return true
    }
}

/// The original of the picture last chosen here, so "Move and scale" can
/// cut it again (`fam.avatar.source`): on this phone only, at most 1024 px.
enum AvatarSource {
    private static var file: URL? {
        FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first?
            .appendingPathComponent("fam-avatar-source.jpg")
    }

    static func keep(_ image: UIImage) {
        guard let file, let data = image.jpegData(compressionQuality: 0.9) else { return }
        try? data.write(to: file, options: .atomic)
    }

    static func load() -> UIImage? {
        guard let file, let data = try? Data(contentsOf: file) else { return nil }
        return UIImage(data: data)
    }

    static var exists: Bool { load() != nil }

    static func clear() {
        guard let file else { return }
        try? FileManager.default.removeItem(at: file)
    }

    /// Read a chosen file, upright and at most 1024 px on its long edge
    /// (`downscale` before the editor).
    static func prepare(_ data: Data) -> UIImage? {
        guard let source = UIImage(data: data), source.size.width > 0, source.size.height > 0 else {
            return nil
        }
        let long = max(source.size.width, source.size.height)
        let scale = min(1, 1024 / long)
        let size = CGSize(width: (source.size.width * scale).rounded(),
                          height: (source.size.height * scale).rounded())
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        let renderer = UIGraphicsImageRenderer(size: size, format: format)
        return renderer.image { _ in
            source.draw(in: CGRect(origin: .zero, size: size))
        }
    }
}

// MARK: - The app model's YourFAM side

extension AppModel {
    /// "Saved for Later · Open" (`openSaved`): the shelf, from anywhere - the
    /// player and any cover over the tabs are taken down first.
    func openSaved() {
        var wait = false
        if showPlayer {
            showPlayer = false
            wait = true
        }
        if social.cover != nil {
            social.cover = nil
            wait = true
        }
        let go = { [weak self] in
            guard let self else { return }
            self.tabRequest = .you
            self.youPath = [.saved]
            Task { await self.you.loadProfile() }
        }
        if wait {
            Task { @MainActor in
                try? await Task.sleep(nanoseconds: 600_000_000)
                go()
            }
        } else {
            go()
        }
    }

    /// A saved episode (`playSaved`): a pointer, so it plays by its question
    /// and length, and the shelf is told it was played.
    func playSaved(_ item: SavedItem) {
        let title = (item.title ?? "").isEmpty ? item.query : (item.title ?? "")
        // The web's `playSaved` plays it by setting the chosen length
        // (`selectedLengthMinutes = item.minutes`), so searchFAM's Length
        // follows it until the next return to the app puts it back to 1.5.
        if item.minutes > 0 { searchLength = item.minutes }
        play(Episode(key: EpisodeKey(query: item.query, minutes: item.minutes),
                     title: title, surface: "other", titleOverridden: false))
        if let id = item.id, !id.isEmpty {
            let api = self.api
            Task { await api.playedSaved(id) }
        }
    }

    /// A history row (`playHistory`): the episode that was heard, by its id
    /// (§173) - never written again; under the tab it was first heard on.
    func playHistory(_ item: HistoryItem) {
        let title = item.title ?? ""
        var ep = Episode(key: EpisodeKey(query: item.query, minutes: item.minutes, context: item.context ?? ""),
                         title: title.isEmpty ? item.query : title,
                         surface: item.surface ?? "other",
                         titleOverridden: !title.isEmpty)
        ep.episodeID = item.episode ?? ""
        play(ep)
    }

    /// One of your own vibes (`playOwnVibe`): by its question and length.
    func playOwnVibe(query: String, minutes: Double?, title: String?) {
        let words = title ?? ""
        play(Episode(key: EpisodeKey(query: query, minutes: minutes ?? FAMAPI.browseMinutes),
                     title: words.isEmpty ? query : words, surface: "other"))
    }

    /// After a sign-up or a sign-in (`afterAccount`): the interests already
    /// done - straight in; who they are already set (a name, a handle and a
    /// place) - the interests; otherwise who they are first. Any failure
    /// lets them in rather than holding the app shut. Going straight in
    /// hands over to DailyFAM with the FAM intro (`finishEntry`), except
    /// from the app's own opening, where the intro has already played
    /// (`afterAccount({fromBoot: true})`).
    func beginEntry(handoff: Bool = true) async {
        guard stage == .ready else { return }
        // A password first (§604), before anything the first run asks.
        if listener?.passwordNeeded == true {
            entry = .password
            return
        }
        entry = .checking
        guard let prefs = try? await api.preferences() else {
            entry = nil
            if handoff { playIntro() }
            return
        }
        if prefs.introDone == true {
            UserDefaults.standard.set(true, forKey: AppModel.entryKey)
            entry = nil
            if handoff { playIntro() }
            return
        }
        let profile = try? await api.myProfile()
        let place = prefs.location
        let hasPlace = !(place?.city ?? "").isEmpty || !(place?.region ?? "").isEmpty
            || !(place?.country ?? "").isEmpty
        let named = !(profile?.name ?? "").isEmpty && !(profile?.handle ?? "").isEmpty
        entry = (named && hasPlace) ? .interests : .identity
    }

    /// The first run finished or skipped (`finishEntry`): into DailyFAM.
    func finishEntry() {
        // This phone has finished the first run (`entry: "done"`), and the
        // wordmark hands them into DailyFAM (`beginEntryHandoff`, §201).
        UserDefaults.standard.set(true, forKey: AppModel.entryKey)
        playIntro()
        // The tabs are drawn afresh, on DailyFAM.
        entry = nil
        Task { await loadRails() }
    }

    /// The account is gone (an under-13 date confirmed, §411): forget it all.
    func accountErased() {
        player.stop()
        api.token = nil
        forgetEverything()
    }
}

/// The objects every FAM screen reads, for a screen presented over others.
extension View {
    func famObjects(_ model: AppModel) -> some View {
        self.environmentObject(model)
            .environmentObject(model.player)
            .environmentObject(model.nowPlaying)
            .environmentObject(model.mixes)
            .environmentObject(model.social)
            .environmentObject(model.you)
    }
}
