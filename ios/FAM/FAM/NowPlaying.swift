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
    /// The ids of what the listener attached (`attach=`): such an episode is
    /// never stored - no history row, no resume, not queueable (§op-attachments).
    var attach: String = ""
    /// A private search or Go Deeper (§282, §285): `private=1`, and no
    /// search / complete / skip event, history row or resume position.
    var isPrivate: Bool = false

    /// Whether listening to it may be written down anywhere (history,
    /// resume): not for a private one, nor one built on an attachment.
    var keepsRecord: Bool { !isPrivate && attach.isEmpty }
}

/// A queued episode (§190): client state only, never sent to the server.
struct QueueEntry: Identifiable, Equatable {
    let id = UUID()
    var episode: Episode
}

/// Something just queued, flying to the Queue button or the mini player's
/// picture (`Motion.toQueue`): a 28x22 copy of its picture.
struct QueueFlight: Identifiable, Equatable {
    let id = UUID()
    var picture: URL?
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
    /// "Saved for Later · Open": the shelf (`openSaved`).
    var openSaved: () -> Void = {}
    var signedIn: () -> Bool = { false }
    /// Whether the full player is on screen: What's next is offered there
    /// only, never under the mini bar (`post-episode-grid`).
    var playerShowing: () -> Bool = { true }
    /// Private search is on (`privateSearch`): a Go Deeper started now is
    /// private too (§285).
    var privateOn: () -> Bool = { false }
    /// FAM's speller, for a typed follow-up (`acCorrectQuestion`).
    var speller: Speller?
    /// The AI question (`withAiConsent`): asked before any Go Deeper, the
    /// suggestion's too, with Go Deeper still open (`confirmGoDeeper`).
    var ensureConsent: () async -> Bool = { true }
    /// The tab under the player (`stack[0]` on the web): what a follow-up
    /// and What's next's tiles say they came from (`historySurface`).
    var tabSurface: () -> String = { "other" }
    /// The search page's Length (`selectedLengthMinutes`), for a tile that
    /// is not a browse surface's (`episodeMinutes`).
    var searchLength: () -> Double = { FAMAPI.browseMinutes }
    /// Something was queued: its picture flies to the Queue (`Motion.toQueue`).
    @Published var queueFlight: QueueFlight?

    private var run = 0
    private var reported = false
    private var lastResume = Date.distantPast
    private var titleFinal = false
    private var sourcesShown = false
    /// What the picture is (`PLAYER_CARD`): a borrowed one (`fallback`) may
    /// be replaced by any later answer, a placed one only by nothing.
    private var cardBorrowed = false
    private var cardPlaced = false
    private var cancellables = Set<AnyCancellable>()
    private var toastAction: (() -> Void)?

