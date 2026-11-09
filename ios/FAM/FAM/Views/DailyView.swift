import SwiftUI

/// The rails, shown as "DailyFAM" (`names-swapped`): rows of tiles the
/// server ranked, each a title and an angle, played on tap.
struct DailyView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 26) {
                    if model.rails.isEmpty {
                        Group {
                            if model.loadingRails {
                                ProgressView().frame(maxWidth: .infinity)
                            } else {
                                Text(model.railsProblem.isEmpty ? "Nothing is ready yet." : model.railsProblem)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .padding(.horizontal)
                        .padding(.top, 40)
                    }
                    ForEach(model.rails) { rail in
                        RailRow(rail: rail)
                    }
                }
                .padding(.vertical)
            }
            .navigationTitle("DailyFAM")
            .refreshable { await model.loadRails() }
            .task {
                if model.rails.isEmpty { await model.loadRails() }
            }
        }
    }
}

struct RailRow: View {
    @EnvironmentObject private var model: AppModel
    let rail: Rail

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(rail.title)
                .font(.title3.weight(.semibold))
                .padding(.horizontal)
            if rail.topics.isEmpty {
                Text("Nothing here yet.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal)
            } else {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(alignment: .top, spacing: 12) {
                        ForEach(rail.topics) { tile in
                            Button {
                                model.play(tile)
                            } label: {
                                TileCard(tile: tile)
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .padding(.horizontal)
                }
            }
        }
    }
}

/// A card: the picture (4:3) and the whole title, never cut.
struct TileCard: View {
    @EnvironmentObject private var model: AppModel
    let tile: Tile

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Picture(url: model.api.picture(tile.thumb))
                .frame(width: 160, height: 120)
                .clipShape(RoundedRectangle(cornerRadius: 12))
            Text(tile.title)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.primary)
                .fixedSize(horizontal: false, vertical: true)
            if !tile.subtitle.isEmpty {
                Text(tile.subtitle)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
        }
        .frame(width: 160, alignment: .leading)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isButton)
        .accessibilityHint("Plays the episode")
    }
}

/// An episode's picture, or a quiet panel while there is none.
struct Picture: View {
    let url: URL?

    var body: some View {
        ZStack {
            Rectangle().fill(Color.secondary.opacity(0.15))
            if let url {
                AsyncImage(url: url) { image in
                    image.resizable().scaledToFill()
                } placeholder: {
                    Color.clear
                }
            }
        }
        .clipped()
        .accessibilityHidden(true)
    }
}
