import BackgroundTasks
import FAMSafety
import GoogleSignIn
import SwiftUI

@main
struct FAMApp: App {
    @StateObject private var model = AppModel()
    /// Settings' "Dark mode", on this device only (the web's `fam.theme`):
    /// dark unless it says light (§544).
    @AppStorage(FAMTheme.key) private var theme = FAMTheme.standard

    init() {
        // A proper picture cache (a faster start): the cards' pictures
        // (`/api/thumb/…`, `immutable` for a year) come off the disk on
        // the next launch instead of the network. Set before any request,
        // so `URLSession.shared` - AsyncImage's - uses it.
        URLCache.shared = URLCache(memoryCapacity: 50 * 1024 * 1024,
                                   diskCapacity: 300 * 1024 * 1024,
                                   directory: nil)
        // The Keychain's bearer token is the only identity (`listener-header`):
        // no session cookie is ever stored, by any session - the API's, the
        // player's, AsyncImage's - and any an older build kept is dropped.
        FAMAPI.forgetCookies()
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
                .environmentObject(model.player)
                .environmentObject(model.nowPlaying)
                .environmentObject(model.mixes)
                .environmentObject(model.social)
                .environmentObject(model.you)
                .tint(FAMColor.copper)
                // Every colour resolves in the theme this device chose,
                // whatever the window's scheme is...
                .famThemed()
                // ...and the window's - the status bar, the keyboard, the
                // system's sheets - is that theme, or dark over private
                // search's charcoal while that screen shows, which holds in
                // either theme (`#screen-home.private-search`).
                .preferredColorScheme(model.darkChrome ? .dark : FAMTheme.scheme(theme))
                .task { await model.open() }
                // Google's sign-in comes back to the app through its URL scheme;
                // any other link may carry a waitlist invite code.
                .onOpenURL { url in
                    if !GIDSignIn.sharedInstance.handle(url) {
                        model.noteReferral(url)
                        // A shared /s/, /m/ or /j/ link (decision 5).
                        model.openLink(url)
                    }
                }
                // A universal link (`applinks:familiarize.net`) may come as
                // a web activity rather than a URL.
                .onContinueUserActivity(NSUserActivityTypeBrowsingWeb) { activity in
                    guard let url = activity.webpageURL else { return }
                    model.noteReferral(url)
                    model.openLink(url)
                }
        }
        // The remembered DailyFAM page, refreshed while the app is closed
        // (`FeedRefresh`, iOS only): registered here, before launch ends,
        // and asked for each time the app goes to the background.
        .backgroundTask(.appRefresh(FeedRefresh.identifier)) {
            await FeedRefresh.run()
        }
    }
}

