import FAMSafety
import SwiftUI

/// The player, ported from the web's `#screen-player` (`player-layout`,
/// §190, §192). Top to bottom:
///
/// - the Home mark (minimises, never stops), GO DEEPER, the ⋯ menu
/// - the picture: full width, 4:3, centred, faded into the page at top and
///   bottom; share / vibe / save stacked on its right
/// - the title (with the explicit E) and the sources strip
/// - the scrubber: requested length until it has all arrived, the arrived
///   part drawn, a drag clamped to 2 s short of it (`transport`)
/// - ±15, the copper play, next-in-queue; the speed · length pill and the queue
/// - the captions sheet, a 46 pt strip until slid up
struct PlayerView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var player: StreamPlayer
    @EnvironmentObject private var now: NowPlayingModel
    @Environment(\.dismiss) private var dismiss

    @State private var sheet: PlayerSheet?
    @State private var captionsOpen = false

    var body: some View {
        ZStack(alignment: .bottom) {
            VStack(spacing: 0) {
                topRow
                stage
                titleRow
                ScrubberView()
                    .padding(.horizontal, 20)
                    .padding(.top, 12)
                    .padding(.bottom, 4)
                controls
            }
            CaptionsPanel(open: $captionsOpen)
            if !now.nextUp.isEmpty {
                NextUpOverlay(onBack: { dismiss() })
            }
            ToastView()
                .padding(.bottom, 60)
        }
        .background(FAMColor.background.ignoresSafeArea())
        .sheet(item: $sheet) { which in
            sheetView(which)
                .environmentObject(model)
                .environmentObject(player)
                .environmentObject(now)
        }
        .onChange(of: captionsOpen) { _, open in now.captionsOn = open }
    }

    // MARK: Top row

    private var topRow: some View {
        HStack(spacing: 0) {
            Button { dismiss() } label: {
                WebIcon(path: Icons.daily, size: 22, line: 2)
                    .foregroundStyle(FAMColor.text)
                    .frame(width: 44, height: 44)
            }
            .accessibilityLabel("Minimise the player")
            Spacer()
            Button { now.lookAgain(); sheet = .goDeeper } label: {
                Text("GO DEEPER")
                    .font(FAMFont.body(13, .bold))
                    .tracking(0.78)
                    .foregroundStyle(FAMColor.ink)
                    .padding(.horizontal, 34)
                    .padding(.vertical, 8)
                    .background(FAMColor.deeper, in: Capsule())
            }
            Spacer()
            Button { sheet = .menu } label: {
                Image(systemName: "ellipsis")
                    .font(.system(size: 20, weight: .bold))
                    .foregroundStyle(FAMColor.text)
                    .frame(width: 44, height: 44)
            }
            .accessibilityLabel("More options")
        }
        .padding(.horizontal, 12)
        .padding(.top, 10)
    }

    // MARK: The stage: picture and the side buttons

    private var stage: some View {
        GeometryReader { geo in
            ZStack(alignment: .bottomTrailing) {
                PlayerPicture(url: now.picture ?? player.picture)
                    .frame(width: geo.size.width, height: geo.size.width * 3 / 4)
                    .position(x: geo.size.width / 2, y: geo.size.height / 2)
                VStack(spacing: 24) {
                    SideButton(icon: "square.and.arrow.up", caption: "share", on: false, tint: FAMColor.copper) {
                        sheet = .share
                    }
                    SideButton(icon: "arrow.2.squarepath", caption: now.vibed ? "vibed" : "vibe",
                               on: now.vibed, tint: FAMColor.copper) {
                        if now.vibed { now.unvibe() } else if now.episode == nil { now.say("Play something first") } else { sheet = .vibe }
                    }
                    SideButton(icon: now.saved ? "bookmark.fill" : "bookmark",
                               caption: now.saved ? "saved" : "save", on: now.saved, tint: FAMColor.sage) {
                        now.toggleSave()
                    }
                }
                .padding(.trailing, 16)
                .padding(.bottom, 22)
            }
        }
        .frame(maxHeight: .infinity)
    }

    // MARK: Title and sources

    private var titleRow: some View {
        HStack(alignment: .center, spacing: 12) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                if now.explicit {
                    Text("E")
                        .font(FAMFont.mono(13, bold: true))
                        .foregroundStyle(FAMColor.ink)
                        .frame(width: 22, height: 22)
                        .background(FAMColor.textMuted, in: RoundedRectangle(cornerRadius: 3))
                        .accessibilityLabel("Explicit")
                }
                Text(now.episode == nil ? player.title : now.title)
                    .font(FAMFont.heading(22, .medium))
                    .foregroundStyle(FAMColor.text)
                    .lineSpacing(3)
                    .lineLimit(3)
                    .fixedSize(horizontal: false, vertical: true)
                    .contentTransition(.opacity)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if let sources = now.sources, !sources.items.isEmpty || sources.known {
                SourcesStrip(sources: sources) {
                    if !sources.items.isEmpty { sheet = .sources }
                }
            }
        }
        .padding(.horizontal, 20)
    }

    // MARK: Transport

    private var controls: some View {
        VStack(spacing: 14) {
            HStack(spacing: 22) {
                seekButton(back: true)
                Button { player.toggle() } label: {
                    Image(systemName: player.playing ? "pause.fill" : "play.fill")
                        .font(.system(size: 22, weight: .bold))
                        .foregroundStyle(FAMColor.ink)
                        .frame(width: 60, height: 60)
                        .background(FAMColor.copper, in: Circle())
                }
                .accessibilityLabel(player.playing ? "Pause" : "Play")
                seekButton(back: false)
            }
            .overlay(alignment: .trailing) {
                if !now.queue.isEmpty || model.mixPlay != nil {
                    Button {
                        if model.mixPlay != nil { model.skipMixItem() } else { model.stopEpisode(); now.playQueueNext() }
                    } label: {
                        Image(systemName: "forward.end.fill")
                            .font(.system(size: 14, weight: .bold))
                            .foregroundStyle(FAMColor.text)
                            .frame(width: 38, height: 38)
                            .background(FAMColor.surface, in: Circle())
                            .overlay(Circle().stroke(FAMColor.borderStrong))
                    }
                    .offset(x: 54)
                    .accessibilityLabel(model.mixPlay != nil ? "Skip to the next episode in this playlist"
                                        : "Play the next in the queue")
                }
            }
            HStack {
                Button { sheet = .playback } label: {
                    Text(pillText)
                        .font(FAMFont.mono(9.5, bold: true))
                        .foregroundStyle(FAMColor.text)
                        .lineLimit(1)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 8)
                        .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 14))
                        .overlay(RoundedRectangle(cornerRadius: 14).stroke(FAMColor.borderStrong))
                }
                .accessibilityLabel("Playback: \(pillText)")
                Spacer()
                Button { sheet = .queue } label: {
                    VStack(spacing: 2) {
                        Image(systemName: "text.line.first.and.arrowtriangle.forward")
                            .font(.system(size: 12, weight: .semibold))
                            .foregroundStyle(FAMColor.text)
                            .frame(width: 32, height: 32)
                            .background(FAMColor.surface, in: Circle())
                            .overlay(Circle().stroke(FAMColor.borderStrong))
                            .overlay(alignment: .topTrailing) {
                                if !now.queue.isEmpty {
                                    Text("\(now.queue.count)")
                                        .font(FAMFont.mono(9, bold: true))
                                        .foregroundStyle(FAMColor.ink)
                                        .padding(.horizontal, 4)
                                        .frame(minWidth: 15, minHeight: 15)
                                        .background(FAMColor.copper, in: Capsule())
                                        .offset(x: 8, y: -6)
                                }
                            }
                        Text("Queue").monoLabel(7, color: FAMColor.textFaint)
                    }
                }
                .accessibilityLabel("The queue")
            }
            .padding(.bottom, 11)
        }
        .padding(.horizontal, 20)
        .padding(.top, 14)
        .padding(.bottom, 60)
    }

    private var pillText: String {
        "\(FAMAPI.minutes(player.rate))x · \(FAMAPI.minutes(now.episode?.key.minutes ?? FAMAPI.browseMinutes)) min"
    }

    private func seekButton(back: Bool) -> some View {
        Button {
            let caught = !player.skip(by: back ? -15 : 15)
            if caught { now.say("Caught up with the writing — it will keep going") }
        } label: {
            Image(systemName: back ? "gobackward.15" : "goforward.15")
                .font(.system(size: 18, weight: .medium))
                .foregroundStyle(FAMColor.text)
                .frame(width: 38, height: 38)
                .background(FAMColor.surface, in: Circle())
                .overlay(Circle().stroke(FAMColor.borderStrong))
        }
        .accessibilityLabel(back ? "Back 15 seconds" : "Forward 15 seconds")
    }

    // MARK: Sheets

    @ViewBuilder private func sheetView(_ which: PlayerSheet) -> some View {
        switch which {
        case .goDeeper: GoDeeperSheet()
        case .menu: PlayerMenu(sheet: $sheet, captionsOpen: $captionsOpen)
        case .sources: SourcesSheet()
        case .playback: PlaybackSheet()
        case .queue: QueueSheet()
        case .vibe: VibeComposer()
        case .share: ShareSheet()
        case .addToMix: AddToMixSheet(query: now.episode?.key.query ?? "", title: now.title)
        case .report:
            if let ep = now.episode {
                FAMReportSheet(api: model.safety,
                               subject: .episode(query: ep.key.query, minutes: ep.key.minutes),
                               say: { now.say($0) }) { _ in }
            }
        }
    }
}

