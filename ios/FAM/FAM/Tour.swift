import Combine
import SwiftUI
import UIKit

// MARK: - The tour (§594, §595)

/// The web's `Tour` (`static/index.html`), step for step: twelve cards over
/// the real screens, a spotlight hole round each real control that glides
/// between them, Skip on every card but the last, Back, Next and a progress
/// count over the steps that apply. Owed from the first run (`owe`) and
/// offered once to an account from before it (`offerOnce`); Settings' "Take
/// the tour" starts it over (`start`). It waits until nothing else is on
/// screen, pauses under any sheet or cover that opens part-way and comes
/// back on the same step. Rule `tour` (`docs/claude/product.md`).
///
/// * **Motion never holds anything** (rule `motion`): the screen under it
///   is switched first, the hole glides to it in 380 ms, the card swaps in
///   220 ms; with Reduce Motion the hole jumps and the card only fades.
/// * **Stops nothing playing**: it moves between tab roots only, where a
///   minimised episode keeps going.
@MainActor
final class TourModel: ObservableObject {
    /// The device's word on it (`localPrefs().tour` on the web): "owed",
    /// "done" or "" - this phone's alone, and cleared by signing out.
    static let storeKey = "fam.tour"
    static let owedMark = "owed"
    static let doneMark = "done"

    /// The web's `STEPS`, in its words and order. Computed on every read,
    /// as the web's "Talk or attach" body is, so it follows whether the mic
    /// is drawn on this phone.
    static var steps: [TourStep] {
        [
            TourStep(screen: .daily, art: .logo, kicker: "Welcome",
                     title: "Welcome to FAM",
                     body: "FAM turns what you care about into short audio stories, so you can keep up without the work. Here\u{2019}s a quick look around. You can skip whenever you like.",
                     next: "Show me around"),
            TourStep(screen: .daily, target: .feed, kicker: "dailyFAM",
                     title: "Today\u{2019}s stories, made for you",
                     body: "Fresh every morning and evening: Made for you, Trending, Most played today and what your friends are hearing. Tap any tile and it plays."),
            TourStep(screen: .daily, target: .circle, isOptional: true, kicker: "dailyFAM",
                     title: "Your friends\u{2019} vibes",
                     body: "A face with a ring has posted a vibe. Tap it to hear what they\u{2019}re listening to, and reply or like it."),
            TourStep(screen: .daily, art: .player, kicker: "The player",
                     title: "Every episode, one player",
                     body: "GO DEEPER asks the next question for you. CC shows captions; share, VIBE! and save sit on the right, and \u{22EF} adds it to your queue. When it ends, four more are waiting."),
            TourStep(screen: .search, target: .searchBar, kicker: "searchFAM",
                     title: "Ask anything",
                     body: "Type a question and FAM researches it and tells you the story, from today\u{2019}s sources. Underneath, Trending searches shows what people are asking right now."),
            TourStep(screen: .search, target: .barButtons, kicker: "searchFAM",
                     title: "Talk or attach",
                     body: (VoiceSearchModel.available ? "Tap the mic to ask out loud, or the clip" : "Tap the clip")
                        + " to add a document, photo or link and hear about it."),
            TourStep(screen: .search, target: .controls, kicker: "searchFAM",
                     title: "Make it yours",
                     body: "Length sets one to five minutes. Voice picks who reads your searches. Private keeps a search out of your history and the library until you turn it off."),
            TourStep(screen: .search, target: .library, kicker: "searchFAM",
                     title: "The episode library",
                     body: "Every episode other listeners have made, A to Z. Most play straight away."),
            TourStep(screen: .myfam, target: .mixTabs, kicker: "myFAM",
                     title: "Your mixes",
                     body: "A mix is the subjects you follow, like a team or a market, with new episodes every morning. Name one with Create new mix, or copy somebody else\u{2019}s. Categories browses today\u{2019}s episodes by subject."),
            TourStep(screen: .messages, target: .messagesTab, kicker: "Messages",
                     title: "Share with friends",
                     body: "Send an episode to a friend, reply to their vibes and start group chats."),
            TourStep(screen: .you, target: .youTab, kicker: "yourFAM",
                     title: "Your profile",
                     body: "Your vibes, your mixes, your friends, and Settings for everything you can change, including this tour."),
            TourStep(screen: .daily, art: .logo, kicker: "Ready",
                     title: "You\u{2019}re all set",
                     body: "Tap a story on dailyFAM, or ask searchFAM anything.",
                     next: "Start listening")
        ]
    }

