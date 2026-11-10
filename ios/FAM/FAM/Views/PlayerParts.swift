import SwiftUI
import UIKit

// The player's own pieces after the 11.9 packet (§519), each ported from the
// web (`static/index.html`): the transport's drawn glyphs (`.pc-row1`), the
// Captions pill and the captions card over the picture (`#ccIcon`,
// `#ccPanel`, `toggleCC`), the centred modal (`.modal-card`), the title's
// wipe (`Motion.retitle`), the save / VIBE! burst (`Motion.celebrate`), the
// action sheet (`showActionSheet`), "Which one do you mean?" (`askReading`,
// §426) and the limit screen (`showLimitReached`). Rules: `player-layout`,
// `transport`, `live-captions`, `speed-pitch`.

// MARK: - The player's icons, path for path

enum PlayerIcons {
    /// An SVG arc drawn as short lines between two angles (degrees, y down),
    /// so its direction never depends on a flipped coordinate system.
    static func arc(_ p: inout Path, cx: CGFloat, cy: CGFloat, r: CGFloat,
                    from a0: Double, to a1: Double, move: Bool = true) {
        let steps = 48
        for i in 0...steps {
            let t = Double(i) / Double(steps)
            let a = (a0 + (a1 - a0) * t) * Double.pi / 180
            let x = cx + r * CGFloat(cos(a))
            let y = cy + r * CGFloat(sin(a))
            if i == 0 && move { p.move(to: CGPoint(x: x, y: y)) } else { p.addLine(to: CGPoint(x: x, y: y)) }
        }
    }

    /// −15's ring (`M10.6 4.6A8.4 8.4 0 1 1 4.3 9.6`).
    static let backRing: Path = {
        var p = Path()
        PlayerIcons.arc(&p, cx: 12.0346, cy: 12.8766, r: 8.4, from: -99.8335, to: 202.9589)
        return p
    }()

