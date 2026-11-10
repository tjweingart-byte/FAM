import SwiftUI

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
/// card) do not squeeze, as on the web. Otherwise draws exactly what
/// `.plain` drew: the label, unstyled.
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
    /// The first three tiles of a rail arrive one after another; the rest
    /// (and anything seen before) just appear.
    @ViewBuilder
    func arriving(_ key: String, index: Int, book: ArrivalBook) -> some View {
        if index < 3 {
            modifier(ArriveOnce(key: key, delay: Double(min(index, 8)) * 0.04, book: book))
        } else {
            self
        }
    }
}
