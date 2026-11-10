import Foundation
import SwiftUI

/// myFAM: your mixes, the subject catalogue they draw on, public mixes, and
/// the playlist a mix plays as (§134). Shown to the listener as "myFAM";
/// in code and on the server these are DailyFAM mixes (`names-swapped`).
///
/// After the web's `screen-playfam`, `screen-mixdetail`, `screen-addtopics`
/// and `screen-publicmix` (PARITY.md). Where the web has a quirk the native
/// app does not copy, it says so: the page updates in place rather than
/// being pushed again, Delete asks first, Rename starts from the current
/// name, and "Remove from this playlist" sends a `remove` operation rather
/// than the whole list (§498).
@MainActor
final class MixesModel: ObservableObject {
    enum State: Equatable { case loading, ready, failed }

    @Published private(set) var mixes: [Mix] = []
    @Published private(set) var state: State = .loading
    @Published private(set) var subjects: [Subject] = []
    @Published private(set) var tagParent: [String: String] = [:]
    @Published private(set) var tagLabels: [String: String] = [:]
    @Published private(set) var recommended: RecommendedTopics?
    @Published private(set) var push: PushStatus?
    /// The category chips' tree and what you follow (§500).
    @Published private(set) var categories: CategoriesPage?
    /// Which chip is on; every arrival on the tab opens on All.
    @Published var categoryState = CategoryState()
    @Published var categorySubs: [CategorySub] = []

    let api: FAMAPI
    var say: (String, String?, (() -> Void)?) -> Void = { _, _, _ in }

    init(api: FAMAPI) {
        self.api = api
    }

    func mix(_ id: String) -> Mix? { mixes.first { $0.id == id } }

    /// Arriving on the tab (`loadMixes`).
    func load() async {
        Task { await loadCategories() }
        do {
            mixes = try await api.mixes()
            state = .ready
        } catch {
            if mixes.isEmpty { state = .failed }
        }
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

    /// The catalogue, the recommended order and whether this server can
    /// push - asked once a mix page opens (`openMix`).
    func loadForPage() async {
        if subjects.isEmpty, let page = try? await api.catalogue() {
            subjects = page.catalogue
            tagParent = page.tagParent ?? [:]
            tagLabels = page.tagLabels ?? [:]
        }
        if let rec = try? await api.recommendedTopics() { recommended = rec }
        if push == nil { push = try? await api.pushStatus() }
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

    /// The order to offer subjects in: the server's when personalised, else
    /// the catalogue's own, nudged toward what the mix already holds.
    /// Subjects already in the mix are left out when `excludingMix`.
    func recommendations(for mix: Mix?, excludingMix: Bool) -> [Subject] {
        let inMix = Set((mix?.items ?? []).map { Self.base($0.id) })
        var ordered: [Subject]
        if let rec = recommended, rec.personalised {
            let byId = Dictionary(subjects.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
            ordered = rec.order.compactMap { byId[Self.base($0)] }
            let named = Set(ordered.map(\.id))
            ordered += subjects.filter { !named.contains($0.id) }
        } else {
            let mixTags = Set((mix?.items ?? []).compactMap { subject(Self.base($0.id))?.tags?.first })
            ordered = subjects.enumerated().sorted { a, b in
                let sa = mixTags.contains(a.element.tags?.first ?? "") ? 1 : 0
                let sb = mixTags.contains(b.element.tags?.first ?? "") ? 1 : 0
                return sa != sb ? sa > sb : a.offset < b.offset
            }.map(\.element)
        }
        return excludingMix ? ordered.filter { !inMix.contains($0.id) } : ordered
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

    var current: MixItem? { pos < order.count ? items[order[pos]] : nil }

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
