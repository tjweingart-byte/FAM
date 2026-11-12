import Foundation
import SwiftUI

/// myFAM: your mixes, the subject catalogue they draw on, public mixes, and
/// the playlist a mix plays as (§134). Shown to the listener as "myFAM";
/// in code and on the server these are DailyFAM mixes (`names-swapped`).
///
/// After the web's `screen-playfam`, `screen-mixdetail`, `screen-addtopics`
/// and `screen-publicmix` (PARITY.md). Where the web has a quirk the native
/// app does not copy, it says so: the page updates in place rather than
/// being pushed again, and "Remove from this playlist" sends a `remove`
/// operation rather than the whole list (§498). Delete is one tap and
/// Rename's field starts empty under the current name, as on the web.
@MainActor
final class MixesModel: ObservableObject {
    enum State: Equatable { case loading, ready, failed }

    @Published private(set) var mixes: [Mix] = []
    @Published private(set) var state: State = .loading
    @Published private(set) var subjects: [Subject] = []
    @Published private(set) var tagParent: [String: String] = [:]
    @Published private(set) var tagLabels: [String: String] = [:]
    @Published private(set) var recommended: RecommendedTopics?
    /// The shared bank's tags by topic id (`topicBank`): an older bank
    /// episode in a mix ranks Recommended topics by them (`itemTags`).
    @Published private(set) var bankTags: [String: [String]] = [:]
    @Published private(set) var push: PushStatus?
    /// Your own profile: the face on a mix that is yours alone, and the
    /// interests the local recommendation order starts from.
    @Published private(set) var me: ProfileSummary?
    /// The category chips' tree and what you follow (§500).
    @Published private(set) var categories: CategoriesPage?
    /// Which chip is on; every arrival on the tab opens on All.
    @Published var categoryState = CategoryState()
    @Published var categorySubs: [CategorySub] = []
    /// Which of myFAM's two tabs is on (§550, `playfamTab`): every arrival
    /// on the screen opens on Mixes.
    @Published var playfamTab: PlayfamTab = .mixes
    /// The morning's new mixes (§550, `freshMixes`): the server's word
    /// (`/api/mixes/fresh`) on which mixes this morning's edition wrote and
    /// nobody has played since, and the edition it is.
    @Published private(set) var freshIDs: [String] = []
    @Published private(set) var freshEdition = ""
    /// The edition whose dot was looked at (`fam.mixDotSeen`, the web's own
    /// key): kept on the phone, so the dot is back tomorrow and never twice
    /// today.
    @Published private(set) var mixDotSeen: String = UserDefaults.standard.string(forKey: "fam.mixDotSeen") ?? ""
    /// The myFAM screen itself is on show (`#screen-playfam.active`):
    /// looking at it is looking at the dot.
    var screenShowing: () -> Bool = { false }

    static let mixDotSeenKey = "fam.mixDotSeen"
    /// Asked every quarter hour while the app is in front (`startFreshMixChecks`).
    static let freshEvery: UInt64 = 15 * 60 * 1_000_000_000

    let api: FAMAPI
    var say: (String, String?, (() -> Void)?) -> Void = { _, _, _ in }

    init(api: FAMAPI) {
        self.api = api
    }

    func mix(_ id: String) -> Mix? { mixes.first { $0.id == id } }

    /// Signed out: nothing of the last account stays (its face included).
    func reset() {
        mixes = []
        state = .loading
        // The push state and the recommendations are the last account's
        // (`pushState = null`, 10.1 third set).
        push = nil
        recommended = nil
        bankTags = [:]
        me = nil
        categories = nil
        categoryState = CategoryState()
        categorySubs = []
        playfamTab = .mixes
        // The previous listener's new mix is not this one's.
        freshIDs = []
        freshEdition = ""
    }

    // MARK: - The morning's new mix (§550)

