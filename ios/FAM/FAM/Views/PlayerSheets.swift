import SwiftUI
import UIKit

// The sheets and overlays the player opens, each ported from the web's
// (`static/index.html`): Go Deeper (`openGoDeeper`), the ⋯ menu
// (`openPlayerMenu`), Sources (`openSources`), Playback
// (`openSpeedLengthModal`), the Queue (`openQueue`), VIBE! (`toggleEcho`),
// Share (`openShareModal`) and What's next (`maybeOfferNextUp`). Go Deeper,
// Sources and Playback are the web's centred modals, drawn over the player
// (`OverlayModal`); the captions card is in PlayerParts.swift. Words are the
// web's own.

// MARK: - Go Deeper

/// "Go deeper" (`#goDeeperOverlay`): the suggestion chip ("Pick up the
/// thread" - the episode's `<<NEXT:>>` - or "Follow the thread" and a line
/// built from the title), the box, the six lengths, Cancel / Continue. The
/// episode keeps playing under it; one that ends under it starts nothing
/// (`goDeeperIsOpen`). Over the player it is the centred modal (`close`);
/// from a message it is a sheet.
struct GoDeeperSheet: View {
    @EnvironmentObject private var now: NowPlayingModel
    @Environment(\.dismiss) private var dismiss
    /// An episode a message carries, before it has been played
    /// (`goDeeperOnShared`): the follow-up builds on it, not on the player.
    var base: SharedEpisode? = nil
    /// Where a follow-up of `base` goes (the chat plays it).
    var onGo: ((String, Double, Bool) -> Void)? = nil
    /// Set when drawn as the player's modal: how it closes.
    var close: (() -> Void)? = nil
    @State private var typed = ""
    @State private var minutes: Double = 1.5
    @State private var baseThread = ""
    /// The AI question is up over the sheet (`withAiConsent`): one ask at a time.
    @State private var asking = false
    @FocusState private var typing: Bool

    private static let lengths: [Double] = [1, 1.5, 2, 3, 4, 5]

    private var thread: String { base == nil ? now.thread : baseThread }
    private var baseTitle: String { base?.title ?? now.title }

    private var suggestion: String {
        if !thread.isEmpty { return thread }
        return baseTitle.isEmpty ? "" : "What's the bigger story behind \(baseTitle)?"
    }

    var body: some View {
        if close == nil {
            form
                .padding(20)
                .background(FAMColor.surface)
        } else {
            form
        }
    }

    private func shut() {
        if let close { close() } else { dismiss() }
    }

