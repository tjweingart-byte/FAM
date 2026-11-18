import SwiftUI
import UIKit

// MARK: - Motion (§292, §298: `Motion` in static/index.html)
//
// The web's owner-chosen motion, as far as the iOS app draws its own: every
// button squeezes and springs back, tabs nudge toward the side the new tab
// is on, new rails arrive tile by tile. Nothing waits on any of it
// (`motion`, `latency-start-earlier`): the button acts on release as it
// always did, the new tab is live from its first frame, a tile is tappable
// while it arrives. Round three (§640): every curve and time is a named
// token (`Motion` below, the web's `:root` `--ease-*`, `--dur-*`,
// `--spring-*`), and with Reduce Motion on every movement is off, fades
// included - a screen change and a sheet are instant - and only loading
// indicators keep a gentle opacity pulse.

/// The web's motion tokens (`:root` in static/index.html, read alike by its
/// CSS and `Motion.DUR`), so the app and the web share one set of numbers.
enum Motion {
    /// `--ease-out` (.32,.72,0,1): things arriving, and most settling.
    static func easeOut(_ duration: Double) -> Animation {
        .timingCurve(0.32, 0.72, 0, 1, duration: duration)
    }
    /// `--ease-in` (.55,0,1,.45): things leaving.
    static func easeIn(_ duration: Double) -> Animation {
        .timingCurve(0.55, 0, 1, 0.45, duration: duration)
    }
    /// `--ease-in-out` (.77,0,.175,1): things moving on screen.
    static func easeInOut(_ duration: Double) -> Animation {
        .timingCurve(0.77, 0, 0.175, 1, duration: duration)
    }
    /// `--ease-swap` (.25,.1,0,1): an icon or a word changing in place.
    static func easeSwap(_ duration: Double) -> Animation {
        .timingCurve(0.25, 0.1, 0, 1, duration: duration)
    }
    /// `--spring-soft`: .5 s, bounce .15 (a dot landing, a settle).
    static var springSoft: Animation { .spring(duration: 0.5, bounce: 0.15) }
    /// `--spring-icon`: .35 s, no bounce (the icon swap).
    static var springIcon: Animation { .spring(duration: 0.35, bounce: 0) }

    /// `--dur-*`, in seconds.
    enum Dur {
        static let pressDown = 0.09, pressUp = 0.22
        static let colour = 0.15
        static let tab = 0.16
        static let page = 0.34, pageBack = 0.26
        static let modalIn = 0.22, modalOut = 0.16
        static let sheetIn = 0.28, sheetOut = 0.2
        static let toastIn = 0.24, toastOut = 0.17
        static let fadeIn = 0.24, fadeOut = 0.2, handOver = 0.12
        static let flight = 0.36
        static let barIn = 0.26, barOut = 0.19
        static let icon = 0.35, spring = 0.45, settleDelay = 0.03
        static let title = 0.24, picture = 0.24
        static let label = 0.9
        static let nextUp = 0.28
        static let step = 0.26, roll = 0.3
        static let tour = 0.34
        static let arrive = 0.35, arriveLead = 0.09, stagger = 0.03
        static let cardIn = 0.26, cardOut = 0.15, reflow = 0.2
        static let spinnerAfter = 0.3, spinnerMin = 0.5
        static let storyHide = 0.15, storyShow = 0.2, storySpring = 0.4
        static let reply = 0.22
        static let swap = 0.3
        static let knob = 0.22, shackle = 0.18
        static let tick = 0.22, tickAt = 0.08
        static let focus = 0.12
        static let drain = 0.26
        static let column = 0.22
        static let sendingAfter = 1.0, lineOut = 0.12
        static let rowOut = 0.18, undoIn = 0.22
        static let follow = 0.26
        static let photoDevelop = 0.32
        static let switchKnob = 0.2
        static let undoBack = 0.2
        /// A line with an action (Undo, Open) stays this long
        /// (`SNACK_ACTION_MS`).
        static let undoLine = 5.0
    }

    /// The system's Reduce Motion, for code outside a view.
    static var reduced: Bool { UIAccessibility.isReduceMotionEnabled }

    /// `animation`, or none under Reduce Motion (round three: all off).
    static func anim(_ animation: Animation, reduce: Bool = Motion.reduced) -> Animation? {
        reduce ? nil : animation
    }
}

// MARK: - The icon swap (`--spring-icon`, §640)

/// One state of an icon in a swap: the leaving one shrinks to .3 behind a
/// 3 pt blur and fades; the arriving one comes the other way.
private struct IconSwapState: ViewModifier {
    let away: Bool

    func body(content: Content) -> some View {
        content
            .scaleEffect(away ? 0.3 : 1)
            .blur(radius: away ? 3 : 0)
            .opacity(away ? 0 : 1)
    }
}

extension AnyTransition {
    /// The shared icon swap (play / pause, save, the lock, mic / go): both
    /// icons cross at the same moment. Reduce Motion: on one frame.
    static var iconSwap: AnyTransition {
        if Motion.reduced { return .identity }
        return .modifier(active: IconSwapState(away: true), identity: IconSwapState(away: false))
    }
}

/// An icon that crosses to its next state on the icon spring when `key`
/// changes - one cell, so nothing beside it moves.
struct IconSwap<Key: Hashable, Icon: View>: View {
    let key: Key
    @ViewBuilder let icon: () -> Icon
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ZStack {
            icon()
                .id(key)
                .transition(reduceMotion ? .identity : .iconSwap)
        }
        .animation(reduceMotion ? nil : Motion.springIcon, value: key)
    }
}

/// Every button (`PRESSABLE` on the web): down to about 8 pt smaller - a
/// big row barely moves, a small button visibly does, never below 0.9 -
/// in 90 ms, held while pressed, sprung back with a small overshoot. A
/// press that turns into a scroll lets go at once (SwiftUI's own). Areas
/// over about a third of the screen (a story's tap zones, a dim behind a
/// card) do not squeeze, as on the web. Otherwise draws what `.plain`
/// drew: the label, unstyled, dimmed while disabled.
struct PressButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        PressedLabel(label: configuration.label, pressed: configuration.isPressed)
    }
}

extension ButtonStyle where Self == PressButtonStyle {
    /// `.buttonStyle(.press)`: `.plain` with the web's squeeze.
    static var press: PressButtonStyle { PressButtonStyle() }
}

/// The squeeze, for `.press` and for the app's drawn button styles (the
/// copper, outlined and entry pills), which keep their own disabled look.
struct PressedLabel<Label: View>: View {
    let label: Label
    let pressed: Bool
    /// `.plain`'s half-dim while disabled; a drawn style keeps its own.
    var dims = true

    /// Spelled out: the private state below would make the memberwise
    /// initialiser private to this file.
    init(label: Label, pressed: Bool, dims: Bool = true) {
        self.label = label
        self.pressed = pressed
        self.dims = dims
    }
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.isEnabled) private var isEnabled
    @State private var size: CGSize = .zero

    /// About 30 % of a phone's screen, in points.
    private static var largestArea: CGFloat { 100_000 }

    private var squeezes: Bool {
        guard !reduceMotion, isEnabled, size.width > 0, size.height > 0 else { return false }
        return size.width * size.height <= Self.largestArea
    }

    private var scale: CGFloat {
        let side = max(size.width, size.height, 1)
        return max(0.9, 1 - 8 / side)
    }

    var body: some View {
        let target: CGFloat = (pressed && squeezes) ? scale : 1
        let curve: Animation = pressed ? .easeOut(duration: 0.09)
                                       : .spring(response: 0.22, dampingFraction: 0.55)
        label
            .background(GeometryReader { geo in
                Color.clear
                    .onAppear { size = geo.size }
                    .onChange(of: geo.size) { _, new in size = new }
            })
            .animation(curve) { content in
                content.scaleEffect(target)
            }
            // The picture a press lands on is where the player grows from.
            .modifier(PressRecording(pressed: pressed))
            // `.plain` dimmed a disabled button; a custom style draws no
            // disabled look of its own, so this keeps that one.
            .opacity(isEnabled || !dims ? 1 : 0.5)
    }
}

