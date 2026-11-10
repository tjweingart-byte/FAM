import SwiftUI
import UIKit

// The sheets and overlays the player opens, each ported from the web's
// (`static/index.html`): Go Deeper (`openGoDeeper`), the ⋯ menu
// (`openPlayerMenu`), Sources (`openSources`), Playback
// (`openSpeedLengthModal`), the Queue (`openQueue`), VIBE! (`toggleEcho`),
// Share (`openShareModal`), the captions sheet (`toggleCC`) and What's next
// (`maybeOfferNextUp`). Words are the web's own.

// MARK: - Go Deeper

struct GoDeeperSheet: View {
    @EnvironmentObject private var now: NowPlayingModel
    @Environment(\.dismiss) private var dismiss
    /// An episode a message carries, before it has been played
    /// (`goDeeperOnShared`): the follow-up builds on it, not on the player.
    var base: SharedEpisode? = nil
    /// Where a follow-up of `base` goes (the chat plays it).
    var onGo: ((String, Double, Bool) -> Void)? = nil
    @State private var typed = ""
    @State private var minutes: Double = 1.5
    @State private var baseThread = ""
    @FocusState private var typing: Bool

    private static let lengths: [Double] = [1, 1.5, 2, 3, 4, 5]

    private var thread: String { base == nil ? now.thread : baseThread }
    private var baseTitle: String { base?.title ?? now.title }

    private var suggestion: String {
        if !thread.isEmpty { return thread }
        return baseTitle.isEmpty ? "" : "What's the bigger story behind \(baseTitle)?"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Go deeper")
                .font(FAMFont.heading(16, .medium))
                .foregroundStyle(FAMColor.text)
            Text("Ask a follow-up, or steer where this goes next").monoLabel(9.5)
            if !suggestion.isEmpty {
                Button { go(suggestion, typed: false) } label: {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(thread.isEmpty ? "Follow the thread" : "Pick up the thread").monoLabel(8)
                        Text(suggestion)
                            .font(FAMFont.body(12, .semibold))
                            .foregroundStyle(FAMColor.text)
                            .multilineTextAlignment(.leading)
                            .lineSpacing(2)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 11)
                    .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 12))
                    .overlay(RoundedRectangle(cornerRadius: 12).stroke(FAMColor.copper))
                }
                .buttonStyle(.plain)
            }
            TextField("", text: $typed, prompt: Text("Or type your own follow-up").foregroundColor(FAMColor.textFaint))
                .font(FAMFont.body(13))
                .focused($typing)
                .submitLabel(.go)
                .onSubmit { confirm() }
                .padding(.horizontal, 12)
                .padding(.vertical, 11)
                .background(FAMColor.background, in: RoundedRectangle(cornerRadius: 10))
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(FAMColor.borderStrong))
            Text("Length (minutes)").monoLabel(9.5)
            HStack(spacing: 6) {
                ForEach(Self.lengths, id: \.self) { value in
                    let on = value == minutes
                    Button { minutes = value } label: {
                        Text(FAMAPI.minutes(value))
                            .font(FAMFont.mono(9.5, bold: on))
                            .foregroundStyle(on ? FAMColor.ink : FAMColor.textMuted)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 8)
                            .background(on ? FAMColor.copper : FAMColor.surface, in: Capsule())
                            .overlay(Capsule().stroke(on ? FAMColor.copper : FAMColor.borderStrong))
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("\(FAMAPI.minutes(value)) minutes")
                    .accessibilityAddTraits(on ? .isSelected : [])
                }
            }
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                    .font(FAMFont.body(12, .bold))
                    .foregroundStyle(FAMColor.textMuted)
                    .padding(.horizontal, 16)
                Button("Continue") { confirm() }
                    .font(FAMFont.body(12, .bold))
                    .foregroundStyle(FAMColor.ink)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 9)
                    .background(FAMColor.copper, in: Capsule())
            }
            .padding(.top, 4)
        }
        .padding(20)
        .background(FAMColor.surface)
        .presentationDetents([.medium, .large])
        .presentationBackground(FAMColor.surface)
        .onAppear {
            // The episode's own length, rounded to a half and kept to 1-5.
            let m = ((base?.minutes ?? now.episode?.key.minutes ?? 1.5) * 2).rounded() / 2
            minutes = min(max(m, 1), 5)
        }
        .task {
            // A shared episode's own predicted follow-up, from what is kept.
            guard let base else { return }
            let key = EpisodeKey(query: base.query, minutes: base.minutes)
            if let next = try? await now.api.next(key, cachedOnly: true) { baseThread = next.thread ?? "" }
        }
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

    private func go(_ words: String, typed: Bool) {
        dismiss()
        if base != nil, let onGo {
            let question = words.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !question.isEmpty else { return }
            onGo(question, minutes, typed || question != thread)
            return
        }
        now.followUp(words, minutes: minutes, typed: typed)
    }
}