    private var form: some View {
        VStack(alignment: .leading, spacing: 0) {
            ModalTitle(text: "Go deeper")
            Text("Ask a follow-up, or steer where this goes next")
                .monoLabel(9.5, tracking: 0.76)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.bottom, 8)
            if !suggestion.isEmpty {
                chip.padding(.bottom, 10)
            }
            TextField("", text: $typed, prompt: Text("Or type your own follow-up").foregroundColor(FAMColor.textFaint))
                .font(FAMFont.body(13))
                .foregroundStyle(FAMColor.text)
                .focused($typing)
                .padding(.horizontal, 12)
                .frame(minHeight: 41)
                .background(FAMColor.background, in: RoundedRectangle(cornerRadius: 10))
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(FAMColor.borderStrong))
                .submitLabel(.go)
                .onSubmit { confirm() }
            Text("Length (minutes)")
                .monoLabel(9.5, tracking: 0.76)
                .padding(.top, 22)
                .padding(.bottom, 8)
            lengthRow
            // No gap above the buttons: the web's row touches the lengths.
            HStack(spacing: 8) {
                Spacer()
                ModalButton(label: "Cancel") { shut() }
                ModalButton(label: "Continue", primary: true) { confirm() }
            }
        }
        .onAppear {
            now.goDeeperOpen = true
            // The episode's own length, rounded to a half and kept to 1-5
            // (`offeredMinutes`).
            let m = ((base?.minutes ?? now.episode?.key.minutes ?? 1.5) * 2).rounded() / 2
            minutes = min(max(m, 1), 5)
        }
        .onDisappear { now.goDeeperOpen = false }
        .task {
            // A shared episode's own predicted follow-up, from what is kept.
            guard let base else { return }
            let key = EpisodeKey(query: base.query, minutes: base.minutes)
            if let next = try? await now.api.next(key, cachedOnly: true) { baseThread = next.thread ?? "" }
        }
    }

    private var chip: some View {
        Button { go(suggestion, typed: false) } label: {
            VStack(alignment: .leading, spacing: 4) {
                Text(thread.isEmpty ? "Follow the thread" : "Pick up the thread")
                    .monoLabel(8, tracking: 0.64)
                Text(suggestion)
                    .font(FAMFont.body(12, .semibold))
                    .foregroundStyle(FAMColor.text)
                    .multilineTextAlignment(.leading)
                    .lineSpacing(12 * 0.35 - 3)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 14)
            .padding(.vertical, 11)
            .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(FAMColor.copper))
        }
        .buttonStyle(.press)
    }

    private var lengthRow: some View {
        HStack(spacing: 6) {
            ForEach(Self.lengths, id: \.self) { value in
                lengthPill(value)
            }
        }
    }

    private func lengthPill(_ value: Double) -> some View {
        let on = value == minutes
        return Button { minutes = value } label: {
            Text(FAMAPI.minutes(value))
                .font(FAMFont.mono(9.5, bold: on))
                .foregroundStyle(on ? FAMColor.ink : FAMColor.textMuted)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 8)
                .background(on ? FAMColor.copper : FAMColor.surface, in: Capsule())
                .overlay(Capsule().stroke(on ? FAMColor.copper : FAMColor.borderStrong))
        }
        .buttonStyle(.press)
        .accessibilityLabel("\(FAMAPI.minutes(value)) minutes")
        .accessibilityAddTraits(on ? .isSelected : [])
    }

    private func confirm() {
        let words = typed.trimmingCharacters(in: .whitespacesAndNewlines)
        if words.isEmpty {
            if suggestion.isEmpty { typing = true; return }
            go(suggestion, typed: false)
        } else {
            go(words, typed: words != suggestion)
        }
    }

    /// A Go Deeper question is the listener's words too (consent.py): the
    /// AI question first, for any Go Deeper - the suggestion's too - with
    /// this still open over the player (`confirmGoDeeper`). A message's Go
    /// Deeper is a sheet, which would cover the question, so it closes
    /// first and asks over the conversation.
    private func go(_ words: String, typed: Bool) {
        guard !asking else { return }
        if base != nil {
            shut()
            now.goDeeperOpen = false
            let chosen = minutes
            let suggested = suggestion
            let send = onGo
            let ensure = now.ensureConsent
            Task { @MainActor in
                guard await ensure() else { return }
                let question = words.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !question.isEmpty, let send else { return }
                // FAM's own suggestion is not the listener's words (`own`).
                send(question, chosen, typed || question != suggested)
            }
            return
        }
        asking = true
        let ensure = now.ensureConsent
        Task { @MainActor in
            let allowed = await ensure()
            asking = false
            guard allowed else { return }
            shut()
            now.goDeeperOpen = false
            now.followUp(words, minutes: minutes, typed: typed)
        }
    }
}

// MARK: - The ⋯ menu

