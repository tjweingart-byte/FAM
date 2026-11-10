import SwiftUI

/// Which interest the Topic screen is for: the chip's id (a subject like
/// `f:nfl` or a facet) and the words it showed.
struct TopicOpen: Hashable {
    var id: String
    var label: String
}

/// One interest, as a page of episodes (`#screen-topic`, `openTopic`):
/// behind every interest chip - YourFAM's, a friend's profile, a chat's
/// header. It generates nothing to fill itself: every card is an episode
/// that already exists, a tile from the bank or the live pool
/// (`/api/interest`). Latest is the freshest on the subject; Friends vibed
/// is what the people you follow chose to show. Each card's badge is the
/// length a tap writes it at (myFAM's own, `BROWSE_MINUTES`), not the
/// length it happened to be made at.
struct TopicView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var social: SocialModel
    @Environment(\.dismiss) private var dismiss
    let open: TopicOpen

    @State private var label = ""
    @State private var filter = "latest"
    @State private var cards: [InterestCard] = []
    @State private var more = false
    @State private var reason = ""
    @State private var loading = true
    /// Bumped on every new filter, so an answer for the old one is dropped.
    @State private var seq = 0

    /// `TOPIC_HUES`: one of six dark hues, chosen by the words, so the same
    /// episode is always the same colour.
    static let hues: [UInt32] = [0x4A3F2C, 0x2F4038, 0x3D3450, 0x4A3330, 0x2E3D4A, 0x3F3A2A]

    static func hue(_ text: String) -> Color {
        var sum = 0
        for unit in text.utf16 {
            sum = (sum * 31 + Int(unit)) % 9973
        }
        return Color(hex: hues[sum % hues.count])
    }

    var body: some View {
        VStack(spacing: 0) {
            bar
            ScrollView {
                content
                    .padding(.horizontal, 16)
                    .padding(.bottom, 24)
            }
        }
        .background(FAMColor.background)
        .toolbar(.hidden, for: .navigationBar)
        .task {
            if label.isEmpty { label = open.label.isEmpty ? open.id : open.label }
            await load(append: false)
        }
    }

    // MARK: - The bar (`.yf-bar`, "INTEREST")

    private var bar: some View {
        HStack(spacing: 6) {
            SocialBackButton { dismiss() }
                .padding(.leading, -8)
            Text("Interest")
                .font(FAMFont.mono(10))
                .tracking(1.6)
                .textCase(.uppercase)
                .foregroundStyle(FAMColor.textMuted)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 16)
        .padding(.top, 12)
        .padding(.bottom, 4)
    }

    // MARK: - The page

    private var content: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(label)
                .font(FAMFont.heading(27, .medium))
                .foregroundStyle(FAMColor.text)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 4)
                .padding(.bottom, 5)
                .accessibilityAddTraits(.isHeader)
            Text("Episodes on this topic, freshest first")
                .font(FAMFont.body(11.5))
                .foregroundStyle(FAMColor.textMuted)
            filters.padding(.top, 13)
            listing
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var filters: some View {
        HStack(spacing: 7) {
            filterButton("Latest", "latest")
            filterButton("Friends vibed", "friends")
        }
    }

    private func filterButton(_ title: String, _ value: String) -> some View {
        let on = filter == value
        return Button { setFilter(value) } label: {
            Text(title)
                .font(FAMFont.body(11, on ? .bold : .regular))
                .foregroundStyle(on ? FAMColor.ink : FAMColor.text)
                .padding(.horizontal, 11)
                .frame(minHeight: 26)
                .background(on ? FAMColor.copper : Color.clear, in: Capsule())
                .overlay(Capsule().stroke(on ? FAMColor.copper : FAMColor.borderStrong))
        }
        .buttonStyle(.press)
        .accessibilityAddTraits(on ? .isSelected : [])
    }

    @ViewBuilder private var listing: some View {
        if loading && cards.isEmpty {
            empty("Loading\u{2026}")
        } else if cards.isEmpty {
            empty(reason.isEmpty ? "Nothing on this yet." : reason)
        } else {
            grid.padding(.top, 14)
            if more {
                Button { Task { await load(append: true) } } label: {
                    Text("View more")
                        .font(FAMFont.body(12.5))
                        .foregroundStyle(FAMColor.text)
                        .frame(maxWidth: .infinity, minHeight: 40)
                        .overlay(Capsule().stroke(FAMColor.borderStrong))
                        .contentShape(Capsule())
                }
                .buttonStyle(.press)
                .padding(.top, 12)
            }
        }
    }

    /// `.pf-empty` inside `.yf-topic`: 8 pt below the filters.
    private func empty(_ text: String) -> some View {
        Text(text)
            .font(FAMFont.body(11))
            .foregroundStyle(FAMColor.textMuted)
            .lineSpacing(5)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(15)
            .overlay(RoundedRectangle(cornerRadius: 12)
                .stroke(FAMColor.borderStrong, style: StrokeStyle(lineWidth: 1, dash: [4, 3])))
            .padding(.top, 8)
    }

    private var grid: some View {
        let columns = [GridItem(.flexible(), spacing: 10, alignment: .top),
                       GridItem(.flexible(), spacing: 10, alignment: .top)]
        return LazyVGrid(columns: columns, alignment: .leading, spacing: 10) {
            ForEach(Array(cards.enumerated()), id: \.offset) { index, card in
                cardView(card, index: index)
            }
        }
    }

    private func cardView(_ card: InterestCard, index: Int) -> some View {
        let title = (card.title ?? "").isEmpty ? card.query : (card.title ?? "")
        return Button { play(card) } label: {
            VStack(alignment: .leading, spacing: 0) {
                HStack(alignment: .bottom) {
                    Text(FAMAPI.minutes(FAMAPI.browseMinutes) + " MIN")
                        .font(FAMFont.mono(8.5))
                        .foregroundStyle(FAMColor.text)
                        .padding(.horizontal, 5)
                        .padding(.vertical, 2)
                        .background(Color(red: 20 / 255, green: 18 / 255, blue: 26 / 255).opacity(0.55),
                                    in: RoundedRectangle(cornerRadius: 5))
                    Spacer(minLength: 0)
                    TopicPlayDisc()
                }
                .padding(8)
                .frame(maxWidth: .infinity, minHeight: 68, alignment: .bottom)
                .background(Self.hue(card.query))
                Text(title)
                    .font(FAMFont.heading(12.5, .medium))
                    .foregroundStyle(FAMColor.text)
                    .lineLimit(3)
                    .lineSpacing(1)
                    .multilineTextAlignment(.leading)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 10)
                    .padding(.top, 8)
                    .padding(.bottom, 10)
            }
            .background(FAMColor.surface)
            .clipShape(RoundedRectangle(cornerRadius: 13))
            .contentShape(RoundedRectangle(cornerRadius: 13))
        }
        .buttonStyle(.press)
        .accessibilityLabel("Play episode: " + title)
    }

    // MARK: - Asking

    private func setFilter(_ value: String) {
        guard filter != value else { return }
        filter = value
        cards = []
        more = false
        reason = ""
        loading = true
        seq += 1
        Task { await load(append: false) }
    }

    /// `loadTopic`: a second View more while one is in flight would ask for
    /// the same offset and draw the same page twice.
    private func load(append: Bool) async {
        if append {
            if loading { return }
            loading = true
        }
        let mine = seq
        let offset = append ? cards.count : 0
        do {
            let page = try await model.api.interest(id: open.id.isEmpty ? open.label : open.id,
                                                    label: label, filter: filter, offset: offset)
            guard mine == seq else { return }
            if let named = page.label, !named.isEmpty { label = named }
            cards = (append ? cards : []) + page.episodes
            more = page.more ?? false
            reason = page.reason ?? ""
            loading = false
        } catch {
            guard mine == seq else { return }
            loading = false
            reason = "Could not load episodes on this. Nothing is lost \u{2014} try again in a moment."
        }
    }

    /// `playTopicCard`: the card's question at myFAM's length, its title
    /// kept; a friend's vibe says who. A social cover closes first, so the
    /// loading screen and the player are not under it.
    private func play(_ card: InterestCard) {
        let title = (card.title ?? "").isEmpty ? card.query : (card.title ?? "")
        let ep = Episode(key: EpisodeKey(query: card.query, minutes: FAMAPI.browseMinutes),
                         title: title, surface: "other")
        let app = model
        social.closeCover { app.play(ep) }
    }
}