    /// −15's arrowhead (`M12.6 2.2 9.4 4.7l3.2 2.5z`).
    static let backHead: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 12.6, y: 2.2)); p.addLine(to: CGPoint(x: 9.4, y: 4.7))
        p.addLine(to: CGPoint(x: 12.6, y: 7.2)); p.closeSubpath()
        return p
    }()

    /// +15's ring, the mirror (`M13.4 4.6A8.4 8.4 0 1 0 19.7 9.6`).
    static let forwardRing: Path = {
        var p = Path()
        PlayerIcons.arc(&p, cx: 11.9654, cy: 12.8766, r: 8.4, from: -80.1665, to: -382.9589)
        return p
    }()

    /// +15's arrowhead (`M11.4 2.2 14.6 4.7l-3.2 2.5z`).
    static let forwardHead: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 11.4, y: 2.2)); p.addLine(to: CGPoint(x: 14.6, y: 4.7))
        p.addLine(to: CGPoint(x: 11.4, y: 7.2)); p.closeSubpath()
        return p
    }()

    /// Next: the solid triangle and its bar (`#mixSkipBtn`).
    static let nextTriangle: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 5.5, y: 5.2)); p.addLine(to: CGPoint(x: 5.5, y: 18.8))
        p.addLine(to: CGPoint(x: 17.3, y: 12)); p.closeSubpath()
        return p
    }()

    static let nextBar: Path = {
        Path(roundedRect: CGRect(x: 18.4, y: 4.6, width: 2.4, height: 14.8), cornerRadius: 1.1)
    }()

    /// Pause: two rounded bars (`rect x 6 / 13.4, 4.6 wide, rx 1.7`).
    static let pause: Path = {
        var p = Path()
        p.addRoundedRect(in: CGRect(x: 6, y: 3.8, width: 4.6, height: 16.4), cornerSize: CGSize(width: 1.7, height: 1.7))
        p.addRoundedRect(in: CGRect(x: 13.4, y: 3.8, width: 4.6, height: 16.4), cornerSize: CGSize(width: 1.7, height: 1.7))
        return p
    }()

    /// The Captions pill's glyph, in its own 24 x 18 box (`#ccIcon svg`).
    static let captionsPill: Path = {
        var p = Path()
        p.addRoundedRect(in: CGRect(x: 1.1, y: 1.1, width: 21.8, height: 15.8), cornerSize: CGSize(width: 4, height: 4))
        PlayerIcons.arc(&p, cx: 8.3029, cy: 9, r: 2.7, from: -51.0576, to: -308.9424)
        PlayerIcons.arc(&p, cx: 15.7029, cy: 9, r: 2.7, from: -51.0576, to: -308.9424)
        return p
    }()

    /// The menu's "Closed captions" (`MENU_ICONS.cc`).
    static let captionsMenu: Path = {
        var p = Path()
        p.addRoundedRect(in: CGRect(x: 2.5, y: 5, width: 19, height: 14), cornerSize: CGSize(width: 3, height: 3))
        PlayerIcons.arc(&p, cx: 8.5682, cy: 12, r: 2.3, from: -51.5, to: -308.5)
        PlayerIcons.arc(&p, cx: 15.1682, cy: 12, r: 2.3, from: -51.5, to: -308.5)
        return p
    }()

    /// The queue button's lines (`M4 6h11M4 12h11M4 18h7`).
    static let queueLines: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 4, y: 6)); p.addLine(to: CGPoint(x: 15, y: 6))
        p.move(to: CGPoint(x: 4, y: 12)); p.addLine(to: CGPoint(x: 15, y: 12))
        p.move(to: CGPoint(x: 4, y: 18)); p.addLine(to: CGPoint(x: 11, y: 18))
        return p
    }()

    /// ... and its filled triangle (`M17 14v6l4-3z`).
    static let queueTriangle: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 17, y: 14)); p.addLine(to: CGPoint(x: 17, y: 20))
        p.addLine(to: CGPoint(x: 21, y: 17)); p.closeSubpath()
        return p
    }()

    /// The captions card's copy.
    static let copy: Path = {
        var p = Path()
        p.addRoundedRect(in: CGRect(x: 8, y: 8, width: 12, height: 12), cornerSize: CGSize(width: 2, height: 2))
        p.move(to: CGPoint(x: 16, y: 8)); p.addLine(to: CGPoint(x: 16, y: 6))
        p.addQuadCurve(to: CGPoint(x: 14, y: 4), control: CGPoint(x: 16, y: 4))
        p.addLine(to: CGPoint(x: 6, y: 4))
        p.addQuadCurve(to: CGPoint(x: 4, y: 6), control: CGPoint(x: 4, y: 4))
        p.addLine(to: CGPoint(x: 4, y: 14))
        p.addQuadCurve(to: CGPoint(x: 6, y: 16), control: CGPoint(x: 4, y: 16))
        p.addLine(to: CGPoint(x: 8, y: 16))
        return p
    }()

    /// Remove from this playlist: a circled minus (`MENU_ICONS.removeMix`).
    static let circleMinus: Path = {
        var p = Path()
        p.addEllipse(in: CGRect(x: 3, y: 3, width: 18, height: 18))
        p.move(to: CGPoint(x: 8, y: 12)); p.addLine(to: CGPoint(x: 16, y: 12))
        return p
    }()

    /// Report: the flag (`M5 21V4`, `M5 4h11l-2 4 2 4H5`).
    static let flag: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 5, y: 21)); p.addLine(to: CGPoint(x: 5, y: 4))
        p.move(to: CGPoint(x: 5, y: 4)); p.addLine(to: CGPoint(x: 16, y: 4))
        p.addLine(to: CGPoint(x: 14, y: 8)); p.addLine(to: CGPoint(x: 16, y: 12))
        p.addLine(to: CGPoint(x: 5, y: 12))
        return p
    }()

    /// The limit screen's mark: a circled exclamation.
    static let alert: Path = {
        var p = Path()
        p.addEllipse(in: CGRect(x: 3, y: 3, width: 18, height: 18))
        p.move(to: CGPoint(x: 12, y: 7.5)); p.addLine(to: CGPoint(x: 12, y: 12.5))
        p.move(to: CGPoint(x: 12, y: 16)); p.addLine(to: CGPoint(x: 12, y: 16.01))
        return p
    }()

    /// The mini player's empty thumb: headphones.
    static let headphones: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 4, y: 15)); p.addLine(to: CGPoint(x: 4, y: 12))
        p.addCurve(to: CGPoint(x: 20, y: 12), control1: CGPoint(x: 4, y: 1.5), control2: CGPoint(x: 20, y: 1.5))
        p.addLine(to: CGPoint(x: 20, y: 15))
        p.addRoundedRect(in: CGRect(x: 3, y: 14, width: 4, height: 6), cornerSize: CGSize(width: 1.5, height: 1.5))
        p.addRoundedRect(in: CGRect(x: 17, y: 14, width: 4, height: 6), cornerSize: CGSize(width: 1.5, height: 1.5))
        return p
    }()
}

