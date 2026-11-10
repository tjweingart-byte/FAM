import Combine
import Foundation
import SwiftUI
import UIKit

/// One episode as the app asked for it: what it is, where it came from,
/// and what the player should say about it. Built at the tap, before audio.
struct Episode: Equatable {
    var key: EpisodeKey
    var title: String
    var surface: String
    var topicID: String = ""
    /// Typed by the listener (a Go Deeper they wrote): held to their AI answer.
    var own: Bool = false
    var picture: URL?
    /// A tile's title stands; only a typed question's title is replaced by
    /// the writer's (`titleOverridden` on the web).
    var titleOverridden: Bool = true
    /// A replay only, never written anew: a category's episodes, queued
    /// after the one tapped (`cachedOnly` on the web's queue entry).
    var cachedOnly: Bool = false
    /// A kept episode's id (`X-FAM-Episode`): a message's Listen replays the
    /// sender's exact episode (§288); "" asks the question as usual.
    var episodeID: String = ""
    /// A myFAM playlist's own episode: the playlist's key while it plays it
    /// (`fromMix`, `mixPlay.key` on the web).
    var mixKey: String = ""
    /// A Go Deeper off a playlist's episode, or off a follow-up already on
    /// that detour (§519, `mixDetour`): the playlist waits behind it and
    /// carries on from its next episode when this ends, is skipped or fails.
    var mixDetour: String = ""
    /// One of a category's episodes, played or queued from its list
    /// (`fromCategory`): the rest of that list stays queued behind it.
    var fromCategory: Bool = false
    /// Played from the Queue, which keeps a category's list as it is.
    var queued: Bool = false
}

/// A queued episode (§190): client state only, never sent to the server.
struct QueueEntry: Identifiable, Equatable {
    let id = UUID()
    var episode: Episode
}

/// One tile of the What's next grid.
struct NextUpTile: Identifiable {
    let id = UUID()
    var title: String
    var sub: String
    /// The Go Deeper suggestion, played as a follow-up of this episode.
    var deeper: Bool
    var episode: Episode?
}

/// Everything about the episode playing that is not the audio itself: the
/// thread and the writer's title (`/api/next`), its picture
/// (`/api/episode/card`), its sources, whether it is saved or vibed, the
/// captions, the queue, the What's next grid, and what the taste model and
/// the listening history are told. Ported from the web player
/// (`static/index.html`: `populatePlayer`, `speakText`, `fetchEpisodeThread`,
/// `fetchEpisodeSources`, `askPlayerCard`, `refreshSavedState`, `toggleCC`,
/// `recordFamEvent`, `noteResume`, `noteListen`, `maybeOfferNextUp`).
@MainActor
final class NowPlayingModel: ObservableObject {
    static let finishedShare = 0.85          // FINISHED_SHARE
    static let resumeEvery: TimeInterval = 15 // RESUME_EVERY_MS
    static let skipAfter: Double = 5          // a skip counts after 5 s
    static let nextUpSeconds = 15
    /// `localStorage["fam.captions"]` on the web: "1" on, "0" off.
    static let captionsKey = "fam.captions"

    @Published private(set) var episode: Episode?
    @Published private(set) var title = ""
    @Published private(set) var explicit = false
    @Published private(set) var thread = ""
    @Published private(set) var picture: URL?
    @Published private(set) var searcher = ""
    @Published private(set) var sources: Sources?
    @Published private(set) var saved = false
    @Published private(set) var vibed = false
    /// The save and VIBE! bursts (`Motion.celebrate`): counted up only when
    /// the listener turns one on, never when painted from the server.
    @Published private(set) var savedBurst = 0
    @Published private(set) var vibedBurst = 0
    /// The captions card (§519): off for each new episode unless this device
    /// remembers it on (`fam.captions`, `captionsWanted`).
    @Published private(set) var captionsOn = false
    @Published private(set) var sentences: [String] = []
    @Published private(set) var captionStarts: [Double]?
    @Published private(set) var captionsDone = false
    @Published private(set) var queue: [QueueEntry] = []
    @Published private(set) var nextUp: [NextUpTile] = []
    @Published private(set) var nextUpLeft = NowPlayingModel.nextUpSeconds
    @Published private(set) var nextUpPaused = false
    @Published private(set) var toast: Toast?
    /// "Added to Queue · Open": whoever is on screen opens the Queue sheet.
    @Published var showQueue = false
    /// Go Deeper is open (`goDeeperIsOpen`): an episode ending under it
    /// starts nothing - no queue, no playlist, no What's next (10.1).
    var goDeeperOpen = false