    /// The overlay is up (it stays up through its own fade out).
    @Published private(set) var visible = false
    /// A sheet or cover opened over it part-way: hidden, not ended
    /// (`.fam-tour.paused`).
    @Published private(set) var paused = false
    /// The step on the card: changed when the card swaps, so the hole and
    /// the words move together, as the web's `place()` runs in `swap`.
    @Published private(set) var cardStep = -1
    @Published private(set) var cardOpacity: Double = 0
    @Published private(set) var cardLift: CGFloat = 0
    /// The shown step's control moved (a tab's nudge, a scroll).
    @Published private(set) var frameTick = 0
    /// The tab the tour wants on screen (`openScreen`): `MainTabs` opens it
    /// as its own bar would. Sent on every ask, the same tab included, and
    /// never replayed to a view that subscribes again.
    let opens = PassthroughSubject<Tab, Never>()
    /// DailyFAM back to its top, so the friends' row is on screen
    /// (`scrollIntoView`).
    let scrolls = PassthroughSubject<Void, Never>()

    /// `Tour.on()`.
    private(set) var running = false
    /// The step being shown (`at`), and the steps shown, for Back (`shown`).
    private var at = -1
    private var shownSteps: [Int] = []
    /// Bumped by every move, so a late callback from an earlier one (a card
    /// still fading out, an optional step's second look) does nothing once
    /// a quicker tap has moved on.
    private var seq = 0
    /// An optional step looked at once already (`step._looked`).
    private var looked: Set<Int> = []
    /// The first card arrives with the overlay (`card._fresh`).
    private var fresh = false
    private var waitTask: Task<Void, Never>?
    private var watchTask: Task<Void, Never>?

    /// The app, asked what is on screen.
    weak var app: AppModel?
    /// The tabs are drawn (`MainTabs` on screen): counted, as a new
    /// account's tabs appear before the last account's go.
    var hosts = 0

    init() {
        TourFrames.shared.moved = { [weak self] target in self?.frameMoved(target) }
    }

    private var stored: String {
        get { UserDefaults.standard.string(forKey: Self.storeKey) ?? "" }
        set { UserDefaults.standard.set(newValue, forKey: Self.storeKey) }
    }

    private var reduced: Bool { UIAccessibility.isReduceMotionEnabled }

    private var signedIn: Bool { app?.listener?.authenticated == true }

    /// `done()`: taken here, or the account says so.
    private var isDone: Bool {
        if stored == Self.doneMark { return true }
        return signedIn && app?.you.prefs?.tourDone == true
    }

    // MARK: Being offered

    /// Owed from the first run, and kept owed across a relaunch until it
    /// runs. A new account's, whatever an earlier account on this phone did.
    func owe() {
        stored = Self.owedMark
    }

    /// Every way into the app signed in (the app opening, a sign-in, the
    /// first run ending): owed from the first run, or - for an account that
    /// was here before the tour existed - owed once if the account says it
    /// has not been taken (§595). The account's word is asked for once per
    /// phone; a "done" from it is kept here so it is not asked again.
    func offerOnce() {
        if running { return }
        let here = stored
        if here == Self.owedMark {
            offer()
            return
        }
        guard here != Self.doneMark, signedIn, let app else { return }
        let asking = app.listener?.userId
        let api = app.api
        Task { [weak self] in
            guard let answer = try? await api.preferences() else { return }
            guard let self, let app = self.app else { return }
            // Signed out, or somebody else signed in, while this was asked.
            guard answer.saved == true, app.listener?.authenticated == true,
                  app.listener?.userId == asking else { return }
            if answer.tourDone == true {
                self.stored = Self.doneMark
                return
            }
            self.stored = Self.owedMark
            self.offer()
        }
    }

    func offer() {
        if running || isDone || stored != Self.owedMark { return }
        waitUntilClear { [weak self] in
            guard let self else { return }
            // Asked again at the moment it would start: a sign-out while it
            // waited has taken the debt away (`forget`).
            if !self.running && !self.isDone && self.stored == Self.owedMark { self.begin() }
        }
    }

    /// Signing out: whatever this phone knew about the tour was the last
    /// account's, and the next one is asked afresh (`offerOnce`).
    func forget() {
        waitTask?.cancel()
        waitTask = nil
        if running { finishQuietly() }
        stored = ""
        // The last account's sheets are gone with it.
        TourCovers.shared.clear()
    }

    /// Settings' "Take the tour": the same tour, from the top, begun at
    /// once as the web's `start()` is. Nothing is over Settings when its
    /// row is tapped, so a cover still counted then is one whose going was
    /// missed: forgotten, so it cannot hold the tour paused.
    func start() {
        if running { return }
        waitTask?.cancel()
        waitTask = nil
        TourCovers.shared.clear()
        begin()
    }

    private func finishQuietly() {
        running = false
        seq += 1
        watchTask?.cancel()
        watchTask = nil
        visible = false
        paused = false
        fresh = false
        at = -1
        cardStep = -1
    }

    // MARK: What is on screen

