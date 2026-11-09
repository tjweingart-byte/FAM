import SwiftUI

/// The web app's look, as one place every screen reads from. The values are
/// `static/index.html`'s light theme (`:root` / `html[data-theme="light"]`,
/// the default since §250) and its type rule (`typefaces`): Bricolage
/// Grotesque for headings, Geist for body, Geist Mono for labels. A CSS pixel
/// is a point here - the web is drawn for a 390-point phone - so its sizes
/// carry over as they are. Change the look there and here together.
enum FAMColor {
    static let page = Color(hex: 0xE9E3D6)
    static let background = Color(hex: 0xFFFFFF)
    static let surface = Color(hex: 0xF7F3EA)
    static let surface2 = Color(hex: 0xEEE8DC)
    static let paper = Color(hex: 0xE6DECD)
    /// The thumbnail's ground while its picture loads (`.seed-thumb`).
    static let thumb = Color(hex: 0xEDE6D6)
    static let copper = Color(hex: 0xA87520)
    static let coral = Color(hex: 0xC4502F)
    static let sage = Color(hex: 0x5C8669)
    /// GO DEEPER's yellow (`--deeper`).
    static let deeper = Color(hex: 0xFFD23F)
    static let text = Color(hex: 0x1E1B27)
    static let textMuted = Color(hex: 0x5E5770)
    static let textFaint = Color(hex: 0x7D768C)
    /// Text on copper or yellow (`--ink`).
    static let ink = Color(hex: 0x18151F)
    static let border = Color(red: 30 / 255, green: 27 / 255, blue: 39 / 255).opacity(0.12)
    static let borderStrong = Color(red: 30 / 255, green: 27 / 255, blue: 39 / 255).opacity(0.22)
}

extension Color {
    init(hex: UInt32) {
        self.init(red: Double((hex >> 16) & 0xFF) / 255,
                  green: Double((hex >> 8) & 0xFF) / 255,
                  blue: Double(hex & 0xFF) / 255)
    }
}

/// The three typefaces. Every size scales with the phone's text size
/// (`relativeTo`), and screens give text room to grow (`large-text`).
enum FAMFont {
    static func heading(_ size: CGFloat, _ weight: Weight = .regular) -> Font {
        .custom("BricolageGrotesque-" + weight.name, size: size, relativeTo: .title)
    }

    static func body(_ size: CGFloat, _ weight: Weight = .regular) -> Font {
        .custom("Geist-" + weight.name, size: size, relativeTo: .body)
    }

    static func mono(_ size: CGFloat, bold: Bool = false) -> Font {
        .custom(bold ? "GeistMono-Bold" : "GeistMono-Regular", size: size, relativeTo: .caption)
    }

    enum Weight {
        case regular, medium, semibold, bold
        var name: String {
            switch self {
            case .regular: return "Regular"
            case .medium: return "Medium"
            case .semibold: return "SemiBold"
            case .bold: return "Bold"
            }
        }
    }
}

extension View {
    /// The web's small uppercase labels: Geist Mono, spaced out.
    func monoLabel(_ size: CGFloat = 9, color: Color = FAMColor.textMuted, bold: Bool = false) -> some View {
        self.font(FAMFont.mono(size, bold: bold))
            .tracking(size * 0.14)
            .textCase(.uppercase)
            .foregroundStyle(color)
    }
}

// MARK: - The wordmark

/// "daily" + F A M, as the web draws it (`.wordmark`): the word set in
/// Bricolage, the three letters drawn - F and M as strokes, the A as the
/// double chevron that is FAM's mark - one cap height tall, in copper.
struct Wordmark: View {
    var lead = ""
    var size: CGFloat = 27

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 0) {
            if !lead.isEmpty {
                Text(lead)
                    .font(FAMFont.heading(size))
                    .foregroundStyle(FAMColor.text)
            }
            FAMLetters(capHeight: size * 0.68)
                .padding(.leading, lead.isEmpty ? 0 : size * 0.15)
                .alignmentGuide(.firstTextBaseline) { d in d[.bottom] }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(lead + "FAM")
        .fixedSize()
    }
}