    /// The dot on the myFAM tab (`drawMixDot`): signed in, a mix new this
    /// morning, and this edition's dot not yet looked at.
    func showsMixDot(signedIn: Bool) -> Bool {
        signedIn && !freshIDs.isEmpty && !freshEdition.isEmpty && mixDotSeen != freshEdition
    }

    /// A mix's card says "New today" until it is played (`isFreshMix`).
    func isFresh(_ id: String) -> Bool { freshIDs.contains(id) }

    /// The tab was looked at (`clearMixDot`): this edition's dot is gone.
    func clearMixDot() {
        guard !freshEdition.isEmpty else { return }
        mixDotSeen = freshEdition
        UserDefaults.standard.set(freshEdition, forKey: Self.mixDotSeenKey)
    }

    /// Played is no longer new (`noteMixOpen(id, true)`).
    func notePlayed(_ id: String) {
        freshIDs.removeAll { $0 == id }
    }

    /// One ask (`checkFreshMixes`): a guest has none; any failure keeps
    /// what was drawn (a dot is never worth an error).
    func checkFreshMixes(signedIn: Bool) async {
        guard signedIn else {
            freshIDs = []
            freshEdition = ""
            return
        }
        guard let answer = try? await api.freshMixes() else { return }
        freshIDs = answer.fresh
        freshEdition = answer.edition
        // Looking at the tab right now is looking at it.
        if screenShowing() { clearMixDot() }
    }

    /// Asked when the app is in front - so on coming back to it too - and
    /// every quarter hour; the edition lands once a morning
    /// (`startFreshMixChecks`). Ends when the task is cancelled.
    func runFreshMixChecks(signedIn: @escaping @MainActor () -> Bool) async {
        while !Task.isCancelled {
            await checkFreshMixes(signedIn: signedIn())
            try? await Task.sleep(nanoseconds: Self.freshEvery)
        }
    }

    /// Every arrival on the screen (`openPlayFAM`): Mixes, no category
    /// left open, and the dot looked at.
    func arrived() {
        categoryState = CategoryState()
        playfamTab = .mixes
        clearMixDot()
    }

    /// Arriving on the tab (`loadMixes`): a failure looks like one - the
    /// sentence in place of the list, whatever was drawn before - because
    /// an old list would read as a fresh one.
    func load() async {
        Task { await loadCategories() }
        do {
            mixes = try await api.mixes()
            state = .ready
        } catch {
            state = .failed
        }
    }

    /// The mixes, read now, or the reason they could not be (Add to myFAM
    /// Playlist, which says so in a toast rather than an empty sheet).
    func loadOrThrow() async throws {
        mixes = try await api.mixes()
        state = .ready
    }

    /// Warmed a few seconds after DailyFAM's feed is on screen, before the
    /// tab is ever shown (a faster start): the mixes, then the categories,
    /// one request after the other - never beside anything else. The tab's
    /// arrival still asks for both afresh; this only means it draws at once.
    func warm() async {
        if let found = try? await api.mixes() {
            mixes = found
            state = .ready
        }
        if Task.isCancelled { return }
        await loadCategories()
    }

    /// Asked with every arrival on the tab; a failure keeps the last answer.
    func loadCategories() async {
        if let page = try? await api.categories() { categories = page }
    }

    func follows(_ id: String) -> Bool {
        categories?.mine.contains { $0.id == id } ?? false
    }

    /// Follow or unfollow one node: the whole list sent, then read back.
    func toggleFollow(_ id: String) async -> Bool {
        guard let page = categories else { return false }
        var ids = page.mine.map(\.id)
        let adding = !ids.contains(id)
        if adding { ids.append(id) } else { ids.removeAll { $0 == id } }
        do {
            try await api.followCategories(ids)
            await loadCategories()
            say(adding ? "Added to your categories" : "Removed from your categories", nil, nil)
            return true
        } catch {
            say(Self.sentence(error, else: "Could not save that"), nil, nil)
            return false
        }
    }