// MARK: - The ⋯ menu

struct PlayerMenu: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var now: NowPlayingModel
    @EnvironmentObject private var player: StreamPlayer
    @Environment(\.dismiss) private var dismiss
    @Binding var sheet: PlayerSheet?
    @Binding var captionsOpen: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 12) {
                Picture(url: now.picture ?? player.picture)
                    .frame(width: 44, height: 44)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                VStack(alignment: .leading, spacing: 3) {
                    Text(now.title)
                        .font(FAMFont.body(14, .semibold))
                        .foregroundStyle(FAMColor.text)
                        .lineLimit(2)
                    if !now.searcher.isEmpty {
                        Text(now.searcher)
                            .font(FAMFont.body(11.5))
                            .foregroundStyle(FAMColor.textMuted)
                    }
                }
            }
            .padding(.horizontal, 22)
            .padding(.vertical, 16)
            Divider()
            row("Share", "square.and.arrow.up") { open(.share) }
            row(now.vibed ? "Take back your VIBE!" : "VIBE! to your friends", "arrow.2.squarepath") {
                if now.vibed { dismiss(); now.unvibe() } else { open(.vibe) }
            }
            row(now.saved ? "Remove from Save for Later" : "Save for later",
                now.saved ? "bookmark.fill" : "bookmark") { dismiss(); now.toggleSave() }
            row("Closed captions · \(captionsOpen ? "On" : "Off")", "captions.bubble") {
                dismiss(); captionsOpen.toggle()
            }
            // The web's order (`openPlayerMenu`): the playlist rows, then the
            // queue, then Report.
            Button {
                if (now.episode?.key.query ?? "").isEmpty {
                    dismiss()
                    now.say("Play something first")
                } else {
                    open(.addToMix)
                }
            } label: {
                HStack(spacing: 14) {
                    WebIcon(path: Icons.circlePlus, size: 18, line: 1.8)
                        .foregroundStyle(FAMColor.textMuted)
                        .frame(width: 22)
                    AddToPlaylistLabel()
                    Spacer()
                }
                .padding(.horizontal, 22)
                .padding(.vertical, 14)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if model.canRemoveFromPlayingMix {
                row("Remove from this playlist", "minus.circle") { dismiss(); model.removeFromPlayingMix() }
            }
            row("Add to Queue", "text.badge.plus") {
                dismiss()
                if let ep = now.episode { now.addToQueue(ep) } else { now.say("Nothing to queue") }
            }
            row("Go to Queue", "list.bullet") { open(.queue) }
            if now.episode != nil {
                row("Report", "flag", danger: true) { open(.report) }
            }
            Button("Cancel") { dismiss() }
                .font(FAMFont.body(14, .semibold))
                .foregroundStyle(FAMColor.textMuted)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 16)
        }
        .background(FAMColor.surface)
        .presentationDetents([.medium, .large])
        .presentationBackground(FAMColor.surface)
    }

    private func open(_ next: PlayerSheet) {
        // One sheet at a time: close this, then open the next.
        sheet = nil
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { sheet = next }
    }

    private func row(_ label: String, _ icon: String, danger: Bool = false,
                     action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 14) {
                Image(systemName: icon)
                    .font(.system(size: 16))
                    .foregroundStyle(danger ? FAMColor.coral : FAMColor.textMuted)
                    .frame(width: 22)
                Text(label)
                    .font(FAMFont.body(13.5, .semibold))
                    .foregroundStyle(danger ? FAMColor.coral : FAMColor.text)
                Spacer()
            }
            .padding(.horizontal, 22)
            .padding(.vertical, 14)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

