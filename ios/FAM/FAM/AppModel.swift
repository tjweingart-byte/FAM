import FAMSafety
import Foundation
import SwiftUI

/// What the app knows: who is listening, today's rails, and what is playing.
/// The server decides everything that matters (the rails' order, what an
/// episode says, whether someone is past the waitlist); this holds the
/// answers and asks again when they may have changed.
@MainActor
final class AppModel: ObservableObject {
    enum Stage: Equatable {
        /// Asking the server who this is.
        case opening
        /// No account on this phone yet.
        case signedOut
        /// An account waiting for its place (`waitlist-gate`).
        case waiting
        case ready
    }

    @Published private(set) var stage: Stage = .opening
    @Published private(set) var listener: Listener?
    @Published private(set) var rails: [Rail] = []
    @Published private(set) var railsProblem = ""
    @Published private(set) var loadingRails = false
    @Published var showPlayer = false
    /// "taste", or "startup"/"sample": rail 1 is headed "Start here".
    @Published private(set) var tasteSource = "taste"
    /// The face book the cards' friends pills point into.
    @Published private(set) var friendFaces: [FriendFace] = []
    /// `query|minutes` of every saved episode, so bookmarks are lit.
    @Published private(set) var savedTiles: Set<String> = []
    @Published private(set) var pickUp: PickUpPage?
    @Published private(set) var circle: CirclePage?
    /// Where a resumed episode should jump to once enough has arrived.
    private var resumeAt: Double = 0

    let api: FAMAPI
    let safety: SafetyAPI
    let consent: ConsentModel
    let player = StreamPlayer()
    let loading = LoadingModel()
    let nowPlaying: NowPlayingModel
    /// The episode behind the loading screen, until its audio is released.
    private var pending: Episode?

    /// The last thing asked to play, so a refusal can be answered and retried.
    private var lastPlay: (() -> Void)?

    init(api: FAMAPI = FAMAPI()) {
        self.api = api
        let tokenSource = api
        safety = SafetyAPI(base: api.base, client: FAMAPI.client,
                           token: { tokenSource.token })
        consent = ConsentModel(api: safety)
        nowPlaying = NowPlayingModel(api: api, player: player)
        // The player calls back on the main queue (its delegate queue).
        player.onRefused = { [weak self] error in
            MainActor.assumeIsolated { self?.refused(error) ?? false }
        }
        player.onReady = { [weak self] cacheHit in
            MainActor.assumeIsolated { self?.loading.ready(cacheHit: cacheHit) }
        }
        player.onFailed = { [weak self] in
            MainActor.assumeIsolated {
                guard let self, self.loading.visible else { return }
                // Step aside and let the player say why, in the server's words.
                self.loading.stop()
                self.showPlayer = true
            }
        }
        loading.onRelease = { [weak self] in
            MainActor.assumeIsolated {
                guard let self else { return }
                self.player.release()
                if let ep = self.pending { self.nowPlaying.begin(ep) }
                self.pending = nil
                self.showPlayer = true
                self.seekWhenArrived()
            }
        }
        player.onFinished = { [weak self] in
            MainActor.assumeIsolated { self?.nowPlaying.ended() }
        }
        nowPlaying.playEpisode = { [weak self] ep in self?.play(ep) }
        nowPlaying.signedIn = { [weak self] in self?.listener?.authenticated == true }
    }

    // MARK: - Opening and accounts

    /// Asked on every open: who is this, and may they in?
    func open() async {
        guard api.token != nil else {
            stage = .signedOut
            return
        }
        do {
            let who = try await api.me()
            arrive(who)
        } catch FAMError.signedOut {
            api.token = nil
            stage = .signedOut
        } catch {
            // Offline or the server is away: keep the account, show what
            // can be shown, and let the rails say why they are empty.
            stage = .ready
        }
    }

    func signIn(email: String, password: String) async throws {
        arrive(try await api.signIn(email: email, password: password))
    }