enum PlayerSheet: String, Identifiable {
    case goDeeper, menu, sources, playback, queue, vibe, share, report, addToMix
    var id: String { rawValue }
}

/// The picture, faded into the page: white at the top edge, clear from 12%
/// to 60%, white at the bottom (`.player-bg::after`).
struct PlayerPicture: View {
    let url: URL?

    var body: some View {
        ZStack {
            FAMColor.background
            if let url {
                AsyncImage(url: url, transaction: Transaction(animation: .easeOut(duration: 0.32))) { phase in
                    if let image = phase.image {
                        image.resizable().scaledToFill()
                    } else {
                        Color.clear
                    }
                }
                LinearGradient(stops: [
                    .init(color: FAMColor.background, location: 0),
                    .init(color: FAMColor.background.opacity(0), location: 0.12),
                    .init(color: FAMColor.background.opacity(0), location: 0.60),
                    .init(color: FAMColor.background, location: 1),
                ], startPoint: .top, endPoint: .bottom)
            } else {
                FAMLetters(capHeight: 40, color: FAMColor.copper.opacity(0.3))
            }
        }
        .clipped()
        .accessibilityHidden(true)
    }
}

/// Share / vibe / save: a dark glass circle and a caption chip, the same in
/// both themes; vibed is copper, saved is sage (`.pt-icon`, `.pt-cap`).
struct SideButton: View {
    let icon: String
    let caption: String
    let on: Bool
    let tint: Color
    let action: () -> Void

