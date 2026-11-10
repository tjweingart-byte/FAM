import Foundation
import SwiftUI

// Messages, Friends and a friend's profile, ported from the web
// (`static/index.html`: `renderThreadList`, `openThreadWith`,
// `checkNewFollowers`, the notifications poll, `openPersonProfile`).
//
// `op-social-live`: the message cursor is the server's `head` - a row id,
// never a timestamp - and it belongs to the client; `bootstrap` announces
// nothing; a send draws at once. `op-follow-graph`: follows are one-way and
// a friend is a mutual follow, derived. `listener-id-server`: the only ids
// sent are other people's, as the server handed them out.

/// Where a social tap leads. Hashable, so a NavigationStack can hold it.
enum SocialRoute: Hashable {
    case thread(ChatOpen)
    case person(PersonOpen)
    case friends
}

/// What the caller knew about a conversation when it opened it: the header
/// draws from this at once, then from the server's answer (`openThreadWith`).
struct ChatOpen: Hashable {
    /// Their listener id, or a group's `g:...`.
    var with: String
    var name: String = ""
    var handle: String = ""
    var avatar: String = ""
    var explicit: Bool = false
    var group: Bool = false
    var named: Bool = false
    var members: [ChatMember] = []
    /// "You’re not friends with …" for a group with strangers in it (§523).
    var warning: String = ""
    /// False until the server has answered once - a group opened from a
    /// banner has no members yet (quirk 16), so its sub line waits.
    var loaded: Bool = false
}

/// Who a profile is for. `/api/person` never returns an id, so the id is
/// whatever the follow graph said (`op-friend-profile`).
struct PersonOpen: Hashable {
    var handle: String
    var userID: String = ""
    var name: String = ""
    var avatar: String = ""
}

/// A social screen shown over another tab, with its own stack.
struct SocialCover: Identifiable {
    let id = UUID()
    let root: SocialRoute
}

/// The in-app drop-down (`#notifBanner`).
struct SocialBanner: Identifiable, Equatable {
    let id = UUID()
    let title: String
    let text: String
    let name: String
    let handle: String
    let avatar: String
    /// A message opens its conversation; a follow opens Friends.
    let chat: ChatOpen?
}

enum LoadState: Equatable {
    case idle, loading, loaded, failed
}

@MainActor
final class SocialModel: ObservableObject {
    /// `NOTIF_POLL_MS`, `BANNER_MS`, `BANNER_GAP_MS`.
    static let pollSeconds: Double = 3
    static let bannerSeconds: Double = 3
    static let bannerGap: Double = 0.42

    let api: FAMAPI
    /// The app's toast (`nowPlaying.say`).
    var say: (String) -> Void = { _ in }
    /// Ask the tab bar to show a tab.
    var requestTab: (Tab) -> Void = { _ in }
    /// Close the full-screen player if it is up; true when it was. A
    /// banner is drawn over the player too, and its tap must not open a
    /// conversation underneath it.
    var leavePlayer: () -> Bool = { false }

    // The inbox (`inboxThreads`) and the Messages badge.
    @Published private(set) var threads: [InboxThread] = []
    @Published private(set) var inboxState: LoadState = .idle
    @Published private(set) var unread = 0

    // The follow graph (`/api/friends`).
    @Published private(set) var graph: FriendsPage?
    @Published private(set) var graphState: LoadState = .idle

    /// Your own profile: your handle (invites), the interests you show
    /// ("N in common") and your vibes (a chat's (+)).
    @Published private(set) var me: ProfileSummary?

    // Where the social screens are: the Messages tab's stack, or a cover
    // over whichever tab a face or a row was tapped on.
    @Published var messagesPath: [SocialRoute] = []
    @Published var cover: SocialCover?
    @Published var coverPath: [SocialRoute] = []

    // The drop-down banner and the "started following you" popup.
    @Published private(set) var banner: SocialBanner?
    @Published var follower: FollowPerson?

    /// Set by the tab bar: whether Messages is the tab on screen. Published:
    /// a conversation left open under another tab stops polling (and stops
    /// marking itself read, and swallowing its banners) until it is back.
    @Published var onMessagesTab = false
    /// The conversation on screen, whose messages raise no banner.
    var chatOnScreen: String?