/// `openPlayerMenu`: the episode's picture, title and searcher, then the
/// rows in the web's order with the web's icons (`MENU_ICONS`), and Cancel.
struct PlayerMenu: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var now: NowPlayingModel
    @EnvironmentObject private var player: StreamPlayer
    @Environment(\.dismiss) private var dismiss
    @Binding var sheet: PlayerSheet?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                header
                rows
                SheetCancelButton { dismiss() }
            }
            .padding(.bottom, 12)
        }
        .background(FAMColor.surface)
    }

    private var header: some View {
        HStack(spacing: 12) {
            // The picture only when there is one (`menuHead`'s thumb).
            if let pic = now.picture ?? player.picture {
                Picture(url: pic)
                    .frame(width: 44, height: 44)
                    .background(FAMColor.surface2)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
            }
            VStack(alignment: .leading, spacing: 3) {
                Text(now.episode == nil ? player.title : now.title)
                    .font(FAMFont.body(14, .semibold))
                    .foregroundStyle(FAMColor.text)
                    .lineLimit(3)
                    .fixedSize(horizontal: false, vertical: true)
                if !now.searcher.isEmpty {
                    Text(now.searcher)
                        .font(FAMFont.body(11.5))
                        .foregroundStyle(FAMColor.textMuted)
                        .lineLimit(1)
                }
            }
            Spacer(minLength: 0)
        }
        // `.sheet-head`: padding 4 4 12 inside the sheet's 10 top, a faint
        // rule under it and 6 below.
        .padding(.horizontal, 4)
        .padding(.top, 14)
        .padding(.bottom, 12)
        .overlay(alignment: .bottom) { Rectangle().fill(FAMColor.border).frame(height: 1) }
        .padding(.bottom, 6)
    }

    @ViewBuilder private var rows: some View {
        row("Share", Icons.menuShare) { open(.share) }
        row(now.vibed ? "Take back your VIBE!" : "VIBE! to your friends", Icons.reshare) {
            if now.vibed {
                dismiss()
                now.unvibe()
            } else if now.episode == nil {
                dismiss()
                now.say("Play something first")
            } else {
                open(.vibe)
            }
        }
        row(now.saved ? "Remove from Save for Later" : "Save for later", Icons.bookmark) {
            dismiss()
            now.toggleSave()
        }
        row("Closed captions \u{00B7} \(now.captionsOn ? "On" : "Off")", PlayerIcons.captionsMenu) {
            dismiss()
            now.setCaptions(!now.captionsOn)
        }
        Button {
            if (now.episode?.key.query ?? "").isEmpty {
                dismiss()
                now.say("Play something first")
            } else {
                open(.addToMix)
            }
        } label: {
            HStack(spacing: 14) {
                menuIcon(Icons.circlePlus)
                AddToPlaylistLabel()
                Spacer()
            }
            .padding(.horizontal, 22)
            .padding(.vertical, 14)
            .contentShape(Rectangle())
        }
        .buttonStyle(.press)
        if model.canRemoveFromPlayingMix {
            row("Remove from this playlist", PlayerIcons.circleMinus) {
                dismiss()
                model.removeFromPlayingMix()
            }
        }
        row("Add to Queue", Icons.addQueue) {
            dismiss()
            if let ep = now.episode { now.addToQueue(ep) } else { now.say("Nothing to queue") }
        }
        row("Go to Queue", Icons.queue) { open(.queue) }
        if now.episode != nil {
            row("Report", PlayerIcons.flag, danger: true) { open(.report) }
        }
    }

    private func open(_ next: PlayerSheet) {
        // One sheet at a time: close this, then open the next.
        sheet = nil
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { sheet = next }
    }

    private func menuIcon(_ path: Path) -> some View {
        WebIcon(path: path, size: 18, line: 1.8)
            .foregroundStyle(FAMColor.textMuted)
            .frame(width: 22)
    }

    private func row(_ label: String, _ icon: Path, danger: Bool = false,
                     action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 14) {
                menuIcon(icon)
                Text(label)
                    .font(FAMFont.body(13.5, .semibold))
                    .foregroundStyle(danger ? FAMColor.coral : FAMColor.text)
                Spacer()
            }
            .padding(.horizontal, 22)
            .padding(.vertical, 14)
            .contentShape(Rectangle())
        }
        .buttonStyle(.press)
    }
}

/// "Add to myFAM Playlist", with "myFAM" drawn as the wordmark
/// (`addToPlaylistLabel`, `myFamMark`); read aloud as the words.
struct AddToPlaylistLabel: View {
    var body: some View {
        // "my" in the row's own type; F A M the heading's glyphs, one cap
        // tall and muted like every svg in a menu row (`.menu-row svg`).
        HStack(alignment: .firstTextBaseline, spacing: 0) {
            Text("Add to my")
            FAMLetters(capHeight: 13.5 * 0.7, color: FAMColor.textMuted)
                .padding(.leading, 13.5 * 0.15)
                .alignmentGuide(.firstTextBaseline) { d in d[.bottom] }
            Text(" Playlist")
        }
        .font(FAMFont.body(13.5, .semibold))
        .foregroundStyle(FAMColor.text)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Add to myFAM Playlist")
    }
}

// MARK: - Sources

/// `openSources`: the centred card, "Sources" and a 30 pt round X; a row
/// per publisher (its mark, the headline - green for a live feed, copper
/// for an attachment - and "host · tier · date", ↗ when it opens); the
/// cross-check note under them.
struct SourcesSheet: View {
    @EnvironmentObject private var now: NowPlayingModel
    @Environment(\.openURL) private var openURL
    let close: () -> Void

    var body: some View {
        HStack {
            Text("Sources")
                .font(FAMFont.heading(16, .medium))
                .foregroundStyle(FAMColor.text)
            Spacer()
            Button(action: close) {
                WebIcon(path: Icons.close, size: 14, line: 2)
                    .foregroundStyle(FAMColor.text)
                    .frame(width: 30, height: 30)
                    .overlay(Circle().stroke(FAMColor.borderStrong))
            }
            .buttonStyle(.press)
            .accessibilityLabel("Close")
        }
        .padding(.bottom, 8)
        // The card grows with its rows up to 76% of the screen, then scrolls
        // (`max-height: 76%`). Measured rather than `.frame(maxHeight:)`,
        // which would always take the whole 76%.
        ScrollView {
            list
                .background(GeometryReader { geo in
                    Color.clear
                        .onAppear { listHeight = geo.size.height }
                        .onChange(of: geo.size.height) { _, h in listHeight = h }
                })
        }
        .scrollBounceBehavior(.basedOnSize)
        .frame(height: min(max(listHeight, 40), UIScreen.main.bounds.height * 0.76 - 90))
    }