// MARK: - The transport's glyphs

/// ±15: a bare 34 pt arrow, stroke 1.7, with "15" in Geist 10.5 bold
/// (−.02em) laid over its middle (§519).
struct SeekGlyph: View {
    let back: Bool

    var body: some View {
        let scale: CGFloat = 34 / 24
        let box = CGRect(x: 0, y: 0, width: 24, height: 24)
        let ring = back ? PlayerIcons.backRing : PlayerIcons.forwardRing
        let head = back ? PlayerIcons.backHead : PlayerIcons.forwardHead
        ZStack {
            Glyph(path: ring, box: box)
                .stroke(style: StrokeStyle(lineWidth: 1.7 * scale, lineCap: .round, lineJoin: .round))
            Glyph(path: head, box: box).fill()
            Glyph(path: head, box: box)
                .stroke(style: StrokeStyle(lineWidth: 1.2 * scale, lineCap: .round, lineJoin: .round))
            Text("15")
                .font(FAMFont.body(10.5, .bold))
                .tracking(-0.21)
                .offset(y: 1)
        }
        .frame(width: 34, height: 34)
        .accessibilityHidden(true)
    }
}

/// The copper play's glyph: pause bars or the rounded triangle, 22 pt, ink.
struct PlayPauseGlyph: View {
    let playing: Bool
    var size: CGFloat = 22

    var body: some View {
        let box = CGRect(x: 0, y: 0, width: 24, height: 24)
        ZStack {
            if playing {
                Glyph(path: PlayerIcons.pause, box: box).fill()
                    .transition(.scale(scale: 0.5).combined(with: .opacity))
            } else {
                ZStack {
                    Glyph(path: Icons.play, box: box).fill()
                    Glyph(path: Icons.play, box: box)
                        .stroke(style: StrokeStyle(lineWidth: 2.2 * size / 24, lineJoin: .round))
                }
                .transition(.scale(scale: 0.5).combined(with: .opacity))
            }
        }
        .frame(width: size, height: size)
        .foregroundStyle(FAMColor.ink)
        // The swap: the old one shrinks out, the new one pops in
        // (`Motion.swapIcon`, 90 ms + 180 ms).
        .animation(.spring(response: 0.27, dampingFraction: 0.6), value: playing)
        .accessibilityHidden(true)
    }
}

/// Next (slot five): solid, 26 pt.
struct NextGlyph: View {
    var body: some View {
        let box = CGRect(x: 0, y: 0, width: 24, height: 24)
        ZStack {
            Glyph(path: PlayerIcons.nextTriangle, box: box).fill()
            Glyph(path: PlayerIcons.nextTriangle, box: box)
                .stroke(style: StrokeStyle(lineWidth: 1.4 * 26 / 24, lineJoin: .round))
            Glyph(path: PlayerIcons.nextBar, box: box).fill()
        }
        .frame(width: 26, height: 26)
        .accessibilityHidden(true)
    }
}

