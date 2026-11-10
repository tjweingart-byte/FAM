import FAMSafety
import GoogleSignIn
import SwiftUI

@main
struct FAMApp: App {
    @StateObject private var model = AppModel()

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
                // Light only (§250); the system's chrome goes dark over
                // private search's charcoal while that screen shows.
                .preferredColorScheme(model.darkChrome ? .dark : .light)
                .task { await model.open() }
                // Google's sign-in comes back to the app through its URL scheme.
                .onOpenURL { url in _ = GIDSignIn.sharedInstance.handle(url) }
        }
    }
}

/// Which screen the app is on is the server's answer to "who is this":
/// signed out, waiting for a place, or in.
struct RootView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        Group {
            switch model.stage {
            case .opening:
                Wordmark(size: 30)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            case .signedOut:
                AuthView()
            case .waiting:
                WaitingView()
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
                    MainTabs()
                }
            }
        }
        .background(FAMColor.background.ignoresSafeArea())
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

    /// "Added to Queue · Open" with the player down.
    private var queueOpen: Binding<Bool> {
        Binding(get: { now.showQueue && !model.showPlayer },
                set: { if !$0 { now.showQueue = false } })
    }

    var body: some View {
        VStack(spacing: 0) {
            ZStack {
                DailyView(scrollToTop: $scrollToTop).opacity(tab == .daily ? 1 : 0).allowsHitTesting(tab == .daily)
                MyFAMView(path: $model.mixPath).opacity(tab == .myfam ? 1 : 0).allowsHitTesting(tab == .myfam)
                SearchView(tab: $tab, attach: model.attachments, voice: model.voiceSearch,
                           loading: model.loading).opacity(tab == .search ? 1 : 0).allowsHitTesting(tab == .search)
                MessagesView().opacity(tab == .messages ? 1 : 0).allowsHitTesting(tab == .messages)
                YouView().opacity(tab == .you ? 1 : 0).allowsHitTesting(tab == .you)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
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
                TabBar(tab: $tab, unread: social.unread, onDaily: {
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
                    // Every arrival opens on the list, read afresh (`openMessages`).
                    social.messagesPath = []
                    Task { await social.loadInbox() }
                }, onYou: {
                    // `openProfile`: the stack reset to the hub, read afresh.
                    model.youPath = []
                    Task { await model.you.loadProfile() }
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
        }
        .overlay(alignment: .top) { SocialBannerLayer() }
        .overlay {
            if let person = social.follower {
                FollowerPopup(person: person)
            }
        }
        .animation(.easeOut(duration: 0.2), value: social.follower)
        .onChange(of: tab) { _, now in
            social.onMessagesTab = now == .messages
            // YourFAM showing is one of the web's two moments to raise a
            // new follower (`checkNewFollowers`).
            if now == .you {
                Task {
                    await social.loadGraph()
                    social.checkNewFollowers()
                }
            }
        }
        // The poll: while the app is in front only; back in front, at once.
        .task(id: scenePhase == .active) {
            guard scenePhase == .active else { return }
            await social.runNotifications()
        }
        .overlay(alignment: .bottom) {
            ToastView().padding(.bottom, 96)
        }
        // Voice search (§151): full screen over the tabs and the mini player.
        .overlay { VoiceSearchLayer(voice: model.voiceSearch) }
        .overlay {
            if let name = model.mixDone {
                MixDoneOverlay(name: name) {
                    model.mixDone = nil
                    tab = .myfam
                    model.mixPath = []
                    Task { await model.mixes.load() }
                }
            }
        }
        .onChange(of: model.tabRequest) { _, wanted in
            guard let wanted else { return }
            tab = wanted
            model.tabRequest = nil
            if wanted == .myfam { Task { await model.mixes.load() } }
        }
        .task { await model.mixes.load() }
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
                    if badge > 0 { TabBadge(count: badge).offset(x: 7, y: -4) }
                }
                Text(label)
                    .font(FAMFont.mono(8.5, bold: on))
                    .foregroundStyle(on ? palette.copper : palette.textMuted)
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity)
        }
        .buttonStyle(.plain)
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
        .buttonStyle(.plain)
        .frame(maxWidth: .infinity)
        .accessibilityLabel("Search")
        .accessibilityAddTraits(on ? .isSelected : [])
    }
}

/// `.tab-badge`: coral, at least 14 x 14, radius 7, white Geist Mono 8
/// bold; "9+" above nine; drawn on the icon's top right.
struct TabBadge: View {
    let count: Int

    var body: some View {
        Text(count > 9 ? "9+" : "\(count)")
            .font(FAMFont.mono(8, bold: true))
            .foregroundStyle(Color.white)
            .padding(.horizontal, 3)
            .frame(minWidth: 14, minHeight: 14)
            .background(FAMColor.coral, in: Capsule())
            .accessibilityHidden(true)
    }
}