    init(api: FAMAPI, player: StreamPlayer) {
        self.api = api
        self.player = player
        // Pausing sends the position at once, as the web does.
        player.$phase
            .removeDuplicates()
            .sink { [weak self] phase in
                MainActor.assumeIsolated {
                    if phase == .paused { self?.sendResume() }
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
                let answer = try? await api.next(ep.key, cachedOnly: ep.cachedOnly)
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
        cardBorrowed = false
        cardPlaced = false
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
            // A private search is nobody's history, its searcher's included
            // (§282); an attachment's episode is never stored.
            // A replay from a category's list or Search DailyFAM is not
            // written down either (`historySurface`: `cachedOnly`).
            if ep.keepsRecord && !ep.cachedOnly {
                Task { await api.noteListen(ep.key, surface: ep.surface, title: heard,
                                            episode: player.episodeID) }
            }
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
        if signedIn() && ep.keepsRecord {
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
        // A private episode's finish is not reported (§282).
        if ep.isPrivate { return }
        Task { await api.event("complete", topicID: ep.topicID, text: ep.key.query,
                               thread: thread, minutes: ep.key.minutes, episode: player.episodeID) }
    }

    private func reportSkipIfAbandoned() {
        // Abandoned part way: past 5 s, before the whole of it arrived.
        guard let ep = episode, !reported, player.position > Self.skipAfter,
              !player.complete else { return }
        reported = true
        if ep.isPrivate { return }
        Task { await api.event("skip", topicID: ep.topicID, text: ep.key.query,
                               minutes: ep.key.minutes, episode: player.episodeID) }
    }

    private func sendResume() {
        guard let ep = episode, signedIn(), player.position > 0 else { return }
        lastResume = Date()
        // No resume position for a private search (§282), nor for an
        // attachment's episode, which cannot be asked for again.
        guard ep.keepsRecord else { return }
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
        // A replay may be told about whatever is kept; anything else only
        // about what is current, or it would take a superseded episode's
        // title (`episodeReplay`, §143).
        guard let ep = episode, let answer = try? await api.next(ep.key, cachedOnly: ep.cachedOnly),
              mine == run else { return }
        if thread.isEmpty, let t = answer.thread, !t.isEmpty { thread = t }
        if answer.explicit == true { explicit = true }
        if let t = answer.title, !t.isEmpty, (answer.titleFinal == true || !titleFinal) {
            let becameFinal = answer.titleFinal == true && !titleFinal
            titleFinal = answer.titleFinal == true
            if !ep.titleOverridden && t != title {
                // The wipe is drawn by the title itself (`WipeText`).
                title = t
                // No row to retitle for a category-list replay (`cachedOnly`).
                if signedIn() && ep.keepsRecord && !ep.cachedOnly {
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
        // `askPlayerCard`: a placed answer replaces anything but a tile's
        // picture; any other answer fills an empty picture or replaces a
        // borrowed one that was never placed.
        let fromTile = ep.picture != nil
        let may = card.placed == true ? !fromTile : (picture == nil || (cardBorrowed && !cardPlaced))
        guard may else { return }
        cardBorrowed = card.fallback == true
        cardPlaced = cardPlaced || card.placed == true
        if picture != url { picture = url }
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

    /// Taken off the shelf on Save for Later: if it is the episode on the
    /// player, its save goes off too (`removeSaved`).
    func savedRemoved(query: String, minutes: Double) {
        guard let ep = episode, ep.key.query == query, ep.key.minutes == minutes else { return }
        saved = false
    }

    /// `saveForLater`: lit once the server has it - the burst and "Saved
    /// for Later · Open" after its OK, its own sentence on a no. Taking it
    /// off says "Removed from Saved for Later · Undo", and Undo puts back
    /// the episode taken off, not whatever plays by then.
    func toggleSave() {
        guard let ep = episode else { say("Play something first"); return }
        guard signedIn() else { say("Could not save that"); return }
        let heard = title
        if saved {
            Task {
                do {
                    try await api.unsave(ep.key)
                    if self.episode == ep { self.saved = false }
                    say("Removed from Saved for Later", action: "Undo") { [weak self] in
                        self?.resave(ep, title: heard)
                    }
                } catch {
                    say("Could not take that off")
                }
            }
        } else {
            Task {
                do {
                    try await api.save(ep.key, title: heard)
                    if self.episode == ep {
                        self.saved = true
                        self.savedBurst += 1
                    }
                    say("Saved for Later", action: "Open") { [weak self] in self?.openSaved() }
                } catch {
                    say((error as? FAMError)?.errorDescription ?? "Could not save that")
                }
            }
        }
    }

    /// Undo after taking an episode off the shelf: that episode, saved
    /// again; the player's save lit only if it is still the one playing.
    private func resave(_ ep: Episode, title: String) {
        Task {
            do {
                try await api.save(ep.key, title: title)
                if let now = self.episode, now.key.query == ep.key.query, now.key.minutes == ep.key.minutes {
                    self.saved = true
                }
            } catch {
                say("Could not save that")
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

    /// A vibe posted from the story editor (`postVibeStory`): VIBE! is lit
    /// only if the episode playing is still the one vibed, and VIBED bursts
    /// 300 ms after the editor has gone back to it.
    func vibedFromEditor(_ key: EpisodeKey) {
        guard let ep = episode, ep.key.query == key.query, ep.key.minutes == key.minutes else { return }
        vibed = true
        Task {
            try? await Task.sleep(nanoseconds: 300_000_000)
            if self.episode == ep { self.vibedBurst += 1 }
        }
    }

    func unvibe() {
        guard let ep = episode else { say("Play something first"); return }
        Task {
            do {
                try await api.unvibe(ep.key)
                vibed = false
                snack("Vibe taken back")
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
        // Never stored, so there is nothing to play later (`queueable`).
        guard ep.attach.isEmpty else {
            say("An episode about an attachment can\u{2019}t be queued")
            return
        }
        queue.append(QueueEntry(episode: ep))
        // It flies from wherever it was to the Queue (`Motion.toQueue`).
        queueFlight = QueueFlight(picture: ep.picture)
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
        let topics = (try? await api.nextUp(topicID: ep.topicID, query: ep.key.query)) ?? []
        guard mine == run else { return }
        // The lead is read after the round trip (`buildNextUpTiles`), so a
        // `<<NEXT:>>` line fetched as the episode ended has had the same
        // time to land.
        var tiles: [NextUpTile] = []
        let lead = goDeeperSuggestion
        if !lead.isEmpty {
            tiles.append(NextUpTile(title: lead, sub: "go deeper", deeper: true, episode: nil))
        }
        // A tile says where it was tapped from - the tab under the player -
        // and is that surface's length (`historySurface`, `episodeMinutes`).
        let surface = tabSurface()
        let browse = surface == "myfam" || surface == "dailyfam"
        let minutes = browse ? FAMAPI.browseMinutes : searchLength()
        for (i, t) in topics.enumerated() where tiles.count < 4 {
            let sub = !t.angle.isEmpty ? t.angle : !t.subtitle.isEmpty ? t.subtitle
                : (i == 0 && lead.isEmpty ? "most likely next" : "recommended")
            let next = Episode(key: EpisodeKey(query: t.query, minutes: minutes),
                               title: t.title, surface: surface,
                               topicID: t.id, picture: api.picture(t.thumb))
            tiles.append(NextUpTile(title: t.title, sub: sub, deeper: false, episode: next))
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
        let context = title
        // A typed follow-up is a search, corrected whole at send as the
        // search box's is (`acCorrectQuestion`); FAM's own suggestion - the
        // thread, or the line built from the title - is sent as written and
        // is not the listener's words (`own`, consent.py).
        let own = typed || question != goDeeperSuggestion
        guard own, let speller else {
            startFollowUp(question, base: base, context: context, minutes: minutes, own: own)
            return
        }
        Task {
            let fixed = await speller.correct(question)
            startFollowUp(fixed.query, base: base, context: context, minutes: minutes, own: true)
        }
    }

    private func startFollowUp(_ question: String, base: Episode, context: String,
                               minutes: Double, own: Bool) {
        // With private search on, a Go Deeper is private too (§285): no
        // event, no history, written under nobody's name and listed nowhere.
        let hush = privateOn()
        if !hush { Task { await api.event("search", text: question) } }
        var ep = Episode(key: EpisodeKey(query: question, minutes: minutes, context: context),
                         title: Self.titleFromPrompt(question),
                         // The tab under the player, as the web's
                         // `stack[0] === "home"` decides it.
                         surface: tabSurface() == "search" ? "search" : "other",
                         own: own,
                         picture: nil, titleOverridden: false)
        ep.isPrivate = hush
        // Going deeper on a playlist's episode is a detour, never the end of
        // the playlist (§519); a Go Deeper on that follow-up is the same one.
        ep.mixDetour = detourFor?(base) ?? ""
        playEpisode?(ep)
    }

    /// Go Deeper's suggestion (`goDeeperSuggestionText`): the episode's
    /// `<<NEXT:>>` thread, else a line built from its title.
    var goDeeperSuggestion: String {
        if !thread.isEmpty { return thread }
        return title.isEmpty ? "" : "What's the bigger story behind \(title)?"
    }

    /// A search's provisional title (`deriveTitleFromPrompt`, `SearchTitle`).
    static func titleFromPrompt(_ text: String) -> String {
        SearchTitle.from(text)
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

    /// What an action did, with nothing to tap (`snack(msg)` with no
    /// action): the same line for 2.4 s, where a plain toast is 2.2.
    func snack(_ text: String) {
        toastAction = nil
        let t = Toast(text: text, action: nil)
        toast = t
        Task {
            try? await Task.sleep(nanoseconds: 2_400_000_000)
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