/// The Captions pill's glyph, 18 x 14 from its 24 x 18 box, stroke 1.9.
struct CaptionsGlyph: View {
    var body: some View {
        Glyph(path: PlayerIcons.captionsPill, box: CGRect(x: 0, y: 0, width: 24, height: 18))
            .stroke(style: StrokeStyle(lineWidth: 1.9 * 18 / 24, lineCap: .round, lineJoin: .round))
            .frame(width: 18, height: 14)
            .accessibilityHidden(true)
    }
}

/// The queue button's glyph: three lines and a filled triangle, 16 pt.
struct QueueGlyph: View {
    var body: some View {
        let box = CGRect(x: 0, y: 0, width: 24, height: 24)
        ZStack {
            Glyph(path: PlayerIcons.queueLines, box: box)
                .stroke(style: StrokeStyle(lineWidth: 2 * 16 / 24, lineCap: .round, lineJoin: .round))
            Glyph(path: PlayerIcons.queueTriangle, box: box).fill()
        }
        .frame(width: 16, height: 16)
        .accessibilityHidden(true)
    }
}

// MARK: - The Captions pill (`.cc-pill`, §519)

/// 36 tall, radius 18, "Captions" in Geist 12.5 bold beside the glyph;
/// copper with ink when on.
struct CaptionsPill: View {
    let on: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 7) {
                CaptionsGlyph()
                Text("Captions")
                    .font(FAMFont.body(12.5, .bold))
                    .lineLimit(1)
            }
            .foregroundStyle(on ? FAMColor.ink : FAMColor.text)
            .padding(.leading, 12)
            .padding(.trailing, 14)
            .frame(minHeight: 36)
            .background(on ? FAMColor.copper : FAMColor.surface, in: Capsule())
            .overlay(Capsule().stroke(on ? FAMColor.copper : FAMColor.borderStrong))
        }
        .buttonStyle(.press)
        .accessibilityLabel("Captions")
        .accessibilityValue(on ? "On" : "Off")
        .accessibilityAddTraits(on ? .isSelected : [])
    }
}

// MARK: - The captions card (`#ccPanel`, §519)

/// Over the picture, inset 8 / 12: a dark blurred card, "Captions" in copper
/// mono, copy and X on the right, and the sentence being said - one at a
/// time, Bricolage 24 semibold - centred in what is left, cross-fading as
/// the next one comes. The right padding leaves room for share / VIBE! /
/// save, which stay on top. A swipe down closes it.
struct CaptionsCard: View {
    @EnvironmentObject private var player: StreamPlayer
    @EnvironmentObject private var now: NowPlayingModel