    /// Something the tour must not cover or be covered by: a sheet or a
    /// cover, the activation card, the Terms, the player, the loading
    /// screen, the follower popup, a social screen, New message, the AI
    /// question, voice search, the Queue (`.modal-overlay.active,
    /// .sheet-overlay.active`). A tour running pauses while one is up.
    private func overlayOpen() -> Bool {
        guard let app else { return true }
        if TourCovers.shared.any { return true }
        if app.owedActivation != nil || app.termsOwed != nil || app.updateRequired != nil { return true }
        if app.showPlayer || app.loading.visible { return true }
        if app.social.follower != nil || app.social.cover != nil || app.social.newChatOpen { return true }
        if app.consent.asking != nil || app.readingAsk != nil || app.limitReached != nil { return true }
        if app.voiceSearch.isOpen || app.mixDone != nil || app.nowPlaying.showQueue { return true }
        return false
    }

    /// `blocked()`: anything else on screen, the first run, the FAM intro
    /// or the log-out splash, or a screen that is not a tab's root.
    private func blocked() -> Bool {
        guard let app else { return true }
        if app.stage != .ready || app.entry != nil || hosts <= 0 { return true }
        if app.splash { return true }
        // The FAM intro over the tabs (1.82 s; none with Reduce Motion).
        if !reduced && Date().timeIntervalSince(app.introAt) < 1.9 { return true }
        if overlayOpen() { return true }
        switch app.shownTab {
        case .daily: return !app.dailyPath.isEmpty
        case .myfam: return !app.mixPath.isEmpty
        case .messages: return !app.social.messagesPath.isEmpty
        case .you: return !app.youPath.isEmpty
        case .search: return false
        }
    }

    /// Two clear looks in a row, so a sheet or the activation step that is
    /// about to open (it is fetched first) gets there before the tour does.
    private func waitUntilClear(_ then: @escaping () -> Void) {
        waitTask?.cancel()
        waitTask = Task { [weak self] in
            var clear = 0
            while !Task.isCancelled {
                guard let self else { return }
                clear = self.blocked() ? 0 : clear + 1
                if clear >= 2 {
                    then()
                    return
                }
                try? await Task.sleep(nanoseconds: 450_000_000)
            }
        }
    }

    // MARK: Running

    private func begin() {
        running = true
        paused = false
        shownSteps = []
        looked = []
        fresh = false
        cardStep = -1
        cardOpacity = 0
        cardLift = 0
        withAnimation(.easeOut(duration: reduced ? 0.12 : 0.24)) { visible = true }
        watchTask?.cancel()
        watchTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 400_000_000)
                guard let self, self.running else { return }
                self.watch()
            }
        }
        go(0, 1)
    }

    /// Whether a step applies at all, asked of any screen, shown or not: an
    /// optional control that is not drawn for this listener is passed over.
    /// The friends' row is drawn on the faces row's own condition.
    private func present(_ step: TourStep) -> Bool {
        guard step.isOptional else { return true }
        if step.target == .circle {
            guard let circle = app?.circle else { return false }
            return !(circle.circle.isEmpty && circle.me == nil)
        }
        guard let target = step.target else { return false }
        return TourFrames.shared.frame(target) != nil
    }

    private func openScreen(_ tab: Tab) {
        opens.send(tab)
    }

    /// `dir` is 1 forward, -1 back: an optional step whose control is not
    /// on screen (no friends yet) is passed over either way.
    private func go(_ index: Int, _ dir: Int) {
        seq += 1
        let mine = seq
        let steps = Self.steps
        let i = max(0, index)
        if i >= steps.count {
            finish()
            return
        }
        let step = steps[i]
        openScreen(step.screen)
        if step.isOptional && !present(step) {
            // The screen may still be drawing it; one short look before passing.
            if !looked.contains(i) {
                looked.insert(i)
                Task { [weak self] in
                    try? await Task.sleep(nanoseconds: 350_000_000)
                    guard let self else { return }
                    if self.running && mine == self.seq {
                        self.go(i, dir)
                    } else {
                        self.looked.remove(i)
                    }
                }
                return
            }
            looked.remove(i)
            go(i + dir, dir)
            return
        }
        looked.remove(i)
        at = i
        if dir > 0 { shownSteps.append(i) }
        // `scrollIntoView`: the friends' row sits at DailyFAM's top.
        if step.target == .circle { scrolls.send() }
        draw(i)
    }

    private func draw(_ i: Int) {
        let mine = seq
        let reduce = reduced
        if !fresh {
            // The first card arrives with the overlay.
            fresh = true
            showCard(i, first: true)
            if reduce {
                cardOpacity = 1
                cardLift = 0
                return
            }
            cardOpacity = 0
            cardLift = 10
            Task { [weak self] in
                try? await Task.sleep(nanoseconds: 16_000_000)
                guard let self, self.running, mine == self.seq else { return }
                withAnimation(.timingCurve(0.2, 0.8, 0.2, 1, duration: 0.26)) {
                    self.cardOpacity = 1
                    self.cardLift = 0
                }
            }
            return
        }
        withAnimation(.easeIn(duration: reduce ? 0.06 : 0.12)) { cardOpacity = 0 }
        Task { [weak self] in
            try? await Task.sleep(nanoseconds: reduce ? 60_000_000 : 120_000_000)
            // A newer card is on its way.
            guard let self, self.running, mine == self.seq else { return }
            self.showCard(i, first: false)
            if !reduce {
                self.cardLift = 6
                try? await Task.sleep(nanoseconds: 16_000_000)
                guard self.running, mine == self.seq else { return }
            }
            withAnimation(.timingCurve(0.2, 0.8, 0.2, 1, duration: reduce ? 0.12 : 0.22)) {
                self.cardOpacity = 1
                self.cardLift = 0
            }
        }
    }

    /// The card's words and the hole change together; VoiceOver hears the
    /// new title (the web's `aria-live` title).
    private func showCard(_ i: Int, first: Bool) {
        cardStep = i
        let title = Self.steps[i].title
        UIAccessibility.post(notification: first ? .screenChanged : .layoutChanged, argument: title)
    }

    /// A sheet, the activation step or the Terms opening part-way: the tour
    /// steps aside and comes back where it was once it has gone.
    private func watch() {
        guard running, at >= 0 else { return }
        let other = overlayOpen()
        if other && !paused {
            paused = true
        } else if !other && paused {
            paused = false
            openScreen(Self.steps[at].screen)
        }
    }

    func next() {
        if running { go(at + 1, 1) }
    }

    func back() {
        guard running, at > 0 else { return }
        while let last = shownSteps.last, last >= at { shownSteps.removeLast() }
        let prev = shownSteps.popLast() ?? 0
        go(prev, 1)
    }

    func skip() {
        finish()
    }

    /// Finished or skipped, remembered alike: "done" here, `tour_done` on
    /// the account, and back on dailyFAM.
    func finish() {
        guard running else { return }
        running = false
        waitTask?.cancel()
        waitTask = nil
        watchTask?.cancel()
        watchTask = nil
        stored = Self.doneMark
        if signedIn, let you = app?.you {
            Task { _ = await you.savePreference(["tour_done": true]) }
        }
        fresh = false
        openScreen(.daily)
        if paused {
            close()
            return
        }
        withAnimation(.easeIn(duration: reduced ? 0.12 : 0.22)) { visible = false }
        Task { [weak self] in
            try? await Task.sleep(nanoseconds: 240_000_000)
            guard let self, !self.running else { return }
            self.close()
        }
    }

    private func close() {
        visible = false
        paused = false
        at = -1
        cardStep = -1
    }

    // MARK: For the card

    /// "n of N", counted over the steps that apply to this listener, so a
    /// step passed over (no friends yet) never shows as a jump in the count.
    func count(of index: Int) -> (n: Int, of: Int) {
        let steps = Self.steps
        let live = steps.indices.filter { present(steps[$0]) }
        let n = (live.firstIndex(of: index) ?? -1) + 1
        return (n, max(live.count, 1))
    }

    func frame(of target: TourTarget) -> CGRect? {
        TourFrames.shared.frame(target)
    }

    private func frameMoved(_ target: TourTarget) {
        let steps = Self.steps
        guard running, steps.indices.contains(cardStep), steps[cardStep].target == target else { return }
        frameTick &+= 1
    }
}

