import FAMSafety
import Foundation
import Network
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
    /// This launch's (or sign-in's) first DailyFAM feed has been asked and
    /// answered: the rails are on screen, so the notifications poll may
    /// start and myFAM may be warmed behind it (a faster start).
    @Published private(set) var feedDrawn = false
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
    /// The search page's Length (`selectedLengthMinutes`): the bubble and
    /// the player's Length menu are one choice, back to 1.5 on every return
    /// to the app (SearchView keeps the two in step).
    @Published var searchLength: Double = 1.5
    /// The tab under everything (`stack[0]` on the web), told by the tabs:
    /// what a Go Deeper and What's next's tiles say they came from.
    var shownTab: Tab = .daily
    /// A link the app was opened with (`/s/`, `/m/`, `/j/`), waiting until
    /// somebody is signed in and let in (decision 5).
    var pendingLink: URL?
    /// The last link followed, so one that arrives twice is followed once.
    var lastLink: (link: AppLink, at: Date)?
    /// Tiles marked "Not interested" since the page was drawn, so an open
    /// View more drops them too.
    @Published private(set) var notInterestedIDs: Set<String> = []
    /// Rails whose last card "Not interested" took: their heading and View
    /// more end stay until the page is drawn again (the web removes cards only).
    @Published private(set) var emptiedRails: Set<String> = []
    /// This member's referral link while the waitlist runs, fetched ahead
    /// because a share sheet must open inside the tap (`loadInviteLink`).
    @Published private(set) var inviteLink: String?
    /// The YourFAM tab's stack (Settings, the shelves), and the first-run
    /// step after an account is made or signed in (`afterAccount`).
    @Published var youPath: [YouRoute] = []
    /// The first-run step; when it ends, an activation step held through it
    /// opens (`releaseActivation`, §414).
    @Published var entry: EntryStep? {
        didSet {
            if entry == nil && oldValue != nil && activationOwed {
                Task { [weak self] in await self?.checkActivation() }
            }
        }
    }
    /// The activation step owed at launch (§411, `activation_needed` on
    /// `/api/auth/me`): the web's first-time card, not closable, over the
    /// app until it is answered.
    @Published var owedActivation: Activation?
    /// `/api/auth/me` said the step is owed and it has not been shown yet.
    private var activationOwed = false

    // The front door and account life.

    /// Whether the waitlist runs, from the last `/api/auth/me` (a log-in's
    /// answer leaves it out): nil until the server has said.
    private var waitlistOn: Bool?
    /// The app's public home (`APP_HOME_URL`, `/api/auth/me`'s `home`).
    private(set) var home = ""
    /// The FAM mark over everything for half a second on log-out
    /// (`LOGOUT_SPLASH_MS`), so it reads as the app resetting.
    @Published private(set) var splash = false
    /// Bumped whenever the FAM intro plays (§201): every open, and when
    /// signing in or finishing sign-up hands over to DailyFAM.
    @Published private(set) var introRun = 0
    /// Bumped when the phone changes account without signing out (linking
    /// an Apple ID or Google account that had a FAM account of its own):
    /// every tab is drawn afresh, nothing of the last account left on it.
    @Published private(set) var accountEpoch = 0
    /// The Terms to agree to before going on (§228, `checkTerms`).
    @Published private(set) var termsOwed: TermsState?
    /// This build is retired (426): the update screen over everything.
    @Published private(set) var updateRequired: UpdateNotice?
    /// `deprecated` is said once a launch (`clientStatusSaid`).
    private var deprecatedSaid = false
    /// A sample playing in place on the welcome or status page
    /// (`welcomePlaying`): its question, "" when none; and the line under it.
    @Published private(set) var sampleQuery = ""
    @Published private(set) var sampleNote = ""
    /// An invite code a link carried (`referralCode`), kept until an
    /// account is made with it (the web's `famReferral`).
    static let referralKey = "fam.referral"
    /// This phone finished the first run (`fam.prefs` `entry: "done"`).
    static let entryKey = "fam.entryDone"
    let mixes: MixesModel
    /// YourFAM: the profile, Settings and the shelves.
    let you: YouModel
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
    /// The loading screen the last episode asked for (a playlist's glide, a
    /// tile's dial), so asking again after a 409 waits behind the same one.
    private var lastGlide: LoadingModel.Glide?
    private var lastDial: LoadingModel.Dial?
    /// "Which one do you mean?" (§426): a name in the question means more
    /// than one thing; the server's readings, asked before anything is made.
    @Published private(set) var readingAsk: ReadingAsk?
    /// The limit screen (`showLimitReached`): an allowance ran out.
    @Published var limitReached: QuotaVerdict?

    // searchFAM's state shared with Settings and the player.

    /// Private search (§282, §285): one switch in two places - the bubble
    /// and Settings - kept with the account's preferences (`private_search`),
    /// on until it is turned off, covering every search and Go Deeper.
    /// Private search, remembered on this phone so it holds from the first
    /// second after launch - before the server's preferences arrive - and a
    /// search in that window is never recorded (`private-search`). The
    /// server's answer, when it comes, is the truth and replaces it.
    @Published private(set) var privateSearch = UserDefaults.standard.bool(forKey: AppModel.privateKey) {
        didSet { UserDefaults.standard.set(privateSearch, forKey: AppModel.privateKey) }
    }
    static let privateKey = "fam.privateSearch"
    /// The voice bank and the voice searches are spoken in (§147, §447).
    @Published private(set) var voices: VoicesPage?
    /// A voice was picked on the search page for the next search only
    /// (`voicePickedForSearch`); a Settings choice is standing and sets nothing.
    private var voicePicked = false
    /// The player was playing when voice search opened: it carries on if
    /// the listener closes it without searching.
    private var resumeAfterVoice = false
    /// The status bar and keyboard over private search's charcoal
    /// (`SearchView.chromeDark`); the chosen theme everywhere else.
    @Published var darkChrome = false
    let speller: Speller
    let attachments: AttachModel
    let voiceSearch = VoiceSearchModel()
    /// "Hey FAM" (§158), opt-in in Settings; listens only while FAM is in
    /// front and nothing is playing, being made, or using the microphone.
    let wake = WakeListener()
    /// The story editor's Speak has the microphone: "Hey FAM" stands aside.
    var storyDictating = false
    /// The story editor is up (a cover over everything, voice search
    /// included): "Hey FAM" stands aside rather than open voice search
    /// where nobody can see it.
    var storyEditing = false

    // Offline listening (the web's `OfflineShelf`, §161).

    /// No connection: the phone has no path, or a request never reached the
    /// server (`isOffline`: a phone can report a network that reaches nothing).
    @Published private(set) var isOffline = false
    /// The keys of every episode kept on this phone (`OfflineShelf.keyOf`).
    @Published private(set) var keptKeys: Set<String> = []
    let shelf = OfflineShelf()
    private let pathMonitor = NWPathMonitor()
    private var pathDown = false
    private var requestFailed = false
    /// The stream to keep once it has all arrived, and the episode it is.
    private var keepWatch: (token: Int, episode: Episode)?
    /// The episode playing came off the shelf, not a stream.
    private var playingFromDevice = false
    /// The rail tiles already drawn, so only new ones arrive (`Motion.arriveRails`).
    let arrivals = ArrivalBook()

    // A faster start: one tab at a time.

    /// The first feed of this launch (after `/api/auth/me`); a second look
    /// waits on this one rather than asking again.
    private var feedTask: Task<Void, Never>?
    /// What is warmed behind the feed, one request at a time.
    private var warmTask: Task<Void, Never>?

    init(api: FAMAPI = FAMAPI()) {
        self.api = api
        let tokenSource = api
        safety = SafetyAPI(base: api.base, client: FAMAPI.client,
                           token: { tokenSource.token })
        consent = ConsentModel(api: safety)
        nowPlaying = NowPlayingModel(api: api, player: player)
        mixes = MixesModel(api: api)
        social = SocialModel(api: api)
        you = YouModel(api: api, safety: safety)
        speller = Speller(api: api)
        attachments = AttachModel(api: api)
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
                // A sample played in place says why under its card, and
                // nothing else moves (`playWelcomeSample`'s onError).
                if !self.sampleQuery.isEmpty {
                    let words = self.player.failureWords
                    self.sampleEnded(words.isEmpty ? "That episode could not be played just now." : words)
                    return
                }
                // No connection, and this one is on the phone: play it from
                // here (`onError` with `err.network` on the web).
                if self.playFromShelfAfterNetworkFailure() { return }
                // One episode of a playlist (or its detour) that would not
                // play is that episode's failure, not the playlist's.
                if self.mixPlay != nil && self.mixItemFailed() { return }
                self.generationFailed()
            }
        }
        // A replay, the dial and the glide start their audio the moment it
        // arrives; the player opens after the screen's 0.45 s (`genStepsGate`).
        loading.onAudioNow = { [weak self] in
            MainActor.assumeIsolated { self?.player.release() }
        }
        loading.onGiveUp = { [weak self] words in
            MainActor.assumeIsolated { self?.loadingGaveUp(words) }
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
                // A stream arrived, so there is a connection whatever was
                // said before.
                if !self.playingFromDevice && self.requestFailed { self.setOffline(false) }
            }
        }
        player.onFinished = { [weak self] in
            MainActor.assumeIsolated {
                guard let self else { return }
                // A sample on the front door is not an episode of the app's.
                if !self.sampleQuery.isEmpty {
                    self.sampleEnded("")
                    return
                }
                self.nowPlaying.ended()
            }
        }
        player.onArrived = { [weak self] in
            MainActor.assumeIsolated { self?.keepIfWhole() }
        }
        keptKeys = shelf.keys
        shelf.onChange = { [weak self] in
            guard let self else { return }
            self.keptKeys = self.shelf.keys
        }
        // The phone's own word for whether there is a network (the web's
        // `online` / `offline` events).
        // Delivered on the main queue, so the handler is on the main actor
        // as every other callback here is (`MainActor.assumeIsolated`).
        pathMonitor.pathUpdateHandler = { [weak self] path in
            let down = path.status != .satisfied
            MainActor.assumeIsolated { self?.pathChanged(down: down) }
        }
        pathMonitor.start(queue: .main)
        nowPlaying.playEpisode = { [weak self] ep in self?.play(ep) }
        nowPlaying.onEnded = { [weak self] ep in self?.mixItemEnded(ep) ?? false }
        nowPlaying.detourFor = { [weak self] base in self?.detour(for: base) ?? "" }
        mixes.say = { [weak self] text, action, perform in
            self?.nowPlaying.say(text, action: action, perform: perform)
        }
        nowPlaying.signedIn = { [weak self] in self?.listener?.authenticated == true }
        nowPlaying.playerShowing = { [weak self] in self?.showPlayer ?? false }
        social.say = { [weak self] text in self?.nowPlaying.say(text) }
        you.say = { [weak self] text, action, perform in
            self?.nowPlaying.say(text, action: action, perform: perform)
        }
        you.vibeDeleted = { [weak self] in
            // Weak in the task too: a strong capture inside a weak one is
            // what the compiler warned about.
            Task { [weak self] in
                await self?.loadCircle()
                await self?.social.loadMe()
            }
        }
        you.meChanged = { [weak self] in
            // Weak in the task too: a strong capture inside a weak one is
            // what the compiler warned about.
            Task { [weak self] in
                await self?.loadCircle()
                await self?.social.loadMe()
            }
        }
        nowPlaying.openSaved = { [weak self] in self?.openSaved() }
        nowPlaying.speller = speller
        nowPlaying.privateOn = { [weak self] in self?.privateSearch ?? false }
        wirePlayerHooks()
        attachments.say = { [weak self] text in self?.nowPlaying.say(text) }
        you.prefsLoaded = { [weak self] prefs in self?.privateSearch = prefs.privateSearch ?? false }
        voiceSearch.keepsPlayback = { [weak self] in self?.player.isLoaded ?? false }
        voiceSearch.onClosed = { [weak self] in self?.wake.holdAfterVoiceSearch() }
        wake.keepsPlayback = { [weak self] in self?.player.isLoaded ?? false }
        wake.say = { [weak self] text in self?.nowPlaying.say(text) }
        wake.mayListen = { [weak self] in self?.wakeMayListen ?? false }
        wake.heard = { [weak self] rest in self?.wakeHeard(rest) }
        // Off the microphone before the player takes the audio session, on
        // every start and resume, lock screen included.
        player.willStartAudio = { [weak self] in
            if Thread.isMainThread {
                MainActor.assumeIsolated { self?.wake.stop(restore: false) }
            } else {
                Task { @MainActor in self?.wake.stop(restore: false) }
            }
        }
        voiceSearch.onCancel = { [weak self] in
            guard let self, self.resumeAfterVoice else { return }
            self.resumeAfterVoice = false
            self.player.resume()
        }
        social.requestTab = { [weak self] tab in self?.tabRequest = tab }
        // "Couldn't reach FAM. Try again." when the AI answer cannot be read.
        consent.unreachable = { [weak self] in self?.nowPlaying.say("Couldn\u{2019}t reach FAM. Try again.") }
        // A deprecated or retired build hears it from any answer (§208).
        ClientStatusWatch.handler = { [weak self] status, words, link in
            MainActor.assumeIsolated { self?.clientNotice(status, words: words, link: link) }
        }
        social.leavePlayer = { [weak self] in
            guard let self, self.showPlayer else { return false }
            self.showPlayer = false
            return true
        }
    }

    // MARK: - Opening and accounts

    /// Asked on every open: who is this, and may they in? The boot chain
    /// (`refreshAuth → bootToFirstScreen → checkTerms → checkActivation`):
    /// the FAM intro over the first screen, the first run on a phone that
    /// never finished it (an account let in off the waitlist), the Terms.
    func open() async {
        // What the server makes of this build, beside everything else
        // (IOS_APP.md: version 1.0 asks at launch).
        Task { await checkClient() }
        // Whether this server writes real episodes, for the loading
        // screen's note (`genStatusNote`, from `/api/health`).
        Task { await loadHealthNote() }
        guard api.token != nil else {
            stage = .signedOut
            playIntro()
            // The waitlist switch and the home address, asked as a guest.
            Task { await learnServer() }
            return
        }
        // The last page drawn on this phone, at once, so the tab opens on
        // cards rather than LOADING; the fresh page replaces it on arrival.
        showRememberedFeed()
        // Who this is first; the feed after it, asked by the DailyFAM tab as
        // it opens (the owner's call: never the two together). Nothing else
        // is asked until the feed is on screen.
        do {
            let who = try await api.me()
            arrive(who)
            playIntro()
            noteActivation(who)
            // Signed in on a phone that never finished the first run (most
            // often somebody let in off the waitlist): the steps a new
            // account gets (`routeFirstScreen` → `afterAccount({fromBoot})`).
            if stage == .ready && !UserDefaults.standard.bool(forKey: Self.entryKey) {
                await beginEntry(handoff: false)
            }
            await checkTerms()
        } catch FAMError.signedOut {
            api.token = nil
            dropFirstFeed()
            stage = .signedOut
            playIntro()
        } catch {
            // Offline or the server is away: keep the account, show what
            // can be shown, and let the rails say why they are empty.
            stage = .ready
            playIntro()
        }
    }

    func signIn(email: String, password: String) async throws {
        arrive(try await api.signIn(email: email, password: password))
        await afterSignIn(toast: "Signed in")
    }

    func signUp(email: String, password: String) async throws {
        arrive(try await api.signUp(email: email, password: password))
        UserDefaults.standard.removeObject(forKey: Self.referralKey)
        await afterSignIn(toast: "Welcome to FAM")
    }

    /// Sign up (decision 1): the web's `/waitlist` join - the email alone
    /// (§296), the terms ticked, the invite code a link carried - then
    /// straight on to the profile with a way to skip it (`?setup=1`), and
    /// the status page after. With the waitlist off the account goes in.
    func joinWaitlist(email: String) async throws {
        let who = try await api.joinWaitlist(email: email, referral: pendingReferral)
        UserDefaults.standard.removeObject(forKey: Self.referralKey)
        arrive(who)
        await refreshWho()
        if stage == .waiting {
            entry = .identity
        } else if stage == .ready {
            nowPlaying.say("Welcome to FAM")
            await beginEntry()
            await checkTerms()
        }
    }

    /// After any sign-in (`postAuth`): the toast, `/api/auth/me` (whether
    /// the waitlist runs, the activation step), the first run, the Terms.
    private func afterSignIn(toast: String) async {
        await refreshWho()
        if stage == .ready && !toast.isEmpty { nowPlaying.say(toast) }
        await beginEntry()
        await checkTerms()
    }

    /// `/api/auth/me` again: the waitlist switch and `activation_needed`
    /// are its alone (a log-in's answer leaves them out).
    private func refreshWho() async {
        guard let who = try? await api.me() else { return }
        arrive(who)
        noteActivation(who)
    }

    /// Signed out: the waitlist switch and the home address, as a guest.
    private func learnServer() async {
        guard let who = try? await api.me() else { return }
        if let on = who.waitlist { waitlistOn = on }
        if let at = who.home, !at.isEmpty { home = at }
    }

    /// The invite code a link carried (`?referralCode=`), kept until an
    /// account is made (`famReferral`).
    var pendingReferral: String {
        UserDefaults.standard.string(forKey: Self.referralKey) ?? ""
    }

    /// Any link that opens the app may carry a waitlist invite code.
    func noteReferral(_ url: URL) {
        let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
        let code = parts?.queryItems?.first { $0.name == "referralCode" }?.value ?? ""
        let clean = code.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { return }
        UserDefaults.standard.set(String(clean.prefix(64)), forKey: Self.referralKey)
    }

    // MARK: - The FAM intro, the splash, the client's status

    /// The FAM intro (§201): skipped under Reduce Motion by the layer.
    func playIntro() {
        introRun += 1
    }

    /// `/api/health` once (`genStatusNote`): "Staging: playing the built-in
    /// sample script…", "Playing the built-in sample script…" or "The
    /// server's API key was rejected…", under the steps and in place of the
    /// dial's and the glide's status. Unread, nothing is said.
    private func loadHealthNote() async {
        guard let health = try? await api.health() else { return }
        loading.note = health.loadingNote
    }

    /// The loading screen gave up (§497): the audio stops and the episode
    /// may be asked again; a playlist's episode is that episode's failure
    /// (`mixItemFailed`), anything else the web's sentence.
    private func loadingGaveUp(_ words: String) {
        player.stop()
        if mixPlay != nil && mixItemFailed() { return }
        pending = nil
        showPlayer = false
        nowPlaying.say(words)
    }

    /// `/api/client-status` at launch.
    private func checkClient() async {
        guard let status = try? await api.clientStatus() else { return }
        if status.updateRequired {
            clientNotice("retired", words: status.message, link: "")
            // The store link is the 426's (`update_url`): one more question
            // hears it, and the watch fills it in.
            _ = try? await api.me()
        } else if status.updateRecommended {
            clientNotice("deprecated", words: status.message, link: "")
        }
    }

    /// `clientStatusSeen`: retired raises the update screen (the server's
    /// sentence and store link); deprecated is said once.
    func clientNotice(_ status: String, words: String, link: String) {
        if status == "retired" {
            let old = updateRequired
            let sentence = words.isEmpty ? (old?.message ?? UpdateNotice.fallback) : words
            let store = link.isEmpty ? (old?.link ?? "") : link
            let next = UpdateNotice(message: sentence, link: store)
            if next != old {
                updateRequired = next
                // Nothing else will work: the player comes down, so the
                // update screen is what shows.
                showPlayer = false
            }
            return
        }
        guard !deprecatedSaid else { return }
        deprecatedSaid = true
        nowPlaying.say(words.isEmpty ? "A newer version of FAM is available in the App Store" : words)
    }

    /// Back in the foreground (`visibilitychange`, §329): a signed-in
    /// listener's voice is asked for now, not at the tap. Never awaited.
    func cameToFront() {
        guard listener?.authenticated == true, stage == .ready else { return }
        let api = self.api
        Task { await api.voiceWake() }
    }

    // MARK: - The Terms, agreed (§228)

    /// `checkTerms`, after the open and every sign-in: an account that
    /// never agreed (an Apple or Google one made from Log in, an older
    /// release) or agreed to an older version is asked once.
    func checkTerms() async {
        guard stage == .ready, listener?.authenticated == true else { return }
        guard let terms = try? await api.termsState() else { return }
        termsOwed = terms.accepted ? nil : terms
    }

    /// Continue on the Terms card (`acceptTerms`).
    func acceptTerms() async {
        guard let owed = termsOwed else { return }
        if let now = try? await api.acceptTerms(version: owed.version), now.accepted {
            termsOwed = nil
        } else {
            nowPlaying.say("Couldn\u{2019}t save that. Try again.")
        }
    }

    // MARK: - Samples in place (`playWelcomeSample`, the status page's card)

    /// A tap plays or pauses in place: a replay, no player, no navigation.
    func toggleSample(_ tile: Tile) {
        if sampleQuery == tile.query && player.isLoaded {
            player.toggle()
            return
        }
        sampleQuery = tile.query
        sampleNote = "Starting the episode\u{2026}"
        player.play(title: tile.title, picture: api.picture(tile.thumb),
                    request: api.sampleAudio(query: tile.query),
                    minutes: FAMAPI.browseMinutes, holdStart: false)
    }

    /// The first word: the note goes.
    func sampleStarted() {
        if !sampleQuery.isEmpty && sampleNote.hasPrefix("Starting") { sampleNote = "" }
    }

    /// Ended or failed: the card is a play button again, with why if it failed.
    private func sampleEnded(_ why: String) {
        sampleQuery = ""
        sampleNote = why
    }

    /// Past the front door the sample is not one of the app's players
    /// (`stopWelcomeSampleLeaving`).
    func stopSample() {
        guard !sampleQuery.isEmpty else { return }
        player.stop()
        sampleQuery = ""
        sampleNote = ""
    }

    // MARK: - The activation step (§411, §414)

    /// `checkActivation` after `refreshAuth`: a log-in's answer does not
    /// say whether the step is owed, so `/api/auth/me` is asked, as the web
    /// asks it after every sign-in.
    func askActivation() async {
        guard stage == .ready, let who = try? await api.me() else { return }
        noteActivation(who)
    }

    private func noteActivation(_ who: Listener) {
        guard who.authenticated, who.activationNeeded == true else { return }
        activationOwed = true
        Task { [weak self] in await self?.checkActivation() }
    }

    /// `checkActivation`: held through sign-up's own steps (a new account
    /// is asked its name and place first); then `/api/activation`, and the
    /// card if it is still owed.
    func checkActivation() async {
        guard activationOwed, stage == .ready, entry == nil, owedActivation == nil else { return }
        guard let found = await you.loadActivation() else { return }
        activationOwed = false
        if found.needed == true { owedActivation = found }
    }

    /// "Forgot password?" finished (§244): the code and the new password
    /// sign this phone in, like a log-in.
    func finishReset(email: String, code: String, password: String) async throws {
        arrive(try await api.resetFinish(email: email, code: code, new: password))
        // "Password changed. You're signed in." is the reset screen's own.
        await afterSignIn(toast: "")
    }

    /// The password changed in Settings: the server ended every session,
    /// this one too, so this phone signs in again with the new password -
    /// held only for this call, the new token to the Keychain as any
    /// log-in's is. False when that sign-in could not be made: the dead
    /// token is dropped and the phone is on the log-in screen.
    /// `/api/auth/password` signs out every device, this one included, and
    /// the web lets it: its `postAuth` toasts, then `refreshAuth` finds this
    /// tab signed out too. So does this phone - no quiet sign-in again with
    /// the new password. Returns whether this device is still signed in
    /// (always false now; kept so the caller's shape does not change).
    func changePassword(current: String, new: String) async throws -> Bool {
        try await api.changePassword(current: current, new: new)
        player.stop()
        api.token = nil
        forgetEverything()
        return false
    }

    func signIn(provider: String, idToken: String, nonce: String,
                authorizationCode: String = "", acceptTerms: Bool) async throws {
        arrive(try await api.signIn(provider: provider, idToken: idToken, nonce: nonce,
                                    authorizationCode: authorizationCode, acceptTerms: acceptTerms,
                                    referral: pendingReferral))
        UserDefaults.standard.removeObject(forKey: Self.referralKey)
        // A new Apple or Google account made from Log in never ticked the
        // box: `checkTerms` (in `afterSignIn`) asks it once (§228).
        await afterSignIn(toast: acceptTerms ? "Welcome to FAM" : "Signed in")
    }

    /// Link Apple or Google to the account signed in now (`sign_in_with`
    /// case 2). If that Apple ID or Google account already has a FAM account
    /// of its own, the server signs in to that one instead - said plainly.
    func link(provider: String, idToken: String, nonce: String,
              authorizationCode: String = "") async throws {
        let before = listener?.userId
        let who = try await api.signIn(provider: provider, idToken: idToken, nonce: nonce,
                                       authorizationCode: authorizationCode, acceptTerms: false)
        let name = provider == "apple" ? "Apple ID" : "Google account"
        if who.userId != before {
            // Another account: nothing of the old one stays on screen or on
            // the phone - the same clearing a sign-out does, the new token
            // kept - and every tab is drawn afresh for the new one.
            player.stop()
            forgetEverything()
            accountEpoch += 1
            arrive(who)
            nowPlaying.say("That \(name) already had its own FAM account, so you're signed in to it now.")
            Task { [weak self] in await self?.afterSignIn(toast: "") }
        } else {
            arrive(who)
            nowPlaying.say(provider == "apple" ? "Apple is linked to your account" : "Google is linked to your account")
        }
    }

    /// Log out (`logOut`): the FAM mark over the screen for half a second
    /// while everything of theirs is forgotten, then the front door.
    func signOut() async {
        let began = Date()
        splash = true
        player.stop()
        await api.signOut()
        forgetEverything()
        let left = 0.5 - Date().timeIntervalSince(began)
        if left > 0 { try? await Task.sleep(nanoseconds: UInt64(left * 1_000_000_000)) }
        splash = false
    }

    func deleteAccount() async throws {
        player.stop()
        try await api.deleteAccount()
        forgetEverything()
        nowPlaying.say("Account deleted")
    }

    private func arrive(_ who: Listener) {
        guard who.authenticated else {
            api.token = nil
            stage = .signedOut
            return
        }
        if let on = who.waitlist { waitlistOn = on }
        if let at = who.home, !at.isEmpty { home = at }
        listener = who
        // Waiting only when the server says the waitlist runs and this
        // account is on it (`d.waitlist && d.status === "waitlisted"`); a
        // log-in's answer leaves `waitlist` out, so the last word on it
        // stands until `/api/auth/me` is asked again.
        let held = who.waitlisted && (waitlistOn ?? true)
        stage = held ? .waiting : .ready
        if !held { stopSample() }
        // At launch the AI question's answer and the search settings are
        // asked behind the feed (`warmBehindFeed`); once the app is running
        // (a link, a new password) they are asked again at once, as before.
        guard feedDrawn else { return }
        Task { await consent.load() }
        // Painted from the stored preference, so private search is on from
        // the first search after a launch (`loadPreferences`).
        if !held { Task { await loadSearchSettings() } }
    }

    func forgetEverything() {
        stopSample()
        termsOwed = nil
        // Search DailyFAM's way back and searchFAM's Length are the last
        // listener's too.
        catalogReturn = false
        searchLength = 1.5
        // DailyFAM's own: the faces row, Pick up, the lit bookmarks, the
        // friends on the cards, the tiles turned down and "Start here"
        // (`goDeeperData`, `savedTileQueries`, §127) - the next listener
        // never sees the last one's while a reload is on its way.
        pickUp = nil
        circle = nil
        friendFaces = []
        savedTiles = []
        notInterestedIDs = []
        emptiedRails = []
        tasteSource = "taste"
        forgetDevicePrefs()
        activationOwed = false
        owedActivation = nil
        listener = nil
        you.reset()
        youPath = []
        entry = nil
        showPlayer = false
        nowPlaying.stopped()
        consent.forget()
        rails = []
        mixes.reset()
        social.reset()
        mixPlay = nil
        mixPath = []
        dailyPath = []
        inviteLink = nil
        dropFirstFeed()
        // The episodes and the page kept on this phone are this listener's
        // (`forgetOfflineCopies`): the next person on it starts clean.
        shelf.clear()
        keptKeys = []
        keepWatch = nil
        requestFailed = false
        isOffline = pathDown
        privateSearch = false
        darkChrome = false
        voices = nil
        voicePicked = false
        attachments.clear()
        if voiceSearch.isOpen { voiceSearch.close(sending: true) }
        stage = .signedOut
    }

    /// The web's `forgetLocalPrefs` and the device half of
    /// `forgetOfflineCopies`: the speed and the pitch lock, "Hey FAM", the
    /// pinned time zone, the first run's "done" and the stories seen are
    /// this listener's, not the phone's. The theme and captions stay (their
    /// own keys on the web too).
    private func forgetDevicePrefs() {
        player.setRate(1)
        player.setKeepsPitch(true)
        wake.forget()
        let store = UserDefaults.standard
        for key in ["speed", "pitch_lock", FAMAPI.zoneKey, "storiesSeen", Self.entryKey] {
            store.removeObject(forKey: key)
        }
        FAMAPI.forgetCookies()
    }

    // MARK: - DailyFAM

    /// The page, and with it the faces row, the saved bookmarks and Pick
    /// up where you left off - as `loadMyFamFeed` does on the web.
    func loadRails() async {
        loadingRails = true
        defer { loadingRails = false }
        var drew = false
        // The rails first, then the faces row, Pick up and the bookmarks
        // (a faster start: the cards are what the listener came for).
        do {
            let data = try await api.railsData()
            if Task.isCancelled { return }
            let page = try FAMAPI.decodeRails(data)
            rails = page.sections
            emptiedRails = []
            tasteSource = page.tasteSource ?? "taste"
            friendFaces = page.friendFaces ?? []
            railsProblem = ""
            if requestFailed { setOffline(false) }
            // Remembered only once it has drawn, so a page that fails to
            // draw is never the one an offline open falls back to.
            shelf.rememberFeed(data)
            drew = true
        } catch FAMError.waitlisted {
            stage = .waiting
        } catch {
            // Dropped (signed out meanwhile): nothing to say about it.
            if Task.isCancelled { return }
            // No connection: the last page drawn, with what is on this
            // phone at full brightness and the rest faded.
            let network = error is URLError
            if network || isOffline, let last = shelf.rememberedFeed() {
                setOffline(true)
                rails = last.sections
                emptiedRails = []
                tasteSource = last.tasteSource ?? "taste"
                friendFaces = last.friendFaces ?? []
                railsProblem = ""
            } else {
                railsProblem = "Your feed could not load. Search still works — try again in a moment."
            }
        }
        async let faces: Void = loadCircle()
        async let saves: Void = loadSavedTiles()
        // Pick up only once the feed has drawn (`loadGoDeeper` after
        // `renderMyFamFeed`), never after a failure or a remembered page.
        if drew { await loadPickUp() }
        _ = await (faces, saves)
    }

    // MARK: - A faster start

    /// The last DailyFAM page this phone drew (`famLastFeed`), shown at
    /// launch while the fresh one is asked - only into an empty page.
    private func showRememberedFeed() {
        guard rails.isEmpty, let last = shelf.rememberedFeed() else { return }
        rails = last.sections
        tasteSource = last.tasteSource ?? "taste"
        friendFaces = last.friendFaces ?? []
    }

    /// This launch's first feed, asked once, after `/api/auth/me` has
    /// answered: the DailyFAM tab starts it, and anything else that asks
    /// for the first feed waits on the same one.
    @discardableResult
    func startFirstFeed() -> Task<Void, Never> {
        if let feedTask { return feedTask }
        let task = Task { [weak self] in
            guard let self else { return }
            await self.loadRails()
            if Task.isCancelled { return }
            self.feedArrived()
        }
        feedTask = task
        return task
    }

    /// The DailyFAM tab's first look: the feed already asked, or asked now
    /// (after a sign-in, which has no launch feed).
    func firstFeed() async {
        if rails.isEmpty { showRememberedFeed() }
        await startFirstFeed().value
    }

    /// Signed out (or the account turned out to be gone): the next listener
    /// starts with a feed of their own.
    private func dropFirstFeed() {
        feedTask?.cancel()
        feedTask = nil
        warmTask?.cancel()
        warmTask = nil
        feedDrawn = false
    }

    /// The feed is on screen: the poll may start (MainTabs watches
    /// `feedDrawn`), and a few seconds later what the other tabs and the
    /// first search need is warmed, one request at a time.
    private func feedArrived() {
        guard !feedDrawn else { return }
        feedDrawn = true
        warmTask?.cancel()
        warmTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 3_000_000_000)
            if Task.isCancelled { return }
            await self?.warmBehindFeed()
        }
    }

    /// One after another, never together, so the read allowance (60 in
    /// 10 s) is never spent by the warming: the AI question's answer and
    /// the search settings (so private search is on from the first search
    /// or Go Deeper), the facets' names (the tuning dial's "Tuning in to
    /// …"), the invite link (a share sheet must open inside the tap), then
    /// myFAM's mixes and categories, so that tab draws at once.
    private func warmBehindFeed() async {
        guard listener != nil, stage == .ready else { return }
        if consent.current == nil { await consent.load() }
        if Task.isCancelled { return }
        if listener?.waitlisted != true {
            if you.prefs == nil { await you.loadPrefs() }
            if Task.isCancelled { return }
            if voices == nil, let page = try? await api.voices() { voices = page }
            if Task.isCancelled { return }
        }
        await mixes.loadVocabulary()
        if Task.isCancelled { return }
        if inviteLink == nil { await loadInviteLink() }
        if Task.isCancelled { return }
        await mixes.warm()
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
        // The server's home (`AUTH.home`), else the address this build talks to.
        let url = inviteLink ?? ((home.isEmpty ? api.base.absoluteString : home) + "/")
        let who = handle.isEmpty ? "" : " \u{2014} I'm @" + handle
        return "Come listen with me on FAM" + who + ". " + url
    }

    /// A failed read leaves what was drawn (`loadGoDeeper`).
    func loadPickUp() async {
        if let page = try? await api.pickUp() { pickUp = page }
    }

    private func loadSavedTiles() async {
        // Offline the bookmarks are not drawn and not asked for (`loadTileSaves`).
        if isOffline { return }
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
        // The bookmark changes once the server has answered, as the web's
        // `saveTile` paints it - never lit for a save that did not happen.
        if savedTiles.contains(key) {
            Task {
                do {
                    try await api.unsaveTile(query: tile.query)
                    savedTiles.remove(key)
                    nowPlaying.snack("Removed from Saved for Later")
                } catch {
                    nowPlaying.say("Could not take that off")
                }
            }
        } else {
            Task {
                do {
                    try await api.saveTile(query: tile.query, title: tile.title, source: "myfam")
                    savedTiles.insert(key)
                    nowPlaying.say("Saved for Later", action: "Open") { [weak self] in self?.openSaved() }
                    await api.event("save", topicID: tile.id, text: tile.query)
                } catch {
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
                    nowPlaying.snack("Removed from Saved for Later")
                } else {
                    try await api.saveShared(query: query, minutes: minutes, title: title)
                    nowPlaying.say("Saved for Later", action: "Open") { [weak self] in self?.openSaved() }
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
        for i in rails.indices {
            let had = !rails[i].topics.isEmpty
            rails[i].topics.removeAll { $0.id == tile.id }
            if had && rails[i].topics.isEmpty { emptiedRails.insert(rails[i].key) }
        }
        notInterestedIDs.insert(tile.id)
        nowPlaying.snack("Got it — you’ll see less like this")
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
                         title: card.title.isEmpty ? SearchTitle.from(card.query) : card.title,
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
            if await api.dismissPickUp(query: query) { await loadPickUp() }
        }
    }

    /// Search DailyFAM: an episode somebody else made, replayed.
    /// A replay (`cachedOnly`, so no history row, as `historySurface`),
    /// titled by its question when it has no title (`playMyFamSearch`).
    func playCatalog(_ item: CatalogEpisode) {
        var ep = Episode(key: EpisodeKey(query: item.query, minutes: item.minutes),
                         title: item.title.isEmpty ? item.query : item.title, surface: "",
                         picture: api.picture(item.thumb ?? ""))
        ep.cachedOnly = true
        startPlaying(ep, cachedOnly: true)
    }

    /// Search DailyFAM's tap closed the list for the player: it comes back
    /// once the player or the loading screen has gone and nothing else
    /// (the limit screen, "Which one do you mean?") stands in its place.
    @Published var catalogReturn = false

    func catalogShouldReturn() -> Bool {
        guard catalogReturn, !loading.visible, !showPlayer else { return false }
        guard limitReached == nil, readingAsk == nil else { return false }
        catalogReturn = false
        return true
    }

    // MARK: - Playing

    /// A DailyFAM tile: written ahead, at the browse length (`op-taste-scoring`),
    /// behind the tuning dial (§509, `startBankTopic`): no steps, audio
    /// never held. `railKey` is the rail the card was tapped on.
    func play(_ tile: Tile, railKey: String = "") {
        if isOffline && !isKept(tile) {
            nowPlaying.say("You\u{2019}re offline, and this one isn\u{2019}t on your device yet")
            return
        }
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
        // The first rail holding the tile, in the page's order, whichever
        // one was tapped - as the web finds it in `railTiles`.
        if let found = rails.first(where: holds) {
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

    // MARK: - searchFAM

    /// What search reads at launch: the private switch and the voice bank.
    func loadSearchSettings() async {
        await you.loadPrefs()
        if let page = try? await api.voices() { voices = page }
    }

    /// The bubble or Settings (`setPrivateSearch`): painted at once, saved
    /// with the account, "Could not save that" if it was not.
    func setPrivateSearch(_ on: Bool) {
        privateSearch = on
        nowPlaying.say(on ? "Private search is on" : "Private search is off")
        Task {
            if !(await you.savePreference(["private_search": on])) {
                nowPlaying.say("Could not save that")
            }
        }
    }

    /// The voice searches are spoken in (`voiceItem`): a pick on the search
    /// page is for the next search (`voice_picked`, §447); one in Settings is
    /// a standing choice. Kept on the server either way.
    func chooseVoice(_ id: String, fromSettings: Bool) {
        if !fromSettings { voicePicked = true }
        if let page = voices {
            if id == (page.selected ?? "") { return }
            voices = VoicesPage(selected: id, voices: page.voices)
        }
        Task {
            try? await api.chooseVoice(id)
            await you.loadSettings()
        }
    }

    /// The Playback modal's Voice row (`voiceItem` from the player): a pick
    /// for the next search (`voice_picked`), kept on the server first; then,
    /// for a searched episode on the player, "Switching to …" and the same
    /// episode asked again in that voice - the cached script reused, only
    /// the audio new (§447).
    func revoice(_ id: String, label: String) {
        voicePicked = true
        guard let page = voices, id != (page.selected ?? "") else { return }
        voices = VoicesPage(selected: id, voices: page.voices)
        // `generate(key)`: asked again, never the kept episode replayed by
        // its id (that would be the old voice).
        var asked: Episode?
        if showPlayer, var ep = nowPlaying.episode, ep.surface == "search" {
            ep.episodeID = ""
            ep.cachedOnly = false
            asked = ep
        }
        let again = asked
        if again != nil { nowPlaying.say("Switching to " + label) }
        Task {
            try? await api.chooseVoice(id)
            await you.loadSettings()
            if let again { play(again) }
        }
    }

    /// The voice a search is sent in: the listener's choice, else none (the
    /// server's own default).
    var searchVoice: String { voices?.selected ?? "" }

    /// A search: the listener's own words, so the AI question comes first
    /// (5.1.2(i), `consent.py`) - asked before the tap's wait, never inside it
    /// (`sendSearch`). `query` is asked, `title` (every spelling fixed) is
    /// the provisional title until the writer's lands. With only an
    /// attachment, the fixed summarise question, titled with its name.
    /// Returns whether it was sent, so the box and the chips clear.
    @discardableResult
    func sendSearch(query: String, title: String, minutes: Double) async -> Bool {
        guard await consent.ensure() else { return false }
        if attachments.pending {
            nowPlaying.say("Still reading that attachment")
            return false
        }
        let attach = attachments.ids
        var question = query.trimmingCharacters(in: .whitespacesAndNewlines)
        let typed = !question.isEmpty
        if !typed && !attach.isEmpty { question = AttachModel.summarise }
        // Nothing typed and nothing attached sends nothing (the web plays
        // its demo episode here; quirk 1 not copied).
        guard !question.isEmpty else { return false }
        let shown: String
        if !typed && !attach.isEmpty {
            shown = attachments.firstName
        } else {
            shown = NowPlayingModel.titleFromPrompt(title.isEmpty ? question : title)
        }
        var ep = Episode(key: EpisodeKey(query: question, minutes: minutes),
                         title: shown, surface: "search",
                         picture: nil, titleOverridden: !attach.isEmpty)
        ep.attach = attach
        ep.isPrivate = privateSearch
        // What someone searches is the clearest statement of interest they
        // make - unless it is private.
        let asked = question
        if !privateSearch { Task { await api.event("search", text: asked) } }
        // The attachment belonged to that question.
        attachments.clear()
        play(ep)
        return true
    }

    /// A trending search is a search (`runTrendSearch`): the same question
    /// at the length its episode was written for, so it lands on the
    /// episode already written; private when private search is on.
    func playTrending(_ t: TrendingSearch, minutes chosen: Double) {
        // `exploreMinutes: t.minutes || 0`: with none of its own, the length
        // the search page has chosen.
        var ep = Episode(key: EpisodeKey(query: t.query, minutes: t.minutes > 0 ? t.minutes : chosen),
                         title: t.label, surface: "search",
                         picture: nil, titleOverridden: !t.title.isEmpty)
        ep.isPrivate = privateSearch
        if !privateSearch { Task { await api.event("search", text: t.query) } }
        play(ep)
    }

    /// `wakeShouldRun`, apart from the app being in front: in the app, no
    /// voice search, no episode playing or being made.
    private var wakeMayListen: Bool {
        guard stage == .ready, entry == nil, listener != nil else { return false }
        if voiceSearch.isOpen || storyDictating || storyEditing || owedActivation != nil { return false }
        if loading.visible || player.playing { return false }
        return true
    }

    /// `wakeUp`: heard. Voice search is drawn over searchFAM, and its X says
    /// it goes back there, so searchFAM is what is under it - the player or
    /// a social screen closes first.
    private func wakeHeard(_ rest: String) {
        let go: () -> Void = { [weak self] in
            guard let self else { return }
            self.tabRequest = .search
            self.openVoiceSearch(seed: rest)
        }
        if showPlayer {
            showPlayer = false
            Task { @MainActor in
                try? await Task.sleep(nanoseconds: 350_000_000)
                go()
            }
        } else {
            social.closeCover(go)
        }
    }

    /// The mic: voice search opens and listens at once. Whatever is playing
    /// pauses while the microphone has the audio. `seed` is what followed
    /// "hey FAM".
    func openVoiceSearch(seed: String = "") {
        guard VoiceSearchModel.available else {
            nowPlaying.say("This phone can't turn speech into a search")
            return
        }
        wake.stop(restore: false)
        if player.playing {
            player.pause()
            resumeAfterVoice = true
        } else {
            resumeAfterVoice = false
        }
        voiceSearch.present(seed: seed)
    }

    /// A voice search was sent: what was playing stays paused for it.
    func voiceSearchSent() {
        resumeAfterVoice = false
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
        // "Hey FAM" never listens while an episode is being made (§158).
        wake.stop()
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
        let interrupting = noteInterruption(by: ep)
        if !keepsPlaylist(ep) { mixPlay = nil }
        // Something else started: the rest of a category's list is not this
        // episode's "next" (§500 review). The Queue and the category keep it.
        if !ep.queued && !ep.fromCategory { nowPlaying.dropCategoryQueue() }
        self.resumeAt = resumeAt
        lastGlide = glide
        lastDial = dial
        // Which reading of a name is asked on any tap that may write an
        // episode, never on a replay (§426).
        let askReading = !cachedOnly && ep.episodeID.isEmpty
        // The voice is sent only for a search (§147), and "picked" once, on
        // the next search after a pick (§447).
        let searched = ep.surface == "search"
        let voice = searched ? searchVoice : ""
        let picked = searched && voicePicked
        if searched { voicePicked = false }
        let request = api.audio(query: ep.key.query, minutes: ep.key.minutes, surface: ep.surface,
                                topicID: ep.topicID, context: ep.key.context, own: ep.own,
                                cachedOnly: cachedOnly, episode: ep.episodeID,
                                reading: ep.key.reading, askReading: askReading,
                                voice: voice, voicePicked: picked,
                                isPrivate: ep.isPrivate, attach: ep.attach)
        let api = self.api
        let go = { [weak self] in
            guard let self else { return }
            // A playlist's episode a Go Deeper only interrupts is not
            // skipped: it comes back where it was (the owner's call).
            self.nowPlaying.leaving(reportSkip: !interrupting)
            self.nowPlaying.watchTitle(ep)
            self.showPlayer = false
            self.pending = ep
            self.lastAsked = ep
            // Offline, and this one is on the phone: no steps to check off,
            // the kept samples played (`fromDevice` on the web).
            if self.isOffline && ep.attach.isEmpty && self.shelf.has(ep.key) {
                self.playFromShelf(ep, glide: glide, dial: dial)
                return
            }
            self.playingFromDevice = false
            self.loading.start(ask: {
                try? await api.progress(query: ep.key.query, minutes: ep.key.minutes,
                                        surface: ep.surface, topicID: ep.topicID,
                                        context: ep.key.context, reading: ep.key.reading)
            }, glide: glide, dial: dial)
            self.player.play(title: ep.title, picture: ep.picture, request: request,
                             minutes: ep.key.minutes, holdStart: true)
            // Kept on this phone once all of it has arrived, so it plays
            // offline next time - never an attachment's (the listener's own
            // document), exactly as the web's `keepWhenWhole`.
            self.keepWatch = ep.attach.isEmpty ? (token: self.player.playToken, episode: ep) : nil
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
        // As long as it takes, while this episode is the one playing
        // (`pendingResumeSeek`): another play, a stop or a failure ends it.
        let token = player.playToken
        Task {
            while player.playToken == token {
                if player.seekLimit >= at { player.seek(to: at); return }
                if player.complete { player.seek(to: min(at, player.seekLimit)); return }
                if player.phase == .failed || player.phase == .idle { return }
                try? await Task.sleep(nanoseconds: 250_000_000)
            }
        }
    }

    /// The server said no. The AI question is asked and the episode tried
    /// again on a yes; the waitlist moves the app to its waiting screen.
    private func refused(_ error: FAMError) -> Bool {
        // A front-door sample refused is that sample's failure, said under
        // its card; nothing else moves.
        if !sampleQuery.isEmpty { return false }
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
            forgetEverything()
            showPlayer = false
            return true
        case let .refused(status, _) where status == 409 && !(pending?.episodeID.isEmpty ?? true):
            // The sender's episode is no longer kept (`askIfGone`): the
            // question is asked instead, as a message always played before §288.
            guard var again = pending else { return false }
            again.episodeID = ""
            loading.stop()
            // A resumed episode (Pick up, a playlist's after a detour) still
            // goes back to where it was, behind the same loading screen.
            if again.own {
                play(again, resumeAt: resumeAt)
            } else {
                startPlaying(again, resumeAt: resumeAt, cachedOnly: again.cachedOnly,
                             glide: lastGlide, dial: lastDial)
            }
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
            showPlayer = false
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
        // With the player on screen, back first (`onGenerationFailed`).
        showPlayer = false
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
        // Any mix played - somebody else's public one too - keeps it out of
        // dormancy (`playMixItem`: `noteMixOpen(mixPlay.id, true)`).
        if !list.mixID.isEmpty { Task { await api.noteMixOpen(list.mixID, played: true) } }
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
    /// True when it kept one: that episode is paused for the detour, not
    /// skipped.
    @discardableResult
    private func noteInterruption(by ep: Episode) -> Bool {
        guard var list = mixPlay, !list.key.isEmpty, ep.mixDetour == list.key,
              let current = nowPlaying.episode, current.mixKey == list.key else { return false }
        let midway = player.phase == .playing || player.phase == .buffering || player.phase == .paused
        var kept = false
        if midway && player.position > 0 {
            list.interrupted = InterruptedItem(episode: current, episodeID: player.episodeID,
                                               position: player.position)
            kept = true
        } else {
            list.interrupted = nil
        }
        mixPlay = list
        return kept
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
        // owner's call); skipping the playlist's own episode moves on. A
        // detour is read from the player or from what is still being made
        // (`lastGenerate`): a skip while a Go Deeper follow-up loads is a
        // skip of that follow-up.
        let moved = (playingDetour || askingDetour) ? resumeMix() : advanceMix()
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
            // Past the last item, the listener's Queue carries on (`finishMix`).
            if !resumeMix() { nowPlaying.playQueueNext() }
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
        // The last one failed with something queued: the Queue plays, as
        // the web's `finishMix` plays it.
        if !advanceMix() { nowPlaying.playQueueNext() }
        return true
    }

    /// The items still to come, for the Queue sheet's "Next from {mix}".
    var mixUpNext: [MixItem] {
        guard let list = mixPlay, list.pos + 1 < list.order.count else { return [] }
        return list.order[(list.pos + 1)...].map { list.items[$0] }
    }

    /// The player's "Remove from this playlist": a `remove` operation, never
    /// the whole list (§498); this episode plays on and the next one follows.
    /// Somebody else's public mix, or an entry already gone from the mix:
    /// "That playlist has changed", as the web says (`mixById` finds none).
    func removeFromPlayingMix() {
        guard var list = mixPlay, !list.mixID.isEmpty, list.removedKey != list.key else { return }
        guard list.mine, let item = list.current,
              mixes.mix(list.mixID)?.items.contains(where: { $0.id == item.id }) == true else {
            nowPlaying.say("That playlist has changed")
            return
        }
        list.removedKey = list.key
        mixPlay = list
        let name = list.name
        let mixID = list.mixID
        Task {
            guard let saved = await mixes.update(mixID, ["remove": [item.id]]) else { return }
            spliceFromPlaylist(mixID: mixID, itemID: item.id)
            nowPlaying.snack("Removed from \(saved.name.isEmpty ? name : saved.name)")
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
    /// (`openPlayerMenu`: `mixPlay.id && mixPlay.key === current`, and not
    /// once it was taken out). Any mix, as the web shows it - somebody
    /// else's public one too, where the tap says the playlist has changed.
    var canRemoveFromPlayingMix: Bool {
        guard let list = mixPlay, !list.mixID.isEmpty, !list.key.isEmpty,
              nowPlaying.episode?.mixKey == list.key else { return false }
        return list.removedKey != list.key
    }

    /// What was asked last - still loading, or playing - is a detour of
    /// the playlist (`lastGenerate.mixDetour`), not one of its own items.
    var askingDetour: Bool {
        guard let list = mixPlay, !list.key.isEmpty, let asked = lastAsked else { return false }
        return asked.mixDetour == list.key && asked.mixKey != list.key
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
                               title: e.title.isEmpty ? e.query : e.title, surface: "",
                               picture: api.picture(e.thumb ?? ""))
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

// MARK: - Offline listening (`OfflineShelf`, §161)

extension AppModel {
    /// A card's episode is on this phone (`on-device` on the web).
    func isKept(_ tile: Tile) -> Bool {
        keptKeys.contains(OfflineShelf.keyOf(query: tile.query, minutes: FAMAPI.browseMinutes, context: ""))
    }

    /// `setOffline`: what a failed (or a successful) request says about the
    /// connection, beside the phone's own word.
    func setOffline(_ off: Bool) {
        requestFailed = off
        let now = pathDown || requestFailed
        if now != isOffline { isOffline = now }
    }

    /// The network came or went. Coming back clears what a failed request
    /// said, as the web's `online` event does.
    fileprivate func pathChanged(down: Bool) {
        pathDown = down
        if !down { requestFailed = false }
        let now = pathDown || requestFailed
        if now != isOffline { isOffline = now }
    }

    /// The stream the player was asked for has all arrived: kept, if the
    /// server said it may be and the player has not moved on (`keepWhenWhole`).
    fileprivate func keepIfWhole() {
        guard let watch = keepWatch, watch.token == player.playToken else { return }
        keepWatch = nil
        guard let whole = player.wholeEpisode() else { return }
        shelf.put(watch.episode.key, title: watch.episode.title, rate: whole.rate, samples: whole.samples)
        keptKeys = shelf.keys
    }

    /// The kept samples, behind the same loading screen a replay has: all
    /// five done, released after a moment (`playFromDevice`).
    fileprivate func playFromShelf(_ ep: Episode, glide: LoadingModel.Glide?, dial: LoadingModel.Dial?) {
        guard let row = shelf.entry(ep.key) else { return }
        keepWatch = nil
        playingFromDevice = true
        loading.start(ask: { nil }, glide: glide, dial: dial)
        Task {
            let stored = await shelf.samples(for: row)
            // Something else was asked for meanwhile, or the wait was cancelled.
            guard pending == ep else { return }
            guard let stored, !stored.isEmpty else {
                loading.stop()
                pending = nil
                nowPlaying.say("That episode isn\u{2019}t on this device.")
                return
            }
            player.playStored(title: ep.title, picture: ep.picture, rate: row.rate, stored: stored,
                              holdStart: true)
        }
    }

    /// A stream that never reached the server, for an episode on the shelf:
    /// offline it is, and it plays from here. False when it was not that.
    fileprivate func playFromShelfAfterNetworkFailure() -> Bool {
        guard player.lastFailureWasNetwork, !playingFromDevice, let ep = lastAsked,
              ep.attach.isEmpty, shelf.has(ep.key) else { return false }
        setOffline(true)
        pending = ep
        playFromShelf(ep, glide: lastGlide, dial: lastDial)
        return true
    }
}