    struct Toast: Identifiable, Equatable {
        let id = UUID()
        let text: String
        var action: String?
    }

    let api: FAMAPI
    private let player: StreamPlayer
    /// Who to ask to play an episode (the app model, which owns the loading screen).
    var playEpisode: ((Episode) -> Void)?
    var signedIn: () -> Bool = { false }
    /// Whether the full player is on screen: What's next is offered there
    /// only, never under the mini bar (`post-episode-grid`).
    var playerShowing: () -> Bool = { true }

    private var run = 0
    private var reported = false
    private var lastResume = Date.distantPast
    private var titleFinal = false
    private var sourcesShown = false
    private var cancellables = Set<AnyCancellable>()
    private var toastAction: (() -> Void)?

    init(api: FAMAPI, player: StreamPlayer) {
        self.api = api
        self.player = player
        // Pausing sends the position at once, as the web does.
        player.$phase
            .removeDuplicates()
            .sink { [weak self] phase in
                guard let self else { return }
                MainActor.assumeIsolated {
                    if phase == .paused { self.sendResume() }
                }
            }
            .store(in: &cancellables)
        // Leaving the app sends the position too (`pagehide` on the web).
        NotificationCenter.default.publisher(for: UIApplication.didEnterBackgroundNotification)
            .sink { [weak self] _ in
                MainActor.assumeIsolated { self?.sendResume() }
            }
            .store(in: &cancellables)
    }

    // MARK: - An episode's life

    /// Called at the tap, before any audio: an episode that was playing and
    /// is being left counts as skipped if it got far enough - unless a Go
    /// Deeper only interrupts it, to resume it afterwards (`reportSkip`
    /// false: a playlist's episode the owner's detour comes back to).
    func leaving(reportSkip: Bool = true) {
        if reportSkip { reportSkipIfAbandoned() } else { sendResume() }
        stopNextUp()
    }

    // MARK: - The title before the first word (`titleWatch`)

    /// A provisional title (`/api/next`'s `title`) that landed while the
    /// episode was still being made, so the player opens on it rather than
    /// on the question.
    private var earlyTitle: (key: EpisodeKey, title: String)?
    private var titleWatchTask: Task<Void, Never>?

    /// Every 2 s until the first audio, for an episode whose title the
    /// writer's may replace (`titleWatch`); a replay asks nothing.
    func watchTitle(_ ep: Episode) {
        titleWatchTask?.cancel()
        titleWatchTask = nil
        earlyTitle = nil
        guard !ep.titleOverridden, ep.episodeID.isEmpty else { return }
        let api = self.api
        titleWatchTask = Task { [weak self] in
            for _ in 0..<45 {
                try? await Task.sleep(nanoseconds: 2_000_000_000)
                if Task.isCancelled { return }
                let answer = try? await api.next(ep.key)
                if Task.isCancelled { return }
                if let t = answer?.title, !t.isEmpty {
                    self?.earlyTitle = (key: ep.key, title: t)
                    return
                }
            }
        }
    }

    /// The first audio is playing: everything the player shows starts here.
    func begin(_ ep: Episode) {
        run += 1
        let mine = run
        episode = ep
        title = ep.title
        titleWatchTask?.cancel()
        titleWatchTask = nil
        if !ep.titleOverridden, let early = earlyTitle, early.key == ep.key { title = early.title }
        earlyTitle = nil
        explicit = false
        thread = ""
        picture = ep.picture
        searcher = ""
        sources = nil
        sourcesShown = false
        saved = false
        vibed = false
        stopCaptions()
        captionsOn = false
        sentences = []
        captionStarts = nil
        captionsDone = false
        titleFinal = false
        reported = false
        lastResume = Date()
        stopNextUp()

        if signedIn() {
            let heard = title
            Task { await api.noteListen(ep.key, surface: ep.surface, title: heard,
                                        episode: player.episodeID) }
            Task { if let s = try? await api.isSaved(ep.key), mine == run { saved = s } }
        }
        Task { await askCard(mine) }
        Task { await askThread(mine) }
        Task { await askSources(mine) }
        Task { await watch(mine) }
        // Turned on again if that is how the listener left them (§519).
        if Self.captionsRemembered { showCaptions(true) }
    }