    @State private var listHeight: CGFloat = 0

    private var list: some View {
        VStack(alignment: .leading, spacing: 0) {
                let items = now.sources?.items ?? []
                if items.isEmpty {
                    Text("No sources for this episode.")
                        .font(FAMFont.body(12))
                        .foregroundStyle(FAMColor.textMuted)
                        .padding(.vertical, 10)
                }
                ForEach(items, id: \.self) { item in
                    sourceRow(item)
                }
                if let r = now.sources?.retrievers, r.count > 1 {
                    Text("Cross-checked across \(r.joined(separator: " and ")).")
                        .font(FAMFont.body(11))
                        .foregroundStyle(FAMColor.textMuted)
                        .padding(.top, 12)
                }
            }
    }

    private func sourceRow(_ item: SourceItem) -> some View {
        Button {
            if let s = item.url, let url = URL(string: s) { openURL(url) }
        } label: {
            HStack(spacing: 12) {
                SourceMark(label: item.label, url: item.url, size: 30)
                VStack(alignment: .leading, spacing: 3) {
                    Text(item.title?.isEmpty == false ? item.title! : item.label)
                        .font(FAMFont.body(12, .semibold))
                        .foregroundStyle(nameColor(item))
                        .multilineTextAlignment(.leading)
                    Text([item.label, item.tier, item.at].compactMap { $0 }.filter { !$0.isEmpty }
                        .joined(separator: " \u{00B7} "))
                        .monoLabel(8, color: FAMColor.textFaint, tracking: 0.48)
                }
                Spacer(minLength: 0)
                if item.url != nil {
                    Text("\u{2197}")
                        .font(FAMFont.body(13))
                        .foregroundStyle(FAMColor.textFaint)
                }
            }
            .padding(.vertical, 10)
            .overlay(alignment: .bottom) { Rectangle().fill(FAMColor.border).frame(height: 1) }
        }
        .buttonStyle(.press)
        .disabled(item.url == nil)
    }

    private func nameColor(_ item: SourceItem) -> Color {
        switch item.kind {
        case "live": return FAMColor.sage
        case "attachment": return FAMColor.copper
        default: return FAMColor.text
        }
    }
}

// MARK: - Playback (`openSpeedLengthModal`)

/// "Playback": Speed - and, for a search, Length and Voice - each a white
/// row that opens its choices as an action sheet; Done.
struct PlaybackSheet: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var player: StreamPlayer
    @EnvironmentObject private var now: NowPlayingModel
    let close: () -> Void
    /// The action sheet a row opens (speed, length, voice).
    let open: (PlayerSheet) -> Void

    var body: some View {
        ModalTitle(text: "Playback")
        field("Speed", "\(FAMAPI.minutes(player.rate))x") { open(.speed) }
        // Length and voice only for a search (§147): everything else is the
        // browse length, in the bank voice it was given.
        if let ep = now.episode, ep.surface == "search" {
            field("Length", "\(FAMAPI.minutes(ep.key.minutes)) min") { open(.length) }
            field("Voice", voiceLabel) { openVoice() }
        }
        ModalButton(label: "Done", primary: true, wide: true) { close() }
            .padding(.top, 4)
            .task { if model.voices == nil { await model.loadSearchSettings() } }
    }

    /// `voiceLabel(selectedVoice)`: the chosen voice's name, else the
    /// bank's first, else "Ian" (the web's fallback).
    private var voiceLabel: String {
        let bank = model.voices?.voices ?? []
        let selected = model.voices?.selected ?? ""
        if let chosen = bank.first(where: { $0.id == selected }) { return chosen.label }
        return bank.first?.label ?? "Ian"
    }

    /// `openVoiceMenu`: with no voices reported, the web's sentence instead.
    private func openVoice() {
        if let page = model.voices, !page.voices.isEmpty {
            open(.voice)
        } else {
            now.say("No voices reported by the server")
        }
    }

    private func field(_ label: String, _ value: String, action: @escaping () -> Void) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label).monoLabel(9.5, tracking: 0.76)
            Button(action: action) {
                Text(value)
                    .font(FAMFont.body(13, .semibold))
                    .foregroundStyle(FAMColor.text)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 12)
                    .frame(minHeight: 39)
                    .background(FAMColor.background, in: RoundedRectangle(cornerRadius: 10))
                    .overlay(RoundedRectangle(cornerRadius: 10).stroke(FAMColor.borderStrong))
            }
            .buttonStyle(.press)
            .accessibilityLabel("\(label): \(value)")
        }
        .padding(.bottom, 14)
    }
}

