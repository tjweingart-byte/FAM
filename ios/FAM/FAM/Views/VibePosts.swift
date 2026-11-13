import FAMSafety
import SwiftUI

// Vibes as posts (§547, `vibe-posts`), ported from the web's
// `screen-vibeposts` (`openVibePosts`, `vibePostHTML`, `openVibeReplies`).
//
// The owner's "Vibes as Posts", option C ("Compact Trio"): a vibe tapped on
// your own YourFAM grid or a friend's opens here, as a post - who vibed it
// and the day they did, the line they picked across the picture, a small
// player under it with that line marked in gold, then likes, the friends
// who heard it, the title, the caption and the reply shown under it.
// Scrolling down goes through the rest of their vibes, newest first.
//
// **It plays where it sits.** Play is the ordinary play (`AppModel.play`,
// so a replay and its rules), marked `inPlace` so the full player does not
// open over the posts; leaving this screen never stops it. The post's bar
// reads the one player, so there is still one transport.

/// Whose vibes, and the tile tapped. `fromYou`: pushed on YourFAM's stack
/// (your own grid), else on a social stack (a friend's profile).
struct VibePostsOpen: Hashable {
    var handle: String
    var index: Int = 0
    var fromYou: Bool = false
}

extension YouRoute {
    /// The posts screen has no tab bar on the web (`placeNowBar`).
    var isVibePosts: Bool {
        switch self {
        case .vibePosts: return true
        default: return false
        }
    }
}

/// One post as the screen draws it: the server's post, with the counts a
/// like or a reply changes kept here.
struct VibePostItem: Identifiable, Equatable {
    let id: Int
    let query: String
    let title: String
    let minutes: Double
    let at: Double
    let caption: String
    let lineText: String
    /// Where the line starts, or nil (a line without one has no chip).
    let lineAt: Double?
    var likes: Int
    var liked: Bool
    var replies: Int
    var topReply: VibeReply?
    let heardCount: Int
    let heardFaces: [VibePostPerson]
    let thumb: String

    init(_ p: VibePost) {
        id = p.id
        query = p.query
        title = p.title ?? ""
        let m = p.minutes ?? 0
        minutes = m > 0 ? m : FAMAPI.browseMinutes
        at = p.at ?? 0
        caption = p.caption ?? ""
        lineText = p.line?.text ?? ""
        lineAt = lineText.isEmpty ? nil : p.line?.at
        likes = p.likes ?? 0
        liked = p.liked ?? false
        replies = p.replies ?? 0
        topReply = p.topReply
        heardCount = p.heard?.count ?? 0
        heardFaces = Array((p.heard?.faces ?? []).prefix(3))
        thumb = p.thumb ?? ""
    }

    var key: EpisodeKey { EpisodeKey(query: query, minutes: minutes) }
    var shownTitle: String { title.isEmpty ? query : title }
}

/// A like on a reply, as the server last said it.
struct VibeReplyLike: Equatable {
    let likes: Int
    let liked: Bool
}

/// A report asked from a post's or a reply's menu.
struct VibeReportAsk: Identifiable {
    let id = UUID()
    let subject: ReportSubject
    /// The post that leaves the list once reported (a vibe), else 0.
    let postID: Int
}

@MainActor
enum VibePostPlay {
    /// The post whose episode is the one loaded (`vibePostIsCurrent`).
    static func isCurrent(_ p: VibePostItem, now: NowPlayingModel, player: StreamPlayer) -> Bool {
        guard player.isLoaded, let ep = now.episode else { return false }
        return ep.key.query == p.query && ep.key.minutes == p.minutes
    }

    /// "today", "yesterday", "3 days ago", then the date - the catalogue's
    /// calendar (`episodeDayLabel`), worked from how long ago.
    static func dayLabel(_ age: Double) -> String {
        let made = Date().addingTimeInterval(-max(0, age))
        let cal = Calendar.current
        let days = cal.dateComponents([.day], from: cal.startOfDay(for: made),
                                      to: cal.startOfDay(for: Date())).day ?? 0
        if days <= 0 { return "Today" }
        if days == 1 { return "Yesterday" }
        if days <= 6 { return "\(days) days ago" }
        let gregorian = Calendar(identifier: .gregorian)
        let sameYear = gregorian.component(.year, from: made) == gregorian.component(.year, from: Date())
        let format = DateFormatter()
        format.locale = Locale(identifier: "en_US_POSIX")
        format.calendar = gregorian
        format.timeZone = TimeZone.current
        format.dateFormat = sameYear ? "MMM d" : "MMM d, yyyy"
        return format.string(from: made)
    }

    /// `vibeDayWord`: Today and Yesterday lower-cased after "Vibed".
    static func vibedWord(_ at: Double) -> String {
        let age = Date().timeIntervalSince1970 - at
        let word = dayLabel(age)
        if word == "Today" || word == "Yesterday" { return word.lowercased() }
        return word
    }
}

// MARK: - The screen's state (`vp`, `vpr`)

@MainActor
final class VibePostsModel: ObservableObject {
    @Published private(set) var posts: [VibePostItem] = []
    @Published private(set) var person: VibePostPerson?
    @Published private(set) var mine: Bool
    @Published private(set) var more = false
    /// "loading", "ready" or "failed" (the first page only).
    @Published private(set) var state = "loading"
    /// The first answer has come: the title is "Your vibes" or "Vibes".
    @Published private(set) var loaded = false
    /// The post to scroll to once the first page is drawn (`focus`).
    @Published private(set) var scrollTarget: Int?
    private var loading = false
    private var started = false

    // The replies sheet.
    @Published private(set) var repliesShown = false
    @Published private(set) var replyID = 0
    @Published private(set) var replies: [VibeReply] = []
    /// "loading", "ready" or "failed".
    @Published private(set) var repliesState = "loading"
    @Published private(set) var repliesMine = false
    @Published private(set) var replyLikes: [Int: VibeReplyLike] = [:]
    @Published private(set) var replyParent = 0
    @Published private(set) var replyParentName = ""
    @Published var replyText = ""
    @Published private(set) var sending = false