/// "Add to myFAM Playlist", with "myFAM" drawn as the wordmark
/// (`addToPlaylistLabel`, `myFamMark`); read aloud as the words.
struct AddToPlaylistLabel: View {
    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 0) {
            Text("Add to ")
            Wordmark(lead: "my", size: 13.5)
            Text(" Playlist")
        }
        .font(FAMFont.body(13.5, .semibold))
        .foregroundStyle(FAMColor.text)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Add to myFAM Playlist")
    }
}

// MARK: - Sources

struct SourcesSheet: View {
    @EnvironmentObject private var now: NowPlayingModel
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("Sources").font(FAMFont.heading(16, .medium)).foregroundStyle(FAMColor.text)
                Spacer()
                Button { dismiss() } label: {
                    Image(systemName: "xmark").foregroundStyle(FAMColor.textMuted)
                }
                .accessibilityLabel("Close")
            }
            .padding(20)
            ScrollView {
                VStack(spacing: 0) {
                    let items = now.sources?.items ?? []
                    if items.isEmpty {
                        Text("No sources for this episode.")
                            .font(FAMFont.body(12))
                            .foregroundStyle(FAMColor.textMuted)
                            .padding(20)
                    }
                    ForEach(items, id: \.self) { item in
                        Button {
                            if let s = item.url, let url = URL(string: s) { openURL(url) }
                        } label: {
                            HStack(spacing: 12) {
                                SourceMark(label: item.label, size: 30)
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(item.title?.isEmpty == false ? item.title! : item.label)
                                        .font(FAMFont.body(12, .semibold))
                                        .foregroundStyle(nameColor(item))
                                        .multilineTextAlignment(.leading)
                                    Text([item.label, item.tier, item.at].compactMap { $0 }.filter { !$0.isEmpty }
                                        .joined(separator: " · "))
                                        .monoLabel(8, color: FAMColor.textFaint)
                                }
                                Spacer()
                                if item.url != nil {
                                    Image(systemName: "arrow.up.right").foregroundStyle(FAMColor.textMuted)
                                }
                            }
                            .padding(.horizontal, 20)
                            .padding(.vertical, 10)
                        }
                        .buttonStyle(.plain)
                        Divider().padding(.leading, 62)
                    }
                    if let r = now.sources?.retrievers, r.count > 1 {
                        Text("Cross-checked across \(r.joined(separator: " and ")).")
                            .font(FAMFont.body(11))
                            .foregroundStyle(FAMColor.textMuted)
                            .padding(20)
                    }
                }
            }
        }
        .background(FAMColor.surface)
        .presentationDetents([.medium, .large])
        .presentationBackground(FAMColor.surface)
    }

    private func nameColor(_ item: SourceItem) -> Color {
        switch item.kind {
        case "live": return FAMColor.sage
        case "attachment": return FAMColor.copper
        default: return FAMColor.text
        }
    }
}

// MARK: - Playback: speed and length

struct PlaybackSheet: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var player: StreamPlayer
    @EnvironmentObject private var now: NowPlayingModel
    @Environment(\.dismiss) private var dismiss

    private static let speeds: [Double] = [0.5, 0.8, 1, 1.2, 1.5, 2]
    private static let lengths: [Double] = [1, 1.5, 2, 3, 4, 5]

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Playback").font(FAMFont.heading(16, .medium)).foregroundStyle(FAMColor.text)
            Text("Speed").monoLabel(9.5)
            Menu {
                ForEach(Self.speeds, id: \.self) { s in
                    Button("\(FAMAPI.minutes(s))x" + (s == player.rate ? "  ✓" : "")) { player.setRate(s) }
                }
                Section("Voice") {
                    Button("Keep the voice's pitch" + (player.keepsPitch ? "  ✓" : "")) {
                        player.setKeepsPitch(!player.keepsPitch)
                        now.say(player.keepsPitch ? "Speed changes keep the voice's pitch"
                                                  : "Speed changes will shift the voice's pitch")
                    }
                }
            } label: { valueBox("\(FAMAPI.minutes(player.rate))x") }
            // Length only for a search: everything else is the browse length.
            if let ep = now.episode, ep.surface == "search" {
                Text("Length").monoLabel(9.5)
                Menu {
                    ForEach(Self.lengths, id: \.self) { m in
                        Button("\(FAMAPI.minutes(m)) min" + (m == ep.key.minutes ? "  ✓" : "")) {
                            guard m != ep.key.minutes else { return }
                            dismiss()
                            now.say("Rebuilding at \(FAMAPI.minutes(m)) min")
                            var again = ep
                            again.key.minutes = m
                            model.play(again)
                        }
                    }
                } label: { valueBox("\(FAMAPI.minutes(ep.key.minutes)) min") }
            }
            HStack {
                Spacer()
                Button("Done") { dismiss() }
                    .font(FAMFont.body(12, .bold))
                    .foregroundStyle(FAMColor.ink)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 9)
                    .background(FAMColor.copper, in: Capsule())
            }
        }
        .padding(20)
        .background(FAMColor.surface)
        .presentationDetents([.medium])
        .presentationBackground(FAMColor.surface)
    }

    private func valueBox(_ text: String) -> some View {
        HStack {
            Text(text).font(FAMFont.body(13, .semibold)).foregroundStyle(FAMColor.text)
            Spacer()
            Image(systemName: "chevron.up.chevron.down").foregroundStyle(FAMColor.textMuted)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(FAMColor.background, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(FAMColor.borderStrong))
    }
}

