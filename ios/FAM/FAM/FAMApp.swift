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
                .aiConsentSheet(model.consent)
                .tint(FAMColor.copper)
                .preferredColorScheme(.light)
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
                MainTabs()
            }
        }
        .background(FAMColor.background.ignoresSafeArea())
        .overlay { LoadingLayer(loading: model.loading) }
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

/// The web's bottom bar (`.tabbar`): DailyFAM, myFAM, the round search
/// button in the middle, Messages (with its unread badge) and YourFAM. The
/// mini player sits just above it, and leaving the player never stops it
/// (`transport`). The notifications poll runs here, every 3 s while the
/// app is in front (`op-social-live`), with its banners and the follower
/// popup over every tab.
struct MainTabs: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var player: StreamPlayer
    @EnvironmentObject private var social: SocialModel
    @Environment(\.scenePhase) private var scenePhase
    @State private var tab: Tab = .daily
    @State private var scrollToTop = 0

    /// A rail's View more, and every screen pushed from Messages (a
    /// conversation, a profile, Friends), have no tab bar or mini player.
    private var sectionOpen: Bool {
        (tab == .daily && !model.dailyPath.isEmpty) || (tab == .messages && !social.messagesPath.isEmpty)
    }

    var body: some View {
        VStack(spacing: 0) {
            ZStack {
                DailyView(scrollToTop: $scrollToTop).opacity(tab == .daily ? 1 : 0).allowsHitTesting(tab == .daily)
                MyFAMView(path: $model.mixPath).opacity(tab == .myfam ? 1 : 0).allowsHitTesting(tab == .myfam)
                SearchView(tab: $tab).opacity(tab == .search ? 1 : 0).allowsHitTesting(tab == .search)
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
            }
            // A rail's View more is a screen of its own on the web, with
            // neither the tab bar nor the mini player.
            if !sectionOpen {
                if player.isLoaded {
                    MiniPlayer()
                        .padding(.horizontal, 10)
                        .padding(.bottom, 8)
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
                })
            }
        }
        .overlay(alignment: .top) { SocialBannerLayer() }
        .overlay {
            if let person = social.follower {
                FollowerPopup(person: person)
            }
        }
        .animation(.easeOut(duration: 0.2), value: social.follower)
        .onChange(of: tab) { _, now in social.onMessagesTab = now == .messages }
        // The poll: while the app is in front only; back in front, at once.
        .task(id: scenePhase == .active) {
            guard scenePhase == .active else { return }
            await social.runNotifications()
        }
        .overlay(alignment: .bottom) {
            ToastView().padding(.bottom, 96)
        }
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

    var body: some View {
        // Five equal slots with the search in the middle, as the web's five
        // tabs sit.
        HStack(alignment: .bottom, spacing: 0) {
            item(.daily, "DailyFAM", Icons.daily, tone: Icons.dailyTone)
            item(.myfam, "myFAM", Icons.myfam, tone: Icons.myfamTone)
            centre
            item(.messages, "Messages", Icons.messages, tone: Icons.messages, badge: unread)
            item(.you, "YourFAM", Icons.you, tone: Icons.youTone)
        }
        .padding(.horizontal, 8)
        .padding(.top, 10)
        .padding(.bottom, 4)
        .background(FAMColor.background.ignoresSafeArea(edges: .bottom))
        .overlay(alignment: .top) { Rectangle().fill(FAMColor.border).frame(height: 1) }
    }

    private func item(_ which: Tab, _ label: String, _ icon: Path, tone: Path? = nil,
                      badge: Int = 0) -> some View {
        let on = tab == which
        let color = on ? FAMColor.copper : FAMColor.textMuted
        return Button {
            if which == .daily { onDaily() }
            if which == .myfam { onMyFAM() }
            if which == .messages { onMessages() }
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
                .background(on ? FAMColor.copper.opacity(0.18) : Color.clear, in: Capsule())
                .overlay(alignment: .topTrailing) {
                    if badge > 0 { TabBadge(count: badge).offset(x: 7, y: -4) }
                }
                Text(label)
                    .font(FAMFont.mono(8.5, bold: on))
                    .foregroundStyle(on ? FAMColor.copper : FAMColor.textMuted)
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
        let color = on ? FAMColor.ink : FAMColor.text
        return Button { tab = .search } label: {
            ZStack {
                Glyph(path: Icons.searchTone, box: CGRect(x: 0, y: 0, width: 24, height: 24))
                    .fill(color.opacity(on ? 0.45 : 0.16))
                    .frame(width: 22, height: 22)
                WebIcon(path: Icons.search, size: 22, line: 2.2)
                    .foregroundStyle(color)
            }
                .frame(width: 50, height: 50)
                .background(on ? FAMColor.copper : FAMColor.surface, in: Circle())
                .overlay(Circle().stroke(on ? FAMColor.copper : FAMColor.borderStrong))
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
