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
    /// DailyFAM's stack: a rail's View more (`screen-section`), which has
    /// no tab bar and no mini player, as on the web.
    @Published var dailyPath: [String] = []
    /// Words for searchFAM's box, from Search DailyFAM's "Search FAM for
    /// it" (`searchFromMyFam`): put in the box, not sent.
    @Published var searchPrefill: String?
    /// Tiles marked "Not interested" since the page was drawn, so an open
    /// View more drops them too.
    @Published private(set) var notInterestedIDs: Set<String> = []
    /// This member's referral link while the waitlist runs, fetched ahead
    /// because a share sheet must open inside the tap (`loadInviteLink`).
    @Published private(set) var inviteLink: String?
    let mixes: MixesModel
    /// Messages, Friends and profiles, and the notifications poll.
    let social: SocialModel
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
    /// The episode last asked for (`lastGenerate`): a failure is read against
    /// what was being made, not what is on the player - a follow-up can fail
    /// before it ever reaches the player (§519).
    private var lastAsked: Episode?
    /// "Which one do you mean?" (§426): a name in the question means more
    /// than one thing; the server's readings, asked before anything is made.
    @Published private(set) var readingAsk: ReadingAsk?
    /// The limit screen (`showLimitReached`): an allowance ran out.
    @Published var limitReached: QuotaVerdict?

    init(api: FAMAPI = FAMAPI()) {
        self.api = api
        let tokenSource = api
        safety = SafetyAPI(base: api.base, client: FAMAPI.client,
                           token: { tokenSource.token })
        consent = ConsentModel(api: safety)
        nowPlaying = NowPlayingModel(api: api, player: player)
        mixes = MixesModel(api: api)
        social = SocialModel(api: api)
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
                // One episode of a playlist (or its detour) that would not
                // play is that episode's failure, not the playlist's.
                if self.mixPlay != nil && self.mixItemFailed() { return }
                self.generationFailed()
            }
        }
        loading.onRelease = { [weak self] in
            MainActor.assumeIsolated {
                guard let self else { return }
                self.player.release()
                if let ep = self.pending { self.nowPlaying.begin(ep) }
                // The glide marks the playlist's own episode Played only now:
                // a skip before a word, or a failure, is not something that
                // played. A detour is not one of its items.
                if var list = self.mixPlay, let ep = self.pending, !list.key.isEmpty, ep.mixKey == list.key {
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
        nowPlaying.onEnded = { [weak self] ep in self?.mixItemEnded(ep) ?? false }
        nowPlaying.detourFor = { [weak self] base in self?.detour(for: base) ?? "" }
        mixes.say = { [weak self] text, action, perform in
            self?.nowPlaying.say(text, action: action, perform: perform)
        }
        nowPlaying.signedIn = { [weak self] in self?.listener?.authenticated == true }
        social.say = { [weak self] text in self?.nowPlaying.say(text) }
        social.requestTab = { [weak self] tab in self?.tabRequest = tab }
        social.leavePlayer = { [weak self] in
            guard let self, self.showPlayer else { return false }
            self.showPlayer = false
            return true
        }
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
        mixes.reset()
        social.reset()
        mixPlay = nil
        mixPath = []
        dailyPath = []
        inviteLink = nil
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
        // The facets' names, for the tuning dial's "Tuning in to …".
        Task { await mixes.loadVocabulary() }
        if inviteLink == nil { Task { await loadInviteLink() } }
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

    /// While the waitlist runs an invite is this member's referral link;
    /// otherwise the app's own address is used (`inviteFriends`).
    func loadInviteLink() async {
        guard let me = try? await api.waitlistMe() else { return }
        if me.waitlist == true, let link = me.referralLink, !link.isEmpty { inviteLink = link }
    }

    /// The words an invite sends, with the link: "Come listen with me on
    /// FAM — I'm @handle." and the referral link or the app's address.
    func inviteText(handle: String) -> String {
        let url = inviteLink ?? (FAMAPI.production.absoluteString + "/")
        let who = handle.isEmpty ? "" : " \u{2014} I'm @" + handle
        return "Come listen with me on FAM" + who + ". " + url
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
        // Always the English day name, as the web's own list (`madeForTitle`).
        let weekday = Calendar.current.component(.weekday, from: now)
        let names = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
        let day = names[(weekday - 1 + 7) % 7]
        let lead = (tasteSource == "startup" || tasteSource == "sample") ? "Start here," : "Made for you,"
        return lead + (multiline ? "\n" : " ") + day + " " + part
    }

    /// A rail's heading, as the web draws it (`SECTION_TITLE`).
    func heading(for rail: Rail) -> String {
        if rail.key == "from_history" { return madeForTitle() }
        let known = Self.sectionTitle(rail.key)
        return known.isEmpty ? rail.title : known
    }

    /// `SECTION_TITLE`: "" for a key the web does not name.
    static func sectionTitle(_ key: String) -> String {
        switch key {
        case "world_trending": return "Trending"
        case "missed": return "What you missed last week"
        case "most_played": return "Most played episodes today"
        case "followers": return "What your friends are listening to"
        case "might_like": return "Explore New"
        default: return ""
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

    /// An episode a message carries, by its own length: is it saved?
    func isSaved(query: String, minutes: Double) -> Bool {
        savedTiles.contains("\(query)|\(FAMAPI.minutes(minutes))")
    }

    /// A message card's bookmark (`saveSharedEpisode`): "Saved for Later" /
    /// "Removed from Saved for Later", or "Could not save that".
    func toggleSaved(query: String, minutes: Double, title: String) {
        let key = "\(query)|\(FAMAPI.minutes(minutes))"
        let on = savedTiles.contains(key)
        if on { savedTiles.remove(key) } else { savedTiles.insert(key) }
        Task {
            do {
                if on {
                    try await api.unsaveShared(query: query, minutes: minutes)
                    nowPlaying.say("Removed from Saved for Later")
                } else {
                    try await api.saveShared(query: query, minutes: minutes, title: title)
                    nowPlaying.say("Saved for Later")
                }
            } catch {
                if on { savedTiles.insert(key) } else { savedTiles.remove(key) }
                nowPlaying.say("Could not save that")
            }
        }
    }

    /// A shared episode's Listen (`playSharedEpisode`): the sender's exact
    /// episode when the message names one, else the question.
    func playShared(query: String, minutes: Double, title: String, episode: String) {
        guard !query.isEmpty else { return }
        var ep = Episode(key: EpisodeKey(query: query, minutes: minutes),
                         title: title.isEmpty ? query : title, surface: "other")
        ep.episodeID = episode
        play(ep)
    }

    /// "Not interested" from a card's ⋯ (§499): the tile leaves every rail.
    func notInterested(_ tile: Tile) {
        Task { await api.event("not_interested", topicID: tile.id,
                               text: tile.query.isEmpty ? tile.title : tile.query) }
        for i in rails.indices { rails[i].topics.removeAll { $0.id == tile.id } }
        notInterestedIDs.insert(tile.id)
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
        // Compared as the web does, without case (`dismissGoDeeper`).
        let q = query.lowercased()
        if var page = pickUp {
            page = PickUpPage(threads: page.threads.filter { $0.thread.lowercased() != q },
                              resume: page.resume.filter { $0.query.lowercased() != q })
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

    /// A DailyFAM tile: written ahead, at the browse length (`op-taste-scoring`),
    /// behind the tuning dial (§509, `startBankTopic`): no steps, audio
    /// never held. `railKey` is the rail the card was tapped on.
    func play(_ tile: Tile, railKey: String = "") {
        let ep = Episode(key: EpisodeKey(query: tile.query, minutes: FAMAPI.browseMinutes),
                         title: tile.title, surface: "myfam", topicID: tile.id,
                         picture: api.picture(tile.thumb))
        startPlaying(ep, dial: dial(for: tile, railKey: railKey))
    }

    /// What the dial shows (`paintLoadingVariant("dial")`): the heading of
    /// the rail the tile is on, up to four neighbouring tags from that rail
    /// (every rail when it is on none) with the tile's own at most fourth,
    /// its facet named in full, and its title.
    func dial(for tile: Tile, railKey: String) -> LoadingModel.Dial {
        let tag = tile.tagText.isEmpty ? "DailyFAM" : tile.tagText
        var key = ""
        let holds: (Rail) -> Bool = { rail in rail.topics.contains(where: { other in other.id == tile.id }) }
        if !railKey.isEmpty, let tapped = rails.first(where: { rail in rail.key == railKey }), holds(tapped) {
            key = railKey
        } else if let found = rails.first(where: holds) {
            key = found.key
        }
        let heading = key == "from_history" ? madeForTitle(multiline: false) : Self.sectionTitle(key)
        var pool: [Tile] = []
        if key.isEmpty {
            for rail in rails { pool.append(contentsOf: rail.topics) }
        } else if let rail = rails.first(where: { $0.key == key }) {
            pool = rail.topics
        }
        var seen: Set<String> = [tag.lowercased()]
        var others: [String] = []
        for other in pool {
            let word = other.tagText
            if word.isEmpty || seen.contains(word.lowercased()) || others.count >= 4 { continue }
            seen.insert(word.lowercased())
            others.append(word)
        }
        let at = min(3, others.count)
        var labels = Array(others.prefix(at))
        labels.append(tag)
        labels.append(contentsOf: others.dropFirst(at))
        var title = tile.title
        if title.hasSuffix(" \u{00B7} today") { title = String(title.dropLast(" \u{00B7} today".count)) }
        return LoadingModel.Dial(heading: heading, labels: labels, at: at,
                                 tune: dialWord(tag), title: title)
    }

    /// A tag as words a person reads (`dialWord`): a facet's own name
    /// ("Money & markets"), then the category tree's label, else the tag
    /// with each word capitalised (left alone if it has capitals already).
    func dialWord(_ tag: String) -> String {
        if let named = mixes.tagLabels[tag.lowercased()] { return named }
        if let page = mixes.categories {
            if let facet = page.facets.first(where: { $0.id == tag }), facet.label != tag { return facet.label }
            if let node = page.all.first(where: { $0.id == tag }), node.label != tag { return node.label }
        }
        if tag.contains(where: { $0.isUppercase }) { return tag }
        var out = ""
        var start = true
        for ch in tag {
            out += start ? String(ch).uppercased() : String(ch)
            start = ch == " " || ch == "-" || ch == "/"
        }
        return out
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
                              glide: LoadingModel.Glide? = nil, dial: LoadingModel.Dial? = nil) {
        // A playlist's own episode, and a Go Deeper detour off it, keep the
        // playlist; anything else started on the player ends it - a search,
        // a tile, What's next, a shared episode, the Queue, a category (§519).
        noteInterruption(by: ep)
        if !keepsPlaylist(ep) { mixPlay = nil }
        // Something else started: the rest of a category's list is not this
        // episode's "next" (§500 review). The Queue and the category keep it.
        if !ep.queued && !ep.fromCategory { nowPlaying.dropCategoryQueue() }
        self.resumeAt = resumeAt
        // Which reading of a name is asked on any tap that may write an
        // episode, never on a replay (§426).
        let askReading = !cachedOnly && ep.episodeID.isEmpty
        let request = api.audio(query: ep.key.query, minutes: ep.key.minutes, surface: ep.surface,
                                topicID: ep.topicID, context: ep.key.context, own: ep.own,
                                cachedOnly: cachedOnly, episode: ep.episodeID,
                                reading: ep.key.reading, askReading: askReading)
        let api = self.api
        let go = { [weak self] in
            guard let self else { return }
            self.nowPlaying.leaving()
            self.showPlayer = false
            self.pending = ep
            self.lastAsked = ep
            self.loading.start(ask: {
                try? await api.progress(query: ep.key.query, minutes: ep.key.minutes,
                                        surface: ep.surface, topicID: ep.topicID,
                                        context: ep.key.context, reading: ep.key.reading)
            }, glide: glide, dial: dial)
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
        case let .refused(status, _) where status == 409 && !(pending?.episodeID.isEmpty ?? true):
            // The sender's episode is no longer kept (`askIfGone`): the
            // question is asked instead, as a message always played before §288.
            guard var again = pending else { return false }
            again.episodeID = ""
            loading.stop()
            // A resumed episode (Pick up, a playlist's after a detour) still
            // goes back to where it was.
            play(again, resumeAt: resumeAt)
            return true
        case let .refused(status, _) where status == 300 && (player.refusal?.readings?.count ?? 0) > 1:
            // A name that means two things (§426): which one, then the same
            // episode again with the reading chosen (`askReading`).
            guard let ep = pending ?? lastAsked else { return false }
            loading.stop()
            pending = nil
            showPlayer = false
            let readings = Array((player.refusal?.readings ?? []).prefix(4))
            readingAsk = ReadingAsk(question: ep.key.query, readings: readings, episode: ep)
            return true
        case let .refused(status, _) where status == 429 && player.refusal?.refusedBy == "quota"
            && player.refusal?.quota != nil:
            // An allowance running out is not a failure to generate: the
            // limit screen, in the server's words, and the playlist is over
            // - the next would be refused the same way (`endMixQueue`).
            loading.stop()
            pending = nil
            mixPlay = nil
            limitReached = player.refusal?.quota
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

    /// Anything else that would not play (`onGenerationFailed`): said in the
    /// server's words, else "Could not generate that briefing".
    private func generationFailed() {
        loading.stop()
        pending = nil
        let words = player.failureWords
        nowPlaying.say(words.isEmpty ? "Could not generate that briefing" : words)
    }

    /// The listener's answer to "Which one do you mean?": the choice joins
    /// the question - "how did United do (Manchester United)" - so saving,
    /// sharing and history name it as they name any question (§432), and the
    /// same episode is asked again. Cancel (nil) leaves it unplayed.
    func answerReading(_ choice: String?) {
        guard let ask = readingAsk else { return }
        readingAsk = nil
        guard let choice, !choice.isEmpty else { return }
        var ep = ask.episode
        let asked = ep.key.query
        if !asked.hasSuffix("(" + choice + ")") { ep.key.query = asked + " (" + choice + ")" }
        ep.key.reading = choice
        play(ep)
    }
}

/// "Which one do you mean?": the question asked and the server's readings.
struct ReadingAsk: Equatable {
    let question: String
    let readings: [String]
    let episode: Episode
}

// MARK: - myFAM: a mix playing as a playlist (§134)

extension AppModel {
    /// Open one of your mixes from anywhere: the myFAM tab, its page.
    func openMix(_ id: String) {
        tabRequest = .myfam
        mixPath = [.mix(id)]
        showPlayer = false
    }

    /// Somebody else's public mix from anywhere (a profile's Mixes tile):
    /// the myFAM tab, its public page.
    func openPublicMix(_ id: String) {
        tabRequest = .myfam
        mixPath = [.publicMix(id)]
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

    /// One item, as today's edition, behind the glide (`playMixItem`). Each
    /// play gets a key of its own (`mixPlay.key`): an end, a skip or a
    /// failure arriving for anything else moves nothing.
    private func playMixItem() {
        guard var list = mixPlay, let item = list.current else { return }
        list.key = UUID().uuidString
        mixPlay = list
        let (words, dated) = MixPlaylist.prompt(item)
        var ep = Episode(key: EpisodeKey(query: words, minutes: item.minutes),
                         title: dated ? item.title + " · today" : item.title,
                         surface: "dailyfam",
                         topicID: (item.custom || item.follow) ? "" : item.id)
        ep.mixKey = list.key
        let glide = LoadingModel.Glide(mixName: list.name,
                                       titles: list.order.map { list.items[$0].title },
                                       pos: list.pos, played: list.played,
                                       first: list.pos == 0)
        if list.mine { Task { await api.noteMixOpen(list.mixID, played: true) } }
        startPlaying(ep, glide: glide)
    }

    /// The playlist a follow-up of `base` is a detour of (`mixDetourFor`):
    /// the playlist's own episode, or a follow-up already on its detour.
    func detour(for base: Episode) -> String {
        guard let list = mixPlay, !list.key.isEmpty else { return "" }
        return (base.mixKey == list.key || base.mixDetour == list.key) ? list.key : ""
    }

    /// Whether starting `ep` leaves the playlist playing (§519): only its
    /// own episode and a detour off it do.
    private func keepsPlaylist(_ ep: Episode) -> Bool {
        guard let list = mixPlay, !list.key.isEmpty else { return false }
        return ep.mixKey == list.key || ep.mixDetour == list.key
    }

    /// A detour starting off the playlist's own episode part way through
    /// keeps where that episode was, so it can resume there (the owner's
    /// call: Go Deeper only interrupts the playlist - it never skips one).
    /// An episode already finished keeps nothing: the playlist then carries
    /// on with the next. Nested follow-ups keep the first interruption.
    private func noteInterruption(by ep: Episode) {
        guard var list = mixPlay, !list.key.isEmpty, ep.mixDetour == list.key,
              let current = nowPlaying.episode, current.mixKey == list.key else { return }
        let midway = player.phase == .playing || player.phase == .buffering || player.phase == .paused
        if midway && player.position > 0 {
            list.interrupted = InterruptedItem(episode: current, episodeID: player.episodeID,
                                               position: player.position)
        } else {
            list.interrupted = nil
        }
        mixPlay = list
    }

    /// An episode ended: for the playlist's own episode, the playlist's
    /// next; for a detour off it, back to the interrupted episode (or the
    /// next, if it had finished); else nothing (false lets the queue, or
    /// What's next, carry on).
    func mixItemEnded(_ ep: Episode) -> Bool {
        guard var list = mixPlay, !list.key.isEmpty else { return false }
        guard ep.mixKey == list.key || ep.mixDetour == list.key else { return false }
        // One played through (`mixOnEnd`).
        list.failures = 0
        mixPlay = list
        if ep.mixDetour == list.key { return resumeMix() }
        return advanceMix()
    }

    /// The detour is over (ended, skipped or failed): the interrupted
    /// episode resumes from where it was - the same audio by its episode id,
    /// sought once that much has arrived, as Pick up's resume does - and the
    /// playlist carries on after it. The owner's call; the web's §519 moves
    /// on to the next episode instead (PARITY.md, the player).
    private func resumeMix() -> Bool {
        guard var list = mixPlay, let back = list.interrupted else { return advanceMix() }
        list.interrupted = nil
        list.key = UUID().uuidString
        mixPlay = list
        var ep = back.episode
        ep.mixKey = list.key
        ep.mixDetour = ""
        if !back.episodeID.isEmpty { ep.episodeID = back.episodeID }
        let glide = LoadingModel.Glide(mixName: list.name,
                                       titles: list.order.map { list.items[$0].title },
                                       pos: list.pos, played: list.played,
                                       first: false)
        startPlaying(ep, resumeAt: back.position, glide: glide)
        return true
    }

    /// The next one in the playlist, from the playlist's own episode - never
    /// from a detour's follow-up (`advanceMix`) - or the end of it.
    private func advanceMix() -> Bool {
        guard var list = mixPlay else { return false }
        if list.pos + 1 < list.order.count {
            list.pos += 1
            mixPlay = list
            playMixItem()
            return true
        }
        return finishMix()
    }

    /// The player's next while a mix plays (`skipMixItem`): during a detour
    /// it moves on from the playlist's episode too. With no playlist it is
    /// the queue's next.
    func skipMixItem() {
        guard var list = mixPlay else {
            stopEpisode()
            nowPlaying.playQueueNext()
            return
        }
        // A skip is the listener moving on, not a failure: the count of
        // failures in a row starts again (`skipMixItem`).
        list.failures = 0
        mixPlay = list
        // Skipping a detour goes back to the interrupted episode (the
        // owner's call); skipping the playlist's own episode moves on.
        let moved = playingDetour ? resumeMix() : advanceMix()
        if !moved { nowPlaying.playQueueNext() }
    }

    /// After the last item: the listener's Queue carries on, else the
    /// player stops and says the mix is done (`finishMix`).
    private func finishMix() -> Bool {
        let name = mixPlay?.name ?? "myFAM"
        mixPlay = nil
        if !nowPlaying.queue.isEmpty { return false }
        stopEpisode()
        loading.stop()
        showPlayer = false
        mixDone = name
        return true
    }

    /// Something being made for the playlist could not play
    /// (`mixItemFailed`). Read from what was asked for (`lastGenerate`): a
    /// detour's follow-up that failed goes back to the playlist; the
    /// playlist's own episode moves on to the next, unless two failed in a
    /// row or there is no connection. False when it was neither.
    @discardableResult
    func mixItemFailed() -> Bool {
        guard var list = mixPlay, !list.key.isEmpty else { return false }
        let asked = lastAsked
        if let off = asked, off.mixDetour == list.key, off.mixKey != list.key {
            loading.stop()
            nowPlaying.say("That follow-up couldn\u{2019}t play \u{2014} back to your playlist.")
            _ = resumeMix()
            return true
        }
        guard let mine = asked, mine.mixKey == list.key, let item = list.current else { return false }
        loading.stop()
        let title = item.title.isEmpty ? "That episode" : item.title
        list.failures += 1
        mixPlay = list
        // No connection: the next one would fail the same way.
        if player.lastFailureWasNetwork {
            mixPlay = nil
            nowPlaying.say("\(title) couldn\u{2019}t play (no connection), so the playlist stopped.")
            return true
        }
        if list.failures >= 2 {
            mixPlay = nil
            nowPlaying.say("\(title) couldn\u{2019}t play, and neither could the one before it, so the playlist stopped.")
            return true
        }
        let last = list.pos + 1 >= list.order.count
        nowPlaying.say("\(title) couldn\u{2019}t play" + (last ? "." : " \u{2014} playing the next one."))
        _ = advanceMix()
        return true
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

    /// Only while the playlist plays its own entry - not during a detour
    /// (`openPlayerMenu`: `mixPlay.key === current`).
    var canRemoveFromPlayingMix: Bool {
        guard let list = mixPlay, list.mine, !list.key.isEmpty, let item = list.current,
              nowPlaying.episode?.mixKey == list.key else { return false }
        return mixes.mix(list.mixID)?.items.contains(where: { $0.id == item.id }) == true
    }

    /// The Queue's "Going deeper · {mix} carries on after": the episode on
    /// the player is a detour of the playlist playing.
    var playingDetour: Bool {
        guard let list = mixPlay, !list.key.isEmpty else { return false }
        return nowPlaying.episode?.mixDetour == list.key
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
            made.fromCategory = true
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
