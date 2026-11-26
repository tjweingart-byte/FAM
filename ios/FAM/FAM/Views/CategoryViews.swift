import SwiftUI

/// Where Explore's category chips stand (§229, §500, §550, §691): at rest
/// (All - every category's stories) or inside one main category. Since the
/// 11.25 packet the row holds nothing narrower, so `root` and `node` are
/// the same chip.
struct CategoryState: Equatable {
    var root: String?
    var node: String?

    var atRest: Bool { root == nil }
}

/// A chip (`.catchip`): 32 tall, white, a strong border; on, filled dark.
struct CategoryChip: View {
    let label: String
    var on = false
    var starred = false
    var dashed = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 5) {
                if starred {
                    StarShape().fill(on ? FAMColor.background : FAMColor.copper).frame(width: 11, height: 11)
                        .accessibilityLabel("Following")
                }
                Text(label)
                    .font(FAMFont.body(12.5, .semibold))
                    .foregroundStyle(on ? FAMColor.background : (dashed ? FAMColor.textMuted : FAMColor.text))
                    .lineLimit(1)
                    .fixedSize()
            }
            .padding(.horizontal, 13)
            .frame(minHeight: 32)
            .background(on ? FAMColor.text : FAMColor.background, in: Capsule())
            .overlay(Capsule().stroke(on ? FAMColor.text : FAMColor.borderStrong,
                                      style: StrokeStyle(lineWidth: 1, dash: dashed ? [3, 2] : [])))
        }
        .buttonStyle(.press)
        .accessibilityAddTraits(on ? .isSelected : [])
    }
}

/// The followed star (`M12 2.8l2.8 5.9 6.4.8-4.7 4.4 1.2 6.4L12 17.2l-5.7 3.1 1.2-6.4-4.7-4.4 6.4-.8z`).
struct StarShape: Shape {
    func path(in r: CGRect) -> Path {
        let pts: [(Double, Double)] = [(12, 2.8), (14.8, 8.7), (21.2, 9.5), (16.5, 13.9), (17.7, 20.3),
                                       (12, 17.2), (6.3, 20.3), (7.5, 13.9), (2.8, 9.5), (9.2, 8.7)]
        var p = Path()
        for (i, pt) in pts.enumerated() {
            let c = CGPoint(x: r.minX + r.width * pt.0 / 24, y: r.minY + r.height * pt.1 / 24)
            if i == 0 { p.move(to: c) } else { p.addLine(to: c) }
        }
        p.closeSubpath()
        return p
    }
}

/// The chips row, edge to edge (`renderCategoryChips`). **Only the main
/// categories, and it never changes** (11.25 packet #5, the owner: "I only
/// want the main categories and I don't want them to ever disappear"):
/// All, then the facets in the server's order - the same chips in the same
/// places whether or not one is picked. Picking one lights it and loads
/// that category; All, or the lit chip again, goes back to All. A followed
/// main category keeps its star and its place. The X, the sub-categories,
/// the listener's followed sub-categories and "More" (with its find) are
/// gone from the row.
struct CategoryChipsRow: View {
    @EnvironmentObject private var mixes: MixesModel
    @Binding var state: CategoryState

    var body: some View {
        if let page = mixes.categories {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 7) {
                    CategoryChip(label: "All", on: state.atRest) { state = CategoryState() }
                    ForEach(page.facets) { f in
                        CategoryChip(label: f.label, on: state.node == f.id,
                                     starred: mixes.follows(f.id)) { pick(f.id) }
                    }
                }
                .padding(.horizontal, 18)
                .padding(.top, 2)
                .padding(.bottom, 10)
            }
            .accessibilityLabel("Categories")
        }
    }

    /// The lit chip again is All.
    private func pick(_ id: String) {
        state = state.node == id ? CategoryState() : CategoryState(root: id, node: id)
    }
}

