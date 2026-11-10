import FAMSafety
import SwiftUI
import UIKit

/// The player, ported from the web's `#screen-player` (`player-layout`,
/// §190, §192) as the 11.9 packet left it (§519). Top to bottom:
///
/// - the Home mark (minimises, never stops), GO DEEPER, the ⋯ menu
/// - the picture: 40 pt wider than the screen, 4:3, centred, faded into the
///   page at top and bottom; the captions card over it when captions are
///   on; share / vibe / save stacked on its right, 34 pt up, above the card
/// - the title (with the explicit E, wiped in when the writer's lands) and
///   the sources strip
/// - the scrubber: requested length until it has all arrived, the arrived
///   part drawn, a drag clamped to 2 s short of it, sought on release
///   (`transport`)
/// - the five-slot transport: an empty slot, −15, the 66 pt copper play,
///   +15, next (a playlist or a queue); then the speed · length pill, the
///   Captions pill and the queue
///
/// Go Deeper, Playback and Sources are the web's centred modals, drawn here
/// over the player; the ⋯ menu, the Queue and the speed choices are bottom
/// sheets, as on the web.
struct PlayerView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var player: StreamPlayer
    @EnvironmentObject private var now: NowPlayingModel
    @Environment(\.dismiss) private var dismiss

    @State private var sheet: PlayerSheet?
    @State private var popup: PlayerPopup?
    @State private var badgePop: CGFloat = 1

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
            if !now.nextUp.isEmpty {
                NextUpOverlay(onBack: backFromNextUp)
            }
            popupLayer
            ToastView()
                .padding(.bottom, 8)
        }
        .background(FAMColor.background.ignoresSafeArea())
        .animation(.easeOut(duration: 0.2), value: popup)
        .animation(.timingCurve(0.22, 0.7, 0.3, 1, duration: 0.36), value: now.nextUp.isEmpty)
        .sheet(item: $sheet) { which in
            sheetView(which)
                .environmentObject(model)
                .environmentObject(player)
                .environmentObject(now)
        }
        .onChange(of: now.showQueue) { _, wanted in
            guard wanted else { return }
            now.showQueue = false
            openSheet(.queue)
        }
        .onChange(of: now.queue.count) { old, new in
            // The badge pops when something is queued (0 → 1.25 → 1, 260 ms).
            guard new > old else { return }
            badgePop = 0
            withAnimation(.spring(response: 0.26, dampingFraction: 0.55)) { badgePop = 1 }
        }
    }

    /// What's next's Back: to the search page for a search, else DailyFAM
    /// (`Back to search` / `Back to DailyFAM`).
    private func backFromNextUp() {
        let fromSearch = now.episode?.surface == "search"
        model.tabRequest = fromSearch ? .search : .daily
        dismiss()
    }

    /// A sheet after whatever is up has gone (one presentation at a time).
    private func openSheet(_ which: PlayerSheet) {
        if sheet == nil {
            sheet = which
        } else {
            sheet = nil
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { sheet = which }
        }
    }

    // MARK: Top row (`.player-top`: 44 / 1fr / 44)

    private var topRow: some View {
        HStack(spacing: 0) {
            Button { dismiss() } label: {
                WebIcon(path: Icons.daily, size: 22, line: 2)
                    .foregroundStyle(FAMColor.text)
                    .frame(width: 44, height: 44)
            }
            .accessibilityLabel("Minimise the player")
            Spacer(minLength: 0)
            Button { openGoDeeper() } label: {
                Text("GO DEEPER")
                    .font(FAMFont.body(13, .bold))
                    .tracking(0.78)
                    .foregroundStyle(FAMColor.ink)
                    .padding(.horizontal, 34)
                    .padding(.vertical, 8)
                    .background(FAMColor.deeper, in: Capsule())
            }
            .buttonStyle(.plain)
            Spacer(minLength: 0)
            Button { openSheet(.menu) } label: {
                WebFill(path: Icons.dots(1.9), size: 22)
                    .foregroundStyle(FAMColor.text)
                    .frame(width: 44, height: 44)
            }
            .accessibilityLabel("More options")
        }
        .padding(.horizontal, 12)
        .padding(.top, 10)
    }

    /// `openGoDeeper('player')`: the episode keeps playing; the thread and
    /// the sources are asked again.
    private func openGoDeeper() {
        now.lookAgain()
        popup = .goDeeper
    }

    // MARK: The stage: picture, captions card, side buttons

    private var stage: some View {
        GeometryReader { geo in
            let wide = geo.size.width + 40
            ZStack(alignment: .bottomTrailing) {
                PlayerPicture(url: now.picture ?? player.picture)
                    .frame(width: wide, height: wide * 3 / 4)
                    .position(x: geo.size.width / 2, y: geo.size.height / 2)
                if now.captionsOn {
                    CaptionsCard()
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .frame(width: geo.size.width, height: geo.size.height)
                        .transition(.opacity.combined(with: .offset(y: 8)))
                }
                sideStack
            }
            .frame(width: geo.size.width, height: geo.size.height)
            .animation(.easeOut(duration: 0.22), value: now.captionsOn)
        }
        .frame(maxHeight: .infinity)
    }

    /// Share / VIBE! / save (`.p-side`): 24 apart, 34 pt up plus 12 of
    /// padding (§519: it was 10, and SAVE sat on the sources pill).
    private var sideStack: some View {
        VStack(spacing: 24) {
            SideButton(icon: Icons.menuShare, caption: "share", on: false, tint: FAMColor.copper) {
                openSheet(.share)
            }
            SideButton(icon: Icons.reshare, caption: now.vibed ? "vibed" : "vibe",
                       on: now.vibed, tint: FAMColor.copper) {
                tapVibe()
            }
            .modifier(BurstModifier(trigger: now.vibedBurst, word: "VIBED"))
            SideButton(icon: Icons.bookmark, caption: now.saved ? "saved" : "save",
                       on: now.saved, tint: FAMColor.sage) {
                now.toggleSave()
            }
            .modifier(BurstModifier(trigger: now.savedBurst, word: "SAVED"))
        }
        .padding(.trailing, 16)
        .padding(.bottom, 34 + 12)
    }

    private func tapVibe() {
        if now.vibed {
            now.unvibe()
        } else if now.episode == nil {
            now.say("Play something first")
        } else {
            openSheet(.vibe)
        }
    }

    // MARK: Title and sources

    private var shownTitle: String { now.episode == nil ? player.title : now.title }

    private var titleRow: some View {
        HStack(alignment: .center, spacing: 12) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                if now.explicit {
                    PlayerExplicitBadge(size: 17)
                }
                WipeText(text: shownTitle, font: FAMFont.heading(22, .medium),
                         lines: 3, lineSpacing: 22 * 0.2 - 4)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if let sources = now.sources, !sources.items.isEmpty || sources.known {
                SourcesStrip(sources: sources) {
                    if !sources.items.isEmpty { popup = .sources }
                }
            }
        }
        .padding(.horizontal, 20)
    }

    // MARK: Transport (§519: `.pc-row1` is a five-slot grid)

    private var controls: some View {
        VStack(spacing: 14) {
            transportRow
            secondRow
        }
        .padding(.horizontal, 20)
        .padding(.top, 14)
        .padding(.bottom, 22)
    }

    /// Next shows for a playlist or a queue with something in it.
    private var showsNext: Bool { model.mixPlay != nil || !now.queue.isEmpty }

    /// `minmax(0,1fr) minmax(0,1fr) 74px minmax(0,1fr) minmax(0,1fr)`, at
    /// most 330 wide: the play stays dead centre whether next shows or not.
    private var transportRow: some View {
        HStack(spacing: 0) {
            Color.clear
                .frame(width: 48, height: 48)
                .frame(maxWidth: .infinity)
            seekButton(back: true)
                .frame(maxWidth: .infinity)
            playButton
                .frame(width: 74)
            seekButton(back: false)
                .frame(maxWidth: .infinity)
            nextSlot
                .frame(maxWidth: .infinity)
        }
        .frame(maxWidth: 330)
        .frame(height: 66)
        .frame(maxWidth: .infinity)
    }

    private var playButton: some View {
        Button { player.toggle() } label: {
            PlayPauseGlyph(playing: player.playing)
                .frame(width: 66, height: 66)
                .background(FAMColor.copper, in: Circle())
                .shadow(color: .black.opacity(0.3), radius: 7, y: 6)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(player.playing ? "Pause" : "Play")
    }

    @ViewBuilder private var nextSlot: some View {
        if showsNext {
            Button { model.skipMixItem() } label: {
                NextGlyph()
                    .foregroundStyle(FAMColor.text)
                    .frame(width: 48, height: 48)
                    .contentShape(Circle())
            }
            .buttonStyle(BareCircleStyle())
            // The web's words, whether it is the playlist's next or the queue's.
            .accessibilityLabel("Skip to the next episode in this playlist")
        } else {
            Color.clear.frame(width: 48, height: 48)
        }
    }

    private func seekButton(back: Bool) -> some View {
        Button {
            // Nothing playing yet: said, not sought (`skipAudio`).
            guard player.isLoaded, player.phase != .connecting, player.phase != .failed else {
                now.say("Nothing playing yet")
                return
            }
            let caught = !player.skip(by: back ? -15 : 15)
            if caught { now.say("Caught up with the writing \u{2014} it will keep going") }
        } label: {
            SeekGlyph(back: back)
                .foregroundStyle(FAMColor.text)
                .frame(width: 48, height: 48)
                .contentShape(Circle())
        }
        .buttonStyle(BareCircleStyle())
        .accessibilityLabel(back ? "Back 15 seconds" : "Forward 15 seconds")
    }

    /// Row 2: the speed · length pill, the Captions pill and the queue.
    private var secondRow: some View {
        HStack(spacing: 12) {
            Button { popup = .playback } label: {
                Text(pillText)
                    .font(FAMFont.mono(9.5, bold: true))
                    .foregroundStyle(FAMColor.text)
                    .lineLimit(1)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 8)
                    .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 14))
                    .overlay(RoundedRectangle(cornerRadius: 14).stroke(FAMColor.borderStrong))
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Playback: \(pillText)")
            Spacer(minLength: 0)
            CaptionsPill(on: now.captionsOn) { now.setCaptions(!now.captionsOn) }
            queueButton
        }
        .padding(.bottom, 11)
    }

    private var queueButton: some View {
        Button { openSheet(.queue) } label: {
            QueueGlyph()
                .foregroundStyle(FAMColor.text)
                .frame(width: 32, height: 32)
                .background(FAMColor.surface, in: Circle())
                .overlay(Circle().stroke(FAMColor.borderStrong))
                .overlay(alignment: .topTrailing) { queueBadge }
                .overlay(alignment: .top) {
                    // The caption hangs 34 pt below the circle's top.
                    Text("Queue")
                        .monoLabel(7, color: FAMColor.textFaint, tracking: 0.49)
                        .fixedSize()
                        .offset(y: 34)
                }
        }
        .buttonStyle(.plain)
        .accessibilityLabel(now.queue.isEmpty ? "The queue" : "The queue, \(now.queue.count)")
    }

    @ViewBuilder private var queueBadge: some View {
        if !now.queue.isEmpty {
            Text("\(now.queue.count)")
                .font(FAMFont.mono(9, bold: true))
                .foregroundStyle(FAMColor.ink)
                .padding(.horizontal, 4)
                .frame(minWidth: 15, minHeight: 15)
                .background(FAMColor.copper, in: Capsule())
                .scaleEffect(badgePop)
                .offset(x: 8, y: -6)
        }
    }

    private var pillText: String {
        "\(FAMAPI.minutes(player.rate))x · \(FAMAPI.minutes(now.episode?.key.minutes ?? FAMAPI.browseMinutes)) min"
    }

    // MARK: Modals drawn over the player

    @ViewBuilder private var popupLayer: some View {
        if let which = popup {
            OverlayModal(close: { popup = nil }) {
                popupContent(which)
            }
            .transition(.opacity)
            .zIndex(5)
        }
    }

    @ViewBuilder private func popupContent(_ which: PlayerPopup) -> some View {
        switch which {
        case .goDeeper:
            GoDeeperSheet(close: { popup = nil })
        case .sources:
            SourcesSheet(close: { popup = nil })
        case .playback:
            PlaybackSheet(close: { popup = nil }) { choice in
                popup = nil
                openSheet(choice)
            }
        }
    }

    // MARK: Sheets

    @ViewBuilder private func sheetView(_ which: PlayerSheet) -> some View {
        switch which {
        case .menu: PlayerMenu(sheet: $sheet)
        case .queue: QueueSheet()
        case .vibe: VibeComposer()
        case .share: ShareSheet()
        case .speed: ActionSheetList(items: speedItems)
        case .length: ActionSheetList(items: lengthItems)
        case .addToMix: AddToMixSheet(query: now.episode?.key.query ?? "", title: now.title)
        case .report:
            if let ep = now.episode {
                FAMReportSheet(api: model.safety,
                               subject: .episode(query: ep.key.query, minutes: ep.key.minutes),
                               say: { now.say($0) }) { _ in }
            }
        }
    }

    /// `openSpeedMenu`: the six speeds, then "Voice" and the pitch lock.
    private var speedItems: [ActionItem] {
        var items: [ActionItem] = []
        for s in [0.5, 0.8, 1, 1.2, 1.5, 2.0] {
            let label = "\(FAMAPI.minutes(s))x" + (s == player.rate ? "  \u{2713}" : "")
            items.append(ActionItem(label: label, action: { player.setRate(s) }))
        }
        items.append(ActionItem(label: "Voice", group: true))
        let pitch = "Keep the voice's pitch" + (player.keepsPitch ? "  \u{2713}" : "")
        items.append(ActionItem(label: pitch, action: { player.setKeepsPitch(!player.keepsPitch) }))
        return items
    }

    /// `openLengthMenu` (a search only): a new length is a new episode.
    private var lengthItems: [ActionItem] {
        guard let ep = now.episode else { return [] }
        var items: [ActionItem] = []
        for m in [1, 1.5, 2, 3, 4, 5.0] {
            let label = "\(FAMAPI.minutes(m)) min" + (m == ep.key.minutes ? "  \u{2713}" : "")
            items.append(ActionItem(label: label, action: {
                guard m != ep.key.minutes else { return }
                now.say("Rebuilding at \(FAMAPI.minutes(m)) min")
                var again = ep
                again.key.minutes = m
                model.play(again)
            }))
        }
        return items
    }
}