    /// The episode played to its end.
    func ended() {
        guard let ep = episode else { return }
        let whole = player.total
        if signedIn() {
            Task { await api.noteResume(ep.key, seconds: whole, title: title,
                                        duration: whole, episode: player.episodeID) }
        }
        reportComplete()
        let mine = run
        Task {
            await refreshThreadOnce(mine)
            await refreshSourcesOnce(mine)
        }
        // Ended under an open Go Deeper: the sound stops and the sheet
        // stays up on its own - nothing else starts over it (10.1).
        if goDeeperOpen { return }
        // A playlist's episode, or a Go Deeper detour off it, plays the
        // playlist's next instead of offering What's next (`fromMix`, §519).
        if onEnded?(ep) == true { return }
        if let next = queue.first {
            queue.removeFirst()
            playQueued(next)
            return
        }
        // What's next is the full player's, never the mini bar's.
        guard playerShowing() else { return }
        Task { await offerNextUp(mine) }
    }

    /// The mini player's X, or a new episode replacing this one.
    func stopped() {
        reportSkipIfAbandoned()
        stopCaptions()
        captionsOn = false
        stopNextUp()
        run += 1
        episode = nil
    }

    // MARK: - Listening, as the taste model and history hear it

    /// The 1 s watch: 85% heard is finished; progress every 15 s.
    private func watch(_ mine: Int) async {
        while mine == run {
            try? await Task.sleep(nanoseconds: 1_000_000_000)
            guard mine == run, let ep = episode else { return }
            if !reported && player.total > 0 && player.position / player.total >= Self.finishedShare {
                reportComplete()
            }
            if player.playing && Date().timeIntervalSince(lastResume) >= Self.resumeEvery {
                sendResume()
            }
            _ = ep
        }
    }

    private func reportComplete() {
        guard !reported, let ep = episode else { return }
        reported = true
        Task { await api.event("complete", topicID: ep.topicID, text: ep.key.query,
                               thread: thread, minutes: ep.key.minutes, episode: player.episodeID) }
    }

    private func reportSkipIfAbandoned() {
        // Abandoned part way: past 5 s, before the whole of it arrived.
        guard let ep = episode, !reported, player.position > Self.skipAfter,
              !player.complete else { return }
        reported = true
        Task { await api.event("skip", topicID: ep.topicID, text: ep.key.query,
                               minutes: ep.key.minutes, episode: player.episodeID) }
    }

    private func sendResume() {
        guard let ep = episode, signedIn(), player.position > 0 else { return }
        lastResume = Date()
        let duration = player.complete ? player.arrived : 0
        Task { await api.noteResume(ep.key, seconds: player.position, title: title,
                                    duration: duration, episode: player.episodeID) }
    }

    // MARK: - The thread and the writer's title (`fetchEpisodeThread`)

    private func askThread(_ mine: Int) async {
        // At the first word, +6 s and +20 s (`onFirstAudio`); again at the
        // end and when Go Deeper opens.
        await refreshThreadOnce(mine)
        try? await Task.sleep(nanoseconds: 6_000_000_000)
        guard mine == run else { return }
        await refreshThreadOnce(mine)
        try? await Task.sleep(nanoseconds: 14_000_000_000)
        guard mine == run else { return }
        await refreshThreadOnce(mine)
    }

    private func refreshThreadOnce(_ mine: Int) async {
        guard let ep = episode, let answer = try? await api.next(ep.key), mine == run else { return }
        if thread.isEmpty, let t = answer.thread, !t.isEmpty { thread = t }
        if answer.explicit == true { explicit = true }
        if let t = answer.title, !t.isEmpty, (answer.titleFinal == true || !titleFinal) {
            let becameFinal = answer.titleFinal == true && !titleFinal
            titleFinal = answer.titleFinal == true
            if !ep.titleOverridden && t != title {
                // The wipe is drawn by the title itself (`WipeText`).
                title = t
                if signedIn() {
                    Task { await api.noteListen(ep.key, surface: ep.surface, title: t,
                                                episode: player.episodeID, retitle: true) }
                }
            }
            Task { await askCard(mine) }
            if becameFinal { await refreshSourcesOnce(mine) }
        }
    }

