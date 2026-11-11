import SwiftUI

/// The web app's look, as one place every screen reads from. The values are
/// `static/index.html`'s two themes: dark (the `:root` block, the app's
/// default since §546) and light (`html[data-theme="light"]`, the listener's
/// choice in Settings, "Dark mode" Off, kept on this device under `fam.theme`). Every
/// token resolves by the trait it is drawn in (`Color(light:dark:)`), so the
/// root's colour scheme (`FAMApp`, `FAMTheme`) is the only switch. Its type
/// rule (`typefaces`): Bricolage Grotesque for headings, Geist for body,
/// Geist Mono for labels. A CSS pixel is a point here - the web is drawn for a
/// 390-point phone - so its sizes carry over as they are. Change the look
/// there and here together.
enum FAMColor {
    static let page = Color(light: 0xE9E3D6, dark: 0x1E1B27)
    static let background = Color(light: 0xFFFFFF, dark: 0x2A2733)
    static let surface = Color(light: 0xF7F3EA, dark: 0x38334A)
    static let surface2 = Color(light: 0xEEE8DC, dark: 0x423C56)
    static let paper = Color(light: 0xE6DECD, dark: 0xF4EFE4)
    /// The thumbnail's ground while its picture loads (`.seed-thumb`): a
    /// literal on the web, the same in both themes.
    static let thumb = Color(hex: 0xEDE6D6)
    static let copper = Color(light: 0xA87520, dark: 0xE0B563)
    static let coral = Color(light: 0xC4502F, dark: 0xE2694F)
    static let sage = Color(light: 0x5C8669, dark: 0x8FAE9A)
    /// GO DEEPER's yellow (`--deeper`), the same in both themes.
    static let deeper = Color(hex: 0xFFD23F)
    static let text = Color(light: 0x1E1B27, dark: 0xF4EFE4)
    static let textMuted = Color(light: 0x5E5770, dark: 0xABA3C4)
    static let textFaint = Color(light: 0x7D768C, dark: 0x8A83A0)
    /// Text on copper or yellow (`--ink`), the same in both themes.
    static let ink = Color(hex: 0x18151F)
    static let border = Color(light: 0x1E1B27, lightAlpha: 0.12, dark: 0xF4EFE4, darkAlpha: 0.10)
    static let borderStrong = Color(light: 0x1E1B27, lightAlpha: 0.22, dark: 0xF4EFE4, darkAlpha: 0.20)
    /// YourFAM's and a friend's hero wash at its top (`.screen-top`,
    /// `.yf-bar`: `color-mix(in srgb, var(--copper) 24%, var(--bg))`).
    static let wash = Color(light: 0xEADEC9, dark: 0x56493E)
}

/// The theme this device chose (Settings, "Dark mode"): the web's
/// `fam.theme`, its own key rather than the preferences, so signing out
/// never changes the colours. Dark unless it says "light": dark is what a
/// new listener sees on the iPhone, light the choice in Settings (§546, the
/// owner 11.11, reversing §250's light default for the app).
enum FAMTheme {
    static let key = "fam.theme"
    static let light = "light"
    static let dark = "dark"
    /// What a phone that never chose is drawn in.
    static let standard = dark

    static func isDark(_ stored: String) -> Bool {
        stored != light
    }

    static func scheme(_ stored: String) -> ColorScheme {
        isDark(stored) ? .dark : .light
    }
}

/// Draws its content in the theme this device chose. The root carries it;
/// a sheet or cover that can open while the window is dark for private
/// search's charcoal (`AppModel.darkChrome`) carries it too, since a
/// presentation takes its scheme from the window rather than the view that
/// presents it.
struct FAMThemed: ViewModifier {
    @AppStorage(FAMTheme.key) private var theme = FAMTheme.standard

    func body(content: Content) -> some View {
        content.environment(\.colorScheme, FAMTheme.scheme(theme))
    }
}

extension View {
    func famThemed() -> some View { modifier(FAMThemed()) }
}

extension Color {
    init(hex: UInt32) {
        self.init(red: Double((hex >> 16) & 0xFF) / 255,
                  green: Double((hex >> 8) & 0xFF) / 255,
                  blue: Double(hex & 0xFF) / 255)
    }