    private static let glass = Color(red: 20 / 255, green: 18 / 255, blue: 28 / 255).opacity(0.62)
    private static let light = Color(hex: 0xF4EFE4)

    var body: some View {
        Button(action: action) {
            VStack(spacing: 3) {
                Image(systemName: icon)
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(on ? FAMColor.ink : Self.light)
                    .frame(width: 40, height: 40)
                    .background(on ? tint : Self.glass, in: Circle())
                    .overlay(Circle().stroke(on ? tint : Self.light.opacity(0.45)))
                    .shadow(color: .black.opacity(0.35), radius: 3, y: 1)
                Text(caption)
                    .font(FAMFont.mono(7, bold: true))
                    .tracking(0.5)
                    .textCase(.uppercase)
                    .foregroundStyle(on ? FAMColor.ink : Self.light)
                    .padding(.horizontal, 5)
                    .padding(.vertical, 1)
                    .background(on ? tint : Self.glass, in: RoundedRectangle(cornerRadius: 6))
            }
        }
        .buttonStyle(.plain)
        .accessibilityLabel(caption)
        .accessibilityAddTraits(on ? .isSelected : [])
    }
}

/// The first three publishers' marks, overlapped, and the count
/// (`.src-strip`); "from knowledge" when nothing was retrieved.
struct SourcesStrip: View {
    let sources: Sources
    let open: () -> Void