/// Explore's panel (`drawCategoryPanel`; §691, the owner's 11.24 mock):
/// a story to a card. With no category picked (`node` "") it is **All** -
/// every category, the Trending edition's stories first, then today's most
/// played; inside a category the same cards sit under Play all and Follow.
/// The server's order is the order drawn. §500's 16:9 hero and its rows
/// are gone, as on the web.
struct CategoryPanel: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var mixes: MixesModel
    /// The category, or "" for All.
    let node: String

    @State private var page: CategoryEpisodes?
    @State private var failed = false
    @State private var following = false
    /// What `page` was asked for; while another loads, the last page stays,
    /// inert (`aria-busy`, `inert`, §642/§646).
    @State private var pageNode = ""
    /// The first page has waited 300 ms: "Loading…" may show.
    @State private var slow = false
    /// The story whose "Add to playlist" was tapped (`addExploreToPlaylist`).
    @State private var adding: CategoryEpisode?
    /// What the server is asked for: the category, or every one.
    private var asked: String { node.isEmpty ? FAMAPI.exploreAll : node }
    private var busy: Bool { page != nil && pageNode != asked }
    @Environment(\.dynamicTypeSize) private var typeSize

    static let tones: [String: Color] = [
        "sports": Color(hex: 0x56402F), "money": Color(hex: 0x2F4A3E), "business": Color(hex: 0x4A4632),
        "tech": Color(hex: 0x3B3A66), "science": Color(hex: 0x2C4A55), "health": Color(hex: 0x3D4A30),
        "culture": Color(hex: 0x5A3A30), "world": Color(hex: 0x2C415A),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let page {
                if page.episodes.isEmpty {
                    if node.isEmpty {
                        note("Nothing has been made today yet. Stories come up here as FAM writes them and people play them.")
                    } else {
                        // The heading and Follow side by side; at the
                        // accessibility sizes Follow goes under it (`large-text`).
                        let layout = typeSize.isAccessibilitySize
                            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8))
                            : AnyLayout(HStackLayout(spacing: 10))
                        layout {
                            Text(page.label).font(FAMFont.heading(19, .semibold)).foregroundStyle(FAMColor.text)
                                .fixedSize(horizontal: false, vertical: true)
                            followButton
                        }
                        .padding(.top, 6)
                        .padding(.bottom, 12)
                        note("Nothing has been made in \(page.label) today yet. Episodes come up here as people play and FAM writes them.")
                    }
                } else {
                    if !node.isEmpty { actions(page) }
                    ForEach(Array(page.episodes.enumerated()), id: \.offset) { _, e in
                        ExploreCard(episode: e, kicker: mixes.exploreKicker(e),
                                    tone: Self.tones[mixes.facet(of: e.node ?? "")] ?? FAMColor.surface2,
                                    // One card, one episode: nothing of the
                                    // page is queued behind it, so it ends on
                                    // What's next (11.25 packet #1). Play
                                    // all still queues the page.
                                    play: { model.playExplore(e) },
                                    add: { adding = e })
                    }
                }
            } else if failed {
                VStack(spacing: 6) {
                    note(node.isEmpty ? "Could not load today’s stories right now."
                                      : "Could not load this category right now.")
                    // `.sample-note-btn`: the copper pill.
                    Button { Task { await load() } } label: {
                        Text("Try again")
                            .font(FAMFont.body(12, .bold))
                            .foregroundStyle(FAMColor.ink)
                            .padding(.horizontal, 12)
                            .padding(.vertical, 6)
                            .background(FAMColor.copper, in: Capsule())
                    }
                    .buttonStyle(.press)
                }
                .frame(maxWidth: .infinity)
            } else if slow {
                note("Loading today’s stories…")
            }
        }
        .padding(.horizontal, 18)
        // Play all, Follow and the cards do nothing while the next loads.
        .allowsHitTesting(!busy)
        // Asked again with every arrival on the tab (`exploreAsk`), so All
        // is never yesterday's.
        .task(id: asked + "|" + String(mixes.exploreAsk)) { await load() }
        .famSheet(item: $adding) { e in
            AddToMixSheet(query: e.query, title: e.title.isEmpty ? e.query : e.title)
                .environmentObject(model)
                .environmentObject(model.mixes)
                .environmentObject(model.nowPlaying)
        }
    }

    private func load() async {
        failed = false
        let wanted = asked
        // Nothing of a category left stays under the All chip while All
        // loads (§691 review).
        if node.isEmpty && page != nil && pageNode != wanted {
            page = nil
            pageNode = ""
        }
        if page == nil {
            // The line only after 300 ms (`lateLoader`).
            slow = false
            Task { @MainActor in
                try? await Task.sleep(nanoseconds: 300_000_000)
                if page == nil && asked == wanted { slow = true }
            }
        }
        do {
            let answer = try await model.api.categoryEpisodes(wanted)
            if Task.isCancelled { return }
            page = answer
            pageNode = wanted
        } catch {
            // A load overtaken by the next node's says nothing.
            if Task.isCancelled { return }
            failed = true
            page = nil
            pageNode = ""
        }
    }

    private func note(_ text: String) -> some View {
        Text(text)
            .font(FAMFont.body(11))
            .foregroundStyle(FAMColor.textFaint)
            .multilineTextAlignment(.center)
            .lineSpacing(4)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 30)
            .padding(.horizontal, 18)
    }

    private func actions(_ page: CategoryEpisodes) -> some View {
        let total = max(1, Int(page.episodes.reduce(0) { $0 + ($1.minutes ?? FAMAPI.browseMinutes) }.rounded()))
        // Follow under Play all at the accessibility sizes (`large-text`).
        let layout = typeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8))
            : AnyLayout(HStackLayout(spacing: 8))
        return layout {
            Button { model.playCategory(page.episodes, from: 0) } label: {
                HStack(spacing: 7) {
                    PlayTriangle().fill(FAMColor.ink).frame(width: 13, height: 13)
                    Text("Play all \(page.episodes.count) · \(total) min")
                }
                .font(FAMFont.body(13, .bold))
                .foregroundStyle(FAMColor.ink)
                .frame(maxWidth: .infinity, minHeight: 38)
                .background(FAMColor.copper, in: Capsule())
            }
            .buttonStyle(.press)
            followButton
        }
        .padding(.top, 4)
        .padding(.bottom, 10)
    }

    private var followButton: some View {
        let on = mixes.follows(node)
        return Button {
            guard mixes.categories?.saved == true else { return }
            guard !following else { return }
            following = true
            // Following on the tap (§642); refused, it flips back.
            Task { _ = await mixes.toggleFollow(node); following = false }
        } label: {
            Text(on ? "Following" : "Follow")
                .font(FAMFont.body(12, .bold))
                .foregroundStyle(on ? FAMColor.ink : FAMColor.text)
                .padding(.horizontal, 14)
                .frame(minHeight: 34)
                .background(on ? FAMColor.paper : Color.clear, in: Capsule())
                .overlay(Capsule().stroke(on ? FAMColor.paper : FAMColor.borderStrong))
        }
        .buttonStyle(.press)
    }
}