/// `.press` on something tapped with `onTapGesture` rather than a Button
/// (a card that holds its own buttons): squeezed while a finger rests on
/// it, let go when it moves - a press that turns into a scroll lets go, and
/// the tap and the scroll still belong to whoever had them.
struct TapSqueeze: ViewModifier {
    @GestureState private var down = false

    func body(content: Content) -> some View {
        content
            .pressSqueeze(down)
            .simultaneousGesture(
                LongPressGesture(minimumDuration: 60, maximumDistance: 10)
                    .updating($down) { pressing, state, _ in state = pressing })
    }
}

extension View {
    func tapSqueeze() -> some View { modifier(TapSqueeze()) }
}

// MARK: - A removed row slides out, then folds (`removeRow`)

/// A row taken out of a list (the queue, Saved for Later): it slides 45 %
/// of its width to the left and fades over 180 ms on `--ease-in`; the rows
/// under it close the gap over the next 200 ms on `--ease-in-out`
/// (`rowFold`; round three's `saved-remove` A).
struct RowGone: ViewModifier {
    let gone: Bool

    func body(content: Content) -> some View {
        let gone = self.gone
        content
            .visualEffect { view, proxy in
                view.offset(x: gone ? -0.45 * proxy.size.width : 0)
            }
            .opacity(gone ? 0 : 1)
    }
}

extension AnyTransition {
    /// With Reduce Motion the list just redraws (`removeRow`'s `reduced`).
    static var rowRemoval: AnyTransition {
        if UIAccessibility.isReduceMotionEnabled { return .identity }
        return .asymmetric(insertion: .opacity,
                           removal: AnyTransition.modifier(active: RowGone(gone: true), identity: RowGone(gone: false))
                               .animation(Motion.easeIn(Motion.Dur.rowOut)))
    }
}

extension Animation {
    /// The rows under a removed one close up after it has slid away, on the
    /// web's default EASE (`cubic-bezier(.2,.8,.2,1)`: the fold names no
    /// easing of its own). Reduce Motion: nil, the list just redraws.
    static var rowFold: Animation? {
        if UIAccessibility.isReduceMotionEnabled { return nil }
        return Motion.easeInOut(Motion.Dur.reflow).delay(Motion.Dur.rowOut)
    }
}

// MARK: - Tabs nudge toward the new tab's side

extension Tab {
    /// Left to right along the bar, so a change knows its side.
    var order: Int {
        switch self {
        case .daily: return 0
        case .myfam: return 1
        case .search: return 2
        case .messages: return 3
        case .you: return 4
        }
    }
}

/// One tab's page in the tab stack (`Motion.screen(…, "tab")`, round
/// three's C, §640): the old page is gone on the first frame; the new one's
/// content nudges 12 pt in from the new tab's side over 160 ms. Only the
/// page moves; the tab bar and the mini player stay where they are. Reduce
/// Motion: the page is simply there.
struct TabPage: ViewModifier {
    let me: Tab
    let current: Tab
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    func body(content: Content) -> some View {
        let on = me == current
        let side: CGFloat = me.order > current.order ? 1 : -1
        let shift: CGFloat = (on || reduceMotion) ? 0 : 12 * side
        content
            .animation(on && !reduceMotion ? Motion.easeOut(Motion.Dur.tab) : nil) { page in
                page.offset(x: shift)
            }
            .opacity(on ? 1 : 0)
            .allowsHitTesting(on)
    }
}

extension View {
    func tabPage(_ me: Tab, current: Tab) -> some View {
        modifier(TabPage(me: me, current: current))
    }
}

// MARK: - Rails arrive tile by tile

/// The tiles already drawn on this phone, by id, so a redraw - a refresh,
/// the page reloaded, a rail scrolled back into view - never replays the
/// arrival (`arriveRails`' `before`).
final class ArrivalBook {
    var seen: Set<String> = []
}

/// `Motion.arrive`: a new tile rises 8 pt and fades in over 260 ms, each
/// one 30-40 ms after the one before. Tappable throughout. The rails'
/// round-three arrival (§641, `rails-arrive` B) passes `blur` 2 and 350 ms;
/// Refresh's deal (`section-refresh` B) comes from the side (`dx` 24, no
/// rise). Reduce Motion: there at once.
struct ArriveOnce: ViewModifier {
    let key: String
    let delay: Double
    let book: ArrivalBook
    var rise: CGFloat = 8
    var dx: CGFloat = 0
    var blur: CGFloat = 0
    var duration: Double = 0.26
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var shown: Bool

    init(key: String, delay: Double, book: ArrivalBook,
         rise: CGFloat = 8, dx: CGFloat = 0, blur: CGFloat = 0, duration: Double = 0.26) {
        self.key = key
        self.delay = delay
        self.book = book
        self.rise = rise
        self.dx = dx
        self.blur = blur
        self.duration = duration
        _shown = State(initialValue: book.seen.contains(key))
    }

    func body(content: Content) -> some View {
        content
            .opacity(shown ? 1 : 0)
            .offset(x: shown ? 0 : dx, y: shown ? 0 : rise)
            .blur(radius: shown ? 0 : blur)
            .onAppear {
                if shown { return }
                let first = !book.seen.contains(key)
                book.seen.insert(key)
                if reduceMotion || !first {
                    shown = true
                    return
                }
                withAnimation(Motion.easeOut(duration).delay(delay)) {
                    shown = true
                }
            }
    }
}

extension View {
    /// The rails' arrival (round three, §641 `rails-arrive` B), in reading
    /// order as the web's `arriveRails` reckons it on a phone: each rail in
    /// view (the first three) puts its heading at its clock and its tiles
    /// 90 ms after it, 30 ms apart; only the first three tiles of a rail
    /// are on screen at the start, and six tiles at most arrive, so the
    /// first two rails' tiles and the first three headings arrive - 350 ms
    /// on `--ease-out`, 8 pt up out of a 2 pt blur. `index` is the tile's
    /// place along its rail (-1 for the heading). Anything drawn before on
    /// this phone just appears.
    @ViewBuilder
    func arriving(_ key: String, index: Int, rail order: Int, book: ArrivalBook) -> some View {
        // Each rail in view with arriving tiles spends a lead and three
        // steps: 90 + 3 x 30 ms.
        let clock = Double(min(order, 2)) * (Motion.Dur.arriveLead + 3 * Motion.Dur.stagger)
        if index < 0 && order < 3 {
            modifier(ArriveOnce(key: key, delay: clock, book: book, blur: 2, duration: Motion.Dur.arrive))
        } else if index >= 0 && index < 3 && order < 2 {
            modifier(ArriveOnce(key: key, delay: clock + Motion.Dur.arriveLead + Double(index) * Motion.Dur.stagger,
                                book: book, blur: 2, duration: Motion.Dur.arrive))
        } else {
            self
        }
    }
}

// MARK: - The tab icon no longer pops (`tabPop` deleted, §640)

/// Round three (§640): the tab icon never pops. Kept as a no-op so the tab
/// bar's call reads as before.
struct TabIconPop: ViewModifier {
    let on: Bool

    func body(content: Content) -> some View { content }
}

extension View {
    func tabIconPop(_ on: Bool) -> some View { modifier(TabIconPop(on: on)) }
}

// MARK: - The player grows out of the tapped picture

/// A picture on screen: what it shows, where it is (in the window), and
/// the corner it is drawn with.
struct PictureSpot {
    let url: URL?
    let frame: CGRect
    let radius: CGFloat
}