/// One card of the tour (`STEPS`).
struct TourStep {
    let screen: Tab
    var target: TourTarget? = nil
    var isOptional: Bool = false
    var art: TourArt? = nil
    let kicker: String
    let title: String
    let body: String
    var next: String? = nil
}

enum TourArt {
    case logo, player
}

/// The real controls the hole goes round (the web's selectors).
enum TourTarget: Hashable {
    /// `#myfamFeed`: DailyFAM's rails.
    case feed
    /// `#myfamCircle`: the friends' faces row.
    case circle
    /// `#searchBar`.
    case searchBar
    /// `#searchBar .bar-btns`: mic, attach (and go).
    case barButtons
    /// `#screen-home .fam-controls`: Length, Voice, Private.
    case controls
    /// `#myfamSearchBtn`: the bookshelf on searchFAM.
    case library
    /// `#screen-playfam .pf-tabs`: Mixes | Categories.
    case mixTabs
    /// The Messages and yourFAM tabs on the bar.
    case messagesTab
    case youTab
}

/// Where each target is on screen, in the global space. A plain box, as
/// `FaceFrames` is: only a move of the shown step's control redraws.
final class TourFrames {
    static let shared = TourFrames()
    private var frames: [TourTarget: CGRect] = [:]
    var moved: ((TourTarget) -> Void)?

    func set(_ target: TourTarget, _ frame: CGRect?) {
        if frames[target] == frame { return }
        frames[target] = frame
        moved?(target)
    }

    func frame(_ target: TourTarget) -> CGRect? {
        frames[target]
    }
}

private struct TourTargetReader: ViewModifier {
    let target: TourTarget?

    @ViewBuilder
    func body(content: Content) -> some View {
        if let target {
            content.background(GeometryReader { geo in
                Color.clear
                    .onAppear { TourFrames.shared.set(target, geo.frame(in: .global)) }
                    .onChange(of: geo.frame(in: .global)) { _, frame in TourFrames.shared.set(target, frame) }
                    .onDisappear { TourFrames.shared.set(target, nil) }
            })
        } else {
            content
        }
    }
}

