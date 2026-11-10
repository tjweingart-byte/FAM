import FAMSafety
import SwiftUI

/// YourFAM, the listener's own profile (`#screen-profile`, `renderProfile`,
/// shots 04-12): the wordmark and the gear, the hero (your face, the name,
/// place · @handle), friends / vibes / mixes, Edit profile and Saved for
/// Later, the interests line the server chose (`op-profile-hub`), and the
/// Vibes / Mixes grid - a vibe held for half a second is deleted whole
/// (`vibe-caption`). Settings and the shelves are pushed on this tab's
/// stack; Edit profile comes up over it and goes back to it.
struct YouView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var social: SocialModel
    @EnvironmentObject private var you: YouModel

    @State private var editing = false
    @State private var held: MyVibe?
    @State private var story: StoryReel?
    /// The note composer, from your picture or its (+) (§297).
    @State private var composing: StoryEditorSubject?
    @AppStorage("storiesSeen") private var seenJSON = "{}"

    /// The top strip's wash (`.screen-top`: copper 24% over the page), in
    /// either theme.
    static let wash = FAMColor.wash

    var body: some View {
        NavigationStack(path: $model.youPath) {
            hub
                .toolbar(.hidden, for: .navigationBar)
                .navigationDestination(for: YouRoute.self) { route in
                    YouScreen(route: route)
                        .toolbar(.hidden, for: .navigationBar)
                }
        }
        .fullScreenCover(isPresented: $editing) {
            IdentityView(mode: .edit).famObjects(model)
        }
        .sheet(item: $held) { v in holdSheet(v) }
        .fullScreenCover(item: $story) { reel in
            StoryViewer(reel: reel, markSeen: markSeen, seenAt: { seen[$0] ?? 0 },
                        openMine: { showMine() })
                .environmentObject(model)
        }
        .fullScreenCover(item: $composing) { subject in
            StoryEditor(subject: subject, openMine: { showMine() })
                .environmentObject(model)
        }
        .task(id: model.listener?.userId) {
            await you.loadProfile()
            await social.loadGraph()
            social.checkNewFollowers()
            // A mix on this page opens its own page when it is yours: the
            // mixes are no longer asked at launch, so if myFAM has not been
            // warmed or shown yet, they are asked now (a faster start).
            if model.mixes.state != .ready { await model.mixes.load() }
        }
    }

    // MARK: - The page

    private var hub: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(spacing: 0) {
                    header
                    content(proxy)
                }
                .padding(.horizontal, 18)
                .padding(.top, 2)
                .padding(.bottom, 18)
                .background(alignment: .top) {
                    // Attached to the content, so the wash scrolls with it.
                    LinearGradient(colors: [Self.wash, FAMColor.background], startPoint: .top, endPoint: .bottom)
                        .frame(height: 290)
                }
            }
            .refreshable { await you.loadProfile() }
            // The strip under the clock is opaque (`.screen-top`): nothing
            // scrolls under it.
            .clipped()
        }
        .background(FAMColor.background, ignoresSafeAreaEdges: [])
        .background(Self.wash)
    }

    @ViewBuilder private func content(_ proxy: ScrollViewProxy) -> some View {
        if let p = you.profile {
            hero(p)
            stats(p, proxy: proxy)
            buttons(p)
            interests(p)
            tabs.id("shelf")
            grid(p)
        } else if you.profileState == .failed {
            // The web clears the header and offers nothing (Q2): here the
            // gear stays and the page can be asked again.
            SocialNote(text: "Your profile could not load. Nothing is lost \u{2014} try again in a moment.") {
                Button("Try again") { Task { await you.loadProfile() } }
                    .font(FAMFont.body(12, .bold))
                    .foregroundStyle(FAMColor.copper)
                    .buttonStyle(.press)
            }
            .padding(.top, 2)
        }
    }

    /// `.yf-head`: the wordmark centred, the 36 pt gear on the right; no (+)
    /// (`friends-vibes-first`).
    private var header: some View {
        ZStack {
            Wordmark(lead: "your", size: 28)
            HStack {
                Spacer()
                Button { model.youPath.append(.settings) } label: {
                    RoundIcon { WebIcon(path: YouIcons.gear, size: 17) }
                }
                .buttonStyle(.press)
                .accessibilityLabel("Settings")
            }
        }
        .frame(minHeight: 44)
        .padding(.top, 6)
        .padding(.bottom, 2)
    }

    // MARK: - Hero

    private func hero(_ p: MyProfile) -> some View {
        VStack(spacing: 0) {
            photo(p)
            // Both wrap, centred, rather than being cut (`large-text`).
            Text(nameLine(p))
                .font(FAMFont.heading(24, .semibold))
                .foregroundStyle(FAMColor.text)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 12)
            Text(placeLine(p))
                .font(FAMFont.body(12))
                .foregroundStyle(FAMColor.textMuted)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 5)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 8)
    }

    private func nameLine(_ p: MyProfile) -> String {
        let n = p.name ?? ""
        return n.isEmpty ? "Set up your profile" : n
    }

    /// "Philadelphia, PA, USA · @maya", "@maya", "… · no handle yet".
    private func placeLine(_ p: MyProfile) -> String {
        let place = p.location ?? ""
        let h = p.handle ?? ""
        let at = h.isEmpty ? "no handle yet" : "@" + h
        return place.isEmpty ? at : place + " \u{00B7} " + at
    }

    /// Your face: with a story up, the ring (copper while any of it is
    /// unseen, faint once seen) and a tap opens it; with none, a tap opens
    /// the note composer. The (+) badge - a button of its own beside the
    /// face, never inside it - always adds to your story (§297, account
    /// only). The picture is changed in Edit profile and Settings (Q4).
    private func photo(_ p: MyProfile) -> some View {
        let mine = p.stories ?? []
        let name = p.name ?? ""
        let handle = p.handle ?? ""
        let avatar = p.avatar ?? ""
        let account = model.listener?.authenticated == true
        return ZStack(alignment: .bottomTrailing) {
            if mine.isEmpty {
                Button { compose() } label: {
                    Face(name: name, handle: handle, avatar: avatar, size: 92, initialsSize: 28)
                }
                .buttonStyle(.press)
                .disabled(!account)
                .accessibilityLabel("Add to your story")
            } else {
                Button { openMyStory(p) } label: {
                    ZStack {
                        Circle().strokeBorder(ringColor(mine), lineWidth: 2)
                        Face(name: name, handle: handle, avatar: avatar, size: 86, initialsSize: 28)
                    }
                    .frame(width: 94, height: 94)
                }
                .buttonStyle(.press)
                .accessibilityLabel("Your story")
            }
            if account {
                // `.yf-hero .yf-add`: 24 pt, the text colour, a 3 pt border
                // of the page, at the picture's bottom right.
                Button { compose() } label: {
                    ZStack {
                        Circle().fill(FAMColor.background).frame(width: 30, height: 30)
                        Circle().fill(FAMColor.text).frame(width: 24, height: 24)
                        WebIcon(path: Icons.plus, size: 11).foregroundStyle(FAMColor.background)
                    }
                    .contentShape(Circle())
                }
                .buttonStyle(.press)
                .accessibilityLabel("Add to your story")
            }
        }
    }

    private func compose() {
        guard model.listener?.authenticated == true else { return }
        composing = .note()
    }

    /// The snack's View after posting: your story, read again.
    private func showMine() {
        Task { @MainActor in
            await model.loadCircle()
            await you.loadProfile()
            try? await Task.sleep(nanoseconds: 350_000_000)
            if let p = you.profile { openMyStory(p) }
        }
    }

    private func ringColor(_ stories: [Story]) -> Color {
        let last = stories.map(\.at).max() ?? 0
        return last > (seen["me"] ?? 0) ? FAMColor.copper : FAMColor.textFaint
    }

    // MARK: - Stats

    private func stats(_ p: MyProfile, proxy: ScrollViewProxy) -> some View {
        let friends = p.follows?.friends ?? 0
        let vibes = p.echoCount ?? (p.echoes ?? []).count
        let mixes = (p.mixes ?? []).count
        let fresh = social.graph?.newFollowers?.count ?? 0
        return HStack(spacing: 0) {
            stat(friends, friends == 1 ? "friend" : "friends", badge: fresh) {
                social.open(.friends)
            }
            stat(vibes, vibes == 1 ? "vibe" : "vibes", badge: 0) {
                you.shelfTab = 0
                withAnimation { proxy.scrollTo("shelf", anchor: .top) }
            }
            stat(mixes, mixes == 1 ? "mix" : "mixes", badge: 0) {
                you.shelfTab = 1
                withAnimation { proxy.scrollTo("shelf", anchor: .top) }
            }
        }
        .padding(.top, 16)
    }

    /// A number (Bricolage 21 semibold) over its word (Geist 11 muted).
    /// Friends carries the new-followers count, cleared by opening Friends
    /// (Q10: the web fetches it and draws nothing).
    private func stat(_ n: Int, _ word: String, badge: Int, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            VStack(spacing: 2) {
                Text("\(n)")
                    .font(FAMFont.heading(21, .semibold))
                    .foregroundStyle(FAMColor.text)
                    .overlay(alignment: .topTrailing) {
                        if badge > 0 {
                            TabBadge(count: badge).offset(x: 14, y: -2)
                        }
                    }
                Text(word)
                    .font(FAMFont.body(11))
                    .foregroundStyle(FAMColor.textMuted)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 4)
            .contentShape(Rectangle())
        }
        .buttonStyle(.press)
        .accessibilityElement(children: .combine)
        .accessibilityHint(badge > 0 ? "\(badge) new" : "")
    }

    // MARK: - Edit profile, Saved for Later

    private func buttons(_ p: MyProfile) -> some View {
        let unnamed = (p.name ?? "").isEmpty
        return HStack(spacing: 7) {
            Button { editing = true } label: {
                Text(unnamed ? "Set up profile" : "Edit profile")
                    .font(FAMFont.body(12, .medium))
                    .foregroundStyle(FAMColor.text)
                    .padding(.horizontal, 14)
                    .frame(minHeight: 34)
                    .overlay(Capsule().stroke(FAMColor.borderStrong))
                    .contentShape(Capsule())
            }
            .buttonStyle(.press)
            Button { model.youPath.append(.saved) } label: {
                HStack(spacing: 7) {
                    WebIcon(path: YouIcons.bookmark, size: 14)
                    Text("Saved for Later").font(FAMFont.body(12, .bold))
                }
                .foregroundStyle(FAMColor.ink)
                .padding(.horizontal, 14)
                .frame(minHeight: 34)
                .background(FAMColor.paper, in: Capsule())
                .contentShape(Capsule())
            }
            .buttonStyle(.press)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 14)
    }

    // MARK: - Interests (`interests_shown`, at most `interests_max`)

    /// One line that scrolls sideways, cut at the edge on purpose; the
    /// server chose them and the order. Each chip opens its Topic screen
    /// (`openInterestAt`).
    private func interests(_ p: MyProfile) -> some View {
        let limit = max(1, p.interestsMax ?? 5)
        let shown = Array((p.interestsShown ?? []).prefix(limit))
        return VStack(alignment: .leading, spacing: 9) {
            Text("Interests").monoLabel(10, color: FAMColor.copper, tracking: 1.6)
            if shown.isEmpty {
                Button { editing = true } label: {
                    Text("Add interests in Edit profile")
                        .font(FAMFont.body(11))
                        .italic()
                        .foregroundStyle(FAMColor.textMuted)
                        .padding(.horizontal, 11)
                        .frame(minHeight: 26)
                        .overlay(Capsule().stroke(FAMColor.borderStrong))
                        .contentShape(Capsule())
                }
                .buttonStyle(.press)
            } else {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 7) {
                        ForEach(shown, id: \.id) { i in
                            InterestChipButton(topic: TopicOpen(id: i.id, label: i.label ?? i.id),
                                               height: 26, size: 11) { model.youPath.append(.topic($0)) }
                        }
                    }
                    .padding(.vertical, 1)
                    .padding(.horizontal, 1)
                    .padding(.bottom, 2)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.top, 16)
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
        let on = you.shelfTab == which
        return Button { you.shelfTab = which } label: {
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

    private var columns: [GridItem] {
        [GridItem(.flexible(), spacing: 3), GridItem(.flexible(), spacing: 3), GridItem(.flexible(), spacing: 3)]
    }

    @ViewBuilder private func grid(_ p: MyProfile) -> some View {
        if you.shelfTab == 0 {
            vibesGrid(p)
        } else {
            mixesGrid(p)
        }
    }

    @ViewBuilder private func vibesGrid(_ p: MyProfile) -> some View {
        let vibes = p.echoes ?? []
        let total = p.echoCount ?? vibes.count
        if vibes.isEmpty {
            SocialNote(text: "Nothing vibed yet. Tap VIBE! on any episode and it shows here.")
                .padding(.top, 8)
        } else {
            LazyVGrid(columns: columns, spacing: 3) {
                ForEach(vibes) { v in vibeTile(v) }
                if total > vibes.count {
                    Button { model.youPath.append(.myVibe) } label: {
                        Color.clear
                            .aspectRatio(3.0 / 4.0, contentMode: .fit)
                            .background(LinearGradient(colors: [FAMColor.surface2, FAMColor.surface],
                                                       startPoint: .topLeading, endPoint: .bottomTrailing))
                            .overlay {
                                Text("See all")
                                    .font(FAMFont.body(12))
                                    .foregroundStyle(FAMColor.text)
                            }
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.press)
                }
            }
            .padding(.top, 3)
        }
    }

    /// A tap plays it; held still for half a second (a move of more than
    /// 10 pt cancels) it offers "Remove vibe from profile" (`armVibeHolds`).
    private func vibeTile(_ v: MyVibe) -> some View {
        PicTile(thumb: v.thumb ?? "", title: v.title ?? v.query, titleLines: nil)
            .contentShape(Rectangle())
            .onTapGesture {
                model.playOwnVibe(query: v.query, minutes: v.minutes, title: v.title)
            }
            .onLongPressGesture(minimumDuration: 0.5, maximumDistance: 10) {
                UIImpactFeedbackGenerator(style: .light).impactOccurred()
                held = v
            }
            .accessibilityElement(children: .combine)
            .accessibilityAddTraits(.isButton)
            .accessibilityAction(named: "Remove vibe from profile") { held = v }
    }

    @ViewBuilder private func mixesGrid(_ p: MyProfile) -> some View {
        let mixes = p.mixes ?? []
        if mixes.isEmpty {
            SocialNote(text: "No public mixes yet. A new DailyFAM mix is public unless you switch it off.")
                .padding(.top, 8)
        } else {
            LazyVGrid(columns: columns, spacing: 3) {
                ForEach(mixes) { m in
                    Button { openMix(m) } label: {
                        PicTile(thumb: mixPicture(m), title: m.name ?? "", titleLines: nil)
                    }
                    .buttonStyle(.press)
                }
            }
            .padding(.top, 3)
        }
    }

    private func mixPicture(_ m: ProfileMix) -> String {
        let cover = m.cover ?? ""
        return cover.isEmpty ? (m.thumb ?? "") : cover
    }

    /// `openProfileMix`: your own mix opens its page; otherwise its public page.
    private func openMix(_ m: ProfileMix) {
        if model.mixes.mixes.contains(where: { $0.id == m.id }) {
            model.openMix(m.id)
        } else {
            model.openPublicMix(m.id)
        }
    }

    // MARK: - The hold sheet

    private func holdSheet(_ v: MyVibe) -> some View {
        let item = YouSheetItem(label: "Remove vibe from profile", danger: true, icon: YouIcons.remove) {
            remove(v)
        }
        return YouSheet(headTitle: v.title ?? v.query, headSub: "Your vibe", headThumb: v.thumb ?? "",
                        items: [item])
            .environmentObject(model)
    }

    private func remove(_ v: MyVibe) {
        let you = self.you
        Task {
            let ok = await you.deleteVibe(query: v.query, minutes: v.minutes ?? FAMAPI.browseMinutes)
            if ok {
                model.nowPlaying.say("Vibe deleted")
            } else {
                model.nowPlaying.say("Could not delete that vibe")
            }
        }
    }

    // MARK: - Your story

    private var seen: [String: Double] {
        (try? JSONDecoder().decode([String: Double].self, from: Data(seenJSON.utf8))) ?? [:]
    }

    private func markSeen(_ id: String, _ at: Double) {
        var map = seen
        map[id] = max(map[id] ?? 0, at)
        if let data = try? JSONEncoder().encode(map) { seenJSON = String(decoding: data, as: UTF8.self) }
    }

    private func openMyStory(_ p: MyProfile) {
        let mine = p.stories ?? []
        guard !mine.isEmpty else { return }
        story = StoryReel(people: [StoryPerson(id: "me", name: "Your story", handle: p.handle ?? "",
                                               avatar: p.avatar ?? "", stories: mine, mine: true)],
                          start: 0)
    }
}

/// A screen on the YourFAM stack.
struct YouScreen: View {
    let route: YouRoute

    var body: some View {
        switch route {
        case .settings: SettingsView()
        case .saved: SavedView()
        case .history: HistoryView()
        case .myVibe: MyVibeView()
        case .signInMethods: SignInMethodsView()
        case .topic(let open): TopicView(open: open)
        }
    }
}