/// The pictures inside one button's label, kept out of SwiftUI's state so a
/// scroll redraws nothing. The press asks it for the one to grow from.
final class PressSlot {
    private var spots: [UUID: PictureSpot] = [:]
    /// The button's own frame, in the window.
    var frame: CGRect = .zero

    func set(_ id: UUID, _ spot: PictureSpot) { spots[id] = spot }
    func remove(_ id: UUID) { spots[id] = nil }

    /// The largest picture of the button that is on screen (`pictured`).
    var best: PictureSpot? {
        let screen = UIScreen.main.bounds
        return spots.values
            .filter { $0.frame.width > 0 && $0.frame.intersects(screen) }
            .max { $0.frame.width * $0.frame.height < $1.frame.width * $1.frame.height }
    }
}

private struct PressSlotKey: EnvironmentKey {
    static let defaultValue: PressSlot? = nil
}

extension EnvironmentValues {
    /// The pressable label a picture is drawn inside, if any.
    var pressSlot: PressSlot? {
        get { self[PressSlotKey.self] }
        set { self[PressSlotKey.self] = newValue }
    }
}

/// What was tapped (`Motion`'s `tapped`): the picture inside whatever was
/// last pressed, held for eight seconds - a tile whose episode is still
/// being written opens the player when the first words arrive, not on the
/// tap. Every press replaces it, a press with no picture by nothing. Also
/// where the mini player's picture is, for the player to shrink into.
final class PlayerMotion {
    static let shared = PlayerMotion()

    private var tapped: (spot: PictureSpot?, at: Date)?
    /// The button last pressed, where it was (`Motion`'s `pressed`).
    private var button: (frame: CGRect, at: Date)?
    /// The mini player's picture, while one is drawn.
    var mini: PictureSpot?
    /// Each DailyFAM card's frame, by tile id: a tile queued from its ⋯
    /// flies from the card (`addToQueue`'s `[data-topic]`).
    var tiles: [String: CGRect] = [:]
    /// The search loader's chevrons, in the window: the iris into the
    /// player opens from their middle (§643 `loader-to-player` C).
    var chevrons: CGRect = .zero
    /// The DailyFAM wait's picture, in the window: the player grows out of
    /// it rather than the tile under the loader (§641 `tile-to-player` A).
    var dial: PictureSpot?

    func pressed(_ spot: PictureSpot?) { tapped = (spot, Date()) }
    func pressedButton(_ frame: CGRect) { button = (frame, Date()) }

    /// The face a story grows out of (`pressedCircle`): the button pressed
    /// in the last 1.5 s, as the circle in its middle, or nothing.
    func pressedCircle() -> CGRect? {
        guard let button, Date().timeIntervalSince(button.at) < 1.5,
              button.frame.width > 0, button.frame.intersects(UIScreen.main.bounds) else { return nil }
        let side = max(16, min(button.frame.width, button.frame.height))
        return CGRect(x: button.frame.midX - side / 2, y: button.frame.midY - side / 2, width: side, height: side)
    }

    /// The picture to grow from, if one was pressed in the last 8 s.
    func growFrom() -> PictureSpot? {
        guard let tapped, Date().timeIntervalSince(tapped.at) < 8 else { return nil }
        return tapped.spot
    }
}

/// Gives a button's label a slot its pictures report to, and tells
/// `PlayerMotion` which picture a press landed on.
struct PressRecording: ViewModifier {
    let pressed: Bool
    @State private var slot = PressSlot()

    func body(content: Content) -> some View {
        content
            .environment(\.pressSlot, slot)
            .background(GeometryReader { geo in
                let frame = geo.frame(in: .global)
                Color.clear
                    .onAppear { slot.frame = frame }
                    .onChange(of: frame) { _, now in slot.frame = now }
            })
            .onChange(of: pressed) { _, down in
                guard down else { return }
                PlayerMotion.shared.pressed(slot.best)
                PlayerMotion.shared.pressedButton(slot.frame)
            }
    }
}

/// A picture that the player may grow out of when its button is pressed.
/// Reports its frame to the button around it; nothing outside a button.
struct TapSpot: ViewModifier {
    let url: URL?
    var radius: CGFloat = 10
    @Environment(\.pressSlot) private var slot
    @State private var id = UUID()

    @ViewBuilder
    func body(content: Content) -> some View {
        if let slot, url != nil {
            content
                .background(GeometryReader { geo in
                    let frame = geo.frame(in: .global)
                    Color.clear
                        .onAppear { slot.set(id, PictureSpot(url: url, frame: frame, radius: radius)) }
                        .onChange(of: frame) { _, now in
                            slot.set(id, PictureSpot(url: url, frame: now, radius: radius))
                        }
                })
                .onDisappear { slot.remove(id) }
        } else {
            content
        }
    }
}

extension View {
    func tapSpot(_ url: URL?, radius: CGFloat = 10) -> some View {
        modifier(TapSpot(url: url, radius: radius))
    }
}

/// How the player screen comes and goes (`Motion.screen`, "player-open" /
/// "player-close"), drawn inside a cover iOS shows and hides without its
/// slide:
///
/// - open from a picture: a copy of it flies from where it was to the
///   player's picture (380 ms) while the player fades in (240 ms); the
///   player's own picture is hidden under the copy until it lands
/// - open with no picture: pushed in from the right (320 ms)
/// - close with the mini player there: the player's picture flies into the
///   mini player's (340 ms, ease-in-out) while the player fades (200 ms)
/// - close without it: slid away to the right (260 ms)
/// - Reduce Motion: instant either way (round three, §640)
///
/// The player is live and tappable from its first frame; audio started on
/// the tap. Nothing here holds anything (`motion`).
enum PlayerStagePhase: Equatable {
    case opening
    case shown
    case closing
}

final class PlayerStage: ObservableObject {
    @Published var phase: PlayerStagePhase = .shown
    /// The screen under a pushed player (no picture to grow from): 1 while
    /// it is pushed aside (`slide(from, "0%", "-28%", …, [1, 0.55])`).
    @Published var under: CGFloat = 0
    var underCurve: Animation? = nil
    /// Where the player's picture is, reported by `PlayerView`.
    var pictureFrame: CGRect = .zero
    /// The picture the open grows from, and the one the close shrinks into.
    var from: PictureSpot?
    var into: PictureSpot?
    /// The search loader handed over (§643 `loader-to-player` C): the
    /// player opens through a hole growing from this point, in the window
    /// - the loader's chevrons - over 340 ms on `--ease-in-out`.
    var iris: CGPoint?

    /// How long the close takes before the cover can go.
    func closeDuration(reduceMotion: Bool) -> Double {
        if reduceMotion { return 0 }
        return into == nil ? 0.26 : 0.36
    }
}

struct PlayerEntrance<Content: View>: View {
    @ObservedObject var stage: PlayerStage
    @ViewBuilder let content: () -> Content
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// 0 → 1 as the player arrives; 1 → 0 as it leaves.
    @State private var shown: CGFloat = 0
    /// The copy in flight: 0 at its start, 1 where it lands.
    @State private var flight: CGFloat = 0
    @State private var flying: (from: PictureSpot, to: PictureSpot)?
    @State private var entered = false
    /// The iris's radius while it opens; nil draws the player whole.
    @State private var irisRadius: CGFloat?
    @State private var irisCentre: CGPoint = .zero

    private var width: CGFloat { UIScreen.main.bounds.width }