    private static let ink = Color(red: 18 / 255, green: 15 / 255, blue: 28 / 255)
    private static let light = Color(hex: 0xF4EFE4)

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            topRow
            sentenceArea
        }
        .padding(.top, 14)
        .padding(.leading, 18)
        .padding(.trailing, 64)
        .padding(.bottom, 16)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Self.ink.opacity(0.9), in: RoundedRectangle(cornerRadius: 18))
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 18))
        .shadow(color: .black.opacity(0.3), radius: 14, y: 18)
        .contentShape(RoundedRectangle(cornerRadius: 18))
        // Swipe down to close (> 40 pt), as the web's `ccSwipe` - but not a
        // swipe that starts while a long sentence is scrolled down: that
        // one scrolls the text back up.
        .simultaneousGesture(DragGesture(minimumDistance: 14)
            .onChanged { _ in
                if swipeFromScrolled == nil { swipeFromScrolled = textScrolled }
            }
            .onEnded { value in
                let fromScrolled = swipeFromScrolled ?? textScrolled
                swipeFromScrolled = nil
                guard !fromScrolled else { return }
                let down = value.translation.height
                if down > 40 && down > abs(value.translation.width) { now.setCaptions(false) }
            })
    }

    /// The sentence is scrolled away from its top (`cc-text.scrollTop > 0`).
    @State private var textScrolled = false
    /// Whether the swipe in progress started with the text scrolled.
    @State private var swipeFromScrolled: Bool?

    private var topRow: some View {
        HStack(spacing: 8) {
            Text("Captions")
                .font(FAMFont.mono(10, bold: true))
                .tracking(1.2)
                .textCase(.uppercase)
                .foregroundStyle(FAMColor.copper)
            Spacer(minLength: 0)
            roundButton(PlayerIcons.copy, line: 2, label: "Copy the transcript", action: copy)
            roundButton(Icons.close, line: 2.2, label: "Turn captions off") { now.setCaptions(false) }
        }
        // The buttons reach past the card's right padding (`.cc-toprow`).
        .padding(.trailing, -50)
    }

    private func roundButton(_ path: Path, line: CGFloat, label: String,
                             action: @escaping () -> Void) -> some View {
        Button(action: action) {
            WebIcon(path: path, size: 14, line: line)
                .foregroundStyle(Self.light)
                .frame(width: 32, height: 32)
                .background(Self.light.opacity(0.08), in: Circle())
                .overlay(Circle().stroke(Self.light.opacity(0.25)))
        }
        .buttonStyle(.press)
        .accessibilityLabel(label)
    }

    private var currentIndex: Int {
        now.captionIndex(at: player.position, total: player.total) ?? 0
    }

    private var currentText: String {
        // What the web's card says while captions are off (`renderCaptions`),
        // here only as the card fades out.
        if !now.captionsOn { return "Displayed here when captions are turned on." }
        if now.sentences.isEmpty {
            return now.captionsDone
                ? "No transcript for this one \u{2014} an episode you attached a file to is never stored, so there is nothing to read back."
                : "Catching up with the script\u{2026}"
        }
        let i = min(max(currentIndex, 0), now.sentences.count - 1)
        return now.sentences[i]
    }

    /// The sentence key: one per sentence, -1 for the placeholder, so the
    /// cross-fade runs only when the sentence changes.
    private var sentenceKey: Int {
        now.sentences.isEmpty ? -1 : currentIndex
    }

    private var sentenceArea: some View {
        GeometryReader { geo in
            ScrollView(showsIndicators: false) {
                ZStack(alignment: .leading) {
                    Text(currentText)
                        .font(FAMFont.heading(24, .semibold))
                        .tracking(-0.24)
                        .lineSpacing(24 * 0.28 - 4)
                        .foregroundStyle(Self.light)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .id(sentenceKey)
                        .transition(.asymmetric(
                            insertion: .opacity.combined(with: .offset(y: 4)),
                            removal: .opacity.combined(with: .offset(y: -4))))
                }
                .frame(maxWidth: .infinity, minHeight: geo.size.height, alignment: .leading)
                .animation(.easeInOut(duration: 0.32), value: sentenceKey)
                .background(GeometryReader { inner in
                    // Where the text's top sits in the scroll view: above
                    // it once a long sentence is scrolled down.
                    let top = inner.frame(in: .named("ccScroll")).minY
                    Color.clear.onChange(of: top) { _, y in
                        let scrolled = y < -1
                        if scrolled != textScrolled { textScrolled = scrolled }
                    }
                })
            }
            .coordinateSpace(name: "ccScroll")
            // A sentence that fits does not bounce, so the swipe is the card's.
            .scrollBounceBehavior(.basedOnSize)
        }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.updatesFrequently)
    }

    private func copy() {
        guard !now.sentences.isEmpty else { now.say("No transcript for this one yet"); return }
        UIPasteboard.general.string = now.sentences.joined(separator: " ")
        now.say("Transcript copied \u{2713}")
    }
}

