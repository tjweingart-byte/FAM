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
    /// The mini player's picture, while one is drawn.
    var mini: PictureSpot?

    func pressed(_ spot: PictureSpot?) { tapped = (spot, Date()) }

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
            .onChange(of: pressed) { _, down in
                if down { PlayerMotion.shared.pressed(slot.best) }
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
            withAnimation(.timingCurve(0.2, 0.8, 0.2, 1, duration: 0.32)) { shown = 1 }
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
            withAnimation(.easeInOut(duration: 0.26)) { shown = 0 }
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
/// here. A drag from the left edge takes it back, as an iOS push does.
/// The screen is live from its first frame. Reduce Motion: a 120 ms
/// crossfade in, gone at once.
final class PushedState: ObservableObject {
    @Published var leaving = false
}

private struct PushedScreen<Content: View>: View {
    @ObservedObject var state: PushedState
    let close: () -> Void
    let content: Content
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var shown: CGFloat = 0
    @State private var drag: CGFloat = 0

    private var width: CGFloat { UIScreen.main.bounds.width }

    var body: some View {
        content
            .background(FAMColor.background.ignoresSafeArea())
            .opacity(reduceMotion ? shown : 1)
            .offset(x: reduceMotion ? 0 : (1 - shown) * width + drag)
            .overlay(alignment: .leading) {
                // The edge a back swipe starts from.
                if !reduceMotion {
                    Color.clear
                        .frame(width: 20)
                        .frame(maxHeight: .infinity)
                        .contentShape(Rectangle())
                        .gesture(DragGesture(minimumDistance: 8, coordinateSpace: .global)
                            .onChanged { g in drag = max(0, g.translation.width) }
                            .onEnded { g in
                                let far = g.translation.width > width / 3
                                    || g.predictedEndTranslation.width > width / 2
                                if far {
                                    close()
                                } else {
                                    withAnimation(.timingCurve(0.2, 0.8, 0.2, 1, duration: 0.2)) { drag = 0 }
                                }
                            })
                }
            }
            .allowsHitTesting(!state.leaving)
            .onAppear {
                withAnimation(reduceMotion ? .easeInOut(duration: 0.12)
                                           : .timingCurve(0.2, 0.8, 0.2, 1, duration: 0.32)) { shown = 1 }
            }
            .onChange(of: state.leaving) { _, leaving in
                guard leaving else {
                    // Asked for again before it had gone: back in place.
                    withAnimation(.timingCurve(0.2, 0.8, 0.2, 1, duration: 0.2)) {
                        shown = 1
                        drag = 0
                    }
                    return
                }
                if reduceMotion { return }
                withAnimation(.easeInOut(duration: 0.26)) {
                    shown = 0
                    drag = 0
                }
            }
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
