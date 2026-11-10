import FAMSafety
import SwiftUI

// MARK: - The two palettes of searchFAM

/// searchFAM's colours: the theme's own (`light` is the app's tokens, which
/// follow Settings' "Dark mode"), or the private charcoal
/// (`#screen-home.private-search`, index.html:219-228), which holds in either
/// theme and covers only the search screen and its tab bar - never the
/// player, the sheets or the voice search screen (shots 11, 37).
struct SearchPalette: Equatable {
    let background: Color
    let surface: Color
    let surface2: Color
    let copper: Color
    let text: Color
    let textMuted: Color
    let textFaint: Color
    let border: Color
    let borderStrong: Color

    static let light = SearchPalette(
        background: FAMColor.background, surface: FAMColor.surface, surface2: FAMColor.surface2,
        copper: FAMColor.copper, text: FAMColor.text, textMuted: FAMColor.textMuted,
        textFaint: FAMColor.textFaint, border: FAMColor.border, borderStrong: FAMColor.borderStrong)

    /// `--bg` #161519, `--surface` #232129, `--surface-2` #2C2A33, the gold
    /// #E0B563, `--text` #F1EEF4, muted #A7A2B3, faint #7F7A8B, the borders
    /// at .12 and .22 of the text colour.
    static let charcoal = SearchPalette(
        background: Color(hex: 0x161519), surface: Color(hex: 0x232129), surface2: Color(hex: 0x2C2A33),
        copper: Color(hex: 0xE0B563), text: Color(hex: 0xF1EEF4), textMuted: Color(hex: 0xA7A2B3),
        textFaint: Color(hex: 0x7F7A8B),
        border: Color(red: 241 / 255, green: 238 / 255, blue: 244 / 255).opacity(0.12),
        borderStrong: Color(red: 241 / 255, green: 238 / 255, blue: 244 / 255).opacity(0.22))

    static func of(_ privateOn: Bool) -> SearchPalette { privateOn ? charcoal : light }
}

// MARK: - The private blind (`Motion.dimDown`)

/// What is left of the page as it was while the new one is revealed: the
/// web's `clip-path: inset(h 0 0 0)` (the charcoal falling, `fromTop`) or
/// `inset(0 0 h 0)` (lifting). It reaches up past the view so the status
/// bar's strip, drawn by the page's background, goes with it.
struct BlindClip: Shape {
    var hidden: CGFloat
    let fromTop: Bool

    var animatableData: CGFloat {
        get { hidden }
        set { hidden = newValue }
    }

    func path(in rect: CGRect) -> Path {
        let full = CGRect(x: rect.minX - 50, y: rect.minY - 100,
                          width: rect.width + 100, height: rect.height + 100)
        let gone = full.height * min(max(hidden, 0), 1)
        let kept = fromTop
            ? CGRect(x: full.minX, y: full.minY + gone, width: full.width, height: full.height - gone)
            : CGRect(x: full.minX, y: full.minY, width: full.width, height: full.height - gone)
        return Path(kept)
    }
}

// MARK: - The bar's and the bubbles' icons, path for path (spec 1.7)

enum SearchIcons {
    /// Mic: `<rect x="9" y="3" width="6" height="11" rx="3"/>`,
    /// `M5.5 11a6.5 6.5 0 0013 0M12 17.5V21M9 21h6`.
    static let mic: Path = {
        var p = Path()
        p.addRoundedRect(in: CGRect(x: 9, y: 3, width: 6, height: 11), cornerSize: CGSize(width: 3, height: 3))
        p.move(to: CGPoint(x: 5.5, y: 11))
        p.addArc(center: CGPoint(x: 12, y: 11), radius: 6.5, startAngle: .degrees(180),
                 endAngle: .degrees(0), clockwise: true)
        p.move(to: CGPoint(x: 12, y: 17.5)); p.addLine(to: CGPoint(x: 12, y: 21))
        p.move(to: CGPoint(x: 9, y: 21)); p.addLine(to: CGPoint(x: 15, y: 21))
        return p
    }()

