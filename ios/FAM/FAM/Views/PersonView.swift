import FAMSafety
import SwiftUI

/// A friend's profile (`#screen-person`, shots 38-44): only what they chose
/// to publish (`op-friend-profile`). Drawn at once from what the caller knew
/// and the follow graph (standing, the id), then `/api/person` by handle -
/// which never carries an id, so Message and Follow use the graph's, and a
/// friend of a friend is followed by handle and the graph read again.
struct PersonView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var social: SocialModel
    let open: PersonOpen

    @State private var page: PersonPage?
    @State private var missing = false
    @State private var tab = 0
    @State private var allFriends = false
    @State private var menu = false
    @State private var reporting = false
    /// Block's sheet (`blockPerson`): the sentence, then "Block".
    @State private var confirm: SheetSpec?
    @State private var working = false
    /// Follow tapped and not yet answered: "✓ Following" at once, put
    /// back if the server says no (`Motion.follow`).
    @State private var flipped = false
    /// Unfollow tapped and not yet answered: "Follow" at once, one tap
    /// (`unfollowPerson`, the owner's decision 7).
    @State private var unflipped = false
    @State private var followedHandles: Set<String> = []
    @State private var addedMixes: [String: Bool] = [:]
    /// A mix of theirs already in your myFAM, asked about before it goes.
    @State private var removingMix: PersonMix?
    @State private var story: StoryReel?
    @AppStorage("storiesSeen") private var seenJSON = "{}"

    /// `.yf-person .yf-hero`'s wash, the top bar's colour at the top.
    private static let wash = FAMColor.wash

    private var handle: String { page?.handle ?? open.handle }
    private var name: String {
        if let n = page?.name, !n.isEmpty { return n }
        if !open.name.isEmpty { return open.name }
        if let n = social.person(handle: handle)?.name, !n.isEmpty { return n }
        return handle.isEmpty ? "Someone" : "@" + handle
    }
    private var avatar: String { page?.avatar ?? open.avatar }
    /// The graph's id: the one the caller had, else found by handle.
    private var userID: String {
        if !open.userID.isEmpty { return open.userID }
        return social.person(handle: handle)?.userId ?? ""
    }
    private var following: Bool { !userID.isEmpty && social.isFollowing(userID) }
    /// The E beside their name (§224): their profile says so, or - before
    /// it answers - the follow graph does (`pub.explicit || person.explicit`).
    private var explicit: Bool {
        if page?.explicit == true { return true }
        return social.person(handle: handle)?.explicit == true
    }

    var body: some View {
        presentations(screen)
    }

    private var screen: some View {
        VStack(spacing: 0) {
            topBar
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(spacing: 0) {
                        hero
                        if let page { stats(page, proxy: proxy) }
                        actions.padding(.top, 14)
                        interests
                        tabs.id("tabs")
                        grid
                        theirFriends.id("friends")
                        SocialNote(text: "Only what they chose to share. What someone listens to stays private.")
                            .padding(.top, 2)
                    }
                    .padding(.horizontal, 18)
                    .padding(.bottom, 24)
                    .background(alignment: .top) {
                        LinearGradient(colors: [Self.wash, FAMColor.background], startPoint: .top, endPoint: .bottom)
                            .frame(height: 360)
                            .padding(.horizontal, -18)
                    }
                }
            }
        }
        .background(FAMColor.background)
        .toolbar(.hidden, for: .navigationBar)
        .task { await load() }
    }

    /// The ⋯, Report, Block, Unfollow and the story, kept out of `body`.
    private func presentations<V: View>(_ content: V) -> some View {
        content
        .famSheet(isPresented: $menu) { menuSheet }
        .famSheet(isPresented: $reporting) {
            FAMReportSheet(api: model.safety, subject: .person(handle: handle),
                           say: { model.nowPlaying.say($0) }) { _ in }
        }
        .famSheet(item: $confirm) { spec in FAMActionSheet(spec: spec) }
        // `confirmRemoveMix`: the copy may have been renamed since, so ask.
        .confirmationDialog(removingMix.map { "Remove \($0.name ?? "this mix") from your myFAM?" } ?? "",
                            isPresented: Binding(get: { removingMix != nil },
                                                 set: { if !$0 { removingMix = nil } }),
                            titleVisibility: .visible) {
            if let m = removingMix {
                Button("Yes, remove it", role: .destructive) { toggleMix(m, added: true) }
                Button("Cancel", role: .cancel) {}
            }
        }
        .fullScreenCover(item: $story) { reel in
            StoryViewer(reel: reel, markSeen: markSeen, seenAt: { seen[$0] ?? 0 }, openMine: {
                // "Vibed to your story · View" after the VIBE! arrows.
                Task { @MainActor in
                    await model.loadCircle()
                    try? await Task.sleep(nanoseconds: 350_000_000)
                    if let mine = StoryReel.mine(model) { story = mine }
                }
            }, origin: reel.origin)
                .environmentObject(model)
        }
    }

    private func load() async {
        if social.graph == nil { await social.loadGraph() }
        if social.me == nil { Task { await social.loadMe() } }
        do {
            page = try await model.api.person(handle: open.handle, userID: open.userID)
            missing = false
        } catch {
            missing = true
        }
    }

    // MARK: - Top bar (`#personTop`)

    private var topBar: some View {
        HStack(spacing: 6) {
            SocialBackButton { social.back() }
            Spacer()
            if !userID.isEmpty || !handle.isEmpty {
                Button { menu = true } label: {
                    RoundIcon { WebFill(path: Icons.dots(1.8), size: 17) }
                }
                .buttonStyle(.press)
                .accessibilityLabel("More")
            }
        }
        .padding(.horizontal, 16)
        .padding(.top, 12)
        .padding(.bottom, 4)
        .background(Self.wash.ignoresSafeArea(edges: .top))
    }

    // MARK: - Hero

    private var hero: some View {
        VStack(spacing: 0) {
            photo
            HStack(spacing: 6) {
                Text(name)
                    .font(FAMFont.heading(24, .semibold))
                    .foregroundStyle(FAMColor.text)
                    .multilineTextAlignment(.center)
                if explicit { ExplicitBadge() }
            }
            .padding(.top, 12)
            if !placeLine.isEmpty {
                Text(placeLine)
                    .font(FAMFont.body(12))
                    .foregroundStyle(FAMColor.textMuted)
                    .multilineTextAlignment(.center)
                    .padding(.top, 5)
            }
            let standing = social.standing(userID)
            if !standing.isEmpty {
                Text(standing)
                    .font(FAMFont.mono(10))
                    .foregroundStyle(FAMColor.textMuted)
                    .padding(.top, 4)
            }
            if missing && page == nil && open.name.isEmpty {
                SocialNote(text: "No listener by that handle.")
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 8)
    }

    /// "Austin, TX, USA · @beth" - the place only when they gave one.
    private var placeLine: String {
        let place = page?.location ?? ""
        let at = handle.isEmpty ? "" : "@" + handle
        if place.isEmpty { return at }
        return at.isEmpty ? place : place + " \u{00B7} " + at
    }

    /// Their newest vibe is under 48 hours old: the story ring.
    private var ringed: Bool {
        guard let at = page?.vibes?.map({ $0.at ?? 0 }).max() else { return false }
        return Date().timeIntervalSince1970 - at < 48 * 3600
    }

    /// Their stories in the faces row, if they are there.
    private var circlePerson: CirclePerson? {
        model.circle?.circle.first { !handle.isEmpty && $0.handle.lowercased() == handle.lowercased() && !$0.stories.isEmpty }
    }

    @ViewBuilder private var photo: some View {
        if ringed {
            let face = Face(name: name, handle: handle, avatar: avatar, size: 86, initialsSize: 28)
                .padding(2)
                .overlay(Circle().stroke(FAMColor.copper, lineWidth: 2))
                .padding(2)
            if let person = circlePerson {
                Button { openStory(person) } label: { face }
                    .buttonStyle(.press)
                    .accessibilityLabel("\(name)'s story")
            } else {
                face
            }
        } else {
            Face(name: name, handle: handle, avatar: avatar, size: 92, initialsSize: 28)
        }
    }

    // MARK: - Stats, buttons, interests

    private func stats(_ page: PersonPage, proxy: ScrollViewProxy) -> some View {
        let friends = page.friendCount ?? 0
        let vibes = page.vibeCount ?? (page.vibes ?? []).count
        let mixes = (page.mixes ?? []).count
        return HStack(spacing: 0) {
            stat(friends, friends == 1 ? "friend" : "friends", enabled: page.friends != nil) {
                withAnimation { proxy.scrollTo("friends", anchor: .top) }
            }
            stat(vibes, vibes == 1 ? "vibe" : "vibes", enabled: true) {
                tab = 0
                withAnimation { proxy.scrollTo("tabs", anchor: .top) }
            }
            stat(mixes, mixes == 1 ? "mix" : "mixes", enabled: true) {
                tab = 1
                withAnimation { proxy.scrollTo("tabs", anchor: .top) }
            }
        }
        .padding(.top, 16)
    }

    /// A number and its word. Friends is a button only when their list is
    /// yours to see (§285); otherwise a plain number, never a greyed button.
    @ViewBuilder
    private func stat(_ n: Int, _ word: String, enabled: Bool, action: @escaping () -> Void) -> some View {
        if enabled {
            Button(action: action) { statLabel(n, word) }
                .buttonStyle(.press)
                .accessibilityElement(children: .combine)
        } else {
            statLabel(n, word)
                .accessibilityElement(children: .combine)
        }
    }

    private func statLabel(_ n: Int, _ word: String) -> some View {
        VStack(spacing: 2) {
            Text("\(n)")
                .font(FAMFont.heading(21, .semibold))
                .foregroundStyle(FAMColor.text)
            Text(word)
                .font(FAMFont.body(12))
                .foregroundStyle(FAMColor.textMuted)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 4)
        .contentShape(Rectangle())
    }

    /// Message (gold, friends only - §520) + Following / Follow; with no id - somebody reached
    /// through a friend's friends - only Follow, by handle.
    @ViewBuilder private var actions: some View {
        HStack(spacing: 7) {
            if !userID.isEmpty {
                // Message only between friends (§520); the server refuses
                // anyone else.
                if social.isFriend(userID) {
                Button { message() } label: {
                    HStack(spacing: 7) {
                        WebIcon(path: Icons.chat, size: 15)
                        Text("Message").font(FAMFont.body(12, .bold))
                    }
                    .foregroundStyle(FAMColor.ink)
                    .padding(.horizontal, 14)
                    .frame(minHeight: 34)
                    .background(FAMColor.copper, in: Capsule())
                }
                .buttonStyle(.press)
                }
                Button {
                    if shownFollowing { unfollow() } else { follow() }
                } label: {
                    HStack(spacing: 5) {
                        if flipped {
                            FollowTick()
                        }
                        Text(shownFollowing ? "Following" : "Follow")
                            .font(FAMFont.body(12, .medium))
                    }
                    .foregroundStyle(FAMColor.text)
                    .padding(.horizontal, 14)
                    .frame(minHeight: 34)
                    .overlay(Capsule().stroke(FAMColor.borderStrong))
                    .animation(.easeOut(duration: 0.26), value: flipped)
                }
                .buttonStyle(.press)
                .disabled(working)
            } else if !handle.isEmpty && !missing {
                Button { follow() } label: {
                    Text("Follow")
                        .font(FAMFont.body(12, .bold))
                        .foregroundStyle(FAMColor.ink)
                        .padding(.horizontal, 14)
                        .frame(minHeight: 34)
                        .background(FAMColor.copper, in: Capsule())
                }
                .buttonStyle(.press)
                .disabled(working)
            }
        }
        .frame(maxWidth: .infinity)
    }

    /// What the button says: the tap's answer at once, then the graph's.
    private var shownFollowing: Bool {
        if flipped { return true }
        if unflipped { return false }
        return following
    }

    @ViewBuilder private var interests: some View {
        let labels = page?.interestLabels ?? []
        if !labels.isEmpty {
            let common = social.inCommon(labels)
            VStack(alignment: .leading, spacing: 9) {
                Text("Interests").monoLabel(10, color: FAMColor.copper, tracking: 1.6)
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 7) {
                        ForEach(page?.topics ?? [], id: \.self) { topic in
                            InterestChipButton(topic: topic, height: 26, size: 11) { social.open(.topic($0)) }
                        }
                        if common > 0 {
                            InterestChip(label: "\(common) in common", common: true, height: 26, size: 11)
                        }
                    }
                    .padding(.vertical, 1)
                    .padding(.horizontal, 1)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.top, 16)
        }
    }

    // MARK: - Vibes | Mixes

    private var tabs: some View {
        HStack(spacing: 0) {
            tabButton("Vibes", 0)
            tabButton("Mixes", 1)
        }
        .overlay(alignment: .bottom) { Rectangle().fill(FAMColor.border).frame(height: 1) }
        .padding(.top, 20)
    }

    private func tabButton(_ title: String, _ which: Int) -> some View {
        let on = tab == which
        return Button { tab = which } label: {
            Text(title)
                .monoLabel(10.5, color: on ? FAMColor.text : FAMColor.textFaint, tracking: 1.89)
                .frame(maxWidth: .infinity)
                .frame(minHeight: 34)
                .overlay(alignment: .bottom) {
                    Rectangle().fill(on ? FAMColor.copper : Color.clear).frame(height: 2)
                }
                .contentShape(Rectangle())
        }
        .buttonStyle(.press)
        .accessibilityAddTraits(on ? .isSelected : [])
    }

    /// `.yf-pics`: three columns 3 apart, tiles at 3:4. Empty before the
    /// profile answers - no "nothing yet" claims until then.
    @ViewBuilder private var grid: some View {
        let columns = [GridItem(.flexible(), spacing: 3), GridItem(.flexible(), spacing: 3),
                       GridItem(.flexible(), spacing: 3)]
        if let page {
            if tab == 0 {
                let vibes = page.vibes ?? []
                if vibes.isEmpty {
                    SocialNote(text: "Nothing vibed yet.")
                } else {
                    LazyVGrid(columns: columns, spacing: 3) {
                        ForEach(vibes) { v in
                            Button { play(v) } label: {
                                PicTile(thumb: v.thumb ?? "", title: v.title ?? v.query)
                            }
                            .buttonStyle(.press)
                        }
                    }
                    .padding(.top, 3)
                }
            } else {
                let mixes = page.mixes ?? []
                if mixes.isEmpty {
                    SocialNote(text: "No public mixes yet.")
                } else {
                    LazyVGrid(columns: columns, spacing: 3) {
                        ForEach(mixes) { m in mixTile(m) }
                    }
                    .padding(.top, 3)
                }
            }
        }
    }

    /// A mix tile opens the public mix; its corner (+) adds it to your
    /// myFAM, ✓ once added (not on your own).
    private func mixTile(_ m: PersonMix) -> some View {
        let added = addedMixes[m.id] ?? (m.added == true)
        let cover = m.cover ?? ""
        let picture = cover.isEmpty ? (m.thumb ?? "") : cover
        return Button { openMix(m) } label: {
            PicTile(thumb: picture, title: m.name ?? "")
        }
        .buttonStyle(.press)
        .overlay(alignment: .topTrailing) {
            if m.mine != true {
                Button {
                    if added { removingMix = m } else { toggleMix(m, added: false) }
                } label: {
                    Group {
                        if added {
                            WebIcon(path: Icons.check, size: 12, line: 2.4).foregroundStyle(FAMColor.ink)
                        } else {
                            WebIcon(path: Icons.plus, size: 12, line: 2.4).foregroundStyle(FAMColor.copper)
                        }
                    }
                    // `.mix-add`: 28 pt, a dark glass behind the copper (+),
                    // copper with an ink ✓ once added (shot 39).
                    .frame(width: 28, height: 28)
                    .background(added ? FAMColor.copper : Color(hex: 0x0F0E12).opacity(0.55), in: Circle())
                    .overlay(Circle().stroke(FAMColor.copper))
                }
                .buttonStyle(.press)
                .padding(6)
                .accessibilityLabel(added ? "In your myFAM" : "Add to myFAM")
            }
        }
    }

    // MARK: - Their friends (only for a friend of theirs)

    @ViewBuilder private var theirFriends: some View {
        if let list = page?.friends {
            VStack(alignment: .leading, spacing: 0) {
                Text("Friends \u{00B7} \(page?.friendCount ?? list.count)")
                    .monoLabel(10, color: FAMColor.copper, tracking: 1.6)
                if list.isEmpty {
                    SocialNote(text: "No friends to show yet.")
                } else {
                    let shown = allFriends ? list : Array(list.prefix(4))
                    ForEach(shown, id: \.handle) { f in friendRow(f) }
                    if list.count > 4 {
                        Button { allFriends.toggle() } label: {
                            Text(allFriends ? "Show less" : "View more")
                                .font(FAMFont.body(12.5))
                                .foregroundStyle(FAMColor.text)
                                .frame(maxWidth: .infinity)
                                .frame(minHeight: 40)
                                .overlay(Capsule().stroke(FAMColor.borderStrong))
                        }
                        .buttonStyle(.press)
                        .padding(.top, 4)
                    }
                }
            }
            .padding(.top, 20)
            .padding(.bottom, 12)
        }
    }

    private func friendRow(_ f: PersonFriend) -> some View {
        let isFollowing = f.following == true || followedHandles.contains(f.handle)
        return HStack(spacing: 10) {
            Button { social.openPerson(handle: f.handle, name: f.name ?? "", avatar: f.avatar ?? "") } label: {
                HStack(spacing: 10) {
                    Face(name: f.name ?? "", handle: f.handle, avatar: f.avatar ?? "", size: 34, initialsSize: 12)
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 5) {
                            Text((f.name ?? "").isEmpty ? "@" + f.handle : (f.name ?? ""))
                                .font(FAMFont.body(13, .medium))
                                .foregroundStyle(FAMColor.text)
                                .lineLimit(1)
                            // The E beside a name that swears (§224).
                            if f.explicit == true { ExplicitBadge() }
                        }
                        Text("@" + f.handle).monoLabel(8.5, tracking: 0.34)
                    }
                    Spacer(minLength: 0)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.press)
            if isFollowing {
                Text("Following").monoLabel(9.5, tracking: 0.38)
            } else {
                Button { followFriend(f) } label: {
                    Text("Follow")
                        .font(FAMFont.body(11, .bold))
                        .foregroundStyle(FAMColor.ink)
                        .padding(.horizontal, 12)
                        .frame(minHeight: 28)
                        .background(FAMColor.copper, in: Capsule())
                }
                .buttonStyle(.press)
            }
        }
        .padding(.vertical, 8)
        .overlay(alignment: .bottom) { Rectangle().fill(FAMColor.border).frame(height: 1) }
    }

    // MARK: - The ⋯ (`openPersonMenu`, shot 42)

    private var menuSheet: some View {
        var actions: [SheetAction] = []
        if !userID.isEmpty {
            if following {
                actions.append(SheetAction(label: "Unfollow", danger: true) { unfollow() })
            } else {
                actions.append(SheetAction(label: "Follow") { follow() })
            }
            actions.append(SheetAction(label: "Your friends") { social.open(.friends) })
        }
        if !handle.isEmpty {
            actions.append(SheetAction(label: "Report", danger: true) { reporting = true })
            actions.append(SheetAction(label: "Block", danger: true) { askBlock() })
        }
        return FAMActionSheet(spec: SheetSpec(actions: actions))
    }

    // MARK: - Actions

    private func message() {
        guard !userID.isEmpty else { return }
        social.open(.thread(ChatOpen(with: userID, name: name, handle: handle, avatar: avatar,
                                     explicit: page?.explicit ?? false)))
    }

    /// By the graph's id, or by handle for someone with none on this side
    /// - then the graph is read again, which is what brings Message.
    private func follow() {
        working = true
        let id = userID
        let h = handle
        if !id.isEmpty { flipped = true }
        Task {
            await social.follow(userID: id, handle: id.isEmpty ? h : "")
            flipped = false
            working = false
        }
    }

    /// One tap (`unfollowPerson`): "Follow" at once, put back if refused.
    private func unfollow() {
        let id = userID
        guard !id.isEmpty else { return }
        working = true
        unflipped = true
        Task {
            await social.unfollow(id)
            unflipped = false
            working = false
        }
    }

    /// `blockPerson(handle, name, goBack)`: the web's sheet, then "Blocked",
    /// the graph and the inbox re-read, and back.
    private func askBlock() {
        let app = model
        let social = self.social
        confirm = FAMBlock.sheet(name: page?.name ?? open.name, handle: handle, model: model) {
            app.nowPlaying.say("Blocked")
            Task {
                await social.loadGraph()
                await social.loadInbox()
            }
            social.back()
        }
    }

    private func followFriend(_ f: PersonFriend) {
        followedHandles.insert(f.handle)
        Task {
            let ok = await social.follow(handle: f.handle)
            if !ok { followedHandles.remove(f.handle) }
        }
    }

    /// A vibe opens as their posts at the one tapped (§547,
    /// `playPersonVibe`); without a handle to ask by - or for somebody
    /// without an account - it plays as the story's Play does, a cover
    /// closing first so the loading screen and the player are not hidden.
    private func play(_ v: PersonVibe) {
        let vibes = page?.vibes ?? []
        let index = vibes.firstIndex(of: v) ?? 0
        if !handle.isEmpty && model.listener?.authenticated == true {
            social.open(.vibePosts(VibePostsOpen(handle: handle, index: index, fromYou: false)))
            return
        }
        let ep = Episode(key: EpisodeKey(query: v.query, minutes: v.minutes ?? FAMAPI.browseMinutes),
                         title: v.title ?? v.query, surface: "other")
        let app = model
        social.closeCover { app.play(ep) }
    }

    private func openMix(_ m: PersonMix) {
        let app = model
        let id = m.id
        let mine = m.mine == true
        social.closeCover {
            if mine { app.openMix(id) } else { app.openPublicMix(id) }
        }
    }

    /// `addMixFrom` / `removeMixCopy`: "Added to your myFAM as <name>"
    /// with Open, or "Removed from your myFAM"; a refusal in the server's
    /// words.
    private func toggleMix(_ m: PersonMix, added: Bool) {
        addedMixes[m.id] = !added
        let app = model
        let social = self.social
        Task {
            do {
                if added {
                    try await model.api.removePublicMix(m.id)
                    model.nowPlaying.say("Removed from your myFAM")
                } else {
                    let copy = try await model.api.addPublicMix(m.id)
                    let copyID = copy.id
                    model.nowPlaying.say("Added to your myFAM as \(copy.name)", action: "Open") {
                        social.closeCover { app.openMix(copyID) }
                    }
                }
                await model.mixes.load()
            } catch {
                addedMixes[m.id] = added
                let fallback = added ? "Could not remove that mix" : "Could not add that mix"
                model.nowPlaying.say(SocialModel.sentence(error, else: fallback))
            }
        }
    }

    // MARK: - Their story (the ring)

    private var seen: [String: Double] {
        (try? JSONDecoder().decode([String: Double].self, from: Data(seenJSON.utf8))) ?? [:]
    }

    private func markSeen(_ id: String, _ at: Double) {
        var map = seen
        map[id] = max(map[id] ?? 0, at)
        if let data = try? JSONEncoder().encode(map) { seenJSON = String(decoding: data, as: UTF8.self) }
    }

    private func openStory(_ p: CirclePerson) {
        var reel = StoryReel(people: [StoryPerson(id: p.userId, name: p.name, handle: p.handle, avatar: p.avatar,
                                                  stories: p.stories, mine: false)], start: 0)
        presentGrowing(reel: &reel) { story = $0 }
    }
}