/// The pieces of a target drawn lazily (DailyFAM's rails, `#myfamFeed`):
/// each reports its bounds, and the stack round them reads them once.
struct TourFeedParts: PreferenceKey {
    static let defaultValue: [Anchor<CGRect>] = []
    static func reduce(value: inout [Anchor<CGRect>], nextValue: () -> [Anchor<CGRect>]) {
        value.append(contentsOf: nextValue())
    }
}

/// Reports the union of the parts drawn as one target, in the global
/// space. Cheap when the tour is not running: a compare and a store.
struct TourUnionReader: View {
    let target: TourTarget
    let parts: [Anchor<CGRect>]

    var body: some View {
        GeometryReader { geo in
            let rect: CGRect? = Self.union(parts, in: geo)
            Color.clear
                .onAppear { TourFrames.shared.set(target, rect) }
                .onChange(of: rect) { _, now in TourFrames.shared.set(target, now) }
                .onDisappear { TourFrames.shared.set(target, nil) }
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }

    private static func union(_ parts: [Anchor<CGRect>], in geo: GeometryProxy) -> CGRect? {
        guard let first = parts.first else { return nil }
        var all: CGRect = geo[first]
        for part in parts.dropFirst() { all = all.union(geo[part]) }
        let origin = geo.frame(in: .global).origin
        return all.offsetBy(dx: origin.x, dy: origin.y)
    }
}

/// The sheets and covers on screen, each by its own id - in as it appears,
/// out as it goes, so a second appearance never counts twice - so the tour
/// waits behind them and pauses under them.
final class TourCovers {
    static let shared = TourCovers()
    private var open: Set<UUID> = []

    var any: Bool { !open.isEmpty }

    func shown(_ id: UUID) { open.insert(id) }

    func gone(_ id: UUID) { open.remove(id) }

    func clear() { open.removeAll() }
}

private struct TourCoverCount: ViewModifier {
    @State private var id = UUID()

    func body(content: Content) -> some View {
        content
            .onAppear { TourCovers.shared.shown(id) }
            .onDisappear { TourCovers.shared.gone(id) }
    }
}

extension View {
    /// This view is one the tour may put its hole round; nil marks nothing
    /// (a still copy of a page, as searchFAM's blind draws).
    func tourTarget(_ target: TourTarget?) -> some View {
        modifier(TourTargetReader(target: target))
    }

    /// One piece of DailyFAM's rails, for the tour's hole round all of them.
    func tourFeedPart() -> some View {
        anchorPreference(key: TourFeedParts.self, value: .bounds) { [$0] }
    }

    /// This presented view is a sheet or cover the tour waits behind.
    func tourCover() -> some View {
        modifier(TourCoverCount())
    }
}

// MARK: - The overlay

/// Where the hole and the card go (`place()`): the hole round the control,
/// 6 pt out and rounded min(22, h / 2); the card below it when it fits,
/// above when that fits, and over the bottom of a control too tall for
/// either (the dailyFAM feed), with the hole cut short. No control: the
/// card centred and no hole.
struct TourSpot: Equatable {
    var hole: CGRect
    var radius: CGFloat
    var none: Bool
    var cardTop: CGFloat

    static func place(target: CGRect?, box: CGRect, cardHeight: CGFloat) -> TourSpot {
        let width: CGFloat = box.width
        let height: CGFloat = box.height
        let gap: CGFloat = 14
        let pad: CGFloat = 6
        let ch: CGFloat = cardHeight
        guard let r = target, r.width >= 2, r.height >= 2 else {
            return TourSpot(hole: CGRect(x: width / 2, y: height / 2, width: 0, height: 0),
                            radius: 0, none: true, cardTop: max(16, (height - ch) / 2))
        }
        let x: CGFloat = max(8, r.minX - box.minX - pad)
        let y: CGFloat = max(8, r.minY - box.minY - pad)
        let x2: CGFloat = min(width - 8, r.maxX - box.minX + pad)
        var y2: CGFloat = min(height - 8, r.maxY - box.minY + pad)
        let top: CGFloat
        if height - y2 >= ch + gap + 16 {
            top = y2 + gap
        } else if y >= ch + gap + 16 {
            top = y - gap - ch
        } else {
            top = height - ch - 16
            y2 = max(y + 60, top - gap)
        }
        let w: CGFloat = max(0, x2 - x)
        let h: CGFloat = max(0, y2 - y)
        return TourSpot(hole: CGRect(x: x, y: y, width: w, height: h),
                        radius: min(22, h / 2), none: false, cardTop: max(16, top))
    }
}

/// The dim with the hole cut out (`.tour-hole`'s 9999 px shadow,
/// rgba(10,9,18,.72)), its hole animatable.
struct TourDim: Shape {
    var hole: CGRect
    var radius: CGFloat