    // MARK: - The picture (`askPlayerCard`)

    private func askCard(_ mine: Int) async {
        guard let ep = episode,
              let card = try? await api.card(ep.key, title: title, episode: player.episodeID),
              mine == run else { return }
        if let s = card.searcher { searcher = s }
        guard let path = card.thumb, !path.isEmpty, let url = api.picture(path) else { return }
        // A picture from the tile stands; otherwise the episode's own
        // (placed in its category) replaces a borrowed one.
        if ep.picture == nil && (picture == nil || card.placed == true) { picture = url }
    }

    // MARK: - Sources (`fetchEpisodeSources`)

    private func askSources(_ mine: Int) async {
        // +6 s, +20 s, then every 8 s while playing until shown (75 tries).
        try? await Task.sleep(nanoseconds: 6_000_000_000)
        await refreshSourcesOnce(mine)
        try? await Task.sleep(nanoseconds: 14_000_000_000)
        await refreshSourcesOnce(mine)
        var tries = 0
        while mine == run && !sourcesShown && tries < 75 {
            try? await Task.sleep(nanoseconds: 8_000_000_000)
            if player.playing { await refreshSourcesOnce(mine) }
            tries += 1
        }
    }

    private func refreshSourcesOnce(_ mine: Int) async {
        guard let ep = episode, let found = try? await api.sources(ep.key), mine == run else { return }
        // An empty answer never wipes sources already showing.
        if found.items.isEmpty && (sources?.items.isEmpty == false) { return }
        if !found.items.isEmpty || found.known { sources = found }
        if !found.items.isEmpty { sourcesShown = true }
    }

    /// One more look, when Go Deeper opens.
    func lookAgain() {
        let mine = run
        Task {
            await refreshThreadOnce(mine)
            await refreshSourcesOnce(mine)
        }
    }

    // MARK: - Save and VIBE!

    func toggleSave() {
        guard let ep = episode else { say("Play something first"); return }
        guard signedIn() else { say("Could not save that"); return }
        if saved {
            saved = false
            Task {
                do {
                    try await api.unsave(ep.key)
                    // Inside a Task that already holds the model; the undo
                    // only re-saves if the same episode is still playing.
                    say("Removed from Saved for Later", action: "Undo") {
                        guard self.episode == ep else { return }
                        self.toggleSave()
                    }
                } catch {
                    saved = true
                    say("Could not take that off")
                }
            }
        } else {
            saved = true
            savedBurst += 1
            Task {
                do {
                    try await api.save(ep.key, title: title)
                    say("Saved for Later")
                } catch {
                    saved = false
                    say("Could not save that")
                }
            }
        }
    }

    /// VIBE! it to your story, with a caption; or take it back.
    func vibe(caption: String) async -> Bool {
        guard let ep = episode else { say("Play something first"); return false }
        do {
            try await api.vibe(ep.key, title: title, thread: thread, caption: caption)
            if episode == ep { vibed = true }
            say("Vibed to your story")
            // VIBED bursts 300 ms after the composer closes (`toggleEcho`).
            Task {
                try? await Task.sleep(nanoseconds: 300_000_000)
                if self.episode == ep { self.vibedBurst += 1 }
            }
            return true
        } catch {
            say((error as? FAMError)?.errorDescription ?? "Could not vibe that")
            return false
        }
    }

    func unvibe() {
        guard let ep = episode else { say("Play something first"); return }
        Task {
            do {
                try await api.unvibe(ep.key)
                vibed = false
                say("Vibe taken back")
            } catch {
                say("Could not take that back")
            }
        }
    }