    private var cursor = 0
    private var bootstrapped = false
    private var bannerQueue: [SocialBanner] = []
    private var bannerTask: Task<Void, Never>?
    private var bannerHeld = false
    /// Followers already raised this session, by popup or banner.
    private var raised: Set<String> = []

    init(api: FAMAPI) {
        self.api = api
    }

    /// Signing out forgets every conversation, the graph and the cursor.
    func reset() {
        threads = []
        inboxState = .idle
        unread = 0
        graph = nil
        graphState = .idle
        me = nil
        messagesPath = []
        cover = nil
        coverPath = []
        banner = nil
        follower = nil
        cursor = 0
        bootstrapped = false
        bannerQueue = []
        bannerTask?.cancel()
        raised = []
    }

    // MARK: - Navigation

    /// Open a social screen where the listener is: pushed on the stack on
    /// screen, else over the current tab with a stack of its own.
    func open(_ route: SocialRoute) {
        if cover != nil {
            coverPath.append(route)
        } else if onMessagesTab {
            messagesPath.append(route)
        } else {
            coverPath = []
            cover = SocialCover(root: route)
        }
    }

    /// Open after a sheet or a cover has finished closing: a second
    /// presentation started while the first is still leaving is dropped.
    func openAfterClose(_ route: SocialRoute) {
        Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 600_000_000)
            self?.open(route)
        }
    }

    /// The server's own sentence (`detail` or `error`), else the fallback.
    nonisolated static func sentence(_ error: Error, else fallback: String) -> String {
        if let e = error as? FAMError {
            switch e {
            case let .refused(_, message) where !message.isEmpty: return message
            case let .signedOut(message) where !message.isEmpty: return message
            case let .waitlisted(message) where !message.isEmpty: return message
            default: break
            }
        }
        return fallback
    }

    /// Something that plays or leaves for another tab: the loading screen,
    /// the player and the tabs are drawn under a cover, so it closes first.
    func closeCover(_ then: @escaping () -> Void) {
        guard cover != nil else {
            then()
            return
        }
        cover = nil
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 600_000_000)
            then()
        }
    }

    /// Back from whichever social screen is on top.
    func back() {
        if cover != nil {
            if coverPath.isEmpty { cover = nil } else { coverPath.removeLast() }
        } else if !messagesPath.isEmpty {
            messagesPath.removeLast()
        }
    }

    /// A profile by handle (no handle: "They have not chosen a handle yet").
    func openPerson(handle: String, userID: String = "", name: String = "", avatar: String = "") {
        let h = handle.hasPrefix("@") ? String(handle.dropFirst()) : handle
        guard !h.isEmpty || !userID.isEmpty else {
            say("They have not chosen a handle yet")
            return
        }
        var id = userID
        if id.isEmpty, let known = person(handle: h) { id = known.userId }
        open(.person(PersonOpen(handle: h, userID: id, name: name, avatar: avatar)))
    }

    /// A conversation with somebody in the graph or a search.
    static func chat(with p: FollowPerson) -> ChatOpen {
        ChatOpen(with: p.userId, name: p.name ?? "", handle: p.handle ?? "", avatar: p.avatar ?? "",
                 explicit: p.explicit ?? false)
    }

    static func chat(from t: InboxThread) -> ChatOpen {
        ChatOpen(with: t.with, name: t.name ?? "", handle: t.handle ?? "", avatar: t.avatar ?? "",
                 explicit: t.explicit ?? false, group: t.group ?? false,
                 members: t.members ?? [], loaded: t.group != true || !(t.members ?? []).isEmpty)
    }

    // MARK: - The inbox

    /// `renderThreadList`: "Loading…" only when nothing is drawn yet; a
    /// refresh keeps the old list.
    func loadInbox() async {
        if threads.isEmpty { inboxState = .loading }
        do {
            let page = try await api.inbox()
            threads = page.threads
            unread = page.unread
            inboxState = .loaded
        } catch {
            if threads.isEmpty { inboxState = .failed }
        }
    }

    func setUnread(_ n: Int?) {
        if let n { unread = n }
    }

    /// A chat deleted or a group left: gone from the list at once.
    func dropThread(_ with: String, unread n: Int?) {
        threads.removeAll { $0.with == with }
        setUnread(n)
    }

    /// The groups this listener is in, for the share sheet's GROUPS row.
    var groups: [InboxThread] { threads.filter { $0.group == true } }

    // MARK: - The graph

    func loadGraph() async {
        if graph == nil { graphState = .loading }
        do {
            graph = try await api.friends()
            graphState = .loaded
        } catch {
            if graph == nil { graphState = .failed }
        }
    }

    func loadMe() async {
        if let found = try? await api.profile() { me = found }
    }

    /// The people a message, a group or a share can go to: friends only -
    /// two people who follow each other (the owner's rule, PROBLEMS.md
    /// §523; the server refuses anyone else).
    var reachable: [FollowPerson] {
        guard let g = graph else { return [] }
        var seen: Set<String> = []
        var out: [FollowPerson] = []
        for p in g.friends where !seen.contains(p.userId) {
            seen.insert(p.userId)
            out.append(p)
        }
        return out
    }

    /// Whether you and they follow each other: the only people a one-to-one
    /// message may go to.
    func isFriend(_ userID: String) -> Bool {
        !userID.isEmpty && (graph?.friends.contains { $0.userId == userID } ?? false)
    }

    /// New message's empty sentence (`paintNewChatContacts`, §523).
    static let nobodyToMessage = "Nobody to message yet. When someone you follow follows you back, you can message them."
    /// The share sheet's (`loadShareContacts`).
    static let nobodyToSend = "Nobody to send to yet \u{2014} find people under Friends"

    func person(handle: String) -> FollowPerson? {
        guard let g = graph, !handle.isEmpty else { return nil }
        let h = handle.lowercased()
        let all = g.friends + g.following + g.followers
        return all.first { ($0.handle ?? "").lowercased() == h }
    }

    func isFollowing(_ userID: String) -> Bool {
        graph?.following.contains { $0.userId == userID } ?? false
    }

    func followsMe(_ userID: String) -> Bool {
        graph?.followers.contains { $0.userId == userID } ?? false
    }

    /// "friends" / "you follow them" / "follows you" / "".
    func standing(_ userID: String) -> String {
        guard !userID.isEmpty else { return "" }
        let mine = isFollowing(userID)
        let theirs = followsMe(userID)
        if mine && theirs { return "friends" }
        if mine { return "you follow them" }
        if theirs { return "follows you" }
        return ""
    }

    /// How many of these labels are among the interests you show.
    func inCommon(_ labels: [String]) -> Int {
        let mine = Set((me?.interestsShown ?? []).compactMap { $0.label?.lowercased() })
        guard !mine.isEmpty else { return 0 }
        return labels.filter { mine.contains($0.lowercased()) }.count
    }

    /// Your handle, for an invite's "I'm @handle".
    var myHandle: String { me?.handle ?? "" }

    /// `followPerson`: by id, then the graph re-read. Returns whether it worked.
    @discardableResult
    func follow(userID: String = "", handle: String = "", quiet: Bool = false) async -> Bool {
        do {
            try await api.follow(userID: userID, handle: handle)
            await loadGraph()
            if !quiet { say("Following") }
            return true
        } catch {
            say(SocialModel.sentence(error, else: "Could not follow that person"))
            return false
        }
    }

    /// `unfollowPerson`: immediate, then the graph re-read.
    @discardableResult
    func unfollow(_ userID: String) async -> Bool {
        do {
            try await api.unfollow(userID: userID)
            await loadGraph()
            say("Unfollowed")
            return true
        } catch {
            say("Could not unfollow")
            return false
        }
    }

    // MARK: - Notifications (`index.html:22055-22285`)

    /// While the app is in front: the bootstrap once (the cursor, and the
    /// follower popup's first look), then a poll every 3 s - at once on
    /// coming back to the front. Cancelled when the app leaves the front.
    func runNotifications() async {
        if !bootstrapped {
            do {
                let first = try await api.notifications(since: 0, bootstrap: true)
                cursor = first.head
                unread = first.unread
                bootstrapped = true
            } catch {
                // Try again on the next round; nothing is announced meanwhile.
            }
            await loadGraph()
            Task { await loadMe() }
            checkNewFollowers()
        } else {
            await poll()
        }
        while !Task.isCancelled {
            try? await Task.sleep(nanoseconds: UInt64(Self.pollSeconds * 1_000_000_000))
            if Task.isCancelled { break }
            if !bootstrapped {
                if let first = try? await api.notifications(since: 0, bootstrap: true) {
                    cursor = first.head
                    unread = first.unread
                    bootstrapped = true
                }
                continue
            }
            await poll()
        }
    }

    private func poll() async {
        guard let page = try? await api.notifications(since: cursor, bootstrap: false) else { return }
        cursor = max(cursor, page.head)
        unread = page.unread
        // The Messages list on screen re-reads itself when a message lands.
        if !page.messages.isEmpty && onMessagesTab && messagesPath.isEmpty && cover == nil {
            Task { await loadInbox() }
        }
        for m in page.messages {
            guard let chat = Self.chat(for: m), chat.with != chatOnScreen else { continue }
            let sender = m.from?.name ?? "Someone"
            let title = m.group != nil ? sender + " in " + (m.group?.name ?? "the group") : sender
            enqueue(SocialBanner(title: title, text: Self.bannerText(m), name: sender,
                                 handle: m.from?.handle ?? "", avatar: m.from?.avatar ?? "", chat: chat))
        }
        for p in page.follows where !raised.contains(p.userId) {
            raised.insert(p.userId)
            Task { await api.followerAnnounced(p.userId) }
            // The web's words: their name, else "Someone" (`pollNotifications`).
            let name = (p.name ?? "").isEmpty ? "Someone" : (p.name ?? "")
            let handle = p.handle ?? ""
            enqueue(SocialBanner(title: name + " started following you",
                                 text: handle.isEmpty ? "Tap to follow back" : "@" + handle,
                                 name: name, handle: handle, avatar: p.avatar ?? "", chat: nil))
        }
        if !page.follows.isEmpty { Task { await loadGraph() } }
    }

    /// Which conversation a notification opens: the group, else the sender.
    static func chat(for m: ChatMessage) -> ChatOpen? {
        if let g = m.group {
            return ChatOpen(with: g.userId, name: g.name ?? "", group: true, loaded: false)
        }
        guard let from = m.from, let id = from.userId, !id.isEmpty else { return nil }
        return ChatOpen(with: id, name: from.name ?? "", handle: from.handle ?? "", avatar: from.avatar ?? "")
    }

    /// An episode - a share, or a reply to or a like on a vibe, which
    /// carry one too - is "Shared: <title|query|an episode>"; anything
    /// else is its words (`pollNotifications`).
    static func bannerText(_ m: ChatMessage) -> String {
        guard m.kind == "episode" else { return m.text ?? "" }
        let title = m.title ?? ""
        let query = m.query ?? ""
        return "Shared: " + (!title.isEmpty ? title : (!query.isEmpty ? query : "an episode"))
    }

    // MARK: Banners: one at a time, 3 s each, 420 ms apart

    private func enqueue(_ b: SocialBanner) {
        bannerQueue.append(b)
        if banner == nil && bannerTask == nil { showNextBanner() }
    }

    private func showNextBanner() {
        guard !bannerQueue.isEmpty else {
            bannerTask = nil
            return
        }
        let next = bannerQueue.removeFirst()
        withAnimation(.easeOut(duration: 0.25)) { banner = next }
        scheduleBannerEnd(next.id)
    }

    private func scheduleBannerEnd(_ id: UUID) {
        bannerTask?.cancel()
        bannerTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(Self.bannerSeconds * 1_000_000_000))
            guard let self, !Task.isCancelled, self.banner?.id == id, !self.bannerHeld else { return }
            self.dismissBanner()
        }
    }

    /// A finger on the banner holds it; lifting lets it go on.
    func holdBanner(_ on: Bool) {
        bannerHeld = on
        if !on, let id = banner?.id { scheduleBannerEnd(id) }
    }

    func dismissBanner() {
        bannerHeld = false
        withAnimation(.easeIn(duration: 0.2)) { banner = nil }
        bannerTask?.cancel()
        bannerTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(Self.bannerGap * 1_000_000_000))
            guard let self, !Task.isCancelled else { return }
            self.bannerTask = nil
            self.showNextBanner()
        }
    }

    /// A banner's tap: a message opens its conversation; a follow opens
    /// Friends (the web goes to YourFAM first, then pushes Friends).
    func openBanner(_ b: SocialBanner) {
        dismissBanner()
        if leavePlayer() {
            // The player is going down: open once it has.
            Task { @MainActor [weak self] in
                try? await Task.sleep(nanoseconds: 600_000_000)
                self?.route(b)
            }
            return
        }
        route(b)
    }

    private func route(_ b: SocialBanner) {
        if let chat = b.chat {
            if cover != nil {
                coverPath.append(.thread(chat))
            } else {
                requestTab(.messages)
                messagesPath = [.thread(chat)]
            }
        } else {
            if cover != nil {
                coverPath.append(.friends)
            } else {
                requestTab(.you)
                coverPath = []
                cover = SocialCover(root: .friends)
            }
        }
    }

    // MARK: The follower popup (`checkNewFollowers`)

    /// On opening the app: the first follower never announced, one at a
    /// time. Shown ⇒ announced, once ever; Friends is what clears "new".
    func checkNewFollowers() {
        guard follower == nil, let next = graph?.announce?.first(where: { !raised.contains($0.userId) }) else { return }
        raised.insert(next.userId)
        follower = next
        Task { await api.followerAnnounced(next.userId) }
    }

    /// Follow back from the popup: "You and <Name> are friends now".
    func followBack(_ p: FollowPerson) {
        follower = nil
        Task {
            do {
                try await api.follow(userID: p.userId)
                let name = (p.name ?? "").isEmpty ? "@" + (p.handle ?? "") : (p.name ?? "")
                say("You and \(name) are friends now")
                await loadGraph()
            } catch {
                say(SocialModel.sentence(error, else: "Could not follow that person"))
            }
            checkNewFollowers()
        }
    }

    func notNow() {
        follower = nil
        Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 400_000_000)
            self?.checkNewFollowers()
        }
    }

    /// Opening Friends: nobody is new any more.
    func friendsOpened() async {
        await api.friendsSeen()
        await loadGraph()
    }

    // MARK: - Sending inside FAM (the share sheet's people and groups)

    /// One tap, one message: an episode - with the id of the one heard,
    /// so they hear it too (§288; the web's share sheet drops it) - or a
    /// mix's link as words. "Sent to <First|group name>".
    func sendShare(to: String, label: String, text: String = "", query: String = "", minutes: Double = 0,
                   title: String = "", episode: String = "") async -> Bool {
        do {
            _ = try await api.sendMessage(to: to, text: text, query: query, minutes: minutes,
                                          title: title, episode: episode)
            // The web's snack drops the tick its toast text carries.
            say("Sent to \(label)")
            return true
        } catch {
            say(SocialModel.sentence(error, else: "Could not send that"))
            return false
        }
    }
}