    /// One colour per theme, chosen by the trait it is drawn in.
    init(light: UInt32, dark: UInt32) {
        self.init(light: light, lightAlpha: 1, dark: dark, darkAlpha: 1)
    }

    /// One colour per theme with its own opacity (the web's
    /// `rgba(var(--fg-rgb), a)` borders).
    init(light: UInt32, lightAlpha: CGFloat, dark: UInt32, darkAlpha: CGFloat) {
        self.init(uiColor: UIColor { trait in
            trait.userInterfaceStyle == .dark
                ? UIColor(famHex: dark, alpha: darkAlpha)
                : UIColor(famHex: light, alpha: lightAlpha)
        })
    }
}

extension UIColor {
    convenience init(famHex hex: UInt32, alpha: CGFloat = 1) {
        self.init(red: CGFloat((hex >> 16) & 0xFF) / 255,
                  green: CGFloat((hex >> 8) & 0xFF) / 255,
                  blue: CGFloat(hex & 0xFF) / 255,
                  alpha: alpha)
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
    /// The drawn letters grow with the word beside them, which follows the
    /// phone's text size (`large-text`); one line always (`fixedSize`).
    @ScaledMetric(relativeTo: .title) private var grow: CGFloat = 1

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 0) {
            if !lead.isEmpty {
                Text(lead)
                    .font(FAMFont.heading(size))
                    .foregroundStyle(FAMColor.text)
            }
            FAMLetters(capHeight: size * 0.68 * (lead.isEmpty ? 1 : grow))
                .padding(.leading, lead.isEmpty ? 0 : size * 0.15 * grow)
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

/// A 24-point icon filled rather than stroked (the web's `fill="currentColor"`).
struct WebFill: View {
    let path: Path
    var size: CGFloat = 20

    var body: some View {
        Glyph(path: path, box: CGRect(x: 0, y: 0, width: 24, height: 24))
            .fill()
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

    /// The tone (filled at .16, .45 when on) under DailyFAM's house.
    static let dailyTone: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 4, y: 10)); p.addLine(to: CGPoint(x: 12, y: 3))
        p.addLine(to: CGPoint(x: 20, y: 10)); p.addLine(to: CGPoint(x: 20, y: 21))
        p.addLine(to: CGPoint(x: 4, y: 21)); p.closeSubpath()
        return p
    }()

    /// The tone inside search's lens (`circle cx=11 cy=11 r=7`).
    static let searchTone: Path = {
        var p = Path()
        p.addEllipse(in: CGRect(x: 4, y: 4, width: 14, height: 14))
        return p
    }()

    /// The tone inside YourFAM's front person: the head and the body.
    static let youTone: Path = {
        var p = Path()
        p.addEllipse(in: CGRect(x: 5.5, y: 4.5, width: 7, height: 7))
        p.move(to: CGPoint(x: 3, y: 20))
        p.addCurve(to: CGPoint(x: 9, y: 14), control1: CGPoint(x: 3, y: 16.7), control2: CGPoint(x: 5.7, y: 14))
        p.addCurve(to: CGPoint(x: 15, y: 20), control1: CGPoint(x: 12.3, y: 14), control2: CGPoint(x: 15, y: 16.7))
        p.closeSubpath()
        return p
    }()