    var body: some View {
        content()
            .environment(\.playerPictureHidden, flying != nil)
            .opacity(opacity)
            .offset(x: offset)
            .mask { irisMask }
            .overlay {
                // The copy is placed in the window's points, whatever this
                // layer's own origin is.
                GeometryReader { geo in
                    let origin = geo.frame(in: .global).origin
                    if let flying {
                        ghost(flying.from, flying.to)
                            .offset(x: -origin.x, y: -origin.y)
                    }
                }
                .allowsHitTesting(false)
            }
            // Leaving, it takes no more taps: what is under it is next.
            .allowsHitTesting(stage.phase != .closing)
            // Once: closing a cover the player opened over it (VIBE!, your
            // story) brings it back on screen, which is not an entrance.
            .onAppear {
                guard !entered else { return }
                entered = true
                open()
            }
            .onChange(of: stage.phase) { _, phase in
                if phase == .closing { close() }
                if phase == .shown { settle() }
            }
    }

    /// The pushed / popped page's place: from the right, back to it.
    private var slides: Bool {
        !reduceMotion && flyingKind == .none
    }

    private enum Kind { case none, grow, shrink, iris }

    /// The hole the player shows through while the iris opens.
    @ViewBuilder private var irisMask: some View {
        if let r = irisRadius {
            GeometryReader { geo in
                let origin = geo.frame(in: .global).origin
                RoundHole(centre: CGPoint(x: irisCentre.x - origin.x, y: irisCentre.y - origin.y), radius: r)
            }
            .ignoresSafeArea()
        } else {
            Rectangle().ignoresSafeArea()
        }
    }
    @State private var flyingKind: Kind = .none

    private var opacity: CGFloat {
        if slides { return 1 }
        return shown
    }

    private var offset: CGFloat {
        guard slides else { return 0 }
        return (1 - shown) * width
    }

    private func open() {
        if !reduceMotion, let centre = stage.iris {
            // The loader opens from its chevrons outward; the player is
            // whole under it from the first frame.
            flyingKind = .iris
            shown = 1
            let screen = UIScreen.main.bounds
            let far = [CGPoint(x: 0, y: 0), CGPoint(x: screen.maxX, y: 0),
                       CGPoint(x: 0, y: screen.maxY), CGPoint(x: screen.maxX, y: screen.maxY)]
                .map { (($0.x - centre.x) * ($0.x - centre.x) + ($0.y - centre.y) * ($0.y - centre.y)).squareRoot() }
                .max() ?? screen.height
            var still = Transaction()
            still.disablesAnimations = true
            withTransaction(still) {
                irisCentre = centre
                irisRadius = 0.5
            }
            DispatchQueue.main.async {
                withAnimation(Motion.easeInOut(0.34)) { irisRadius = far + 2 }
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.36) {
                withTransaction(still) { irisRadius = nil }
            }
        } else if !reduceMotion, let from = stage.from {
            flyingKind = .grow
            DispatchQueue.main.async {
                // The player's picture has been laid out by now.
                let to = PictureSpot(url: from.url, frame: stage.pictureFrame, radius: 0)
                // Round three (§640 `player-open` A): 360 ms on
                // `--ease-in-out` both ways; the player fades in over 240.
                if to.frame != .zero { fly(from, to, duration: 0.36, curve: Motion.easeInOut(0.36)) }
                withAnimation(Motion.easeOut(0.24)) { shown = 1 }
            }
        } else if reduceMotion {
            // Round three: instant (§640).
            flyingKind = .none
            shown = 1
        } else {
            flyingKind = .none
            let curve = Motion.easeOut(Motion.Dur.page)
            withAnimation(curve) { shown = 1 }
            stage.underCurve = curve
            stage.under = 1
            // Once it is in, the page under is put back where it was,
            // unseen (the web's push ends by cancelling its animations), so
            // a close that shrinks into the mini player finds it in place.
            DispatchQueue.main.asyncAfter(deadline: .now() + Motion.Dur.page) {
                guard stage.phase != .closing else { return }
                stage.underCurve = nil
                stage.under = 0
            }
        }
    }

    private func close() {
        if reduceMotion {
            shown = 0
            return
        }
        if let into = stage.into, stage.pictureFrame != .zero {
            flyingKind = .shrink
            let from = PictureSpot(url: into.url, frame: stage.pictureFrame, radius: 0)
            fly(from, into, duration: 0.36, curve: Motion.easeInOut(0.36))
            withAnimation(Motion.easeIn(0.2)) { shown = 0 }
        } else {
            flyingKind = .none
            let curve = Motion.easeOut(Motion.Dur.pageBack)
            withAnimation(curve) { shown = 0 }
            // The pop: the page under comes back from 28 % left as this goes.
            stage.underCurve = nil
            stage.under = 1
            DispatchQueue.main.async {
                stage.underCurve = curve
                stage.under = 0
            }
        }
    }

    /// The copy is drawn where it starts, then moved on the next frame (a
    /// view inserted and moved in one change would appear where it ends).
    private func fly(_ from: PictureSpot, _ to: PictureSpot, duration: Double, curve: Animation) {
        var still = Transaction()
        still.disablesAnimations = true
        withTransaction(still) {
            flight = 0
            flying = (from, to)
        }
        DispatchQueue.main.async {
            withAnimation(curve) { flight = 1 }
        }
        // Landed: the copy goes, and the real picture is there under it.
        // A close leaves it for the cover to take away.
        DispatchQueue.main.asyncAfter(deadline: .now() + duration + 0.02) {
            if stage.phase != .closing { flying = nil }
        }
    }

    /// Opened again before the close finished: back as it was.
    private func settle() {
        flying = nil
        withAnimation(.easeOut(duration: 0.2)) { shown = 1 }
        if flyingKind == .none && !reduceMotion {
            stage.underCurve = nil
            stage.under = 0
        }
    }