enum PlayerSheet: String, Identifiable {
    case menu, queue, vibe, share, report, addToMix, speed, length
    var id: String { rawValue }
}

/// The centred modals (`.modal-card`) the player draws over itself.
enum PlayerPopup: String, Identifiable {
    case goDeeper, sources, playback
    var id: String { rawValue }
}

/// A bare 48 pt target: no background, a faint disc while pressed
/// (`.mini-ctrl-btn.seek:active`).
struct BareCircleStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .background(Circle().fill(FAMColor.text.opacity(configuration.isPressed ? 0.08 : 0)))
    }
}

/// The explicit E (`.is-explicit::before`): 1.25em square, radius 3, muted
/// with ink, Geist Mono bold at .62em.
struct PlayerExplicitBadge: View {
    var size: CGFloat = 17

    var body: some View {
        Text("E")
            .font(FAMFont.mono(size * 0.8, bold: true))
            .foregroundStyle(FAMColor.ink)
            .frame(width: size, height: size)
            .background(FAMColor.textMuted, in: RoundedRectangle(cornerRadius: 3))
            .accessibilityLabel("Explicit")
    }
}

/// The picture, faded into the page: white at the top edge, clear from 12%
/// to 60%, white at the bottom (`.player-bg::after`). It blurs up as it
/// loads: held at 0, blur 8, scale 1.06 and brought sharp over 320 ms
/// (`Motion.picture`).
struct PlayerPicture: View {
    let url: URL?

