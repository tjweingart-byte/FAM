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

    @State private var sheet: PlayerSheet?
    /// Your story, opened from "Vibed to your story · View".
    @State private var myStory: StoryReel?
    @State private var popup: PlayerPopup?
    @State private var badgePop: CGFloat = 1
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// The queue's flight (`Motion.toQueue`): from the picture to the Queue.
    @State private var flying: QueueFlight?
    @State private var pictureFrame: CGRect = .zero
    @State private var queueFrame: CGRect = .zero
    @State private var queueBump: CGFloat = 1
    /// "N friends listened", from the faces on the picture (§543).
    @State private var friendsOpen = false
    /// How the player comes and goes (`PlayerEntrance`): where its picture
    /// is, and whether a copy flying to or from it covers it now.
    @EnvironmentObject private var playerStage: PlayerStage
    @Environment(\.playerPictureHidden) private var pictureHidden

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
        .overlay { flightLayer }
        .coordinateSpace(name: Self.space)
        .background(FAMColor.background.ignoresSafeArea())
        .animation(.easeOut(duration: 0.2), value: popup)
        // What's next comes and goes on its own transition's timings.
        .animation(.easeOut(duration: 0.2), value: now.nextUp.isEmpty)
        .famSheet(item: Binding(get: { sheet == .vibe ? nil : sheet }, set: { sheet = $0 })) { which in
            sheetView(which)
                .environmentObject(model)
                .environmentObject(player)
                .environmentObject(now)
        }
        // VIBE! is the story editor, over the whole player as on the web.
        .fullScreenCover(isPresented: Binding(get: { sheet == .vibe },
                                              set: { if !$0 && sheet == .vibe { sheet = nil } })) {
            VibeComposer(openMine: { showMyStory() })
                .environmentObject(model)
                .environmentObject(player)
                .environmentObject(now)
        }
        .fullScreenCover(item: $myStory) { reel in
            StoryViewerCover(reel: reel)
                .environmentObject(model)
        }
        // The faces' own card (§394), as a DailyFAM card opens it.
        .famSheet(isPresented: $friendsOpen) {
            friendsSheet
        }
        .onChange(of: now.showQueue) { _, wanted in
            guard wanted else { return }
            now.showQueue = false
            openSheet(.queue)
        }
        .onChange(of: now.queue.count) { old, new in
            // The badge pops when something is queued (0 → 1.25 → 1, 260 ms),
            // as the flight lands (450 ms later) when there is one.
            guard new > old else { return }
            badgePop = 0
            let wait = reduceMotion ? 0 : 0.45
            DispatchQueue.main.asyncAfter(deadline: .now() + wait) {
                withAnimation(.spring(response: 0.26, dampingFraction: 0.55)) { badgePop = 1 }
            }
        }
        .onChange(of: now.queueFlight) { _, flight in
            guard let flight, !reduceMotion, queueFrame != .zero else { return }
            flying = flight
        }
    }

    static let space = "playerRoot"

    /// The copy of the picture flying to the Queue button; the button
    /// bumps as it lands.
    @ViewBuilder private var flightLayer: some View {
        if let flight = flying {
            let start = CGPoint(x: pictureFrame.midX, y: pictureFrame.midY)
            let end = CGPoint(x: queueFrame.midX, y: queueFrame.midY)
            QueueGhost(picture: flight.picture ?? now.picture, from: start, to: end) {
                flying = nil
                withAnimation(.easeOut(duration: 0.1)) { queueBump = 1.3 }
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) {
                    withAnimation(.easeOut(duration: 0.16)) { queueBump = 1 }
                }
            }
            .id(flight.id)
        }
    }

    /// What's next's Back: to the search page for a search, else DailyFAM
    /// (`Back to search` / `Back to DailyFAM`).
    private func backFromNextUp() {
        let fromSearch = now.episode?.surface == "search"
        model.tabRequest = fromSearch ? .search : .daily
        model.showPlayer = false
    }

    /// `viewMyNewStory`: your story read again, once the editor has gone.
    private func showMyStory() {
        Task { @MainActor in
            await model.loadCircle()
            try? await Task.sleep(nanoseconds: 350_000_000)
            if let reel = StoryReel.mine(model) { myStory = reel }
        }
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
            Button { model.showPlayer = false } label: {
                WebIcon(path: Icons.daily, size: 22, line: 2)
                    .foregroundStyle(FAMColor.text)
                    .frame(width: 44, height: 44)
            }
            .buttonStyle(.press)
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
            .buttonStyle(.press)
            Spacer(minLength: 0)
            Button { openSheet(.menu) } label: {
                WebFill(path: Icons.dots(1.9), size: 22)
                    .foregroundStyle(FAMColor.text)
                    .frame(width: 44, height: 44)
            }
            .buttonStyle(.press)
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
                    .background(FrameReporter(space: Self.space) { pictureFrame = $0 })
                    .background(GeometryReader { pic in
                        let frame = pic.frame(in: .global)
                        Color.clear
                            .onAppear { playerStage.pictureFrame = frame }
                            .onChange(of: frame) { _, now in playerStage.pictureFrame = now }
                    })
                    .opacity(pictureHidden ? 0 : 1)
                    .position(x: geo.size.width / 2, y: geo.size.height / 2)
                // Who of your friends listened (§543): top left of the
                // picture, under the captions.
                if let friends = now.listenedFriends {
                    PlayerFriendsPill(friends: friends) { openFriends() }
                        .padding(.leading, 16)
                        .padding(.top, friendsTop(stage: geo.size.height, picture: wide * 3 / 4))
                        .frame(width: geo.size.width, height: geo.size.height, alignment: .topLeading)
                        .transition(.opacity)
                }
                // `.p-demo`: 6 pt from the stage's top, centred, on a server
                // with no working key (`body.demo-mode`).
                if model.demoMode {
                    DemoBadge()
                        .padding(.top, 6)
                        .frame(width: geo.size.width, height: geo.size.height, alignment: .top)
                }
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

    /// `.p-friends`' top: 12 below the picture's top edge, never above 10
    /// from the stage's (the picture can be taller than the stage).
    private func friendsTop(stage: CGFloat, picture: CGFloat) -> CGFloat {
        max(10, stage / 2 - picture / 2 + 12)
    }

    /// `openFriendsListened('_player')`: one presentation at a time.
    private func openFriends() {
        guard now.listenedFriends != nil else { return }
        if sheet == nil && popup == nil {
            friendsOpen = true
            return
        }
        sheet = nil
        popup = nil
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { friendsOpen = true }
    }

    @ViewBuilder private var friendsSheet: some View {
        if let friends = now.listenedFriends {
            FriendsListened(count: max(friends.count, friends.faces.count), faces: friends.faces)
                .environmentObject(model)
        } else {
            FriendsListened(count: 0, faces: [])
                .environmentObject(model)
        }
    }

    /// Share / VIBE! / save (`.p-side`): 24 apart, 34 pt up plus 12 of
    /// padding (§519: it was 10, and SAVE sat on the sources pill).
    private var sideStack: some View {
        VStack(spacing: 24) {
            // Read aloud with the web's names (`aria-label` / `title`).
            SideButton(icon: Icons.menuShare, caption: "share", on: false, tint: FAMColor.copper,
                       spoken: "Share this episode") {
                openSheet(.share)
            }
            SideButton(icon: Icons.reshare, caption: now.vibed ? "vibed" : "vibe",
                       on: now.vibed, tint: FAMColor.copper,
                       spoken: now.vibed ? "Take the vibe back" : "VIBE! this to your friends") {
                tapVibe()
            }
            .modifier(BurstModifier(trigger: now.vibedBurst, word: "VIBED"))
            SideButton(icon: Icons.bookmark, caption: now.saved ? "saved" : "save",
                       on: now.saved, tint: FAMColor.sage,
                       spoken: now.saved ? "Remove from Save for Later" : "Save for later") {
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
                // "from knowledge" opens too: "No sources for this episode."
                // (`openSources`: items or `known`).
                SourcesStrip(sources: sources) { popup = .sources }
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
            prevSlot
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
        .buttonStyle(.press)
        .accessibilityLabel(player.playing ? "Pause" : "Play")
    }

    /// Back one (§550, `#mixPrevBtn`), in the slot that balances next:
    /// only while a myFAM playlist plays - the queue has no "before".
    @ViewBuilder private var prevSlot: some View {
        if model.mixPlay != nil {
            Button { model.prevMixItem() } label: {
                PrevGlyph()
                    .foregroundStyle(FAMColor.text)
                    .frame(width: 48, height: 48)
                    .contentShape(Circle())
            }
            .buttonStyle(BareCircleStyle())
            .accessibilityLabel("Back to the previous episode in this playlist")
        } else {
            Color.clear.frame(width: 48, height: 48)
        }
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
            .buttonStyle(.press)
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
                .background(FrameReporter(space: Self.space) { queueFrame = $0 })
                .scaleEffect(queueBump)
                .overlay(alignment: .topTrailing) { queueBadge }
                .overlay(alignment: .top) {
                    // The caption hangs 34 pt below the circle's top.
                    Text("Queue")
                        .monoLabel(7, color: FAMColor.textFaint, tracking: 0.49)
                        .fixedSize()
                        .offset(y: 34)
                }
        }
        .buttonStyle(.press)
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
            // In over 200 ms (the card lifting, `OverlayModal`), out over
            // 150, the card dropping 8 pt.
            .transition(.famOverlay(reduceMotion: reduceMotion))
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
        case .vibe: EmptyView()
        case .share: ShareSheet()
        case .speed: ActionSheetList(items: speedItems)
        case .length: ActionSheetList(items: lengthItems)
        case .voice: ActionSheetList(items: voiceItems)
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
        items.append(ActionItem(label: pitch, action: {
            let on = !player.keepsPitch
            player.setKeepsPitch(on)
            // Said from every place it is set (`setPitchLock`).
            now.say(on ? "Speed changes keep the voice's pitch" : "Speed changes will shift the voice's pitch")
        }))
        return items
    }

    /// `openVoiceMenu` from the Playback modal (a search only): "Voice for
    /// your searches", the bank with a tick on the chosen one; a new voice
    /// is the same episode voiced again (`AppModel.revoice`).
    private var voiceItems: [ActionItem] {
        var items = [ActionItem(label: "Voice for your searches", group: true)]
        let page = model.voices
        let selected = page?.selected ?? ""
        for v in page?.voices ?? [] {
            let label = v.label + (v.id == selected ? "  \u{2713}" : "")
            let id = v.id
            let name = v.label
            items.append(ActionItem(label: label, action: { model.revoice(id, label: name) }))
        }
        return items
    }

    /// `openLengthMenu` (a search only): the search page's Length, ticked
    /// and moved (`selectedLengthMinutes`); a change made on the player is
    /// a new episode at that length (`regenerateAtNewLength`).
    private var lengthItems: [ActionItem] {
        guard let ep = now.episode else { return [] }
        var items: [ActionItem] = []
        let chosen = model.searchLength
        for m in [1, 1.5, 2, 3, 4, 5.0] {
            let label = "\(FAMAPI.minutes(m)) min" + (m == chosen ? "  \u{2713}" : "")
            items.append(ActionItem(label: label, action: {
                let changed = m != model.searchLength
                model.searchLength = m
                guard changed else { return }
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
    case menu, queue, vibe, share, report, addToMix, speed, length, voice
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
            .pressSqueeze(configuration.isPressed)
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
            .padding(.horizontal, 2)
            // A floor, so the E is never cut as the text grows (`large-text`).
            .frame(minWidth: size, minHeight: size)
            .background(FAMColor.textMuted, in: RoundedRectangle(cornerRadius: 3))
            .accessibilityLabel("Explicit")
    }
}

/// `.p-friends` (§543): the DailyFAM card's faces on the player - up to
/// three 26 pt faces overlapped 6, ringed in the pill's ink, and a copper
/// "+N" for the rest, on rgba(20,18,28,.62) over a blur, a cream hairline
/// round it and a soft shadow under it. A tap lists who listened.
struct PlayerFriendsPill: View {
    let friends: NowPlayingModel.PlayerFriends
    let open: () -> Void

    private static let ground = Color(red: 20 / 255, green: 18 / 255, blue: 28 / 255)
    private static let rim = Color(red: 244 / 255, green: 239 / 255, blue: 228 / 255)

    private var shown: [FriendFace] { Array(friends.faces.prefix(3)) }

    private var count: Int { max(friends.count, friends.faces.count) }

    private var more: Int { count - shown.count }

    private var label: String {
        if count == 1, let face = friends.faces.first {
            let name = face.name.isEmpty ? (face.handle ?? "") : face.name
            return (name.isEmpty ? "A friend" : name) + " listened"
        }
        return "\(count) friends listened"
    }

    var body: some View {
        Button(action: open) { look }
            .buttonStyle(.press)
            .fixedSize()
            .accessibilityLabel(label)
    }

    private var look: some View {
        HStack(spacing: 5) {
            faces
            if more > 0 {
                Text("+\(more)")
                    .font(FAMFont.mono(10, bold: true))
                    .foregroundStyle(FAMColor.copper)
            }
        }
        .padding(.leading, 3)
        .padding(.trailing, more > 0 ? 8 : 3)
        .padding(.vertical, 3)
        .background(Self.ground.opacity(0.62), in: Capsule())
        .background(.ultraThinMaterial, in: Capsule())
        .overlay(Capsule().stroke(Self.rim.opacity(0.45), lineWidth: 1))
        .shadow(color: .black.opacity(0.35), radius: 3, x: 0, y: 1)
    }

    private var faces: some View {
        HStack(spacing: -6) {
            ForEach(Array(shown.enumerated()), id: \.offset) { i, face in
                Face(name: face.name, handle: face.handle ?? "", avatar: face.avatar ?? "",
                     size: 26, initialsSize: 10)
                    .overlay(Circle().stroke(Self.ground.opacity(0.9), lineWidth: 1.5))
                    .zIndex(Double(3 - i))
            }
        }
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
    /// What VoiceOver says, when it is not the caption.
    var spoken: String? = nil
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
        .buttonStyle(.press)
        .accessibilityLabel(spoken ?? caption)
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
                            SourceMark(label: item.label, url: item.url, size: 20)
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
        .buttonStyle(.press)
        .accessibilityLabel("Show this episode's sources")
    }

    private var countText: String {
        let n = sources.items.count
        if n == 0 { return "from knowledge" }
        return n <= 3 ? "\(n)" : "+\(n - 3)"
    }
}

/// A publisher's mark (`srcMark`): its initials on a colour from its name
/// (`srcHue`), and the site's icon over them when it loads - taken from the
/// article URL's origin, and only when there is one.
struct SourceMark: View {
    let label: String
    /// The article's address: its origin's `/favicon.ico` is the logo.
    var url: String? = nil
    var size: CGFloat = 20

    private static let palette: [UInt32] = [0xC9A876, 0x8FAE9A, 0xC3A8E0, 0xE2694F, 0x8FB4D9, 0xD9B38F]

    /// `srcHue`: `sum = (sum * 31 + charCode) % 9973` over UTF-16 units.
    static func hue(_ label: String) -> Int {
        var sum = 0
        for unit in label.utf16 { sum = (sum * 31 + Int(unit)) % 9973 }
        return sum % palette.count
    }

    /// `srcInitials`: "www." off the front, the first dotted word, two letters.
    static func initials(_ label: String) -> String {
        let clean = label.hasPrefix("www.") ? String(label.dropFirst(4)) : label
        let first = clean.split(separator: ".", omittingEmptySubsequences: false).first.map(String.init) ?? ""
        let word = first.isEmpty ? clean : first
        let two = String(word.prefix(2)).uppercased()
        return two.isEmpty ? "?" : two
    }

    /// `originOf(item.url) + "/favicon.ico"`, for an http(s) address only.
    static func logo(_ address: String?) -> URL? {
        guard let address, let parts = URLComponents(string: address),
              let scheme = parts.scheme?.lowercased(), scheme == "https" || scheme == "http",
              let host = parts.host, !host.isEmpty else { return nil }
        let port = parts.port.map { ":\($0)" } ?? ""
        return URL(string: "\(scheme)://\(host)\(port)/favicon.ico")
    }

    var body: some View {
        ZStack {
            Circle().fill(Color(hex: Self.palette[Self.hue(label)]))
            Text(Self.initials(label))
                .font(FAMFont.body(size * 0.4, .bold))
                .foregroundStyle(FAMColor.ink)
            if let url = Self.logo(self.url) {
                FadeInImage(url: url) { image in
                    image.resizable().scaledToFit().padding(size * 0.15)
                        .background(Circle().fill(FAMColor.paper))
                }
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
    /// "Nothing playing yet" was said for this touch.
    @State private var refused = false

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
                        // Nothing playing at all: said once, at the touch
                        // (`makeScrubbable`'s pointerdown). Playing but
                        // nothing arrived yet, the knob holds at the start.
                        guard player.isLoaded, player.phase != .connecting, player.phase != .failed else {
                            if !refused {
                                refused = true
                                now.say("Nothing playing yet")
                            }
                            return
                        }
                        dragging = true
                        let wanted = total * min(max(value.location.x / width, 0), 1)
                        dragValue = min(wanted, player.seekLimit)
                    }
                    .onEnded { value in
                        refused = false
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
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// Something was queued: the picture bumps, as the web's queue target
    /// does when the queued episode lands on it (`Motion.toQueue`).
    @State private var bump = false
    /// The copy flying here from the middle of the page while the player
    /// is down (`toQueue` with no tile frame to start from).
    @State private var flying: QueueFlight?
    @State private var thumbFrame: CGRect = .zero
    @State private var barFrame: CGRect = .zero

    private static let space = "miniRoot"

    var body: some View {
        bar
            .overlay { flightLayer }
            .coordinateSpace(name: Self.space)
            .background(GeometryReader { geo in
                let frame = geo.frame(in: .global)
                Color.clear
                    .onAppear { barFrame = frame }
                    .onChange(of: frame) { _, now in barFrame = now }
            })
            .onChange(of: now.queueFlight) { _, flight in
                guard let flight, !reduceMotion, !model.showPlayer, thumbFrame != .zero else { return }
                flying = flight
            }
    }

    /// The copy in flight, from the screen's middle to the picture.
    @ViewBuilder private var flightLayer: some View {
        if let flight = flying {
            let screen = UIScreen.main.bounds
            // From the card that queued it, else the middle of the page.
            let card = flight.from.flatMap { $0.intersects(screen) ? $0 : nil }
            let origin = card.map { CGPoint(x: $0.midX, y: $0.midY) } ?? CGPoint(x: screen.midX, y: screen.midY)
            let start = CGPoint(x: origin.x - barFrame.minX, y: origin.y - barFrame.minY)
            let end = CGPoint(x: thumbFrame.midX, y: thumbFrame.midY)
            QueueGhost(picture: flight.picture, from: start, to: end) {
                flying = nil
            }
            .id(flight.id)
        }
    }

    private var bar: some View {
        HStack(spacing: 11) {
            thumb
                .background(FrameReporter(space: Self.space) { thumbFrame = $0 })
                .scaleEffect(bump ? 1.3 : 1)
                .onChange(of: now.queue.count) { old, new in
                    guard new > old, !reduceMotion else { return }
                    // As the flight lands, when there is one (450 ms).
                    let wait = model.showPlayer ? 0 : 0.45
                    DispatchQueue.main.asyncAfter(deadline: .now() + wait) {
                        withAnimation(.easeOut(duration: 0.1)) { bump = true }
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) {
                            withAnimation(.easeOut(duration: 0.16)) { bump = false }
                        }
                    }
                }
            VStack(alignment: .leading, spacing: 3) {
                HStack(alignment: .firstTextBaseline, spacing: 5) {
                    if now.explicit && now.episode != nil { PlayerExplicitBadge(size: 15) }
                    WipeText(text: now.episode == nil ? player.title : now.title,
                             font: FAMFont.heading(12, .medium))
                }
                Text(metaLine)
                    .monoLabel(8, tracking: 0.56)
                    .lineLimit(1)
            }
            Spacer(minLength: 4)
            Button { player.toggle() } label: {
                PlayPauseGlyph(playing: player.playing, size: 16)
                    .frame(width: 36, height: 36)
                    .background(FAMColor.copper, in: Circle())
            }
            .buttonStyle(.press)
            .accessibilityLabel(player.playing ? "Pause" : "Play")
            Button { model.stopEpisode() } label: {
                WebIcon(path: Icons.close, size: 16, line: 2.6)
                    .foregroundStyle(FAMColor.textMuted)
                    .frame(width: 26, height: 26)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.press)
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
        // The web's squeeze on the bar's tap (`.press`), let go as soon as
        // the finger moves away.
        .tapSqueeze()
        .onTapGesture {
            // The player grows out of this picture (`pictured` finds
            // `#nowThumb` inside the bar).
            PlayerMotion.shared.pressed(PlayerMotion.shared.mini)
            model.showPlayer = true
        }
    }

    /// The length the bar counts against: the minutes asked for
    /// (`nowBarState.minutes * 60`), whatever the episode turns out to be.
    private var asked: Double {
        if let minutes = now.episode?.key.minutes, minutes > 0 { return minutes * 60 }
        return max(player.total, 0.1)
    }

    /// "{source · }m:ss left" (`startNowBarTicker`). No play on the web
    /// passes a source (`playOpts` has none), so the line is "m:ss left".
    private var metaLine: String {
        let source = ""
        let left = clock(max(0, asked - player.position)) + " left"
        return source.isEmpty ? left : source + " \u{00b7} " + left
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
        .background(GeometryReader { geo in
            // Where the player shrinks into (`#nowThumb`).
            let frame = geo.frame(in: .global)
            Color.clear
                .onAppear { reportThumb(frame) }
                .onChange(of: frame) { _, now in reportThumb(now) }
                .onChange(of: now.picture ?? player.picture) { _, _ in reportThumb(frame) }
                .onDisappear { PlayerMotion.shared.mini = nil }
        })
        .accessibilityHidden(true)
    }

    private func reportThumb(_ frame: CGRect) {
        PlayerMotion.shared.mini = PictureSpot(url: now.picture ?? player.picture, frame: frame, radius: 9)
    }

    private var progress: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Rectangle().fill(FAMColor.borderStrong)
                Rectangle().fill(FAMColor.copper)
                    .frame(width: geo.size.width * min(player.position / asked, 1))
                    .animation(.linear(duration: 0.2), value: player.position)
            }
        }
        .frame(height: 2)
    }
}

/// The card's 7 pt copper dot, growing from its middle as the card comes up
/// (`.toast::before`: scale 0 to 1, .26 s after .06 s); whole with reduced
/// motion.
private struct ToastDot: View {
    @State private var grown = UIAccessibility.isReduceMotionEnabled

    var body: some View {
        Circle()
            .fill(FAMColor.copper)
            .frame(width: 7, height: 7)
            .scaleEffect(grown ? 1 : 0)
            .onAppear {
                guard !grown else { return }
                withAnimation(.timingCurve(0.2, 0.8, 0.2, 1, duration: 0.26).delay(0.06)) { grown = true }
            }
    }
}

/// What an action did, as a card that floats (§550, `.toast` / `.snack`;
/// was §299's plain line on a fade of the page's colour, which over a dark
/// screen - a friend's vibe, the story viewer - was a white band across the
/// picture). The surface colour, a hairline, a shadow and a copper dot,
/// sized to its words and centred, so it reads the same over any screen
/// and covers nothing but itself; an action (Undo, Open) in copper to the
/// right of the words. Where it sits is unchanged (`placeAboveBars`).
struct ToastView: View {
    @EnvironmentObject private var now: NowPlayingModel

    var body: some View {
        ZStack {
            if let toast = now.toast {
                card(toast)
                    .id(toast.id)
                    // In: up 8 pt from 0.98 (`translateY(8px) scale(0.98)`).
                    .transition(.opacity.combined(with: .offset(y: 8)).combined(with: .scale(scale: 0.98)))
            }
        }
        .animation(.timingCurve(0.2, 0.8, 0.2, 1, duration: 0.22), value: now.toast)
    }

    private func card(_ toast: NowPlayingModel.Toast) -> some View {
        let shape = RoundedRectangle(cornerRadius: 14, style: .continuous)
        return HStack(alignment: .center, spacing: 10) {
            ToastDot()
            HStack(alignment: .firstTextBaseline, spacing: 14) {
                Text(toast.text)
                    .font(FAMFont.body(13.5, .medium))
                    .foregroundStyle(FAMColor.text)
                    .multilineTextAlignment(.leading)
                    .lineSpacing(13.5 * 0.35 - 4)
                    .lineLimit(toast.action == nil ? 3 : 1)
                    .truncationMode(.tail)
                if let action = toast.action {
                    Button(action) { now.toastTapped() }
                        .font(FAMFont.body(13.5, .bold))
                        .foregroundStyle(FAMColor.copper)
                        .buttonStyle(.press)
                        .fixedSize()
                }
            }
        }
        .padding(.top, 11)
        .padding(.bottom, 11)
        .padding(.leading, 14)
        .padding(.trailing, 16)
        .background(FAMColor.surface, in: shape)
        .overlay(shape.stroke(FAMColor.borderStrong, lineWidth: 1))
        // `0 16px 34px -14px rgba(0,0,0,.55), 0 2px 6px rgba(0,0,0,.12)`:
        // SwiftUI has no spread, so the deep one is fainter and tighter.
        .shadow(color: Color.black.opacity(0.3), radius: 11, x: 0, y: 12)
        .shadow(color: Color.black.opacity(0.12), radius: 3, x: 0, y: 2)
        // Sized to its words, never wider than the screen less 16 a side
        // (`max-width:calc(100% - 32px)` on a phone), centred.
        .fixedSize(horizontal: false, vertical: true)
        .padding(.horizontal, 16)
        .frame(maxWidth: .infinity, alignment: .center)
        // Only the action takes a tap; the card leaves what is under it
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