    /// The paperclip: `M21 11.5l-8.8 8.8a5 5 0 01-7.1-7.1l9-9a3.4 3.4 0
    /// 014.8 4.8l-9 9a1.8 1.8 0 01-2.5-2.5l8.3-8.3` (each arc a half turn).
    static let attach: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 21, y: 11.5))
        p.addLine(to: CGPoint(x: 12.2, y: 20.3))
        p.addArc(center: CGPoint(x: 8.65, y: 16.75), radius: 5.02, startAngle: .degrees(45),
                 endAngle: .degrees(225), clockwise: false)
        p.addLine(to: CGPoint(x: 14.1, y: 4.2))
        p.addArc(center: CGPoint(x: 16.5, y: 6.6), radius: 3.39, startAngle: .degrees(225),
                 endAngle: .degrees(405), clockwise: false)
        p.addLine(to: CGPoint(x: 9.9, y: 18))
        p.addArc(center: CGPoint(x: 8.65, y: 16.75), radius: 1.77, startAngle: .degrees(45),
                 endAngle: .degrees(225), clockwise: false)
        p.addLine(to: CGPoint(x: 15.7, y: 7.2))
        return p
    }()

    /// Go, a right arrow: `M5 12h14M13 6l6 6-6 6` (iOS had `arrow.up`).
    static let go: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 5, y: 12)); p.addLine(to: CGPoint(x: 19, y: 12))
        p.move(to: CGPoint(x: 13, y: 6)); p.addLine(to: CGPoint(x: 19, y: 12)); p.addLine(to: CGPoint(x: 13, y: 18))
        return p
    }()

    /// Length: `<circle cx="12" cy="13" r="8"/>`, `M12 9v4l2.5 2.5M10 2h4`.
    static let length: Path = {
        var p = Path()
        p.addEllipse(in: CGRect(x: 4, y: 5, width: 16, height: 16))
        p.move(to: CGPoint(x: 12, y: 9)); p.addLine(to: CGPoint(x: 12, y: 13)); p.addLine(to: CGPoint(x: 14.5, y: 15.5))
        p.move(to: CGPoint(x: 10, y: 2)); p.addLine(to: CGPoint(x: 14, y: 2))
        return p
    }()

    /// Voice: `M4 10v4M8 7v10M12 4v16M16 8v8M20 11v2`.
    static let voice: Path = {
        var p = Path()
        let bars: [(CGFloat, CGFloat, CGFloat)] = [(4, 10, 14), (8, 7, 17), (12, 4, 20), (16, 8, 16), (20, 11, 13)]
        for b in bars {
            p.move(to: CGPoint(x: b.0, y: b.1)); p.addLine(to: CGPoint(x: b.0, y: b.2))
        }
        return p
    }()

    /// Private: the hat and the glasses.
    static let incognito: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 3, y: 11)); p.addLine(to: CGPoint(x: 21, y: 11))
        p.move(to: CGPoint(x: 5.5, y: 11)); p.addLine(to: CGPoint(x: 7.2, y: 5.4))
        p.addQuadCurve(to: CGPoint(x: 8.6, y: 4.3), control: CGPoint(x: 7.5, y: 4.3))
        p.addLine(to: CGPoint(x: 15.4, y: 4.3))
        p.addQuadCurve(to: CGPoint(x: 16.8, y: 5.4), control: CGPoint(x: 16.5, y: 4.3))
        p.addLine(to: CGPoint(x: 18.5, y: 11))
        p.addEllipse(in: CGRect(x: 4.7, y: 13.7, width: 5.6, height: 5.6))
        p.addEllipse(in: CGRect(x: 13.7, y: 13.7, width: 5.6, height: 5.6))
        p.move(to: CGPoint(x: 10.3, y: 16.3))
        p.addCurve(to: CGPoint(x: 13.7, y: 16.3), control1: CGPoint(x: 11.4, y: 15.7),
                   control2: CGPoint(x: 12.6, y: 15.7))
        return p
    }()

    /// The private note's lock.
    static let lock: Path = {
        var p = Path()
        p.addRoundedRect(in: CGRect(x: 5, y: 11, width: 14, height: 10), cornerSize: CGSize(width: 2, height: 2))
        p.move(to: CGPoint(x: 8, y: 11)); p.addLine(to: CGPoint(x: 8, y: 8))
        p.addArc(center: CGPoint(x: 12, y: 8), radius: 4, startAngle: .degrees(180),
                 endAngle: .degrees(360), clockwise: false)
        p.addLine(to: CGPoint(x: 16, y: 11))
        return p
    }()

    /// The consent card's shield with its check.
    static let shield: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 12, y: 3)); p.addLine(to: CGPoint(x: 19, y: 6)); p.addLine(to: CGPoint(x: 19, y: 11))
        p.addCurve(to: CGPoint(x: 12, y: 21), control1: CGPoint(x: 19, y: 16), control2: CGPoint(x: 15.5, y: 19.5))
        p.addCurve(to: CGPoint(x: 5, y: 11), control1: CGPoint(x: 8.5, y: 19.5), control2: CGPoint(x: 5, y: 16))
        p.addLine(to: CGPoint(x: 5, y: 6))
        p.closeSubpath()
        p.move(to: CGPoint(x: 9, y: 12)); p.addLine(to: CGPoint(x: 11, y: 14)); p.addLine(to: CGPoint(x: 15, y: 10))
        return p
    }()
}