    let handle: String
    let focus: Int
    static let page = 12

    init(handle: String, focus: Int) {
        self.handle = handle
        self.focus = max(0, focus)
        self.mine = handle.isEmpty
    }

    func index(of id: Int) -> Int? { posts.firstIndex(where: { $0.id == id }) }

    func start(_ api: FAMAPI, say: @escaping (String) -> Void) {
        guard !started else { return }
        started = true
        Task { await self.load(api, next: false, say: say) }
    }

    /// `loadVibePosts`: the grid shows twelve, so the first page asks for
    /// enough that the tapped one is there; More pages by the last's time.
    func load(_ api: FAMAPI, next: Bool, say: @escaping (String) -> Void) async {
        guard !loading else { return }
        loading = true
        var before: Double = 0
        if next, let last = posts.last { before = last.at }
        let wanted = next ? Self.page : max(Self.page, focus + 1)
        do {
            let page = try await api.vibePosts(handle: handle, before: before, limit: min(30, wanted))
            loading = false
            if let who = page.person { person = who }
            mine = page.mine ?? false
            more = page.more ?? false
            loaded = true
            var fresh: [VibePostItem] = []
            for p in page.posts ?? [] { fresh.append(VibePostItem(p)) }
            if next {
                // A post already here (the list moved meanwhile) is not drawn twice.
                let known = Set(posts.map(\.id))
                posts.append(contentsOf: fresh.filter { !known.contains($0.id) })
            } else {
                posts = fresh
            }
            state = "ready"
            if !next && posts.indices.contains(focus) { scrollTarget = posts[focus].id }
        } catch {
            loading = false
            if next {
                say("Could not load more vibes")
            } else {
                state = "failed"
            }
        }
    }

    /// Drawn at once, put right if the server says otherwise; found again
    /// by id, since the list may have changed by the time it answers.
    func like(_ id: Int, api: FAMAPI, say: @escaping (String) -> Void) {
        guard let i = index(of: id) else { return }
        let on = !posts[i].liked
        posts[i].liked = on
        posts[i].likes = max(0, posts[i].likes + (on ? 1 : -1))
        Task {
            do {
                let d = try await api.likeVibePost(id, on: on)
                if let k = self.index(of: id) {
                    self.posts[k].likes = d.likes ?? self.posts[k].likes
                    self.posts[k].liked = d.liked ?? on
                }
            } catch {
                if let k = self.index(of: id) {
                    self.posts[k].liked = !on
                    self.posts[k].likes = max(0, self.posts[k].likes + (on ? -1 : 1))
                }
                say("Could not like that")
            }
        }
    }

    /// Removes only the post itself, wherever it sits now.
    func remove(_ id: Int) {
        posts.removeAll { $0.id == id }
    }

    // MARK: Replies

    func openReplies(_ id: Int, api: FAMAPI) {
        replyID = id
        replies = []
        replyLikes = [:]
        repliesState = "loading"
        replyParent = 0
        replyParentName = ""
        repliesMine = mine
        sending = false
        repliesShown = true
        Task { await self.loadReplies(api) }
    }

    func closeReplies() {
        repliesShown = false
    }

    func loadReplies(_ api: FAMAPI) async {
        let id = replyID
        do {
            let d = try await api.vibeReplies(id)
            guard id == replyID else { return }
            replies = d.replies ?? []
            replyLikes = [:]
            repliesMine = d.mine ?? false
            repliesState = "ready"
            if let k = index(of: id) {
                posts[k].replies = d.count ?? 0
                posts[k].topReply = replies.first
            }
        } catch {
            guard id == replyID else { return }
            repliesState = "failed"
        }
    }

    func findReply(_ id: Int) -> VibeReply? {
        for c in replies {
            if c.id == id { return c }
            for r in c.replies ?? [] where r.id == id { return r }
        }
        return nil
    }

    func likes(of c: VibeReply) -> VibeReplyLike {
        if let known = replyLikes[c.id] { return known }
        return VibeReplyLike(likes: c.likes ?? 0, liked: c.liked ?? false)
    }

    /// `replyToVibeReply`: answering a reply, or 0 for a new one.
    func replyTo(_ id: Int) {
        let c = id == 0 ? nil : findReply(id)
        replyParent = c == nil ? 0 : id
        var name = ""
        if let r = c {
            let h = r.handle ?? ""
            name = h.isEmpty ? (r.name ?? "") : h
        }
        replyParentName = name
    }

    func send(_ api: FAMAPI, say: @escaping (String) -> Void) {
        let text = replyText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !sending else { return }
        sending = true
        let id = replyID
        let parent = replyParent
        Task {
            do {
                _ = try await api.postVibeReply(id, text: text, parent: parent)
                self.sending = false
                self.replyParent = 0
                self.replyParentName = ""
                self.replyText = ""
                await self.loadReplies(api)
            } catch {
                self.sending = false
                say(SocialModel.sentence(error, else: "Could not post that"))
            }
        }
    }

    func likeReply(_ id: Int, api: FAMAPI, say: @escaping (String) -> Void) {
        guard let c = findReply(id) else { return }
        let on = !likes(of: c).liked
        Task {
            do {
                let d = try await api.likeVibeReply(id, on: on)
                self.replyLikes[id] = VibeReplyLike(likes: d.likes ?? 0, liked: d.liked ?? on)
            } catch {
                say("Could not like that")
            }
        }
    }

    func deleteReply(_ id: Int, api: FAMAPI, say: @escaping (String) -> Void) {
        Task {
            let ok = (try? await api.deleteVibeReply(id)) ?? false
            if ok {
                await self.loadReplies(api)
            } else {
                say("Could not delete that")
            }
        }
    }
}

// MARK: - The screen (`#screen-vibeposts`)

