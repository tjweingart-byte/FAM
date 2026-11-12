import SwiftUI
import UIKit

// MARK: - Motion (§292, §298: `Motion` in static/index.html)
//
// The web's owner-chosen motion, as far as the iOS app draws its own: every
// button squeezes and springs back, tabs nudge toward the side the new tab
// is on, new rails arrive tile by tile. Nothing waits on any of it
// (`motion`, `latency-start-earlier`): the button acts on release as it
// always did, the new tab is live from its first frame, a tile is tappable
// while it arrives. With Reduce Motion on, a tab change is the web's 120 ms
// crossfade and nothing else moves.

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

private struct PressedLabel: View {
    let label: ButtonStyleConfiguration.Label
    let pressed: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.isEnabled) private var isEnabled
    @State private var size: CGSize = .zero

    /// About 30 % of a phone's screen, in points.
    private static let largestArea: CGFloat = 100_000

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
            .opacity(isEnabled ? 1 : 0.5)
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

/// One tab's page in the tab stack (`Motion.screen(…, "tab")`): the old
/// page slides 18 pt toward the side away from the new tab as it fades,
/// the new one comes in from the new tab's side. Only the page moves; the
/// tab bar and the mini player stay where they are.
struct TabPage: ViewModifier {
    let me: Tab
    let current: Tab
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    func body(content: Content) -> some View {
        let on = me == current
        let side: CGFloat = me.order > current.order ? 1 : -1
        let shift: CGFloat = (on || reduceMotion) ? 0 : 18 * side
        let curve: Animation = reduceMotion ? .easeInOut(duration: 0.12)
                                            : .timingCurve(0.2, 0.8, 0.2, 1, duration: 0.2)
        content
            .animation(curve) { page in
                page.opacity(on ? 1 : 0).offset(x: shift)
            }
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
/// one 40 ms after the one before (eight at most). Tappable throughout.
struct ArriveOnce: ViewModifier {
    let key: String
    let delay: Double
    let book: ArrivalBook
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var shown: Bool

    init(key: String, delay: Double, book: ArrivalBook) {
        self.key = key
        self.delay = delay
        self.book = book
        _shown = State(initialValue: book.seen.contains(key))
    }

    func body(content: Content) -> some View {
        content
            .opacity(shown ? 1 : 0)
            .offset(y: shown ? 0 : 8)
            .onAppear {
                if shown { return }
                let first = !book.seen.contains(key)
                book.seen.insert(key)
                if reduceMotion || !first {
                    shown = true
                    return
                }
                withAnimation(.timingCurve(0.2, 0.8, 0.2, 1, duration: 0.26).delay(delay)) {
                    shown = true
                }
            }
    }
}

extension View {
    /// The first three tiles of each rail arrive one after another in
    /// reading order - along a rail, then the next rail down, each heading
    /// before its tiles - `slot` places this one in that order (40 ms a
    /// step, eight steps at most). `index` is its place along its rail:
    /// past the third (and anything seen before) it just appears.
    @ViewBuilder
    func arriving(_ key: String, index: Int, slot: Int, book: ArrivalBook) -> some View {
        if index < 3 {
            modifier(ArriveOnce(key: key, delay: Double(min(slot, 8)) * 0.04, book: book))
        } else {
            self
        }
    }
}

// MARK: - The tab icon pops (`tabPop`)

/// The newly chosen tab's icon (`.tabbar .tab.active .dot svg`): from 0.7
/// and 2 pt low, up past full size to 1.12 and 1 pt high at 60 %, settling
/// at 420 ms on the web's overshooting curve. Only on a change to it -
/// never on the first draw - and still with Reduce Motion.
struct TabIconPop: ViewModifier {
    let on: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var scale: CGFloat = 1
    @State private var lift: CGFloat = 0

    func body(content: Content) -> some View {
        content
            .scaleEffect(scale)
            .offset(y: lift)
            .onChange(of: on) { _, now in
                guard now, !reduceMotion else { return }
                var still = Transaction()
                still.disablesAnimations = true
                withTransaction(still) {
                    scale = 0.7
                    lift = 2
                }
                // 0 → 60 %: 252 ms out to the overshoot; 60 → 100 %: back.
                withAnimation(.timingCurve(0.3, 1.6, 0.5, 1, duration: 0.252)) {
                    scale = 1.12
                    lift = -1
                }
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.252) {
                    withAnimation(.timingCurve(0.3, 1.6, 0.5, 1, duration: 0.168)) {
                        scale = 1
                        lift = 0
                    }
                }
            }
    }
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
/// - Reduce Motion: a 120 ms crossfade either way
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

    /// How long the close takes before the cover can go.
    func closeDuration(reduceMotion: Bool) -> Double {
        if reduceMotion { return 0.12 }
        return into == nil ? 0.26 : 0.34
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

    private var width: CGFloat { UIScreen.main.bounds.width }

    var body: some View {
        content()
            .environment(\.playerPictureHidden, flying != nil)
            .opacity(opacity)
            .offset(x: offset)
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
            .onAppear { open() }
            .onChange(of: stage.phase) { _, phase in
                if phase == .closing { close() }
                if phase == .shown { settle() }
            }
    }

    /// The pushed / popped page's place: from the right, back to it.
    private var slides: Bool {
        !reduceMotion && flyingKind == .none
    }

    private enum Kind { case none, grow, shrink }
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
        if !reduceMotion, let from = stage.from {
            flyingKind = .grow
            DispatchQueue.main.async {
                // The player's picture has been laid out by now.
                let to = PictureSpot(url: from.url, frame: stage.pictureFrame, radius: 0)
                if to.frame != .zero { fly(from, to, duration: 0.38, curve: .timingCurve(0.2, 0.8, 0.2, 1, duration: 0.38)) }
                withAnimation(.easeOut(duration: 0.24)) { shown = 1 }
            }
        } else if reduceMotion {
            flyingKind = .none
            withAnimation(.easeInOut(duration: 0.12)) { shown = 1 }
        } else {
            flyingKind = .none
            let curve = Animation.timingCurve(0.2, 0.8, 0.2, 1, duration: 0.32)
            withAnimation(curve) { shown = 1 }
            stage.underCurve = curve
            stage.under = 1
            // Once it is in, the page under is put back where it was,
            // unseen (the web's push ends by cancelling its animations), so
            // a close that shrinks into the mini player finds it in place.
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.34) {
                guard stage.phase != .closing else { return }
                stage.underCurve = nil
                stage.under = 0
            }
        }
    }