    var body: some View {
        ZStack {
            FAMColor.background
            if let url {
                AsyncImage(url: url) { phase in
                    if let image = phase.image {
                        BlurUp(image: image)
                    } else {
                        Color.clear
                    }
                }
                .id(url)
                LinearGradient(stops: [
                    .init(color: FAMColor.background, location: 0),
                    .init(color: FAMColor.background.opacity(0), location: 0.12),
                    .init(color: FAMColor.background.opacity(0), location: 0.60),
                    .init(color: FAMColor.background, location: 1),
                ], startPoint: .top, endPoint: .bottom)
            }
        }
        .clipped()
        .accessibilityHidden(true)
    }
}

/// The picture's arrival: blurred, a little large and clear, then sharp.
private struct BlurUp: View {
    let image: Image
    @State private var sharp = UIAccessibility.isReduceMotionEnabled

    var body: some View {
        image.resizable().scaledToFill()
            .opacity(sharp ? 1 : 0)
            .blur(radius: sharp ? 0 : 8)
            .scaleEffect(sharp ? 1 : 1.06)
            .onAppear {
                withAnimation(.timingCurve(0.2, 0.8, 0.2, 1, duration: 0.32)) { sharp = true }
            }
    }
}

/// Share / vibe / save (`.pt-icon`, `.pt-cap`): a 40 pt dark glass circle
/// with the web's 16 pt glyph, and its caption chip hung 43 pt below the
/// circle's top. VIBE! on is copper, save on is sage, each glyph ink and
/// its caption's words in that colour.
struct SideButton: View {
    let icon: Path
    let caption: String
    let on: Bool
    let tint: Color
    let action: () -> Void