/// The go arrow is drawn with square caps (`stroke-linecap` left square).
struct GoArrow: View {
    var size: CGFloat = 13

    var body: some View {
        Glyph(path: SearchIcons.go, box: CGRect(x: 0, y: 0, width: 24, height: 24))
            .stroke(style: StrokeStyle(lineWidth: 2.4 * size / 24, lineCap: .square, lineJoin: .miter))
            .frame(width: size, height: size)
            .accessibilityHidden(true)
    }
}

// MARK: - The attachment chips (`#attachRow`, `.attach-chip`)

/// Chips that wrap, each offered the row's width so a long failure
/// sentence wraps inside its chip rather than running off the screen.
struct ChipFlow: Layout {
    var spacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? 354
        var x: CGFloat = 0
        var y: CGFloat = 0
        var line: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(ProposedViewSize(width: width, height: nil))
            if x > 0 && x + size.width > width {
                y += line + spacing
                x = 0
                line = 0
            }
            x += size.width + spacing
            line = max(line, size.height)
        }
        return CGSize(width: width, height: y + line)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX
        var y = bounds.minY
        var line: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(ProposedViewSize(width: bounds.width, height: nil))
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

/// 28 tall, radius 9, padding 6/9, surface with the strong border, the
/// name Geist 11 (at most 150 wide), the note Geist Mono 8 uppercase and
/// the ×; 0.6 while reading; coral once failed.
struct AttachChip: View {
    let slot: AttachSlot
    let palette: SearchPalette
    let remove: () -> Void

    var body: some View {
        let failed = !slot.error.isEmpty
        let ink = failed ? FAMColor.coral : palette.text
        let soft = failed ? FAMColor.coral : palette.textMuted
        return HStack(spacing: 7) {
            Text(slot.name.isEmpty ? "attachment" : slot.name)
                .font(FAMFont.body(11))
                .foregroundStyle(ink)
                .lineLimit(1)
                .truncationMode(.tail)
                .frame(maxWidth: 150, alignment: .leading)
                .fixedSize(horizontal: true, vertical: false)
            Text(slot.note)
                .font(FAMFont.mono(8))
                .tracking(0.48)
                .textCase(.uppercase)
                .foregroundStyle(soft)
                .lineLimit(3)
                .fixedSize(horizontal: false, vertical: true)
            Button(action: remove) {
                Text("\u{00D7}")
                    .font(FAMFont.body(13))
                    .foregroundStyle(soft)
                    .frame(minWidth: 16, minHeight: 16)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.press)
            .accessibilityLabel("Remove " + slot.name)
        }
        .padding(.horizontal, 9)
        .padding(.vertical, 6)
        .frame(minHeight: 28)
        .background(palette.surface, in: RoundedRectangle(cornerRadius: 9))
        .overlay(RoundedRectangle(cornerRadius: 9).stroke(failed ? FAMColor.coral : palette.borderStrong))
        .opacity(slot.pending ? 0.6 : 1)
    }
}

