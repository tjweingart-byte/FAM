import SwiftUI
import UIKit

// What changed since you last listened (§545), the owner's picks from the
// signal designs, drawn as `static/index.html` draws them:
//
// * R5 on the myFAM tab (`mixSignalCardHTML`): the story's picture with the
//   mix's own cover in its corner, ringed in the signal's colour; the name
//   and one line naming the topic and the story; a "Trending" or "Major
//   update" pill where the arrow was.
// * Q5 inside the mix: the changed topic's row outlined, the episode last
//   heard and today's as two bubbles joined by an arrow (`mixBubblesHTML`),
//   "Start with what changed" and "or play all N from the top".
// * A picture on every topic row (`mixTopicPicHTML`): the one the player
//   will draw for it, else the subject's icon on paper.
//
// The colour always comes with its word.

extension FAMColor {
    /// `--sig-tr`: trending gold (lighter than copper in dark, so small text
    /// on a card reads).
    static let sigTrending = Color(light: 0x8A5F14, dark: 0xE0B563)
    /// `--sig-up`: a major update, coral.
    static let sigUpdate = Color(light: 0xB4441F, dark: 0xF08A6E)
    /// `--sig-ink`: the ink a filled pill takes.
    static let sigInk = Color(light: 0xFFFFFF, dark: 0x18151F)

    /// The signal's colour: coral for a result in, gold for anything else.
    static func signal(_ update: Bool) -> Color {
        update ? sigUpdate : sigTrending
    }
}

/// The two signals' icons (`sigIconSVG`): a trend line, and a bolt for a
/// result in. Stroked at 2.6 with round caps and joins, in a 24 box.
enum SignalIcons {
    /// `M3 17l6-6 4 4 8-8` and `M15 7h6v6`.
    static let trending: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 3, y: 17))
        p.addLine(to: CGPoint(x: 9, y: 11))
        p.addLine(to: CGPoint(x: 13, y: 15))
        p.addLine(to: CGPoint(x: 21, y: 7))
        p.move(to: CGPoint(x: 15, y: 7))
        p.addLine(to: CGPoint(x: 21, y: 7))
        p.addLine(to: CGPoint(x: 21, y: 13))
        return p
    }()

    /// `M13 2L4 14h7l-1 8 9-12h-7z`.
    static let update: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 13, y: 2))
        p.addLine(to: CGPoint(x: 4, y: 14))
        p.addLine(to: CGPoint(x: 11, y: 14))
        p.addLine(to: CGPoint(x: 10, y: 22))
        p.addLine(to: CGPoint(x: 19, y: 10))
        p.addLine(to: CGPoint(x: 12, y: 10))
        p.closeSubpath()
        return p
    }()

    static func path(_ isUpdate: Bool) -> Path {
        isUpdate ? update : trending
    }

    /// The icon as a template image, for a line of text that wraps with it
    /// in it (`.mix-sig-line svg`: inline, 2 pt under the baseline, 3 pt
    /// before the words). The 3 pt is drawn into the image's width.
    static func inline(_ isUpdate: Bool, size: CGFloat) -> UIImage {
        let scale = size / 24
        let drawn = path(isUpdate).applying(CGAffineTransform(scaleX: scale, y: scale))
        let cgPath = drawn.cgPath
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: size + 3, height: size))
        let image = renderer.image { context in
            let g = context.cgContext
            g.addPath(cgPath)
            g.setLineWidth(2.6 * scale)
            g.setLineCap(.round)
            g.setLineJoin(.round)
            g.setStrokeColor(UIColor.black.cgColor)
            g.strokePath()
        }
        return image.withRenderingMode(.alwaysTemplate)
    }

    /// The inline icon as a piece of a `Text`.
    static func text(_ isUpdate: Bool, size: CGFloat, color: Color) -> Text {
        let image = Image(uiImage: inline(isUpdate, size: size)).renderingMode(.template)
        return Text(image).foregroundColor(color).baselineOffset(-2)
    }
}

/// The signal's ring round a picture (`box-shadow: 0 0 0 2px var(--surface),
/// 0 0 0 4px var(--sig-c)`): 2 pt of the card's surface, then 2 pt of the
/// signal's colour, outside the picture and following its corners.
struct SignalRing: View {
    let radius: CGFloat
    let color: Color

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: radius + 1)
                .stroke(FAMColor.surface, lineWidth: 2)
                .padding(-1)
            RoundedRectangle(cornerRadius: radius + 3)
                .stroke(color, lineWidth: 2)
                .padding(-3)
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

/// A picture the server picked (`/api/...`, or a data URL in a preview),
/// filling its frame; nothing while it loads, as the web's `<img>`.
struct MixPicture: View {
    @EnvironmentObject private var model: AppModel
    let source: String

