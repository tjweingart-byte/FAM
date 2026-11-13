import SwiftUI

// The queue's flight (`Motion.toQueue`): something queued flies as a
// 28x22 copy of its picture to the Queue button (the full player) or the
// mini player's picture, in an arc 24 pt over the higher end, shrinking to
// 12x10 and fading in its second half, 450 ms on the web's ease-in-out;
// the badge pops as it lands and the target bumps (1 → 1.3 → 1, 260 ms).
// A tile's ⋯ has no frame to fly from here, so it starts where the web's
// does when its tile is not on screen: the middle of the page. Stilled by
// Reduce Motion, as every movement is.

/// The copy in flight, between two centres in its container's space.
struct QueueGhost: View {
    let picture: URL?
    let from: CGPoint
    let to: CGPoint
    let done: () -> Void
    @State private var t: CGFloat = 0

    var body: some View {
        face
            .modifier(QueueFlightPath(t: t, from: from, to: to))
            .allowsHitTesting(false)
            .accessibilityHidden(true)
            .onAppear {
                withAnimation(.timingCurve(0.4, 0, 0.2, 1, duration: 0.45)) { t = 1 }
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.46) { done() }
            }
    }

    private var face: some View {
        ZStack {
            FAMColor.paper
            if let picture {
                AsyncImage(url: picture) { image in
                    image.resizable().scaledToFill()
                } placeholder: {
                    FAMColor.copper.opacity(0.45)
                }
            } else {
                FAMColor.copper.opacity(0.45)
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: 5))
    }
}

/// The three keyframes of `toQueue`, as one animatable step `t` (0 to 1).
struct QueueFlightPath: ViewModifier, Animatable {
    var t: CGFloat
    let from: CGPoint
    let to: CGPoint

    var animatableData: CGFloat {
        get { t }
        set { t = newValue }
    }

    func body(content: Content) -> some View {
        let frame = place(t)
        return content
            .frame(width: frame.size.width, height: frame.size.height)
            .opacity(frame.opacity)
            .position(frame.centre)
    }

    private struct Frame {
        var centre: CGPoint
        var size: CGSize
        var opacity: Double
    }

    private func place(_ t: CGFloat) -> Frame {
        // The web's numbers are top-left corners: 28x22 at the start,
        // 20x16 half way (10 right of the middle, 24 over the higher end),
        // 12x10 at the end.
        let sx = from.x - 14
        let sy = from.y - 11
        let ex = to.x - 6
        let ey = to.y - 5
        let mid = CGPoint(x: (sx + ex) / 2 + 10 + 10, y: min(sy, ey) - 24 + 8)
        if t <= 0.5 {
            let u = t / 0.5
            return Frame(centre: lerp(from, mid, u),
                         size: CGSize(width: 28 - 8 * u, height: 22 - 6 * u),
                         opacity: 1)
        }
        let u = (t - 0.5) / 0.5
        return Frame(centre: lerp(mid, to, u),
                     size: CGSize(width: 20 - 8 * u, height: 16 - 6 * u),
                     opacity: Double(1 - u))
    }

    private func lerp(_ a: CGPoint, _ b: CGPoint, _ u: CGFloat) -> CGPoint {
        CGPoint(x: a.x + (b.x - a.x) * u, y: a.y + (b.y - a.y) * u)
    }
}

/// Where a view sits in a named space, reported as it moves.
struct FrameReporter: View {
    let space: String
    let report: (CGRect) -> Void

    var body: some View {
        GeometryReader { geo in
            let frame = geo.frame(in: .named(space))
            Color.clear
                .onAppear { report(frame) }
                .onChange(of: frame) { _, now in report(now) }
        }
    }
}