// MARK: - The queue

struct QueueSheet: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var now: NowPlayingModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Now playing").monoLabel(9)
            Text(now.episode == nil ? "Nothing is playing." : now.title)
                .font(FAMFont.body(13, .semibold))
                .foregroundStyle(now.episode == nil ? FAMColor.textMuted : FAMColor.copper)
            if let list = model.mixPlay, !model.mixUpNext.isEmpty {
                Text("Next from \(list.name)").monoLabel(9).padding(.top, 10)
                ForEach(model.mixUpNext) { item in
                    Text(item.title)
                        .font(FAMFont.body(13, .semibold))
                        .foregroundStyle(FAMColor.text)
                }
            }
            Text("Next in queue").monoLabel(9).padding(.top, 10)
            if now.queue.isEmpty {
                Text("Your queue is empty. Add episodes from the three dots on the player or on a DailyFAM tile.")
                    .font(FAMFont.body(12))
                    .foregroundStyle(FAMColor.textMuted)
            }
            ForEach(now.queue) { entry in
                HStack {
                    Button {
                        dismiss()
                        model.stopEpisode()
                        now.playFromQueue(entry)
                    } label: {
                        Text(entry.episode.title)
                            .font(FAMFont.body(13))
                            .foregroundStyle(FAMColor.text)
                            .lineLimit(1)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .buttonStyle(.plain)
                    Button { withAnimation { now.removeFromQueue(entry) } } label: {
                        Image(systemName: "xmark")
                            .foregroundStyle(FAMColor.textMuted)
                            .frame(width: 32, height: 32)
                    }
                    .accessibilityLabel("Remove from the queue")
                }
            }
            HStack {
                Spacer()
                Button("Close") { dismiss() }
                    .font(FAMFont.body(12, .bold))
                    .foregroundStyle(FAMColor.textMuted)
            }
            .padding(.top, 8)
        }
        .padding(22)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(FAMColor.surface)
        .presentationDetents([.medium, .large])
        .presentationBackground(FAMColor.surface)
    }
}

// MARK: - VIBE!

/// Posts the episode to your story with a caption. The web's full story
/// editor (layouts, fonts, stickers, @tags) is not ported yet - see PARITY.md.
struct VibeComposer: View {
    @EnvironmentObject private var now: NowPlayingModel
    @EnvironmentObject private var player: StreamPlayer
    @Environment(\.dismiss) private var dismiss
    @State private var caption = ""
    @State private var posting = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("VIBE! to your story").font(FAMFont.heading(16, .medium)).foregroundStyle(FAMColor.text)
            ZStack(alignment: .bottomLeading) {
                PlayerPicture(url: now.picture ?? player.picture)
                    .aspectRatio(4 / 3, contentMode: .fit)
                    .clipShape(RoundedRectangle(cornerRadius: 14))
                Text(now.title)
                    .font(FAMFont.heading(17, .semibold))
                    .foregroundStyle(FAMColor.text)
                    .padding(14)
            }
            TextField("", text: $caption, prompt: Text("Say something about it").foregroundColor(FAMColor.textFaint),
                      axis: .vertical)
                .font(FAMFont.body(14))
                .lineLimit(1...4)
                .onChange(of: caption) { _, text in
                    if text.count > 150 { caption = String(text.prefix(150)) }
                }
                .padding(12)
                .background(FAMColor.background, in: RoundedRectangle(cornerRadius: 10))
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(FAMColor.borderStrong))
            Button {
                posting = true
                Task {
                    let ok = await now.vibe(caption: caption)
                    posting = false
                    if ok { dismiss() }
                }
            } label: {
                if posting { ProgressView().tint(FAMColor.ink) } else { Text("VIBE!") }
            }
            .buttonStyle(CopperButtonStyle())
            .disabled(posting)
        }
        .padding(20)
        .background(FAMColor.surface)
        .presentationDetents([.large])
        .presentationBackground(FAMColor.surface)
    }
}