    private static let glass = Color(red: 20 / 255, green: 18 / 255, blue: 28 / 255).opacity(0.62)
    private static let light = Color(hex: 0xF4EFE4)

    var body: some View {
        Button(action: action) {
            WebIcon(path: icon, size: 16, line: 2)
                .foregroundStyle(on ? FAMColor.ink : Self.light)
                .frame(width: 40, height: 40)
                .background(on ? tint : Self.glass, in: Circle())
                .background(.ultraThinMaterial, in: Circle())
                .overlay(Circle().stroke(on ? tint : Self.light.opacity(0.45)))
                .shadow(color: .black.opacity(0.35), radius: 3, y: 1)
                .overlay(alignment: .top) { chip.offset(y: 43) }
        }
        .buttonStyle(.plain)
        .accessibilityLabel(caption)
        .accessibilityAddTraits(on ? .isSelected : [])
    }

    private var chip: some View {
        Text(caption)
            .font(FAMFont.mono(7, bold: true))
            .tracking(0.49)
            .textCase(.uppercase)
            .foregroundStyle(on ? tint : Self.light)
            .shadow(color: .black.opacity(0.6), radius: 1, y: 1)
            .padding(.horizontal, 5)
            .padding(.vertical, 1)
            .background(Self.glass, in: RoundedRectangle(cornerRadius: 6))
            .fixedSize()
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
                    // The knob and its ring (`0 0 0 3px`, 6 px while held).
                    Circle()
                        .fill(Color(red: 224 / 255, green: 181 / 255, blue: 99 / 255)
                            .opacity(dragging ? 0.22 : 0.25))
                        .frame(width: dragging ? 28 : 16, height: dragging ? 28 : 16)
                        .offset(x: x - (dragging ? 14 : 8))
                        .animation(.easeOut(duration: 0.12), value: dragging)
                    Circle()
                        .fill(FAMColor.copper)
                        .frame(width: dragging ? 16 : 10, height: dragging ? 16 : 10)
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

/// The bar above the tabs (`#nowBar`): the picture's 38 pt crop, the title
/// (the same title the full player shows, with its E), "m:ss left", the
/// copper play/pause and an X that stops it; a 2 pt progress line along the
/// bottom. 58 tall, radius 14, surface with the strong border.
struct MiniPlayer: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var player: StreamPlayer
    @EnvironmentObject private var now: NowPlayingModel

    var body: some View {
        HStack(spacing: 11) {
            thumb
            VStack(alignment: .leading, spacing: 3) {
                HStack(alignment: .firstTextBaseline, spacing: 5) {
                    if now.explicit && now.episode != nil { PlayerExplicitBadge(size: 15) }
                    WipeText(text: now.episode == nil ? player.title : now.title,
                             font: FAMFont.heading(12, .medium))
                }
                Text("\(clock(max(0, player.total - player.position))) left")
                    .monoLabel(8, tracking: 0.56)
                    .lineLimit(1)
            }
            Spacer(minLength: 4)
            Button { player.toggle() } label: {
                PlayPauseGlyph(playing: player.playing, size: 16)
                    .frame(width: 36, height: 36)
                    .background(FAMColor.copper, in: Circle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(player.playing ? "Pause" : "Play")
            Button { model.stopEpisode() } label: {
                WebIcon(path: Icons.close, size: 16, line: 2.6)
                    .foregroundStyle(FAMColor.textMuted)
                    .frame(width: 26, height: 26)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Hide the player")
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 9)
        .frame(minHeight: 58)
        .background(FAMColor.surface)
        .overlay(alignment: .bottom) { progress }
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(FAMColor.borderStrong))
        .contentShape(Rectangle())
        .onTapGesture { model.showPlayer = true }
    }

    /// The player's picture at full height, sides cropped; headphones when
    /// there is none.
    private var thumb: some View {
        ZStack {
            FAMColor.paper
            if let url = now.picture ?? player.picture {
                AsyncImage(url: url) { image in
                    image.resizable().scaledToFill()
                } placeholder: { Color.clear }
            } else {
                WebIcon(path: PlayerIcons.headphones, size: 18, line: 1.8)
                    .foregroundStyle(FAMColor.textMuted)
            }
        }
        .frame(width: 38, height: 38)
        .clipShape(RoundedRectangle(cornerRadius: 9))
        .accessibilityHidden(true)
    }

    private var progress: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Rectangle().fill(FAMColor.borderStrong)
                Rectangle().fill(FAMColor.copper)
                    .frame(width: geo.size.width * min(player.position / max(player.total, 0.1), 1))
                    .animation(.linear(duration: 0.2), value: player.position)
            }
        }
        .frame(height: 2)
    }
}

