import SwiftUI

/// The rails, shown as "DailyFAM" (`names-swapped`): the wordmark centred at
/// the top, then each rail's heading in the display face and a sideways row
/// of cards (`.feed-section`, `.feed-rail`, `.seed-card`).
struct DailyView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(spacing: 0) {
            Wordmark(lead: "daily", size: 28)
                .frame(maxWidth: .infinity, minHeight: 48)
                .padding(.top, 4)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    if model.rails.isEmpty {
                        Group {
                            if model.loadingRails {
                                ProgressView().frame(maxWidth: .infinity)
                            } else {
                                EmptyNote(text: model.railsProblem.isEmpty ? "Nothing is ready yet." : model.railsProblem)
                            }
                        }
                        .padding(.top, 40)
                    }
                    ForEach(model.rails) { rail in
                        RailRow(rail: rail)
                    }
                }
                .padding(.horizontal, 18)
                .padding(.bottom, 24)
            }
            .refreshable { await model.loadRails() }
        }
        .background(FAMColor.background)
        .task {
            if model.rails.isEmpty { await model.loadRails() }
        }
    }
}

struct RailRow: View {
    @EnvironmentObject private var model: AppModel
    let rail: Rail

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(rail.title)
                .font(FAMFont.heading(20))
                .foregroundStyle(FAMColor.text)
                .lineSpacing(3)
                .padding(.top, 26)
                .padding(.bottom, 12)
                .accessibilityAddTraits(.isHeader)
            if rail.topics.isEmpty {
                EmptyNote(text: "Nothing here yet.")
            } else {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(alignment: .top, spacing: 11) {
                        ForEach(rail.topics) { tile in
                            Button {
                                model.play(tile)
                            } label: {
                                TileCard(tile: tile)
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .padding(.trailing, 14)
                    .padding(.vertical, 2)
                }
                .padding(.trailing, -18)
            }
        }
    }
}

/// A card (`.seed-card`): the picture with its subject on a cream chip,
/// then the whole title - never cut - and the card's one line.
struct TileCard: View {
    @EnvironmentObject private var model: AppModel
    let tile: Tile

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ZStack(alignment: .bottomLeading) {
                Picture(url: model.api.picture(tile.thumb))
                    .frame(width: 150, height: 96)
                if !tile.thumbFacet.isEmpty {
                    Text(tile.thumbFacet)
                        .monoLabel(8, color: Color(hex: 0x2A2530))
                        .padding(.horizontal, 6)
                        .padding(.vertical, 3)
                        .background(Color(red: 237 / 255, green: 230 / 255, blue: 214 / 255).opacity(0.92),
                                    in: RoundedRectangle(cornerRadius: 4))
                        .padding(8)
                }
            }
            VStack(alignment: .leading, spacing: 6) {
                Text(tile.title)
                    .font(FAMFont.heading(tile.title.count > 60 ? 11.5 : 12.5, .semibold))
                    .foregroundStyle(FAMColor.text)
                    .lineSpacing(1)
                    .fixedSize(horizontal: false, vertical: true)
                if !tile.hook.isEmpty {
                    Text(tile.hook)
                        .font(FAMFont.body(10))
                        .foregroundStyle(FAMColor.textMuted)
                        .lineSpacing(2)
                        .lineLimit(3)
                }
            }
            .padding(.horizontal, 11)
            .padding(.top, 10)
            .padding(.bottom, 12)
        }
        .frame(width: 150, alignment: .leading)
        .background(FAMColor.surface)
        .clipShape(RoundedRectangle(cornerRadius: 13))
        .overlay(RoundedRectangle(cornerRadius: 13).stroke(FAMColor.border))
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isButton)
        .accessibilityHint("Plays the episode")
    }
}

/// An episode's picture over the thumbnail ground (`.seed-thumb`).
struct Picture: View {
    let url: URL?

    var body: some View {
        ZStack {
            Rectangle().fill(FAMColor.thumb)
            if let url {
                AsyncImage(url: url) { image in
                    image.resizable().scaledToFill()
                } placeholder: {
                    Color.clear
                }
            } else {
                FAMLetters(capHeight: 18, color: FAMColor.copper.opacity(0.35))
            }
        }
        .clipped()
        .accessibilityHidden(true)
    }
}

/// The dashed box a rail shows when it has nothing (`.feed-empty`).
struct EmptyNote: View {
    let text: String

    var body: some View {
        Text(text)
            .font(FAMFont.body(11))
            .foregroundStyle(FAMColor.textMuted)
            .lineSpacing(3)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(14)
            .overlay(RoundedRectangle(cornerRadius: 12)
                .stroke(FAMColor.borderStrong, style: StrokeStyle(lineWidth: 1, dash: [4, 3])))
    }
}