/// `.yf-round.gold` at 28 pt with `yfPlaySVG(11)` (`M8 5v14l11-7z`).
private struct TopicPlayDisc: View {
    var body: some View {
        TopicPlayMark()
            .fill(FAMColor.ink)
            .frame(width: 11, height: 11)
            .frame(width: 28, height: 28)
            .background(FAMColor.copper, in: Circle())
            .accessibilityHidden(true)
    }
}

private struct TopicPlayMark: Shape {
    func path(in rect: CGRect) -> Path {
        let sx = rect.width / 24
        let sy = rect.height / 24
        var p = Path()
        p.move(to: CGPoint(x: 8 * sx, y: 5 * sy))
        p.addLine(to: CGPoint(x: 8 * sx, y: 19 * sy))
        p.addLine(to: CGPoint(x: 19 * sx, y: 12 * sy))
        p.closeSubpath()
        return p
    }
}

extension PersonPage {
    /// `interestRows`: their chips as Topic screens - the id beside each
    /// label, else the label itself.
    var topics: [TopicOpen] {
        let ids = interests ?? []
        let labels = interestLabels ?? []
        var rows: [TopicOpen] = []
        for (i, label) in labels.enumerated() {
            let id = i < ids.count && !ids[i].isEmpty ? ids[i] : label
            rows.append(TopicOpen(id: id, label: label))
        }
        return rows
    }
}

/// An interest chip that opens its Topic screen (`.yf-chip` as a button).
struct InterestChipButton: View {
    let topic: TopicOpen
    var height: CGFloat = 23
    var size: CGFloat = 10
    let open: (TopicOpen) -> Void

    var body: some View {
        Button { open(topic) } label: {
            InterestChip(label: topic.label, height: height, size: size)
                .contentShape(Capsule())
        }
        .buttonStyle(.press)
        .accessibilityHint("Opens episodes on this interest")
    }
}