/// One conversation on screen (`openThreadWith`, `sendThreadMessage`): the
/// whole thread on open, then every 2 s what arrived after `head`, kept
/// in step by row id; sends drawn at once at 55%, replaced in place.
@MainActor
final class ChatModel: ObservableObject {
    /// `THREAD_POLL_MS`, the typing note's pace (`TYPING_NOTE_MS`).
    static let pollSeconds: Double = 2
    static let typingEvery: TimeInterval = 2.5

    struct Row: Identifiable, Equatable {
        let id: String
        var message: ChatMessage
        var pending: Bool
    }

    let api: FAMAPI
    var say: (String) -> Void = { _ in }

    @Published var who: ChatOpen
    @Published private(set) var rows: [Row] = []
    @Published private(set) var state: LoadState = .idle
    @Published private(set) var typing = false
    @Published var draft = ""
    /// Rows that arrived since the last draw, which pop in (`popMessages`).
    @Published private(set) var fresh: Set<String> = []

    private var head = 0
    private var lastTypingNote = Date.distantPast

    init(api: FAMAPI, who: ChatOpen) {
        self.api = api
        self.who = who
    }

    /// Open, then top up every 2 s while on screen. A failed poll is silent.
    func run() async {
        if state != .loaded { await load() }
        while !Task.isCancelled {
            try? await Task.sleep(nanoseconds: UInt64(Self.pollSeconds * 1_000_000_000))
            if Task.isCancelled { break }
            // An open that failed is tried again on the poll's beat, quietly:
            // "Could not open that conversation." stays until one works.
            if state == .failed {
                await load(quiet: true)
            } else {
                await poll()
            }
        }
    }