    /// The subject catalogue and the facets' names (`/api/preferences`,
    /// once): the mix pages read it, and DailyFAM's tuning dial names a
    /// tile's facet from it ("Money & markets", `dialWord`).
    func loadVocabulary() async {
        guard subjects.isEmpty, let page = try? await api.catalogue() else { return }
        subjects = page.catalogue
        tagParent = page.tagParent ?? [:]
        tagLabels = page.tagLabels ?? [:]
    }

    /// The catalogue, the recommended order and whether this server can
    /// push - asked once a mix page opens (`openMix`).
    func loadForPage() async {
        await loadVocabulary()
        if let rec = try? await api.recommendedTopics() { recommended = rec }
        if bankTags.isEmpty, let bank = try? await api.topicBank() {
            var tags: [String: [String]] = [:]
            for t in bank { tags[t.id] = t.tags ?? [] }
            bankTags = tags
        }
        if push == nil { push = try? await api.pushStatus() }
        if me == nil, let found = try? await api.profile() { me = found }
    }

    /// The face a mix with nobody else in it shows (`mixOwnerFace`): your
    /// own name, handle and picture as your profile has them - nil when
    /// none is known, so nothing is drawn rather than something invented.
    func ownFace(fallbackName: String?) -> MixPerson? {
        let name = (me?.name ?? "").isEmpty ? (fallbackName ?? "") : (me?.name ?? "")
        let handle = me?.handle ?? ""
        if name.isEmpty && handle.isEmpty { return nil }
        return MixPerson(name: name, handle: handle, avatar: me?.avatar, owner: true, you: nil)
    }

    /// The server's answer replaces the local copy, by id (`saveMix`).
    func keep(_ mix: Mix) {
        if let i = mixes.firstIndex(where: { $0.id == mix.id }) { mixes[i] = mix } else { mixes.append(mix) }
    }

    func drop(_ id: String) {
        mixes.removeAll { $0.id == id }
    }

    /// Any write to a mix: the answer kept, the server's sentence said on a no.
    @discardableResult
    func update(_ id: String, _ body: [String: Any]) async -> Mix? {
        do {
            let mix = try await api.updateMix(id, body)
            keep(mix)
            return mix
        } catch {
            say(Self.sentence(error, else: "Could not save that mix"), nil, nil)
            return nil
        }
    }

    static func sentence(_ error: Error, else fallback: String) -> String {
        if case let FAMError.refused(_, message) = error, !message.isEmpty { return message }
        if case let FAMError.signedOut(message) = error, !message.isEmpty { return message }
        return fallback
    }

    // MARK: - Subjects

    func subject(_ id: String) -> Subject? { subjects.first { $0.id == id } }

    /// Add topics' suggestions (`renderAddTopics`): "{Facet} · new
    /// briefing every day", "Topic · new briefing every day" with none.
    func addTopicsSub(_ s: Subject) -> String {
        (facetLabel(s) ?? "Topic") + " · new briefing every day"
    }

    /// "{Facet} · new briefing every day", or just the second half.
    func subjectSub(_ s: Subject) -> String {
        if let facet = facetLabel(s) { return "\(facet) · new briefing every day" }
        return "new briefing every day"
    }

    func facetLabel(_ s: Subject) -> String? {
        guard let tag = s.tags?.first else { return nil }
        var top = tag
        var seen = 0
        while let up = tagParent[top], !up.isEmpty, seen < 8 { top = up; seen += 1 }
        return tagLabels[top] ?? tagLabels[tag]
    }

    /// The base subject of an item id (`f:nfl~Eagles` → `nfl`).
    static func base(_ id: String) -> String {
        guard id.hasPrefix("f:") else { return id }
        let rest = id.dropFirst(2)
        return String(rest.split(separator: "~", maxSplits: 1).first ?? "")
    }