    func signUp(email: String, password: String) async throws {
        arrive(try await api.signUp(email: email, password: password))
    }

    func signIn(provider: String, idToken: String, nonce: String,
                authorizationCode: String = "", acceptTerms: Bool) async throws {
        arrive(try await api.signIn(provider: provider, idToken: idToken, nonce: nonce,
                                    authorizationCode: authorizationCode, acceptTerms: acceptTerms))
    }

    /// Link Apple or Google to the account signed in now (`sign_in_with`
    /// case 2). If that Apple ID or Google account already has a FAM account
    /// of its own, the server signs in to that one instead - said plainly.
    func link(provider: String, idToken: String, nonce: String,
              authorizationCode: String = "") async throws {
        let before = listener?.userId
        let who = try await api.signIn(provider: provider, idToken: idToken, nonce: nonce,
                                       authorizationCode: authorizationCode, acceptTerms: false)
        arrive(who)
        let name = provider == "apple" ? "Apple ID" : "Google account"
        if who.userId != before {
            nowPlaying.say("That \(name) already had its own FAM account, so you're signed in to it now.")
        } else {
            nowPlaying.say(provider == "apple" ? "Apple is linked to your account" : "Google is linked to your account")
        }
    }

    func signOut() async {
        player.stop()
        await api.signOut()
        forget()
    }

    func deleteAccount() async throws {
        player.stop()
        try await api.deleteAccount()
        forget()
    }

    private func arrive(_ who: Listener) {
        guard who.authenticated else {
            api.token = nil
            stage = .signedOut
            return
        }
        listener = who
        stage = who.waitlisted ? .waiting : .ready
        Task { await consent.load() }
    }

    private func forget() {
        listener = nil
        rails = []
        stage = .signedOut
    }

    // MARK: - DailyFAM

    /// The page, and with it the faces row, the saved bookmarks and Pick
    /// up where you left off - as `loadMyFamFeed` does on the web.
    func loadRails() async {
        loadingRails = true
        defer { loadingRails = false }
        async let faces: Void = loadCircle()
        async let pick: Void = loadPickUp()
        async let saves: Void = loadSavedTiles()
        do {
            let page = try await api.rails()
            rails = page.sections
            tasteSource = page.tasteSource ?? "taste"
            friendFaces = page.friendFaces ?? []
            railsProblem = ""
        } catch FAMError.waitlisted {
            stage = .waiting
        } catch {
            railsProblem = "Your feed could not load. Search still works — try again in a moment."
        }
        _ = await (faces, pick, saves)
    }

    func loadCircle() async {
        if let found = try? await api.circle() { circle = found }
    }

    func loadPickUp() async {
        pickUp = (try? await api.pickUp()) ?? PickUpPage(threads: [], resume: [])
    }

    private func loadSavedTiles() async {
        if let keys = try? await api.savedKeys() { savedTiles = keys }
    }

    /// "Made for you,\nMonday evening" - or "Start here" with no history yet.
    func madeForTitle(multiline: Bool = true) -> String {
        let now = Date()
        let hour = Calendar.current.component(.hour, from: now)
        let part = hour < 12 ? "morning" : hour < 18 ? "afternoon" : "evening"
        let day = now.formatted(.dateTime.weekday(.wide))
        let lead = (tasteSource == "startup" || tasteSource == "sample") ? "Start here," : "Made for you,"
        return lead + (multiline ? "\n" : " ") + day + " " + part
    }

    /// A rail's heading, as the web draws it (`SECTION_TITLE`).
    func heading(for rail: Rail) -> String {
        switch rail.key {
        case "from_history": return madeForTitle()
        case "world_trending": return "Trending"
        case "missed": return "What you missed last week"
        case "most_played": return "Most played episodes today"
        case "followers": return "What your friends are listening to"
        default: return rail.title
        }
    }

