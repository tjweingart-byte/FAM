import SwiftUI

/// Where the myFAM tab's category chips stand (§229, §500): All, inside a
/// category (its root and a chosen sub), or finding one.
struct CategoryState: Equatable {
    var root: String?
    var node: String?
    var finding = false

    var atRest: Bool { root == nil && !finding }
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

/// The chips row, edge to edge (`renderCategoryChips`).
struct CategoryChipsRow: View {
    @EnvironmentObject private var mixes: MixesModel
    @Binding var state: CategoryState
    let subs: [CategorySub]

    var body: some View {
        if let page = mixes.categories {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 7) {
                    if state.atRest {
                        CategoryChip(label: "All", on: true) {}
                        if page.saved {
                            ForEach(page.mine) { c in
                                CategoryChip(label: c.label, starred: true) { pick(c.id) }
                            }
                        }
                        ForEach(page.facets.filter { f in !page.mine.contains { $0.id == f.id } }) { f in
                            CategoryChip(label: f.label) { pick(f.id) }
                        }
                        CategoryChip(label: "More", dashed: true) { state = CategoryState(finding: true) }
                    } else {
                        Button { state = CategoryState() } label: {
                            Image(systemName: "xmark")
                                .font(.system(size: 11, weight: .bold))
                                .foregroundStyle(FAMColor.text)
                                .frame(width: 34, height: 32)
                                .overlay(Capsule().stroke(FAMColor.borderStrong))
                        }
                        .buttonStyle(.press)
                        .accessibilityLabel("All of myFAM")
                        if state.finding {
                            Text("Find a category")
                                .font(FAMFont.body(12.5))
                                .foregroundStyle(FAMColor.textMuted)
                        } else if let root = state.root {
                            CategoryChip(label: label(root), on: state.node == root,
                                         starred: mixes.follows(root)) { state.node = root }
                            ForEach(subs) { sub in
                                CategoryChip(label: sub.label, on: state.node == sub.id,
                                             starred: mixes.follows(sub.id)) {
                                    // The chosen sub again goes back up to the root.
                                    state.node = state.node == sub.id ? root : sub.id
                                }
                            }
                        }
                    }
                }
                .padding(.horizontal, 18)
                .padding(.top, 2)
                .padding(.bottom, 10)
            }
            .accessibilityLabel("Categories")
        }
    }

    private func pick(_ id: String) {
        state = CategoryState(root: id, node: id)
    }

    private func label(_ id: String) -> String {
        mixes.categories?.all.first { $0.id == id }?.label
            ?? mixes.categories?.facets.first { $0.id == id }?.label ?? id
    }
}