// MARK: - The centred modal (`.modal-card` over `.modal-overlay`)

/// Drawn inside the player rather than presented: 312 wide, padding 20,
/// radius 18, surface with a strong border and the web's shadow, over a dim
/// (`rgba(10,9,15,.62)`) that closes it. It fades in over 200 ms and the
/// card lifts 14 pt (`mo-in`, `mo-lift`).
struct OverlayModal<Content: View>: View {
    let close: () -> Void
    @ViewBuilder var content: () -> Content
    @State private var lifted = false

    var body: some View {
        ZStack {
            Color(hex: 0x0A090F).opacity(0.62)
                .ignoresSafeArea()
                .contentShape(Rectangle())
                .onTapGesture { close() }
                .accessibilityHidden(true)
            card
        }
    }

    private var card: some View {
        VStack(alignment: .leading, spacing: 0) {
            content()
        }
        .padding(20)
        .frame(width: 312)
        .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 18))
        .overlay(RoundedRectangle(cornerRadius: 18).stroke(FAMColor.borderStrong))
        .shadow(color: .black.opacity(0.35), radius: 20, y: 20)
        .offset(y: lifted ? 0 : 14)
        .onAppear {
            withAnimation(.timingCurve(0.2, 0.8, 0.2, 1, duration: 0.2)) { lifted = true }
        }
    }
}

/// `.modal-title`: Bricolage 16 medium, 12 below.
struct ModalTitle: View {
    let text: String

    var body: some View {
        Text(text)
            .font(FAMFont.heading(16, .medium))
            .foregroundStyle(FAMColor.text)
            .padding(.bottom, 12)
            .accessibilityAddTraits(.isHeader)
    }
}

// MARK: - The title's wipe (`Motion.retitle`)

/// A new title is set at once and revealed left to right over 320 ms
/// (`clip-path: inset(0 100% 0 0)` to `inset(0 0 0 0)`), only when there
/// was a title before.
struct WipeText: View {
    let text: String
    var font: Font
    var color: Color = FAMColor.text
    var lines: Int = 1
    var lineSpacing: CGFloat = 0
    @State private var reveal: CGFloat = 1

    var body: some View {
        Text(text)
            .font(font)
            .foregroundStyle(color)
            .lineSpacing(lineSpacing)
            .lineLimit(lines)
            .mask(alignment: .leading) {
                GeometryReader { geo in
                    Rectangle().frame(width: geo.size.width * reveal)
                }
            }
            .onChange(of: text) { old, _ in
                guard !old.isEmpty else { return }
                reveal = 0
                withAnimation(.timingCurve(0.4, 0, 0.2, 1, duration: 0.32)) { reveal = 1 }
            }
    }
}

// MARK: - The save / VIBE! burst (`Motion.celebrate`)

/// Only when the listener turns one on: the icon squeezes (1 → .8 → 1.2 → 1
/// in 380 ms), a copper ring grows from its centre and fades (.4 → 1.7,
/// 450 ms), six sparks fly out at 60° steps, and a "SAVED" / "VIBED"
/// lozenge shows beside it for 1.1 s.
struct BurstModifier: ViewModifier {
    let trigger: Int
    let word: String
    @State private var squeeze: CGFloat = 1
    @State private var ring: CGFloat = 0.4
    @State private var ringOpacity: Double = 0
    @State private var spread: CGFloat = 0
    @State private var sparkOpacity: Double = 0
    @State private var lozenge: Double = 0

    func body(content: Content) -> some View {
        content
            .scaleEffect(squeeze)
            .background(sparks)
            .overlay(alignment: .trailing) { label }
            .onChange(of: trigger) { _, _ in fire() }
    }

