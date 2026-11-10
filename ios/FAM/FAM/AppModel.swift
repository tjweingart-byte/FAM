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
    /// A mix playing as a playlist (§134), and the "finished" overlay.
    @Published private(set) var mixPlay: MixPlaylist?
    @Published var mixDone: String?
    /// The myFAM tab's stack, and a request to show a tab.
    @Published var mixPath: [MixRoute] = []
    @Published var tabRequest: Tab?
    let mixes: MixesModel
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
        mixes = MixesModel(api: api)
        // The player calls back on the main queue (its delegate queue).
        player.onRefused = { [weak self] error in
            MainActor.assumeIsolated { self?.refused(error) ?? false }
        }
        player.onReady = { [weak self] cacheHit in
            MainActor.assumeIsolated { self?.loading.ready(cacheHit: cacheHit) }
        }
        player.onFailed = { [weak self] in
            MainActor.assumeIsolated {
                guard let self else { return }
                if self.mixPlay != nil { self.mixItemFailed(); return }
                guard self.loading.visible else { return }
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
                if var list = self.mixPlay {
                    list.failures = 0
                    list.played.insert(list.pos)
                    self.mixPlay = list
                }
                self.pending = nil
                self.showPlayer = true
                self.seekWhenArrived()
            }
        }
        player.onFinished = { [weak self] in
            MainActor.assumeIsolated { self?.nowPlaying.ended() }
        }
        nowPlaying.playEpisode = { [weak self] ep in self?.play(ep) }
        nowPlaying.onEnded = { [weak self] in self?.mixItemEnded() ?? false }
        mixes.say = { [weak self] text, action, perform in
            self?.nowPlaying.say(text, action: action, perform: perform)
        }
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
                startPlaying(ep, resumeAt: resumeAt, cachedOnly: ep.cachedOnly)
            }
            return
        }
        // A queued category episode stays a replay (`cachedOnly`).
        startPlaying(ep, resumeAt: resumeAt, cachedOnly: ep.cachedOnly)
    }

    private func startPlaying(_ ep: Episode, resumeAt: Double = 0, cachedOnly: Bool = false,
                              glide: LoadingModel.Glide? = nil) {
        // Anything else started ends a mix's playlist.
        if glide == nil { mixPlay = nil }
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
            }, glide: glide)
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
        case let .refused(status, message):
            // An allowance running out (429, `X-FAM-Quota`) or a plan that
            // does not cover it (402): the next item would be refused the
            // same way, so the playlist is over (`endMixQueue`), said in
            // the server's own sentence. The player still shows why.
            if (status == 429 || status == 402) && mixPlay != nil {
                mixPlay = nil
                if !message.isEmpty { nowPlaying.say(message) }
            }
            return false
        default:
            return false
        }
    }
}

// MARK: - myFAM: a mix playing as a playlist (§134)

extension AppModel {
    /// Open one of your mixes from anywhere: the myFAM tab, its page.
    func openMix(_ id: String) {
        tabRequest = .myfam
        mixPath = [.mix(id)]
        showPlayer = false
    }

    /// Play a mix from one of its items to the end, then the ones above it
    /// (`startMixQueue`). Somebody else's public mix plays the same way.
    func playMix(_ mix: Mix, from itemID: String? = nil) {
        guard !mix.items.isEmpty else { nowPlaying.say("Add a topic first"); return }
        let start = itemID.flatMap { id in mix.items.firstIndex { $0.id == id } } ?? 0
        let order = Array(start..<mix.items.count) + Array(0..<start)
        mixPlay = MixPlaylist(mixID: mix.id, name: mix.name, items: mix.items, order: order, pos: 0,
                              mine: mixes.mix(mix.id) != nil)
        playMixItem()
    }

    /// One item, as today's edition, behind the glide (`playMixItem`).
    private func playMixItem() {
        guard let list = mixPlay, let item = list.current else { return }
        let (words, dated) = MixPlaylist.prompt(item)
        let ep = Episode(key: EpisodeKey(query: words, minutes: item.minutes),
                         title: dated ? item.title + " · today" : item.title,
                         surface: "dailyfam",
                         topicID: (item.custom || item.follow) ? "" : item.id)
        let glide = LoadingModel.Glide(mixName: list.name,
                                       titles: list.order.map { list.items[$0].title },
                                       pos: list.pos, played: list.played,
                                       first: list.pos == 0)
        if list.mine { Task { await api.noteMixOpen(list.mixID, played: true) } }
        startPlaying(ep, glide: glide)
    }

    /// An item ended: the next one, or the end of the playlist. False lets
    /// the queue (or What's next) carry on.
    func mixItemEnded() -> Bool {
        guard var list = mixPlay else { return false }
        if list.pos + 1 < list.order.count {
            list.pos += 1
            mixPlay = list
            playMixItem()
            return true
        }
        return finishMix()
    }