// MARK: - Share

/// "Share this FAM" (`openShareModal`, shots 45-48): send it inside FAM to
/// a group or a person - with the id of the episode heard, so they hear
/// that one (§288) - or ANYWHERE ELSE through the server's destinations
/// (`POST /api/share`), with iOS's share sheet for the rest.
struct ShareSheet: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var now: NowPlayingModel
    @Environment(\.dismiss) private var dismiss
    @State private var link: SharedLink?
    @State private var problem = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Share this FAM")
                .font(FAMFont.heading(16, .medium))
                .foregroundStyle(FAMColor.text)
                .padding(.horizontal, 22)
                .padding(.top, 20)
                .padding(.bottom, 14)
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    SendInsideFAM(send: { to, label in await sendInside(to: to, label: label) },
                                  sent: { dismiss() })
                    Text("Anywhere else").monoLabel(9.5, tracking: 1.14)
                        .padding(.top, 20)
                        .padding(.bottom, 4)
                    if let link {
                        ShareTargetRows(url: link.url, targets: link.targets ?? [:],
                                        isPublic: link.public ?? true) { dismiss() }
                    } else {
                        Text(problem.isEmpty ? "Making a link\u{2026}" : problem)
                            .font(FAMFont.body(12))
                            .foregroundStyle(FAMColor.textMuted)
                            .padding(.vertical, 10)
                    }
                }
                .padding(.horizontal, 22)
            }
            SheetCancelButton { dismiss() }
        }
        .padding(.bottom, 12)
        .background(FAMColor.surface)
        .presentationDetents([.large])
        .presentationBackground(FAMColor.surface)
        .environmentObject(model.social)
        .task { await makeLink() }
    }

    private func makeLink() async {
        guard let ep = now.episode else {
            problem = "Play something first"
            return
        }
        do {
            link = try await model.api.share(ep.key, title: now.title)
        } catch {
            problem = "Could not make a link"
        }
    }

    /// The episode playing, by its question and length, and the id of the
    /// one heard (`X-FAM-Episode`).
    private func sendInside(to: String, label: String) async -> Bool {
        guard let ep = now.episode else {
            model.nowPlaying.say("Play something first")
            return false
        }
        return await model.social.sendShare(to: to, label: label, query: ep.key.query,
                                            minutes: ep.key.minutes, title: now.title,
                                            episode: model.player.episodeID)
    }
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

// MARK: - Captions