    func load(quiet: Bool = false) async {
        if rows.isEmpty && !quiet { state = .loading }
        do {
            let page = try await api.chat(with: who.with)
            take(page.with)
            rows = page.messages.map { Row(id: "m\($0.id)", message: $0, pending: false) }
            head = page.head
            typing = page.typing ?? false
            state = .loaded
        } catch {
            if rows.isEmpty { state = .failed }
        }
    }

    private func poll() async {
        guard state == .loaded, let page = try? await api.chat(with: who.with, since: head) else { return }
        head = max(head, page.head)
        take(page.with)
        let known = Set(rows.map { $0.message.id })
        var added: [Row] = []
        for m in page.messages where !known.contains(m.id) {
            added.append(Row(id: "m\(m.id)", message: m, pending: false))
        }
        typing = page.typing ?? false
        if added.contains(where: { $0.message.mine != true }) { typing = false }
        if !added.isEmpty {
            fresh = Set(added.map(\.id))
            rows.append(contentsOf: added)
        }
    }

    /// The header may only now get its picture, or a group its people.
    private func take(_ w: ChatWith) {
        var next = who
        if let n = w.name, !n.isEmpty { next.name = n }
        if let h = w.handle { next.handle = h }
        if let a = w.avatar { next.avatar = a }
        if let e = w.explicit { next.explicit = e }
        if w.group == true {
            next.group = true
            next.named = w.named ?? false
            next.members = w.members ?? next.members
            next.warning = w.warning ?? next.warning
        }
        next.loaded = true
        if next != who { who = next }
    }