    /// The copy: the picture, moved and sized between the two frames, its
    /// corner rounding between theirs.
    private func ghost(_ a: PictureSpot, _ b: PictureSpot) -> some View {
        let t = flight
        let x = a.frame.minX + (b.frame.minX - a.frame.minX) * t
        let y = a.frame.minY + (b.frame.minY - a.frame.minY) * t
        let w = a.frame.width + (b.frame.width - a.frame.width) * t
        let h = a.frame.height + (b.frame.height - a.frame.height) * t
        let r = a.radius + (b.radius - a.radius) * t
        return ZStack {
            FAMColor.thumb
            if let url = a.url ?? b.url {
                AsyncImage(url: url) { $0.resizable().scaledToFill() } placeholder: { Color.clear }
            }
        }
        .frame(width: w, height: h)
        .clipShape(RoundedRectangle(cornerRadius: r))
        .offset(x: x, y: y)
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

private struct PlayerPictureHiddenKey: EnvironmentKey {
    static let defaultValue = false
}

extension EnvironmentValues {
    /// The player's picture is under a copy in flight: draw it hidden.
    var playerPictureHidden: Bool {
        get { self[PlayerPictureHiddenKey.self] }
        set { self[PlayerPictureHiddenKey.self] = newValue }
    }
}

// MARK: - Screens push in from the right

/// A screen shown over the app as a cover (`Motion.screen`'s "push" and
/// "pop"): it comes in from the right over 320 ms and goes back out to it
/// over 260 ms, as the web's screens and iOS's own pushes do - never up
/// from the bottom, which is iOS's cover and not a screen. The cover is
/// shown and hidden without iOS's slide; what is seen moving is drawn
/// here.
/// The screen is live from its first frame. Reduce Motion: in and gone at
/// once (round three, §640).
final class PushedState: ObservableObject {
    @Published var leaving = false
    /// 0 with nothing pushed, 1 with the screen fully in: the pushed
    /// screen and the one under it both move by it, so they move together.
    @Published var shown: CGFloat = 0
    /// How the last change to `shown` moves (the screen under the cover is
    /// in another presentation, so it is given the same curve itself).
    var curve: Animation? = nil

    /// The push's own curve (340 ms) and the pop's (260 ms), both
    /// `--ease-out` (round three, §640).
    static let pushCurve = Motion.easeOut(Motion.Dur.page)
    static let popCurve = Motion.easeOut(Motion.Dur.pageBack)
    static let settleCurve = Animation.timingCurve(0.2, 0.8, 0.2, 1, duration: 0.2)

    func move(to value: CGFloat, _ curve: Animation?) {
        self.curve = curve
        withAnimation(curve) { shown = value }
    }
}

private struct PushedScreen<Content: View>: View {
    @ObservedObject var state: PushedState
    let close: () -> Void
    let content: Content
    /// A page sheet (round three, §645 `edit-profile-open` B): up from the
    /// bottom edge (300 ms on `--ease-out`) and back down (220 ms on
    /// `--ease-in`) rather than in from the right.
    var vertical = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var width: CGFloat { UIScreen.main.bounds.width }
    private var height: CGFloat { UIScreen.main.bounds.height }

    var body: some View {
        let away = reduceMotion ? 0 : 1 - state.shown
        content
            .background(FAMColor.background.ignoresSafeArea())
            .opacity(reduceMotion ? state.shown : 1)
            .offset(x: vertical ? 0 : away * width, y: vertical ? away * height : 0)
            // Back by the left edge, 1:1 under the finger (round three,
            // §640 `screens` B): a drag that starts within 24 pt of the
            // edge and goes sideways moves the screen and the one under it;
            // let go past half way, or flicked faster than 0.1 pt/ms, it
            // goes back; else it springs home. Taps still reach what is
            // there. Reduce Motion: the drag follows, the end is at once.
            // The strip is the 14 pt along the edge, under nothing pressable.
            .overlay(alignment: .leading) {
                if !vertical {
                    Color.clear
                        .frame(width: 14)
                        .frame(maxHeight: .infinity)
                        .contentShape(Rectangle())
                        .gesture(edgeBack)
                        .ignoresSafeArea()
                        .accessibilityHidden(true)
                }
            }
            .allowsHitTesting(!state.leaving)
            .onAppear {
                state.move(to: 1, reduceMotion ? nil : (vertical ? Motion.easeOut(0.3) : PushedState.pushCurve))
            }
            .onChange(of: state.leaving) { _, leaving in
                guard leaving else {
                    // Asked for again before it had gone: back in place.
                    state.move(to: 1, PushedState.settleCurve)
                    return
                }
                if reduceMotion { return }
                state.move(to: 0, vertical ? Motion.easeIn(0.22) : PushedState.popCurve)
            }
    }

    private var edgeBack: some Gesture {
        DragGesture(minimumDistance: 10, coordinateSpace: .global)
            .onChanged { g in
                guard !vertical, !state.leaving else { return }
                let x = max(0, g.translation.width)
                state.move(to: max(0, 1 - x / width), nil)
            }
            .onEnded { g in
                guard !vertical, !state.leaving else { return }
                let x = max(0, g.translation.width)
                if x > 0 && (x > width / 2 || g.velocity.width > 100) {
                    close()
                } else if state.shown < 1 {
                    state.move(to: 1, reduceMotion ? nil : Animation.spring(duration: 0.4, bounce: 0))
                }
            }
    }
}

/// The screen a push covers (`slide(from, "0%", "-28%", …, [1, 0.55])`):
/// it moves 28 % of the way left and dims to 55 % as the new one comes in,
/// and comes back as it goes.
/// Only the view the cover hangs from moves - the tab bar and the mini
/// player, outside it, stay where they are, as the web counter-moves them.
/// Still with Reduce Motion.
private struct UnderPush: ViewModifier {
    @ObservedObject var state: PushedState
    /// Under a page sheet the page recedes instead: .97 and 10 % darker.
    var vertical = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    func body(content: Content) -> some View {
        let width = UIScreen.main.bounds.width
        let t = reduceMotion ? 0 : state.shown
        content
            .offset(x: vertical ? 0 : -0.28 * width * t)
            .scaleEffect(vertical ? 1 - 0.03 * t : 1)
            .brightness(vertical ? -0.1 * Double(t) : 0)
            .opacity(vertical ? 1 : Double(1 - 0.45 * t))
            .animation(state.curve, value: state.shown)
    }
}

/// One presented screen's identity, for the `isPresented:` form.
struct ScreenFlag: Identifiable, Equatable {
    var id: Int { 0 }
}

private struct ScreenCover<Item: Identifiable, Screen: View>: ViewModifier {
    @Binding var item: Item?
    let screen: (Item) -> Screen
    var vertical = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// What the cover shows: the item, held while it slides away.
    @State private var held: Item?
    @StateObject private var state = PushedState()
    /// Each open or close, so a close's timer that a reopen overtook does
    /// nothing.
    @State private var run = 0

    func body(content: Content) -> some View {
        content
            .modifier(UnderPush(state: state, vertical: vertical))
            .onChange(of: item?.id) { _, _ in follow() }
            .onAppear { if item != nil && held == nil { follow() } }
            .fullScreenCover(item: Binding(get: { held }, set: { new in
                // A `dismiss()` inside the screen: it slides away first.
                if new == nil && item != nil { item = nil }
            })) { shown in
                PushedScreen(state: state, close: { item = nil }, content: screen(shown), vertical: vertical)
                    .famThemed()
                    .presentationBackground(.clear)
                    // The tour waits behind it and pauses under it (§594).
                    .tourCover()
            }
    }

    private func follow() {
        var still = Transaction()
        still.disablesAnimations = true
        run += 1
        let mine = run
        if let item {
            state.leaving = false
            withTransaction(still) { held = item }
        } else if held != nil, !state.leaving {
            state.leaving = true
            let wait = reduceMotion ? 0 : (vertical ? 0.22 : 0.26)
            DispatchQueue.main.asyncAfter(deadline: .now() + wait) {
                guard self.item == nil, mine == run else { return }
                withTransaction(still) { held = nil }
                // `leaving` stays set until the next push (the screen still
                // alive through its dismissal would read a reset as "asked
                // for again" and come back). Gone: the screen under is home.
                var none = Transaction()
                none.disablesAnimations = true
                withTransaction(none) { state.shown = 0 }
            }
        }
    }
}

extension View {
    /// `.fullScreenCover(item:)` for a screen: pushed in from the right.
    func screenCover<Item: Identifiable, Screen: View>(
        item: Binding<Item?>, @ViewBuilder content: @escaping (Item) -> Screen
    ) -> some View {
        modifier(ScreenCover(item: item, screen: content))
    }

    /// `.fullScreenCover(isPresented:)` for a screen: pushed in from the right.
    func screenCover<Screen: View>(
        isPresented: Binding<Bool>, @ViewBuilder content: @escaping () -> Screen
    ) -> some View {
        let flag = Binding<ScreenFlag?>(get: { isPresented.wrappedValue ? ScreenFlag() : nil },
                                        set: { isPresented.wrappedValue = $0 != nil })
        return modifier(ScreenCover(item: flag, screen: { _ in content() }))
    }

    /// A page sheet over the screen (Edit profile, Where you are and Your
    /// interests from Settings and yourFAM, §645 `edit-profile-open` B):
    /// up from the bottom edge over a page that recedes to .97.
    func pageSheetCover<Screen: View>(
        isPresented: Binding<Bool>, @ViewBuilder content: @escaping () -> Screen
    ) -> some View {
        let flag = Binding<ScreenFlag?>(get: { isPresented.wrappedValue ? ScreenFlag() : nil },
                                        set: { isPresented.wrappedValue = $0 != nil })
        return modifier(ScreenCover(item: flag, screen: { _ in content() }, vertical: true))
    }
}

// MARK: - Sheets and modals fade and lift

/// A presented card says it is the web's centred `.modal-card` rather than
/// a bottom sheet, so the overlay draws no panel around it.
struct ModalLayoutKey: PreferenceKey {
    static let defaultValue = false
    static func reduce(value: inout Bool, nextValue: () -> Bool) { value = value || nextValue() }
}

extension View {
    /// This presented view lays itself out as a centred card.
    func modalLayout() -> some View { preference(key: ModalLayoutKey.self, value: true) }
}

final class OverlayState: ObservableObject {
    @Published var leaving = false
}

/// The window's own bottom inset (the home indicator), for a panel that
/// runs down behind it.
private var windowBottomInset: CGFloat {
    let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
    let window = scenes.flatMap(\.windows).first { $0.isKeyWindow } ?? scenes.first?.windows.first
    return window?.safeAreaInsets.bottom ?? 0
}

/// Every sheet and modal (`.sheet-overlay`, `.modal-overlay` and
/// `Motion.watchOverlays`): the dim (`rgba(10,9,15,.62)`) fades in as a
/// sheet slides up from the bottom edge (280 ms) or a modal grows from .97
/// (220 ms); closing, 200 / 160 ms on `--ease-in` (round three). A sheet is the web's
/// `.sheet-card`: the surface, 20 pt top corners and a strong top border,
/// at most 82 % of the screen, sized to what it holds; a tap on the dim
/// closes it. Reduce Motion: there at once, nothing lifts, closing is at
/// once (round three, §640). The card is
/// live from its first frame.
private struct OverlayCard<Content: View>: View {
    @ObservedObject var state: OverlayState
    let close: () -> Void
    let content: Content
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var shown = false
    @State private var modal = false

    private static var dim: Color { Color(hex: 0x0A090F).opacity(0.62) }

    var body: some View {
        ZStack(alignment: .bottom) {
            Self.dim
                .ignoresSafeArea()
                .opacity(shown ? 1 : 0)
                .contentShape(Rectangle())
                .onTapGesture { if !modal { close() } }
                .accessibilityHidden(true)
            card
                // Round three (§640 `sheets` A): a sheet slides all the way
                // up from the bottom edge (280 ms on `--ease-out`) and back
                // down (200 ms on `--ease-in`); a centred modal fades in
                // from .97 (220 ms) and out (160 ms). The dim follows.
                .opacity(modal ? (shown ? 1 : 0) : 1)
                .scaleEffect(modal && !shown && !reduceMotion ? 0.97 : 1)
                .offset(y: lift)
        }
        .ignoresSafeArea(.container, edges: .bottom)
        .allowsHitTesting(!state.leaving)
        .onPreferenceChange(ModalLayoutKey.self) { modal = $0 }
        .onAppear {
            withAnimation(reduceMotion ? nil : Motion.easeOut(modal ? Motion.Dur.modalIn : Motion.Dur.sheetIn)) {
                shown = true
            }
        }
        .onChange(of: state.leaving) { _, leaving in
            if !leaving {
                withAnimation(Motion.anim(Motion.easeOut(Motion.Dur.modalIn), reduce: reduceMotion)) { shown = true }
                return
            }
            guard !reduceMotion else { return }
            withAnimation(Motion.easeIn(modal ? Motion.Dur.modalOut : Motion.Dur.sheetOut)) { shown = false }
        }
    }

    /// A sheet below the screen's bottom edge until it is shown.
    private var lift: CGFloat {
        if reduceMotion || shown || modal { return 0 }
        return UIScreen.main.bounds.height
    }

    /// One chain for both shapes, so a card that turns out to be a modal
    /// on its first frame keeps its identity (and its state).
    private var card: some View {
        let corners = UnevenRoundedRectangle(topLeadingRadius: 20, topTrailingRadius: 20)
        return UpTo(cap: modal ? .infinity : UIScreen.main.bounds.height * 0.82) {
            content.frame(maxWidth: .infinity)
        }
            .padding(.bottom, modal ? 0 : windowBottomInset)
            .background(modal ? Color.clear : FAMColor.surface)
            .clipShape(modal ? AnyShape(Rectangle()) : AnyShape(corners))
            .overlay(corners.stroke(FAMColor.borderStrong, lineWidth: 1)
                .padding(.bottom, -2)
                .opacity(modal ? 0 : 1)
                .allowsHitTesting(false))
    }
}

private struct OverlayCover<Item: Identifiable, Card: View>: ViewModifier {
    @Binding var item: Item?
    let onDismiss: (() -> Void)?
    let card: (Item) -> Card
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var held: Item?
    @StateObject private var state = OverlayState()
    /// Each open or close, so a close's timer that a reopen overtook does
    /// nothing.
    @State private var run = 0

    func body(content: Content) -> some View {
        content
            .onChange(of: item?.id) { _, _ in follow() }
            .onAppear { if item != nil && held == nil { follow() } }
            .fullScreenCover(item: Binding(get: { held }, set: { new in
                // A `dismiss()` inside the card: it fades and drops first.
                if new == nil && item != nil { item = nil }
            }), onDismiss: onDismiss) { shown in
                OverlayCard(state: state, close: { item = nil }, content: card(shown))
                    .famThemed()
                    .presentationBackground(.clear)
                    // The tour waits behind it and pauses under it (§594).
                    .tourCover()
            }
    }

    private func follow() {
        var still = Transaction()
        still.disablesAnimations = true
        run += 1
        let mine = run
        if let item {
            state.leaving = false
            withTransaction(still) { held = item }
        } else if held != nil, !state.leaving {
            state.leaving = true
            let wait = reduceMotion ? 0 : Motion.Dur.sheetOut
            DispatchQueue.main.asyncAfter(deadline: .now() + wait) {
                guard self.item == nil, mine == run else { return }
                withTransaction(still) { held = nil }
                // `leaving` stays set until the next one opens, so the card
                // never fades back in while its dismissal is processed.
            }
        }
    }
}

extension View {
    /// `.sheet(item:)` drawn as the web's: fades and lifts (`watchOverlays`).
    func famSheet<Item: Identifiable, Card: View>(
        item: Binding<Item?>, onDismiss: (() -> Void)? = nil,
        @ViewBuilder content: @escaping (Item) -> Card
    ) -> some View {
        modifier(OverlayCover(item: item, onDismiss: onDismiss, card: content))
    }

    /// `.sheet(isPresented:)` drawn as the web's: fades and lifts.
    func famSheet<Card: View>(
        isPresented: Binding<Bool>, onDismiss: (() -> Void)? = nil,
        @ViewBuilder content: @escaping () -> Card
    ) -> some View {
        let flag = Binding<ScreenFlag?>(get: { isPresented.wrappedValue ? ScreenFlag() : nil },
                                        set: { isPresented.wrappedValue = $0 != nil })
        return modifier(OverlayCover(item: flag, onDismiss: onDismiss, card: { _ in content() }))
    }
}

// MARK: - Small pictures fade in as they land

/// A small picture that is clear until it has loaded, then fades in
/// (`.src-mark img`, `.src-logo img`: 160 ms; `.mix-face img`: 200 ms),
/// so a slow or missing one leaves what is drawn under it.
struct FadeInImage<Shown: View>: View {
    let url: URL
    var duration: Double = 0.16
    @ViewBuilder let shown: (Image) -> Shown

    var body: some View {
        AsyncImage(url: url, transaction: Transaction(animation: .easeInOut(duration: duration))) { phase in
            if let image = phase.image {
                shown(image).transition(.opacity)
            } else {
                Color.clear
            }
        }
    }
}

/// Something that fades in once, as it first appears (a CSS transition on
/// a class added with it). Still with Reduce Motion, as the web's are.
struct FadesIn<Content: View>: View {
    var duration: Double = 0.2
    @ViewBuilder let content: () -> Content
    @State private var on = false

    var body: some View {
        content()
            .opacity(on ? 1 : 0)
            .onAppear { withAnimation(.easeInOut(duration: duration)) { on = true } }
    }
}

// MARK: - The web's modal timings, for overlays drawn in place

extension AnyTransition {
    /// A card coming and going as the web's modals do (`mo-lift`, `mo-drop`):
    /// in over 200 ms, lifting 14 pt as it fades; out over 150 ms, easing
    /// in, dropping 8 pt. Reduce Motion: there and gone at once (§640).
    static func famCard(reduceMotion: Bool) -> AnyTransition {
        if reduceMotion { return .identity }
        return .asymmetric(
            insertion: AnyTransition.opacity.combined(with: .offset(y: 14))
                .animation(.timingCurve(0.2, 0.8, 0.2, 1, duration: 0.2)),
            removal: AnyTransition.opacity.combined(with: .offset(y: 8))
                .animation(.timingCurve(0.4, 0, 1, 1, duration: 0.15)))
    }

    /// The dim behind it (`mo-in`, `mo-out`): 200 ms in, 150 ms out.
    static func famDim(reduceMotion: Bool) -> AnyTransition {
        if reduceMotion { return .identity }
        return .asymmetric(
            insertion: AnyTransition.opacity.animation(.timingCurve(0.2, 0.8, 0.2, 1, duration: 0.2)),
            removal: AnyTransition.opacity.animation(.timingCurve(0.4, 0, 1, 1, duration: 0.15)))
    }
}

/// A card drawn in place over its own dim, closing (`mo-drop`): the whole
/// overlay fades over 150 ms, easing in, and the card inside drops 8 pt as
/// it does (`dropsOnLeave`). Opening is `famDim`'s, the card lifting
/// (`liftsIn`). Reduce Motion: `famDim`'s, nothing drops.
private struct FamCardLeavingKey: EnvironmentKey {
    static let defaultValue = false
}

extension EnvironmentValues {
    /// The overlay this card is in is closing.
    var famCardLeaving: Bool {
        get { self[FamCardLeavingKey.self] }
        set { self[FamCardLeavingKey.self] = newValue }
    }
}

private struct OverlayLeaving: ViewModifier {
    let leaving: Bool

    func body(content: Content) -> some View {
        content
            .opacity(leaving ? 0 : 1)
            .environment(\.famCardLeaving, leaving)
    }
}

private struct DropsOnLeave: ViewModifier {
    @Environment(\.famCardLeaving) private var leaving

    func body(content: Content) -> some View {
        content.offset(y: leaving ? 8 : 0)
    }
}

extension AnyTransition {
    static func famOverlay(reduceMotion: Bool) -> AnyTransition {
        if reduceMotion { return famDim(reduceMotion: true) }
        return .asymmetric(
            insertion: famDim(reduceMotion: false),
            removal: AnyTransition.modifier(active: OverlayLeaving(leaving: true),
                                            identity: OverlayLeaving(leaving: false))
                .animation(.timingCurve(0.4, 0, 1, 1, duration: 0.15)))
    }
}

extension View {
    /// Drops 8 pt while the overlay it is in closes (`famOverlay`).
    func dropsOnLeave() -> some View { modifier(DropsOnLeave()) }
}

/// A card lifting 14 pt into place as it first appears (`mo-lift`, 200 ms),
/// for a card drawn inside its own dim. Still with Reduce Motion.
struct LiftsIn: ViewModifier {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var lifted = false

    func body(content: Content) -> some View {
        content
            .offset(y: lifted || reduceMotion ? 0 : 14)
            .onAppear {
                withAnimation(.timingCurve(0.2, 0.8, 0.2, 1, duration: 0.2)) { lifted = true }
            }
    }
}

extension View {
    func liftsIn() -> some View { modifier(LiftsIn()) }
}

/// The tab's page under a player pushed in from the right: 28 % left and
/// dimmed to 55 %, back as it goes (the tab bar and mini player stay put).
struct UnderPlayer: ViewModifier {
    @ObservedObject var stage: PlayerStage

    func body(content: Content) -> some View {
        let width = UIScreen.main.bounds.width
        content
            .offset(x: -0.28 * width * stage.under)
            .opacity(Double(1 - 0.45 * stage.under))
            .animation(stage.underCurve, value: stage.under)
    }
}

/// Opens a story out of the face just pressed (`growStory`), as DailyFAM's
/// faces row does: the cover comes up without iOS's slide and the story
/// grows from the face. With no face pressed, or Reduce Motion, it is
/// iOS's own cover.
func presentGrowing(reel: inout StoryReel, show: (StoryReel) -> Void) {
    guard !UIAccessibility.isReduceMotionEnabled, let face = PlayerMotion.shared.pressedCircle() else {
        show(reel)
        return
    }
    reel.origin = face
    var still = Transaction()
    still.disablesAnimations = true
    let shown = reel
    withTransaction(still) { show(shown) }
}

/// The story editor growing out of the (+) or face just pressed (round
/// three, §645 `story-editor`, opening as A): a circle from the press
/// widening past the screen's corners, 300 ms on `--ease-out`, inside a
/// cover shown without iOS's slide over a clear background. With nothing
/// pressed, or Reduce Motion, it is simply there.
struct GrowsFromPress<Content: View>: View {
    @ViewBuilder let content: () -> Content
    @State private var origin: CGPoint? = GrowsFromPressOrigin.pressed()
    @State private var open = false

    var body: some View {
        let screen = UIScreen.main.bounds
        let c = origin ?? CGPoint(x: screen.midX, y: screen.midY)
        let far = [CGPoint(x: 0, y: 0), CGPoint(x: screen.maxX, y: 0),
                   CGPoint(x: 0, y: screen.maxY), CGPoint(x: screen.maxX, y: screen.maxY)]
            .map { (($0.x - c.x) * ($0.x - c.x) + ($0.y - c.y) * ($0.y - c.y)).squareRoot() }
            .max() ?? screen.height
        content()
            .modifier(CircleReveal(centre: c, radius: (origin == nil || open) ? far + 2 : 20))
            .onAppear {
                guard origin != nil else { return }
                withAnimation(Motion.easeOut(0.3)) { open = true }
            }
    }
}

enum GrowsFromPressOrigin {
    /// The middle of the button pressed a moment ago, unless Reduce Motion.
    static func pressed() -> CGPoint? {
        guard !Motion.reduced, let face = PlayerMotion.shared.pressedCircle() else { return nil }
        return CGPoint(x: face.midX, y: face.midY)
    }

    /// Present with no slide when the editor will grow out of a press.
    static func present(_ show: () -> Void) {
        var t = Transaction()
        t.disablesAnimations = pressed() != nil
        withTransaction(t) { show() }
    }
}

/// Sizes its one child to the child's own height, never more than `cap`
/// (`.sheet-card`'s `max-height:82%` with its height from what it holds):
/// a plain `.frame(maxHeight:)` would take the whole cap instead.
private struct UpTo: Layout {
    let cap: CGFloat

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? UIScreen.main.bounds.width
        let room = min(proposal.height ?? cap, cap)
        let height = subviews.first?.sizeThatFits(ProposedViewSize(width: width, height: room)).height ?? 0
        return CGSize(width: width, height: min(height, room))
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        subviews.first?.place(at: bounds.origin, proposal: ProposedViewSize(bounds.size))
    }
}

// MARK: - Round three, phase B: shared pieces (§652)

/// A line whose words or numbers changed since it was last drawn (round
/// three, §641 `changed-since` B): the digits roll to their new value
/// (320 ms on `--ease-in-out`) and the line warms to copper and back over
/// 600 ms, so it is found on a long page. Not on its first draw. Reduce
/// Motion: the new words at once, no warmth.
struct RollingText: View {
    let text: String
    var font: Font
    var color: Color = FAMColor.textMuted
    var tracking: CGFloat = 0
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var warm = false
    @State private var run = 0