// MARK: - The queue

/// `openQueue` (§519): "Queue", then Now playing (its picture, or ▶; the
/// detour line while Go Deeper is a detour of a playlist), "Next from
/// {mix}" numbered from the next one and not tappable, and "Next in queue"
/// - a tap plays it and ends any playlist, × takes it out (Undo). Close.
struct QueueSheet: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var now: NowPlayingModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Queue")
                .font(FAMFont.heading(19, .semibold))
                .foregroundStyle(FAMColor.text)
                .padding(.horizontal, 22)
                .padding(.top, 16)
                .padding(.bottom, 4)
                .accessibilityAddTraits(.isHeader)
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    group("Now playing")
                    nowRow
                    mixRows
                    group("Next in queue")
                    if now.queue.isEmpty {
                        empty("Your queue is empty. Add episodes from the three dots on the player or on a DailyFAM tile.")
                    }
                    ForEach(Array(now.queue.enumerated()), id: \.element.id) { index, entry in
                        queueRow(entry, place: index + 1)
                            // `removeRow`: left 45 % and faded, then folded.
                            .transition(.rowRemoval)
                    }
                }
                .animation(.rowFold, value: now.queue)
            }
            Button { dismiss() } label: {
                Text("Close")
                    .font(FAMFont.body(13, .bold))
                    .foregroundStyle(FAMColor.textMuted)
                    .frame(maxWidth: .infinity)
                    .padding(12)
                    .background(FAMColor.background, in: RoundedRectangle(cornerRadius: 14))
            }
            .buttonStyle(.press)
            .padding(.horizontal, 16)
            .padding(.top, 8)
        }
        .padding(.bottom, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(FAMColor.surface)
    }

    private func group(_ text: String) -> some View {
        Text(text)
            .monoLabel(9, color: FAMColor.textFaint, tracking: 1.08)
            .padding(.horizontal, 22)
            .padding(.top, 16)
            .padding(.bottom, 4)
    }

    private func empty(_ text: String) -> some View {
        Text(text)
            .font(FAMFont.body(12.5))
            .foregroundStyle(FAMColor.textFaint)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, 22)
            .padding(.top, 6)
            .padding(.bottom, 8)
    }

    @ViewBuilder private var nowRow: some View {
        if now.episode == nil {
            empty("Nothing is playing.")
        } else {
            let detourLine = (model.playingDetour && model.mixPlay != nil)
                ? "Going deeper \u{00B7} \(model.mixPlay?.name ?? "your playlist") carries on after" : ""
            HStack(spacing: 12) {
                pic(now.picture, mark: "\u{25B6}", now: true)
                texts(now.title, sub: detourLine, now: true)
                Spacer(minLength: 0)
            }
            .padding(.leading, 22)
            .padding(.trailing, 14)
            .padding(.vertical, 7)
            .frame(minHeight: 56)
        }
    }

    /// The playlist's episode a Go Deeper interrupted: it comes back first,
    /// where it was (the owner's call, PARITY.md), so it heads the list.
    private var resuming: InterruptedItem? {
        model.playingDetour ? model.mixPlay?.interrupted : nil
    }

    @ViewBuilder private var mixRows: some View {
        if let list = model.mixPlay, !model.mixUpNext.isEmpty || resuming != nil {
            let name = list.name.isEmpty ? "your playlist" : list.name
            let offset = resuming == nil ? 0 : 1
            group("Next from \(name)")
            if let back = resuming {
                HStack(spacing: 12) {
                    pic(back.episode.picture, mark: "1", now: false)
                    texts(back.episode.title, sub: name, now: false)
                    Spacer(minLength: 0)
                }
                .padding(.leading, 22)
                .padding(.trailing, 14)
                .padding(.vertical, 7)
                .frame(minHeight: 56)
            }
            ForEach(Array(model.mixUpNext.enumerated()), id: \.offset) { index, item in
                HStack(spacing: 12) {
                    pic(nil, mark: "\(index + 1 + offset)", now: false)
                    texts(item.title.isEmpty ? item.query : item.title, sub: name, now: false)
                    Spacer(minLength: 0)
                }
                .padding(.leading, 22)
                .padding(.trailing, 14)
                .padding(.vertical, 7)
                .frame(minHeight: 56)
            }
        }
    }

    private func queueRow(_ entry: QueueEntry, place: Int) -> some View {
        HStack(spacing: 12) {
            Button {
                dismiss()
                model.stopEpisode()
                now.playFromQueue(entry)
            } label: {
                HStack(spacing: 12) {
                    pic(entry.episode.picture, mark: "\(place)", now: false)
                    texts(entry.episode.title, sub: "Queued", now: false)
                    Spacer(minLength: 0)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.press)
            Button { now.removeFromQueue(entry) } label: {
                Text("\u{00D7}")
                    .font(.system(size: 20))
                    .foregroundStyle(FAMColor.textMuted)
                    .frame(width: 40, height: 40)
            }
            .buttonStyle(.press)
            .accessibilityLabel("Take \(entry.episode.title) out of the queue")
        }
        .padding(.leading, 22)
        .padding(.trailing, 14)
        .padding(.vertical, 7)
        .frame(minHeight: 56)
    }

    /// `.q-pic`: 42 pt, radius 8, the picture or its place in line.
    private func pic(_ url: URL?, mark: String, now playing: Bool) -> some View {
        ZStack {
            FAMColor.surface2
            if let url {
                AsyncImage(url: url) { image in
                    image.resizable().scaledToFill()
                } placeholder: { Color.clear }
            } else {
                Text(mark)
                    .font(FAMFont.mono(playing ? 12 : 11, bold: true))
                    .foregroundStyle(playing ? FAMColor.copper : FAMColor.textMuted)
            }
        }
        .frame(width: 42, height: 42)
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .accessibilityHidden(true)
    }

    private func texts(_ title: String, sub: String, now playing: Bool) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
                .font(FAMFont.body(14, .semibold))
                .foregroundStyle(playing ? FAMColor.copper : FAMColor.text)
                .lineLimit(3)
                .fixedSize(horizontal: false, vertical: true)
            if !sub.isEmpty {
                Text(sub)
                    .font(FAMFont.body(11.5))
                    .foregroundStyle(FAMColor.textMuted)
                    .lineLimit(1)
            }
        }
    }
}

