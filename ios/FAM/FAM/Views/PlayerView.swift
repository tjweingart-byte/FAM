import SwiftUI

/// The player, after the web's (`player-layout`): the Home mark top left to
/// close it, the picture (4:3, never missing), the title in the display
/// face, the copper scrubber over what has arrived, and ±15 s either side of
/// the copper play button - the two transport gestures (`transport`). GO
/// DEEPER, share/vibe/save, the queue and captions join when they are built
/// (`no-dead-controls`).
struct PlayerView: View {
    @EnvironmentObject private var player: StreamPlayer
    @Environment(\.dismiss) private var dismiss

    @State private var dragging = false
    @State private var dragValue: Double = 0

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Button { dismiss() } label: {
                    WebIcon(path: Icons.daily, size: 26, line: 2.2)
                        .foregroundStyle(FAMColor.text)
                        .frame(width: 44, height: 44)
                }
                .accessibilityLabel("Close the player")
                Spacer()
            }
            .padding(.horizontal, 14)
            .padding(.top, 10)

            Picture(url: player.picture)
                .aspectRatio(4 / 3, contentMode: .fit)
                .frame(maxWidth: .infinity)
                .padding(.top, 8)

            VStack(alignment: .leading, spacing: 0) {
                Text(player.title)
                    .font(FAMFont.heading(24, .medium))
                    .foregroundStyle(FAMColor.text)
                    .lineSpacing(2)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 20)
                status
                scrubber.padding(.top, 18)
            }
            .padding(.horizontal, 20)

            controls.padding(.top, 22)
            Spacer(minLength: 12)
        }
        .background(FAMColor.background.ignoresSafeArea())
        .presentationDragIndicator(.visible)
    }

    /// The scrubber: a thin track, copper as far as you are, a copper knob.
    /// Dragging is clamped to what has arrived (`transport`).
    private var scrubber: some View {
        let total = max(player.total, 0.1)
        let shown = dragging ? dragValue : player.position
        return VStack(spacing: 8) {
            GeometryReader { geo in
                let width = geo.size.width
                let x = width * min(max(shown / total, 0), 1)
                ZStack(alignment: .leading) {
                    Capsule().fill(FAMColor.borderStrong.opacity(0.6)).frame(height: 4)
                    // What has arrived so far: the furthest a drag can reach.
                    Capsule().fill(FAMColor.borderStrong)
                        .frame(width: width * min(player.arrived / total, 1), height: 4)
                    Capsule().fill(FAMColor.copper).frame(width: x, height: 4)
                    Circle()
                        .fill(FAMColor.copper)
                        .frame(width: dragging ? 20 : 14, height: dragging ? 20 : 14)
                        .offset(x: x - (dragging ? 10 : 7))
                }
                .frame(height: 24)
                .contentShape(Rectangle())
                .gesture(DragGesture(minimumDistance: 0)
                    .onChanged { value in
                        guard player.arrived > 0 else { return }
                        dragging = true
                        dragValue = min(total * min(max(value.location.x / width, 0), 1), player.arrived)
                    }
                    .onEnded { _ in
                        guard dragging else { return }
                        player.seek(to: dragValue)
                        dragging = false
                    })
            }
            .frame(height: 24)
            .accessibilityElement()
            .accessibilityLabel("Position")
            .accessibilityValue("\(clock(shown)) of \(clock(player.total))")
            .accessibilityAdjustableAction { direction in
                player.skip(by: direction == .increment ? 15 : -15)
            }
            HStack {
                Text(clock(shown))
                Spacer()
                Text(clock(player.total))
            }
            .font(FAMFont.mono(10))
            .foregroundStyle(FAMColor.textMuted)
        }
    }

    private var controls: some View {
        HStack(spacing: 44) {
            seekButton(back: true)
            Button { player.toggle() } label: {
                Image(systemName: player.playing ? "pause.fill" : "play.fill")
                    .font(.system(size: 26, weight: .bold))
                    .foregroundStyle(FAMColor.ink)
                    .frame(width: 60, height: 60)
                    .background(FAMColor.copper, in: Circle())
            }
            .accessibilityLabel(player.playing ? "Pause" : "Play")
            seekButton(back: false)
        }
        .disabled(player.phase == .connecting || player.phase == .failed)
        .opacity(player.phase == .connecting || player.phase == .failed ? 0.5 : 1)
    }

    /// ±15: the number inside the circle the arrow travels round (`.skip-num`).
    private func seekButton(back: Bool) -> some View {
        Button { player.skip(by: back ? -15 : 15) } label: {
            Image(systemName: back ? "gobackward.15" : "goforward.15")
                .font(.system(size: 19, weight: .medium))
                .foregroundStyle(FAMColor.text)
                .frame(width: 38, height: 38)
                .background(FAMColor.surface, in: Circle())
                .overlay(Circle().stroke(FAMColor.borderStrong))
        }
        .accessibilityLabel(back ? "Back 15 seconds" : "Forward 15 seconds")
    }

    /// What is happening, honestly: a new episode is being written before
    /// its first word, and nothing is said to cover that.
    @ViewBuilder private var status: some View {
        switch player.phase {
        case .connecting:
            Text("Writing your episode…")
                .monoLabel(9.5, color: FAMColor.copper)
                .padding(.top, 8)
        case .buffering:
            Text("Waiting for the next words…")
                .monoLabel(9.5, color: FAMColor.textMuted)
                .padding(.top, 8)
        case .failed:
            Text(player.problem.isEmpty ? "That episode could not be played." : player.problem)
                .font(FAMFont.body(12))
                .foregroundStyle(FAMColor.coral)
                .padding(.top, 8)
        default:
            EmptyView()
        }
    }
}

/// The bar above the tabs while something is loaded (`.nowbar`): picture,
/// title, copper play/pause and the X that stops it. Tap to open the player.
struct MiniPlayer: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var player: StreamPlayer

    var body: some View {
        HStack(spacing: 10) {
            Picture(url: player.picture)
                .frame(width: 40, height: 40)
                .clipShape(RoundedRectangle(cornerRadius: 8))
            VStack(alignment: .leading, spacing: 3) {
                Text(player.title)
                    .font(FAMFont.heading(12, .medium))
                    .foregroundStyle(FAMColor.text)
                    .lineLimit(1)
                Text(meta)
                    .monoLabel(8)
                    .lineLimit(1)
            }
            Spacer(minLength: 4)
            Button { player.toggle() } label: {
                Image(systemName: player.playing ? "pause.fill" : "play.fill")
                    .font(.system(size: 14, weight: .bold))
                    .foregroundStyle(FAMColor.ink)
                    .frame(width: 36, height: 36)
                    .background(FAMColor.copper, in: Circle())
            }
            .accessibilityLabel(player.playing ? "Pause" : "Play")
            Button { player.stop() } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 11, weight: .bold))
                    .foregroundStyle(FAMColor.textMuted)
                    .frame(width: 26, height: 26)
                    .overlay(Circle().stroke(FAMColor.borderStrong))
            }
            .accessibilityLabel("Stop")
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(FAMColor.border))
        .contentShape(Rectangle())
        .onTapGesture { model.showPlayer = true }
    }

    private var meta: String {
        switch player.phase {
        case .connecting: return "Writing…"
        case .failed: return "Could not play"
        default: return "\(clock(player.position)) · \(clock(player.arrived))"
        }
    }
}

func clock(_ seconds: Double) -> String {
    let s = max(0, Int(seconds.rounded()))
    return String(format: "%d:%02d", s / 60, s % 60)
}