/// Which screen the app is on is the server's answer to "who is this":
/// signed out, waiting for a place, or in.
struct RootView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.scenePhase) private var scenePhase
    /// The app went to the background since it was last active: a return
    /// passes through `.inactive`, so `old == .background` is never seen.
    @State private var wentAway = false

    /// The front door's own toasts (the tabs and the first run draw theirs).
    private var rootToasts: Bool {
        switch model.stage {
        case .opening, .signedOut: return true
        case .waiting: return model.entry != .identity
        case .ready: return model.entry == .checking
        }
    }

    var body: some View {
        stages
            .overlay(alignment: .bottom) {
                if rootToasts { ToastView().padding(.bottom, 40) }
            }
            // The Terms (§228) and the update screen (§208) over everything,
            // then the FAM intro and the log-out splash, which take no touches.
            .overlay { TermsLayer() }
            .overlay { IntroLayer(run: model.introRun) }
            .overlay { SplashLayer(shown: model.splash) }
            .overlay {
                if let notice = model.updateRequired {
                    UpdateRequiredView(notice: notice)
                }
            }
            // Back in the foreground (§329): the voice is asked for now.
            .onChange(of: scenePhase) { _, new in
                if new == .background {
                    wentAway = true
                    // The next background refresh of the DailyFAM page
                    // (`FeedRefresh`): only for an account let in.
                    FeedRefresh.schedule()
                } else if new == .active && wentAway {
                    wentAway = false
                    model.cameToFront()
                }
            }
            // On the waitlist the first run is the status page's profile
            // (`?setup=1`): who you are, then the status page; the interests
            // come once the account is let in.
            .onChange(of: model.entry) { _, step in
                if model.stage == .waiting && step == .interests { model.entry = nil }
            }
    }

    private var stages: some View {
        Group {
            switch model.stage {
            case .opening:
                Wordmark(size: 30)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            case .signedOut:
                FrontDoorView()
            case .waiting:
                if model.entry == .identity {
                    IdentityView(mode: .firstRun)
                } else {
                    WaitingView()
                }
            case .ready:
                // The first run's steps after an account (`afterAccount`):
                // who you are, then your interests - then the app.
                switch model.entry {
                case .checking:
                    Wordmark(size: 30)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                case .identity:
                    IdentityView(mode: .firstRun)
                case .interests:
                    InterestsView(mode: .firstRun)
                case nil:
                    // Drawn afresh for another account (`accountEpoch`).
                    MainTabs()
                        .id(model.accountEpoch)
                }
            }
        }
        .background(FAMColor.background.ignoresSafeArea())
        // The activation step the first time it is owed (§411, §414): the
        // web's card over the app, with no X - the date is required.
        .fullScreenCover(item: $model.owedActivation) { owed in
            ActivationCard(activation: owed, owed: true)
                .famObjects(model)
                .interactiveDismissDisabled(true)
                .famThemed()
        }
        .overlay { LoadingLayer(loading: model.loading) }
        // "Which one do you mean?" and the limit screen (`onGenerationFailed`).
        .overlay { RefusalLayer() }
        // The AI question as the web's centred card (`aiConsentOverlay`),
        // over everything here; the player and the social covers draw it too.
        .overlay { ConsentLayer(consent: model.consent, say: { model.nowPlaying.say($0) }) }
    }
}

/// Voice search's screen while it is open.
struct VoiceSearchLayer: View {
    @ObservedObject var voice: VoiceSearchModel

    var body: some View {
        ZStack {
            if voice.isOpen {
                VoiceSearchView(voice: voice)
                    .transition(.opacity)
            }
        }
    }
}

/// The loading screen over everything while an episode is being made.
struct LoadingLayer: View {
    @EnvironmentObject private var model: AppModel
    @ObservedObject var loading: LoadingModel

    var body: some View {
        if loading.visible {
            LoadingView(loading: loading) { model.cancelLoading() }
                .transition(.opacity)
        }
    }
}

enum Tab: Hashable {
    case daily, myfam, search, messages, you
}