/// The E before an explicit title - the same badge Search DailyFAM draws
/// (`CatalogView`).
struct ExplicitBadge: View {
    var body: some View {
        Text("E")
            .font(FAMFont.mono(8, bold: true))
            .foregroundStyle(FAMColor.ink)
            .padding(.horizontal, 3)
            // 14 x 14 at the default size, a floor as the E grows (`large-text`).
            .frame(minWidth: 14, minHeight: 14)
            .background(FAMColor.textMuted, in: RoundedRectangle(cornerRadius: 3))
            .accessibilityLabel("Explicit")
    }
}

// MARK: - Explore | Playlists (§550, §691)

/// Two equal tabs under the wordmark, a copper rule under the one that is
/// on, as Groups | Messages (`.pf-tabs`, `showPlayfamTab`): **Explore** -
/// today's stories, the trending ones first, filtered by a row of category
/// chips - and **Playlists**, the listener's mixes. Explore is the first
/// tab and the one an arrival opens on (§691, the owner's 11.24 mock).
struct PlayfamTabs: View {
    let tab: PlayfamTab
    let pick: (PlayfamTab) -> Void

    var body: some View {
        HStack(spacing: 0) {
            button(.categories, "Explore")
            button(.mixes, "Playlists")
        }
        // The hairline under both; the copper rule of the one on is drawn
        // over it (`margin-bottom:-1px`) - one rule that slides between
        // them (round three, §642 `playfam-tabs` B: 240 ms on
        // `--ease-in-out`).
        .background(alignment: .bottom) {
            Rectangle().fill(FAMColor.border).frame(height: 1)
        }
        .overlay(alignment: .bottom) {
            SlidingRule(count: 2, index: tab == .categories ? 0 : 1, height: 2.5, duration: 0.24)
        }
        .padding(.horizontal, 18)
        .padding(.bottom, 12)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("myFAM")
    }

    private func button(_ which: PlayfamTab, _ label: String) -> some View {
        let on = tab == which
        return Button { pick(which) } label: {
            Text(label)
                .font(FAMFont.body(15, .semibold))
                .foregroundStyle(on ? FAMColor.text : FAMColor.textMuted)
                .animation(Motion.anim(.easeInOut(duration: 0.15)), value: on)
                .lineLimit(1)
                .padding(.top, 8)
                .padding(.bottom, 9 + 2.5)
                .frame(maxWidth: .infinity, minHeight: 42)
                .contentShape(Rectangle())
        }
        .buttonStyle(.press)
        .accessibilityAddTraits(on ? .isSelected : [])
        // `#pfTabCats` / `#pfTabMixes`: the tour's "Explore" and
        // "Your playlists".
        .tourTarget(which == .mixes ? .mixesTab : .categoriesTab)
    }
}

