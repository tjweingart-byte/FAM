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
    case daily, myfam, search, you
}

/// The web's bottom bar (`.tabbar`): DailyFAM, the round search button in
/// the middle, YourFAM. myFAM and Messages join it when they are built - a
/// control with nothing behind it is worse than no control
/// (`no-dead-controls`). The mini player sits just above it, and leaving
/// the player never stops it (`transport`).
struct MainTabs: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var player: StreamPlayer
    @State private var tab: Tab = .daily
    @State private var scrollToTop = 0

    private var sectionOpen: Bool { tab == .daily && !model.dailyPath.isEmpty }

    var body: some View {
        VStack(spacing: 0) {
            ZStack {
                DailyView(scrollToTop: $scrollToTop).opacity(tab == .daily ? 1 : 0).allowsHitTesting(tab == .daily)
                MyFAMView(path: $model.mixPath).opacity(tab == .myfam ? 1 : 0).allowsHitTesting(tab == .myfam)
                SearchView(tab: $tab).opacity(tab == .search ? 1 : 0).allowsHitTesting(tab == .search)
                YouView().opacity(tab == .you ? 1 : 0).allowsHitTesting(tab == .you)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            // A rail's View more is a screen of its own on the web, with
            // neither the tab bar nor the mini player.
            if !sectionOpen {
                if player.isLoaded {
                    MiniPlayer()
                        .padding(.horizontal, 10)
                        .padding(.bottom, 8)
                }
                TabBar(tab: $tab, onDaily: {
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
                })
            }
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
    var onDaily: () -> Void = {}
    var onMyFAM: () -> Void = {}

    var body: some View {
        // Five equal slots with the search in the middle, as the web's five
        // tabs sit: Messages' slot stays empty until it is built
        // (`no-dead-controls`), so the search is still centred.
        HStack(alignment: .bottom, spacing: 0) {
            item(.daily, "DailyFAM", Icons.daily, tone: Icons.dailyTone)
            item(.myfam, "myFAM", Icons.myfam, tone: Icons.myfamTone)
            centre
            Color.clear
                .frame(maxWidth: .infinity)
                .frame(height: 1)
                .accessibilityHidden(true)
            item(.you, "YourFAM", Icons.you, tone: Icons.youTone)
        }
        .padding(.horizontal, 8)
        .padding(.top, 10)
        .padding(.bottom, 4)
        .background(FAMColor.background.ignoresSafeArea(edges: .bottom))
        .overlay(alignment: .top) { Rectangle().fill(FAMColor.border).frame(height: 1) }
    }

    private func item(_ which: Tab, _ label: String, _ icon: Path, tone: Path? = nil) -> some View {
        let on = tab == which
        let color = on ? FAMColor.copper : FAMColor.textMuted
        return Button {
            if which == .daily { onDaily() }
            if which == .myfam { onMyFAM() }
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
                Text(label)
                    .font(FAMFont.mono(8.5, bold: on))
                    .foregroundStyle(on ? FAMColor.copper : FAMColor.textMuted)
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
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