/// The captions sheet (`#ccPanel`): a 46 pt strip until opened. Open, it
/// shows the whole script so far, scrollable, with the sentence being said
/// highlighted and kept in view as the episode plays (the owner's call for
/// iOS - the web shows the current sentence alone). Scrolling by hand stops
/// the following until the listener taps "Follow along". Copy takes it all.
struct CaptionsPanel: View {
    @EnvironmentObject private var player: StreamPlayer
    @EnvironmentObject private var now: NowPlayingModel
    @Binding var open: Bool
    @State private var following = true

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Text("Closed captions")
                    .monoLabel(8.5, color: open ? Color(hex: 0xE0B563) : FAMColor.textMuted)
                Spacer()
                Button(action: copy) {
                    Image(systemName: "doc.on.doc")
                        .font(.system(size: 10))
                        .foregroundStyle(open ? FAMColor.paper : FAMColor.textMuted)
                        .frame(width: 20, height: 20)
                        .overlay(RoundedRectangle(cornerRadius: 6)
                            .stroke(open ? Color(hex: 0xF4EFE4).opacity(0.22) : FAMColor.borderStrong))
                }
                .accessibilityLabel("Copy the transcript")
            }
            .frame(height: 46)
            .overlay(alignment: .top) {
                Capsule().fill(FAMColor.borderStrong).frame(width: 36, height: 4).padding(.top, 6)
            }
            .contentShape(Rectangle())
            .onTapGesture { withAnimation(.easeInOut(duration: 0.26)) { open.toggle() } }
            // Swipe the strip up to open, down to close; the script below
            // scrolls on its own.
            .gesture(DragGesture(minimumDistance: 20).onEnded { value in
                if value.translation.height < -30 { withAnimation { open = true } }
                if value.translation.height > 30 { withAnimation { open = false } }
            })
            if open {
                script
            }
        }
        .padding(.horizontal, 18)
        .padding(.bottom, open ? 18 : 0)
        .background(open ? FAMColor.ink : FAMColor.surface,
                    in: UnevenRoundedRectangle(topLeadingRadius: 16, topTrailingRadius: 16))
        .overlay(UnevenRoundedRectangle(topLeadingRadius: 16, topTrailingRadius: 16)
            .stroke(open ? FAMColor.ink : FAMColor.border))
        .ignoresSafeArea(edges: .bottom)
    }

    /// Every sentence so far; the current one bright and copper-edged, the
    /// rest dimmed; tap a sentence to jump there.
    @ViewBuilder private var script: some View {
        if now.sentences.isEmpty {
            Text(now.captionsDone
                 ? "No transcript for this one — an episode you attached a file to is never stored, so there is nothing to read back."
                 : "Catching up with the script…")
                .font(FAMFont.body(12).italic())
                .foregroundStyle(FAMColor.textFaint)
                .frame(maxWidth: .infinity, minHeight: 60, alignment: .topLeading)
        } else {
            let current = now.captionIndex(at: player.position, total: player.total) ?? 0
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 10) {
                        ForEach(Array(now.sentences.enumerated()), id: \.offset) { i, sentence in
                            Text(sentence)
                                .font(FAMFont.body(15, i == current ? .semibold : .regular))
                                .foregroundStyle(i == current ? FAMColor.paper : FAMColor.paper.opacity(i < current ? 0.45 : 0.6))
                                .lineSpacing(4)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.leading, 10)
                                .overlay(alignment: .leading) {
                                    if i == current {
                                        Capsule().fill(Color(hex: 0xE0B563)).frame(width: 3)
                                    }
                                }
                                .id(i)
                                .contentShape(Rectangle())
                                .onTapGesture { jump(to: i) }
                                .accessibilityAddTraits(i == current ? .isSelected : [])
                        }
                    }
                    .padding(.vertical, 6)
                    .animation(.easeOut(duration: 0.25), value: current)
                }
                .frame(maxHeight: UIScreen.main.bounds.height * 0.5)
                .simultaneousGesture(DragGesture(minimumDistance: 8).onChanged { _ in following = false })
                .onChange(of: current) { _, i in
                    guard following else { return }
                    withAnimation(.easeOut(duration: 0.35)) { proxy.scrollTo(i, anchor: .center) }
                }
                .onAppear { proxy.scrollTo(current, anchor: .center) }
                .overlay(alignment: .bottomTrailing) {
                    if !following {
                        Button {
                            following = true
                            withAnimation { proxy.scrollTo(current, anchor: .center) }
                        } label: {
                            Text("Follow along")
                                .monoLabel(8.5, color: FAMColor.ink, bold: true)
                                .padding(.horizontal, 10)
                                .padding(.vertical, 6)
                                .background(Color(hex: 0xE0B563), in: Capsule())
                        }
                        .padding(6)
                    }
                }
            }
        }
    }

    /// A sentence tapped: play from where it starts, when that is known
    /// and has arrived.
    private func jump(to index: Int) {
        guard let starts = now.captionStarts, starts.indices.contains(index) else { return }
        player.seek(to: starts[index])
        following = true
    }

    private func copy() {
        guard !now.sentences.isEmpty else { now.say("No transcript for this one yet"); return }
        UIPasteboard.general.string = now.sentences.joined(separator: " ")
        now.say("Transcript copied ✓")
    }
}

// MARK: - What's next

/// After an episode (`post-episode-grid`): four tiles, the first - Go
/// Deeper's own suggestion - starts in 15 s; touching the search box pauses
/// it for good.
struct NextUpOverlay: View {
    @EnvironmentObject private var now: NowPlayingModel
    @EnvironmentObject private var model: AppModel
    let onBack: () -> Void
    @State private var words = ""
    @FocusState private var typing: Bool