    var animatableData: AnimatablePair<CGRect.AnimatableData, CGFloat> {
        get { AnimatablePair(hole.animatableData, radius) }
        set {
            hole.animatableData = newValue.first
            radius = newValue.second
        }
    }

    func path(in rect: CGRect) -> Path {
        var p = Path(rect)
        let corner: CGFloat = max(0, min(radius, hole.width / 2, hole.height / 2))
        p.addRoundedRect(in: hole, cornerSize: CGSize(width: corner, height: corner))
        return p
    }
}

/// The hole's 2 pt yellow ring (`0 0 0 2px rgba(255,210,63,.9)`), just
/// outside it.
struct TourRing: Shape {
    var hole: CGRect
    var radius: CGFloat

    var animatableData: AnimatablePair<CGRect.AnimatableData, CGFloat> {
        get { AnimatablePair(hole.animatableData, radius) }
        set {
            hole.animatableData = newValue.first
            radius = newValue.second
        }
    }

    func path(in rect: CGRect) -> Path {
        let ring = hole.insetBy(dx: -1, dy: -1)
        let corner: CGFloat = max(0, min(radius + 1, ring.width / 2, ring.height / 2))
        return Path(roundedRect: ring, cornerSize: CGSize(width: corner, height: corner))
    }
}

/// `#famTour` over the tabs and the bar: it takes every tap while it is up,
/// so nothing underneath is touched by accident.
struct TourOverlay: View {
    @ObservedObject var tour: TourModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// The card's height as drawn (`card.offsetHeight`).
    @State private var cardHeight: CGFloat = 0
    /// The card has been placed once: it glides from then on, never from
    /// where it was before it was measured.
    @State private var placed = false

    private var glide: Animation { .timingCurve(0.2, 0.8, 0.2, 1, duration: 0.38) }

    var body: some View {
        ZStack {
            if tour.visible && !tour.paused {
                GeometryReader { geo in
                    layer(geo)
                }
                // The whole window, the status bar's strip and the home
                // indicator's included, as the web's dims its whole page.
                .ignoresSafeArea()
                .transition(.opacity)
                // Back from a pause with the card already measured: it
                // glides again from its next move.
                .onAppear { if cardHeight > 0 { settle() } }
                .onDisappear { placed = false }
            }
        }
    }

    private func settle() {
        guard !placed else { return }
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 60_000_000)
            placed = true
        }
    }

    private func layer(_ geo: GeometryProxy) -> some View {
        // The reader fills the window (`ignoresSafeArea`), in the global
        // space the targets report in. The card is placed in the room left
        // between the status bar and the home indicator, and the hole and
        // the card are drawn back in the window's own coordinates.
        let full = geo.frame(in: .global)
        let insets = geo.safeAreaInsets
        let topInset: CGFloat = insets.top
        let roomHeight: CGFloat = max(0, full.height - insets.top - insets.bottom)
        let room = CGRect(x: full.minX, y: full.minY + topInset, width: full.width, height: roomHeight)
        let steps = TourModel.steps
        let index = tour.cardStep
        let step: TourStep? = steps.indices.contains(index) ? steps[index] : nil
        let target: CGRect? = step?.target.flatMap { tour.frame(of: $0) }
        let spot = TourSpot.place(target: target, box: room, cardHeight: cardHeight)
        let hole: CGRect = spot.hole.offsetBy(dx: 0, dy: topInset)
        let cardTop: CGFloat = spot.cardTop + topInset
        let cardGlide: Animation? = (reduceMotion || !placed) ? nil : glide
        return ZStack(alignment: .topLeading) {
            TourDim(hole: hole, radius: spot.radius)
                .fill(Color(hex: 0x0A0912).opacity(0.72), style: FillStyle(eoFill: true))
                .frame(width: full.width, height: full.height)
                .animation(reduceMotion ? nil : glide, value: hole)
                .contentShape(Rectangle())
                .onTapGesture {}
                .accessibilityHidden(true)
            TourRing(hole: hole, radius: spot.radius)
                .stroke(FAMColor.deeper.opacity(0.9), lineWidth: 2)
                .frame(width: full.width, height: full.height)
                .opacity(spot.none ? 0 : 1)
                .animation(reduceMotion ? nil : glide, value: hole)
                .animation(reduceMotion ? nil : .easeInOut(duration: 0.38), value: spot.none)
                .allowsHitTesting(false)
                .accessibilityHidden(true)
            if let step {
                TourCard(tour: tour, step: step, index: index, maxHeight: max(0, roomHeight - 32))
                    .background(GeometryReader { g in
                        Color.clear
                            .onAppear { cardHeight = g.size.height }
                            .onChange(of: g.size.height) { _, h in cardHeight = h }
                    })
                    .frame(maxWidth: 420)
                    .padding(.horizontal, 16)
                    .frame(width: full.width)
                    .opacity(tour.cardOpacity)
                    .offset(y: tour.cardLift)
                    .animation(cardGlide) { $0.offset(y: cardTop) }
            }
        }
        .frame(width: full.width, height: full.height, alignment: .topLeading)
        .onChange(of: cardHeight) { _, h in
            if h > 0 { settle() }
        }
    }
}

