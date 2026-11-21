import SwiftUI

// The queue's flight (`Motion.toQueue`, round three's `queue` A, §640):
// something queued flies as a 28x22 copy of its picture to the Queue
// button (the full player) or the mini player's picture, along a quadratic
// arc bulging 15 % of the distance upward, lifting to 1.15 at its middle,
// shrinking to 12 pt wide over its second half and fading over its last
// fifth, 360 ms on `--ease-in-out`; the Queue's count rolls up as it lands
// and nothing bumps.
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
                withAnimation(Motion.easeInOut(Motion.Dur.flight)) { t = 1 }
                DispatchQueue.main.asyncAfter(deadline: .now() + Motion.Dur.flight + 0.01) { done() }
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
        // The bulge, on whichever side of the line is up: a quadratic
        // curve's middle is half its control point's offset, so 2 x 15 %.
        let dx = to.x - from.x
        let dy = to.y - from.y
        let d = max(1, (dx * dx + dy * dy).squareRoot())
        var nx = -dy / d
        var ny = dx / d
        if ny > 0 {
            nx = -nx
            ny = -ny
        }
        let cx = dx / 2 + nx * 0.3 * d
        let cy = dy / 2 + ny * 0.3 * d
        let u = 1 - t
        let x = 2 * u * t * cx + t * t * dx
        let y = 2 * u * t * cy + t * t * dy
        let lift = 1 + 0.15 * CGFloat(sin(Double.pi * Double(t)))
        let shrink: CGFloat = t <= 0.5 ? 1 : 1 - (t - 0.5) * 2 * (1 - 12 / 28)
        let k = lift * shrink
        let fade: Double = t < 0.8 ? 1 : Double(max(0, (1 - t) / 0.2))
        return Frame(centre: CGPoint(x: from.x + x, y: from.y + y),
                     size: CGSize(width: 28 * k, height: 22 * k),
                     opacity: fade)
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