    var body: some View {
        Group {
            if let image = MixCover.image(source) {
                Image(uiImage: image).resizable().scaledToFill()
            } else if let url = model.api.picture(source) {
                AsyncImage(url: url) { phase in
                    if let image = phase.image {
                        image.resizable().scaledToFill()
                    } else {
                        Color.clear
                    }
                }
            } else {
                Color.clear
            }
        }
        .accessibilityHidden(true)
    }
}

// MARK: - R5, the mix card

/// A mix card when one of its topics changed since this listener last
/// heard it (`mixSignalCardHTML`). The whole card opens the mix; the pill
/// is a label, not a button, as on the web.
struct MixSignalCard: View {
    let mix: Mix
    let signal: MixSignal
    @Environment(\.dynamicTypeSize) private var typeSize

    private var color: Color { FAMColor.signal(signal.isUpdate) }

    var body: some View {
        HStack(alignment: .center, spacing: 13) {
            picture
            VStack(alignment: .leading, spacing: 0) {
                // `#mixList .mix-name`: no margin under it.
                Text(mix.name)
                    .font(FAMFont.heading(14, .semibold))
                    .foregroundStyle(FAMColor.text)
                    .multilineTextAlignment(.leading)
                line
                    .padding(.top, 3)
                // At the accessibility sizes the pill takes its own row
                // under the words rather than squeezing the name
                // (`large-text`).
                if typeSize.isAccessibilitySize {
                    pill.padding(.top, 7)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if !typeSize.isAccessibilitySize {
                pill
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 13)
        .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(color))
        .padding(.bottom, 11)
        .contentShape(Rectangle())
    }

    /// The story's picture ringed, the mix's cover small in its lower-left
    /// corner; with no picture approved for the story, the mix's own cover
    /// takes the ring and nothing sits in the corner.
    @ViewBuilder
    private var picture: some View {
        if signal.picture.isEmpty {
            MixArt(cover: mix.cover)
                .overlay(SignalRing(radius: 12, color: color))
        } else {
            MixPicture(source: signal.picture)
                .frame(width: 46, height: 46)
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .overlay(SignalRing(radius: 12, color: color))
                .overlay(alignment: .bottomLeading) { corner }
        }
    }

    /// `.mix-sig-own`: 22 pt, 7 pt corners, a 2 pt surface border, the
    /// folder at 12 pt or the cover.
    private var corner: some View {
        MixArt(cover: mix.cover, size: 18, radius: 5, glyphSize: 12)
            .padding(2)
            .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 7))
            .offset(x: -6, y: 6)
    }

    /// `.mix-sig-line`: the icon, the topic in the signal's colour, " · "
    /// and the story; two lines at most.
    private var line: some View {
        let icon = SignalIcons.text(signal.isUpdate, size: 12, color: color)
        let topic = Text(signal.topic)
            .font(FAMFont.body(11.5, .bold))
            .foregroundColor(color)
        let story = Text(" \u{00B7} " + signal.headline)
            .foregroundColor(FAMColor.text)
        return (icon + topic + story)
            .font(FAMFont.body(11.5))
            .lineSpacing(2)
            .lineLimit(2)
            .multilineTextAlignment(.leading)
    }

    /// `.mix-pill`: the signal's icon, its word and the arrow, ink on the
    /// signal's colour.
    private var pill: some View {
        HStack(spacing: 4) {
            WebIcon(path: SignalIcons.path(signal.isUpdate), size: 12, line: 2.6)
            Text(signal.label)
                .font(FAMFont.body(11, .bold))
                .lineLimit(1)
            WebIcon(path: Icons.chevron, size: 12, line: 2.6)
        }
        .foregroundStyle(FAMColor.sigInk)
        .padding(.leading, 9)
        .padding(.trailing, 6)
        .frame(minHeight: 26)
        .background(color, in: Capsule())
        .fixedSize()
    }
}

// MARK: - Q5, inside the mix

/// A topic row's picture (`.mix-topic-pic`): 40 pt, 10 pt corners, on
/// paper; the subject's icon at 20 pt when nothing is approved; ringed in
/// the signal's colour when the topic changed.
struct MixTopicPic: View {
    let item: MixItem
    let ring: Color?

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 10).fill(FAMColor.paper)
            if item.picture.isEmpty {
                SubjectIcon(name: item.icon, size: 20)
                    .foregroundStyle(FAMColor.ink)
            } else {
                MixPicture(source: item.picture)
            }
        }
        .frame(width: 40, height: 40)
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .overlay {
            if let ring {
                SignalRing(radius: 10, color: ring)
            }
        }
        .accessibilityHidden(true)
    }
}

/// `.mix-sig-tag`: "Trending · since you last listened", the icon in it.
struct MixSignalTag: View {
    let signal: MixSignal

    var body: some View {
        tag
            .font(FAMFont.body(11, .bold))
            .multilineTextAlignment(.leading)
    }

    private var tag: Text {
        let color = FAMColor.signal(signal.isUpdate)
        let icon = SignalIcons.text(signal.isUpdate, size: 11, color: color)
        let words = Text(signal.label + " \u{00B7} since you last listened")
            .foregroundColor(color)
        return icon + words
    }
}

