import SwiftUI

/// The player: picture, title, the draggable bar (only over what has
/// arrived) and ±15 s - the two transport gestures (`transport`).
struct PlayerView: View {
    @EnvironmentObject private var player: StreamPlayer
    @Environment(\.dismiss) private var dismiss

    @State private var dragging = false
    @State private var dragValue: Double = 0

    var body: some View {
        VStack(spacing: 22) {
            Capsule()
                .fill(Color.secondary.opacity(0.4))
                .frame(width: 40, height: 5)
                .padding(.top, 8)

            Picture(url: player.picture)
                .aspectRatio(4 / 3, contentMode: .fit)
                .clipShape(RoundedRectangle(cornerRadius: 18))
                .padding(.horizontal, 24)

            Text(player.title)
                .font(.title3.weight(.semibold))
                .multilineTextAlignment(.center)
                .padding(.horizontal, 24)

            status

            VStack(spacing: 6) {
                Slider(value: Binding(
                    get: { dragging ? dragValue : player.position },
                    set: { dragValue = $0 }
                ), in: 0...max(player.arrived, 0.1), onEditingChanged: { editing in
                    if editing {
                        dragValue = player.position
                        dragging = true
                    } else {
                        player.seek(to: dragValue)
                        dragging = false
                    }
                })
                .disabled(player.arrived == 0)
                .accessibilityLabel("Position")
                HStack {
                    Text(clock(dragging ? dragValue : player.position))
                    Spacer()
                    Text(clock(player.arrived))
                }
                .font(.caption.monospacedDigit())
                .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 24)

            HStack(spacing: 44) {
                Button { player.skip(by: -15) } label: {
                    Image(systemName: "gobackward.15").font(.title)
                }
                .accessibilityLabel("Back 15 seconds")
                Button { player.toggle() } label: {
                    Image(systemName: player.playing ? "pause.circle.fill" : "play.circle.fill")
                        .font(.system(size: 64))
                }
                .accessibilityLabel(player.playing ? "Pause" : "Play")
                Button { player.skip(by: 15) } label: {
                    Image(systemName: "goforward.15").font(.title)
                }
                .accessibilityLabel("Forward 15 seconds")
            }
            .disabled(player.phase == .connecting || player.phase == .failed)

            Spacer()
        }
        .presentationDragIndicator(.hidden)
    }

    /// What is happening, honestly: a new episode is being written before
    /// its first word, and nothing is said to cover that.
    @ViewBuilder private var status: some View {
        switch player.phase {
        case .connecting:
            Label("Writing your episode…", systemImage: "hourglass")
                .foregroundStyle(.secondary)
        case .buffering:
            Label("Waiting for the next words…", systemImage: "ellipsis")
                .foregroundStyle(.secondary)
        case .failed:
            Text(player.problem.isEmpty ? "That episode could not be played." : player.problem)
                .font(.footnote)
                .foregroundStyle(.red)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 24)
        default:
            EmptyView()
        }
    }
}

/// Above the tab bar while something is loaded. Tap to open the player; the
/// X stops it (`transport`: leaving the player never does).
struct MiniPlayer: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var player: StreamPlayer

    var body: some View {
        HStack(spacing: 12) {
            Picture(url: player.picture)
                .frame(width: 44, height: 44)
                .clipShape(RoundedRectangle(cornerRadius: 8))
            Text(player.title)
                .font(.subheadline.weight(.semibold))
                .lineLimit(1)
            Spacer(minLength: 4)
            Button { player.toggle() } label: {
                Image(systemName: player.playing ? "pause.fill" : "play.fill")
                    .font(.title3)
                    .frame(width: 36, height: 36)
            }
            .accessibilityLabel(player.playing ? "Pause" : "Play")
            Button { player.stop() } label: {
                Image(systemName: "xmark")
                    .font(.body.weight(.semibold))
                    .frame(width: 36, height: 36)
            }
            .accessibilityLabel("Stop")
        }
        .padding(8)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14))
        .padding(.horizontal, 10)
        .contentShape(Rectangle())
        .onTapGesture { model.showPlayer = true }
    }
}

func clock(_ seconds: Double) -> String {
    let s = max(0, Int(seconds.rounded()))
    return String(format: "%d:%02d", s / 60, s % 60)
}