struct VibePostsView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var social: SocialModel
    @Environment(\.inSocialCover) private var inCover
    @StateObject private var vp: VibePostsModel
    let open: VibePostsOpen

    @State private var menuFor: VibePostItem?
    @State private var replyMenuFor: VibeReply?
    @State private var reporting: VibeReportAsk?
    @State private var sharing: ShareSubject?
    @State private var listHeight: CGFloat = 0
    @FocusState private var replyFocused: Bool

    init(open: VibePostsOpen) {
        self.open = open
        _vp = StateObject(wrappedValue: VibePostsModel(handle: open.handle, focus: open.index))
    }

    var body: some View {
        presentations(screen)
    }

    private var screen: some View {
        ZStack {
            VStack(spacing: 0) {
                topBar
                ScrollViewReader { proxy in
                    ScrollView {
                        postsBody
                    }
                    .onChange(of: vp.scrollTarget) { _, target in
                        guard let target else { return }
                        Task { @MainActor in
                            try? await Task.sleep(nanoseconds: 60_000_000)
                            proxy.scrollTo(target, anchor: .top)
                        }
                    }
                }
            }
            // `#vpReplies` is shown and hidden by `hidden`: at once.
            if vp.repliesShown {
                repliesLayer
                    .transition(.identity)
            }
            // Inside a cover the app's loading screen is drawn under it, so
            // the wait for a post's episode is drawn here (`generate`'s
            // overlay is over every screen on the web).
            if inCover {
                LoadingLayer(loading: model.loading)
            }
        }
        .background(FAMColor.background)
        .toolbar(.hidden, for: .navigationBar)
        .onAppear {
            model.vibePostsShowing += 1
            let app = model
            vp.start(model.api, say: { app.nowPlaying.say($0) })
        }
        .onDisappear { model.vibePostsShowing = max(0, model.vibePostsShowing - 1) }
    }

    /// The ⋯ sheets, Report and Share, kept out of `body`.
    private func presentations<V: View>(_ content: V) -> some View {
        content
            .famSheet(item: $menuFor) { p in postMenu(p) }
            .famSheet(item: $replyMenuFor) { c in replyMenu(c) }
            .famSheet(item: $reporting) { ask in reportSheet(ask) }
            .famSheet(item: $sharing) { subject in
                ShareSheet(subject: subject)
                    .environmentObject(model)
                    .environmentObject(model.nowPlaying)
            }
    }

    // MARK: Top bar (`.vp-bar-top`)

    private var topBar: some View {
        HStack(spacing: 6) {
            SocialBackButton { back() }
            VStack(alignment: .leading, spacing: 1) {
                Text(titleWord)
                    .font(FAMFont.heading(17, .semibold))
                    .foregroundStyle(FAMColor.text)
                    .fixedSize(horizontal: false, vertical: true)
                if !subLine.isEmpty {
                    Text(subLine)
                        .font(FAMFont.body(12))
                        .foregroundStyle(FAMColor.textMuted)
                        .lineLimit(1)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 16)
        .padding(.top, 12)
        .padding(.bottom, 4)
    }

    private var titleWord: String {
        vp.loaded && vp.mine ? "Your vibes" : "Vibes"
    }

    private var subLine: String {
        if vp.loaded {
            let h = vp.person?.handle ?? ""
            return h.isEmpty ? "" : "@" + h
        }
        return open.handle.isEmpty ? "" : "@" + open.handle
    }

    private func back() {
        if open.fromYou {
            if !model.youPath.isEmpty { model.youPath.removeLast() }
        } else {
            social.back()
        }
    }

    // MARK: The posts (`renderVibePosts`)

    @ViewBuilder private var postsBody: some View {
        VStack(spacing: 0) {
            if vp.state == "failed" {
                note("Could not load these vibes. Pull back and try again.")
            } else if vp.state != "ready" {
                note("Loading\u{2026}")
            } else if vp.posts.isEmpty {
                note(vp.mine ? "Nothing vibed yet. Tap VIBE! on any episode and it shows here."
                             : "No vibes to show.")
            } else {
                LazyVStack(spacing: 0) {
                    ForEach(vp.posts) { p in
                        post(p).id(p.id)
                    }
                }
                if vp.more {
                    moreButton
                }
            }
        }
        .padding(.top, 4)
        .padding(.bottom, 24)
    }

    private func note(_ words: String) -> some View {
        SocialNote(text: words)
            .padding(.horizontal, 16)
            .padding(.top, -4)
    }

    private var moreButton: some View {
        Button {
            let app = model
            let vp = self.vp
            Task { await vp.load(app.api, next: true, say: { app.nowPlaying.say($0) }) }
        } label: {
            Text("More vibes")
                .font(FAMFont.body(14))
                .foregroundStyle(FAMColor.text)
                .padding(.horizontal, 18)
                .padding(.vertical, 9)
                .overlay(Capsule().stroke(FAMColor.borderStrong, lineWidth: 1))
                .contentShape(Capsule())
        }
        .buttonStyle(.press)
        .padding(.top, 4)
    }

    // MARK: A post (`vibePostHTML`)

    private var who: VibePostPerson? { vp.person }

    private var whoWord: String {
        let h = who?.handle ?? ""
        return h.isEmpty ? (who?.name ?? "") : h
    }

    private func post(_ p: VibePostItem) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            postHead(p)
            postPicture(p)
            VibePostTransport(post: p, play: { play(p) }, playLine: { playLine(p) })
            postActions(p)
            postMeta(p)
        }
        .padding(.top, 6)
        .padding(.bottom, 20)
    }

    private func postHead(_ p: VibePostItem) -> some View {
        HStack(spacing: 10) {
            Face(name: who?.name ?? "", handle: who?.handle ?? "", avatar: who?.avatar ?? "",
                 size: 32, initialsSize: 12)
            VStack(alignment: .leading, spacing: 1) {
                HStack(spacing: 5) {
                    if who?.explicit == true { ExplicitBadge() }
                    Text(whoWord)
                        .font(FAMFont.body(14, .semibold))
                        .foregroundStyle(FAMColor.text)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Text(("Vibed " + VibePostPlay.vibedWord(p.at)).uppercased())
                    .font(FAMFont.mono(10))
                    .tracking(0.8)
                    .foregroundStyle(FAMColor.textMuted)
            }
            Spacer(minLength: 0)
            Button { menuFor = p } label: {
                WebFill(path: Icons.dots(1.7), size: 18)
                    .foregroundStyle(FAMColor.text)
                    .frame(minWidth: 36, minHeight: 36)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.press)
            .accessibilityLabel("More")
        }
        .padding(.horizontal, 16)
        .padding(.top, 6)
        .padding(.bottom, 8)
    }

    /// `.vp-pic`: 4:3, radius 14, the picture over the surface, and the
    /// line across its foot in gold quotes (`.vp-strip`).
    private func postPicture(_ p: VibePostItem) -> some View {
        let url = p.thumb.isEmpty ? nil : model.api.picture(p.thumb)
        return Color.clear
            .aspectRatio(4.0 / 3.0, contentMode: .fit)
            .background {
                ZStack {
                    FAMColor.surface
                    if let url {
                        AsyncImage(url: url) { $0.resizable().scaledToFill() } placeholder: { Color.clear }
                    }
                }
            }
            .overlay(alignment: .bottom) {
                if !p.lineText.isEmpty { lineStrip(p.lineText) }
            }
            .clipShape(RoundedRectangle(cornerRadius: 14))
            .padding(.horizontal, 16)
            .accessibilityElement(children: .combine)
            .accessibilityLabel(p.lineText.isEmpty ? p.shownTitle : "\u{201C}" + p.lineText + "\u{201D}")
    }

    private func lineStrip(_ words: String) -> some View {
        let gold = Color(hex: 0xE0B563)
        let opening = Text("\u{201C}").foregroundColor(gold)
        let closing = Text("\u{201D}").foregroundColor(gold)
        let spoken = Text(words).foregroundColor(Color(hex: 0xF4EFE4))
        return (opening + spoken + closing)
            .font(FAMFont.heading(15, .semibold))
            .lineSpacing(2)
            .multilineTextAlignment(.leading)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.top, 28)
            .padding(.horizontal, 12)
            .padding(.bottom, 10)
            .background(LinearGradient(colors: [Color.clear, Color(hex: 0x0E0C12).opacity(0.88)],
                                       startPoint: .top, endPoint: .bottom))
    }

    /// Heart, reply, send (the share sheet), then save at the far end.
    private func postActions(_ p: VibePostItem) -> some View {
        HStack(spacing: 2) {
            Button { like(p) } label: {
                actionIcon {
                    ZStack {
                        if p.liked {
                            WebFill(path: VibePostIcons.heart, size: 22)
                        }
                        WebIcon(path: VibePostIcons.heart, size: 22, line: 1.9)
                    }
                    .foregroundStyle(p.liked ? FAMColor.coral : FAMColor.text)
                }
            }
            .buttonStyle(.press)
            .accessibilityLabel(p.liked ? "Unlike" : "Like")
            .accessibilityAddTraits(p.liked ? .isSelected : [])
            Button { vp.openReplies(p.id, api: model.api) } label: {
                actionIcon { WebIcon(path: VibePostIcons.bubble, size: 22, line: 1.9) }
            }
            .buttonStyle(.press)
            .accessibilityLabel("Replies")
            Button { sharing = ShareSubject(key: p.key, title: p.shownTitle) } label: {
                actionIcon { WebIcon(path: VibePostIcons.send, size: 22, line: 1.9) }
            }
            .buttonStyle(.press)
            .accessibilityLabel("Send")
            Spacer(minLength: 0)
            Button { save(p) } label: {
                actionIcon { WebIcon(path: VibePostIcons.save, size: 22, line: 1.9) }
            }
            .buttonStyle(.press)
            .accessibilityLabel("Save for later")
        }
        .padding(.horizontal, 10)
        .padding(.top, 6)
    }

    private func actionIcon<Icon: View>(@ViewBuilder _ icon: () -> Icon) -> some View {
        icon()
            .foregroundStyle(FAMColor.text)
            .padding(6)
            .frame(minWidth: 36, minHeight: 36)
            .contentShape(Rectangle())
    }

    /// Likes and who heard it, the title, the caption, the top reply and
    /// "View all n replies" / "View the reply" / "Add a reply".
    private func postMeta(_ p: VibePostItem) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            if p.likes > 0 || p.heardCount > 0 {
                counts(p)
            }
            Text(p.shownTitle)
                .font(FAMFont.body(14, .semibold))
                .foregroundStyle(FAMColor.text)
                .fixedSize(horizontal: false, vertical: true)
            if !p.caption.isEmpty {
                said(whoWord, p.caption, explicit: false)
            }
            if let top = p.topReply {
                let h = top.handle ?? ""
                said(h.isEmpty ? (top.name ?? "") : h, top.text ?? "", explicit: top.explicit == true)
            }
            Button { vp.openReplies(p.id, api: model.api) } label: {
                Text(repliesWord(p.replies))
                    .font(FAMFont.body(13))
                    .foregroundStyle(FAMColor.textMuted)
                    .padding(.vertical, 2)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.press)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 16)
        .padding(.top, 2)
    }

    private func repliesWord(_ n: Int) -> String {
        if n > 1 { return "View all \(n) replies" }
        if n == 1 { return "View the reply" }
        return "Add a reply"
    }

    private func counts(_ p: VibePostItem) -> some View {
        var line = Text("")
        var any = false
        if p.likes > 0 {
            line = Text(p.likes == 1 ? "1 like" : "\(p.likes) likes").font(FAMFont.body(14, .semibold))
            any = true
        }
        if p.heardCount > 0 {
            let heard = "heard by \(p.heardCount) " + (p.heardCount == 1 ? "friend" : "friends")
            line = any ? line + Text(" \u{00B7} " + heard) : Text(heard)
        }
        return HStack(spacing: 8) {
            if !p.heardFaces.isEmpty {
                HStack(spacing: -7) {
                    ForEach(Array(p.heardFaces.enumerated()), id: \.offset) { item in
                        Face(name: item.element.name ?? "", handle: item.element.handle ?? "",
                             avatar: item.element.avatar ?? "", size: 20, initialsSize: 8)
                            .overlay(Circle().stroke(FAMColor.background, lineWidth: 2))
                    }
                }
            }
            line
                .font(FAMFont.body(14))
                .foregroundStyle(FAMColor.text)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// "<b>handle</b> words", the E before a flagged name.
    private func said(_ name: String, _ words: String, explicit: Bool) -> some View {
        let line = Text(name).font(FAMFont.body(14, .semibold)) + Text(" " + words)
        return HStack(alignment: .firstTextBaseline, spacing: 4) {
            if explicit { ExplicitBadge() }
            line
                .font(FAMFont.body(14))
                .foregroundStyle(FAMColor.text)
                .lineSpacing(2)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: Playing in place (`playVibePost`, `playVibePostLine`)

    private func play(_ p: VibePostItem) {
        if VibePostPlay.isCurrent(p, now: model.nowPlaying, player: model.player) {
            model.player.toggle()
            return
        }
        start(p, at: 0)
    }

    private func start(_ p: VibePostItem, at: Double) {
        var ep = Episode(key: p.key, title: p.shownTitle, surface: "other")
        ep.picture = p.thumb.isEmpty ? nil : model.api.picture(p.thumb)
        ep.inPlace = true
        model.play(ep, resumeAt: at)
    }

    /// The gold chip: the episode playing jumps to the line at once (never
    /// past what is written); one not playing starts and jumps once enough
    /// of it has arrived (`pendingResumeSeek`, here `resumeAt`).
    private func playLine(_ p: VibePostItem) {
        guard let start0 = p.lineAt else { return }
        let at = max(0, start0)
        let player = model.player
        if VibePostPlay.isCurrent(p, now: model.nowPlaying, player: player) {
            let phase = player.phase
            let active = phase == .playing || phase == .buffering || phase == .paused || phase == .finished
            if active || player.complete {
                player.seek(to: min(at, player.seekLimit))
                if phase == .paused || phase == .finished { player.resume() }
                return
            }
        }
        start(p, at: at)
    }

    // MARK: Like, save, the ⋯

    private var signedIn: Bool { model.listener?.authenticated == true }

    private func like(_ p: VibePostItem) {
        guard signedIn else {
            model.nowPlaying.say("Create a free account to like vibes.")
            return
        }
        let app = model
        vp.like(p.id, api: model.api, say: { app.nowPlaying.say($0) })
    }

    private func save(_ p: VibePostItem) {
        guard signedIn else {
            model.nowPlaying.say("Create a free account to save episodes.")
            return
        }
        let app = model
        let key = p.key
        let title = p.shownTitle
        Task {
            do {
                try await app.api.save(key, title: title, source: "vibe")
                app.nowPlaying.say("Saved for Later", action: "Open") { app.openSaved() }
            } catch {
                app.nowPlaying.say("Could not save that")
            }
        }
    }

    /// `openVibePostMenu`: Delete vibe on yours (the grid's own delete),
    /// Report vibe on a friend's; either takes only that post off.
    private func postMenu(_ p: VibePostItem) -> some View {
        var items: [YouSheetItem] = []
        if vp.mine {
            items.append(YouSheetItem(label: "Delete vibe", danger: true, icon: YouIcons.remove) {
                deleteVibe(p)
            })
        } else {
            items.append(YouSheetItem(label: "Report vibe", danger: true, icon: PlayerIcons.flag) {
                reporting = VibeReportAsk(subject: .vibe(id: p.id), postID: p.id)
            })
        }
        return YouSheet(headTitle: p.shownTitle, headSub: vp.mine ? "Your vibe" : "A vibe",
                        headThumb: p.thumb, items: items)
            .environmentObject(model)
    }

    /// `deleteMyVibe`: "Vibe deleted", or "Could not delete that vibe".
    private func deleteVibe(_ p: VibePostItem) {
        let app = model
        let vp = self.vp
        let id = p.id
        Task {
            let ok = await app.you.deleteVibe(query: p.query, minutes: p.minutes)
            if ok {
                app.nowPlaying.snack("Vibe deleted")
                vp.remove(id)
            } else {
                app.nowPlaying.say("Could not delete that vibe")
            }
        }
    }

    private func reportSheet(_ ask: VibeReportAsk) -> some View {
        let app = model
        let vp = self.vp
        let postID = ask.postID
        let isVibe = postID > 0
        return FAMReportSheet(api: model.safety, subject: ask.subject,
                              say: { app.nowPlaying.say($0) }) { receipt in
            guard receipt != nil else { return }
            if isVibe {
                vp.remove(postID)
            } else {
                Task { await vp.loadReplies(app.api) }
            }
        }
    }

    // MARK: Replies (`#vpReplies`)

    private var repliesLayer: some View {
        GeometryReader { geo in
            ZStack(alignment: .bottom) {
                Color(hex: 0x0E0C12).opacity(0.45)
                    .ignoresSafeArea()
                    .onTapGesture { close() }
                    .accessibilityHidden(true)
                repliesCard(height: geo.size.height)
            }
        }
    }

    private func close() {
        replyFocused = false
        vp.closeReplies()
    }

    private func repliesCard(height: CGFloat) -> some View {
        // `max-height: 78%`; the list as tall as what it holds, at least 120.
        let most = max(120, height * 0.78 - 130)
        let listTall = min(max(listHeight, 120), most)
        return VStack(spacing: 0) {
            HStack {
                Text("Replies")
                    .font(FAMFont.heading(16, .semibold))
                    .foregroundStyle(FAMColor.text)
                Spacer(minLength: 0)
                Button { close() } label: {
                    WebIcon(path: Icons.close, size: 16, line: 2.4)
                        .foregroundStyle(FAMColor.text)
                        .frame(minWidth: 36, minHeight: 36)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.press)
                .accessibilityLabel("Close")
            }
            .padding(.leading, 16)
            .padding(.trailing, 10)
            .padding(.top, 8)
            .padding(.bottom, 2)
            .overlay(alignment: .bottom) { Rectangle().fill(FAMColor.border).frame(height: 1) }
            ScrollView {
                repliesList
                    .background(GeometryReader { g in
                        Color.clear
                            .onAppear { listHeight = g.size.height }
                            .onChange(of: g.size.height) { _, h in listHeight = h }
                    })
            }
            .scrollBounceBehavior(.basedOnSize)
            .frame(height: listTall)
            replyBox
        }
        .background(FAMColor.background, in: UnevenRoundedRectangle(topLeadingRadius: 20, topTrailingRadius: 20))
        .background(alignment: .bottom) {
            // Under the home indicator too (`env(safe-area-inset-bottom)`).
            FAMColor.background.frame(height: 40).offset(y: 40).ignoresSafeArea(edges: .bottom)
        }
        .accessibilityAddTraits(.isModal)
    }

    @ViewBuilder private var repliesList: some View {
        VStack(alignment: .leading, spacing: 0) {
            if vp.repliesState == "failed" {
                SocialNote(text: "Could not load the replies.").padding(.horizontal, 16)
            } else if vp.repliesState != "ready" {
                SocialNote(text: "Loading\u{2026}").padding(.horizontal, 16)
            } else if vp.replies.isEmpty {
                SocialNote(text: "No replies yet." + (vp.repliesMine ? "" : " Say something about it."))
                    .padding(.horizontal, 16)
            } else {
                ForEach(vp.replies) { c in
                    replyRow(c, top: true)
                    ForEach(c.replies ?? []) { r in
                        replyRow(r, top: false)
                    }
                }
            }
        }
        .padding(.vertical, 6)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func replyRow(_ c: VibeReply, top: Bool) -> some View {
        let liking = vp.likes(of: c)
        let h = c.handle ?? ""
        let name = h.isEmpty ? (c.name ?? "") : h
        let age = VibePostPlay.dayLabel(Date().timeIntervalSince1970 - (c.at ?? 0))
        let head = Text(name).font(FAMFont.body(13.5, .semibold)).foregroundColor(FAMColor.text)
            + Text(" ") + Text(age).font(FAMFont.body(12)).foregroundColor(FAMColor.textMuted)
        return HStack(alignment: .top, spacing: 10) {
            Face(name: c.name ?? "", handle: h, avatar: c.avatar ?? "", size: 30, initialsSize: 11)
            VStack(alignment: .leading, spacing: 0) {
                HStack(alignment: .firstTextBaseline, spacing: 4) {
                    if c.explicit == true { ExplicitBadge() }
                    head.fixedSize(horizontal: false, vertical: true)
                }
                Text(c.text ?? "")
                    .font(FAMFont.body(13.5))
                    .foregroundStyle(FAMColor.text)
                    .lineSpacing(2)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 14) {
                    Button { replyTo(c.id) } label: { replyAct("Reply") }
                        .buttonStyle(.press)
                    Button { replyMenuFor = c } label: { replyAct("More") }
                        .buttonStyle(.press)
                }
                .padding(.top, 2)
            }
            Spacer(minLength: 0)
            Button { likeReply(c.id) } label: {
                VStack(spacing: 1) {
                    ZStack {
                        if liking.liked { WebFill(path: VibePostIcons.heart, size: 14) }
                        WebIcon(path: VibePostIcons.heart, size: 14, line: 2)
                    }
                    if liking.likes > 0 {
                        Text("\(liking.likes)").font(FAMFont.body(10))
                    }
                }
                .foregroundStyle(liking.liked ? FAMColor.coral : FAMColor.textMuted)
                .padding(4)
                .frame(minWidth: 32)
                .contentShape(Rectangle())
            }
            .buttonStyle(.press)
            .accessibilityLabel("Like")
            .accessibilityAddTraits(liking.liked ? .isSelected : [])
        }
        .padding(.leading, top ? 16 : 56)
        .padding(.trailing, 16)
        .padding(.vertical, 8)
    }

    private func replyAct(_ word: String) -> some View {
        Text(word)
            .font(FAMFont.body(12, .semibold))
            .foregroundStyle(FAMColor.textMuted)
            .padding(.vertical, 2)
            .contentShape(Rectangle())
    }

    private func replyTo(_ id: Int) {
        vp.replyTo(id)
        replyFocused = true
    }

    private func likeReply(_ id: Int) {
        guard signedIn else {
            model.nowPlaying.say("Create a free account to like replies.")
            return
        }
        let app = model
        vp.likeReply(id, api: model.api, say: { app.nowPlaying.say($0) })
    }

    @ViewBuilder private var replyBox: some View {
        VStack(alignment: .leading, spacing: 0) {
            if !signedIn {
                Text("Replying needs an account.")
                    .font(FAMFont.body(13))
                    .foregroundStyle(FAMColor.textMuted)
            } else {
                if vp.replyParent != 0 {
                    HStack(spacing: 4) {
                        Text("Replying to @" + vp.replyParentName)
                            .font(FAMFont.body(12))
                            .foregroundStyle(FAMColor.textMuted)
                        Button { vp.replyTo(0) } label: {
                            Text("Cancel")
                                .font(FAMFont.body(12, .semibold))
                                .foregroundStyle(FAMColor.text)
                        }
                        .buttonStyle(.press)
                    }
                    .padding(.bottom, 6)
                }
                HStack(spacing: 8) {
                    replyField
                    Button { sendReply() } label: {
                        Text("Post")
                            .font(FAMFont.body(14, .bold))
                            .foregroundStyle(FAMColor.copper)
                            .padding(.vertical, 6)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.press)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 16)
        .padding(.top, 10)
        .padding(.bottom, 14)
        .overlay(alignment: .top) { Rectangle().fill(FAMColor.border).frame(height: 1) }
    }

    private var replyField: some View {
        let text = Binding<String>(get: { vp.replyText }, set: { value in
            // `maxlength="500"`.
            vp.replyText = value.count > 500 ? String(value.prefix(500)) : value
        })
        return TextField("", text: text,
                         prompt: Text("Add a reply\u{2026}").foregroundColor(FAMColor.textFaint))
            .font(FAMFont.body(14))
            .foregroundStyle(FAMColor.text)
            .focused($replyFocused)
            .submitLabel(.send)
            .onSubmit { sendReply() }
            .padding(.horizontal, 14)
            .padding(.vertical, 9)
            .background(FAMColor.surface, in: Capsule())
            .overlay(Capsule().stroke(FAMColor.borderStrong, lineWidth: 1))
            .accessibilityLabel("Your reply")
    }

    private func sendReply() {
        let app = model
        vp.send(model.api, say: { app.nowPlaying.say($0) })
    }

    /// `openVibeReplyMenu`: Delete reply on yours or any on your own vibe;
    /// Report reply on anybody else's (coral only when it is alone).
    private func replyMenu(_ c: VibeReply) -> some View {
        var items: [YouSheetItem] = []
        let id = c.id
        let app = model
        let vp = self.vp
        if c.mine == true || vp.repliesMine {
            items.append(YouSheetItem(label: "Delete reply", danger: true, icon: YouIcons.remove) {
                vp.deleteReply(id, api: app.api, say: { app.nowPlaying.say($0) })
            })
        }
        if c.mine != true {
            let alone = items.isEmpty
            items.append(YouSheetItem(label: "Report reply", danger: alone, icon: PlayerIcons.flag) {
                reporting = VibeReportAsk(subject: .comment(id: id), postID: 0)
            })
        }
        return YouSheet(items: items)
            .environmentObject(model)
    }
}

// MARK: - A post's player (`.vp-tr`)

/// The play button, the bar with the line's stretch in gold, the times and
/// the gold chip that plays from the line. Painted from the one player
/// while the post's episode is the one loaded; every other post rests.
struct VibePostTransport: View {
    @EnvironmentObject private var player: StreamPlayer
    @EnvironmentObject private var now: NowPlayingModel
    let post: VibePostItem
    let play: () -> Void
    let playLine: () -> Void

    @State private var dragging = false
    @State private var dragValue: Double = 0
    /// "Nothing playing yet" was said for this touch.
    @State private var refused = false

    private var current: Bool { VibePostPlay.isCurrent(post, now: now, player: player) }

    /// The real length while its episode is the one playing, else its minutes.
    private var total: Double {
        if current && player.total > 0 { return player.total }
        return post.minutes * 60
    }

    var body: some View {
        let isCurrent = current
        let playing = isCurrent && player.playing
        let length = total
        let heard = isCurrent ? min(player.position, length) : 0
        let shown = dragging ? dragValue : heard
        return HStack(spacing: 10) {
            Button(action: play) {
                ZStack {
                    Circle().fill(FAMColor.copper)
                    WebFill(path: playing ? VibePostIcons.pause : VibePostIcons.play, size: 16)
                        .foregroundStyle(FAMColor.ink)
                        .offset(x: playing ? 0 : 1)
                }
                .frame(width: 34, height: 34)
                .contentShape(Circle())
            }
            .buttonStyle(.press)
            .accessibilityLabel(playing ? "Pause" : "Play")
            VStack(spacing: 4) {
                GeometryReader { geo in
                    track(width: geo.size.width, shown: shown, total: length, live: isCurrent)
                }
                .frame(height: 24)
                .padding(.vertical, -10)
                .accessibilityElement()
                .accessibilityLabel("Position")
                .accessibilityValue("\(clock(shown)) of \(clock(length))")
                HStack {
                    Text(clock(shown))
                    Spacer(minLength: 0)
                    Text(clock(length))
                }
                .font(FAMFont.mono(10))
                .monospacedDigit()
                .foregroundStyle(FAMColor.textMuted)
            }
            .padding(.top, 6)
            if let at = post.lineAt {
                Button(action: playLine) {
                    HStack(spacing: 4) {
                        WebFill(path: VibePostIcons.chipPlay, size: 10)
                        Text(clock(at)).font(FAMFont.mono(11))
                    }
                    .foregroundStyle(FAMColor.copper)
                    .padding(.horizontal, 9)
                    .padding(.vertical, 5)
                    .background(FAMColor.copper.opacity(0.18), in: Capsule())
                    .contentShape(Capsule())
                }
                .buttonStyle(.press)
                .accessibilityLabel("Play from the line")
            }
        }
        .padding(.horizontal, 16)
        .padding(.top, 10)
    }

    private func track(width: CGFloat, shown: Double, total: Double, live: Bool) -> some View {
        let fraction = total > 0 ? min(max(shown / total, 0), 1) : 0
        let x = width * CGFloat(fraction)
        let segment = lineSegment(width: width, total: total)
        return ZStack(alignment: .leading) {
            Capsule().fill(FAMColor.borderStrong).frame(height: 4)
            if let segment {
                RoundedRectangle(cornerRadius: 3)
                    .fill(FAMColor.copper.opacity(0.85))
                    .frame(width: segment.width, height: 8)
                    .offset(x: segment.x)
            }
            Capsule().fill(FAMColor.text).frame(width: x, height: 4)
            if live {
                Circle().fill(FAMColor.text).frame(width: 11, height: 11).offset(x: x - 5.5)
            }
        }
        .frame(width: width, height: 24)
        .contentShape(Rectangle())
        .gesture(scrub(width: width, total: total))
    }

    /// The line's stretch: from where it starts, as wide as the sentence
    /// would take to say at a reading pace, never past the end.
    private func lineSegment(width: CGFloat, total: Double) -> (x: CGFloat, width: CGFloat)? {
        guard let at = post.lineAt, total > 0 else { return nil }
        let left = min(98, (at / total) * 100)
        let spoken = (Double(post.lineText.utf16.count) / 15 / total) * 100
        let wide = max(2, min(100 - left, spoken))
        return (x: width * CGFloat(left / 100), width: width * CGFloat(wide / 100))
    }

    /// Dragging seeks - only the post that is playing, as the player's bar
    /// does, landing on release; a drag on any other post moves nothing.
    private func scrub(width: CGFloat, total: Double) -> some Gesture {
        DragGesture(minimumDistance: 0)
            .onChanged { value in
                guard current else { return }
                guard player.isLoaded, player.phase != .connecting, player.phase != .failed else {
                    if !refused {
                        refused = true
                        now.say("Nothing playing yet")
                    }
                    return
                }
                dragging = true
                let wanted = total * Double(min(max(value.location.x / max(width, 1), 0), 1))
                dragValue = min(wanted, player.seekLimit)
            }
            .onEnded { value in
                refused = false
                guard dragging else { return }
                let wanted = total * Double(min(max(value.location.x / max(width, 1), 0), 1))
                player.seek(to: dragValue)
                if wanted - dragValue > 1 && !player.complete {
                    now.say("Caught up with the writing \u{2014} it will keep going")
                }
                dragging = false
            }
    }
}

// MARK: - The posts' icons, path for path

enum VibePostIcons {
    /// Play (`M8 4.9v14.2L18.6 12z`), filled.
    static let play: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 8, y: 4.9)); p.addLine(to: CGPoint(x: 8, y: 19.1))
        p.addLine(to: CGPoint(x: 18.6, y: 12)); p.closeSubpath()
        return p
    }()

    /// Pause: two bars (`rect 6,4 4.4x16 rx1.5`, `rect 13.6,4`), filled.
    static let pause: Path = {
        var p = Path()
        p.addRoundedRect(in: CGRect(x: 6, y: 4, width: 4.4, height: 16), cornerSize: CGSize(width: 1.5, height: 1.5))
        p.addRoundedRect(in: CGRect(x: 13.6, y: 4, width: 4.4, height: 16), cornerSize: CGSize(width: 1.5, height: 1.5))
        return p
    }()

    /// The chip's play (`M7 4v16l13-8z`), filled.
    static let chipPlay: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 7, y: 4)); p.addLine(to: CGPoint(x: 7, y: 20))
        p.addLine(to: CGPoint(x: 20, y: 12)); p.closeSubpath()
        return p
    }()

    /// The heart (`M12 20s-7.5-4.6-7.5-10.2A4.2 4.2 0 0 1 12 7.3a4.2 4.2 0 0 1
    /// 7.5 2.5C19.5 15.4 12 20 12 20z`), its two arcs by their centres.
    static let heart: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 12, y: 20))
        p.addCurve(to: CGPoint(x: 4.5, y: 9.8), control1: CGPoint(x: 12, y: 20),
                   control2: CGPoint(x: 4.5, y: 15.4))
        p.addArc(center: CGPoint(x: 8.699, y: 9.897), radius: 4.2, startAngle: .degrees(-178.68),
                 endAngle: .degrees(-38.19), clockwise: false)
        p.addArc(center: CGPoint(x: 15.301, y: 9.897), radius: 4.2, startAngle: .degrees(-141.81),
                 endAngle: .degrees(-1.32), clockwise: false)
        p.addCurve(to: CGPoint(x: 12, y: 20), control1: CGPoint(x: 19.5, y: 15.4),
                   control2: CGPoint(x: 12, y: 20))
        p.closeSubpath()
        return p
    }()

    /// Reply: the speech bubble (`M4.5 12a7.5 7.5 0 1 1 3.3 6.2L4.5 19.5
    /// l1.1-3.4A7.5 7.5 0 0 1 4.5 12z`).
    static let bubble: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 4.5, y: 12))
        p.addArc(center: CGPoint(x: 12, y: 11.986), radius: 7.5, startAngle: .degrees(179.9),
                 endAngle: .degrees(484.06), clockwise: false)
        p.addLine(to: CGPoint(x: 4.5, y: 19.5))
        p.addLine(to: CGPoint(x: 5.6, y: 16.1))
        p.addArc(center: CGPoint(x: 11.998, y: 12.186), radius: 7.5, startAngle: .degrees(148.54),
                 endAngle: .degrees(181.42), clockwise: false)
        p.closeSubpath()
        return p
    }()

    /// Send (`M21 3 3 10.4l7.2 2.4L12.6 20 21 3z`, `M10.2 12.8 15 8`).
    static let send: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 21, y: 3)); p.addLine(to: CGPoint(x: 3, y: 10.4))
        p.addLine(to: CGPoint(x: 10.2, y: 12.8)); p.addLine(to: CGPoint(x: 12.6, y: 20))
        p.closeSubpath()
        p.move(to: CGPoint(x: 10.2, y: 12.8)); p.addLine(to: CGPoint(x: 15, y: 8))
        return p
    }()

    /// Save for later (`M6.5 3.5h11v17l-5.5-4-5.5 4z`).
    static let save: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 6.5, y: 3.5)); p.addLine(to: CGPoint(x: 17.5, y: 3.5))
        p.addLine(to: CGPoint(x: 17.5, y: 20.5)); p.addLine(to: CGPoint(x: 12, y: 16.5))
        p.addLine(to: CGPoint(x: 6.5, y: 20.5)); p.closeSubpath()
        return p
    }()
}