    /// The library on searchFAM (index.html:3927): a shelf, two books
    /// standing and one leaning.
    static let bookshelf: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 2.5, y: 21)); p.addLine(to: CGPoint(x: 21.5, y: 21))
        p.addRoundedRect(in: CGRect(x: 4, y: 5, width: 3.6, height: 16), cornerSize: CGSize(width: 0.7, height: 0.7))
        p.addRoundedRect(in: CGRect(x: 8.6, y: 7.5, width: 3.6, height: 13.5), cornerSize: CGSize(width: 0.7, height: 0.7))
        p.move(to: CGPoint(x: 8.6, y: 10.5)); p.addLine(to: CGPoint(x: 12.2, y: 10.5))
        p.move(to: CGPoint(x: 14.3, y: 8.6)); p.addLine(to: CGPoint(x: 17.7, y: 7.7))
        p.addLine(to: CGPoint(x: 21.2, y: 20.3)); p.addLine(to: CGPoint(x: 17.8, y: 21.2))
        p.closeSubpath()
        return p
    }()

    /// The card's save (`M6 4h12a1 1 0 011 1v15l-7-4-7 4V5a1 1 0 011-1z`),
    /// an outline saved or not.
    static let bookmark: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 6, y: 4)); p.addLine(to: CGPoint(x: 18, y: 4))
        p.addQuadCurve(to: CGPoint(x: 19, y: 5), control: CGPoint(x: 19, y: 4))
        p.addLine(to: CGPoint(x: 19, y: 20)); p.addLine(to: CGPoint(x: 12, y: 16))
        p.addLine(to: CGPoint(x: 5, y: 20)); p.addLine(to: CGPoint(x: 5, y: 5))
        p.addQuadCurve(to: CGPoint(x: 6, y: 4), control: CGPoint(x: 5, y: 4))
        p.closeSubpath()
        return p
    }()

    /// Three dots, filled (the card's ⋯ at r 2.2, the story's at r 1.9).
    static func dots(_ r: CGFloat) -> Path {
        var p = Path()
        for x in [CGFloat(5), 12, 19] {
            p.addEllipse(in: CGRect(x: x - r, y: 12 - r, width: r * 2, height: r * 2))
        }
        return p
    }

    /// The menu's share (`MENU_ICONS.share`).
    static let menuShare: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 4, y: 12)); p.addLine(to: CGPoint(x: 4, y: 19))
        p.addQuadCurve(to: CGPoint(x: 6, y: 21), control: CGPoint(x: 4, y: 21))
        p.addLine(to: CGPoint(x: 18, y: 21))
        p.addQuadCurve(to: CGPoint(x: 20, y: 19), control: CGPoint(x: 20, y: 21))
        p.addLine(to: CGPoint(x: 20, y: 12))
        p.move(to: CGPoint(x: 16, y: 6)); p.addLine(to: CGPoint(x: 12, y: 2)); p.addLine(to: CGPoint(x: 8, y: 6))
        p.move(to: CGPoint(x: 12, y: 2)); p.addLine(to: CGPoint(x: 12, y: 15))
        return p
    }()

    /// Add to Queue (`MENU_ICONS.addq`).
    static let addQueue: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 4, y: 6)); p.addLine(to: CGPoint(x: 14, y: 6))
        p.move(to: CGPoint(x: 4, y: 11)); p.addLine(to: CGPoint(x: 14, y: 11))
        p.move(to: CGPoint(x: 4, y: 16)); p.addLine(to: CGPoint(x: 10, y: 16))
        p.move(to: CGPoint(x: 18, y: 13)); p.addLine(to: CGPoint(x: 18, y: 19))
        p.move(to: CGPoint(x: 15, y: 16)); p.addLine(to: CGPoint(x: 21, y: 16))
        return p
    }()

    /// Go to Queue (`MENU_ICONS.queue`).
    static let queue: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 4, y: 6)); p.addLine(to: CGPoint(x: 20, y: 6))
        p.move(to: CGPoint(x: 4, y: 11)); p.addLine(to: CGPoint(x: 20, y: 11))
        p.move(to: CGPoint(x: 4, y: 16)); p.addLine(to: CGPoint(x: 14, y: 16))
        return p
    }()

    /// Not interested: the frown (`MENU_ICONS.notint`).
    static let frown: Path = {
        var p = Path()
        p.addEllipse(in: CGRect(x: 3, y: 3, width: 18, height: 18))
        p.move(to: CGPoint(x: 8.5, y: 15.5))
        p.addCurve(to: CGPoint(x: 12, y: 13.7), control1: CGPoint(x: 9.5, y: 14.3), control2: CGPoint(x: 10.7, y: 13.7))
        p.addCurve(to: CGPoint(x: 15.5, y: 15.5), control1: CGPoint(x: 13.3, y: 13.7), control2: CGPoint(x: 14.5, y: 14.3))
        p.move(to: CGPoint(x: 9, y: 9.5)); p.addLine(to: CGPoint(x: 9.01, y: 9.5))
        p.move(to: CGPoint(x: 15, y: 9.5)); p.addLine(to: CGPoint(x: 15.01, y: 9.5))
        return p
    }()

    /// The story's send, inside the reply box (index.html:4748).
    static let send: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 21.5, y: 3)); p.addLine(to: CGPoint(x: 10.2, y: 14.3))
        p.move(to: CGPoint(x: 21.5, y: 3)); p.addLine(to: CGPoint(x: 14.6, y: 21))
        p.addLine(to: CGPoint(x: 10.2, y: 14.3)); p.addLine(to: CGPoint(x: 3.5, y: 10))
        p.closeSubpath()
        return p
    }()

    /// The story's heart (index.html:4750); filled when liked.
    static let heart: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 12, y: 20.5))
        p.addCurve(to: CGPoint(x: 2.8, y: 11.2), control1: CGPoint(x: 12, y: 20.5), control2: CGPoint(x: 4.5, y: 15.9))
        p.addCurve(to: CGPoint(x: 7.3, y: 4.5), control1: CGPoint(x: 1.6, y: 7.8), control2: CGPoint(x: 3.9, y: 4.5))
        p.addCurve(to: CGPoint(x: 12, y: 7.3), control1: CGPoint(x: 9.3, y: 4.5), control2: CGPoint(x: 10.9, y: 5.6))
        p.addCurve(to: CGPoint(x: 16.7, y: 4.5), control1: CGPoint(x: 13.1, y: 5.6), control2: CGPoint(x: 14.7, y: 4.5))
        p.addCurve(to: CGPoint(x: 21.2, y: 11.2), control1: CGPoint(x: 20.1, y: 4.5), control2: CGPoint(x: 22.4, y: 7.8))
        p.addCurve(to: CGPoint(x: 12, y: 20.5), control1: CGPoint(x: 19.5, y: 15.9), control2: CGPoint(x: 12, y: 20.5))
        p.closeSubpath()
        return p
    }()

    /// VIBE! to your story: the two turning arrows (index.html:4751).
    static let reshare: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 4, y: 10.4))
        p.addArc(center: CGPoint(x: 8.4, y: 10.4), radius: 4.4, startAngle: .degrees(180),
                 endAngle: .degrees(270), clockwise: false)
        p.addLine(to: CGPoint(x: 18, y: 6))
        p.move(to: CGPoint(x: 15, y: 3)); p.addLine(to: CGPoint(x: 18, y: 6)); p.addLine(to: CGPoint(x: 15, y: 9))
        p.move(to: CGPoint(x: 20, y: 13.6))
        p.addArc(center: CGPoint(x: 15.6, y: 13.6), radius: 4.4, startAngle: .degrees(0),
                 endAngle: .degrees(90), clockwise: false)
        p.addLine(to: CGPoint(x: 6, y: 18))
        p.move(to: CGPoint(x: 9, y: 21)); p.addLine(to: CGPoint(x: 6, y: 18)); p.addLine(to: CGPoint(x: 9, y: 15))
        return p
    }()

    /// The story's play (`M8.2 4.9v14.2L18.6 12z`), filled.
    static let play: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 8.2, y: 4.9)); p.addLine(to: CGPoint(x: 8.2, y: 19.1))
        p.addLine(to: CGPoint(x: 18.6, y: 12)); p.closeSubpath()
        return p
    }()

    /// The catalogue row's play (`M7 4.5v15l12.5-7.5z`), filled.
    static let rowPlay: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 7, y: 4.5)); p.addLine(to: CGPoint(x: 7, y: 19.5))
        p.addLine(to: CGPoint(x: 19.5, y: 12)); p.closeSubpath()
        return p
    }()

    /// The refresh arrow (index.html:3829).
    static let refresh: Path = {
        var p = Path()
        p.addArc(center: CGPoint(x: 12, y: 11), radius: 8, startAngle: .degrees(0), endAngle: .degrees(45),
                 clockwise: true)
        p.move(to: CGPoint(x: 20, y: 4)); p.addLine(to: CGPoint(x: 20, y: 11)); p.addLine(to: CGPoint(x: 13, y: 11))
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