/// A profile tile (`.yf-pic`): the picture filling a 3:4 tile (or the
/// cream wash), a dark fade at the foot and the title in Geist 10.5 on it.
struct PicTile: View {
    @EnvironmentObject private var model: AppModel
    let thumb: String
    let title: String
    /// A friend's grid cuts a title at four lines; your own never does
    /// (`.yf-pic-t`, spec §3.1).
    var titleLines: Int? = 4

    var body: some View {
        Color.clear
            .aspectRatio(3.0 / 4.0, contentMode: .fit)
            .background {
                ZStack {
                    LinearGradient(colors: [FAMColor.surface2, FAMColor.surface],
                                   startPoint: .topLeading, endPoint: .bottomTrailing)
                    if let url = picture {
                        AsyncImage(url: url) { $0.resizable().scaledToFill() } placeholder: { Color.clear }
                    }
                }
            }
            .overlay {
                // `.yf-pic::after`: transparent to 25%, then rgba(15,14,18,.88).
                LinearGradient(stops: [.init(color: .clear, location: 0.25),
                                       .init(color: Color(hex: 0x0F0E12).opacity(0.88), location: 1)],
                               startPoint: .top, endPoint: .bottom)
            }
            .overlay(alignment: .bottomLeading) {
                Text(title)
                    .font(FAMFont.body(10.5, .semibold))
                    .foregroundStyle(Color(hex: 0xF4EFE4))
                    .lineSpacing(2)
                    .lineLimit(titleLines)
                    .multilineTextAlignment(.leading)
                    .padding(7)
            }
            .clipped()
            .contentShape(Rectangle())
    }

    private var picture: URL? {
        if thumb.hasPrefix("data:") { return URL(string: thumb) }
        return model.api.picture(thumb)
    }
}

