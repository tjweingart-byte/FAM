import SwiftUI

// MARK: - Option A, the stacked deck (§616)

/// A mix in the myFAM list, as the owner picked from six on 11.14
/// (`mixCardAHTML`): two to a row; the cover is the mix's colour with
/// today's first three stories fanned on it, best on top, the name across
/// the bottom, and today's headline under the card. With a photo of its
/// own, the photo fills the square and the stack steps aside to a corner.
/// No picture anywhere: the colour and the folder.
struct MixDeckCard: View {
    @EnvironmentObject private var mixes: MixesModel
    let mix: Mix
    /// Its place in the grid, for the deal's stagger.
    var index: Int = 0

    /// The mix cards already dealt on this phone this session: a redraw
    /// never deals again (round three, §642 `mix-cards-arrive` B).
    static let book = ArrivalBook()

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// The three pictures fanned out from a squared stack.
    @State private var fanned: Bool

    init(mix: Mix, index: Int = 0) {
        self.mix = mix
        self.index = index
        _fanned = State(initialValue: Motion.reduced || MixDeckCard.book.seen.contains("fan:" + mix.id))
    }

    private var photo: Bool { !mix.cover.isEmpty }
    private var tiles: [MixCoverTile] { Array(mix.coverTiles.prefix(3)) }

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            cover
            headline
        }
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .onAppear {
            guard !fanned else { return }
            MixDeckCard.book.seen.insert("fan:" + mix.id)
            if reduceMotion { fanned = true; return }
            // Each card's pictures fan 40 ms after it rises (240 ms,
            // `--ease-out`).
            withAnimation(Motion.easeOut(0.24).delay(0.1 + Double(min(index, 3)) * 0.04)) { fanned = true }
        }
    }

    // MARK: The cover

    private var cover: some View {
        Color.clear
            .aspectRatio(1, contentMode: .fit)
            .background { ground }
            .overlay { GeometryReader { geo in stack(in: geo.size) } }
            .overlay(alignment: .bottom) { name }
            .overlay(alignment: .topLeading) {
                if mixes.isFresh(mix.id) {
                    Text("New today")
                        .font(FAMFont.body(9.5, .bold))
                        .foregroundStyle(FAMColor.ink)
                        .padding(.horizontal, 7)
                        .padding(.vertical, 3)
                        .background(FAMColor.copper, in: Capsule())
                        .padding(8)
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: 18))
    }

    @ViewBuilder private var ground: some View {
        if photo {
            ZStack {
                MixDeckHue.color(mix.id)
                MixPicture(source: mix.cover)
                LinearGradient(colors: [.clear, Color(hex: 0x0A090F).opacity(0.62)],
                               startPoint: UnitPoint(x: 0.5, y: 0.45), endPoint: .bottom)
            }
        } else {
            MixDeckHue.color(mix.id)
        }
    }

    /// Fanned: the lead flat on top, the second left and the third right,
    /// each turned 7°. Drawn last-first so the lead lands on top.
    @ViewBuilder private func stack(in size: CGSize) -> some View {
        if tiles.isEmpty {
            if !photo {
                WebIcon(path: Icons.folder, size: size.width * 0.34, line: 1.7)
                    .foregroundStyle(Color(hex: 0x18151F).opacity(0.55))
                    .frame(width: size.width, height: size.height)
            }
        } else {
            let box = stackBox(size)
            let k: CGFloat = fanned ? 1 : 0
            ZStack {
                ForEach(Array(Array(tiles.enumerated()).reversed()), id: \.offset) { pair in
                    tile(pair.element, index: pair.offset)
                        .frame(width: box.width, height: box.height)
                        .rotationEffect(.degrees(rotation(pair.offset) * Double(k)))
                        .offset(x: box.width * shift(pair.offset).x * k, y: box.height * shift(pair.offset).y * k)
                }
            }

            .frame(width: box.width, height: box.height)
            .position(x: box.midX, y: box.midY)
        }
    }

    /// `.mix-a-stack`: 12% down, 15% in, 27% up; a 34% square in the top
    /// right corner over a photo.
    private func stackBox(_ size: CGSize) -> CGRect {
        if photo {
            let side = size.width * 0.34
            return CGRect(x: size.width * 0.93 - side, y: size.height * 0.07, width: side, height: side)
        }
        return CGRect(x: size.width * 0.15, y: size.height * 0.12,
                      width: size.width * 0.70, height: size.height * 0.61)
    }

    private func rotation(_ index: Int) -> Double {
        let turn: Double = photo ? 8 : 7
        return index == 1 ? -turn : (index == 2 ? turn : 0)
    }

    private func shift(_ index: Int) -> CGPoint {
        let x: CGFloat = photo ? 0.14 : 0.09
        switch index {
        case 1: return CGPoint(x: -x, y: photo ? 0.06 : 0.04)
        case 2: return CGPoint(x: x, y: photo ? 0.08 : 0.06)
        default: return .zero
        }
    }

    private func tile(_ t: MixCoverTile, index: Int) -> some View {
        let radius: CGFloat = photo ? 6 : 10
        return MixPicture(source: t.picture)
            .background(FAMColor.surface2)
            .overlay(alignment: .topLeading) {
                if index == 0 && t.ready && !photo {
                    Text("READY")
                        .font(FAMFont.mono(7))
                        .tracking(0.56)
                        .foregroundStyle(FAMColor.ink)
                        .padding(.horizontal, 5)
                        .padding(.vertical, 2)
                        .background(FAMColor.copper, in: Capsule())
                        .padding(5)
                }
            }
            .overlay(alignment: .bottomLeading) {
                if !t.label.isEmpty && !photo {
                    Text(t.label)
                        .font(FAMFont.mono(6.5))
                        .tracking(0.52)
                        .textCase(.uppercase)
                        .lineLimit(1)
                        .foregroundStyle(Color(hex: 0x18151F))
                        .padding(.horizontal, 4)
                        .padding(.vertical, 2)
                        .background(Color(hex: 0xF4EFE4).opacity(0.92), in: RoundedRectangle(cornerRadius: 3))
                        .padding(5)
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: radius))
            .overlay(RoundedRectangle(cornerRadius: radius)
                .stroke(Color.white.opacity(0.8), lineWidth: photo ? 1 : 1.5))
            .shadow(color: .black.opacity(0.45), radius: 7, y: 5)
    }

    private var name: some View {
        Text(mix.name)
            .font(FAMFont.heading(16, .semibold))
            .foregroundStyle(Color.white)
            .multilineTextAlignment(.center)
            .lineLimit(2)
            .shadow(color: .black.opacity(0.4), radius: 4, y: 1)
            .padding(.horizontal, 8)
            .padding(.bottom, 9)
    }

    // MARK: The headline (`mixHeadlineHTML`)

    @ViewBuilder private var headline: some View {
        if let h = mix.headline, !h.title.isEmpty {
            (whyLead(h) + Text(h.topic.isEmpty ? "" : " \u{00B7} ") + Text(h.title).foregroundColor(FAMColor.text))
                .font(FAMFont.body(11.5))
                .lineSpacing(2)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 2)
        } else {
            Text(quietLine)
                .font(FAMFont.body(11.5))
                .foregroundStyle(FAMColor.textMuted)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 2)
        }
    }

    /// The topic, bold, in the colour of why it leads: a result in (coral),
    /// trending (gold), most covered (copper), else muted.
    private func whyLead(_ h: MixHeadline) -> Text {
        let color: Color
        let mark: String
        switch h.why {
        case "update": color = FAMColor.sigUpdate; mark = "\u{26A1}\u{FE0E} "
        case "trending": color = FAMColor.sigTrending; mark = "\u{2197}\u{FE0E} "
        case "covered": color = FAMColor.copper; mark = ""
        default: color = FAMColor.textMuted; mark = ""
        }
        return Text(mark + h.topic).fontWeight(.bold).foregroundColor(color)
    }

    /// No headline yet: the topics in today's order, else "No topics yet".
    private var quietLine: String {
        let names = mix.playOrder.prefix(3).map { mix.items[$0].shownTitle }.filter { !$0.isEmpty }
        return names.isEmpty ? "No topics yet" : names.joined(separator: " \u{00B7} ")
    }
}

/// The mix's colour behind its stack (`mixHue`): one of six, the same for a
/// mix on every draw and on both clients. A colour, never a picture.
enum MixDeckHue {
    static let hues: [UInt32] = [0x7FB8E0, 0xF0A04B, 0x9A86C9, 0x7DBE9C, 0xE58A74, 0xD9B45C]

    static func color(_ id: String) -> Color {
        var h: UInt32 = 0
        for unit in id.utf16 { h = h &* 31 &+ UInt32(unit) }
        return Color(hex: hues[Int(h % UInt32(hues.count))])
    }
}