/// `.tour-card`: kicker and Skip, the art, the title, the words, then
/// Back, "n of N" over its bar, and Next. At most the screen less 32 pt
/// tall, scrolling inside past that (large text).
private struct TourCard: View {
    @ObservedObject var tour: TourModel
    let step: TourStep
    let index: Int
    let maxHeight: CGFloat
    @Environment(\.dynamicTypeSize) private var typeSize
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var last: Bool { index == TourModel.steps.count - 1 }

    var body: some View {
        // One copy that scrolls only when taller than the room
        // (`ScrollsWhenTall`, §589): never two copies swapped in and out.
        ScrollsWhenTall { inner }
        .frame(maxHeight: maxHeight)
        .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 20))
        .overlay(RoundedRectangle(cornerRadius: 20).stroke(FAMColor.borderStrong, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 20))
        .shadow(color: Color.black.opacity(0.45), radius: 24, x: 0, y: 18)
        // A modal for VoiceOver (`role="dialog" aria-modal="true"`), and the
        // two-finger scrub is Escape: Skip.
        .accessibilityElement(children: .contain)
        .accessibilityAddTraits(.isModal)
        .accessibilityAction(.escape) { tour.skip() }
        .background {
            // Escape on the last card too (the web's `onKey`).
            if last {
                Button("") { tour.skip() }
                    .keyboardShortcut(.escape, modifiers: [])
                    .frame(width: 0, height: 0)
                    .opacity(0)
                    .accessibilityHidden(true)
            }
        }
    }

    private var inner: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .center, spacing: 10) {
                Text(step.kicker)
                    .monoLabel(10.5, color: FAMColor.copper)
                    .frame(maxWidth: .infinity, alignment: .leading)
                if !last {
                    Button { tour.skip() } label: {
                        Text("Skip")
                            .font(FAMFont.body(13, .medium))
                            .foregroundStyle(FAMColor.textMuted)
                            .padding(.top, 6)
                            .padding(.bottom, 6)
                            .padding(.leading, 10)
                            .padding(.trailing, 2)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.press)
                    .keyboardShortcut(.escape, modifiers: [])
                }
            }
            .padding(.bottom, 6)
            if let art = step.art {
                Group {
                    switch art {
                    case .logo: Wordmark(size: 34)
                    case .player: TourPlayerArt()
                    }
                }
                .frame(maxWidth: .infinity)
                .padding(.top, 4)
                .padding(.bottom, 14)
                .accessibilityHidden(true)
            }
            Text(step.title)
                .font(FAMFont.heading(21, .medium))
                .foregroundStyle(FAMColor.text)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.bottom, 6)
                .accessibilityAddTraits(.isHeader)
            Text(step.body)
                .font(FAMFont.body(14))
                .foregroundStyle(FAMColor.textMuted)
                .lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true)
            foot
                .padding(.top, 16)
        }
        .padding(.top, 18)
        .padding(.horizontal, 18)
        .padding(.bottom, 14)
    }

    @ViewBuilder private var foot: some View {
        if typeSize.isAccessibilitySize {
            // Large text: the count over its own row, the buttons under it.
            VStack(alignment: .leading, spacing: 10) {
                progress
                HStack(spacing: 10) {
                    backButton
                    Spacer(minLength: 0)
                    nextButton
                }
            }
        } else {
            HStack(alignment: .center, spacing: 10) {
                backButton
                progress
                nextButton
            }
        }
    }

    @ViewBuilder private var backButton: some View {
        if index != 0 && !last {
            Button { tour.back() } label: {
                Text("Back")
                    .font(FAMFont.body(13.5, .semibold))
                    .foregroundStyle(FAMColor.text)
                    .padding(.horizontal, 14)
                    .frame(minHeight: 40)
                    .overlay(Capsule().stroke(FAMColor.borderStrong, lineWidth: 1))
                    .contentShape(Capsule())
            }
            .buttonStyle(.press)
            .keyboardShortcut(.leftArrow, modifiers: [])
        }
    }

    private var nextButton: some View {
        Button { tour.next() } label: {
            Text(step.next ?? (last ? "Done" : "Next"))
                .font(FAMFont.body(13.5, .bold))
                .foregroundStyle(FAMColor.ink)
                .lineLimit(1)
                .fixedSize(horizontal: true, vertical: false)
                .padding(.horizontal, 18)
                .frame(minHeight: 40)
                .background(FAMColor.copper, in: Capsule())
                .contentShape(Capsule())
        }
        .buttonStyle(.press)
        .keyboardShortcut(.rightArrow, modifiers: [])
    }

    private var progress: some View {
        let count = tour.count(of: index)
        let fraction: CGFloat = CGFloat((Double(count.n) / Double(count.of) * 100).rounded() / 100)
        return VStack(alignment: .leading, spacing: 5) {
            Text("\(count.n) of \(count.of)")
                .font(FAMFont.mono(10))
                .foregroundStyle(FAMColor.textFaint)
            GeometryReader { g in
                ZStack(alignment: .leading) {
                    Capsule().fill(FAMColor.borderStrong)
                    Capsule().fill(FAMColor.copper)
                        .frame(width: g.size.width * fraction)
                        .animation(reduceMotion ? nil : .timingCurve(0.2, 0.8, 0.2, 1, duration: 0.38),
                                   value: fraction)
                }
            }
            .frame(height: 3)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityHidden(true)
    }
}

