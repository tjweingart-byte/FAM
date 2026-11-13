import SwiftUI

// MARK: - A shared episode, before the app is open to you (§575)

/// `static/listen.html`, drawn natively for somebody signed out or still on
/// the waitlist who opens a `/s/<id>` link: "Shared with you", the title,
/// the question, "N min listen", play / pause, ±15 s, the bar clamped to
/// what has been voiced, the times, the status line, and "Want to hear
/// more?" with its door. The page is its own palette whatever the theme,
/// as listen.html restates FAM's rather than importing it. Every control
/// but play is a door (`data-door`), and every door goes where the page's
/// join door goes - `/waitlist`, which in the app is Sign up for somebody
/// signed out and their status page for somebody already in line.
struct SharedListenView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var player: StreamPlayer
    let listen: SharedListen

    // listen.html's `:root`, restated.
    private static let ink = Color(hex: 0x18151F)
    private static let paper = Color(hex: 0xF4EFE4)
    private static let copper = Color(hex: 0xE0B563)
    private static let sage = Color(hex: 0x8FAE9A)
    private static let coral = Color(hex: 0xE2694F)
    private static let muted = Color(hex: 0xF4EFE4).opacity(0.56)
    private static let line = Color(hex: 0xF4EFE4).opacity(0.14)

    private var share: SharePayload? { listen.share }
    private var nothing: Bool { listen.expired }

    var body: some View {
        GeometryReader { geo in
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    brand
                    heading
                    playerBlock.padding(.top, 34)
                    Spacer(minLength: 40)
                    doors
                }
                .frame(maxWidth: 520, alignment: .leading)
                .padding(.horizontal, 22)
                .padding(.top, 28)
                .padding(.bottom, 40)
                .frame(maxWidth: .infinity, minHeight: geo.size.height, alignment: .top)
            }
        }
        .background(Self.backdrop.ignoresSafeArea())
        .environment(\.colorScheme, .dark)
        .onChange(of: player.phase) { _, phase in
            if phase == .playing { model.sharedStarted() }
        }
    }

    // MARK: What it is

    /// The wordmark is a door too (`data-door="wordmark"`).
    private var brand: some View {
        Button { model.shareDoor() } label: {
            FAMLetters(capHeight: 26, color: Self.copper)
                .frame(height: 26, alignment: .bottom)
        }
        .buttonStyle(.press)
        .accessibilityLabel("FAM")
    }

    private var heading: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(nothing ? "Nothing to play" : "Shared with you")
                .font(FAMFont.mono(10.5))
                .tracking(10.5 * 0.16)
                .textCase(.uppercase)
                .foregroundStyle(Self.sage)
                .padding(.top, 34)
                .padding(.bottom, 10)
            Text(nothing ? "This link has expired" : titleWords)
                .font(FAMFont.heading(31, .semibold))
                .lineSpacing(31 * 0.18 - 4)
                .foregroundStyle(Self.paper)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.bottom, 12)
            if let share, !nothing {
                Text(share.question)
                    .font(FAMFont.body(14.5))
                    .lineSpacing(14.5 * 0.5 - 4)
                    .foregroundStyle(Self.muted)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.bottom, 4)
                Text(FAMAPI.minutes(share.minutes) + " min listen")
                    .font(FAMFont.mono(11))
                    .tracking(11 * 0.08)
                    .foregroundStyle(Self.copper)
                    .padding(.top, 14)
            }
        }
    }

    private var titleWords: String {
        guard let title = share?.title, !title.isEmpty else { return "A FAM episode" }
        return title
    }

    // MARK: Listening

    private var ours: Bool { model.shareStarted && !model.shareQuery.isEmpty }

    /// The page's `playing`: true from the press, until a pause or the end.
    private var playing: Bool {
        guard ours, !model.shareEnded else { return false }
        switch player.phase {
        case .paused, .finished, .failed, .idle: return false
        default: return true
        }
    }

    private var planned: Double { (share?.minutes ?? 0) * 60 }
    /// `complete ? have : max(have, planned)`.
    private var total: Double {
        guard ours else { return planned }
        return player.complete ? player.arrived : max(player.arrived, planned)
    }
    private var at: Double { ours ? player.position : 0 }
    private var canSkip: Bool { ours && player.seekLimit > 0 }
    private var canPlay: Bool { share != nil && !nothing }

    private var playerBlock: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 16) {
                Button { model.toggleShared() } label: {
                    Text(playing ? "\u{275A}\u{275A}" : "\u{25B6}\u{FE0E}")
                        .font(.system(size: 26))
                        .foregroundStyle(Self.ink)
                        .frame(width: 74, height: 74)
                        .background(Self.copper, in: Circle())
                        .opacity(canPlay ? 1 : 0.45)
                }
                .buttonStyle(.press)
                .disabled(!canPlay)
                .accessibilityLabel(playing ? "Pause" : "Play")
                skip("\u{21B6} 15", by: -15)
                skip("15 \u{21B7}", by: 15)
            }
            bar.padding(.top, 22).padding(.bottom, 8)
            HStack {
                Text(Self.clock(at))
                Spacer()
                Text(Self.clock(total))
            }
            .font(FAMFont.mono(11))
            .foregroundStyle(Self.muted)
            Text(statusWords)
                .font(FAMFont.body(13))
                .lineSpacing(13 * 0.45 - 3)
                .foregroundStyle(statusBad ? Self.coral : Self.sage)
                .fixedSize(horizontal: false, vertical: true)
                .frame(minHeight: 20, alignment: .topLeading)
                .padding(.top, 18)
        }
    }

    private func skip(_ label: String, by seconds: Double) -> some View {
        Button { _ = player.skip(by: seconds) } label: {
            Text(label)
                .font(FAMFont.mono(11))
                .foregroundStyle(Self.paper)
                .padding(.horizontal, 13)
                .padding(.vertical, 9)
                .overlay(Capsule().stroke(Self.line, lineWidth: 1))
                .opacity(canSkip ? 1 : 0.35)
        }
        .buttonStyle(.press)
        .disabled(!canSkip)
    }

    /// Drag as well as tap, clamped to what has been voiced (the player's
    /// own seek stops at `seekLimit`) - it is still being made as it plays.
    private var bar: some View {
        GeometryReader { geo in
            let pct = total > 0 ? max(0, min(1, at / total)) : 0
            ZStack(alignment: .leading) {
                Capsule().fill(Self.paper.opacity(0.16)).frame(height: 4)
                Capsule().fill(Self.copper).frame(width: geo.size.width * pct, height: 4)
                Circle().fill(Self.paper).frame(width: 13, height: 13)
                    .offset(x: geo.size.width * pct - 6.5)
            }
            .frame(height: 22)
            .contentShape(Rectangle())
            .gesture(DragGesture(minimumDistance: 0).onChanged { drag in
                guard ours, geo.size.width > 0 else { return }
                let to = max(0, min(1, drag.location.x / geo.size.width))
                player.seek(to: to * total)
            })
        }
        .frame(height: 22)
        .accessibilityElement()
        .accessibilityLabel("Position")
        .accessibilityValue(Self.clock(at))
    }

    private var statusWords: String {
        if nothing { return "Ask FAM anything of your own and it will make you an episode." }
        if !listen.problem.isEmpty { return listen.problem }
        return model.shareNote
    }

    private var statusBad: Bool {
        nothing || !listen.problem.isEmpty || model.shareNoteBad
    }

    // MARK: The doors

    /// "Want to hear more?" and the join door, drawn whether or not the app
    /// is out (§319); with an App Store link, the page's other two doors.
    @ViewBuilder private var doors: some View {
        if share != nil && !nothing {
            VStack(alignment: .leading, spacing: 0) {
                Text("Want to hear more?")
                    .font(FAMFont.heading(19, .medium))
                    .foregroundStyle(Self.paper)
                    .padding(.bottom, 12)
                door("Be a part of the conversation with FAM \u{2192}", join: true)
                if share?.hasApp == true {
                    Text("This is one episode. FAM makes them about anything you ask.")
                        .font(FAMFont.body(13.5))
                        .lineSpacing(13.5 * 0.5 - 4)
                        .foregroundStyle(Self.muted)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.bottom, 14)
                    door("Ask your own question", join: false)
                    door("Browse episodes", join: false)
                } else {
                    Text("FAM makes an episode about anything you ask.")
                        .font(FAMFont.body(12))
                        .lineSpacing(12 * 0.5 - 3)
                        .foregroundStyle(Self.muted)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, 8)
                }
            }
        }
    }

    private func door(_ words: String, join: Bool) -> some View {
        Button { model.shareDoor() } label: {
            Text(words)
                .font(FAMFont.body(join ? 15 : 14, join ? .semibold : .medium))
                .foregroundStyle(join ? Self.ink : Self.paper)
                .multilineTextAlignment(.center)
                .padding(.vertical, 15)
                .padding(.horizontal, 18)
                .frame(maxWidth: .infinity)
                .background(join ? Self.copper : Color.clear,
                            in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(join ? Color.clear : Self.line, lineWidth: 1))
        }
        .buttonStyle(.press)
        .padding(.bottom, 10)
    }

    // MARK: Pieces

    /// The body's two gradients: a copper glow at 18 % / 8 % over the
    /// 170° ink fall.
    private static var backdrop: some View {
        ZStack {
            LinearGradient(stops: [.init(color: Color(hex: 0x2A2733), location: 0),
                                   .init(color: Color(hex: 0x1E1B27), location: 0.55),
                                   .init(color: Color(hex: 0x38334A), location: 1)],
                           startPoint: UnitPoint(x: 0.41, y: 0), endPoint: UnitPoint(x: 0.59, y: 1))
            EllipticalGradient(colors: [copper.opacity(0.14), .clear],
                               center: UnitPoint(x: 0.18, y: 0.08),
                               startRadiusFraction: 0, endRadiusFraction: 0.6)
        }
    }

    /// `clock()`: m:ss.
    static func clock(_ seconds: Double) -> String {
        let s = seconds.isFinite && seconds > 0 ? Int(seconds) : 0
        return "\(s / 60):" + String(format: "%02d", s % 60)
    }
}

/// The shared screen over the front door or the status page, while one is
/// open and nobody is let in yet: up from below, down when it closes.
struct SharedListenLayer: View {
    @EnvironmentObject private var model: AppModel

    private var shown: Bool {
        model.stage == .signedOut || model.stage == .waiting
    }

    var body: some View {
        ZStack {
            if shown, let listen = model.sharedListen {
                SharedListenView(listen: listen)
                    .transition(.move(edge: .bottom))
            }
        }
        .animation(.timingCurve(0.2, 0.8, 0.2, 1, duration: 0.3), value: model.sharedListen?.id)
    }
}