    func isSaved(_ tile: Tile) -> Bool {
        savedTiles.contains("\(tile.query)|\(FAMAPI.minutes(FAMAPI.browseMinutes))")
    }

    /// The card's bookmark (`saveTile`).
    func toggleSave(_ tile: Tile) {
        let key = "\(tile.query)|\(FAMAPI.minutes(FAMAPI.browseMinutes))"
        if savedTiles.contains(key) {
            savedTiles.remove(key)
            Task {
                do {
                    try await api.unsaveTile(query: tile.query)
                    nowPlaying.say("Removed from Saved for Later")
                } catch {
                    savedTiles.insert(key)
                    nowPlaying.say("Could not take that off")
                }
            }
        } else {
            savedTiles.insert(key)
            Task {
                do {
                    try await api.saveTile(query: tile.query, title: tile.title, source: "myfam")
                    nowPlaying.say("Saved for Later")
                    await api.event("save", topicID: tile.id, text: tile.query)
                } catch {
                    savedTiles.remove(key)
                    nowPlaying.say("Could not save that")
                }
            }
        }
    }

    /// "Not interested" from a card's ⋯ (§499): the tile leaves every rail.
    func notInterested(_ tile: Tile) {
        Task { await api.event("not_interested", topicID: tile.id,
                               text: tile.query.isEmpty ? tile.title : tile.query) }
        for i in rails.indices { rails[i].topics.removeAll { $0.id == tile.id } }
        nowPlaying.say("Got it — you’ll see less like this")
    }

    func addToQueue(_ tile: Tile) {
        nowPlaying.addToQueue(episode(for: tile))
    }

    func episode(for tile: Tile) -> Episode {
        Episode(key: EpisodeKey(query: tile.query, minutes: FAMAPI.browseMinutes),
                title: tile.title, surface: "myfam", topicID: tile.id,
                picture: api.picture(tile.thumb))
    }

    /// A Pick up card: an episode left part way resumes where it was; a
    /// thread plays as a follow-up.
    func playPickUp(resume card: PickUpResume) {
        let ep = Episode(key: EpisodeKey(query: card.query, minutes: card.minutes, context: card.context ?? ""),
                         title: card.title.isEmpty ? NowPlayingModel.titleFromQuestion(card.query) : card.title,
                         surface: "other")
        play(ep, resumeAt: card.seconds)
    }

    func playPickUp(thread card: PickUpThread) {
        play(Episode(key: EpisodeKey(query: card.thread, minutes: FAMAPI.browseMinutes),
                     title: card.title, surface: "myfam"))
    }

    /// The X on a Pick up card: gone for good, on every device.
    func dismissPickUp(query: String) {
        if var page = pickUp {
            page = PickUpPage(threads: page.threads.filter { $0.thread != query },
                              resume: page.resume.filter { $0.query != query })
            pickUp = page
        }
        Task {
            await api.dismissPickUp(query: query)
            await loadPickUp()
        }
    }

    /// Search DailyFAM: an episode somebody else made, replayed.
    func playCatalog(_ item: CatalogEpisode) {
        startPlaying(Episode(key: EpisodeKey(query: item.query, minutes: item.minutes),
                             title: item.title, surface: "", picture: api.picture(item.thumb ?? "")),
                     cachedOnly: true)
    }

    // MARK: - Playing

    /// A DailyFAM tile: written ahead, at the browse length (`op-taste-scoring`).
    func play(_ tile: Tile) {
        play(Episode(key: EpisodeKey(query: tile.query, minutes: FAMAPI.browseMinutes),
                     title: tile.title, surface: "myfam", topicID: tile.id,
                     picture: api.picture(tile.thumb)))
    }