// MARK: - "Add a link" (`openAttachLink`)

/// The web's modal: the X, "Add a link", the field (https://…, no
/// autocorrect), Cancel / Add. Add with nothing typed puts the caret back.
struct LinkModal: View {
    let done: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""
    @FocusState private var focused: Bool

    var body: some View {
        ModalCard(close: { dismiss() }) {
            HStack(alignment: .top) {
                Text("Add a link")
                    .font(FAMFont.heading(16, .medium))
                    .foregroundStyle(FAMColor.text)
                Spacer()
                Button { dismiss() } label: {
                    WebIcon(path: Icons.close, size: 14, line: 2.4)
                        .foregroundStyle(FAMColor.textMuted)
                        .frame(width: 28, height: 28)
                }
                .buttonStyle(.press)
                .accessibilityLabel("Close")
            }
            .padding(.bottom, 12)
            TextField("", text: $text, prompt: Text("https://\u{2026}").foregroundColor(FAMColor.textFaint))
                .keyboardType(.URL)
                .textContentType(.URL)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled(true)
                .focused($focused)
                .submitLabel(.done)
                .onSubmit { confirm() }
                .youInput()
            HStack(spacing: 8) {
                Spacer()
                ModalButton(label: "Cancel") { dismiss() }
                ModalButton(label: "Add", primary: true) { confirm() }
            }
            .padding(.top, 16)
        }
        .onAppear { focused = true }
    }

    private func confirm() {
        let value = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { focused = true; return }
        dismiss()
        let done = self.done
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 350_000_000)
            done(value)
        }
    }
}

// MARK: - The AI consent card (`#aiConsentOverlay`, shot 19)

/// The web's centred card in place of the system sheet: the copper shield
/// in its 34 pt ring, the server's title, body and "unaffected" line, and
/// Not now / Allow on the right. Every word is the server's
/// (`consent.AI_NOTICE`). Drawn wherever a question can be asked - the
/// root, and over the player and the social covers - so it is never hidden
/// under a full-screen cover.
struct ConsentLayer: View {
    @ObservedObject var consent: ConsentModel
    /// The app's toast, for an Allow the server did not keep.
    var say: (String) -> Void = { _ in }

    var body: some View {
        ZStack {
            if let asking = consent.asking {
                OverlayModal(close: { answer(false) }) {
                    ConsentCard(notice: asking.notice) { allow in answer(allow) }
                }
                .transition(.opacity)
            }
        }
        .animation(.easeOut(duration: 0.2), value: consent.asking)
    }

    private func answer(_ allow: Bool) {
        let model = consent
        let say = self.say
        Task {
            await model.answer(allow)
            // FAMSafety keeps the old answer when the save fails; the web
            // says so ("Couldn’t save that. Try again.").
            if allow && model.current?.given != true { say("Couldn\u{2019}t save that. Try again.") }
        }
    }
}

struct ConsentCard: View {
    let notice: ConsentNotice
    let answer: (Bool) -> Void

    var body: some View {
        WebIcon(path: SearchIcons.shield, size: 20, line: 1.8)
            .foregroundStyle(FAMColor.copper)
            .frame(width: 34, height: 34)
            .background(FAMColor.background, in: Circle())
            .overlay(Circle().stroke(FAMColor.borderStrong))
            .padding(.bottom, 12)
        ModalTitle(text: notice.title)
        Text(notice.body)
            .font(FAMFont.body(12.5))
            .foregroundStyle(FAMColor.textMuted)
            .lineSpacing(6)
            .fixedSize(horizontal: false, vertical: true)
        if !notice.unaffected.isEmpty {
            Text(notice.unaffected)
                .font(FAMFont.mono(9.5))
                .tracking(0.57)
                .foregroundStyle(FAMColor.textFaint)
                .lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 12)
        }
        HStack(spacing: 8) {
            Spacer()
            ModalButton(label: notice.decline) { answer(false) }
            ModalButton(label: notice.allow, primary: true) { answer(true) }
        }
        .padding(.top, 16)
    }
}
