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
/// has nothing to write: all five show done and its audio starts at once,
/// the screen staying up 0.45 s so it never reads as a flash. The numbers
/// are the web's own.
///
/// It gives up as the web does (§497, `holdGenTimeout`): 45 s of silence
/// from the server, started again by every step it checks off; once the
/// script is with the voice, the voice's own `voice_wait` plus 15 s; and
/// for an episode being made where the poll cannot see it (`known: false`),
/// 45 s plus that wait from the tap. The dial and the glide poll too, only
/// to move that time.
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
    static let quiet: TimeInterval = 45             // GEN_QUIET_MS
    static let voiceMargin: TimeInterval = 15       // GEN_VOICE_MARGIN_MS
    static let serverSilent = "That is taking longer than expected. Is the server running?"
    static let voiceSlow = "The voice is taking longer than expected to start. Try again in a moment."

    @Published private(set) var visible = false
    /// How many steps are checked off (0-5).
    @Published private(set) var shown = 0
    /// A mix's episode: the glide along the mix (§509) instead of the
    /// five steps. It holds nothing - audio starts the moment it arrives,
    /// after the 0.45 s minimum.
    @Published private(set) var glide: Glide?
    /// A DailyFAM tile: the tuning dial (§509, `paintLoadingVariant("dial")`)
    /// instead of the five steps. Like the glide it holds nothing.
    @Published private(set) var dial: Dial?

    struct Dial: Hashable {
        /// The rail's heading, one line ("" when the tile is on no rail).
        var heading: String
        /// Up to four neighbouring tags and the tile's own, at `at`.
        var labels: [String]
        var at: Int
        /// "Money & markets": the tile's facet in full (`dialWord`).
        var tune: String
        var title: String
    }

    /// The dial and the glide have no steps and hold no audio.
    private var free: Bool { glide != nil || dial != nil }

    struct Glide: Equatable {
        var mixName: String
        /// Titles in playlist order, " · today" left off.
        var titles: [String]
        var pos: Int
        /// Positions before `pos` whose audio arrived ("Played"); the
        /// rest were skipped.
        var played: Set<Int>
        var first: Bool
        /// Each item's picture, in playlist order (`picture`, the player's
        /// own, §545): the card shows it above the topic's name (§588).
        var pictures: [URL?] = []
    }

    /// Start the held audio and open the player.
    var onRelease: (() -> Void)?
    /// A replay, the dial and the glide: start the audio now, while the
    /// screen stays up for the rest of its 0.45 s (`genStepsGate`'s
    /// `begin()` with `finishGenOverlay`). The player opens at `onRelease`.
    var onAudioNow: (() -> Void)?
    /// Given up (§497): the sentence to say. The screen is already down.
    var onGiveUp: ((String) -> Void)?

    /// What this server says under the steps and in place of the dial's
    /// and the glide's status (`genStatusNote`, from `/api/health`): the
    /// sample script on staging or with no key, or a rejected key. Empty
    /// on a server that writes real episodes.
    @Published var note = ""

    private var server = [false, false, false, false]
    private var audioReady = false
    private var releasing = false
    private var since = Date()
    private var shownAt = Date()
    private var run = 0
    private var ask: (() async -> Progress?)?
    /// The give-up time and the sentence it says (`genDeadline`).
    private var deadline: Date?
    private var deadlineWords = ""
    private var startedAt = Date()
    private var handedAt: Date?

    func start(ask: @escaping () async -> Progress?, glide: Glide? = nil, dial: Dial? = nil) {
        run += 1
        let mine = run
        self.glide = glide
        self.dial = glide == nil ? dial : nil
        self.ask = ask
        server = [false, false, false, false]
        audioReady = false
        releasing = false
        shown = 0
        since = Date()
        shownAt = Date()
        visible = true
        deadline = nil
        handedAt = nil
        startedAt = Date()
        hold(until: startedAt.addingTimeInterval(Self.quiet), words: Self.serverSilent)
        Task { await self.tick(mine) }
        Task { await self.pollLoop(mine) }
        Task { await self.watchDeadline(mine) }
    }

    /// Move the give-up time to `at`, never earlier, and never once the
    /// audio is here (`holdGenTimeout`).
    private func hold(until at: Date, words: String) {
        if audioReady { return }
        if let current = deadline, at <= current { return }
        deadline = at
        deadlineWords = words
    }

    /// Gives up at the deadline: the screen goes and the sentence is said.
    private func watchDeadline(_ mine: Int) async {
        while mine == run && visible && !audioReady {
            if let at = deadline, Date() >= at {
                let words = deadlineWords
                stop()
                onGiveUp?(words)
                return
            }
            try? await Task.sleep(nanoseconds: 250_000_000)
        }
    }

    /// The audio has arrived and is held.
    func ready(cacheHit: Bool) {
        guard visible else { onRelease?(); return }
        audioReady = true
        deadline = nil
        if cacheHit || free {
            // A replay: all five done and the audio started now; only the
            // screen stays, long enough to be seen (`finishGenOverlay`).
            shown = Self.labels.count
            onAudioNow?()
            let wait = max(0, Self.minVisible - Date().timeIntervalSince(shownAt))
            releaseAfter(wait)
        }
    }

    func stop() {
        run += 1
        visible = false
    }

    private func tick(_ mine: Int) async {
        while mine == run && visible && !free {
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
        // The dial and the glide draw nothing from the marks, but they are
        // still read: they are what moves the give-up time (§497).
        while mine == run && visible && !audioReady {
            if let answer = await ask?(), mine == run, !audioReady {
                heard(answer)
            }
            let wait = server[0] ? Self.poll : Self.firstPoll
            try? await Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000))
        }
    }

    /// One answer from `/api/progress` (`pollGenSteps`): the marks, and the
    /// give-up time moved for a server that is working.
    private func heard(_ answer: Progress) {
        var moved = false
        for (i, done) in answer.steps.marks.enumerated() where done && !server[i] {
            server[i] = true
            moved = true
        }
        let now = Date()
        if moved { hold(until: now.addingTimeInterval(Self.quiet), words: Self.serverSilent) }
        let wait = max(0, answer.voiceWait ?? 0)
        if wait > 0 && server[3] {
            // The script is with the voice: as long as the voice may take.
            if handedAt == nil { handedAt = now }
            let from = handedAt ?? now
            hold(until: from.addingTimeInterval(wait + Self.voiceMargin), words: Self.voiceSlow)
        } else if wait > 0 && answer.known == false {
            // Alive, but making the episode where this poll cannot see.
            hold(until: startedAt.addingTimeInterval(Self.quiet + wait), words: Self.voiceSlow)
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
            if let glide = loading.glide {
                // The FAM mark above the glide and the dial, larger than
                // search's (§542, the web's `.fam-loading.alt .fam-loading-wm`).
                VStack(spacing: 22) {
                    FAMLetters(capHeight: 40).opacity(0.92).padding(.bottom, 10)
                    GlideView(glide: glide, still: reduceMotion, note: loading.note)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let dial = loading.dial {
                VStack(spacing: 22) {
                    FAMLetters(capHeight: 40).opacity(0.92).padding(.bottom, 10)
                    DialView(dial: dial, still: reduceMotion, note: loading.note)
                }
                .padding(.horizontal, 32)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
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
                // `#famLoadingStatus` (`.gen-text`): what this server cannot
                // do - the sample script, a rejected key - where attention
                // is. Nothing at all on a server that writes (§232).
                if !loading.note.isEmpty {
                    Text(loading.note)
                        .font(FAMFont.mono(11))
                        .foregroundStyle(FAMColor.textMuted)
                        .lineSpacing(5)
                        .multilineTextAlignment(.center)
                        .frame(maxWidth: 250)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(.horizontal, 32)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            }

            Button(action: cancel) {
                Image(systemName: "xmark")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundStyle(FAMColor.text)
                    .frame(width: 34, height: 34)
                    // `.fam-loading-x`'s rgba(0,0,0,.18) in dark; light keeps
                    // its own lighter wash.
                    .background(Color(light: 0x000000, lightAlpha: 0.06, dark: 0x000000, darkAlpha: 0.18),
                                in: Circle())
                    .overlay(Circle().stroke(FAMColor.borderStrong))
            }
            .buttonStyle(.press)
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
                // The tick pops in: .25 s fade, .3 s overshooting scale
                // (`.fam-step-dot svg`).
                Image(systemName: "checkmark")
                    .font(.system(size: 10, weight: .heavy))
                    .foregroundStyle(FAMColor.ink)
                    .opacity(done ? 1 : 0)
                    .animation(.easeInOut(duration: 0.25), value: done)
                    .scaleEffect(done ? 1 : 0.4)
                    .animation(.timingCurve(0.3, 1.6, 0.5, 1, duration: 0.3), value: done)
            }
            .frame(width: 20, height: 20)
            // The dot fills over .3 s (`.fam-step-dot`).
            .animation(.easeInOut(duration: 0.3), value: done)
            .animation(.easeInOut(duration: 0.3), value: active)
            .shadow(color: FAMColor.copper.opacity(active && pulse ? 0.45 : 0), radius: active ? 5 : 0)
            Text(label)
                .font(FAMFont.mono(12))
                .tracking(0.24)
                .foregroundStyle(color)
        }
        .opacity(done || active ? 1 : 0.55)
        // The words' colour and strength change over .35 s (`.fam-steps li`).
        .animation(.easeInOut(duration: 0.35), value: done)
        .animation(.easeInOut(duration: 0.35), value: active)
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

/// The glide (§509, `paintLoadingVariant("glide")`): the mix's episodes as a
/// strip of cards, sliding so the one being fetched is centred and lifted;
/// earlier ones say Played or Skipped; dots under it for 2-12 items.
struct GlideView: View {
    let glide: LoadingModel.Glide
    let still: Bool
    /// `genStatusNote`, in place of the status when the server has one.
    var note: String = ""

    @State private var arrived = false

    private let cardWidth: CGFloat = 128
    private let gap: CGFloat = 10

    var body: some View {
        VStack(spacing: 14) {
            kicker
            GeometryReader { geo in
                let step = cardWidth + gap
                let centre = geo.size.width / 2 - cardWidth / 2 - CGFloat(glide.pos) * step
                let from = glide.pos == 0 ? centre + 80 : centre + step
                HStack(spacing: gap) {
                    ForEach(Array(glide.titles.enumerated()), id: \.offset) { i, title in
                        card(i, title)
                    }
                }
                .padding(.vertical, 10)
                .offset(x: arrived || still ? centre : from)
            }
            .frame(height: 194)
            if glide.titles.count >= 2 && glide.titles.count <= 12 { dots }
            Text(glide.titles.indices.contains(glide.pos) ? glide.titles[glide.pos] : "")
                .font(FAMFont.heading(19, .semibold))
                .foregroundStyle(FAMColor.text)
                .multilineTextAlignment(.center)
                .lineSpacing(4)
                .frame(maxWidth: 300)
                .modifier(LoadingTitleUp(still: still))
                .padding(.horizontal, 32)
            status
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .onAppear {
            guard !still else { return }
            withAnimation(.spring(response: 0.45, dampingFraction: 0.78).delay(0.1)) { arrived = true }
        }
        .id(glide.pos)
        .accessibilityElement(children: .combine)
    }

    /// "myFAM · {mix} · 2 of 4", the mix's name in copper (`.fam-ld-kick`).
    private var kicker: some View {
        let count = glide.titles.count > 1 ? " · \(glide.pos + 1) of \(glide.titles.count)" : ""
        var line = Text("myFAM")
        if !glide.mixName.isEmpty {
            line = line + Text(" · ") + Text(glide.mixName).foregroundColor(FAMColor.copper)
        }
        line = line + Text(count)
        return line
            .font(FAMFont.mono(10.5))
            .tracking(1.05)
            .foregroundStyle(FAMColor.textMuted)
            .textCase(.uppercase)
            .lineLimit(1)
            .truncationMode(.tail)
            .padding(.horizontal, 32)
    }

    /// "Getting your episode ready", after a pulsing copper dot; "the next"
    /// past the playlist's first (`glide.pos > 0`); the server's note instead
    /// when it has one.
    private var status: some View {
        let words = glide.pos > 0 ? "Getting the next episode ready" : "Getting your episode ready"
        return HStack(spacing: 8) {
            PulsingDot(still: still)
            Text(note.isEmpty ? words : note)
                .font(FAMFont.mono(11.5))
                .tracking(0.345)
                .foregroundStyle(FAMColor.textMuted)
        }
        .padding(.horizontal, 32)
    }

    /// Number at the top (copper on the current card; "Played" or
    /// "Skipped" on the ones before), title at the bottom.
    private func card(_ i: Int, _ title: String) -> some View {
        let current = i == glide.pos
        let before = i < glide.pos
        let played = glide.played.contains(i)
        let label: String = before ? "\(i + 1) · \(played ? "Played" : "Skipped")" : "\(i + 1)"
        let numberColor: Color = current ? FAMColor.copper : ((before && played) ? FAMColor.textMuted : FAMColor.textFaint)
        // The episode's picture, 4:3 like every tile, over the topic's name
        // - never the episode's title (§588).
        let side = cardWidth - 12
        let url: URL? = glide.pictures.indices.contains(i) ? glide.pictures[i] : nil
        return VStack(alignment: .leading, spacing: 5) {
            ZStack {
                FAMColor.surface2
                if let url {
                    AsyncImage(url: url) { $0.resizable().scaledToFill() } placeholder: { Color.clear }
                }
            }
            .frame(width: side, height: side * 0.75)
            .clipShape(RoundedRectangle(cornerRadius: 8))
            Text(label)
                .font(FAMFont.mono(9.5))
                .tracking(0.76)
                .foregroundStyle(numberColor)
                .padding(.horizontal, 4)
            Text(title)
                .font(FAMFont.body(12))
                .foregroundStyle(current ? FAMColor.text : FAMColor.textMuted)
                .lineSpacing(3)
                .lineLimit(2)
                .padding(.horizontal, 4)
        }
        .padding(EdgeInsets(top: 6, leading: 6, bottom: 9, trailing: 6))
        .frame(width: cardWidth, height: 168, alignment: .topLeading)
        .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(current ? FAMColor.copper : FAMColor.border,
                                                          lineWidth: 1))
        .opacity(current ? 1 : 0.4)
        .scaleEffect(current && (arrived || still) ? 1.08 : 1)
        .animation(still ? nil : .spring(response: 0.4, dampingFraction: 0.6).delay(0.25), value: arrived)
    }

    private func dotWidth(_ i: Int) -> CGFloat {
        guard i == glide.pos else { return 6 }
        return (arrived || still) ? 18 : 6
    }

    private var dots: some View {
        HStack(spacing: 6) {
            ForEach(0..<glide.titles.count, id: \.self) { i in
                Capsule()
                    .fill(i == glide.pos ? FAMColor.copper : (i < glide.pos ? FAMColor.textFaint : FAMColor.borderStrong))
                    .frame(width: dotWidth(i), height: 6)
                    // The current dot grows to 18 (`famGlideDot`, .4 s after .25 s).
                    .animation(still ? nil : .easeInOut(duration: 0.4).delay(0.25), value: arrived)
            }
        }
    }
}

/// The dial's and the glide's title coming up into place (`famLdUp`: from
/// 8 pt below and clear, .3 s ease); still with reduced motion.
private struct LoadingTitleUp: ViewModifier {
    let still: Bool
    @State private var up = false

    func body(content: Content) -> some View {
        let shown = still || up
        return content
            .opacity(shown ? 1 : 0)
            .offset(y: shown ? 0 : 8)
            .onAppear {
                guard !still else { return }
                withAnimation(.easeInOut(duration: 0.3)) { up = true }
            }
    }
}

/// The 7 pt copper dot before the glide's status, pulsing 0.3 to 1 over
/// 1.2 s (`.fam-ld-status::before`); still with reduced motion.
private struct PulsingDot: View {
    let still: Bool
    @State private var bright = false

    var body: some View {
        Circle()
            .fill(FAMColor.copper)
            .frame(width: 7, height: 7)
            .opacity(still ? 1 : (bright ? 1 : 0.3))
            .onAppear {
                guard !still else { return }
                withAnimation(.easeInOut(duration: 0.6).repeatForever(autoreverses: true)) { bright = true }
            }
            .accessibilityHidden(true)
    }
}

/// The tuning dial (§509, option 5, `paintLoadingVariant("dial")` and CSS
/// `.fam-dial*`): "DAILYFAM · {rail}", a 280 x 88 dial whose needle sweeps
/// the rail's tags, overshoots and settles on the tile's own, which locks
/// copper at 1.05 s while the static under it quietens; then "Tuning in to
/// {facet}", the title and "Getting your episode ready". The screen goes
/// the moment audio arrives, wherever the needle has got to.
struct DialView: View {
    let dial: LoadingModel.Dial
    let still: Bool
    /// `genStatusNote`, in place of the status when the server has one.
    var note: String = ""

    @State private var shownAt = Date()

    private static let width: CGFloat = 280
    private static let height: CGFloat = 88

    var body: some View {
        VStack(spacing: 14) {
            kicker
            TimelineView(.animation(paused: still)) { context in
                let t = still ? 10 : context.date.timeIntervalSince(shownAt)
                face(t)
            }
            .frame(maxWidth: Self.width)
            .frame(height: Self.height)
            Text("Tuning in to " + dial.tune)
                .font(FAMFont.mono(11))
                .foregroundStyle(FAMColor.textMuted)
                .multilineTextAlignment(.center)
            Text(dial.title)
                .font(FAMFont.heading(19, .semibold))
                .foregroundStyle(FAMColor.text)
                .multilineTextAlignment(.center)
                .lineSpacing(4)
                .frame(maxWidth: 300)
                .modifier(LoadingTitleUp(still: still))
            HStack(spacing: 8) {
                PulsingDot(still: still)
                Text(note.isEmpty ? "Getting your episode ready" : note)
                    .font(FAMFont.mono(11.5))
                    .tracking(0.345)
                    .foregroundStyle(FAMColor.textMuted)
            }
        }
        .onAppear { shownAt = Date() }
        .id(dial)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Tuning in to \(dial.tune). \(dial.title). " + (note.isEmpty ? "Getting your episode ready" : note))
    }

    /// "DAILYFAM · {heading}", the heading in copper (`.fam-ld-kick`).
    private var kicker: some View {
        var line = Text("DailyFAM")
        if !dial.heading.isEmpty {
            line = line + Text(" \u{00B7} ") + Text(dial.heading).foregroundColor(FAMColor.copper)
        }
        return line
            .font(FAMFont.mono(10.5))
            .tracking(1.05)
            .foregroundStyle(FAMColor.textMuted)
            .textCase(.uppercase)
            .lineLimit(1)
            .truncationMode(.tail)
    }

    private func face(_ t: Double) -> some View {
        let count = max(1, dial.labels.count)
        let stop = (Double(dial.at) + 0.5) / Double(count)
        let needle = Self.needle(t, stop: stop)
        let locked = t >= 1.05
        let quiet = Self.quiet(t)
        return GeometryReader { geo in
            let w = geo.size.width
            ZStack(alignment: .topLeading) {
                ticks
                    .frame(width: w - 24, height: 24)
                    .offset(x: 12, y: 14)
                labels(locked: locked)
                    .frame(width: w - 12)
                    .offset(x: 6, y: 46)
                noise(t)
                    .frame(width: w - 24, height: 12)
                    .opacity(quiet)
                    .offset(x: 12, y: Self.height - 9 - 12)
                RoundedRectangle(cornerRadius: 1)
                    .fill(FAMColor.deeper)
                    .frame(width: 2, height: Self.height - 14)
                    .shadow(color: FAMColor.deeper, radius: 5)
                    .offset(x: w * needle - 1, y: 7)
            }
            .frame(width: w, height: Self.height, alignment: .topLeading)
        }
        .background(FAMColor.surface)
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(FAMColor.borderStrong))
    }

    /// Minor ticks every 8 pt (11 tall, faint), majors every 40 (24 tall),
    /// standing on one line, at 70%.
    private var ticks: some View {
        Canvas { context, size in
            var minor = Path()
            var x: CGFloat = 0
            while x < size.width {
                minor.addRect(CGRect(x: x, y: size.height - 11, width: 1, height: 11))
                x += 8
            }
            var major = Path()
            x = 0
            while x < size.width {
                major.addRect(CGRect(x: x, y: 0, width: 1, height: size.height))
                x += 40
            }
            context.fill(minor, with: .color(FAMColor.textFaint))
            context.fill(major, with: .color(FAMColor.textMuted))
        }
        .opacity(0.7)
    }

    private func labels(locked: Bool) -> some View {
        HStack(spacing: 0) {
            ForEach(Array(dial.labels.enumerated()), id: \.offset) { i, word in
                let on = i == dial.at
                Text(word)
                    .font(FAMFont.mono(8.5, bold: on))
                    .tracking(0.425)
                    .textCase(.uppercase)
                    .foregroundStyle(on && locked ? FAMColor.copper : FAMColor.textFaint)
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .padding(.horizontal, 2)
                    .frame(maxWidth: .infinity)
            }
        }
    }

    /// Thirty-six bars of static, each flickering on its own beat.
    private func noise(_ t: Double) -> some View {
        HStack(alignment: .bottom, spacing: 2) {
            ForEach(0..<36, id: \.self) { i in
                let h = Double(30 + (i * 37) % 70) / 100
                let delay = Double(i) * 0.137
                let phase = Int(((t + delay) / 0.09).rounded(.down)) % 2
                let k = still ? 1.0 : (phase == 0 ? 0.3 : 1.0)
                Rectangle()
                    .fill(FAMColor.textMuted.opacity(0.5))
                    .frame(maxWidth: .infinity)
                    .frame(height: 12 * h * k)
            }
        }
        .frame(maxHeight: .infinity, alignment: .bottom)
    }

    /// The static fades to .12 from 1.0 s to 1.4 s (`famDialQuiet`).
    static func quiet(_ t: Double) -> Double {
        if t <= 1.0 { return 1 }
        if t >= 1.4 { return 0.12 }
        return 1 - (t - 1.0) / 0.4 * 0.88
    }

    /// Where the needle is, as a fraction of the dial (`famDialSweep`: 6%,
    /// 90% at 55%, the overshoot back at 78%, the stop at 1.1 s, each leg
    /// eased by cubic-bezier(.5,0,.3,1); then `famDialHold`'s 1 pt sway).
    static func needle(_ t: Double, stop: Double) -> CGFloat {
        let stop2 = max(0.03, stop - 0.06)
        let f = t / 1.1
        if f >= 1 {
            let sway = (1 - cos((t - 1.15) / 1.6 * 2 * .pi)) / 2
            return CGFloat(stop) + CGFloat(t > 1.15 ? sway : 0) / width
        }
        let keys: [(Double, Double)] = [(0, 0.06), (0.55, 0.90), (0.78, stop2), (1, stop)]
        var i = 0
        while i < keys.count - 2 && f > keys[i + 1].0 { i += 1 }
        let a = keys[i]
        let b = keys[i + 1]
        let local = (f - a.0) / (b.0 - a.0)
        let eased = bezier(local, 0.5, 0, 0.3, 1)
        return CGFloat(a.1 + (b.1 - a.1) * eased)
    }

    /// CSS cubic-bezier(x1, y1, x2, y2) at progress `x`.
    static func bezier(_ x: Double, _ x1: Double, _ y1: Double, _ x2: Double, _ y2: Double) -> Double {
        if x <= 0 { return 0 }
        if x >= 1 { return 1 }
        func curve(_ s: Double, _ p1: Double, _ p2: Double) -> Double {
            let u = 1 - s
            return 3 * u * u * s * p1 + 3 * u * s * s * p2 + s * s * s
        }
        var lo = 0.0
        var hi = 1.0
        var s = x
        for _ in 0..<24 {
            s = (lo + hi) / 2
            if curve(s, x1, x2) < x { lo = s } else { hi = s }
        }
        return curve(s, y1, y2)
    }
}