/// The player step's drawing (`tourPlayer()`): GO DEEPER and CC, share,
/// VIBE!'s star and save on the right, the picture and the bar.
private struct TourPlayerArt: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .center, spacing: 8) {
                Text("GO DEEPER")
                    .font(FAMFont.mono(9.5, bold: true))
                    .tracking(9.5 * 0.08)
                    .foregroundStyle(FAMColor.ink)
                    .padding(.vertical, 4)
                    .padding(.horizontal, 9)
                    .background(FAMColor.deeper, in: Capsule())
                Text("CC")
                    .font(FAMFont.mono(9.5, bold: true))
                    .tracking(9.5 * 0.08)
                    .foregroundStyle(FAMColor.text)
                    .padding(.vertical, 4)
                    .padding(.horizontal, 9)
                    .overlay(Capsule().stroke(FAMColor.borderStrong, lineWidth: 1))
                Spacer(minLength: 0)
                HStack(spacing: 10) {
                    WebIcon(path: TourIcons.share, size: 16)
                    WebIcon(path: TourIcons.star, size: 16)
                    WebIcon(path: TourIcons.save, size: 16)
                }
                .foregroundStyle(FAMColor.textMuted)
            }
            RoundedRectangle(cornerRadius: 9)
                .fill(LinearGradient(colors: [FAMColor.surface2, FAMColor.copper],
                                     startPoint: .topLeading, endPoint: .bottomTrailing))
                .opacity(0.85)
                .frame(height: 54)
            GeometryReader { g in
                ZStack(alignment: .leading) {
                    RoundedRectangle(cornerRadius: 2).fill(FAMColor.borderStrong)
                    RoundedRectangle(cornerRadius: 2).fill(FAMColor.copper)
                        .frame(width: g.size.width * 0.38)
                }
            }
            .frame(height: 3)
        }
        .padding(10)
        .frame(maxWidth: .infinity)
        .background(FAMColor.background, in: RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(FAMColor.border, lineWidth: 1))
    }
}

/// `tourPlayer()`'s three icons, path for path.
private enum TourIcons {
    /// `M4 12v7a1 1 0 001 1h14a1 1 0 001-1v-7M16 6l-4-4-4 4M12 2v13`
    static let share: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 4, y: 12)); p.addLine(to: CGPoint(x: 4, y: 19))
        p.addQuadCurve(to: CGPoint(x: 5, y: 20), control: CGPoint(x: 4, y: 20))
        p.addLine(to: CGPoint(x: 19, y: 20))
        p.addQuadCurve(to: CGPoint(x: 20, y: 19), control: CGPoint(x: 20, y: 20))
        p.addLine(to: CGPoint(x: 20, y: 12))
        p.move(to: CGPoint(x: 16, y: 6)); p.addLine(to: CGPoint(x: 12, y: 2)); p.addLine(to: CGPoint(x: 8, y: 6))
        p.move(to: CGPoint(x: 12, y: 2)); p.addLine(to: CGPoint(x: 12, y: 15))
        return p
    }()

    /// `M12 3l2.6 5.6 6 .7-4.5 4.1 1.2 6L12 16.4 6.7 19.4l1.2-6L3.4 9.3l6-.7z`
    static let star: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 12, y: 3))
        p.addLine(to: CGPoint(x: 14.6, y: 8.6))
        p.addLine(to: CGPoint(x: 20.6, y: 9.3))
        p.addLine(to: CGPoint(x: 16.1, y: 13.4))
        p.addLine(to: CGPoint(x: 17.3, y: 19.4))
        p.addLine(to: CGPoint(x: 12, y: 16.4))
        p.addLine(to: CGPoint(x: 6.7, y: 19.4))
        p.addLine(to: CGPoint(x: 7.9, y: 13.4))
        p.addLine(to: CGPoint(x: 3.4, y: 9.3))
        p.addLine(to: CGPoint(x: 9.4, y: 8.6))
        p.closeSubpath()
        return p
    }()

    /// `M6 3h12v18l-6-4-6 4z`
    static let save: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 6, y: 3))
        p.addLine(to: CGPoint(x: 18, y: 3))
        p.addLine(to: CGPoint(x: 18, y: 21))
        p.addLine(to: CGPoint(x: 12, y: 17))
        p.addLine(to: CGPoint(x: 6, y: 21))
        p.closeSubpath()
        return p
    }()
}
