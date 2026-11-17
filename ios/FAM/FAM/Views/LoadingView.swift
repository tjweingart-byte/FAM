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
    /// A replay painted all five done at once: no five ticks in a row
    /// (`.fam-steps.instant`).
    @Published private(set) var instant = false
    /// The five steps' screen gave way to the player (not a cancel, not the
    /// glide or the dial): it leaves by the iris (§643 `loader-to-player` C).
    private(set) var handedOff = false
    /// A mix's episode: the glide along the mix (§509) instead of the
    /// five steps. It holds nothing - audio starts the moment it arrives,
    /// after the 0.45 s minimum.
    @Published private(set) var glide: Glide?
    /// A DailyFAM tile's wait (§592, `paintLoadingVariant("daily")`):
    /// its picture developing, or friends' faces gathering on it, instead
    /// of the five steps. Like the glide it holds nothing. (Still called
    /// `dial` in the plumbing: §509's tuning dial was the first to use it.)
    @Published private(set) var dial: Dial?

    struct Dial: Hashable {
        /// The rail's heading, one line ("" when the tile is on no rail).
        var heading: String
        /// The tile's picture (`thumb`); nil draws the plain surface.
        var picture: URL?
        var title: String
        /// Up to three friends who listened; none develops the picture.
        var faces: [FriendFace] = []
        /// "Maya, Jordan and 4 others listened" (`friendsListenedLine`).
        var friendsLine: String = ""
        /// The tile's episode is already written (`cached`): its picture is
        /// sharp in 420 ms and its friends are drawn at rest (round three,
        /// §641 `picture-develop` B, `friends-gather` C).
        var ready: Bool = false
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
        /// own, §545): the card shows it above the topic's name (§591).
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

    /// Opened on the press (§643's loader-arrives B): the five steps are on
    /// screen while the speller's wait runs under step one - which is what
    /// the speller is doing. Nothing is asked yet; `start` takes the open
    /// screen over, and the first step's 0.5 s still counts from the
    /// request. A cancel meanwhile (`stop`) leaves nothing to send.
    func openEarly() {
        run += 1
        let mine = run
        glide = nil
        dial = nil
        ask = nil
        server = [false, false, false, false]
        audioReady = false
        releasing = false
        shown = 0
        instant = false
        handedOff = false
        since = Date()
        shownAt = Date()
        visible = true
        early = true
        deadline = nil
        handedAt = nil
        startedAt = Date()
        hold(until: startedAt.addingTimeInterval(Self.quiet), words: Self.serverSilent)
        Task { await self.watchDeadline(mine) }
    }

    /// Opened on the press and not yet taken over by a request.
    private(set) var early = false

    /// An early screen no request took over (the send was dropped): gone.
    func closeEarly() {
        guard early, visible else { return }
        stop()
    }

    func start(ask: @escaping () async -> Progress?, glide: Glide? = nil, dial: Dial? = nil) {
        // A search's screen already up since the press keeps when it came.
        let adopted = early && visible && glide == nil && dial == nil
        early = false
        run += 1
        let mine = run
        self.glide = glide
        self.dial = glide == nil ? dial : nil
        self.ask = ask
        server = [false, false, false, false]
        audioReady = false
        releasing = false
        shown = 0
        instant = false
        handedOff = false
        since = Date()
        if !adopted { shownAt = Date() }
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
            instant = shown < Self.labels.count - 1
            shown = Self.labels.count
            onAudioNow?()
            let wait = max(0, Self.minVisible - Date().timeIntervalSince(shownAt))
            releaseAfter(wait)
        }
    }

    func stop() {
        run += 1
        early = false
        handedOff = false
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
            self.handedOff = !self.free
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
                    DailyLoadingView(dial: dial, still: reduceMotion, note: loading.note)
                }
                .padding(.horizontal, 32)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
            VStack(spacing: 22) {
                FAMLetters(capHeight: 22)
                    .opacity(0.92)
                RisingChevrons(still: reduceMotion)
                    .frame(width: 96, height: 88)
                    // Where the iris opens from when the player takes over.
                    .background(GeometryReader { geo in
                        let frame = geo.frame(in: .global)
                        Color.clear
                            .onAppear { PlayerMotion.shared.chevrons = frame }
                            .onChange(of: frame) { _, now in PlayerMotion.shared.chevrons = now }
                    })
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
                StepRow(index: index,
                        label: label,
                        done: index < loading.shown,
                        active: index == loading.shown,
                        still: reduceMotion || loading.instant,
                        reduced: reduceMotion)
                    // The rows rise in as the screen arrives (round three,
                    // §643 `loader-arrives` B): 8 pt, 240 ms, 40 ms apart.
                    .modifier(RowRise(delay: Double(index) * 0.04, still: reduceMotion))
            }
        }
    }
}