// MARK: - VIBE!

/// VIBE! (`toggleEcho` → `openVibeComposer`): the story editor on the
/// episode playing, holding that episode, so a card that turns underneath
/// does not change what is posted. Its picture is the player's, as a path
/// on the server, else the one `/api/episode/card` gives (`StoryEditor`).
struct VibeComposer: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var now: NowPlayingModel
    @EnvironmentObject private var player: StreamPlayer
    /// The snack's View: your story, read again.
    var openMine: (() -> Void)? = nil

    var body: some View {
        StoryEditor(subject: subject, openMine: openMine)
            .environmentObject(model)
    }

    private var subject: StoryEditorSubject {
        let key = now.episode?.key ?? EpisodeKey(query: "", minutes: FAMAPI.browseMinutes)
        let thumb = StoryEditorSubject.thumbPath(now.picture ?? player.picture)
        return .episode(key, title: now.title, thumb: thumb, thread: now.thread)
    }
}

// MARK: - Share

/// "Share this FAM" (`openShareModal`, shots 45-48): send it inside FAM to
/// a group or a person - with the id of the episode heard, so they hear
/// that one (§288) - or ANYWHERE ELSE through the server's destinations
/// (`POST /api/share`), with iOS's share sheet for the rest. Opened from a
/// tile's or a story's ⋯ it shares that episode (`openShareModal(ep)`),
/// else the one playing.
struct ShareSheet: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var now: NowPlayingModel
    @Environment(\.dismiss) private var dismiss
    @State private var link: SharedLink?
    @State private var problem = ""
    /// The rows' own height, so the card is as tall as they are and
    /// scrolls past 500 pt (one copy of the rows: a picked friend is kept).
    @State private var rowsHeight: CGFloat = 0
    /// What a tile's or a story's ⋯ shares; nil for the episode playing.
    let subject: ShareSubject?

    init(subject: ShareSubject? = nil) {
        self.subject = subject
    }

    /// The episode this sheet shares: the caller's, else the one playing
    /// with the id of the one heard (`X-FAM-Episode`).
    private var current: ShareSubject? {
        if let subject { return subject }
        guard let ep = now.episode else { return nil }
        return ShareSubject(key: ep.key, title: now.title, episode: model.player.episodeID)
    }

    /// The web's centred `.modal-card` ("Share this FAM" over the dim, a
    /// tap on the dim or Cancel closes it), its rows scrolling inside it
    /// once they are taller than the card may be.
    var body: some View {
        ModalCard(close: { dismiss() }) {
            Text("Share this FAM")
                .font(FAMFont.heading(16, .medium))
                .foregroundStyle(FAMColor.text)
                .padding(.bottom, 14)
            ScrollView {
                shareRows
                    .background(GeometryReader { geo in
                        Color.clear
                            .onAppear { rowsHeight = geo.size.height }
                            .onChange(of: geo.size.height) { _, height in rowsHeight = height }
                    })
            }
            .scrollBounceBehavior(.basedOnSize)
            .frame(height: min(max(rowsHeight, 60), 500))
            ModalButton(label: "Cancel", wide: true) { dismiss() }
                .padding(.top, 16)
        }
        .environmentObject(model.social)
        // The toast over the sheet: a send's refusal in the server's words
        // ("You can message people who follow you back.") would be under it.
        .overlay(alignment: .bottom) {
            ToastView()
                .environmentObject(model.nowPlaying)
                .padding(.bottom, 70)
        }
        .task { await makeLink() }
    }

    private var shareRows: some View {
        VStack(alignment: .leading, spacing: 0) {
            SendInsideFAM(send: { to, label in await sendInside(to: to, label: label) },
                          sent: { dismiss() })
            Text("Anywhere else").monoLabel(9.5, tracking: 0.76)
                .padding(.top, 20)
                .padding(.bottom, 4)
            if let link {
                ShareTargetRows(url: link.url, targets: link.targets ?? [:],
                                isPublic: link.public ?? true,
                                card: link.card ?? "") { dismiss() }
            } else {
                Text(problem.isEmpty ? "Making a link\u{2026}" : problem)
                    .font(FAMFont.body(12))
                    .foregroundStyle(FAMColor.textMuted)
                    .padding(.vertical, 10)
            }
        }
    }

    private func makeLink() async {
        guard let ep = current else {
            problem = "Play something first"
            return
        }
        do {
            link = try await model.api.share(ep.key, title: ep.title)
        } catch {
            // The server's sentence, else the web's (`loadShareTargets`).
            problem = SocialModel.sentence(error, else: "Could not make a share link")
        }
    }

    /// By its question and length, and - for the episode playing - the id
    /// of the one heard.
    private func sendInside(to: String, label: String) async -> Bool {
        guard let ep = current else {
            model.nowPlaying.say("Play something first")
            return false
        }
        return await model.social.sendShare(to: to, label: label, query: ep.key.query,
                                            minutes: ep.key.minutes, title: ep.title,
                                            episode: ep.episode)
    }
}

