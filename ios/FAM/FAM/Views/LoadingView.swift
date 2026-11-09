import SwiftUI

/// The loading screen's clock, ported from `static/index.html`'s five steps
/// (§148, §263, `search-no-cover`):
///
///   0 Contextualizing your search   the brief
///   1 Retrieving your information   the search and the live providers
///   2 Verifying the relevance       the writer's planning, to its first token
///   3 Finalizing your script        its first sentence, handed to the voice
///   4 Generating the audio          the first audio, here on the phone
///
/// Each of the first four is checked off only when `/api/progress` says the
/// server finished it; the fifth when the audio has arrived. Each stays on
/// screen at least 2 s (the first 0.5 s, the owner's direction), and the
/// audio is held until the fifth check has been seen for 0.4 s. A replay
/// has nothing to write: all five show done and it starts after 0.45 s, so
/// it never reads as a flash. The numbers are the web's own.
@MainActor
final class LoadingModel: ObservableObject {
    static let labels = [
        "Contextualizing your search",
        "Retrieving your information",
        "Verifying the relevance",
        "Finalizing your script",
        "Generating the audio",
    ]
    static let stepMin: TimeInterval = 2.0          // GEN_STEP_MIN_MS
    static let firstStepMin: TimeInterval = 0.5     // GEN_STEP_FIRST_MIN_MS
    static let poll: TimeInterval = 0.7             // GEN_STEP_POLL_MS
    static let firstPoll: TimeInterval = 0.25       // GEN_STEP_FIRST_POLL_MS
    static let lastBeat: TimeInterval = 0.4         // GEN_STEP_LAST_BEAT_MS
    static let minVisible: TimeInterval = 0.45      // GEN_MIN_VISIBLE_MS

    @Published private(set) var visible = false
    /// How many steps are checked off (0-5).
    @Published private(set) var shown = 0

    /// Start the held audio and open the player.
    var onRelease: (() -> Void)?

    private var server = [false, false, false, false]
    private var audioReady = false
    private var releasing = false
    private var since = Date()
    private var shownAt = Date()
    private var run = 0
    private var ask: (() async -> Progress?)?

    func start(ask: @escaping () async -> Progress?) {
        run += 1
        let mine = run
        self.ask = ask
        server = [false, false, false, false]
        audioReady = false
        releasing = false
        shown = 0
        since = Date()
        shownAt = Date()
        visible = true
        Task { await self.tick(mine) }
        Task { await self.pollLoop(mine) }
    }

    /// The audio has arrived and is held.
    func ready(cacheHit: Bool) {
        guard visible else { onRelease?(); return }
        audioReady = true
        if cacheHit {
            // A replay: all five done, held only long enough to be seen.
            shown = Self.labels.count
            let wait = max(0, Self.minVisible - Date().timeIntervalSince(shownAt))
            releaseAfter(wait)
        }
    }

    func stop() {
        run += 1
        visible = false
    }

    private func tick(_ mine: Int) async {
        while mine == run && visible {
            advance()
            try? await Task.sleep(nanoseconds: 100_000_000)
        }
    }

    private func advance() {
        let now = Date()
        let floor = shown == 0 ? Self.firstStepMin : Self.stepMin
        let count = Self.labels.count
        if shown < count && now.timeIntervalSince(since) >= floor {
            // Audio existing means every step before it happened, whatever
            // the poll managed to see.
            let finished = shown < count - 1 ? (server[shown] || audioReady) : audioReady
            if finished {
                shown += 1
                since = now
            }
        }
        if shown >= count && audioReady && !releasing {
            releaseAfter(Self.lastBeat)
        }
    }

    private func releaseAfter(_ seconds: TimeInterval) {
        guard !releasing else { return }
        releasing = true
        let mine = run
        Task {
            try? await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
            guard mine == self.run else { return }
            self.visible = false
            self.onRelease?()
        }
    }

    private func pollLoop(_ mine: Int) async {
        while mine == run && visible && !audioReady {
            if let answer = await ask?() {
                for (i, done) in answer.steps.marks.enumerated() where done {
                    server[i] = true
                }
            }
            let wait = server[0] ? Self.poll : Self.firstPoll
            try? await Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000))
        }
    }
}

/// The screen itself (`.fam-loading`): the app's own background with a
/// copper wash, the FAM letters, three chevrons rising in turn,
/// "FAMiliarizing…", the five steps, and an X that cancels the episode.
struct LoadingView: View {
    @ObservedObject var loading: LoadingModel
    let cancel: () -> Void
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ZStack(alignment: .topTrailing) {
            background
            VStack(spacing: 22) {
                FAMLetters(capHeight: 22)
                    .opacity(0.92)
                RisingChevrons(still: reduceMotion)
                    .frame(width: 96, height: 88)
                    .padding(.top, 4)
                Text("FAMiliarizing…")
                    .font(FAMFont.heading(21))
                    .foregroundStyle(FAMColor.copper)
                    .tracking(0.2)
                    // Bricolage has no italic: the web's browser slants it, so do we.
                    .transformEffect(CGAffineTransform(a: 1, b: 0, c: -0.18, d: 1, tx: 3, ty: 0))
                steps
                    .padding(.top, 2)
            }
            .padding(.horizontal, 32)
            .frame(maxWidth: .infinity, maxHeight: .infinity)

            Button(action: cancel) {
                Image(systemName: "xmark")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundStyle(FAMColor.text)
                    .frame(width: 34, height: 34)
                    .background(Color.black.opacity(0.06), in: Circle())
                    .overlay(Circle().stroke(FAMColor.borderStrong))
            }
            .padding(.top, 18)
            .padding(.trailing, 18)
            .accessibilityLabel("Cancel this episode")
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("What FAM is doing")
    }

