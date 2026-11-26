import SwiftUI

// Following topics (§696, `rule:follow-topics`), after the web piece for
// piece: the follow step's chips and its Made for you preview
// (`.fw-chip`, `.fw-world`, `.fw-preview`), "Follow this?" on DailyFAM
// (`.follow-ask`) and the "Your teams" strip (`.team-strip`). The step
// itself is `InterestsView` (Views/ProfileEdit.swift); what is kept about
// "Follow this?" is the app model's (AppModel.swift).

/// The eight general topics' colours (`FOLLOW_HUES`), the same in both
/// themes, and the cream their words are drawn in.
enum FollowHues {
    static let hues: [String: UInt32] = [
        "sports": 0x8A5A12, "world": 0x5E5770, "tech": 0x3B3F6B, "money": 0x3F5E4C,
        "culture": 0x7A3B2A, "business": 0x4A4A3A, "health": 0x6B4A63, "science": 0x2F5B66,
    ]
    /// On a hue (`#FBF8F1`) and the tick's own ink (`#1E1B27`).
    static let cream = Color(hex: 0xFBF8F1)
    static let tickInk = Color(hex: 0x1E1B27)

    /// A general topic's colour (`--fw-hue`, else `#5E5770`).
    static func world(_ id: String) -> Color {
        Color(hex: hues[id] ?? 0x5E5770)
    }

    /// A preview tile's: its first tag that is a general topic, else the
    /// tile art's own default (`#8A5A12`).
    static func tile(_ tags: [String]) -> Color {
        for tag in tags {
            if let hue = hues[tag] { return Color(hex: hue) }
        }
        return Color(hex: 0x8A5A12)
    }
}

/// A row of chips that wraps (`.fw-chips`: `flex-wrap`, gap 8), where a
/// chip wider than the row is given the row's width so its words wrap
/// rather than run off the screen at large text sizes (`large-text`).
struct WrapRow: Layout {
    var spacing: CGFloat = 8

    private func fit(_ view: LayoutSubview, within width: CGFloat) -> CGSize {
        let ideal = view.sizeThatFits(.unspecified)
        if ideal.width <= width { return ideal }
        return view.sizeThatFits(ProposedViewSize(width: width, height: nil))
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? 10_000
        var x: CGFloat = 0
        var y: CGFloat = 0
        var line: CGFloat = 0
        var widest: CGFloat = 0
        for view in subviews {
            let size = fit(view, within: width)
            if x > 0 && x + size.width > width {
                y += line + spacing
                x = 0
                line = 0
            }
            x += size.width + spacing
            line = max(line, size.height)
            widest = max(widest, x - spacing)
        }
        return CGSize(width: min(widest, width), height: y + line)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX
        var y = bounds.minY
        var line: CGFloat = 0
        for view in subviews {
            let size = fit(view, within: bounds.width)
            if x > bounds.minX && x + size.width > bounds.maxX {
                y += line + spacing
                x = bounds.minX
                line = 0
            }
            view.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
            x += size.width + spacing
            line = max(line, size.height)
        }
    }
}

/// One view at its own width up to a most (`flex: 0 0 auto; max-width`):
/// past that its words wrap.
struct CappedWidth: Layout {
    var most: CGFloat

    private func fit(_ view: LayoutSubview) -> CGSize {
        let ideal = view.sizeThatFits(.unspecified)
        if ideal.width <= most { return ideal }
        return view.sizeThatFits(ProposedViewSize(width: most, height: nil))
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        guard let view = subviews.first else { return .zero }
        return fit(view)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        guard let view = subviews.first else { return }
        view.place(at: CGPoint(x: bounds.minX, y: bounds.minY),
                   proposal: ProposedViewSize(width: bounds.width, height: bounds.height))
    }
}

/// `.fw-chip`: 36 tall (a floor), padding 7 13, radius 18, Geist 13
/// semibold; copper once followed (`.on`), ringed while its drawer is open
/// (`.open`), dashed and muted for "More" and "Follow <words> +" (`.ghost`).
/// `leaf` is a drawer's own chip: medium weight, on the page's colour.
struct FollowChip: View {
    let label: String
    var on = false
    var open = false
    var ghost = false
    var leaf = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(label)
                .font(FAMFont.body(13, leaf ? .medium : .semibold))
                .foregroundStyle(on ? FAMColor.ink : (ghost ? FAMColor.textMuted : FAMColor.text))
                .multilineTextAlignment(.leading)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 13)
                .padding(.vertical, 7)
                .frame(minHeight: 36)
                .background(fill, in: RoundedRectangle(cornerRadius: 18))
                .overlay(RoundedRectangle(cornerRadius: 18)
                    .stroke(on ? FAMColor.copper : FAMColor.borderStrong,
                            style: StrokeStyle(lineWidth: 1, dash: ghost ? [3, 3] : [])))
                // `box-shadow: 0 0 0 2px var(--bg), 0 0 0 3.5px var(--copper)`.
                .overlay(RoundedRectangle(cornerRadius: 20.75)
                    .stroke(FAMColor.copper, lineWidth: 1.5)
                    .padding(-2.75)
                    .opacity(open ? 1 : 0))
        }
        .buttonStyle(.press)
        .accessibilityAddTraits(on ? .isSelected : [])
    }

    private var fill: Color {
        if on { return FAMColor.copper }
        if ghost { return Color.clear }
        return leaf ? FAMColor.background : FAMColor.surface
    }
}