/// The web's bottom bar (`.tabbar`): dailyFAM, myFAM, the round search
/// button in the middle, Messages (with its unread badge) and yourFAM. The
/// mini player sits just above it, and leaving the player never stops it
/// (`transport`). The notifications poll runs here, every 3 s while the
/// app is in front (`op-social-live`), with its banners and the follower
/// popup over every tab.
struct MainTabs: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var player: StreamPlayer
    @EnvironmentObject private var social: SocialModel
    @EnvironmentObject private var now: NowPlayingModel
    @Environment(\.scenePhase) private var scenePhase
    @State private var tab: Tab = .daily
    @State private var scrollToTop = 0
    /// Where the tab's own page ends on screen: the top of the mini player,
    /// else of the tabs (the toast's floor, `placeAboveBars`).
    @State private var pageBottom: CGFloat = 0
    /// The tabs drawn so far (a faster start): at launch only DailyFAM is
    /// built, so only its data is asked for; every other tab is built - and
    /// asks for its own - the first time it is shown, and stays alive after.
    @State private var built: Set<Tab> = [.daily]
    /// YourFAM has been shown once: its own first look reads the graph.
    @State private var youShown = false

    /// The tab bar's binding: a tab never shown before is built first and
    /// shown on the next frame, so it still nudges in from its side.
    private var tabSelection: Binding<Tab> {
        Binding(get: { tab }, set: { show($0) })
    }

    private func show(_ wanted: Tab) {
        if built.contains(wanted) {
            tab = wanted
            return
        }
        built.insert(wanted)
        Task { @MainActor in
            await Task.yield()
            tab = wanted
        }
    }

    /// A rail's View more, and every screen pushed from Messages (a
    /// conversation, a profile, Friends), have no tab bar or mini player.
    private var sectionOpen: Bool {
        (tab == .daily && !model.dailyPath.isEmpty) || (tab == .messages && !social.messagesPath.isEmpty)
    }

    /// The mini bar is there from the first word until the episode ends or
    /// its X is pressed (`showNowBar`, `hideNowBar`): not while it is being
    /// made, not once it has finished or failed.
    private var miniShown: Bool {
        player.phase == .playing || player.phase == .buffering || player.phase == .paused
    }

    /// The toast's distance from the bottom: 2 pt above the page's end
    /// (the mini player's top, else the tabs'), never under 16.
    private func toastLift(_ bottom: CGFloat) -> CGFloat {
        guard pageBottom > 0 else { return 96 }
        return max(16, bottom - pageBottom + 2)
    }

    /// "Added to Queue · Open" with the player down.
    private var queueOpen: Binding<Bool> {
        Binding(get: { now.showQueue && !model.showPlayer },
                set: { if !$0 { now.showQueue = false } })
    }

    var body: some View {
        VStack(spacing: 0) {
            ZStack {
                DailyView(scrollToTop: $scrollToTop).tabPage(.daily, current: tab)
                if built.contains(.myfam) {
                    MyFAMView(path: $model.mixPath).tabPage(.myfam, current: tab)
                }
                if built.contains(.search) {
                    SearchView(tab: tabSelection, attach: model.attachments, voice: model.voiceSearch,
                               loading: model.loading).tabPage(.search, current: tab)
                }
                if built.contains(.messages) {
                    MessagesView().tabPage(.messages, current: tab)
                }
                if built.contains(.you) {
                    YouView().tabPage(.you, current: tab)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(GeometryReader { geo in
                Color.clear
                    .onAppear { pageBottom = geo.frame(in: .global).maxY }
                    .onChange(of: geo.frame(in: .global).maxY) { _, bottom in pageBottom = bottom }
            })
            // A social screen opened from another tab: over it, with a
            // stack of its own (a face, a story's header, Find new friends).
            .fullScreenCover(item: $social.cover) { cover in
                SocialCoverView(cover: cover)
                    .environmentObject(model)
                    .environmentObject(player)
                    .environmentObject(model.nowPlaying)
                    .environmentObject(model.mixes)
                    .environmentObject(social)
                    .environmentObject(model.you)
                    .overlay { ConsentLayer(consent: model.consent, say: { model.nowPlaying.say($0) }) }
                    .famThemed()
            }
            // A rail's View more is a screen of its own on the web, with
            // neither the tab bar nor the mini player.
            if !sectionOpen {
                if miniShown {
                    MiniPlayer()
                        .padding(.horizontal, 12)
                        .padding(.bottom, 8)
                        .transition(.move(edge: .bottom).combined(with: .opacity))
                }
                TabBar(tab: tabSelection, unread: social.unread, onDaily: {
                    // DailyFAM again while on it: back to the rails from View
                    // more, to the top when scrolled, else the page afresh;
                    // arriving from another tab fetches it too (`openMyFamTab`).
                    if tab == .daily {
                        scrollToTop += 1
                    } else {
                        model.dailyPath = []
                        Task { await model.loadRails() }
                    }
                }, onMyFAM: {
                    // Every arrival opens on the list and asks for it afresh
                    // (`openPlayFAM`).
                    model.mixPath = []
                    model.mixes.categoryState = CategoryState()
                    Task { await model.mixes.load() }
                }, onMessages: {
                    // Every arrival opens on the list, read afresh (`openMessages`);
                    // the first arrival's read is the list's own, as it is built.
                    social.messagesPath = []
                    if built.contains(.messages) { Task { await social.loadInbox() } }
                }, onYou: {
                    // `openProfile`: the stack reset to the hub, read afresh;
                    // the first arrival's read is the page's own, as it is built.
                    model.youPath = []
                    if built.contains(.you) { Task { await model.you.loadProfile() } }
                }, dark: tab == .search && model.privateSearch)
                // The bar is the foot of the search screen's blind: the
                // charcoal reaches it last as it falls, and leaves it first
                // as it lifts (`Motion.dimDown`, 320 / 260 ms).
                .animation(model.privateSearch ? Animation.linear(duration: 0.05).delay(0.27)
                                               : Animation.linear(duration: 0.05),
                           value: model.privateSearch)
            }
        }
        // The bar rises from behind the tabs (260 ms) and drops (200 ms).
        .animation(.easeOut(duration: 0.26), value: miniShown)
        .sheet(isPresented: queueOpen) {
            QueueSheet()
                .environmentObject(model)
                .environmentObject(now)
                .famThemed()
        }
        .overlay(alignment: .top) { SocialBannerLayer() }
        .overlay {
            if let person = social.follower {
                FollowerPopup(person: person)
            }
        }
        .animation(.easeOut(duration: 0.2), value: social.follower)
        .onChange(of: tab) { _, now in
            built.insert(now)
            // The tab under the player (`stack[0]`): a Go Deeper's surface.
            model.shownTab = now
            social.onMessagesTab = now == .messages
            // YourFAM showing is one of the web's two moments to raise a
            // new follower (`checkNewFollowers`); its first showing does it
            // in the page's own first look.
            if now == .you {
                if youShown {
                    // `loadProfile` on every arrival: new followers, the
                    // unread count (`loadHubMessages`) and the invite link.
                    Task {
                        await social.loadGraph()
                        social.checkNewFollowers()
                        await social.loadInbox()
                        await model.loadInviteLink()
                    }
                }
                youShown = true
            }
        }
        // A shared link that waited for the tabs (decision 5).
        .onAppear {
            model.shownTab = tab
            model.openPendingLink()
        }
        // Search DailyFAM's "Search FAM for it" before searchFAM was ever
        // shown: built now, and it takes the words as it starts.
        .onChange(of: model.searchPrefill) { _, prefill in
            if prefill != nil && !built.contains(.search) { show(.search) }
        }
        // The poll: while the app is in front only; back in front, at once -
        // and only once DailyFAM's feed is on screen (a faster start).
        .task(id: scenePhase == .active && model.feedDrawn) {
            guard scenePhase == .active, model.feedDrawn else { return }
            await social.runNotifications()
        }
        // New message: the web's centred card over every tab and the bar.
        .overlay { NewMessageLayer() }
        .overlay(alignment: .bottom) {
            // Just above whatever is lowest that it must not cover: the mini
            // player when it is up, else the tabs; 16 with neither
            // (`placeAboveBars`).
            GeometryReader { geo in
                ToastView()
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom)
                    .padding(.bottom, toastLift(geo.frame(in: .global).maxY))
            }
            // Only an action takes a tap; the line leaves the page under it
            // pressable (`.snack.show .snack-act{ pointer-events:auto }`).
            .allowsHitTesting(now.toast?.action != nil)
        }
        // Voice search (§151): full screen over the tabs and the mini player.
        .overlay { VoiceSearchLayer(voice: model.voiceSearch) }
        .overlay {
            if let name = model.mixDone {
                MixDoneOverlay(name: name) {
                    model.mixDone = nil
                    show(.myfam)
                    model.mixPath = []
                    Task { await model.mixes.load() }
                }
            }
        }
        .onChange(of: model.tabRequest) { _, wanted in
            guard let wanted else { return }
            show(wanted)
            model.tabRequest = nil
            if wanted == .myfam { Task { await model.mixes.load() } }
        }
        // myFAM's mixes are no longer asked at launch: `AppModel` warms
        // them a few seconds after the feed, and the tab asks on arrival.
        .background(FAMColor.background.ignoresSafeArea())
        // Full screen, as the web's player is a screen of its own; the Home
        // mark minimises it and the episode carries on in the mini player.
        .fullScreenCover(isPresented: $model.showPlayer) {
            PlayerView()
                .environmentObject(model)
                .environmentObject(player)
                .environmentObject(model.nowPlaying)
                .environmentObject(model.mixes)
                .environmentObject(social)
                .environmentObject(model.you)
                // A message that lands while the player is up still drops
                // down over it (the web's banner sits over every screen);
                // its tap takes the player down first (`leavePlayer`).
                .overlay(alignment: .top) { SocialBannerLayer().environmentObject(social) }
                // A typed Go Deeper asks the AI question over the player.
                .overlay { ConsentLayer(consent: model.consent, say: { model.nowPlaying.say($0) }) }
                .famThemed()
        }
    }
}