    private var sparks: some View {
        ZStack {
            Circle()
                .stroke(FAMColor.copper, lineWidth: 2)
                .frame(width: 40, height: 40)
                .scaleEffect(ring)
                .opacity(ringOpacity)
            ForEach(0..<6, id: \.self) { i in
                let angle = Double(i) * Double.pi / 3
                Circle()
                    .fill(FAMColor.copper)
                    .frame(width: 4, height: 4)
                    .offset(x: CGFloat(cos(angle)) * 32 * spread, y: CGFloat(sin(angle)) * 32 * spread)
                    .opacity(sparkOpacity)
            }
        }
        .allowsHitTesting(false)
    }

    private var label: some View {
        Text(word)
            .font(FAMFont.mono(9, bold: true))
            .tracking(0.72)
            .foregroundStyle(FAMColor.ink)
            .padding(.horizontal, 7)
            .padding(.vertical, 4)
            .background(FAMColor.copper, in: Capsule())
            .fixedSize()
            .offset(x: -50)
            .opacity(lozenge)
            .allowsHitTesting(false)
            .accessibilityHidden(true)
    }

    private func fire() {
        if UIAccessibility.isReduceMotionEnabled {
            lozenge = 1
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.1) { lozenge = 0 }
            return
        }
        ring = 0.4
        ringOpacity = 1
        spread = 0
        sparkOpacity = 1
        withAnimation(.easeOut(duration: 0.1)) { squeeze = 0.8 }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) {
            withAnimation(.easeOut(duration: 0.14)) { squeeze = 1.2 }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.24) {
            withAnimation(.easeOut(duration: 0.14)) { squeeze = 1 }
        }
        withAnimation(.easeOut(duration: 0.45)) {
            ring = 1.7
            ringOpacity = 0
            spread = 1
            sparkOpacity = 0
        }
        withAnimation(.easeOut(duration: 0.2)) { lozenge = 1 }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.9) {
            withAnimation(.easeIn(duration: 0.2)) { lozenge = 0 }
        }
    }
}

// MARK: - The action sheet (`showActionSheet`)

/// One row of a bottom action sheet, or a group heading.
struct ActionItem: Identifiable {
    let id = UUID()
    let label: String
    var group = false
    var action: () -> Void = {}
}

/// The web's bottom sheet: rows 14 / 22, Geist 13.5 semibold; group
/// headings in mono caps; the white Cancel bar.
struct ActionSheetList: View {
    let items: [ActionItem]
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(items) { item in
                if item.group {
                    Text(item.label)
                        .monoLabel(9, color: FAMColor.textFaint)
                        .padding(.horizontal, 22)
                        .padding(.top, 12)
                        .padding(.bottom, 4)
                } else {
                    Button {
                        dismiss()
                        item.action()
                    } label: {
                        Text(item.label)
                            .font(FAMFont.body(13.5, .semibold))
                            .foregroundStyle(FAMColor.text)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.horizontal, 22)
                            .padding(.vertical, 14)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.press)
                }
            }
            SheetCancelButton { dismiss() }
        }
        .padding(.top, 10)
        .padding(.bottom, 12)
        .background(FAMColor.surface)
        .presentationDetents([.medium, .large])
        .presentationBackground(FAMColor.surface)
    }
}

// MARK: - Refusals the app answers itself (`onGenerationFailed`)

/// "Which one do you mean?" (§426) and the limit screen, over every screen.
struct RefusalLayer: View {
    @EnvironmentObject private var model: AppModel
    /// "See plans" (`openPlansFromLimit`): the plans list, asked as it opens.
    @State private var plans = false