    var body: some View {
        Text(text)
            .font(font)
            .tracking(tracking)
            .monospacedDigit()
            .foregroundStyle(warm ? FAMColor.copper : color)
            .contentTransition(reduceMotion ? .identity : .numericText())
            .animation(reduceMotion ? nil : Motion.easeInOut(0.32), value: text)
            .onChange(of: text) { old, _ in
                guard !reduceMotion, !old.isEmpty else { return }
                run += 1
                let mine = run
                withAnimation(.easeInOut(duration: 0.3)) { warm = true }
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                    guard mine == run else { return }
                    withAnimation(.easeInOut(duration: 0.3)) { warm = false }
                }
            }
    }
}

/// A tile put away (§641 `remove-tile` B): it tips back 20° about its top
/// edge and shrinks to .9 as it fades.
private struct FoldAway: ViewModifier {
    let gone: Bool

    func body(content: Content) -> some View {
        content
            .rotation3DEffect(.degrees(gone ? 20 : 0), axis: (x: 1, y: 0, z: 0),
                              anchor: .top, perspective: 0.5)
            .scaleEffect(gone ? 0.9 : 1)
            .opacity(gone ? 0 : 1)
    }
}

extension AnyTransition {
    /// Taking a tile away (Not interested, Pick up's X): it folds away over
    /// 220 ms on `--ease-in` while the others close in; one put back by
    /// Undo rises 8 pt as it fades in. Reduce Motion: at once.
    static var foldAway: AnyTransition {
        if Motion.reduced { return .identity }
        return .asymmetric(
            insertion: AnyTransition.offset(y: 8).combined(with: .opacity)
                .animation(Motion.easeOut(Motion.Dur.cardIn)),
            removal: AnyTransition.modifier(active: FoldAway(gone: true), identity: FoldAway(gone: false))
                .animation(Motion.easeIn(0.22)))
    }
}