/// An episode a tile's or a story's ⋯ hands the share sheet.
struct ShareSubject: Identifiable {
    let id = UUID()
    let key: EpisodeKey
    let title: String
    var episode: String = ""
}

struct ActivityView: UIViewControllerRepresentable {
    let items: [Any]
    let done: () -> Void

    func makeUIViewController(context: Context) -> UIActivityViewController {
        let vc = UIActivityViewController(activityItems: items, applicationActivities: nil)
        vc.completionWithItemsHandler = { _, _, _, _ in done() }
        return vc
    }

    func updateUIViewController(_ vc: UIActivityViewController, context: Context) {}
}

// MARK: - What's next

/// After an episode (`post-episode-grid`): "What’s next", four tiles - the
/// first, Go Deeper's own suggestion, starts in 15 s, its copper bar
/// emptying - the search box (touching it pauses the countdown for good)
/// and "Back to search" / "Back to DailyFAM". The dim fades in over 200 ms
/// (`mo-in`) while the card rises over 360 ms (`nextup-in`); it closes as
/// every modal does, 150 ms with the card dropping 8 pt. Reduce Motion:
/// the card does not move, the dim fades in over 120 ms, closing is at once.
struct NextUpOverlay: View {
    @EnvironmentObject private var now: NowPlayingModel
    @EnvironmentObject private var model: AppModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let onBack: () -> Void
    @State private var words = ""
    @State private var shown = false
    @FocusState private var typing: Bool

    var body: some View {
        let still = shown || reduceMotion
        ZStack {
            Color(red: 10 / 255, green: 9 / 255, blue: 15 / 255).opacity(0.62).ignoresSafeArea()
            card
                .opacity(still ? 1 : 0)
                .offset(y: still ? 0 : 12)
                .scaleEffect(still ? 1 : 0.985)
                .dropsOnLeave()
        }
        .transition(.famOverlay(reduceMotion: reduceMotion))
        .onAppear {
            guard !reduceMotion else { shown = true; return }
            withAnimation(.timingCurve(0.22, 0.7, 0.3, 1, duration: 0.36)) { shown = true }
        }
        .task {
            await model.surveys.offerEpisode(id: model.player.episodeID, title: now.title,
                                             signedIn: model.listener?.authenticated == true)
        }
        .onDisappear { model.surveys.clearPulse() }
    }

    private var subline: String {
        if now.nextUpPaused { return "Paused - pick one or search" }
        return now.nextUpLeft > 0 ? "First one starts in \(now.nextUpLeft)" : "Starting\u{2026}"
    }