/// What an action did, as a plain line (§299, `.toast` / `.snack`): a
/// short copper rule over centred words, no box, on the page's own colour
/// fading up into whatever is behind; an action (Undo, Open) in copper
/// beside the words.
struct ToastView: View {
    @EnvironmentObject private var now: NowPlayingModel

    var body: some View {
        ZStack {
            if let toast = now.toast {
                band(toast)
                    .transition(.opacity.combined(with: .offset(y: 6)))
            }
        }
        .animation(.easeOut(duration: 0.2), value: now.toast)
    }

    private func band(_ toast: NowPlayingModel.Toast) -> some View {
        VStack(spacing: 7) {
            Capsule()
                .fill(FAMColor.copper)
                .frame(width: 22, height: 2)
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(toast.text)
                    .font(FAMFont.body(13.5, .medium))
                    .foregroundStyle(FAMColor.text)
                    .multilineTextAlignment(.center)
                    .lineLimit(toast.action == nil ? 3 : 1)
                if let action = toast.action {
                    Button(action) { now.toastTapped() }
                        .font(FAMFont.body(13.5, .bold))
                        .foregroundStyle(FAMColor.copper)
                        .buttonStyle(.plain)
                }
            }
        }
        .padding(.top, 26)
        .padding(.horizontal, 20)
        .padding(.bottom, 9)
        .frame(maxWidth: .infinity)
        .background(
            LinearGradient(stops: [
                .init(color: FAMColor.background.opacity(0), location: 0),
                .init(color: FAMColor.background.opacity(0.98), location: 0.45),
            ], startPoint: .top, endPoint: .bottom)
        )
        // Only the action takes a tap; the line leaves what is under it
        // pressable.
        .allowsHitTesting(toast.action != nil)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.updatesFrequently)
    }
}

func clock(_ seconds: Double) -> String {
    let s = max(0, Int(seconds.rounded()))
    return String(format: "%d:%02d", s / 60, s % 60)
}