extension Animation {
    /// The others closing in after a tile is taken away: 240 ms on
    /// `--ease-in-out`. Reduce Motion: none.
    static var tileGap: Animation? {
        Motion.anim(Motion.easeInOut(0.24))
    }
}

/// A tab's underline that slides between tabs (§642 `playfam-tabs` B,
/// §645 `shelf-switch` A): one copper rule under a row of equal tabs,
/// moved to the chosen one. Reduce Motion: it jumps.
struct SlidingRule: View {
    let count: Int
    let index: Int
    var height: CGFloat = 2
    var inset: CGFloat = 0
    var duration: Double = 0.24
    var color: Color = FAMColor.copper
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        GeometryReader { geo in
            let width = geo.size.width / CGFloat(max(count, 1))
            Capsule()
                .fill(color)
                .frame(width: max(0, width - 2 * inset), height: height)
                .offset(x: width * CGFloat(index) + inset)
                .animation(reduceMotion ? nil : Motion.easeInOut(duration), value: index)
        }
        .frame(height: height)
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

/// The page under a pair of tabs nudging toward the new tab's side
/// (§642/§645): the new content comes 18 pt in from that side as it fades
/// (200 ms on `--ease-out`, after 90 ms), the old leaving 18 pt the other
/// way (110 ms on `--ease-in`). Reduce Motion: swapped at once.
extension AnyTransition {
    static func tabSwap(forward: Bool) -> AnyTransition {
        if Motion.reduced { return .identity }
        let side: CGFloat = forward ? 1 : -1
        return .asymmetric(
            insertion: AnyTransition.offset(x: 18 * side).combined(with: .opacity)
                .animation(Motion.easeOut(0.2).delay(0.09)),
            removal: AnyTransition.offset(x: -18 * side).combined(with: .opacity)
                .animation(Motion.easeIn(0.11)))
    }
}

/// A small dot or badge appearing and going (§642 `morning-dot` A): in from
/// .5 as it fades (240 ms on `--ease-out`), away to .5 (160 ms on
/// `--ease-in`). Reduce Motion: at once.
extension AnyTransition {
    static var dotPop: AnyTransition {
        if Motion.reduced { return .identity }
        return .asymmetric(
            insertion: AnyTransition.scale(scale: 0.5).combined(with: .opacity)
                .animation(Motion.easeOut(0.24)),
            removal: AnyTransition.scale(scale: 0.5).combined(with: .opacity)
                .animation(Motion.easeIn(0.16)))
    }
}

/// A circle at `centre` (in its own space) of `radius`, the radius
/// animating: the iris's hole and voice search's opening.
struct RoundHole: Shape {
    var centre: CGPoint
    var radius: CGFloat

