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
    /// An interest chip's Topic screen (`openTopic`).
    case topic(TopicOpen)
    /// A vibe tapped on a friend's grid: their vibes as posts (§547).
    case vibePosts(VibePostsOpen)
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
    /// "You’re not friends with …" for a group with strangers in it (§520).
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
    /// The app's Undo line (`nowPlaying.undoLine`): the text, what Undo
    /// does, and the request held until the line goes.
    var undoLine: (String, @escaping () -> Void, @escaping () async -> Void) -> Void = { _, _, _ in }
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
    /// New message's card is up (`#newChatOverlay`), over every tab.
    @Published var newChatOpen = false

    /// Set by the tab bar: whether Messages is the tab on screen. Published:
    /// a conversation left open under another tab stops polling (and stops
    /// marking itself read, and swallowing its banners) until it is back.
    @Published var onMessagesTab = false
    /// The conversation on screen, whose messages raise no banner.
    var chatOnScreen: String?

    /// Chats deleted or left under their Undo (`threadsGone`): a re-read of
    /// the inbox never brings them back.
    private var threadsGone: Set<String> = []
    /// Their unread, still in the server's count until the request goes.
    private var goneUnread: [String: Int] = [:]
    /// Chats read on the tap (`markThreadRead`, §644): drawn read, and
    /// counted off the badge, before the server says so.
    @Published private(set) var readOnTap: Set<String> = []
    /// Every chat opened this session (`threadMemory`), drawn at once on
    /// reopening; emptied at sign-out.
    private var chatMemory: [String: ChatModel] = [:]

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
        followingBack = false
        followShown = [:]
        newChatOpen = false
        cursor = 0
        bootstrapped = false
        threadsGone = []
        goneUnread = [:]
        readOnTap = []
        chatMemory = [:]
        bannerQueue = []
        bannerTask?.cancel()
        raised = []
    }

    // MARK: - Navigation

    /// Open a social screen where the listener is: pushed on the stack on
    /// screen, else over the current tab with a stack of its own.
    func open(_ route: SocialRoute) {
        // A conversation already open is gone back to, never stacked again
        // (the owner's 11.12 notes: each tap on a message's banner pushed
        // the same chat once more, so the back arrow led into it again
        // once per tap).
        if case let .thread(chat) = route, returnToThread(chat.with) { return }
        // Another chat opened from inside a chat (its banner) replaces the
        // open one, so Back goes to the list (§644).
        if case .thread = route {
            if cover != nil, let last = coverPath.last, case .thread = last {
                coverPath[coverPath.count - 1] = route
                return
            }
            if cover == nil, onMessagesTab, let last = messagesPath.last, case .thread = last {
                messagesPath[messagesPath.count - 1] = route
                return
            }
        }
        if cover != nil {
            coverPath.append(route)
        } else if onMessagesTab {
            messagesPath.append(route)
        } else {
            coverPath = []
            cover = SocialCover(root: route)
        }
    }

    /// The conversation with `with` on the stack shown: everything above
    /// it is popped and true returned; false when it is not there.
    private func returnToThread(_ with: String) -> Bool {
        func index(_ path: [SocialRoute]) -> Int? {
            path.lastIndex { if case let .thread(c) = $0 { return c.with == with }; return false }
        }
        if let root = cover?.root {
            if let i = index(coverPath) {
                coverPath = Array(coverPath.prefix(i + 1))
                return true
            }
            if case let .thread(c) = root, c.with == with {
                coverPath = []
                return true
            }
            return false
        }
        if onMessagesTab, let i = index(messagesPath) {
            messagesPath = Array(messagesPath.prefix(i + 1))
            return true
        }
        return false
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
    /// refresh keeps the old list. Read beside the notifications' first
    /// call and whenever the count moves (`fetchInbox`, §644), so the list
    /// is in hand when Messages opens.
    func loadInbox() async {
        if threads.isEmpty { inboxState = .loading }
        do {
            let page = try await api.inbox()
            let gone = threadsGone
            threads = page.threads.filter { !gone.contains($0.with) }
            // A chat read on the tap stays read only while it is the one on
            // screen; elsewhere the server's count stands.
            readOnTap = readOnTap.filter { $0 == chatOnScreen }
            // What the gone chats still hold in the server's count.
            for t in page.threads where gone.contains(t.with) { goneUnread[t.with] = t.unread ?? 0 }
            unread = withoutHeld(page.unread) ?? 0
            inboxState = .loaded
        } catch {
            if threads.isEmpty { inboxState = .failed }
        }
    }

    /// The server's count less the chats read on the tap or gone under
    /// their Undo, which it does not know about yet.
    func withoutHeld(_ n: Int?) -> Int? {
        guard var n else { return nil }
        for t in threads where readOnTap.contains(t.with) { n -= t.unread ?? 0 }
        for k in goneUnread.values { n -= k }
        return max(0, n)
    }

    /// A row's unread count as drawn: none once it was tapped.
    func unreadCount(_ t: InboxThread) -> Int {
        readOnTap.contains(t.with) ? 0 : (t.unread ?? 0)
    }

    /// A chat is read on the tap (`markThreadRead`): its row reads 0 and the
    /// Messages count drops at once.
    func markRead(_ with: String) {
        guard !readOnTap.contains(with), let row = threads.first(where: { $0.with == with }),
              let n = row.unread, n > 0 else { return }
        readOnTap.insert(with)
        unread = max(0, unread - n)
    }

    /// The conversation kept for `open.with`, or a new one kept from now
    /// (`threadMemory`).
    func chat(for open: ChatOpen) -> ChatModel {
        if let kept = chatMemory[open.with] { return kept }
        let made = ChatModel(api: api, who: open)
        made.onScreen = { [weak self, weak made] in
            guard let self, let made else { return false }
            return self.chatOnScreen == made.who.with
        }
        chatMemory[open.with] = made
        return made
    }

    /// Delete chat / Leave group (§644's delete-chat B): no question - the
    /// chat is off the list and out of memory at once, the screen goes
    /// back, and "Chat deleted" / "You left the group" carries Undo for 5 s;
    /// the request goes only when the line goes, and a refusal puts the
    /// chat back and says so (`goneChat`).
    func goneChat(_ with: String, group: Bool) {
        let at = threads.firstIndex { $0.with == with }
        let row = at.map { threads[$0] }
        let n = row.map { unreadCount($0) } ?? 0
        let kept = chatMemory[with]
        threadsGone.insert(with)
        goneUnread[with] = n
        withAnimation(Motion.anim(Motion.easeIn(Motion.Dur.rowOut))) {
            threads.removeAll { $0.with == with }
        }
        chatMemory[with] = nil
        unread = max(0, unread - n)
        back()
        let refused = group ? "Could not leave that group" : "Could not delete that chat"
        undoLine(group ? "You left the group" : "Chat deleted", { [weak self] in
            guard let self else { return }
            self.threadsGone.remove(with)
            self.goneUnread[with] = nil
            if let kept { self.chatMemory[with] = kept }
            guard let row, !self.threads.contains(where: { $0.with == with }) else { return }
            withAnimation(Motion.anim(Motion.easeOut(Motion.Dur.undoIn))) {
                self.threads.insert(row, at: min(at ?? 0, self.threads.count))
            }
            self.unread += n
        }, { [weak self] in
            guard let self else { return }
            do {
                let answer: UnreadAnswer
                if group {
                    answer = try await self.api.leaveGroup(with)
                } else {
                    answer = try await self.api.deleteChat(with: with)
                }
                self.goneUnread[with] = nil
                self.setUnread(self.withoutHeld(answer.unread))
            } catch {
                self.threadsGone.remove(with)
                self.goneUnread[with] = nil
                self.say(SocialModel.sentence(error, else: refused))
                await self.loadInbox()
            }
        })
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
    /// §520; the server refuses anyone else).
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

    /// One-to-one is refused before it opens only when the friends list has
    /// loaded and they are not on it (the web's `notAFriend`); with no list
    /// yet the conversation opens and the server decides on the send.
    func mayNotMessage(_ userID: String) -> Bool {
        graph != nil && !isFriend(userID)
    }

    /// New message's empty sentence (`paintNewChatContacts`, §520).
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
        // A tap not yet answered reads as it asked (§645).
        if let shown = followShown[userID] { return shown }
        return graph?.following.contains { $0.userId == userID } ?? false
    }

    /// Follow / Unfollow tapped and not yet answered, by id: every Follow
    /// button for them reads the tap at once, put back if refused
    /// (`Motion.followButton`, §645).
    @Published private(set) var followShown: [String: Bool] = [:]

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

    /// `followPerson`: by id, then the graph re-read. Returns whether it
    /// worked. Following on the tap; no line on success - "Following" /
    /// "Unfollowed" are gone, only a refusal is said (§645). `quiet` is
    /// kept for the callers that passed it.
    @discardableResult
    func follow(userID: String = "", handle: String = "", quiet: Bool = false) async -> Bool {
        if !userID.isEmpty { followShown[userID] = true }
        do {
            try await api.follow(userID: userID, handle: handle)
            await loadGraph()
            if !userID.isEmpty { followShown[userID] = nil }
            return true
        } catch {
            if !userID.isEmpty { followShown[userID] = nil }
            say(SocialModel.sentence(error, else: "Could not follow that person"))
            return false
        }
    }

    /// `unfollowPerson`: "Follow" on the tap, then the graph re-read.
    @discardableResult
    func unfollow(_ userID: String) async -> Bool {
        followShown[userID] = false
        do {
            try await api.unfollow(userID: userID)
            await loadGraph()
            followShown[userID] = nil
            return true
        } catch {
            followShown[userID] = nil
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
            // The inbox beside the first call, so Messages opens on it.
            Task { await loadInbox() }
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
        let now = withoutHeld(page.unread) ?? page.unread
        let moved = now != unread
        unread = now
        // The inbox is read again when a message lands or the count moves,
        // the list on screen or not (`fetchInbox`, §644).
        if !page.messages.isEmpty || moved {
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

    /// An episode message (`pollNotifications`, §644): a like on a vibe is
    /// "Liked your vibe · <title>", a reply "Replied to your vibe: <words>",
    /// a share "Shared: <title|query|an episode>"; anything else is its
    /// words.
    static func bannerText(_ m: ChatMessage) -> String {
        guard m.kind == "episode" else { return m.text ?? "" }
        let title = m.title ?? ""
        let query = m.query ?? ""
        let words = m.text ?? ""
        if words == FAMAPI.storyLike {
            return "Liked your vibe" + (title.isEmpty ? "" : " \u{00B7} " + title)
        }
        if !words.isEmpty { return "Replied to your vibe: " + words }
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
        // `#notifBanner.show`: down from above, 0.28 s cubic-bezier(.2,.8,.3,1).
        withAnimation(.timingCurve(0.2, 0.8, 0.3, 1, duration: 0.28)) { banner = next }
        // `role=status aria-live=polite`: VoiceOver reads it as it lands.
        AccessibilityNotification.Announcement(next.title + ". " + next.text).post()
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
        // Closed on its open curve (`.notif`'s transition runs both ways).
        withAnimation(.timingCurve(0.2, 0.8, 0.3, 1, duration: 0.28)) { banner = nil }
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

    /// A message opens its conversation over whatever is on screen, without
    /// changing tab (`openThreadWith` pushes the thread on the stack shown);
    /// a follow goes to YourFAM, then Friends.
    private func route(_ b: SocialBanner) {
        if let chat = b.chat {
            open(.thread(chat))
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

    /// The follow-back in flight, so a second tap sends nothing.
    @Published private(set) var followingBack = false

    /// Follow back from the popup (`followBack`, §645): the popup closes on
    /// the tap and every Follow for them reads Following; then "You and
    /// <Name> are friends now" ("they" with no name). A refusal puts them
    /// back and says the server's sentence, else "Could not follow them
    /// back".
    func followBack(_ p: FollowPerson) {
        guard !followingBack else { return }
        followingBack = true
        if follower?.userId == p.userId { follower = nil }
        followShown[p.userId] = true
        Task {
            do {
                try await api.follow(userID: p.userId)
                followingBack = false
                let name = (p.name ?? "").isEmpty ? "they" : (p.name ?? "")
                say("You and \(name) are friends now")
                await loadGraph()
                followShown[p.userId] = nil
                Task { await loadMe() }
                // The next follower waits for this popup to have gone.
                try? await Task.sleep(nanoseconds: 400_000_000)
                checkNewFollowers()
            } catch {
                followingBack = false
                followShown[p.userId] = nil
                say(SocialModel.sentence(error, else: "Could not follow them back"))
            }
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
/// in step by row id; sends drawn at once and replaced in place, "Sending…"
/// under them past a second, a refused one kept, faded, "Not sent · Tap to
/// retry" (§644). Kept in memory by `SocialModel.chat(for:)` while signed
/// in, so a chat opened again is drawn at once (`threadMemory`).
@MainActor
final class ChatModel: ObservableObject {
    /// `THREAD_POLL_MS`, the typing note's pace (`TYPING_NOTE_MS`).
    static let pollSeconds: Double = 2
    static let typingEvery: TimeInterval = 2.5

    struct Row: Identifiable, Equatable {
        let id: String
        var message: ChatMessage
        var pending: Bool
        /// Refused: it stays, faded, "Not sent · Tap to retry" (§644).
        var failed = false
        /// The server's reason, for VoiceOver ("Not sent: <why>").
        var why = ""
        /// The episode id an episode send carried, for a retry.
        var episode = ""
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
    /// "Sending…" under the last bubble: only once a send has waited a
    /// second (`armSending`, §644).
    @Published private(set) var sendingShown = false
    /// The open has waited 300 ms with nothing drawn: "Loading…" may show
    /// (`lateLoader`).
    @Published private(set) var slowLoad = false
    /// Whether this chat is the one on screen: a failure off screen is said
    /// on the bottom line too ("Not sent: …").
    var onScreen: () -> Bool = { true }

    private var head = 0
    private var lastTypingNote = Date.distantPast

    init(api: FAMAPI, who: ChatOpen) {
        self.api = api
        self.who = who
    }

    /// Open, then top up every 2 s while on screen. A failed poll is silent.
    /// A chat kept in memory is drawn at once and topped up from its
    /// cursor at once (`threadMemory`).
    func run() async {
        if state != .loaded { await load() } else { await poll() }
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
        if rows.isEmpty && !quiet {
            state = .loading
            // "Loading…" only past 300 ms (`lateLoader`).
            Task { [weak self] in
                try? await Task.sleep(nanoseconds: 300_000_000)
                guard let self, self.state == .loading else { return }
                self.slowLoad = true
            }
        }
        defer { slowLoad = false }
        do {
            let page = try await api.chat(with: who.with)
            take(page.with)
            // A refused send stays where it was typed (§644).
            let kept = rows.filter { $0.failed || $0.pending }
            rows = page.messages.map { Row(id: "m\($0.id)", message: $0, pending: false) } + kept
            head = page.head
            typing = page.typing ?? false
            state = .loaded
        } catch {
            if rows.isEmpty { state = .failed }
        }
    }

    /// A poll at once (`pollThread` after a rename or a new photo): the
    /// group's system line arrives without waiting for the beat.
    func pollNow() async {
        await poll()
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
    /// at once and the box stays clear. A refusal keeps the bubble, faded,
    /// marked "Not sent · Tap to retry" (§644's send-failed C, reversing the
    /// words put back in the box).
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
        post(row)
    }

    /// The composer's (+): an episode, with the id of the one heard.
    func sendEpisode(query: String, minutes: Double, title: String, episode: String) {
        let row = Row(id: "d" + UUID().uuidString,
                      message: ChatMessage(draftText: "", query: query, minutes: minutes, title: title),
                      pending: true, episode: episode)
        rows.append(row)
        fresh = [row.id]
        post(row)
    }

    /// A tap on "Not sent · Tap to retry" or its bubble: the same draft
    /// again (`retryThreadMessage`).
    func retry(_ id: String) {
        guard let at = rows.firstIndex(where: { $0.id == id }), rows[at].failed else { return }
        rows[at].failed = false
        rows[at].why = ""
        rows[at].pending = true
        post(rows[at])
    }

    /// One draft to the server (`postDraft`): words, or an episode.
    private func post(_ row: Row) {
        let to = who.with
        let m = row.message
        let isEpisode = !(m.query ?? "").isEmpty
        armSending()
        Task {
            do {
                let sent: SentMessage
                if isEpisode {
                    sent = try await api.sendMessage(to: to, query: m.query ?? "", minutes: m.minutes ?? 0,
                                                     title: m.title ?? "", episode: row.episode)
                } else {
                    sent = try await api.sendMessage(to: to, text: m.text ?? "")
                }
                settle(row.id, sent.message)
            } catch {
                let why = SocialModel.sentence(error, else: isEpisode ? "Could not share that"
                                                                       : "Could not send that")
                if let at = rows.firstIndex(where: { $0.id == row.id }) {
                    rows[at].pending = false
                    rows[at].failed = true
                    rows[at].why = why
                }
                // Off screen, the failure is said too (§646).
                if !onScreen() { say("Not sent: " + why) }
            }
            settleSending()
        }
    }

    /// "Sending…" once a send has waited a second; gone when none waits.
    private func armSending() {
        Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(Motion.Dur.sendingAfter * 1_000_000_000))
            guard let self else { return }
            if self.rows.contains(where: \.pending) { self.sendingShown = true }
        }
    }

    private func settleSending() {
        if !rows.contains(where: \.pending) { sendingShown = false }
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
