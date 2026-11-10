import SwiftUI
import UIKit

/// An episode's picture over the thumbnail ground (`.seed-thumb`); the FAM
/// letters, faint, while there is none. On a card (`blurUp`), a picture
/// that lands after the tile was drawn blurs up (`.seed-img`, `BLUR_UP`:
/// from clear, blur 8 and 1.12 to sharp over 320 ms); one already on the
/// phone just shows.
struct Picture: View {
    let url: URL?
    /// Laid over something of its own - a card's drawing (`seedThumbOpen`)
    /// - so no ground: while it loads, or if it fails, that shows through.
    var clearGround = false
    /// A card's picture (`.seed-img`): blurs up when it lands late.
    var blurUp = false

    @State private var drawnAt = Date()

    var body: some View {
        ZStack {
            if !clearGround {
                Rectangle().fill(FAMColor.thumb)
            }
            if let url {
                AsyncImage(url: url) { phase in
                    pictureContent(phase)
                }
            } else if !clearGround {
                FAMLetters(capHeight: 18, color: FAMColor.copper.opacity(0.35))
            }
        }
        .clipped()
        .accessibilityHidden(true)
    }

    @ViewBuilder private func pictureContent(_ phase: AsyncImagePhase) -> some View {
        if let image = phase.image {
            // Within a tenth of a second of the tile being drawn it was
            // already here: no motion (`data-drawn`, 100 ms).
            let late = blurUp && Date().timeIntervalSince(drawnAt) >= 0.1
            PictureBlurUp(image: image, animate: late)
        } else {
            Color.clear
        }
    }
}

/// `BLUR_UP` on a card's picture: held clear, blurred 8 and at 1.12, then
/// brought sharp over 320 ms; still with reduced motion.
private struct PictureBlurUp: View {
    let image: Image
    @State private var sharp: Bool

    init(image: Image, animate: Bool) {
        self.image = image
        _sharp = State(initialValue: !animate || UIAccessibility.isReduceMotionEnabled)
    }

    var body: some View {
        image.resizable().scaledToFill()
            .opacity(sharp ? 1 : 0)
            .blur(radius: sharp ? 0 : 8)
            .scaleEffect(sharp ? 1 : 1.12)
            .onAppear {
                guard !sharp else { return }
                withAnimation(.timingCurve(0.2, 0.8, 0.2, 1, duration: 0.32)) { sharp = true }
            }
    }
}

/// The dashed box a rail shows when it has nothing (`.feed-empty`).
struct EmptyNote: View {
    let text: String

    var body: some View {
        Text(text)
            .font(FAMFont.body(10.5))
            .foregroundStyle(FAMColor.textMuted)
            .lineSpacing(3)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 13)
            .padding(.vertical, 14)
            .overlay(RoundedRectangle(cornerRadius: 12)
                .stroke(FAMColor.borderStrong, style: StrokeStyle(lineWidth: 1, dash: [4, 3])))
    }
}

/// A card with no picture (`seedThumbSVG`): a faint squiggle and one line
/// drawing by the tile's `icon` - sports, golf, business, tech, rocket,
/// camera, music, leaf (the default) or food - in the web's 160 x 120 box,
/// fitted and centred the way the web's `xMidYMid meet` does.
struct SeedThumb: View {
    let icon: String

    private static let ink = Color(hex: 0x2A2530)

    var body: some View {
        Canvas { context, size in
            let scale = min(size.width / 160, size.height / 120)
            let dx = (size.width - 160 * scale) / 2
            let dy = (size.height - 120 * scale) / 2
            context.translateBy(x: dx, y: dy)
            context.scaleBy(x: scale, y: scale)
            context.stroke(Self.squiggle, with: .color(Self.ink.opacity(0.22)), lineWidth: 1.5)
            for part in Self.parts(icon) {
                if part.fill {
                    context.fill(part.path, with: .color(Self.ink))
                } else {
                    context.stroke(part.path, with: .color(Self.ink), lineWidth: part.width)
                }
            }
        }
        .background(FAMColor.thumb)
        .accessibilityHidden(true)
    }

    struct Part {
        let path: Path
        let width: CGFloat
        let fill: Bool
    }

    /// `M-10 90 Q35 25 75 65 T170 35`.
    static let squiggle: Path = {
        var p = Path()
        p.move(to: CGPoint(x: -10, y: 90))
        p.addQuadCurve(to: CGPoint(x: 75, y: 65), control: CGPoint(x: 35, y: 25))
        p.addQuadCurve(to: CGPoint(x: 170, y: 35), control: CGPoint(x: 115, y: 105))
        return p
    }()

    private static func line(_ x1: CGFloat, _ y1: CGFloat, _ x2: CGFloat, _ y2: CGFloat) -> Path {
        var p = Path()
        p.move(to: CGPoint(x: x1, y: y1))
        p.addLine(to: CGPoint(x: x2, y: y2))
        return p
    }

    private static func circle(_ cx: CGFloat, _ cy: CGFloat, _ r: CGFloat) -> Path {
        Path(ellipseIn: CGRect(x: cx - r, y: cy - r, width: r * 2, height: r * 2))
    }