/// F, the chevron A and M, drawn from the web's own paths.
struct FAMLetters: View {
    var capHeight: CGFloat = 18
    var color: Color = FAMColor.copper

    var body: some View {
        let line = capHeight * 2.9 / 16.9
        HStack(spacing: 0) {
            Glyph(path: Self.f, box: CGRect(x: 4.55, y: 4.55, width: 12.9, height: 16.9))
                .stroke(color, style: StrokeStyle(lineWidth: line, lineCap: .round, lineJoin: .round))
                .frame(width: capHeight * 12.9 / 16.9, height: capHeight)
            Glyph(path: Self.a, box: CGRect(x: 3.55, y: 4.55, width: 16.9, height: 16.9))
                .stroke(color, style: StrokeStyle(lineWidth: line, lineCap: .round, lineJoin: .round))
                .frame(width: capHeight, height: capHeight)
                .padding(.leading, capHeight * 0.13)
                .padding(.trailing, capHeight * 0.22)
            Glyph(path: Self.m, box: CGRect(x: 4.55, y: 4.55, width: 14.9, height: 16.9))
                .stroke(color, style: StrokeStyle(lineWidth: line, lineCap: .round, lineJoin: .round))
                .frame(width: capHeight * 14.9 / 16.9, height: capHeight)
        }
        .accessibilityHidden(true)
    }

    /// `M6 20V9.2A3.2 3.2 0 0 1 9.2 6H16` and `M6 13h8`.
    static let f: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 6, y: 20))
        p.addLine(to: CGPoint(x: 6, y: 9.2))
        p.addArc(center: CGPoint(x: 9.2, y: 9.2), radius: 3.2,
                 startAngle: .degrees(180), endAngle: .degrees(270), clockwise: false)
        p.addLine(to: CGPoint(x: 16, y: 6))
        p.move(to: CGPoint(x: 6, y: 13))
        p.addLine(to: CGPoint(x: 14, y: 13))
        return p
    }()

    /// `M5 13l7-7 7 7` and `M5 20l7-7 7 7`: the mark.
    static let a: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 5, y: 13))
        p.addLine(to: CGPoint(x: 12, y: 6))
        p.addLine(to: CGPoint(x: 19, y: 13))
        p.move(to: CGPoint(x: 5, y: 20))
        p.addLine(to: CGPoint(x: 12, y: 13))
        p.addLine(to: CGPoint(x: 19, y: 20))
        return p
    }()

    /// `M6 20V6l6 9 6-9v14`.
    static let m: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 6, y: 20))
        p.addLine(to: CGPoint(x: 6, y: 6))
        p.addLine(to: CGPoint(x: 12, y: 15))
        p.addLine(to: CGPoint(x: 18, y: 6))
        p.addLine(to: CGPoint(x: 18, y: 20))
        return p
    }()
}

/// A path drawn in an SVG viewBox, scaled to whatever frame it is given.
struct Glyph: Shape {
    let shape: Path
    let box: CGRect

    init(path: Path, box: CGRect) {
        self.shape = path
        self.box = box
    }

    func path(in rect: CGRect) -> Path {
        let sx = rect.width / box.width
        let sy = rect.height / box.height
        let move = CGAffineTransform(translationX: -box.minX, y: -box.minY)
            .concatenating(CGAffineTransform(scaleX: sx, y: sy))
            .concatenating(CGAffineTransform(translationX: rect.minX, y: rect.minY))
        return shape.applying(move)
    }
}

/// A 24-point icon drawn from the web's SVG paths, in the current colour.
struct WebIcon: View {
    let path: Path
    var size: CGFloat = 20
    var line: CGFloat = 2

    var body: some View {
        Glyph(path: path, box: CGRect(x: 0, y: 0, width: 24, height: 24))
            .stroke(style: StrokeStyle(lineWidth: line * size / 24, lineCap: .round, lineJoin: .round))
            .frame(width: size, height: size)
            .accessibilityHidden(true)
    }
}