    var body: some View {
        Button(action: open) {
            HStack(spacing: 7) {
                if !sources.items.isEmpty {
                    HStack(spacing: -6) {
                        ForEach(Array(sources.items.prefix(3).enumerated()), id: \.offset) { _, item in
                            SourceMark(label: item.label, size: 20)
                        }
                    }
                }
                Text(countText)
                    .font(FAMFont.mono(9, bold: true))
                    .tracking(0.9)
                    .textCase(.uppercase)
                    .foregroundStyle(FAMColor.copper)
            }
            .padding(.leading, 5)
            .padding(.trailing, 9)
            .padding(.vertical, 4)
            .background(FAMColor.surface, in: Capsule())
            .overlay(Capsule().stroke(FAMColor.border))
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Show this episode's sources")
    }

    private var countText: String {
        let n = sources.items.count
        if n == 0 { return "from knowledge" }
        return n <= 3 ? "\(n)" : "+\(n - 3)"
    }
}

/// A publisher's mark: its initials on a colour from its name, the site's
/// icon over them when it loads.
struct SourceMark: View {
    let label: String
    var size: CGFloat = 20

    private static let palette: [UInt32] = [0xC9A876, 0x8FAE9A, 0xC3A8E0, 0xE2694F, 0x8FB4D9, 0xD9B38F]

    var body: some View {
        let host = label.lowercased().replacingOccurrences(of: "www.", with: "")
        let word = host.split(separator: ".").first.map(String.init) ?? host
        let hash = label.unicodeScalars.reduce(0) { ($0 &* 31 &+ Int($1.value)) & 0x7fffffff }
        ZStack {
            Circle().fill(Color(hex: Self.palette[hash % Self.palette.count]))
            Text(String(word.prefix(2)).uppercased())
                .font(FAMFont.body(size * 0.4, .bold))
                .foregroundStyle(FAMColor.ink)
            if host.contains("."), let url = URL(string: "https://\(host)/favicon.ico") {
                AsyncImage(url: url) { image in
                    image.resizable().scaledToFit().padding(size * 0.15)
                        .background(Circle().fill(Color.white))
                } placeholder: { Color.clear }
                .clipShape(Circle())
            }
        }
        .frame(width: size, height: size)
        .overlay(Circle().stroke(FAMColor.surface, lineWidth: 1.5))
    }
}

/// The scrubber (`.progress-bar`): track, what has arrived, the copper fill
/// and knob; a drag lands on release, clamped to the seek limit.
struct ScrubberView: View {
    @EnvironmentObject private var player: StreamPlayer
    @EnvironmentObject private var now: NowPlayingModel
    @State private var dragging = false
    @State private var dragValue: Double = 0