    private func close() {
        if reduceMotion {
            withAnimation(.easeInOut(duration: 0.12)) { shown = 0 }
            return
        }
        if let into = stage.into, stage.pictureFrame != .zero {
            flyingKind = .shrink
            let from = PictureSpot(url: into.url, frame: stage.pictureFrame, radius: 0)
            fly(from, into, duration: 0.34, curve: .easeInOut(duration: 0.34))
            withAnimation(.easeOut(duration: 0.2)) { shown = 0 }
        } else {
            flyingKind = .none
            let curve = Animation.easeInOut(duration: 0.26)
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
/// The screen is live from its first frame. Reduce Motion: a 120 ms
/// crossfade in, gone at once.
final class PushedState: ObservableObject {
    @Published var leaving = false
    /// 0 with nothing pushed, 1 with the screen fully in: the pushed
    /// screen and the one under it both move by it, so they move together.
    @Published var shown: CGFloat = 0
    /// How the last change to `shown` moves (the screen under the cover is
    /// in another presentation, so it is given the same curve itself).
    var curve: Animation? = nil

    /// The push's own curve (320 ms) and the pop's (260 ms).
    static let pushCurve = Animation.timingCurve(0.2, 0.8, 0.2, 1, duration: 0.32)
    static let popCurve = Animation.easeInOut(duration: 0.26)
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
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var width: CGFloat { UIScreen.main.bounds.width }

    var body: some View {
        content
            .background(FAMColor.background.ignoresSafeArea())
            .opacity(reduceMotion ? state.shown : 1)
            .offset(x: reduceMotion ? 0 : (1 - state.shown) * width)
            .allowsHitTesting(!state.leaving)
            .onAppear {
                state.move(to: 1, reduceMotion ? .easeInOut(duration: 0.12) : PushedState.pushCurve)
            }
            .onChange(of: state.leaving) { _, leaving in
                guard leaving else {
                    // Asked for again before it had gone: back in place.
                    state.move(to: 1, PushedState.settleCurve)
                    return
                }
                if reduceMotion { return }
                state.move(to: 0, PushedState.popCurve)
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
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    func body(content: Content) -> some View {
        let width = UIScreen.main.bounds.width
        let t = reduceMotion ? 0 : state.shown
        content
            .offset(x: -0.28 * width * t)
            .opacity(1 - 0.45 * t)
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
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// What the cover shows: the item, held while it slides away.
    @State private var held: Item?
    @StateObject private var state = PushedState()

    func body(content: Content) -> some View {
        content
            .modifier(UnderPush(state: state))
            .onChange(of: item?.id) { _, _ in follow() }
            .onAppear { if item != nil && held == nil { follow() } }
            .fullScreenCover(item: Binding(get: { held }, set: { new in
                // A `dismiss()` inside the screen: it slides away first.
                if new == nil && item != nil { item = nil }
            })) { shown in
                PushedScreen(state: state, close: { item = nil }, content: screen(shown))
                    .presentationBackground(.clear)
            }
    }

    private func follow() {
        var still = Transaction()
        still.disablesAnimations = true
        if let item {
            state.leaving = false
            withTransaction(still) { held = item }
        } else if held != nil, !state.leaving {
            state.leaving = true
            let wait = reduceMotion ? 0 : 0.26
            DispatchQueue.main.asyncAfter(deadline: .now() + wait) {
                guard self.item == nil else { return }
                withTransaction(still) { held = nil }
                state.leaving = false
                // Gone: the screen under is home, whatever was in flight.
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
/// `Motion.watchOverlays`): the dim (`rgba(10,9,15,.62)`) fades in over
/// 200 ms and the card lifts 14 pt into place as it does; closing, both go
/// over 150 ms, the card dropping 8 pt. A sheet is the web's
/// `.sheet-card`: the surface, 20 pt top corners and a strong top border,
/// at most 82 % of the screen, sized to what it holds; a tap on the dim
/// closes it. Reduce Motion: the dim and card
/// fade in over 120 ms, nothing lifts, closing is at once. The card is
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
                .opacity(shown ? 1 : 0)
                .offset(y: lift)
        }
        .ignoresSafeArea(.container, edges: .bottom)
        .allowsHitTesting(!state.leaving)
        .onPreferenceChange(ModalLayoutKey.self) { modal = $0 }
        .onAppear {
            withAnimation(reduceMotion ? .easeInOut(duration: 0.12)
                                       : .timingCurve(0.2, 0.8, 0.2, 1, duration: 0.2)) { shown = true }
        }
        .onChange(of: state.leaving) { _, leaving in
            if !leaving {
                withAnimation(.timingCurve(0.2, 0.8, 0.2, 1, duration: 0.2)) { shown = true }
                return
            }
            guard !reduceMotion else { return }
            withAnimation(.timingCurve(0.4, 0, 1, 1, duration: 0.15)) { shown = false }
        }
    }

    /// 14 pt low on the way in, 8 pt low on the way out, none still.
    private var lift: CGFloat {
        if reduceMotion || shown { return 0 }
        return state.leaving ? 8 : 14
    }

    /// One chain for both shapes, so a card that turns out to be a modal
    /// on its first frame keeps its identity (and its state).
    private var card: some View {
        let corners = UnevenRoundedRectangle(topLeadingRadius: 20, topTrailingRadius: 20)
        return content
            .frame(maxWidth: .infinity, maxHeight: modal ? .infinity : UIScreen.main.bounds.height * 0.82)
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

    func body(content: Content) -> some View {
        content
            .onChange(of: item?.id) { _, _ in follow() }
            .onAppear { if item != nil && held == nil { follow() } }
            .fullScreenCover(item: Binding(get: { held }, set: { new in
                // A `dismiss()` inside the card: it fades and drops first.
                if new == nil && item != nil { item = nil }
            }), onDismiss: onDismiss) { shown in
                OverlayCard(state: state, close: { item = nil }, content: card(shown))
                    .presentationBackground(.clear)
            }
    }

    private func follow() {
        var still = Transaction()
        still.disablesAnimations = true
        if let item {
            state.leaving = false
            withTransaction(still) { held = item }
        } else if held != nil, !state.leaving {
            state.leaving = true
            let wait = reduceMotion ? 0 : 0.15
            DispatchQueue.main.asyncAfter(deadline: .now() + wait) {
                guard self.item == nil else { return }
                withTransaction(still) { held = nil }
                state.leaving = false
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
    /// in, dropping 8 pt. Reduce Motion: a 120 ms fade in, gone at once.
    static func famCard(reduceMotion: Bool) -> AnyTransition {
        if reduceMotion {
            return .asymmetric(insertion: AnyTransition.opacity.animation(.easeInOut(duration: 0.12)),
                               removal: .identity)
        }
        return .asymmetric(
            insertion: AnyTransition.opacity.combined(with: .offset(y: 14))
                .animation(.timingCurve(0.2, 0.8, 0.2, 1, duration: 0.2)),
            removal: AnyTransition.opacity.combined(with: .offset(y: 8))
                .animation(.timingCurve(0.4, 0, 1, 1, duration: 0.15)))
    }

    /// The dim behind it (`mo-in`, `mo-out`): 200 ms in, 150 ms out.
    static func famDim(reduceMotion: Bool) -> AnyTransition {
        if reduceMotion {
            return .asymmetric(insertion: AnyTransition.opacity.animation(.easeInOut(duration: 0.12)),
                               removal: .identity)
        }
        return .asymmetric(
            insertion: AnyTransition.opacity.animation(.timingCurve(0.2, 0.8, 0.2, 1, duration: 0.2)),
            removal: AnyTransition.opacity.animation(.timingCurve(0.4, 0, 1, 1, duration: 0.15)))
    }
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
            .opacity(1 - 0.45 * stage.under)
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