    /// The order to offer subjects in: the server's when personalised,
    /// else the catalogue in the listener's interest order, re-ranked by
    /// facet overlap with what the mix already holds (`recRanked`).
    /// Subjects already in the mix are left out when `excludingMix`.
    func recommendations(for mix: Mix?, excludingMix: Bool) -> [Subject] {
        let inMix = Set((mix?.items ?? []).map { Self.base($0.id) })
        var ordered: [Subject]
        if let rec = recommended, rec.personalised {
            // The server's order only (`recRanked`): what it does not rank
            // is not recommended.
            let byId = Dictionary(subjects.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
            var named: Set<String> = []
            ordered = []
            for id in rec.order {
                guard let s = byId[Self.base(id)], !named.contains(s.id) else { continue }
                named.insert(s.id)
                ordered.append(s)
            }
        } else {
            ordered = byMixFacets(followRanked(), mix: mix)
        }
        return excludingMix ? ordered.filter { !inMix.contains($0.id) } : ordered
    }

    /// A subject's tags and the parent of each (`facetsOf`).
    private func facets(_ tags: [String]) -> Set<String> {
        var out: Set<String> = []
        for g in tags {
            out.insert(g)
            if let up = tagParent[g], !up.isEmpty { out.insert(up) }
        }
        return out
    }

    /// The catalogue in interest order (`followRanked`): a chosen subject
    /// first, then subjects under a chosen facet. Interests are your
    /// profile's (`interests_shown`, first eight), else every facet.
    private func followRanked() -> [Subject] {
        var want: [String: Int] = [:]
        let shown = Array((me?.interestsShown ?? []).prefix(8))
        if shown.isEmpty {
            for facet in tagLabels.keys { want[facet] = 2 }
        } else {
            for row in shown {
                if row.kind == "topic" {
                    if let c = subject(row.id) {
                        want["id:" + c.id] = 3
                        for g in c.tags ?? [] { want[g] = max(want[g] ?? 0, 1) }
                    }
                } else {
                    want[row.id] = 2
                }
            }
        }
        var scored: [(subject: Subject, score: Int, index: Int)] = []
        for (index, c) in subjects.enumerated() {
            var score = want["id:" + c.id] ?? 0
            for k in facets(c.tags ?? []) { score = max(score, want[k] ?? 0) }
            scored.append((subject: c, score: score, index: index))
        }
        scored.sort { a, b in a.score != b.score ? a.score > b.score : a.index < b.index }
        return scored.map { $0.subject }
    }

    /// The catalogue in the listener's interest order (`followRanked`),
    /// for Add topics' teams and topics.
    func interestOrder() -> [Subject] { followRanked() }

    /// Tags for anything that can sit in a mix (`itemTags`): a followed
    /// subject's, an older bank episode's, or none for a typed topic.
    private func itemTags(_ id: String) -> [String] {
        let base = Self.base(id)
        if id.hasPrefix("f:"), let c = subject(base) { return c.tags ?? [] }
        return bankTags[base] ?? []
    }

    /// Re-ranked by how many of the mix's items share each facet.
    private func byMixFacets(_ ranked: [Subject], mix: Mix?) -> [Subject] {
        var want: [String: Int] = [:]
        for item in mix?.items ?? [] {
            let tags = itemTags(item.id)
            for k in facets(tags) { want[k] = (want[k] ?? 0) + 1 }
        }
        if want.isEmpty { return ranked }
        var scored: [(subject: Subject, score: Int, index: Int)] = []
        for (index, c) in ranked.enumerated() {
            var score = 0
            for k in facets(c.tags ?? []) { score += want[k] ?? 0 }
            scored.append((subject: c, score: score, index: index))
        }
        scored.sort { a, b in a.score != b.score ? a.score > b.score : a.index < b.index }
        return scored.map { $0.subject }
    }

    // MARK: - People

    /// The people line under a mix's name (`mixPeopleLine`).
    static func peopleLabel(_ people: [MixPerson]) -> String {
        func name(_ p: MixPerson, lower: Bool) -> String {
            if p.you == true { return lower ? "you" : "You" }
            if let n = p.name, !n.isEmpty { return n }
            if let h = p.handle, !h.isEmpty { return "@" + h }
            return ""
        }
        guard let first = people.first else { return "" }
        if people.count == 1 { return name(first, lower: false) }
        if people.count == 2 { return "\(name(first, lower: false)) and \(name(people[1], lower: true))" }
        return "\(name(first, lower: false)) + \(people.count - 1)"
    }

    /// "joint · Sam's" / "joint · with 2 others" (`jointLabel`).
    static func jointLabel(_ m: Mix) -> String {
        if !m.isOwner {
            let owner = m.people.first { $0.owner == true }
            let who = owner?.name.flatMap { $0.isEmpty ? nil : $0 }
                ?? owner?.handle.flatMap { $0.isEmpty ? nil : "@" + $0 } ?? "another listener"
            return "joint · \(who)’s"
        }
        let others = m.people.filter { $0.owner != true && $0.you != true }.count
        if others == 0 { return "joint" }
        return others == 1 ? "joint · with 1 other" : "joint · with \(others) others"
    }

    static func briefings(_ n: Int) -> String { n == 1 ? "1 briefing" : "\(n) briefings" }

    /// 24-hour "HH:MM" as the web shows it: "7:05 AM".
    static func timeLabel(_ hhmm: String) -> String {
        let parts = hhmm.split(separator: ":").compactMap { Int($0) }
        guard parts.count == 2 else { return hhmm }
        let h = parts[0] % 12 == 0 ? 12 : parts[0] % 12
        return String(format: "%d:%02d %@", h, parts[1], parts[0] < 12 ? "AM" : "PM")
    }
}

/// myFAM's two tabs (§550, `showPlayfamTab`): the mixes, and the
/// categories that used to be a chip row over them.
enum PlayfamTab: Equatable {
    case mixes
    case categories
}

/// A mix playing as a playlist (`mixPlay`): which item, which have played.
struct MixPlaylist: Equatable {
    var mixID: String
    var name: String
    var items: [MixItem]
    /// Indexes into `items`, in the order they play.
    var order: [Int]
    var pos: Int
    var failures = 0
    /// Items whose first audio arrived (the glide's "Played").
    var played: Set<Int> = []
    /// Somebody else's public mix: not yours to edit, not yours to note.
    var mine: Bool
    /// The episode the playlist is on (`mixPlay.key`): a fresh key for each
    /// item played, carried by that episode (`Episode.mixKey`) and by any Go
    /// Deeper detour off it (`Episode.mixDetour`, §519).
    var key: String = ""
    /// The playlist's episode a Go Deeper interrupted part way, to resume
    /// where it was once the detour is over (the owner's call, differing
    /// from the web's §519, which moves on to the next episode).
    var interrupted: InterruptedItem?
    /// The episode key already taken out of its mix (`mixPlay.removedKey`):
    /// a second press must not take the one before it.
    var removedKey: String = ""
    /// Nil while `pos` is -1: the item playing was taken out of the
    /// playlist and the next one is still to come.
    var current: MixItem? { pos >= 0 && pos < order.count ? items[order[pos]] : nil }

    /// "Thursday, November 12, 2026", for `{date}` (the device's clock).
    static func today() -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US")
        f.dateFormat = "EEEE, MMMM d, yyyy"
        return f.string(from: Date())
    }

    /// The words an item is asked for as today's edition (`playMixItem`).
    static func prompt(_ item: MixItem) -> (words: String, dated: Bool) {
        if !item.prompt.isEmpty && !item.dailyPrompt.isEmpty { return (item.prompt, true) }
        if !item.dailyPrompt.isEmpty {
            return (item.dailyPrompt.replacingOccurrences(of: "{date}", with: today()), true)
        }
        return (item.query, false)
    }
}

/// The playlist's episode as a detour left it: the episode, the id of the
/// audio heard (`X-FAM-Episode`, so the same audio replays, never rewritten)
/// and the position to seek back to.
struct InterruptedItem: Equatable {
    var episode: Episode
    var episodeID: String
    var position: Double
}