    var body: some View {
        ZStack {
            Color(red: 10 / 255, green: 9 / 255, blue: 15 / 255).opacity(0.62).ignoresSafeArea()
            VStack(alignment: .leading, spacing: 12) {
                Text("What's next").font(FAMFont.heading(16, .semibold)).foregroundStyle(FAMColor.text)
                Text(now.nextUpPaused ? "Paused - pick one or search"
                     : now.nextUpLeft > 0 ? "First one starts in \(now.nextUpLeft)" : "Starting…")
                    .monoLabel(9, color: FAMColor.textFaint)
                LazyVGrid(columns: [GridItem(.flexible(), spacing: 9), GridItem(.flexible())], spacing: 9) {
                    ForEach(Array(now.nextUp.enumerated()), id: \.element.id) { index, tile in
                        Button { now.playNextUp(index) } label: {
                            VStack(alignment: .leading, spacing: 6) {
                                Text(tile.title)
                                    .font(FAMFont.heading(12.5, .semibold))
                                    .foregroundStyle(FAMColor.text)
                                    .lineLimit(3)
                                    .multilineTextAlignment(.leading)
                                Spacer(minLength: 0)
                                Text(tile.sub)
                                    .monoLabel(8, color: index == 0 && tile.deeper ? FAMColor.copper : FAMColor.textFaint)
                                    .lineLimit(2)
                            }
                            .frame(maxWidth: .infinity, minHeight: 82, alignment: .topLeading)
                            .padding(12)
                            .background(FAMColor.background, in: RoundedRectangle(cornerRadius: 14))
                            .overlay(RoundedRectangle(cornerRadius: 14)
                                .stroke(index == 0 && tile.deeper ? FAMColor.copper : FAMColor.border))
                            .overlay(alignment: .top) {
                                if index == 0 && !now.nextUpPaused {
                                    GeometryReader { geo in
                                        Capsule().fill(FAMColor.copper)
                                            .frame(width: geo.size.width * Double(now.nextUpLeft) / Double(NowPlayingModel.nextUpSeconds),
                                                   height: 3)
                                            .animation(.linear(duration: 1), value: now.nextUpLeft)
                                    }
                                    .frame(height: 3)
                                    .padding(.horizontal, 6)
                                }
                            }
                        }
                        .buttonStyle(.plain)
                    }
                }
                HStack(spacing: 8) {
                    TextField("", text: $words, prompt: Text("Search for anything").foregroundColor(FAMColor.textFaint))
                        .font(FAMFont.body(16))
                        .focused($typing)
                        .submitLabel(.search)
                        .onSubmit(search)
                        .onChange(of: typing) { _, on in if on { now.pauseNextUp() } }
                        .padding(.horizontal, 12)
                        .padding(.vertical, 10)
                        .background(FAMColor.background, in: RoundedRectangle(cornerRadius: 12))
                        .overlay(RoundedRectangle(cornerRadius: 12).stroke(typing ? FAMColor.copper : FAMColor.borderStrong))
                    Button(action: search) {
                        WebIcon(path: Icons.search, size: 18)
                            .foregroundStyle(FAMColor.ink)
                            .frame(width: 40, height: 40)
                            .background(FAMColor.copper, in: Circle())
                    }
                    .accessibilityLabel("Search")
                }
                Button(now.episode?.surface == "search" ? "Back to search" : "Back to DailyFAM") {
                    now.stopNextUp()
                    onBack()
                }
                .font(FAMFont.body(13, .semibold))
                .foregroundStyle(FAMColor.textMuted)
                .frame(maxWidth: .infinity)
                .padding(.top, 4)
            }
            .padding(18)
            .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 20))
            .overlay(RoundedRectangle(cornerRadius: 20).stroke(FAMColor.borderStrong))
            .padding(.horizontal, 26)
        }
        .transition(.opacity.combined(with: .scale(scale: 0.985)))
    }

    private func search() {
        let text = words.trimmingCharacters(in: .whitespacesAndNewlines)
        now.pauseNextUp()
        guard !text.isEmpty else { typing = true; return }
        now.stopNextUp()
        now.followUp(text, minutes: now.episode?.key.minutes ?? FAMAPI.browseMinutes, typed: true)
    }
}