/// The web's icons (the tab bar's and the player's), path for path.
enum Icons {
    /// DailyFAM: the house with the mark in it.
    static let daily: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 4, y: 10)); p.addLine(to: CGPoint(x: 12, y: 3))
        p.addLine(to: CGPoint(x: 20, y: 10)); p.addLine(to: CGPoint(x: 20, y: 21))
        p.addLine(to: CGPoint(x: 4, y: 21)); p.closeSubpath()
        p.move(to: CGPoint(x: 8.5, y: 12.5)); p.addLine(to: CGPoint(x: 12, y: 9)); p.addLine(to: CGPoint(x: 15.5, y: 12.5))
        p.move(to: CGPoint(x: 8.5, y: 17)); p.addLine(to: CGPoint(x: 12, y: 13.5)); p.addLine(to: CGPoint(x: 15.5, y: 17))
        return p
    }()

    static let search: Path = {
        var p = Path()
        p.addEllipse(in: CGRect(x: 4, y: 4, width: 14, height: 14))
        p.move(to: CGPoint(x: 21, y: 21)); p.addLine(to: CGPoint(x: 16.7, y: 16.7))
        return p
    }()

    /// YourFAM: two people.
    static let you: Path = {
        var p = Path()
        p.addEllipse(in: CGRect(x: 5.5, y: 4.5, width: 7, height: 7))
        p.move(to: CGPoint(x: 3, y: 20))
        p.addCurve(to: CGPoint(x: 15, y: 20), control1: CGPoint(x: 3, y: 12), control2: CGPoint(x: 15, y: 12))
        p.addEllipse(in: CGRect(x: 14.5, y: 6.5, width: 5, height: 5))
        p.move(to: CGPoint(x: 17, y: 14))
        p.addCurve(to: CGPoint(x: 21, y: 19), control1: CGPoint(x: 19.5, y: 14), control2: CGPoint(x: 21, y: 16))
        return p
    }()

    /// The trending searches' arrow.
    static let trend: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 3, y: 17)); p.addLine(to: CGPoint(x: 9, y: 11))
        p.addLine(to: CGPoint(x: 13, y: 15)); p.addLine(to: CGPoint(x: 21, y: 7))
        p.move(to: CGPoint(x: 15, y: 7)); p.addLine(to: CGPoint(x: 21, y: 7)); p.addLine(to: CGPoint(x: 21, y: 13))
        return p
    }()

    /// Back: the double chevron («).
    static let back: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 17, y: 6)); p.addLine(to: CGPoint(x: 11, y: 12)); p.addLine(to: CGPoint(x: 17, y: 18))
        p.move(to: CGPoint(x: 10, y: 6)); p.addLine(to: CGPoint(x: 4, y: 12)); p.addLine(to: CGPoint(x: 10, y: 18))
        return p
    }()

    /// The timer on the Length bubble.
    static let timer: Path = {
        var p = Path()
        p.addEllipse(in: CGRect(x: 5, y: 6, width: 14, height: 14))
        p.move(to: CGPoint(x: 12, y: 10)); p.addLine(to: CGPoint(x: 12, y: 13)); p.addLine(to: CGPoint(x: 14, y: 14.5))
        p.move(to: CGPoint(x: 10, y: 3)); p.addLine(to: CGPoint(x: 14, y: 3))
        return p
    }()
}

// MARK: - Shared pieces

/// The copper pill (`Create account`, `Sign up`).
struct CopperButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(FAMFont.body(16, .bold))
            .foregroundStyle(FAMColor.ink)
            .frame(maxWidth: .infinity, minHeight: 46)
            .background(FAMColor.copper, in: Capsule())
            .opacity(configuration.isPressed ? 0.85 : 1)
    }
}

/// The outlined pill (`Log in`).
struct OutlineButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(FAMFont.body(15, .medium))
            .foregroundStyle(FAMColor.text)
            .padding(.horizontal, 22)
            .frame(minHeight: 40)
            .overlay(Capsule().stroke(FAMColor.borderStrong))
            .opacity(configuration.isPressed ? 0.7 : 1)
    }
}