    var animatableData: CGFloat {
        get { radius }
        set { radius = newValue }
    }

    func path(in rect: CGRect) -> Path {
        Path(ellipseIn: CGRect(x: centre.x - radius, y: centre.y - radius, width: 2 * radius, height: 2 * radius))
    }
}

/// Shows its content only inside a circle about a point in the window
/// (voice search growing out of the mic, §643 `voice-sheet` B).
struct CircleReveal: ViewModifier, Animatable {
    let centre: CGPoint
    var radius: CGFloat

    var animatableData: CGFloat {
        get { radius }
        set { radius = newValue }
    }

    func body(content: Content) -> some View {
        content.mask {
            GeometryReader { geo in
                let origin = geo.frame(in: .global).origin
                RoundHole(centre: CGPoint(x: centre.x - origin.x, y: centre.y - origin.y), radius: radius)
            }
            .ignoresSafeArea()
        }
    }
}

extension AnyTransition {
    /// Opens as a circle out of `centre` (20 pt to past the farthest
    /// corner, 300 ms on `--ease-out`) and closes back into it (240 ms on
    /// `--ease-in-out`). Reduce Motion: at once.
    static func circleOut(from centre: CGPoint) -> AnyTransition {
        if Motion.reduced { return .identity }
        let screen = UIScreen.main.bounds
        let far = [CGPoint(x: 0, y: 0), CGPoint(x: screen.maxX, y: 0),
                   CGPoint(x: 0, y: screen.maxY), CGPoint(x: screen.maxX, y: screen.maxY)]
            .map { (($0.x - centre.x) * ($0.x - centre.x) + ($0.y - centre.y) * ($0.y - centre.y)).squareRoot() }
            .max() ?? screen.height
        let shut = CircleReveal(centre: centre, radius: 20)
        let open = CircleReveal(centre: centre, radius: far + 2)
        return .asymmetric(
            insertion: AnyTransition.modifier(active: shut, identity: open).animation(Motion.easeOut(0.3)),
            removal: AnyTransition.modifier(active: shut, identity: open).animation(Motion.easeInOut(0.24)))
    }
}

// MARK: - The edge swipe back inside a tab's own stack

/// SwiftUI's stacks here hide the navigation bar, which turns off iOS's own
/// swipe from the left edge; drawn behind a stack's root, this turns it back
/// on whenever there is a screen to go back to (round three, §640 `screens`
/// B: back 1:1 under the finger, iOS's own).
struct EdgeSwipeBack: UIViewControllerRepresentable {
    func makeUIViewController(context: Context) -> Controller { Controller() }
    func updateUIViewController(_ controller: Controller, context: Context) {}

    final class Controller: UIViewController, UIGestureRecognizerDelegate {
        private weak var stack: UINavigationController?

        override func viewDidAppear(_ animated: Bool) {
            super.viewDidAppear(animated)
            adopt()
        }

        override func didMove(toParent parent: UIViewController?) {
            super.didMove(toParent: parent)
            adopt()
        }

        private func adopt() {
            guard let nav = navigationController else { return }
            stack = nav
            nav.interactivePopGestureRecognizer?.isEnabled = true
            nav.interactivePopGestureRecognizer?.delegate = self
        }

        func gestureRecognizerShouldBegin(_ gestureRecognizer: UIGestureRecognizer) -> Bool {
            (stack?.viewControllers.count ?? 0) > 1
        }
    }
}