    /// The player's skip while a mix plays (`skipMixItem`).
    func skipMixItem() {
        guard var list = mixPlay else { nowPlaying.playQueueNext(); return }
        // A skip is the listener moving on, not a failure: the count of
        // failures in a row starts again (`skipMixItem`).
        list.failures = 0
        mixPlay = list
        if !mixItemEnded() { nowPlaying.playQueueNext() }
    }

    /// After the last item: the listener's Queue carries on, else the
    /// player stops and says the mix is done (`finishMix`).
    private func finishMix() -> Bool {
        let name = mixPlay?.name ?? "myFAM"
        mixPlay = nil
        if !nowPlaying.queue.isEmpty { return false }
        stopEpisode()
        showPlayer = false
        mixDone = name
        return true
    }

    /// An item could not play: the next one, unless two failed in a row
    /// (`mixItemFailed`).
    func mixItemFailed() {
        guard var list = mixPlay, let item = list.current else { return }
        loading.stop()
        let title = item.title.isEmpty ? "That episode" : item.title
        list.failures += 1
        // No connection: the next one would fail the same way.
        if player.lastFailureWasNetwork {
            mixPlay = nil
            nowPlaying.say("\(title) couldn’t play (no connection), so the playlist stopped.")
            return
        }
        if list.failures >= 2 {
            mixPlay = nil
            nowPlaying.say("\(title) couldn’t play, and neither could the one before it, so the playlist stopped.")
            return
        }
        if list.pos + 1 >= list.order.count {
            mixPlay = nil
            nowPlaying.say("\(title) couldn’t play.")
            return
        }
        nowPlaying.say("\(title) couldn’t play — playing the next one.")
        list.pos += 1
        mixPlay = list
        playMixItem()
    }

    /// The items still to come, for the Queue sheet's "Next from {mix}".
    var mixUpNext: [MixItem] {
        guard let list = mixPlay, list.pos + 1 < list.order.count else { return [] }
        return list.order[(list.pos + 1)...].map { list.items[$0] }
    }

    /// The player's "Remove from this playlist": a `remove` operation, never
    /// the whole list (§498); this episode plays on and the next one follows.
    func removeFromPlayingMix() {
        guard let list = mixPlay, list.mine, let item = list.current,
              mixes.mix(list.mixID)?.items.contains(where: { $0.id == item.id }) == true else {
            nowPlaying.say("That playlist has changed")
            return
        }
        let name = list.name
        let mixID = list.mixID
        Task {
            guard let saved = await mixes.update(mixID, ["remove": [item.id]]) else { return }
            spliceFromPlaylist(mixID: mixID, itemID: item.id)
            nowPlaying.say("Removed from \(saved.name.isEmpty ? name : saved.name)")
        }
    }

    /// Out of the running order too, by id - the playlist may have moved on
    /// meanwhile - with the position stepped back when it was at or before
    /// it, so the next item still follows (`removeFromPlayingMix`).
    private func spliceFromPlaylist(mixID: String, itemID: String) {
        guard var list = mixPlay, list.mixID == mixID else { return }
        guard let at = list.order.firstIndex(where: { list.items[$0].id == itemID }) else { return }
        list.order.remove(at: at)
        var played: Set<Int> = []
        for p in list.played {
            if p < at { played.insert(p) } else if p > at { played.insert(p - 1) }
        }
        list.played = played
        if at <= list.pos { list.pos -= 1 }
        mixPlay = list
    }

    var canRemoveFromPlayingMix: Bool {
        guard let list = mixPlay, list.mine, let item = list.current else { return false }
        return mixes.mix(list.mixID)?.items.contains(where: { $0.id == item.id }) == true
    }

    /// A category's episode: replay only, never written anew, with the rest
    /// of the list queued after it (`playCategoryList`).
    func playCategory(_ list: [CategoryEpisode], from index: Int) {
        guard index < list.count else {
            nowPlaying.say("Nothing has been made in this category today yet")
            return
        }
        // No surface: a category replay is not a DailyFAM edition
        // (`categoryQueueEntry`, `surface: ""`).
        func ep(_ e: CategoryEpisode) -> Episode {
            var made = Episode(key: EpisodeKey(query: e.query, minutes: e.minutes ?? FAMAPI.browseMinutes),
                               title: e.title, surface: "", picture: api.picture(e.thumb ?? ""))
            made.cachedOnly = true
            return made
        }
        // The rest of the category after the one tapped, never the ones
        // above it (`list.slice(1)` of what was played from).
        var rest: [Episode] = []
        if index + 1 < list.count {
            for e in list[(index + 1)...] { rest.append(ep(e)) }
        }
        nowPlaying.replaceCategoryQueue(rest)
        startPlaying(ep(list[index]), cachedOnly: true)
    }
}