/// A step row rising into place as the loader arrives.
private struct RowRise: ViewModifier {
    let delay: Double
    let still: Bool
    @State private var up = false

    func body(content: Content) -> some View {
        let shown = still || up
        return content
            .opacity(shown ? 1 : 0)
            .offset(y: shown ? 0 : 8)
            .onAppear {
                guard !still else { return }
                withAnimation(Motion.easeOut(0.24).delay(delay)) { up = true }
            }
    }
}

private struct StepRow: View {
    let index: Int
    let label: String
    let done: Bool
    let active: Bool
    /// No movement: Reduce Motion, or a replay painting all five at once.
    let still: Bool
    /// Reduce Motion itself: the ring pulses instead of growing.
    let reduced: Bool
    /// When this row became the active one, for the ring's hold-off.
    @State private var activeSince = Date()

    /// The web's tick (`M5 12.5l4.5 4.5L19 7.5`, a 24 box).
    private static let tick: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 5, y: 12.5))
        p.addLine(to: CGPoint(x: 9.5, y: 17))
        p.addLine(to: CGPoint(x: 19, y: 7.5))
        return p
    }()

    var body: some View {
        HStack(spacing: 12) {
            ZStack {
                Circle()
                    .fill(done ? FAMColor.copper : Color.clear)
                Circle()
                    .stroke(done || active ? FAMColor.copper : color, lineWidth: 1.6)
                    // A later step's circle lights once the thread above
                    // it has filled (120 + 260 ms).
                    .animation(still ? nil : Motion.easeOut(Motion.Dur.colour)
                                   .delay(active && index > 0 ? 0.38 : 0), value: active)
                // The tick draws itself (220 ms after 80 ms, `--ease-out`).
                Glyph(path: Self.tick, box: CGRect(x: 0, y: 0, width: 24, height: 24))
                    .trim(from: 0, to: done ? 1 : 0)
                    .stroke(FAMColor.ink, style: StrokeStyle(lineWidth: 3.2 * 12 / 24,
                                                             lineCap: .round, lineJoin: .round))
                    .frame(width: 12, height: 12)
                    .animation(still ? nil : Motion.easeOut(Motion.Dur.tick).delay(Motion.Dur.tickAt), value: done)
                if active { ring }
            }
            .frame(width: 20, height: 20)
            // The dot fills over 150 ms.
            .animation(still ? nil : Motion.easeOut(Motion.Dur.colour), value: done)
            .overlay(alignment: .top) {
                // The thread down to the next circle fills from the top
                // (260 ms on `--ease-in-out`, after 120 ms).
                if index < LoadingModel.labels.count - 1 {
                    ZStack(alignment: .top) {
                        Rectangle().fill(FAMColor.borderStrong)
                        Rectangle().fill(FAMColor.copper)
                            .scaleEffect(x: 1, y: done ? 1 : 0, anchor: .top)
                            .animation(still ? nil : Motion.easeInOut(0.26).delay(0.12), value: done)
                    }
                    .frame(width: 2, height: 13)
                    .offset(y: 20)
                    .accessibilityHidden(true)
                }
            }
            Text(label)
                .font(FAMFont.mono(12))
                .tracking(0.24)
                .foregroundStyle(color)
        }
        .opacity(done || active ? 1 : 0.55)
        // The words' colour and strength change over 150 ms.
        .animation(still ? nil : Motion.easeOut(Motion.Dur.colour), value: done)
        .animation(still ? nil : Motion.easeOut(Motion.Dur.colour), value: active)
        .onChange(of: active) { _, now in if now { activeSince = Date() } }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(label + (done ? ", done" : active ? ", in progress" : ""))
    }

    /// The active circle's heartbeat (§643 `loader-loops` A): once a beat
    /// (2.4 s, the chevrons' clock), 920 ms in - as the chevrons land - a
    /// copper ring grows from the circle to 1.7 and fades over 1.2 s. It
    /// holds off until the circle has lit (380 ms on a later step). Reduce
    /// Motion: it is a loading indicator, so it stays - no growth, its
    /// opacity pulsing .35 to 1 over 1.2 s.
    private var ring: some View {
        TimelineView(.animation) { context in
            let now = context.date
            let t = now.timeIntervalSinceReferenceDate
            let lit = index == 0 || now.timeIntervalSince(activeSince) >= 0.38
            let look = Self.beat(t, reduced: reduced)
            Circle()
                .stroke(FAMColor.copper, lineWidth: 1.6)
                .scaleEffect(look.scale)
                .opacity(lit ? look.opacity : 0)
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }

    static func beat(_ t: Double, reduced: Bool) -> (scale: CGFloat, opacity: Double) {
        if reduced {
            // Alternate .35 → 1 → .35, 1.2 s each way, eased.
            let f = (t.truncatingRemainder(dividingBy: 2.4) + 2.4).truncatingRemainder(dividingBy: 2.4) / 1.2
            let k = f <= 1 ? f : 2 - f
            let eased = 0.5 - 0.5 * cos(Double.pi * k)
            return (1, 0.35 + 0.65 * eased)
        }
        let phase = ((t - 0.92).truncatingRemainder(dividingBy: 2.4) + 2.4).truncatingRemainder(dividingBy: 2.4)
        guard phase < 1.2 else { return (1, 0) }
        let p = phase / 1.2
        let eased = 1 - pow(1 - p, 3)
        return (1 + 0.7 * CGFloat(eased), 0.5 * (1 - eased))
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
                .padding(.horizontal, 32)
            status
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .onAppear {
            guard !still else { return }
            // Round three (§642 `mix-glide` B): the track moves one card
            // over 300 ms on `--ease-in-out`, no overshoot.
            withAnimation(Motion.easeInOut(0.3)) { arrived = true }
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
            // A loading indicator: it keeps pulsing under Reduce Motion.
            PulsingDot(still: false)
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
        // - never the episode's title (§591).
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
        // The light passes on (round three, `mix-glide` B): the card just
        // heard steps down from 1.05 to its rest and dims to .45 (200 ms
        // on `--ease-in`) as the next lifts to 1.05 (200 ms after 160 ms,
        // `--ease-out`). Reduce Motion: the rest pose.
        .opacity(current || (stepping(i) && !(arrived || still)) ? 1 : 0.45)
        .scaleEffect(lift(i) ? 1.05 : 1)
        .animation(still ? nil : (current ? Motion.easeOut(0.2).delay(0.16) : Motion.easeIn(0.2)), value: arrived)
    }

    /// The card heard just before this one, before the hand-off.
    private func stepping(_ i: Int) -> Bool {
        glide.pos > 0 && i == glide.pos - 1 && !glide.first
    }

    private func lift(_ i: Int) -> Bool {
        if i == glide.pos { return arrived || still }
        return stepping(i) && !(arrived || still)
    }

    /// Fixed 6 pt dots, and one 18 pt copper pill that moves one dot along
    /// with the track (round three: no width animates).
    private var dots: some View {
        let pitch: CGFloat = 12
        let at = CGFloat(glide.pos) * pitch - 6
        let from = glide.pos > 0 && !glide.first ? at - pitch : at
        return ZStack(alignment: .leading) {
            HStack(spacing: 6) {
                ForEach(0..<glide.titles.count, id: \.self) { i in
                    Circle()
                        .fill(i < glide.pos ? FAMColor.textFaint : FAMColor.borderStrong)
                        .frame(width: 6, height: 6)
                }
            }
            Capsule()
                .fill(FAMColor.copper)
                .frame(width: 18, height: 6)
                .offset(x: arrived || still ? at : from)
                .animation(still ? nil : Motion.easeInOut(0.3), value: arrived)
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

/// DailyFAM's wait (§592, the owner's pick from the 13 November deck, web
/// `paintLoadingVariant("daily")` and CSS `.fam-dev*`): option 1, the
/// tile's picture coming into focus from a soft grey blur with a light
/// sweeping across it - or, when friends have listened, option 3, the
/// picture with up to three of their faces landing on its corner and a line
/// naming them. Then the title and "Getting your episode ready". The screen
/// goes the moment audio arrives, wherever the animation has got to.
struct DailyLoadingView: View {
    let dial: LoadingModel.Dial
    let still: Bool
    /// `genStatusNote`, in place of the status when the server has one.
    var note: String = ""

    @State private var shown = false

    private static let width: CGFloat = 280
    /// Where each face flies in from (`.fam-dev-faces` --fx / --fy).
    private static let flyFrom: [CGSize] = [CGSize(width: -130, height: -170),
                                             CGSize(width: 40, height: -230),
                                             CGSize(width: 190, height: -130)]

    private var friends: Bool { !dial.faces.isEmpty }
    private var settled: Bool { shown || still || dial.ready }

    var body: some View {
        VStack(spacing: 14) {
            kicker
            picture
                .padding(.bottom, friends ? 18 : 0)
            if friends {
                Text(friendsText)
                    .font(FAMFont.body(13))
                    .foregroundStyle(FAMColor.textMuted)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: Self.width)
                    .opacity(settled ? 1 : 0)
                    .animation(still || dial.ready ? nil : .easeOut(duration: 0.3).delay(0.7), value: shown)
            }
            Text(dial.title)
                .font(FAMFont.heading(19, .semibold))
                .foregroundStyle(FAMColor.text)
                .multilineTextAlignment(.center)
                .lineSpacing(4)
                .frame(maxWidth: 300)
                .modifier(LoadingTitleUp(still: still))
            HStack(spacing: 8) {
                // A loading indicator: it keeps pulsing under Reduce Motion.
                PulsingDot(still: false)
                Text(note.isEmpty ? "Getting your episode ready" : note)
                    .font(FAMFont.mono(11.5))
                    .tracking(0.345)
                    .foregroundStyle(FAMColor.textMuted)
            }
        }
        .onAppear { shown = true }
        .id(dial)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel((friends ? dial.friendsLine + ". " : "") + "\(dial.title). "
                            + (note.isEmpty ? "Getting your episode ready" : note))
    }

    /// The names in the text's own weight, as the web's `<b>`.
    private var friendsText: AttributedString {
        var out = AttributedString(dial.friendsLine)
        for face in dial.faces {
            let name = face.name.isEmpty ? "@" + (face.handle ?? "") : face.name
            if let range = out.range(of: name) {
                out[range].foregroundColor = FAMColor.text
                out[range].font = FAMFont.body(13, .semibold)
            }
        }
        return out
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

    private var picture: some View {
        // Only option 1 develops; with friends the picture is sharp and the
        // faces are the movement.
        let develop = !friends && !still
        let soft = develop && !shown
        return ZStack(alignment: .bottomLeading) {
            ZStack {
                FAMColor.surface2
                if let url = dial.picture {
                    AsyncImage(url: url) { $0.resizable().scaledToFill() } placeholder: { Color.clear }
                }
                if develop && !dial.ready { DevelopSheen() }
            }
            .frame(width: Self.width, height: Self.width * 0.75)
            .clipShape(RoundedRectangle(cornerRadius: 16))
            .blur(radius: soft ? 16 : 0)
            .saturation(soft ? 0 : 1)
            .brightness(soft ? 0.3 : 0)
            // Round three (§641 `picture-develop` B): a written tile is
            // sharp in 420 ms; an unwritten one keeps the slow develop.
            // No scale.
            .animation(develop ? (dial.ready ? Motion.easeOut(0.42)
                                             : .timingCurve(0.2, 0.7, 0.2, 1, duration: 2.4)) : nil,
                       value: shown)
            .shadow(color: .black.opacity(0.22), radius: 14, y: 10)
            if friends { faces.offset(x: 12, y: 18) }
        }
    }

    private var faces: some View {
        HStack(spacing: -10) {
            ForEach(Array(dial.faces.prefix(3).enumerated()), id: \.offset) { i, face in
                let from = Self.flyFrom[min(i, Self.flyFrom.count - 1)]
                Face(name: face.name, handle: face.handle ?? "", avatar: face.avatar ?? "",
                     size: 44, initialsSize: 14)
                    .overlay(Circle().stroke(FAMColor.background, lineWidth: 3))
                    .offset(settled ? .zero : from)
                    .scaleEffect(settled ? 1 : 0.4)
                    .opacity(settled ? 1 : 0)
                    // Unwritten: the flight (600 ms, 160 ms apart); a
                    // written tile's faces are drawn at rest (round three,
                    // §641 `friends-gather` C).
                    .animation(still || dial.ready ? nil
                                   : .timingCurve(0.2, 1.3, 0.4, 1, duration: 0.6).delay(Double(i) * 0.16),
                               value: shown)
            }
        }
    }
}

/// The light sweeping across a developing picture (`famDevSheen`): a soft
/// white band, left to right every 1.4 s, linear (round three).
private struct DevelopSheen: View {
    @State private var across = false

    var body: some View {
        GeometryReader { geo in
            LinearGradient(colors: [.clear, .white.opacity(0.5), .clear],
                           startPoint: .leading, endPoint: .trailing)
                .frame(width: geo.size.width * 0.3)
                .rotationEffect(.degrees(15))
                .offset(x: across ? geo.size.width * 1.2 : -geo.size.width * 0.5)
                .onAppear {
                    withAnimation(.linear(duration: 1.4).repeatForever(autoreverses: false)) {
                        across = true
                    }
                }
        }
        .allowsHitTesting(false)
    }
}