struct TabBar: View {
    @Binding var tab: Tab
    /// The Messages badge (`[data-msg-badge]`): unread messages.
    var unread: Int = 0
    var onDaily: () -> Void = {}
    var onMyFAM: () -> Void = {}
    var onMessages: () -> Void = {}
    var onYou: () -> Void = {}
    /// Private search's charcoal: the search screen includes its tab bar
    /// (`#screen-home.private-search`), so the bar goes dark with it.
    var dark = false

    private var palette: SearchPalette { SearchPalette.of(dark) }

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        // Five equal slots with the search in the middle, as the web's five
        // tabs sit.
        HStack(alignment: .bottom, spacing: 0) {
            // The 11.9 packet's labels (§519); the screens' meanings stay
            // swapped (§185).
            item(.daily, "dailyFAM", Icons.daily, tone: Icons.dailyTone)
            item(.myfam, "myFAM", Icons.myfam, tone: Icons.myfamTone)
            centre
            item(.messages, "Messages", Icons.messages, tone: Icons.messages, badge: unread)
            item(.you, "yourFAM", Icons.you, tone: Icons.youTone)
        }
        .padding(.horizontal, 8)
        .padding(.top, 10)
        .padding(.bottom, 4)
        .background(palette.background.ignoresSafeArea(edges: .bottom))
        .overlay(alignment: .top) { Rectangle().fill(palette.border).frame(height: 1) }
    }

    private func item(_ which: Tab, _ label: String, _ icon: Path, tone: Path? = nil,
                      badge: Int = 0) -> some View {
        let on = tab == which
        let color = on ? palette.copper : palette.textMuted
        return Button {
            if which == .daily { onDaily() }
            if which == .myfam { onMyFAM() }
            if which == .messages { onMessages() }
            if which == .you { onYou() }
            tab = which
        } label: {
            VStack(spacing: 3) {
                ZStack {
                    // The web's `.tone`: a filled shape under the strokes,
                    // faint until the tab is on.
                    if let tone {
                        Glyph(path: tone, box: CGRect(x: 0, y: 0, width: 24, height: 24))
                            .fill(color.opacity(on ? 0.45 : 0.16))
                            .frame(width: 20, height: 20)
                    }
                    WebIcon(path: icon, size: 20)
                        .foregroundStyle(color)
                }
                .frame(width: 44, height: 28)
                .background(on ? palette.copper.opacity(0.18) : Color.clear, in: Capsule())
                .overlay(alignment: .topTrailing) {
                    // The unread count pops in and shrinks away
                    // (`Motion.badge`).
                    ZStack {
                        if badge > 0 {
                            // In: 0 → 1.25 → 1 over 300 ms (the badge's own
                            // pop); out: 1 → 0 over 160 ms, easing in.
                            TabBadge(count: badge, bumps: !reduceMotion)
                                .transition(.asymmetric(insertion: .identity,
                                                        removal: .scale(scale: 0.01).combined(with: .opacity)))
                        }
                    }
                    .offset(x: 7, y: -4)
                    .animation(reduceMotion ? nil : .easeIn(duration: 0.16), value: badge > 0)
                }
                // The labels stop growing at the largest standard size, as
                // Apple's own tab bar does - five slots cannot hold
                // "Messages" at the accessibility sizes - and a long press
                // shows the tab large instead (the Large Content Viewer).
                Text(label)
                    .font(FAMFont.mono(8.5, bold: on))
                    .foregroundStyle(on ? palette.copper : palette.textMuted)
                    .lineLimit(1)
                    .dynamicTypeSize(...DynamicTypeSize.xxxLarge)
            }
            .frame(maxWidth: .infinity)
        }
        .buttonStyle(.press)
        .accessibilityShowsLargeContentViewer {
            WebIcon(path: icon, size: 20)
            Text(label)
        }
        .accessibilityLabel(badge > 0 ? "\(label), \(badge) unread" : label)
        .accessibilityAddTraits(on ? .isSelected : [])
    }

    /// The round search button, raised out of the bar; copper when it is on.
    private var centre: some View {
        let on = tab == .search
        let color = on ? FAMColor.ink : palette.text
        return Button { tab = .search } label: {
            ZStack {
                Glyph(path: Icons.searchTone, box: CGRect(x: 0, y: 0, width: 24, height: 24))
                    .fill(color.opacity(on ? 0.45 : 0.16))
                    .frame(width: 22, height: 22)
                WebIcon(path: Icons.search, size: 22, line: 2.2)
                    .foregroundStyle(color)
            }
                .frame(width: 50, height: 50)
                .background(on ? palette.copper : palette.surface, in: Circle())
                .overlay(Circle().stroke(on ? palette.copper : palette.borderStrong))
                .shadow(color: .black.opacity(0.18), radius: 9, y: 6)
                .offset(y: -14)
                .padding(.bottom, -14)
        }
        .buttonStyle(.press)
        .frame(maxWidth: .infinity)
        .accessibilityLabel("Search")
        .accessibilityAddTraits(on ? .isSelected : [])
    }
}