/// A category's panel (`drawCategoryPanel`): the most played episode as a
/// 16:9 hero, Play all and Follow, then the rest as rows.
struct CategoryPanel: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var mixes: MixesModel
    let node: String
    @Binding var subs: [CategorySub]
    /// The category the chips are inside: its answer alone sets the sub
    /// row, and its facet colours the hero (`catPage.root`).
    var root: String = ""

    @State private var page: CategoryEpisodes?
    @State private var failed = false
    @State private var following = false
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
                } else {
                    hero(page)
                    actions(page)
                    ForEach(Array(page.episodes.enumerated().dropFirst()), id: \.offset) { i, e in
                        row(e, i, page)
                    }
                }
            } else if failed {
                VStack(spacing: 6) {
                    note("Could not load this category right now.")
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
            } else {
                note("Loading today’s episodes…")
            }
        }
        .padding(.horizontal, 18)
        .task(id: node) { await load() }
    }

    private func load() async {
        failed = false
        do {
            let answer = try await model.api.categoryEpisodes(node)
            page = answer
            // The level below comes from the category's own answer only,
            // so narrowing to one of its subs keeps the row
            // (`if(node === catPage.root)`).
            if root.isEmpty || node == root { subs = answer.subcategories }
        } catch {
            failed = true
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

    private func hero(_ page: CategoryEpisodes) -> some View {
        let first = page.episodes[0]
        let shade = Color(red: 10 / 255, green: 9 / 255, blue: 13 / 255)
        // The frame is a 16:9 box of the facet's colour; the picture is an
        // overlay cut to it, so a 4:3 thumbnail can never make it taller.
        return Button { model.playCategory(page.episodes, from: 0) } label: {
            Rectangle()
                .fill(Self.tones[heroFacet(page)] ?? FAMColor.surface2)
                .aspectRatio(16 / 9, contentMode: .fit)
                .frame(maxWidth: .infinity, minHeight: 150)
                .overlay {
                    if let thumb = first.thumb, let url = model.api.picture(thumb) {
                        AsyncImage(url: url) { $0.resizable().scaledToFill() } placeholder: { Color.clear }
                    }
                }
                .overlay {
                    LinearGradient(stops: [.init(color: shade.opacity(0), location: 0.35),
                                           .init(color: shade.opacity(0.78), location: 1)],
                                   startPoint: .top, endPoint: .bottom)
                }
                .overlay(alignment: .bottomLeading) { heroText(page, first) }
                .clipShape(RoundedRectangle(cornerRadius: 16))
                .contentShape(RoundedRectangle(cornerRadius: 16))
        }
        .buttonStyle(.press)
        .padding(.top, 4)
        .padding(.bottom, 10)
    }

    /// The root's facet (`categoryFacetOf(catPage.root)`), else the node's own.
    private func heroFacet(_ page: CategoryEpisodes) -> String {
        let id = root.isEmpty ? node : root
        if let cats = mixes.categories {
            if cats.facets.contains(where: { $0.id == id }) { return id }
            if let found = cats.all.first(where: { $0.id == id }), let facet = found.facet { return facet }
            return ""
        }
        return page.facet ?? ""
    }

    private func heroText(_ page: CategoryEpisodes, _ first: CategoryEpisode) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                if first.explicit == true { ExplicitBadge() }
                Text(first.title.isEmpty ? first.query : first.title)
                    .font(FAMFont.heading(18, .semibold))
                    .foregroundStyle(Color(hex: 0xF4EFE4))
                    .multilineTextAlignment(.leading)
            }
            Text("Most played in \(page.label) today")
                .font(FAMFont.mono(8.5))
                .tracking(0.85)
                .textCase(.uppercase)
                .foregroundStyle(Color(hex: 0xF4EFE4).opacity(0.78))
        }
        .padding(14)
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
        .padding(.bottom, 10)
    }

    private var followButton: some View {
        let on = mixes.follows(node)
        return Button {
            guard mixes.categories?.saved == true else { return }
            guard !following else { return }
            following = true
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

    private func row(_ e: CategoryEpisode, _ i: Int, _ page: CategoryEpisodes) -> some View {
        Button { model.playCategory(page.episodes, from: i) } label: {
            HStack(spacing: 11) {
                ZStack {
                    RoundedRectangle(cornerRadius: 10).fill(FAMColor.surface2)
                    if let thumb = e.thumb, let url = model.api.picture(thumb) {
                        AsyncImage(url: url) { $0.resizable().scaledToFill() } placeholder: {
                            PlayTriangle().fill(FAMColor.copper).frame(width: 14, height: 14)
                        }
                    } else {
                        PlayTriangle().fill(FAMColor.copper).frame(width: 14, height: 14)
                    }
                }
                .frame(width: 38, height: 38)
                .clipShape(RoundedRectangle(cornerRadius: 10))
                VStack(alignment: .leading, spacing: 3) {
                    HStack(alignment: .firstTextBaseline, spacing: 5) {
                        if e.explicit == true { ExplicitBadge() }
                        Text(e.title.isEmpty ? e.query : e.title)
                            .font(FAMFont.heading(12.5, .medium))
                            .foregroundStyle(FAMColor.text)
                            .multilineTextAlignment(.leading)
                    }
                    Text(meta(e, page))
                        .font(FAMFont.mono(8))
                        .tracking(0.32)
                        .foregroundStyle(FAMColor.textFaint)
                }
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 18)
            .padding(.vertical, 12)
            .overlay(alignment: .bottom) { Rectangle().fill(FAMColor.border).frame(height: 1) }
            .contentShape(Rectangle())
        }
        .buttonStyle(.press)
    }

    /// "SPORT · 1.5 MIN · 12 PLAYS" (`catRowMeta`).
    private func meta(_ e: CategoryEpisode, _ page: CategoryEpisodes) -> String {
        // The node's own label, or nothing (`catRowMeta`'s filter(Boolean)).
        var parts: [String] = []
        let nodeLabel = (e.nodeLabel ?? "").uppercased()
        if !nodeLabel.isEmpty { parts.append(nodeLabel) }
        parts.append("\(FAMAPI.minutes(e.minutes ?? FAMAPI.browseMinutes)) MIN")
        if let plays = e.plays, plays > 0 { parts.append(plays == 1 ? "1 PLAY" : "\(plays) PLAYS") }
        return parts.joined(separator: " · ")
    }
}

/// "More": find any category by the start of a word (`renderCategoryFind`).
struct CategoryFind: View {
    @EnvironmentObject private var mixes: MixesModel
    @Binding var state: CategoryState
    @State private var words = ""
    @FocusState private var focused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass").font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(FAMColor.textMuted)
                TextField("", text: $words,
                          prompt: Text("Find a category, like NFL").foregroundColor(FAMColor.textFaint))
                    .font(FAMFont.body(13))
                    .focused($focused)
                    .autocorrectionDisabled()
                    .accessibilityLabel("Find a category")
            }
            .padding(.horizontal, 12)
            .frame(minHeight: 38)
            .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(focused ? FAMColor.copper : FAMColor.borderStrong))
            .padding(.top, 4)
            .padding(.bottom, 12)
            let found = matches
            if found.isEmpty {
                Text("No category called that yet.")
                    .font(FAMFont.body(11)).foregroundStyle(FAMColor.textFaint)
                    .frame(maxWidth: .infinity).padding(.vertical, 30)
            }
            // By place: a facet can also be one of the tree's nodes, listed twice as on the web.
            ForEach(Array(found.enumerated()), id: \.offset) { _, c in
                Button { state = CategoryState(root: c.id, node: c.id) } label: {
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: 4) {
                            if mixes.follows(c.id) {
                                StarShape().fill(FAMColor.copper).frame(width: 11, height: 11)
                            }
                            Text(c.label).font(FAMFont.heading(12.5, .medium)).foregroundStyle(FAMColor.text)
                        }
                        Text((c.path ?? []).joined(separator: " › ").uppercased())
                            .font(FAMFont.mono(8)).tracking(0.32).foregroundStyle(FAMColor.textFaint)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 18)
                    .padding(.vertical, 12)
                    .overlay(alignment: .bottom) { Rectangle().fill(FAMColor.border).frame(height: 1) }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.press)
            }
        }
        .padding(.horizontal, 18)
        .onAppear { focused = true }
    }

    /// Nothing typed: the facets. Typed: the facets and then every node
    /// whose label has a word starting with what was typed - spaces split
    /// words, so "york gi" finds the New York Giants - 60 at most
    /// (`renderCategoryFind`: `(" " + label).indexOf(" " + q)`).
    private var matches: [CategoryNode] {
        guard let page = mixes.categories else { return [] }
        let q = words.trimmingCharacters(in: .whitespaces).lowercased()
        let facets = page.facets
            .filter { Self.starts($0.label, q) }
            .map { CategoryNode(id: $0.id, label: $0.label, facet: $0.id, path: []) }
        if q.isEmpty { return facets }
        let nodes = page.all.filter { Self.starts($0.label, q) }
        return Array((facets + nodes).prefix(60))
    }

    static func starts(_ label: String, _ q: String) -> Bool {
        if q.isEmpty { return true }
        return (" " + label.lowercased()).contains(" " + q)
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