    /// With the episode check open the card can outgrow a small phone:
    /// it scrolls then, and only then.
    private var card: some View {
        ViewThatFits(in: .vertical) {
            cardContent
            ScrollView { cardContent }
                .scrollBounceBehavior(.basedOnSize)
        }
        .padding(18)
        .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 20))
        .overlay(RoundedRectangle(cornerRadius: 20).stroke(FAMColor.borderStrong))
        .shadow(color: .black.opacity(0.35), radius: 20, y: 20)
        .padding(.horizontal, 27)
    }

    private var cardContent: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("What\u{2019}s next")
                .font(FAMFont.heading(16, .semibold))
                .foregroundStyle(FAMColor.text)
                .padding(.bottom, 3)
            Text(subline)
                .monoLabel(9, color: FAMColor.textFaint, tracking: 0.63)
                .padding(.bottom, 14)
            // The episode check (`#surveyPulse`): only when the server offers
            // it; touching it pauses the countdown, as the search box does.
            SurveyPulse(surveys: model.surveys, onTouch: { now.pauseNextUp() })
            LazyVGrid(columns: [GridItem(.flexible(), spacing: 9), GridItem(.flexible())], spacing: 9) {
                ForEach(Array(now.nextUp.enumerated()), id: \.element.id) { index, tile in
                    tileView(tile, index: index)
                }
            }
            searchBox.padding(.top, 12)
            Button {
                now.stopNextUp()
                onBack()
            } label: {
                Text(now.episode?.surface == "search" ? "Back to search" : "Back to DailyFAM")
                    .monoLabel(10, color: FAMColor.textMuted, tracking: 0.7)
                    .frame(maxWidth: .infinity)
                    .padding(11)
            }
            .buttonStyle(.press)
            .padding(.top, 14)
        }
    }

    private func tileView(_ tile: NextUpTile, index: Int) -> some View {
        let lead = index == 0 && tile.deeper
        return Button { now.playNextUp(index) } label: {
            VStack(alignment: .leading, spacing: 0) {
                Text(tile.title)
                    .font(FAMFont.heading(12.5, .semibold))
                    .foregroundStyle(FAMColor.text)
                    .lineLimit(3)
                    .multilineTextAlignment(.leading)
                Spacer(minLength: 7)
                Text(tile.sub)
                    .monoLabel(8, color: lead ? FAMColor.copper : FAMColor.textFaint, tracking: 0.48)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
            }
            .frame(maxWidth: .infinity, minHeight: 82, alignment: .topLeading)
            .padding(.horizontal, 12)
            .padding(.top, 12)
            .padding(.bottom, 13)
            .background(FAMColor.background)
            .overlay(alignment: .topLeading) {
                if index == 0 && !now.nextUpPaused { timerBar }
            }
            .clipShape(RoundedRectangle(cornerRadius: 14))
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(lead ? FAMColor.copper : FAMColor.border))
        }
        .buttonStyle(.press)
    }

    /// The countdown, drawn rather than described: a 3 pt bar that empties
    /// from the right over 15 s (`#nextUpTimer`).
    private var timerBar: some View {
        GeometryReader { geo in
            Rectangle()
                .fill(FAMColor.copper)
                .frame(width: geo.size.width * Double(now.nextUpLeft) / Double(NowPlayingModel.nextUpSeconds),
                       height: 3)
                .animation(.linear(duration: 1), value: now.nextUpLeft)
        }
        .frame(height: 3)
    }

    private var searchBox: some View {
        HStack(spacing: 6) {
            TextField("", text: $words, prompt: Text("Search for anything").foregroundColor(FAMColor.textFaint))
                .font(FAMFont.body(16))
                .foregroundStyle(FAMColor.text)
                .focused($typing)
                .submitLabel(.search)
                .onSubmit(search)
                .onChange(of: typing) { _, on in if on { now.pauseNextUp() } }
                .onChange(of: words) { _, _ in now.pauseNextUp() }
                .padding(.vertical, 11)
            Button(action: search) {
                WebIcon(path: Icons.search, size: 15, line: 2.2)
                    .foregroundStyle(FAMColor.copper)
                    .frame(width: 32, height: 32)
            }
            .buttonStyle(.press)
            .accessibilityLabel("Search")
        }
        .padding(.leading, 12)
        .padding(.trailing, 6)
        .background(FAMColor.background, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(typing ? FAMColor.copper : FAMColor.border))
    }

    private func search() {
        let text = words.trimmingCharacters(in: .whitespacesAndNewlines)
        now.pauseNextUp()
        guard !text.isEmpty else { typing = true; return }
        now.stopNextUp()
        now.followUp(text, minutes: now.episode?.key.minutes ?? FAMAPI.browseMinutes, typed: true)
    }
}