/// `.tab-badge`: coral, at least 14 x 14, radius 7, white Geist Mono 8
/// bold; "9+" above nine; drawn on the icon's top right.
struct TabBadge: View {
    let count: Int
    /// The count bumps when it goes up (`Motion.badge`): 1.3 at 40 %, 220 ms.
    var bumps = false
    @State private var bump = false
    /// The pop-in's scale (`Motion.badge`: 0, 1.25 at 60 %, 1 at 300 ms).
    @State private var pop: CGFloat = 1

    var body: some View {
        label
            .scaleEffect(bump ? 1.3 : 1)
            .scaleEffect(pop)
            .onAppear { popIn() }
            .onChange(of: count) { old, new in
                guard bumps, new > old else { return }
                withAnimation(.easeOut(duration: 0.09)) { bump = true }
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.09) {
                    withAnimation(.easeOut(duration: 0.13)) { bump = false }
                }
            }
    }

    private func popIn() {
        guard bumps else { return }
        var still = Transaction()
        still.disablesAnimations = true
        withTransaction(still) { pop = 0.01 }
        DispatchQueue.main.async {
            withAnimation(.easeOut(duration: 0.18)) { pop = 1.25 }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.18) {
                withAnimation(.easeOut(duration: 0.12)) { pop = 1 }
            }
        }
    }

    private var label: some View {
        Text(count > 9 ? "9+" : "\(count)")
            .font(FAMFont.mono(8, bold: true))
            .foregroundStyle(Color.white)
            .padding(.horizontal, 3)
            .frame(minWidth: 14, minHeight: 14)
            .background(FAMColor.coral, in: Capsule())
            .accessibilityHidden(true)
    }
}