    var body: some View {
        let total = max(player.total, 0.1)
        let shown = dragging ? dragValue : min(player.position, total)
        VStack(spacing: 6) {
            GeometryReader { geo in
                let width = geo.size.width
                let x = width * min(max(shown / total, 0), 1)
                ZStack(alignment: .leading) {
                    Capsule().fill(FAMColor.text.opacity(0.12)).frame(height: 4)
                    Capsule().fill(Color(red: 224 / 255, green: 181 / 255, blue: 99 / 255).opacity(0.22))
                        .frame(width: width * min(player.arrived / total, 1), height: 4)
                    Capsule().fill(FAMColor.copper).frame(width: x, height: 4)
                    Circle()
                        .fill(FAMColor.copper)
                        .frame(width: dragging ? 16 : 10, height: dragging ? 16 : 10)
                        .shadow(color: Color(red: 224 / 255, green: 181 / 255, blue: 99 / 255).opacity(0.25),
                                radius: dragging ? 6 : 3)
                        .offset(x: x - (dragging ? 8 : 5))
                        .animation(.easeOut(duration: 0.12), value: dragging)
                }
                .frame(height: 32)
                .contentShape(Rectangle())
                .gesture(DragGesture(minimumDistance: 0)
                    .onChanged { value in
                        guard player.arrived > 0 else { return }
                        dragging = true
                        let wanted = total * min(max(value.location.x / width, 0), 1)
                        dragValue = min(wanted, player.seekLimit)
                    }
                    .onEnded { value in
                        guard dragging else { return }
                        let wanted = total * min(max(value.location.x / width, 0), 1)
                        player.seek(to: dragValue)
                        if wanted - dragValue > 1 && !player.complete {
                            now.say("Caught up with the writing — it will keep going")
                        }
                        dragging = false
                    })
            }
            .frame(height: 32)
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
            .font(FAMFont.mono(9.5))
            .foregroundStyle(FAMColor.textMuted)
        }
    }
}

/// The bar above the tabs while something is loaded (`#nowBar`): picture,
/// title, "m:ss left", copper play/pause and the X that stops it.
struct MiniPlayer: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var player: StreamPlayer
    @EnvironmentObject private var now: NowPlayingModel

    var body: some View {
        HStack(spacing: 10) {
            Picture(url: now.picture ?? player.picture)
                .frame(width: 40, height: 40)
                .clipShape(RoundedRectangle(cornerRadius: 8))
            VStack(alignment: .leading, spacing: 3) {
                Text(now.episode == nil ? player.title : now.title)
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
            Button { model.stopEpisode() } label: {
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
        .overlay(alignment: .bottomLeading) {
            GeometryReader { geo in
                Capsule().fill(FAMColor.copper)
                    .frame(width: geo.size.width * min(player.position / max(player.total, 0.1), 1), height: 2)
            }
            .frame(height: 2)
            .padding(.horizontal, 12)
        }
        .contentShape(Rectangle())
        .onTapGesture { model.showPlayer = true }
    }

    private var meta: String {
        switch player.phase {
        case .connecting: return "Writing…"
        case .failed: return "Could not play"
        default: return "\(clock(max(0, player.total - player.position))) left"
        }
    }
}

/// The snack bar: one line, an optional action (Undo, Open).
struct ToastView: View {
    @EnvironmentObject private var now: NowPlayingModel

    var body: some View {
        if let toast = now.toast {
            HStack(spacing: 12) {
                Text(toast.text)
                    .font(FAMFont.body(13, .medium))
                    .foregroundStyle(FAMColor.background)
                if let action = toast.action {
                    Button(action) { now.toastTapped() }
                        .font(FAMFont.body(13, .bold))
                        .foregroundStyle(Color(hex: 0xE0B563))
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 11)
            .background(FAMColor.text, in: Capsule())
            .padding(.horizontal, 20)
            .transition(.move(edge: .bottom).combined(with: .opacity))
            .animation(.easeOut(duration: 0.2), value: toast.id)
            .accessibilityAddTraits(.updatesFrequently)
        }
    }
}

func clock(_ seconds: Double) -> String {
    let s = max(0, Int(seconds.rounded()))
    return String(format: "%d:%02d", s / 60, s % 60)
}