    /// The share link and its words (`POST /api/share`).
    func shareItems() async -> [Any]? {
        guard let ep = episode else { say("Play something first"); return nil }
        guard let link = try? await api.share(ep.key, title: title) else {
            say("Could not make a link")
            return nil
        }
        let words = link.targets?["copy"]?.text
            ?? "\(title) - a \(FAMAPI.minutes(ep.key.minutes))-minute FAM episode: \(link.url)"
        return [words]
    }

    // MARK: - Captions (`toggleCC`, `fetchCaptions`, `captionIndexNow`)

    private var captionTask: Task<Void, Never>?

    static var captionsRemembered: Bool {
        UserDefaults.standard.string(forKey: captionsKey) == "1"
    }

    /// The listener's toggle - the Captions pill, the card's X, a swipe
    /// down, the menu row: remembered on this device every time (`toggleCC`).
    func setCaptions(_ on: Bool) {
        UserDefaults.standard.set(on ? "1" : "0", forKey: Self.captionsKey)
        showCaptions(on)
    }

    private func showCaptions(_ on: Bool) {
        guard on != captionsOn else { return }
        captionsOn = on
        if on { startCaptions() } else { stopCaptions() }
    }

    private func startCaptions() {
        captionTask?.cancel()
        let mine = run
        captionTask = Task {
            while !Task.isCancelled && mine == run && captionsOn {
                if !captionsDone, let ep = episode, let t = try? await api.transcript(ep.key), mine == run {
                    sentences = t.sentences
                    captionStarts = (t.starts?.count == t.sentences.count) ? t.starts : nil
                    captionsDone = t.done
                }
                try? await Task.sleep(nanoseconds: 1_200_000_000)
            }
        }
    }

    private func stopCaptions() {
        captionTask?.cancel()
        captionTask = nil
    }

    /// The sentence being said now: by its measured start when the server
    /// has timings, else by its share of the characters.
    func captionIndex(at position: Double, total: Double) -> Int? {
        guard !sentences.isEmpty else { return nil }
        if let starts = captionStarts {
            return starts.lastIndex(where: { $0 <= position + 0.12 }) ?? 0
        }
        let lengths = sentences.map { Double(max($0.count, 1)) }
        let all = lengths.reduce(0, +)
        let target = min(max(position / max(total, 1), 0), 1) * all
        var sum = 0.0
        for (i, n) in lengths.enumerated() {
            sum += n
            if target < sum { return i }
        }
        return sentences.count - 1
    }

    // MARK: - The queue (§190)

    /// Asked when an episode ends, before the queue: true if a mix's
    /// playlist took over.
    var onEnded: ((Episode) -> Bool)?
    /// The playlist a follow-up of this episode is a detour of (§519,
    /// `mixDetourFor`), "" when none.
    var detourFor: ((Episode) -> String)?

    func addToQueue(_ ep: Episode) {
        queue.append(QueueEntry(episode: ep))
        say("Added to Queue", action: "Open") { [weak self] in self?.showQueue = true }
    }

    /// Something else started (§500 review): the rest of a category's list
    /// is not its "next". What the listener queued themselves stays.
    func dropCategoryQueue() {
        guard !categoryQueued.isEmpty else { return }
        queue.removeAll { categoryQueued.contains($0.id) }
        categoryQueued = []
    }

    private func playQueued(_ entry: QueueEntry) {
        var ep = entry.episode
        ep.queued = true
        playEpisode?(ep)
    }

    /// What a category tap queued: replaced by the next category tap,
    /// leaving what the listener queued alone (`playCategoryList`).
    private var categoryQueued: Set<UUID> = []

    func replaceCategoryQueue(_ episodes: [Episode]) {
        queue.removeAll { categoryQueued.contains($0.id) }
        let fresh = episodes.map { QueueEntry(episode: $0) }
        categoryQueued = Set(fresh.map(\.id))
        queue.append(contentsOf: fresh)
    }

    func removeFromQueue(_ entry: QueueEntry) {
        guard let i = queue.firstIndex(of: entry) else { return }
        queue.remove(at: i)
        say("Removed from Queue", action: "Undo") { [weak self] in
            self?.queue.insert(entry, at: min(i, self?.queue.count ?? 0))
        }
    }

    func playFromQueue(_ entry: QueueEntry) {
        guard let i = queue.firstIndex(of: entry) else { return }
        queue.remove(at: i)
        playQueued(entry)
    }