    /// A sample on the waiting screen: a replay, nothing written.
    func playSample(_ tile: Tile) {
        let request = api.sampleAudio(query: tile.query)
        let ep = Episode(key: EpisodeKey(query: tile.query, minutes: FAMAPI.browseMinutes),
                         title: tile.title, surface: "", picture: api.picture(tile.thumb))
        let go = { [weak self] in
            guard let self else { return }
            self.nowPlaying.leaving()
            self.pending = ep
            self.loading.start(ask: { nil })
            self.player.play(title: tile.title, picture: ep.picture, request: request,
                             minutes: FAMAPI.browseMinutes, holdStart: true)
        }
        lastPlay = go
        go()
    }

    /// A search: the listener's own words, so the AI question comes first
    /// (5.1.2(i), `consent.py`) - asked before the tap's wait, never inside it.
    /// Its provisional title is the question until the writer's lands.
    func search(_ words: String, minutes: Double) async {
        let question = words.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !question.isEmpty else { return }
        guard await consent.ensure() else { return }
        play(Episode(key: EpisodeKey(query: question, minutes: minutes),
                     title: NowPlayingModel.titleFromQuestion(question), surface: "search",
                     picture: nil, titleOverridden: false))
    }

    /// Cancel from the loading screen's X: the episode is not wanted.
    func cancelLoading() {
        loading.stop()
        pending = nil
        player.stop()
        nowPlaying.say("Cancelled")
    }

    /// The mini player's X: stop it (`transport`).
    func stopEpisode() {
        nowPlaying.stopped()
        player.stop()
    }

    /// Every play: the loading screen first, the audio held behind it, the
    /// player opened when the five steps are done (`search-no-cover`). A
    /// typed follow-up asks the AI question first, like a search.
    func play(_ ep: Episode, resumeAt: Double = 0) {
        if ep.own && consent.current?.given != true {
            Task {
                guard await consent.ensure() else { return }
                startPlaying(ep, resumeAt: resumeAt)
            }
            return
        }
        startPlaying(ep, resumeAt: resumeAt)
    }

    private func startPlaying(_ ep: Episode, resumeAt: Double = 0, cachedOnly: Bool = false) {
        self.resumeAt = resumeAt
        let request = api.audio(query: ep.key.query, minutes: ep.key.minutes, surface: ep.surface,
                                topicID: ep.topicID, context: ep.key.context, own: ep.own,
                                cachedOnly: cachedOnly)
        let api = self.api
        let go = { [weak self] in
            guard let self else { return }
            self.nowPlaying.leaving()
            self.showPlayer = false
            self.pending = ep
            self.loading.start(ask: {
                try? await api.progress(query: ep.key.query, minutes: ep.key.minutes,
                                        surface: ep.surface, topicID: ep.topicID,
                                        context: ep.key.context)
            })
            self.player.play(title: ep.title, picture: ep.picture, request: request,
                             minutes: ep.key.minutes, holdStart: true)
        }
        lastPlay = go
        go()
    }

    /// A resumed episode jumps to where it was left once that much has
    /// arrived (`pendingResumeSeek`).
    private func seekWhenArrived() {
        let at = resumeAt
        resumeAt = 0
        guard at > 0 else { return }
        Task {
            for _ in 0..<120 {
                if player.seekLimit >= at { player.seek(to: at); return }
                if player.complete { player.seek(to: min(at, player.seekLimit)); return }
                try? await Task.sleep(nanoseconds: 250_000_000)
            }
        }
    }

    /// The server said no. The AI question is asked and the episode tried
    /// again on a yes; the waitlist moves the app to its waiting screen.
    private func refused(_ error: FAMError) -> Bool {
        switch error {
        case .consentRequired:
            loading.stop()
            consent.forget()
            let retry = lastPlay
            Task {
                if await consent.ensure() { retry?() }
            }
            return true
        case .waitlisted:
            loading.stop()
            stage = .waiting
            showPlayer = false
            return true
        case .signedOut:
            loading.stop()
            api.token = nil
            forget()
            showPlayer = false
            return true
        default:
            return false
        }
    }
}