    private static func poly(_ points: [CGPoint], closed: Bool = true) -> Path {
        var p = Path()
        p.addLines(points)
        if closed { p.closeSubpath() }
        return p
    }

    static func parts(_ name: String) -> [Part] {
        switch name {
        case "sports":
            var cross = Path()
            cross.addPath(line(58, 60, 102, 60))
            cross.addPath(line(80, 34, 80, 86))
            cross.addPath(line(64, 42, 96, 78))
            cross.addPath(line(96, 42, 64, 78))
            return [Part(path: circle(80, 60, 26), width: 2.2, fill: false),
                    Part(path: cross, width: 1.4, fill: false)]
        case "golf":
            return [Part(path: line(80, 28, 80, 92), width: 2.2, fill: false),
                    Part(path: poly([CGPoint(x: 80, y: 28), CGPoint(x: 108, y: 39), CGPoint(x: 80, y: 50)]),
                         width: 0, fill: true),
                    Part(path: Path(ellipseIn: CGRect(x: 56, y: 86, width: 48, height: 12)), width: 1.6, fill: false)]
        case "business":
            var bars = Path()
            bars.addRect(CGRect(x: 44, y: 55, width: 14, height: 37))
            bars.addRect(CGRect(x: 64, y: 40, width: 14, height: 52))
            bars.addRect(CGRect(x: 84, y: 24, width: 14, height: 68))
            bars.addRect(CGRect(x: 104, y: 47, width: 14, height: 45))
            return [Part(path: bars, width: 2, fill: false)]
        case "tech":
            var eyes = Path()
            eyes.addPath(circle(70, 58, 4))
            eyes.addPath(circle(90, 58, 4))
            return [Part(path: Path(roundedRect: CGRect(x: 52, y: 34, width: 56, height: 48), cornerRadius: 14),
                         width: 2.2, fill: false),
                    Part(path: eyes, width: 0, fill: true),
                    Part(path: line(80, 34, 80, 20), width: 2, fill: false),
                    Part(path: circle(80, 17, 3), width: 2, fill: false)]
        case "rocket":
            var fins = Path()
            fins.addPath(line(62, 74, 44, 90))
            fins.addPath(line(98, 74, 116, 90))
            let body = poly([CGPoint(x: 80, y: 20), CGPoint(x: 98, y: 62), CGPoint(x: 98, y: 84),
                             CGPoint(x: 62, y: 84), CGPoint(x: 62, y: 62)])
            return [Part(path: body, width: 2.2, fill: false),
                    Part(path: fins, width: 2, fill: false),
                    Part(path: circle(80, 46, 6), width: 1.8, fill: false)]
        case "camera":
            let lens = poly([CGPoint(x: 104, y: 54), CGPoint(x: 120, y: 46), CGPoint(x: 120, y: 80),
                             CGPoint(x: 104, y: 72)])
            return [Part(path: Path(roundedRect: CGRect(x: 44, y: 46, width: 60, height: 40), cornerRadius: 4),
                         width: 2.2, fill: false),
                    Part(path: lens, width: 2, fill: false),
                    Part(path: circle(74, 66, 11), width: 1.8, fill: false)]
        case "music":
            let flag = poly([CGPoint(x: 75, y: 28), CGPoint(x: 102, y: 37), CGPoint(x: 102, y: 52),
                             CGPoint(x: 75, y: 43)])
            return [Part(path: circle(66, 82, 9), width: 2.2, fill: false),
                    Part(path: line(75, 82, 75, 28), width: 2.2, fill: false),
                    Part(path: flag, width: 0, fill: true)]
        case "food":
            var tines = Path()
            tines.addPath(line(56, 20, 56, 58))
            tines.addPath(line(66, 20, 66, 58))
            tines.addPath(line(76, 20, 76, 58))
            var bowl = Path()
            bowl.move(to: CGPoint(x: 53, y: 58))
            bowl.addQuadCurve(to: CGPoint(x: 79, y: 58), control: CGPoint(x: 66, y: 70))
            bowl.addLine(to: CGPoint(x: 70, y: 92))
            bowl.addLine(to: CGPoint(x: 62, y: 92))
            bowl.closeSubpath()
            return [Part(path: tines, width: 2, fill: false), Part(path: bowl, width: 2, fill: false)]
        default:
            // leaf
            var leaf = Path()
            leaf.move(to: CGPoint(x: 80, y: 22))
            leaf.addCurve(to: CGPoint(x: 80, y: 98), control1: CGPoint(x: 112, y: 40), control2: CGPoint(x: 112, y: 82))
            leaf.addCurve(to: CGPoint(x: 80, y: 22), control1: CGPoint(x: 48, y: 82), control2: CGPoint(x: 48, y: 40))
            leaf.closeSubpath()
            return [Part(path: leaf, width: 2.2, fill: false),
                    Part(path: line(80, 30, 80, 92), width: 1.6, fill: false)]
        }
    }
}
