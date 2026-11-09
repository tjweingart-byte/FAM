import FAMSafety
import SwiftUI

@main
struct FAMApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
                .environmentObject(model.player)
                .aiConsentSheet(model.consent)
                .task { await model.open() }
        }
    }
}

/// Which screen the app is on is the server's answer to "who is this":
/// signed out, waiting for a place, or in.
struct RootView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        switch model.stage {
        case .opening:
            ProgressView()
        case .signedOut:
            AuthView()
        case .waiting:
            WaitingView()
        case .ready:
            MainTabs()
        }
    }
}

/// The three tabs, with the mini player above the tab bar whenever
/// something is loaded. Leaving the player never stops it (`transport`).
struct MainTabs: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var player: StreamPlayer

    var body: some View {
        TabView {
            DailyView()
                .safeAreaInset(edge: .bottom) { miniPlayer }
                .tabItem { Label("DailyFAM", systemImage: "sun.max") }
            SearchView()
                .safeAreaInset(edge: .bottom) { miniPlayer }
                .tabItem { Label("Search", systemImage: "magnifyingglass") }
            YouView()
                .safeAreaInset(edge: .bottom) { miniPlayer }
                .tabItem { Label("You", systemImage: "person.crop.circle") }
        }
        .sheet(isPresented: $model.showPlayer) {
            PlayerView()
                .environmentObject(model)
                .environmentObject(player)
        }
    }

    @ViewBuilder private var miniPlayer: some View {
        if player.isLoaded {
            MiniPlayer().padding(.bottom, 6)
        }
    }
}