/// Q5's bubbles (`mixBubblesHTML`): what was last heard, then "→ Today:"
/// in the signal's outline. One bubble when they have never heard it. The
/// arrow opens the "Today" bubble so on a narrow row the two stack.
struct MixBubbles: View {
    let since: MixSince
    let color: Color

    var body: some View {
        BubbleFlow(spacing: 5) {
            if let then = since.then {
                thenBubble(then)
                nowBubble(arrow: true)
            } else {
                nowBubble(arrow: false)
            }
        }
    }

    private func thenBubble(_ then: MixSinceSide) -> some View {
        let day = then.day.isEmpty ? "Before" : then.day
        return Text(day + ": " + then.title)
            .font(FAMFont.body(11))
            .foregroundStyle(FAMColor.textMuted)
            .lineSpacing(1)
            .multilineTextAlignment(.leading)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(FAMColor.background, in: RoundedRectangle(cornerRadius: 9))
    }

    private func nowBubble(arrow: Bool) -> some View {
        let title = since.now?.title ?? ""
        let today: Text
        if arrow {
            let mark = Text("\u{2192}")
                .font(FAMFont.body(12, .bold))
                .foregroundColor(color)
            today = mark + Text(" Today: " + title).foregroundColor(FAMColor.text)
        } else {
            today = Text("Today: " + title).foregroundColor(FAMColor.text)
        }
        return today
            .font(FAMFont.body(11))
            .lineSpacing(1)
            .multilineTextAlignment(.leading)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .overlay(RoundedRectangle(cornerRadius: 9).stroke(color, lineWidth: 1))
    }
}

/// `.mix-bubbles`: a row that wraps (`flex-wrap: wrap; gap: 5px;
/// align-items: center`), each bubble offered the whole width so a long
/// one wraps inside itself rather than running off the row.
struct BubbleFlow: Layout {
    var spacing: CGFloat = 5

    private struct Placed {
        let index: Int
        let size: CGSize
    }

    private func lines(width: CGFloat, subviews: Subviews) -> [[Placed]] {
        var out: [[Placed]] = []
        var current: [Placed] = []
        var x: CGFloat = 0
        for index in subviews.indices {
            let size = subviews[index].sizeThatFits(ProposedViewSize(width: width, height: nil))
            let needed = current.isEmpty ? size.width : x + spacing + size.width
            if !current.isEmpty && needed > width {
                out.append(current)
                current = []
                x = 0
            }
            x = current.isEmpty ? size.width : x + spacing + size.width
            current.append(Placed(index: index, size: size))
        }
        if !current.isEmpty { out.append(current) }
        return out
    }

    private func lineHeight(_ line: [Placed]) -> CGFloat {
        var tallest: CGFloat = 0
        for placed in line { tallest = max(tallest, placed.size.height) }
        return tallest
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? 10_000
        let all = lines(width: width, subviews: subviews)
        var height: CGFloat = 0
        var widest: CGFloat = 0
        for (n, line) in all.enumerated() {
            if n > 0 { height += spacing }
            height += lineHeight(line)
            var x: CGFloat = 0
            for (i, placed) in line.enumerated() {
                x += placed.size.width + (i > 0 ? spacing : 0)
            }
            widest = max(widest, x)
        }
        return CGSize(width: min(widest, width), height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let all = lines(width: bounds.width, subviews: subviews)
        var y = bounds.minY
        for line in all {
            let tall = lineHeight(line)
            var x = bounds.minX
            for placed in line {
                let top = y + (tall - placed.size.height) / 2
                subviews[placed.index].place(at: CGPoint(x: x, y: top), proposal: ProposedViewSize(placed.size))
                x += placed.size.width + spacing
            }
            y += tall + spacing
        }
    }
}

/// "Start with what changed" (`.mix-play`, `playWhatChanged`): the first
/// changed topic's signal icon, then the words; "Start with the N that
/// changed" for more than one.
struct MixChangedButton: View {
    let count: Int
    let isUpdate: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 8) {
                WebIcon(path: SignalIcons.path(isUpdate), size: 14, line: 2.6)
                Text(count == 1 ? "Start with what changed" : "Start with the \(count) that changed")
            }
            .font(FAMFont.body(12.5, .bold))
            .foregroundStyle(FAMColor.ink)
            .frame(maxWidth: .infinity, minHeight: 43)
            .background(FAMColor.copper, in: Capsule())
        }
        .buttonStyle(.press)
    }
}

/// "or play all N from the top" (`.mix-play-all`): a link under the button.
struct MixPlayAllLink: View {
    let count: Int
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text("or play all \(count) from the top")
                .font(FAMFont.body(12, .semibold))
                .foregroundStyle(FAMColor.textMuted)
                .multilineTextAlignment(.center)
                .frame(maxWidth: .infinity)
                .padding(6)
                .contentShape(Rectangle())
        }
        .buttonStyle(.press)
    }
}