    private var background: some View {
        ZStack {
            RadialGradient(colors: [FAMColor.background, FAMColor.page],
                           center: .center, startRadius: 120, endRadius: 520)
            RadialGradient(colors: [Color(red: 224 / 255, green: 181 / 255, blue: 99 / 255).opacity(0.14), .clear],
                           center: UnitPoint(x: 0.5, y: 0.42), startRadius: 0, endRadius: 230)
        }
        .ignoresSafeArea()
    }

    private var steps: some View {
        VStack(alignment: .leading, spacing: 13) {
            ForEach(Array(LoadingModel.labels.enumerated()), id: \.offset) { index, label in
                StepRow(label: label,
                        done: index < loading.shown,
                        active: index == loading.shown,
                        still: reduceMotion)
            }
        }
    }
}

private struct StepRow: View {
    let label: String
    let done: Bool
    let active: Bool
    let still: Bool
    @State private var pulse = false

    var body: some View {
        HStack(spacing: 12) {
            ZStack {
                Circle()
                    .fill(done ? FAMColor.copper : Color.clear)
                Circle()
                    .stroke(done || active ? FAMColor.copper : color, lineWidth: 1.6)
                Image(systemName: "checkmark")
                    .font(.system(size: 10, weight: .heavy))
                    .foregroundStyle(FAMColor.ink)
                    .opacity(done ? 1 : 0)
                    .scaleEffect(done ? 1 : 0.4)
            }
            .frame(width: 20, height: 20)
            .shadow(color: FAMColor.copper.opacity(active && pulse ? 0.45 : 0), radius: active ? 5 : 0)
            Text(label)
                .font(FAMFont.mono(12))
                .tracking(0.24)
                .foregroundStyle(color)
        }
        .opacity(done || active ? 1 : 0.55)
        .animation(.easeOut(duration: 0.3), value: done)
        .onAppear {
            guard !still else { return }
            withAnimation(.easeInOut(duration: 0.8).repeatForever(autoreverses: true)) { pulse = true }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(label + (done ? ", done" : active ? ", in progress" : ""))
    }

    private var color: Color {
        done ? FAMColor.copper : active ? FAMColor.text : FAMColor.textMuted
    }
}

/// Three chevrons, each rising into place, holding, and fading up in turn
/// (`famRise`: 2.4 s, 0 / 0.22 / 0.44 s apart).
private struct RisingChevrons: View {
    let still: Bool

    private static let paths: [Path] = [CGFloat(38), 26, 14].map { y in
        var p = Path()
        p.move(to: CGPoint(x: 6, y: y))
        p.addLine(to: CGPoint(x: 24, y: y - 16))
        p.addLine(to: CGPoint(x: 42, y: y))
        return p
    }

    var body: some View {
        TimelineView(.animation(paused: still)) { context in
            let t = context.date.timeIntervalSinceReferenceDate
            ZStack {
                ForEach(0..<3, id: \.self) { i in
                    let motion = still ? (1.0, 0.0) : Self.rise(t - Double(i) * 0.22)
                    Glyph(path: Self.paths[i], box: CGRect(x: 0, y: 0, width: 48, height: 44))
                        .stroke(FAMColor.copper, style: StrokeStyle(lineWidth: 3.4 * 96 / 48,
                                                                     lineCap: .round, lineJoin: .round))
                        .shadow(color: Color(red: 224 / 255, green: 181 / 255, blue: 99 / 255).opacity(0.55),
                                radius: 9)
                        .opacity(motion.0)
                        .offset(y: motion.1)
                }
            }
        }
        .accessibilityHidden(true)
    }

    /// The keyframes: 0% hidden 11 below, 20% in place, 62% in place,
    /// 100% hidden 11 above.
    static func rise(_ time: Double) -> (Double, Double) {
        let period = 2.4
        let f = (time.truncatingRemainder(dividingBy: period) + period)
            .truncatingRemainder(dividingBy: period) / period
        if f < 0.2 {
            let k = ease(f / 0.2)
            return (k, 11 * (1 - k))
        } else if f < 0.62 {
            return (1, 0)
        } else {
            let k = ease((f - 0.62) / 0.38)
            return (1 - k, -11 * k)
        }
    }

    private static func ease(_ x: Double) -> Double {
        x < 0.5 ? 2 * x * x : 1 - pow(-2 * x + 2, 2) / 2
    }
}
