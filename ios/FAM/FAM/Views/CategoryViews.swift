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
                    StarShape().fill(on ? Color.white : FAMColor.copper).frame(width: 11, height: 11)
                        .accessibilityLabel("Following")
                }
                Text(label)
                    .font(FAMFont.body(12.5, .semibold))
                    .foregroundStyle(on ? Color.white : (dashed ? FAMColor.textMuted : FAMColor.text))
                    .lineLimit(1)
                    .fixedSize()
            }
            .padding(.horizontal, 13)
            .frame(minHeight: 32)
            .background(on ? FAMColor.text : FAMColor.background, in: Capsule())
            .overlay(Capsule().stroke(on ? FAMColor.text : FAMColor.borderStrong,
                                      style: StrokeStyle(lineWidth: 1, dash: dashed ? [3, 2] : [])))
        }
        .buttonStyle(.plain)
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
                        .buttonStyle(.plain)
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

    @State private var page: CategoryEpisodes?
    @State private var failed = false
    @State private var following = false

    static let tones: [String: Color] = [
        "sports": Color(hex: 0x56402F), "money": Color(hex: 0x2F4A3E), "business": Color(hex: 0x4A4632),
        "tech": Color(hex: 0x3B3A66), "science": Color(hex: 0x2C4A55), "health": Color(hex: 0x3D4A30),
        "culture": Color(hex: 0x5A3A30), "world": Color(hex: 0x2C415A),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let page {
                if page.episodes.isEmpty {
                    HStack(spacing: 10) {
                        Text(page.label).font(FAMFont.heading(19, .semibold)).foregroundStyle(FAMColor.text)
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
                    Button("Try again") { Task { await load() } }
                        .font(FAMFont.body(11, .bold))
                        .foregroundStyle(FAMColor.copper)
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
            if !answer.subcategories.isEmpty || subs.isEmpty { subs = answer.subcategories }
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
        return Button { model.playCategory(page.episodes, from: 0) } label: {
            ZStack(alignment: .bottomLeading) {
                Rectangle().fill(Self.tones[page.facet ?? ""] ?? Color(hex: 0x56402F))
                if let thumb = first.thumb, let url = model.api.picture(thumb) {
                    AsyncImage(url: url) { $0.resizable().scaledToFill() } placeholder: { Color.clear }
                }
                LinearGradient(stops: [.init(color: Color(red: 10 / 255, green: 9 / 255, blue: 13 / 255).opacity(0), location: 0.35),
                                       .init(color: Color(red: 10 / 255, green: 9 / 255, blue: 13 / 255).opacity(0.78), location: 1)],
                               startPoint: .top, endPoint: .bottom)
                VStack(alignment: .leading, spacing: 4) {
                    Text((first.explicit == true ? "🅴 " : "") + first.title)
                        .font(FAMFont.heading(18, .semibold))
                        .foregroundStyle(Color(hex: 0xF4EFE4))
                        .multilineTextAlignment(.leading)
                    Text("Most played in \(page.label) today")
                        .font(FAMFont.mono(8.5))
                        .tracking(0.85)
                        .textCase(.uppercase)
                        .foregroundStyle(Color(hex: 0xF4EFE4).opacity(0.78))
                }
                .padding(14)
            }
            .aspectRatio(16 / 9, contentMode: .fit)
            .frame(minHeight: 150)
            .clipShape(RoundedRectangle(cornerRadius: 16))
        }
        .buttonStyle(.plain)
        .padding(.top, 4)
        .padding(.bottom, 10)
    }

    private func actions(_ page: CategoryEpisodes) -> some View {
        let total = max(1, Int(page.episodes.reduce(0) { $0 + ($1.minutes ?? FAMAPI.browseMinutes) }.rounded()))
        return HStack(spacing: 8) {
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
            .buttonStyle(.plain)
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
        .buttonStyle(.plain)
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
                    Text(e.title)
                        .font(FAMFont.heading(12.5, .medium))
                        .foregroundStyle(FAMColor.text)
                        .multilineTextAlignment(.leading)
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
        .buttonStyle(.plain)
    }

    /// "SPORT · 1.5 MIN · 12 PLAYS" (`catRowMeta`).
    private func meta(_ e: CategoryEpisode, _ page: CategoryEpisodes) -> String {
        var parts = [(e.nodeLabel ?? page.label).uppercased(), "\(FAMAPI.minutes(e.minutes ?? FAMAPI.browseMinutes)) MIN"]
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
            .frame(height: 38)
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
            ForEach(found) { c in
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
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 18)
        .onAppear { focused = true }
    }

    /// Nothing typed: the facets. Typed: word starts across every node, 60 at most.
    private var matches: [CategoryNode] {
        guard let page = mixes.categories else { return [] }
        let q = words.trimmingCharacters(in: .whitespaces).lowercased()
        if q.isEmpty {
            return page.facets.map { CategoryNode(id: $0.id, label: $0.label, facet: $0.id, path: []) }
        }
        let facets = page.facets.map { CategoryNode(id: $0.id, label: $0.label, facet: $0.id, path: []) }
        var seen = Set<String>()
        return (facets + page.all).filter { node in
            guard !seen.contains(node.id) else { return false }
            let hit = node.label.lowercased().split(whereSeparator: { !$0.isLetter && !$0.isNumber })
                .contains { $0.hasPrefix(q) } || node.label.lowercased().hasPrefix(q)
            if hit { seen.insert(node.id) }
            return hit
        }.prefix(60).map { $0 }
    }
}