    func playQueueNext() {
        guard let next = queue.first else { return }
        queue.removeFirst()
        playQueued(next)
    }

    // MARK: - What's next (`maybeOfferNextUp`)

    private var nextUpTask: Task<Void, Never>?

    private func offerNextUp(_ mine: Int) async {
        guard let ep = episode else { return }
        var tiles: [NextUpTile] = []
        let lead = thread.isEmpty ? "What's the bigger story behind \(title)?" : thread
        tiles.append(NextUpTile(title: lead, sub: "go deeper", deeper: true, episode: nil))
        if let topics = try? await api.nextUp(topicID: ep.topicID, query: ep.key.query) {
            for (i, t) in topics.enumerated() where tiles.count < 4 {
                let sub = !t.angle.isEmpty ? t.angle : !t.subtitle.isEmpty ? t.subtitle
                    : (i == 0 && tiles.isEmpty ? "most likely next" : "recommended")
                let next = Episode(key: EpisodeKey(query: t.query, minutes: FAMAPI.browseMinutes),
                                   title: t.title, surface: ep.surface == "search" ? "search" : "myfam",
                                   topicID: t.id, picture: api.picture(t.thumb))
                tiles.append(NextUpTile(title: t.title, sub: sub, deeper: false, episode: next))
            }
        }
        guard mine == run, tiles.count >= 2 else { return }
        nextUp = tiles
        nextUpLeft = Self.nextUpSeconds
        nextUpPaused = false
        nextUpTask = Task {
            while !Task.isCancelled && nextUpLeft > 0 {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                if Task.isCancelled || nextUpPaused { return }
                nextUpLeft -= 1
            }
            if !Task.isCancelled && !nextUpPaused { playNextUp(0) }
        }
    }

    func pauseNextUp() {
        nextUpPaused = true
        nextUpTask?.cancel()
    }

    func stopNextUp() {
        nextUpTask?.cancel()
        nextUpTask = nil
        nextUp = []
    }

    func playNextUp(_ index: Int) {
        guard index < nextUp.count else { return }
        let tile = nextUp[index]
        stopNextUp()
        if tile.deeper {
            followUp(tile.title, minutes: episode?.key.minutes ?? FAMAPI.browseMinutes, typed: false)
        } else if let ep = tile.episode {
            playEpisode?(ep)
        }
    }

    // MARK: - Go Deeper (`startFollowUp`)

    /// A follow-up of the episode playing: its title is the context, and a
    /// typed one is the listener's own words.
    func followUp(_ words: String, minutes: Double, typed: Bool) {
        guard let base = episode else { return }
        let question = words.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !question.isEmpty else { return }
        Task { await api.event("search", text: question) }
        var ep = Episode(key: EpisodeKey(query: question, minutes: minutes, context: title),
                         title: Self.titleFromQuestion(question),
                         surface: base.surface == "search" ? "search" : "other",
                         own: typed || question != thread,
                         picture: nil, titleOverridden: false)
        // Going deeper on a playlist's episode is a detour, never the end of
        // the playlist (§519); a Go Deeper on that follow-up is the same one.
        ep.mixDetour = detourFor?(base) ?? ""
        playEpisode?(ep)
    }

    /// A question as a provisional title, until the writer's lands.
    static func titleFromQuestion(_ q: String) -> String {
        var t = q.trimmingCharacters(in: .whitespacesAndNewlines)
        while let last = t.last, "?.!".contains(last) { t.removeLast() }
        guard let first = t.first else { return q }
        return first.uppercased() + t.dropFirst()
    }

    // MARK: - Toasts

    func say(_ text: String, action: String? = nil, perform: (() -> Void)? = nil) {
        toastAction = perform
        let t = Toast(text: text, action: action)
        toast = t
        // A plain toast 2.2 s, one with an action (Undo, Open) 3.5 s
        // (`toast`, `snack`).
        let wait: UInt64 = action == nil ? 2_200_000_000 : 3_500_000_000
        Task {
            try? await Task.sleep(nanoseconds: wait)
            if toast?.id == t.id { toast = nil }
        }
    }

    func toastTapped() {
        let act = toastAction
        toast = nil
        toastAction = nil
        act?()
    }
}