/// The Made for you row the choices lead with (`#followPreview`), in the
/// follow step's dock: the server's own tiles (`/api/follow/preview`),
/// never a mock-up. Scrolled to its end as tiles are added, as the web's.
struct FollowPreviewRow: View {
    let tiles: [Tile]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Made for you \u{00B7} preview")
                .monoLabel(9.5, color: FAMColor.copper, tracking: 1.33)
                .padding(.horizontal, 4)
                .padding(.bottom, 7)
            if tiles.isEmpty {
                Text("Follow something specific and its episode shows up here.")
                    .font(FAMFont.body(11.5))
                    .foregroundStyle(FAMColor.textMuted)
                    .lineSpacing(2)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 4)
            } else {
                ScrollViewReader { proxy in
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(alignment: .top, spacing: 9) {
                            ForEach(tiles) { tile in
                                card(tile).id(tile.id)
                            }
                        }
                        .padding(.horizontal, 4)
                        .padding(.bottom, 2)
                    }
                    .onChange(of: tiles.last?.id) { _, last in
                        if let last { proxy.scrollTo(last, anchor: .trailing) }
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, -4)
        .padding(.bottom, 12)
    }

    /// `.fw-tile`: 172 wide, the 34 pt art in its topic's colour, the
    /// title Geist 11.5 semibold on three lines at most.
    private func card(_ tile: Tile) -> some View {
        HStack(alignment: .center, spacing: 9) {
            SubjectIcon(name: tile.icon, size: 18)
                .foregroundStyle(FollowHues.cream)
                .frame(width: 34, height: 34)
                .background(FollowHues.tile(tile.tags), in: RoundedRectangle(cornerRadius: 10))
            Text(tile.title)
                .font(FAMFont.body(11.5, .semibold))
                .foregroundStyle(FAMColor.text)
                .lineLimit(3)
                .multilineTextAlignment(.leading)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .padding(.leading, 8)
        .padding(.trailing, 10)
        .padding(.vertical, 8)
        .frame(width: 172, alignment: .leading)
        .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 13))
        .overlay(RoundedRectangle(cornerRadius: 13).stroke(FAMColor.border))
        .accessibilityElement(children: .combine)
    }
}

/// "Follow this?" (`#followAsk`, §696): after a search, the one subject
/// the server named, the first thing on DailyFAM under the free month
/// until it is answered. Follow adds it (`POST /api/follow`); Not now is
/// remembered on this phone. Nothing is drawn without a question.
struct FollowAskCard: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        if let ask = model.followAsk {
            VStack(alignment: .leading, spacing: 0) {
                (Text("After you searched ")
                    + Text("\u{201C}" + ask.q + "\u{201D}").font(FAMFont.body(12, .semibold))
                        .foregroundColor(FAMColor.text))
                    .font(FAMFont.body(12))
                    .foregroundStyle(FAMColor.textMuted)
                    .lineSpacing(2)
                    .fixedSize(horizontal: false, vertical: true)
                Text("Follow " + ask.topic + "?")
                    .font(FAMFont.heading(17, .semibold))
                    .foregroundStyle(FAMColor.text)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 3)
                    .padding(.bottom, 10)
                WrapRow(spacing: 8) {
                    FollowChip(label: "Follow", on: true) { model.answerFollowAsk(true) }
                    FollowChip(label: "Not now") { model.answerFollowAsk(false) }
                }
            }
            .padding(.horizontal, 15)
            .padding(.top, 13)
            .padding(.bottom, 14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 14))
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(FAMColor.borderStrong))
            .padding(.bottom, 14)
        }
    }
}

/// "Your teams" (`.team-strip`, §696): the followed teams' games the live
/// pool is holding (`/api/myfam`'s `teams`), above the rails, each with
/// its score line. A game plays as a DailyFAM tile does. Nothing is drawn
/// when none is held - never a made-up line.
struct TeamStrip: View {
    @EnvironmentObject private var model: AppModel

    private var games: [Tile] { model.teams.filter { !$0.title.isEmpty } }

    var body: some View {
        if !games.isEmpty {
            VStack(alignment: .leading, spacing: 0) {
                Text("Your teams")
                    .monoLabel(9.5, tracking: 1.33)
                    .padding(.horizontal, 2)
                    .padding(.bottom, 8)
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(alignment: .top, spacing: 9) {
                        ForEach(games) { game in
                            CappedWidth(most: 230) { card(game) }
                        }
                    }
                    .padding(.bottom, 2)
                    .padding(.trailing, 14)
                }
                // To the screen's edge, as a rail's cards run.
                .padding(.trailing, -18)
            }
            .padding(.bottom, 16)
        }
    }

    /// `.team-game`: 52 tall (a floor), padding 9 13, radius 14; the title
    /// Geist 12.5 semibold, the score line Geist Mono 10.5 in copper.
    private func card(_ game: Tile) -> some View {
        Button { model.play(game) } label: {
            VStack(alignment: .leading, spacing: 3) {
                Text(game.title)
                    .font(FAMFont.body(12.5, .semibold))
                    .foregroundStyle(FAMColor.text)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
                if !game.liveLine.isEmpty {
                    Text(game.liveLine)
                        .font(FAMFont.mono(10.5))
                        .foregroundStyle(FAMColor.copper)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(.horizontal, 13)
            .padding(.vertical, 9)
            .frame(minHeight: 52, alignment: .leading)
            .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 14))
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(FAMColor.border))
        }
        .buttonStyle(.press)
        .accessibilityHint("Plays the episode")
    }
}