    /// The typing note, at most every 2.5 s while there is text.
    func noteTyping() {
        guard !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        guard Date().timeIntervalSince(lastTypingNote) >= Self.typingEvery else { return }
        lastTypingNote = Date()
        let to = who.with
        Task { await api.noteTyping(to: to) }
    }

    /// `sendThreadMessage`: an empty box does nothing; the bubble is drawn
    /// at once; a refusal takes it away, puts the words back, and says the
    /// server's sentence.
    func sendText() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        draft = ""
        // The server ends the dots on a send; the next keystroke says it
        // again at once (`typingSentAt = 0`).
        lastTypingNote = .distantPast
        let row = Row(id: "d" + UUID().uuidString, message: ChatMessage(draftText: text), pending: true)
        rows.append(row)
        fresh = [row.id]
        let to = who.with
        Task {
            do {
                let sent = try await api.sendMessage(to: to, text: text)
                settle(row.id, sent.message)
            } catch {
                rows.removeAll { $0.id == row.id }
                if draft.isEmpty { draft = text } else { draft = text + "\n" + draft }
                say(SocialModel.sentence(error, else: "Could not send that"))
            }
        }
    }

    /// The composer's (+): an episode, with the id of the one heard.
    func sendEpisode(query: String, minutes: Double, title: String, episode: String) {
        let row = Row(id: "d" + UUID().uuidString,
                      message: ChatMessage(draftText: "", query: query, minutes: minutes, title: title),
                      pending: true)
        rows.append(row)
        fresh = [row.id]
        let to = who.with
        Task {
            do {
                let sent = try await api.sendMessage(to: to, query: query, minutes: minutes,
                                                     title: title, episode: episode)
                settle(row.id, sent.message)
            } catch {
                rows.removeAll { $0.id == row.id }
                say(SocialModel.sentence(error, else: "Could not share that"))
            }
        }
    }

    /// The draft becomes the server's row in place. A poll that already
    /// brought it leaves just the one; an answer without the message is
    /// a reload rather than a second copy later (quirk 15).
    private func settle(_ draftID: String, _ message: ChatMessage?) {
        guard let message else {
            rows.removeAll { $0.id == draftID }
            Task { await load() }
            return
        }
        if rows.contains(where: { $0.message.id == message.id && $0.id != draftID }) {
            rows.removeAll { $0.id == draftID }
            return
        }
        if let at = rows.firstIndex(where: { $0.id == draftID }) {
            rows[at].message = message
            rows[at].pending = false
        }
    }

    /// A reported message leaves this listener's thread at once.
    func remove(messageID: Int) {
        rows.removeAll { $0.message.id == messageID }
    }
}
