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
    /// The web's small uppercase labels: Geist Mono, spaced out. `tracking`
    /// is the web's computed letter-spacing in points where a screen gives
    /// one (myFAM does); without it, the spacing every other screen uses.
    func monoLabel(_ size: CGFloat = 9, color: Color = FAMColor.textMuted, bold: Bool = false,
                   tracking: CGFloat? = nil) -> some View {
        self.font(FAMFont.mono(size, bold: bold))
            .tracking(tracking ?? size * 0.14)
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

    /// myFAM: the double chevron (fast-forward).
    static let myfam: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 6, y: 5)); p.addLine(to: CGPoint(x: 12, y: 12)); p.addLine(to: CGPoint(x: 6, y: 19))
        p.move(to: CGPoint(x: 13, y: 5)); p.addLine(to: CGPoint(x: 19, y: 12)); p.addLine(to: CGPoint(x: 13, y: 19))
        return p
    }()

    /// The filled tone under myFAM's chevrons (`M13 5l6 7-6 7z`).
    static let myfamTone: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 13, y: 5)); p.addLine(to: CGPoint(x: 19, y: 12)); p.addLine(to: CGPoint(x: 13, y: 19))
        p.closeSubpath()
        return p
    }()

    /// A mix: the folder.
    static let folder: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 3, y: 7))
        p.addQuadCurve(to: CGPoint(x: 5, y: 5), control: CGPoint(x: 3, y: 5))
        p.addLine(to: CGPoint(x: 9, y: 5)); p.addLine(to: CGPoint(x: 11, y: 7)); p.addLine(to: CGPoint(x: 19, y: 7))
        p.addQuadCurve(to: CGPoint(x: 21, y: 9), control: CGPoint(x: 21, y: 7))
        p.addLine(to: CGPoint(x: 21, y: 17))
        p.addQuadCurve(to: CGPoint(x: 19, y: 19), control: CGPoint(x: 21, y: 19))
        p.addLine(to: CGPoint(x: 5, y: 19))
        p.addQuadCurve(to: CGPoint(x: 3, y: 17), control: CGPoint(x: 3, y: 19))
        p.closeSubpath()
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

    /// Plus (`M12 5v14M5 12h14`): Create new mix, Add topics, (+) a mix.
    static let plus: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 12, y: 5)); p.addLine(to: CGPoint(x: 12, y: 19))
        p.move(to: CGPoint(x: 5, y: 12)); p.addLine(to: CGPoint(x: 19, y: 12))
        return p
    }()

    /// Added (`M5 12.5l4.5 4.5L19 7`).
    static let check: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 5, y: 12.5)); p.addLine(to: CGPoint(x: 9.5, y: 17)); p.addLine(to: CGPoint(x: 19, y: 7))
        return p
    }()

    /// A mix card's chevron (`M9 6l6 6-6 6`).
    static let chevron: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 9, y: 6)); p.addLine(to: CGPoint(x: 15, y: 12)); p.addLine(to: CGPoint(x: 9, y: 18))
        return p
    }()

    /// Close (`M6 6l12 12M18 6L6 18`).
    static let close: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 6, y: 6)); p.addLine(to: CGPoint(x: 18, y: 18))
        p.move(to: CGPoint(x: 18, y: 6)); p.addLine(to: CGPoint(x: 6, y: 18))
        return p
    }()

    /// The cover badge's camera (`M4 8h3l2-3h6l2 3h3v11H4z` and a lens).
    static let camera: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 4, y: 8)); p.addLine(to: CGPoint(x: 7, y: 8)); p.addLine(to: CGPoint(x: 9, y: 5))
        p.addLine(to: CGPoint(x: 15, y: 5)); p.addLine(to: CGPoint(x: 17, y: 8)); p.addLine(to: CGPoint(x: 20, y: 8))
        p.addLine(to: CGPoint(x: 20, y: 19)); p.addLine(to: CGPoint(x: 4, y: 19)); p.closeSubpath()
        p.addEllipse(in: CGRect(x: 8.5, y: 9.5, width: 7, height: 7))
        return p
    }()

    /// Share a mix: the arrow out of the tray.
    static let share: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 12, y: 3)); p.addLine(to: CGPoint(x: 12, y: 15))
        p.move(to: CGPoint(x: 7, y: 8)); p.addLine(to: CGPoint(x: 12, y: 3)); p.addLine(to: CGPoint(x: 17, y: 8))
        p.move(to: CGPoint(x: 5, y: 13)); p.addLine(to: CGPoint(x: 5, y: 19))
        p.addQuadCurve(to: CGPoint(x: 7, y: 21), control: CGPoint(x: 5, y: 21))
        p.addLine(to: CGPoint(x: 17, y: 21))
        p.addQuadCurve(to: CGPoint(x: 19, y: 19), control: CGPoint(x: 19, y: 21))
        p.addLine(to: CGPoint(x: 19, y: 13))
        return p
    }()

    /// Invite collaborators: a person and a plus.
    static let invite: Path = {
        var p = Path()
        p.addEllipse(in: CGRect(x: 5.5, y: 4.5, width: 7, height: 7))
        p.move(to: CGPoint(x: 3, y: 20))
        p.addCurve(to: CGPoint(x: 9, y: 14), control1: CGPoint(x: 3, y: 16.7), control2: CGPoint(x: 5.7, y: 14))
        p.addCurve(to: CGPoint(x: 15, y: 20), control1: CGPoint(x: 12.3, y: 14), control2: CGPoint(x: 15, y: 16.7))
        p.move(to: CGPoint(x: 19, y: 8)); p.addLine(to: CGPoint(x: 19, y: 14))
        p.move(to: CGPoint(x: 16, y: 11)); p.addLine(to: CGPoint(x: 22, y: 11))
        return p
    }()

    /// The listen time's bell.
    static let bell: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 6, y: 8))
        p.addArc(center: CGPoint(x: 12, y: 8), radius: 6,
                 startAngle: .degrees(180), endAngle: .degrees(360), clockwise: false)
        p.addCurve(to: CGPoint(x: 21, y: 17), control1: CGPoint(x: 18, y: 15), control2: CGPoint(x: 21, y: 17))
        p.addLine(to: CGPoint(x: 3, y: 17))
        p.addCurve(to: CGPoint(x: 6, y: 8), control1: CGPoint(x: 3, y: 17), control2: CGPoint(x: 6, y: 15))
        p.move(to: CGPoint(x: 10.3, y: 21))
        p.addQuadCurve(to: CGPoint(x: 13.7, y: 21), control: CGPoint(x: 12, y: 23))
        return p
    }()

    /// Shuffle recommendations.
    static let shuffle: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 16, y: 3)); p.addLine(to: CGPoint(x: 21, y: 3)); p.addLine(to: CGPoint(x: 21, y: 8))
        p.move(to: CGPoint(x: 4, y: 20)); p.addLine(to: CGPoint(x: 21, y: 3))
        p.move(to: CGPoint(x: 21, y: 16)); p.addLine(to: CGPoint(x: 21, y: 21)); p.addLine(to: CGPoint(x: 16, y: 21))
        p.move(to: CGPoint(x: 15, y: 15)); p.addLine(to: CGPoint(x: 21, y: 21))
        p.move(to: CGPoint(x: 4, y: 4)); p.addLine(to: CGPoint(x: 9, y: 9))
        return p
    }()

    /// The ⋯ menu's "Add to myFAM Playlist": a circled plus.
    static let circlePlus: Path = {
        var p = Path()
        p.addEllipse(in: CGRect(x: 3, y: 3, width: 18, height: 18))
        p.move(to: CGPoint(x: 12, y: 8)); p.addLine(to: CGPoint(x: 12, y: 16))
        p.move(to: CGPoint(x: 8, y: 12)); p.addLine(to: CGPoint(x: 16, y: 12))
        return p
    }()

    /// A clock, the time field's own mark.
    static let clock: Path = {
        var p = Path()
        p.addEllipse(in: CGRect(x: 3.5, y: 3.5, width: 17, height: 17))
        p.move(to: CGPoint(x: 12, y: 7.5)); p.addLine(to: CGPoint(x: 12, y: 12)); p.addLine(to: CGPoint(x: 15, y: 14))
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