    var body: some View {
        ZStack {
            if let ask = model.readingAsk {
                // The web's reading card has no backdrop close: Cancel is
                // the only way out without an answer.
                OverlayModal(close: {}) {
                    ReadingCard(ask: ask)
                }
                .transition(.opacity)
            }
            if let verdict = model.limitReached {
                OverlayModal(close: { model.limitReached = nil }) {
                    LimitCard(verdict: verdict) {
                        model.limitReached = nil
                        plans = true
                    }
                }
                .transition(.opacity)
            }
        }
        .animation(.easeOut(duration: 0.2), value: model.readingAsk)
        .animation(.easeOut(duration: 0.2), value: model.limitReached)
        .sheet(isPresented: $plans) {
            PlansSheet(page: nil, fetch: true)
                .environmentObject(model)
                .famThemed()
        }
    }
}

/// The question as it was typed, up to four of the server's readings as
/// copper buttons, and Cancel (`askReading`).
struct ReadingCard: View {
    @EnvironmentObject private var model: AppModel
    let ask: ReadingAsk

    var body: some View {
        ModalTitle(text: "Which one do you mean?")
        Text(ask.question)
            .font(FAMFont.body(12.5))
            .foregroundStyle(FAMColor.textMuted)
            .lineSpacing(5)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.bottom, 14)
        VStack(spacing: 8) {
            ForEach(ask.readings, id: \.self) { reading in
                ModalButton(label: reading, primary: true, wide: true) { model.answerReading(reading) }
            }
        }
        HStack {
            Spacer()
            ModalButton(label: "Cancel") { model.answerReading(nil) }
        }
        .padding(.top, 8)
    }
}

/// `showLimitReached`: the server's title and sentence, the meter only where
/// both numbers are real, and the reset in the listener's own clock.
struct LimitCard: View {
    @EnvironmentObject private var model: AppModel
    let verdict: QuotaVerdict
    /// "See plans": the card closes and the plans list opens.
    var seePlans: () -> Void = {}

    var body: some View {
        WebIcon(path: PlayerIcons.alert, size: 18, line: 2)
            .foregroundStyle(FAMColor.copper)
            .frame(width: 34, height: 34)
            .background(FAMColor.background, in: Circle())
            .overlay(Circle().stroke(FAMColor.borderStrong))
            .padding(.bottom, 12)
        ModalTitle(text: (verdict.title ?? "").isEmpty ? "You've reached your limit" : (verdict.title ?? ""))
        Text((verdict.message ?? "").isEmpty ? "You have used everything your plan allows for now."
             : (verdict.message ?? ""))
            .font(FAMFont.body(12.5))
            .foregroundStyle(FAMColor.textMuted)
            .lineSpacing(3)
            .fixedSize(horizontal: false, vertical: true)
        if let share = meterShare {
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(FAMColor.border)
                    Capsule().fill(FAMColor.copper).frame(width: geo.size.width * share)
                }
            }
            .frame(height: 4)
            .padding(.top, 14)
        }
        if let when = resetText {
            Text(when)
                .font(FAMFont.mono(9.5))
                .foregroundStyle(FAMColor.textMuted)
                .padding(.top, 10)
        }
        HStack(spacing: 8) {
            Spacer()
            ModalButton(label: "Not now") { model.limitReached = nil }
            ModalButton(label: "See plans", primary: true) { seePlans() }
        }
        .padding(.top, 14)
    }

    private var meterShare: CGFloat? {
        guard verdict.unlimited != true, let limit = verdict.limit, limit > 0,
              let used = verdict.used else { return nil }
        return CGFloat(min(max(Double(used) / Double(limit), 0), 1))
    }

    /// "Resets 5:00 PM in your time" - with the weekday when it is more
    /// than 20 hours off (`resetWhen`).
    private var resetText: String? {
        guard let at = verdict.resetsAt, at > 0 else { return nil }
        let when = Date(timeIntervalSince1970: at)
        let time = DateFormatter()
        time.timeStyle = .short
        time.dateStyle = .none
        var text = time.string(from: when)
        if when.timeIntervalSinceNow >= 20 * 3600 {
            let day = DateFormatter()
            day.dateFormat = "EEEE"
            text += " on " + day.string(from: when)
        }
        return "Resets " + text + " in your time"
    }
}
