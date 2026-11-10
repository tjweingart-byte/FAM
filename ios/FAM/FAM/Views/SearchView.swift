import SwiftUI

/// searchFAM, Google's shape (`search-bar`): the FAM mark, the rounded bar,
/// the Length bubble under it, and the five trending searches. Length never
/// prints the choice and goes back to 1.5 whenever the app returns. The
/// mic, attach, Voice and Private join when they are built
/// (`no-dead-controls`).
struct SearchView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.scenePhase) private var scenePhase
    @Binding var tab: Tab

    @State private var words = ""
    @State private var minutes = 1.5
    @State private var trending: [TrendingSearch] = []
    @FocusState private var typing: Bool

    private static let lengths: [Double] = [1, 1.5, 2, 3, 4, 5]

    @State private var library = false

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                // The bookshelf: the A to Z of episodes other people made
                // (10.12 packet #3, top left of searchFAM).
                HStack {
                    Button { library = true } label: {
                        // The web's own bookshelf (index.html:3927), 19 pt at 1.8.
                        WebIcon(path: Icons.bookshelf, size: 19, line: 1.8)
                            .foregroundStyle(FAMColor.text)
                            .frame(width: 34, height: 34)
                            .overlay(Circle().stroke(FAMColor.borderStrong))
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("The episode library")
                    Spacer()
                }
                .padding(.top, 8)
                .padding(.leading, 20)
                Spacer(minLength: 80)
                Wordmark(size: 30)
                    .padding(.bottom, 22)
                bar
                HStack(spacing: 10) {
                    lengthBubble
                }
                .padding(.top, 12)
                if !trending.isEmpty && words.isEmpty {
                    trendingList.padding(.top, 16)
                }
            }
            .padding(.horizontal, 18)
        }
        .scrollDismissesKeyboard(.interactively)
        .background(FAMColor.background)
        .fullScreenCover(isPresented: $library) {
            CatalogView()
                .environmentObject(model)
        }
        .task { await loadTrending() }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { minutes = 1.5 }
        }
        .onChange(of: tab) { _, now in
            if now == .search { Task { await loadTrending() } }
        }
        // Search DailyFAM's "Search FAM for it": the words in the box,
        // ready to send (`searchFromMyFam`).
        .onChange(of: model.searchPrefill) { _, words in
            guard let words else { return }
            model.searchPrefill = nil
            if !words.isEmpty { self.words = words }
            tab = .search
        }
    }

    /// The rounded bar (`.fam-input-box`); the copper go appears once there
    /// is something to send.
    private var bar: some View {
        HStack(alignment: .bottom, spacing: 6) {
            TextField("", text: $words,
                      prompt: Text("FAMiliarize me about…").foregroundColor(FAMColor.textMuted.opacity(0.8)),
                      axis: .vertical)
                .font(FAMFont.body(15))
                .foregroundStyle(FAMColor.text)
                .lineLimit(1...5)
                .focused($typing)
                .submitLabel(.search)
                .onSubmit { go() }
                .padding(.vertical, 9)
            if !words.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                Button { go() } label: {
                    Image(systemName: "arrow.up")
                        .font(.system(size: 15, weight: .bold))
                        .foregroundStyle(FAMColor.ink)
                        .frame(width: 36, height: 36)
                        .background(FAMColor.copper, in: Circle())
                }
                .padding(2)
                .accessibilityLabel("Search")
            }
        }
        .padding(.leading, 18)
        .padding(.trailing, 6)
        .padding(.vertical, 6)
        .frame(minHeight: 52)
        .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 26))
        .overlay(RoundedRectangle(cornerRadius: 26).stroke(FAMColor.border))
    }

    /// Length (`.search-bubble`): a menu of 1 to 5 minutes; the bubble
    /// itself always just says "Length".
    private var lengthBubble: some View {
        Menu {
            Picker("Length", selection: $minutes) {
                ForEach(Self.lengths, id: \.self) { value in
                    Text(value == 1 ? "1 minute" : "\(FAMAPI.minutes(value)) minutes").tag(value)
                }
            }
        } label: {
            HStack(spacing: 8) {
                WebIcon(path: Icons.timer, size: 16)
                    .foregroundStyle(FAMColor.textMuted)
                Text("Length")
                    .font(FAMFont.body(14, .semibold))
                    .foregroundStyle(FAMColor.text)
            }
            .frame(maxWidth: .infinity, minHeight: 44)
            .background(FAMColor.surface, in: Capsule())
            .overlay(Capsule().stroke(FAMColor.border))
        }
        .accessibilityLabel("Length, \(FAMAPI.minutes(minutes)) minutes")
    }

    /// TRENDING SEARCHES (`.trend-searches`): one bubble a search, most
    /// searched first; a tap is a search that lands on an episode already
    /// written.
    private var trendingList: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Trending searches")
                .monoLabel(9)
                .padding(.horizontal, 4)
                .padding(.bottom, 1)
            ForEach(trending.prefix(5), id: \.self) { search in
                Button {
                    typing = false
                    Task { await model.search(search.query, minutes: search.minutes) }
                } label: {
                    HStack(spacing: 8) {
                        WebIcon(path: Icons.trend, size: 13)
                            .foregroundStyle(FAMColor.textMuted)
                        Text(search.label)
                            .font(FAMFont.body(12, .medium))
                            .foregroundStyle(FAMColor.text)
                            .lineLimit(1)
                        Spacer(minLength: 0)
                    }
                    .padding(.horizontal, 14)
                    .padding(.vertical, 6)
                    .frame(minHeight: 32)
                    .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 16))
                    .overlay(RoundedRectangle(cornerRadius: 16).stroke(FAMColor.border))
                }
                .buttonStyle(.plain)
            }
        }
    }

    private func loadTrending() async {
        if let found = try? await model.api.trendingSearches() { trending = found }
    }

    private func go() {
        typing = false
        let asked = words
        Task { await model.search(asked, minutes: minutes) }
    }
}
