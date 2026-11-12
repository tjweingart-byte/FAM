import Foundation
import Security

/// The FAM server, as this app sees it. A native client of the same API the
/// web uses (`ios-native-client`): every path is `/api/v1/...` (`old-clients`),
/// every request names the client (`X-FAM-Client`) and the listener's clock
/// (`X-FAM-TZ`, `listener-clock`), and the listener is the bearer token kept
/// in the Keychain (`listener-header`) - never an id of the app's own.
///
/// `tests/test_ios_app_contract.py` checks in CI that every path here is a
/// route and every field decoded here is in the server's answer.
final class FAMAPI: @unchecked Sendable {
    static let production = URL(string: "https://familiarize.net")!

    /// The server this build talks to (decision 6): `FAMAPIBase` in
    /// Info.plist, written from the `FAM_API_BASE` build setting in
    /// project.yml - production by default, the staging service in the
    /// "FAM Staging" scheme, so the owner chooses per archive. An address,
    /// never a secret; anything but an https address falls back to
    /// production.
    static let configuredBase: URL = {
        var raw = (Bundle.main.object(forInfoDictionaryKey: "FAMAPIBase") as? String ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        while raw.hasSuffix("/") { raw.removeLast() }
        if raw.hasPrefix("https://"), let url = URL(string: raw) { return url }
        return production
    }()

    let base: URL

    init(base: URL = FAMAPI.configuredBase) {
        self.base = base
    }

    /// `ios/<version>+<build>` (`client_versions.py`).
    static var client: String {
        let info = Bundle.main.infoDictionary ?? [:]
        let version = info["CFBundleShortVersionString"] as? String ?? "0"
        let build = info["CFBundleVersion"] as? String ?? "0"
        return "ios/\(version)+\(build)"
    }

    /// The length every DailyFAM episode is written at (`config.BROWSE_MINUTES`).
    static let browseMinutes = 1.5

    // MARK: - The session token

    var token: String? {
        get { Keychain.read("session") }
        set {
            if let newValue { Keychain.write("session", newValue) } else { Keychain.delete("session") }
        }
    }

    // MARK: - Accounts

    /// Who the server thinks this is (`/api/auth/me`): asked on every open.
    func me() async throws -> Listener {
        try await send("GET", "api/v1/auth/me", as: Listener.self)
    }

    /// Sign in with email and password. The token comes back in the body
    /// only because it is asked for (`want_token`), and goes to the Keychain.
    func signIn(email: String, password: String) async throws -> Listener {
        let who = try await send("POST", "api/v1/auth/login",
                                 body: ["email": email, "password": password, "want_token": true],
                                 as: Listener.self)
        if let fresh = who.sessionToken { token = fresh }
        return who
    }

    /// Make an account. An iOS client must say the terms were agreed
    /// (`accept_terms`, §228) or the server answers 400.
    func signUp(email: String, password: String) async throws -> Listener {
        let who = try await send("POST", "api/v1/auth/signup",
                                 body: ["email": email, "password": password,
                                        "accept_terms": true, "want_token": true],
                                 as: Listener.self)
        if let fresh = who.sessionToken { token = fresh }
        return who
    }

    /// Sign in with Apple or Google (`/api/auth/provider`): the platform's
    /// identity token, the raw nonce it was asked with, and - Apple only -
    /// the one-time code the server exchanges so that deleting the account
    /// can revoke it (5.1.1(v), `apple_tokens.py`). `accept_terms` when the
    /// sign-in may make an account and the box was ticked (§228).
    func signIn(provider: String, idToken: String, nonce: String,
                authorizationCode: String = "", acceptTerms: Bool,
                referral: String = "") async throws -> Listener {
        var body: [String: Any] = ["provider": provider, "id_token": idToken, "nonce": nonce,
                                   "accept_terms": acceptTerms, "want_token": true]
        if !authorizationCode.isEmpty { body["authorization_code"] = authorizationCode }
        // The invite code a waitlist link carried (`referral_code`).
        if !referral.isEmpty { body["referral_code"] = String(referral.prefix(64)) }
        let who = try await send("POST", "api/v1/auth/provider", body: body, as: Listener.self)
        if let fresh = who.sessionToken { token = fresh }
        return who
    }

    /// The account's sign-in methods (`/api/account` `identities`).
    func account() async throws -> AccountInfo {
        try await get("api/v1/account", [], as: AccountInfo.self)
    }

    /// Take a provider off the account; the server refuses the last way in.
    func unlink(provider: String) async throws {
        _ = try await sendQuery("DELETE", "api/v1/account/identity",
                                [URLQueryItem(name: "provider", value: provider)], as: Ignored.self)
    }

    func signOut() async {
        _ = try? await send("POST", "api/v1/auth/logout", body: [:], as: OK.self)
        token = nil
        Self.forgetCookies()
    }

    /// No session cookie is ever kept beside the Keychain's token (every
    /// request says `httpShouldHandleCookies = false`, and the app's cookie
    /// store refuses them); anything an older build stored goes too, so a
    /// sign-out whose POST failed cannot leave the last account alive.
    static func forgetCookies() {
        let store = HTTPCookieStorage.shared
        store.cookieAcceptPolicy = .never
        for cookie in store.cookies ?? [] { store.deleteCookie(cookie) }
    }

    /// Delete the account and everything FAM holds about it (5.1.1(v)).
    func deleteAccount() async throws {
        _ = try await send("DELETE", "api/v1/account", as: OK.self)
        token = nil
        Self.forgetCookies()
    }

    // MARK: - Listening

    /// The rails the app shows as "DailyFAM" (`names-swapped`), with the
    /// face book their cards' friends pills point into.
    func rails() async throws -> RailsPage {
        try Self.decodeRails(try await railsData())
    }

    /// The same answer as it came, so the last page that drew can be kept
    /// on the phone for an offline open (`rememberFeed`).
    func railsData() async throws -> Data {
        let items = [URLQueryItem(name: "minutes", value: Self.minutes(Self.browseMinutes))]
        let (data, response) = try await readPaced(request("GET", "api/v1/myfam", query: items))
        try Self.check(response, data)
        return data
    }

    static func decodeRails(_ data: Data) throws -> RailsPage {
        do { return try decoder.decode(RailsPage.self, from: data) } catch { throw FAMError.unreadable }
    }

    /// One rail's View more: up to 16 at a time (§363), `seen` dealt past.
    func section(_ key: String, seen: [String], pageSize: Int) async throws -> SectionPage {
        var items = [
            URLQueryItem(name: "key", value: key),
            URLQueryItem(name: "minutes", value: Self.minutes(Self.browseMinutes)),
            URLQueryItem(name: "page_size", value: String(pageSize)),
            URLQueryItem(name: "seen", value: seen.joined(separator: ",")),
        ]
        // Made for you cycles through what is written (`SECTION_CYCLES`).
        if key == "from_history" { items.append(URLQueryItem(name: "cycle", value: "cached")) }
        return try await get("api/v1/myfam/section", items, as: SectionPage.self)
    }

    /// Who of your friends listened to the episode playing (§543,
    /// `/api/listened`): the faces a DailyFAM card shows, for any question,
    /// matched as every listen count is. An account only; an empty answer
    /// rather than an error.
    func listened(query: String) async throws -> ListenedPage {
        try await get("api/v1/listened", [URLQueryItem(name: "q", value: String(query.prefix(600)))],
                      as: ListenedPage.self)
    }

    /// Pick up where you left off (`/api/godeeper`).
    func pickUp() async throws -> PickUpPage {
        try await get("api/v1/godeeper", [], as: PickUpPage.self)
    }

    /// Never offer this one again, on any device.
    /// True when the server took it (`dismissGoDeeper` reloads only then).
    @discardableResult
    func dismissPickUp(query: String) async -> Bool {
        do {
            _ = try await send("POST", "api/v1/godeeper/dismiss", body: ["query": query], as: Ignored.self)
            return true
        } catch {
            return false
        }
    }

    /// Which episodes are saved, so the cards' bookmarks are lit.
    func savedKeys() async throws -> Set<String> {
        let page = try await get("api/v1/saved", [], as: SavedPage.self)
        return Set(page.items.map { "\($0.query)|\(Self.minutes($0.minutes))" })
    }

    func saveTile(query: String, title: String, source: String) async throws {
        // Cut to `SaveRequest`'s limits, so a long field is never a silent 422.
        _ = try await send("POST", "api/v1/saved", body: [
            "query": Self.cut(query, 500), "minutes": Self.browseMinutes,
            "title": Self.cut(title, 200), "source": Self.cut(source, 40),
        ], as: SavedState.self)
    }

    func unsaveTile(query: String) async throws {
        _ = try await sendQuery("DELETE", "api/v1/saved", [
            URLQueryItem(name: "q", value: query),
            URLQueryItem(name: "minutes", value: Self.minutes(Self.browseMinutes)),
        ], as: SavedState.self)
    }

    /// The faces row: friends and people followed, with their stories (§297).
    func circle() async throws -> CirclePage {
        try await get("api/v1/circle", [URLQueryItem(name: "notes", value: "1")], as: CirclePage.self)
    }

    /// The heart a story's like sends as a message (`STORY_LIKE`).
    static let storyLike = "\u{2764}\u{FE0F}"

    /// A reply to a friend's story is a message carrying the episode
    /// (`story-reply`, `postStoryMessage`). A note has no episode: the
    /// message quotes the words it answers instead, and a like reads
    /// "❤️ your story".
    func reply(to userID: String, text: String, story: Story) async throws {
        var body: [String: Any] = ["to": userID, "reply": true]
        if story.kind == "note" {
            var words = text == Self.storyLike ? Self.storyLike + " your story" : text
            let quoted = story.text ?? ""
            if !quoted.isEmpty {
                let cut = String(quoted.prefix(80))
                let more = quoted.count > 80 ? "\u{2026}" : ""
                words += " \u{2014} re: \u{201C}" + cut + more + "\u{201D}"
            }
            body["text"] = words
        } else {
            body["text"] = text
            body["query"] = story.query ?? ""
            body["minutes"] = story.minutes ?? Self.browseMinutes
            body["title"] = story.title ?? ""
        }
        _ = try await send("POST", "api/v1/messages", body: body, as: Ignored.self)
    }

    /// Take a note off your story; a note is only a story, so it is gone
    /// (`removeFromMyStory`). A vibe is deleted with `unvibe` instead.
    func removeNote(_ noteID: Int) async throws {
        let id = String(noteID)
        _ = try await send("DELETE", "api/v1/story/note/" + id, as: OK.self)
    }

    /// While the waitlist runs, an invite is this member's own referral
    /// link (`loadInviteLink`); the status page draws all of it.
    func waitlistMe() async throws -> WaitlistMe {
        try await get("api/v1/waitlist/me", [], as: WaitlistMe.self)
    }

    /// Sign up, the web's way (decision 1): every sign-up goes through the
    /// waitlist (`/waitlist`'s join form) - the email alone, no password
    /// (§296), the terms box ticked (§228) and the invite code a link
    /// carried. The account is put on the list; the token comes back
    /// because it is asked for.
    /// The join (§571): the email and a password, set on the account as it
    /// is made. A server from before §571 already took `password` (it was
    /// §296's "old join"), so nothing breaks against one.
    func joinWaitlist(email: String, password: String, referral: String) async throws -> Listener {
        let who = try await send("POST", "api/v1/waitlist/join",
                                 body: ["email": email, "password": password,
                                        "referral_code": String(referral.prefix(64)),
                                        "accept_terms": true, "want_token": true],
                                 as: Listener.self)
        if let fresh = who.sessionToken { token = fresh }
        return who
    }

    /// The status page's friend search: two characters or more.
    func waitlistPeople(_ words: String) async throws -> [WaitlistPerson] {
        try await get("api/v1/waitlist/people", [URLQueryItem(name: "q", value: words)],
                      as: WaitlistPeople.self).people
    }

    /// Add somebody the search found: a follow; friends once they add back.
    func waitlistAdd(userID: String) async throws -> WaitlistAdded {
        try await send("POST", "api/v1/waitlist/add", body: ["user_id": userID], as: WaitlistAdded.self)
    }

    /// Whether this server writes real episodes (`/api/health`, asked once
    /// at launch): the loading screen's note (`genStatusNote`).
    func health() async throws -> ServerHealth {
        try await get("api/v1/health", [], as: ServerHealth.self)
    }

    /// What the server makes of this build (asked at launch).
    func clientStatus() async throws -> ClientStatus {
        try await get("api/v1/client-status", [], as: ClientStatus.self)
    }

    /// Back in the foreground: start a GPU booting now, not at the tap
    /// (§329). Fire and forget - a lost wake costs only the cold start.
    func voiceWake() async {
        _ = try? await send("POST", "api/v1/voice/wake", body: [:], as: Ignored.self)
    }

    /// The Terms block of `/api/consent` (`checkTerms`).
    func termsState() async throws -> TermsState {
        try await get("api/v1/consent", [], as: ConsentTermsPage.self).terms
    }

    /// "Continue" on the Terms card (`acceptTerms`): `scope: "terms"`.
    func acceptTerms(version: Int) async throws -> TermsState {
        try await send("POST", "api/v1/consent",
                       body: ["scope": "terms", "allow": true, "version": version],
                       as: ConsentTermsPage.self).terms
    }

    /// The heart on a friend's story.
    func rate(query: String, minutes: Double, like: Bool) async {
        _ = try? await send("POST", "api/v1/rate", body: [
            "query": query, "minutes": minutes, "value": like ? 1 : 0,
        ], as: Ignored.self)
    }

    /// Search DailyFAM: the A to Z of episodes other people made.
    func catalog() async throws -> [CatalogEpisode] {
        try await get("api/v1/myfam/catalog", [], as: CatalogPage.self).episodes
    }

    func searchCatalog(_ words: String) async throws -> [CatalogEpisode] {
        try await get("api/v1/myfam/search", [URLQueryItem(name: "q", value: words)],
                      as: CatalogPage.self).episodes
    }

    /// Under the search box: a tap lands on an episode already written.
    func trendingSearches() async throws -> [TrendingSearch] {
        try await send("GET", "api/v1/searches/trending", as: TrendingSearches.self).searches
    }

    /// Today's kept samples, for the waiting screen (open past the waitlist).
    func samples() async throws -> [Tile] {
        try await send("GET", "api/v1/welcome", as: Samples.self).episodes
    }

    /// The audio of one episode, asked as the web asks it (decision 3,
    /// §242, §252): Opus (`fmt=opus`, "fam-opus v1"), and a replay may be
    /// answered with a signed link to its kept Ogg Opus file (`link=1`).
    /// `StreamPlayer` decodes both, and falls back to PCM by itself (the
    /// same request with `fmt=pcm`) if this phone cannot. A DailyFAM tile
    /// names its topic; a search names its length and says it is a search.
    func audio(query: String, minutes: Double, surface: String,
               topicID: String = "", context: String = "", own: Bool = false,
               cachedOnly: Bool = false, episode: String = "",
               reading: String = "", askReading: Bool = false,
               voice: String = "", voicePicked: Bool = false,
               isPrivate: Bool = false, attach: String = "") -> URLRequest {
        var items = [
            URLQueryItem(name: "q", value: query),
            URLQueryItem(name: "minutes", value: Self.minutes(minutes)),
            URLQueryItem(name: "fmt", value: "opus"),
            URLQueryItem(name: "link", value: "1"),
            URLQueryItem(name: "surface", value: surface),
        ]
        if !topicID.isEmpty { items.append(URLQueryItem(name: "topic_id", value: topicID)) }
        // A follow-up names the episode it follows (Go Deeper), and a typed
        // one is the listener's own words, held to their AI answer.
        if !context.isEmpty { items.append(URLQueryItem(name: "context", value: context)) }
        if own { items.append(URLQueryItem(name: "own", value: "1")) }
        // A replay of an episode somebody else made: never written again.
        if cachedOnly { items.append(URLQueryItem(name: "cached_only", value: "true")) }
        // The exact episode somebody heard (`X-FAM-Episode`, §173, §288): a
        // message's Listen replays the sender's, current or not, and never
        // writes; a 409 says it is no longer kept.
        if !episode.isEmpty { items.append(URLQueryItem(name: "episode", value: episode)) }
        // Which thing a shared name means (§426): the reading chosen is part
        // of the episode's key; `ask_reading` lets the server stop and ask
        // (300 with `readings`) on any tap that may write an episode -
        // never a replay (`askReading` on the web's `playOpts`).
        if !reading.isEmpty { items.append(URLQueryItem(name: "reading", value: reading)) }
        if askReading { items.append(URLQueryItem(name: "ask_reading", value: "1")) }
        // A search's voice (§147), sent for a search only, and `voice_picked`
        // once, on the search after a pick (§447): without it a cached
        // search plays in the voice its audio is already kept in.
        if !voice.isEmpty { items.append(URLQueryItem(name: "voice", value: voice)) }
        if voicePicked { items.append(URLQueryItem(name: "voice_picked", value: "1")) }
        // Private search (§282, §285): off every feed, nobody's author. The
        // server also honours the stored preference whatever is sent.
        if isPrivate { items.append(URLQueryItem(name: "private", value: "1")) }
        // What the listener attached (`/api/attach`): ids, never the file.
        if !attach.isEmpty { items.append(URLQueryItem(name: "attach", value: String(attach.prefix(400)))) }
        var audioRequest = request("GET", "api/v1/audio", query: items)
        // A cold voice may be silent longer than URLSession's 60 s: the
        // loading screen's own give-up (§497, `voice_wait` + 15 s) decides,
        // never the idle timeout.
        audioRequest.timeoutInterval = 300
        return audioRequest
    }

    /// Which of the loading screen's steps the episode being made has
    /// finished (`/api/progress`), asked with exactly the audio request's
    /// key-shaped fields - a different field asks about a different episode.
    func progress(query: String, minutes: Double, surface: String,
                  topicID: String = "", context: String = "",
                  reading: String = "") async throws -> Progress {
        var items = [
            URLQueryItem(name: "q", value: query),
            URLQueryItem(name: "minutes", value: Self.minutes(minutes)),
            URLQueryItem(name: "surface", value: surface),
        ]
        if !topicID.isEmpty { items.append(URLQueryItem(name: "topic_id", value: topicID)) }
        if !context.isEmpty { items.append(URLQueryItem(name: "context", value: context)) }
        if !reading.isEmpty { items.append(URLQueryItem(name: "reading", value: reading)) }
        let (data, response) = try await URLSession.shared.data(
            for: request("GET", "api/v1/progress", query: items))
        try Self.check(response, data)
        return try Self.decoder.decode(Progress.self, from: data)
    }

    // MARK: - The episode playing (the player's side channels)

    /// The episode's key-shaped fields, as every side channel asks them.
    private func episodeItems(_ ep: EpisodeKey) -> [URLQueryItem] {
        var items = [
            URLQueryItem(name: "q", value: ep.query),
            URLQueryItem(name: "minutes", value: Self.minutes(ep.minutes)),
        ]
        if !ep.context.isEmpty { items.append(URLQueryItem(name: "context", value: ep.context)) }
        if !ep.reading.isEmpty { items.append(URLQueryItem(name: "reading", value: ep.reading)) }
        return items
    }

    /// The thread to pick up, and the writer's title (`/api/next`).
    func next(_ ep: EpisodeKey, cachedOnly: Bool = false) async throws -> NextThread {
        var items = episodeItems(ep)
        if cachedOnly { items.append(URLQueryItem(name: "cached_only", value: "true")) }
        return try await get("api/v1/next", items, as: NextThread.self)
    }

    /// Where the episode's facts came from (`/api/sources`).
    func sources(_ ep: EpisodeKey) async throws -> Sources {
        try await get("api/v1/sources", episodeItems(ep), as: Sources.self)
    }

    /// The captions so far (`/api/transcript`); never generates.
    func transcript(_ ep: EpisodeKey) async throws -> Transcript {
        try await get("api/v1/transcript", episodeItems(ep), as: Transcript.self)
    }

    /// The episode's picture and who searched it (`/api/episode/card`).
    func card(_ ep: EpisodeKey, title: String, episode: String) async throws -> EpisodeCard {
        var items = [
            URLQueryItem(name: "q", value: ep.query),
            URLQueryItem(name: "title", value: title),
            URLQueryItem(name: "minutes", value: Self.minutes(ep.minutes)),
        ]
        if !episode.isEmpty { items.append(URLQueryItem(name: "episode", value: episode)) }
        if !ep.context.isEmpty { items.append(URLQueryItem(name: "context", value: ep.context)) }
        return try await get("api/v1/episode/card", items, as: EpisodeCard.self)
    }

    /// Four recommendations after an episode (`/api/nextup`).
    func nextUp(topicID: String, query: String) async throws -> [Tile] {
        try await get("api/v1/nextup", [
            URLQueryItem(name: "topic_id", value: topicID),
            URLQueryItem(name: "q", value: query),
        ], as: NextUpPage.self).topics
    }

    /// Is this episode saved for later?
    func isSaved(_ ep: EpisodeKey) async throws -> Bool {
        try await get("api/v1/saved", episodeItems(ep).filter { $0.name != "context" },
                      as: SavedState.self).saved
    }

    /// `source` says where it was saved from: the player, or a story.
    func save(_ ep: EpisodeKey, title: String, source: String = "player") async throws {
        _ = try await send("POST", "api/v1/saved", body: [
            "query": Self.cut(ep.query, 500), "minutes": ep.minutes,
            "title": Self.cut(title, 200), "source": Self.cut(source, 40),
        ], as: SavedState.self)
    }

    func unsave(_ ep: EpisodeKey) async throws {
        _ = try await sendQuery("DELETE", "api/v1/saved",
                                episodeItems(ep).filter { $0.name != "context" }, as: SavedState.self)
    }

    /// VIBE! the episode to your story (`/api/vibe`, `echo` in code).
    func vibe(_ ep: EpisodeKey, title: String, thread: String, caption: String) async throws {
        // `EchoRequest`'s limits: query 300, title 200, thread 200, caption 1000.
        _ = try await send("POST", "api/v1/vibe", body: [
            "query": Self.cut(ep.query, 300), "title": Self.cut(title, 200), "minutes": ep.minutes,
            "thread": Self.cut(thread, 200), "caption": Self.cut(caption, 1000),
        ], as: Ignored.self)
    }

    func unvibe(_ ep: EpisodeKey) async throws {
        _ = try await sendQuery("DELETE", "api/v1/vibe",
                                episodeItems(ep).filter { $0.name != "context" }, as: OK.self)
    }

    /// A share link (`/s/<id>`) and its wording; no account needed.
    func share(_ ep: EpisodeKey, title: String) async throws -> SharedLink {
        try await send("POST", "api/v1/share", body: [
            "query": Self.cut(ep.query, 500), "minutes": ep.minutes, "title": Self.cut(title, 200),
        ], as: SharedLink.self)
    }

    /// What the taste model learns from (`/api/event`). Fire and forget.
    func event(_ kind: String, topicID: String = "", text: String, thread: String = "",
               minutes: Double = 0, episode: String = "") async {
        var body: [String: Any] = ["kind": kind, "topic_id": topicID,
                                   "text": String(text.prefix(300)),
                                   "thread": String(thread.prefix(200))]
        if minutes > 0 { body["minutes"] = minutes }
        if !episode.isEmpty { body["episode"] = String(episode.prefix(80)) }
        _ = try? await send("POST", "api/v1/event", body: body, as: Ignored.self)
    }

    /// How far through it is, kept on the account (`POST /api/progress`).
    func noteResume(_ ep: EpisodeKey, seconds: Double, title: String, duration: Double,
                    episode: String) async {
        // `ProgressRequest`'s limits - query 500, title 200, context 300,
        // episode 80 - so a long field never silently loses the position.
        _ = try? await send("POST", "api/v1/progress", body: [
            "query": Self.cut(ep.query, 500), "minutes": ep.minutes, "seconds": min(max(0, seconds), 3600),
            "title": Self.cut(title, 200), "context": Self.cut(ep.context, 300),
            "duration": min(max(0, duration), 3600),
            "episode": Self.cut(episode, 80),
        ], as: Ignored.self)
    }

    /// A field cut to the server's `max_length` (counted in characters, as
    /// Python counts them; the web's `slice`).
    static func cut(_ text: String, _ most: Int) -> String {
        text.count > most ? String(text.prefix(most)) : text
    }

    /// The listening history row (`POST /api/history`).
    func noteListen(_ ep: EpisodeKey, surface: String, title: String, episode: String,
                    retitle: Bool = false) async {
        // The web's cuts (`noteListen`): query 500, title 200, context 300,
        // episode 80 - an over-long field is a 422 that would silently
        // leave the episode out of the history.
        var body: [String: Any] = [
            "query": String(ep.query.prefix(500)), "minutes": ep.minutes, "surface": surface,
            "title": String(title.prefix(200)), "context": String(ep.context.prefix(300)),
            "episode": String(episode.prefix(80)),
        ]
        if retitle { body["retitle"] = true }
        _ = try? await send("POST", "api/v1/history", body: body, as: Ignored.self)
    }

    private func get<T: Decodable>(_ path: String, _ items: [URLQueryItem], as type: T.Type) async throws -> T {
        try await sendQuery("GET", path, items, as: type)
    }

    private func sendQuery<T: Decodable>(_ method: String, _ path: String, _ items: [URLQueryItem],
                                         as type: T.Type) async throws -> T {
        let req = request(method, path, query: items)
        let (data, response) = try await exchange(req)
        try Self.check(response, data)
        do { return try Self.decoder.decode(T.self, from: data) } catch { throw FAMError.unreadable }
    }

    /// One request: a read through `readPaced` (its pace retry), anything
    /// else sent once - a POST is never repeated.
    func exchange(_ req: URLRequest) async throws -> (Data, URLResponse) {
        if req.httpMethod == "GET" {
            return try await readPaced(req)
        }
        return try await URLSession.shared.data(for: req)
    }

    /// A read (a GET), with one patient retry when the server's pace says
    /// "Slow down a moment" (429, `X-FAM-Refused-By: pace`): its
    /// `Retry-After`, five seconds at most, then once more - so a tab's
    /// first load (the feed above all) is not an error sentence because
    /// the 60-in-10-s read allowance was briefly full. A second refusal is
    /// answered as it was before.
    func readPaced(_ req: URLRequest) async throws -> (Data, URLResponse) {
        let first = try await URLSession.shared.data(for: req)
        guard let wait = Self.paceWait(first.1, first.0) else { return first }
        try? await Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000))
        if Task.isCancelled { return first }
        return try await URLSession.shared.data(for: req)
    }

    /// Seconds to wait before asking again, when the answer is the pace's
    /// 429; nil for anything else (the allowance's 429 included).
    static func paceWait(_ response: URLResponse, _ data: Data) -> Double? {
        guard let http = response as? HTTPURLResponse, http.statusCode == 429 else { return nil }
        let by = http.value(forHTTPHeaderField: "X-FAM-Refused-By") ?? ""
        let slow = message(in: data).hasPrefix("Slow down")
        guard by == "pace" || (by.isEmpty && slow) else { return nil }
        let asked = Double(http.value(forHTTPHeaderField: "Retry-After") ?? "") ?? 1
        return min(5, max(0.5, asked))
    }

    /// A sample replayed on the waiting screen: exactly the request the
    /// waitlist lets through (`app._welcome_sample_request`).
    func sampleAudio(query: String) -> URLRequest {
        request("GET", "api/v1/audio", query: [
            URLQueryItem(name: "q", value: query),
            URLQueryItem(name: "minutes", value: Self.minutes(Self.browseMinutes)),
            URLQueryItem(name: "fmt", value: "pcm"),
            URLQueryItem(name: "cached_only", value: "true"),
        ])
    }

    /// A picture path from a tile (`/api/thumb/...`) as a full address.
    func picture(_ path: String) -> URL? {
        guard !path.isEmpty else { return nil }
        return URL(string: path, relativeTo: base)?.absoluteURL
    }

    /// 1.5 stays "1.5"; 2.0 is "2", as the server's keys are written.
    static func minutes(_ value: Double) -> String {
        value == value.rounded() ? String(Int(value)) : String(value)
    }

    // MARK: - Optional surveys (`surveys.py`, PROBLEMS.md §560)

    /// The survey to show at this moment, or nil. Asked only when one can be
    /// shown - the server counts an answer here as offered and does not
    /// offer the same survey again for a day. `moment` is `open` or
    /// `episode_end`.
    func offerSurvey(moment: String) async throws -> Survey? {
        try await send("POST", "api/v1/surveys/offer", body: ["moment": moment],
                       as: SurveyOffer.self).survey
    }

    /// One response: `{question: {"pick": ..., "text": ...}}`, as the web
    /// sends it. A refusal is the server's own sentence.
    func answerSurvey(_ survey: String, answers: [String: Any],
                      episodeID: String = "", episodeTitle: String = "") async throws {
        var body: [String: Any] = ["survey": survey, "answers": answers, "build": Self.client]
        if survey == "episode" {
            body["episode"] = ["id": String(episodeID.prefix(200)),
                               "title": String(episodeTitle.prefix(300))]
        }
        _ = try await send("POST", "api/v1/surveys/answer", body: body, as: Ignored.self)
    }

    /// "Not now": offered again a day later, never after the third.
    func dismissSurvey(_ survey: String) async {
        _ = try? await send("POST", "api/v1/surveys/dismiss", body: ["survey": survey],
                            as: Ignored.self)
    }

    // MARK: - Plumbing

    func request(_ method: String, _ path: String, query: [URLQueryItem] = []) -> URLRequest {
        var parts = URLComponents(url: base.appendingPathComponent(path),
                                  resolvingAgainstBaseURL: false)!
        if !query.isEmpty { parts.queryItems = query }
        var req = URLRequest(url: parts.url!)
        req.httpMethod = method
        // The Keychain's bearer token is the only identity (`listener-header`):
        // the server's session cookie is never stored or sent, so it can
        // never outrank the token (`_session_token` reads the cookie first).
        req.httpShouldHandleCookies = false
        req.setValue(Self.client, forHTTPHeaderField: "X-FAM-Client")
        // The listener's clock (`listener-clock`): the zone pinned in
        // Settings on this device, else the device's own.
        req.setValue(Self.listenerZone, forHTTPHeaderField: "X-FAM-TZ")
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        if let token { req.setValue("Bearer " + token, forHTTPHeaderField: "Authorization") }
        return req
    }

    func send<T: Decodable>(_ method: String, _ path: String, body: [String: Any]? = nil,
                            as type: T.Type) async throws -> T {
        var req = request(method, path)
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, response) = try await exchange(req)
        try Self.check(response, data)
        do {
            return try Self.decoder.decode(T.self, from: data)
        } catch {
            throw FAMError.unreadable
        }
    }

    static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    static func check(_ response: URLResponse, _ data: Data) throws {
        if let http = response as? HTTPURLResponse { ClientStatusWatch.seen(http, data) }
        guard let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) else { return }
        let detail = message(in: data)
        // Only a waitlisted account is moved to its status page
        // (`/waitlist/me`); any other refusal of this kind is somebody the
        // server does not know as a member - signed out, to the front door
        // (§217, §231), exactly as the web's fetch wrapper decides.
        if let go = http.value(forHTTPHeaderField: "X-FAM-Waitlist") {
            if go == "/waitlist/me" { throw FAMError.waitlisted(detail) }
            throw FAMError.signedOut(detail)
        }
        if http.value(forHTTPHeaderField: "X-FAM-Consent") == "ai" { throw FAMError.consentRequired }
        if http.statusCode == 401 { throw FAMError.signedOut(detail) }
        throw FAMError.refused(status: http.statusCode, message: detail)
    }

    /// The server's own sentence: `detail` (FastAPI) or `error`.
    static func message(in data: Data) -> String {
        guard let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return "" }
        if let text = body["detail"] as? String { return text }
        if let text = body["error"] as? String { return text }
        return ""
    }
}

// MARK: - What the server sends

/// `/api/auth/me`, `/api/auth/login`, `/api/auth/signup` (`Listener.as_dict`).
struct Listener: Decodable, Equatable {
    let userId: String
    let email: String
    let authenticated: Bool
    let displayName: String?
    let status: String?
    let sessionToken: String?
    /// The activation step is still owed (§411): only `/api/auth/me` says
    /// so (a sign-up or log-in answer leaves it out, and it decodes nil).
    let activationNeeded: Bool?
    /// Whether the waitlist runs (`settings.waitlist`): only `/api/auth/me`
    /// says so. The app waits only when this and `waitlisted` are both true,
    /// as the web's `refreshAuth` does (`d.waitlist && d.status === "waitlisted"`).
    let waitlist: Bool?
    /// The app's public home (`APP_HOME_URL`), from `/api/auth/me`: where an
    /// invite sends somebody when there is no referral link.
    let home: String?

    /// Waiting for a place while the waitlist runs (`waitlist.WAITLISTED`).
    var waitlisted: Bool { status == "waitlisted" }
}

struct RailsPage: Decodable {
    let sections: [Rail]
    /// "taste", or "startup"/"sample" while a listener has no history:
    /// rail 1 is then headed "Start here".
    let tasteSource: String?
    let friendFaces: [FriendFace]?
}

/// A face a card's "friends listened" pill points into (`friend_faces`).
struct FriendFace: Decodable, Hashable {
    let name: String
    let handle: String?
    let avatar: String?
    /// The E mark before a flagged name (§224).
    let explicit: Bool?
}

/// `/api/listened` (§543): the friends who listened to one question -
/// `count` all of them, `faces` the newest first. Lenient: anything
/// missing decodes as nobody.
struct ListenedPage: Decodable {
    let count: Int?
    let faces: [FriendFace]?
}

/// `/api/myfam/section`.
struct SectionPage: Decodable {
    let key: String
    let topics: [Tile]
    let emptyReason: String?
    let ready: Int?
    let total: Int?
    let more: Bool?
    let wrapped: Bool?
    let caughtUp: Bool?
    let friendFaces: [FriendFace]?
    let groups: [TileGroup]?
    /// The page's size when it was dealt one (`page_size`): Refresh is
    /// offered only then (`renderSection`).
    let pageSize: Int?
}

/// Trending's View more, by continent: Worldwide first (§174).
struct TileGroup: Decodable, Identifiable {
    let key: String
    let label: String
    let yours: Bool?
    let topics: [Tile]
    var id: String { key }
}

/// `/api/godeeper`: episodes left part way, and threads to pick up.
struct PickUpPage: Decodable {
    let threads: [PickUpThread]
    let resume: [PickUpResume]
}

struct PickUpThread: Decodable, Hashable {
    let thread: String
    let title: String
    let summary: String?
}

struct PickUpResume: Decodable, Hashable {
    let query: String
    let minutes: Double
    let seconds: Double
    let title: String
    let context: String?
    let duration: Double?
    let summary: String?
}

struct SavedPage: Decodable { let items: [SavedItem] }

struct SavedItem: Decodable {
    let query: String
    let minutes: Double
    let title: String?
    /// The shelf's own row id, its folder ("" unfiled) and where it was
    /// saved from (`saved.SavedItem.as_dict`); a pointer, never an episode id.
    let id: String?
    let folderId: String?
    let source: String?
}

/// `/api/circle?notes=1`.
struct CirclePage: Decodable {
    let circle: [CirclePerson]
    let me: CircleMe?
}

struct CirclePerson: Decodable, Identifiable {
    let userId: String
    let name: String
    let handle: String
    let avatar: String
    let fresh: Bool?
    let stories: [Story]
    var id: String { userId }
}

struct CircleMe: Decodable {
    let name: String?
    let handle: String?
    let avatar: String?
    let stories: [Story]?
}

/// A vibe on someone's story, or a note (`kind: "note"`, no episode).
/// `style` is the editor's layout (a note's background, the text's face,
/// size and place, stickers); `tags` the people @mentioned on it.
struct Story: Decodable, Identifiable, Hashable {
    let id: Int
    let kind: String?
    let query: String?
    let title: String?
    let minutes: Double?
    let caption: String?
    let text: String?
    let at: Double
    let style: StoryStyle?
    let tags: [StoryTag]?
    /// Posted to Close Friends only.
    let close: Bool?
}

/// A story's layout (`social.clean_style` / `clean_note_style`). Every
/// field optional and read leniently: a layout the app cannot read draws
/// the plain page rather than dropping the faces row. A vibe's picture is
/// framed (`frame`), sized (`zoom`) and moved (`tx`, `ty`); a note has a
/// background (`bg`) instead.
struct StoryStyle: Decodable, Hashable {
    let bg: String?
    let font: String?
    let size: Double?
    let cx: Double?
    let cy: Double?
    let frame: String?
    let zoom: Double?
    let tx: Double?
    let ty: Double?
    let thumb: String?
    let stickers: [StorySticker]

    enum CodingKeys: String, CodingKey { case bg, font, size, cx, cy, frame, zoom, tx, ty, thumb, stickers }

    init(from decoder: Decoder) throws {
        let c = try? decoder.container(keyedBy: CodingKeys.self)
        bg = try? c?.decodeIfPresent(String.self, forKey: .bg)
        font = try? c?.decodeIfPresent(String.self, forKey: .font)
        size = try? c?.decodeIfPresent(Double.self, forKey: .size)
        cx = try? c?.decodeIfPresent(Double.self, forKey: .cx)
        cy = try? c?.decodeIfPresent(Double.self, forKey: .cy)
        frame = try? c?.decodeIfPresent(String.self, forKey: .frame)
        zoom = try? c?.decodeIfPresent(Double.self, forKey: .zoom)
        tx = try? c?.decodeIfPresent(Double.self, forKey: .tx)
        ty = try? c?.decodeIfPresent(Double.self, forKey: .ty)
        thumb = try? c?.decodeIfPresent(String.self, forKey: .thumb)
        let found = try? c?.decodeIfPresent([StorySticker].self, forKey: .stickers)
        stickers = found ?? []
    }

    /// Whether the editor laid this out at all (`storyHasLayout`).
    var isEmpty: Bool {
        bg == nil && font == nil && size == nil && cx == nil && cy == nil && frame == nil && zoom == nil
            && tx == nil && ty == nil && thumb == nil && stickers.isEmpty
    }
}

/// A sticker on a story: an emoji or a word, where it sits, its scale and turn.
struct StorySticker: Decodable, Hashable {
    let k: String
    let x: Double
    let y: Double
    let s: Double
    let r: Double

    enum CodingKeys: String, CodingKey { case k, x, y, s, r }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        k = (try? c.decodeIfPresent(String.self, forKey: .k)) ?? ""
        x = (try? c.decodeIfPresent(Double.self, forKey: .x)) ?? 0.5
        y = (try? c.decodeIfPresent(Double.self, forKey: .y)) ?? 0.5
        s = (try? c.decodeIfPresent(Double.self, forKey: .s)) ?? 1
        r = (try? c.decodeIfPresent(Double.self, forKey: .r)) ?? 0
    }
}

/// Somebody @mentioned on a story, and where the tag sits.
struct StoryTag: Decodable, Hashable {
    let handle: String
    let name: String
    let x: Double
    let y: Double

    enum CodingKeys: String, CodingKey { case handle, name, x, y }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        handle = (try? c.decodeIfPresent(String.self, forKey: .handle)) ?? ""
        name = (try? c.decodeIfPresent(String.self, forKey: .name)) ?? ""
        x = (try? c.decodeIfPresent(Double.self, forKey: .x)) ?? 0.5
        y = (try? c.decodeIfPresent(Double.self, forKey: .y)) ?? 0.3
    }
}

/// `/api/waitlist/me`, the two fields an invite needs.
/// `/api/waitlist/me`: everything the web's status page (`/waitlist/me`)
/// draws (decision 2) - the place, the line, the referral link, Your FAM.
struct WaitlistMe: Decodable {
    let waitlist: Bool?
    let referralLink: String?
    let status: String?
    let admin: Bool?
    let name: String?
    let place: Int?
    let total: Int?
    let cutoff: Int?
    let placesUntil: Int?
    let inNextBatch: Bool?
    let referralCode: String?
    let invites: Int?
    let fam: [WaitlistFamPerson]?
    let details: WaitlistDetails?
}

/// One of "Your FAM" on the status page: a friend, or somebody who joined
/// with this account's link.
struct WaitlistFamPerson: Decodable, Hashable {
    let name: String?
    let handle: String?
    let avatar: String?
    let initials: String?
    let invited: Bool?
    let waitlisted: Bool?
}

/// The status page's own details: `has_password` false for an email-only
/// join (§296), which is shown "Set a password so you can sign back in"
/// (§571) and whose sign-out is warned about first.
struct WaitlistDetails: Decodable, Hashable {
    let email: String?
    let hasPassword: Bool?
}

/// `/api/waitlist/people`: the status page's friend search.
struct WaitlistPeople: Decodable {
    let people: [WaitlistPerson]
}

struct WaitlistPerson: Decodable, Hashable, Identifiable {
    let userId: String
    let name: String
    let handle: String
    let avatar: String
    let initials: String
    let waitlisted: Bool
    let added: Bool
    let friend: Bool
    var id: String { userId }
}

/// `/api/waitlist/add`.
struct WaitlistAdded: Decodable {
    let ok: Bool
    let friend: Bool?
}

/// `/api/client-status` (`client_versions.status_for`): what the server
/// makes of this build - asked at launch (IOS_APP.md: 1.0 must).
struct ClientStatus: Decodable {
    let status: String
    let supported: Bool
    let updateRecommended: Bool
    let updateRequired: Bool
    let message: String
}

/// `X-FAM-Client-Status` on any answer, and a 426 (§172, §208): the app is
/// told on the main queue - `deprecated` to say once that a newer FAM is
/// out, `retired` (or the 426) to raise the update screen with the server's
/// sentence and its `update_url` (APP_STORE_URL).
enum ClientStatusWatch {
    /// Set by the app: the status ("deprecated" / "retired"), the 426's
    /// sentence and its store link ("" when the answer had none).
    static var handler: ((String, String, String) -> Void)?

    static func seen(_ http: HTTPURLResponse, _ data: Data) {
        let header = http.value(forHTTPHeaderField: "X-FAM-Client-Status") ?? ""
        let retired = http.statusCode == 426 || header == "retired"
        guard retired || header == "deprecated" else { return }
        var words = ""
        var link = ""
        if http.statusCode == 426,
           let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            words = body["error"] as? String ?? ""
            link = body["update_url"] as? String ?? ""
        }
        let status = retired ? "retired" : "deprecated"
        DispatchQueue.main.async { ClientStatusWatch.handler?(status, words, link) }
    }
}

/// The Terms block of `/api/consent` (§228, `consent.describe_terms`).
struct TermsState: Decodable, Equatable {
    let version: Int
    let accepted: Bool
    let acceptedVersion: Int?
}

/// `/api/consent`, read for its `terms` block only (FAMSafety reads `ai`).
struct ConsentTermsPage: Decodable {
    let terms: TermsState
}

/// `/api/myfam/catalog` and `/api/myfam/search`.
struct CatalogPage: Decodable { let episodes: [CatalogEpisode] }

struct CatalogEpisode: Decodable, Hashable {
    let query: String
    let title: String
    let minutes: Double
    let plays: Int?
    let sourcedAgeSeconds: Double?
    let explicit: Bool?
    let letter: String?
    let thumb: String?
}

/// One row of DailyFAM: "Made for you", "Trending", …
struct Rail: Decodable, Identifiable, Equatable {
    let key: String
    let title: String
    var topics: [Tile]
    /// What an empty rail says, in the server's words (`_empty_reason`).
    let emptyReason: String?
    /// The friends rail: does this listener follow anyone at all?
    let hasCircle: Bool?
    var id: String { key }
}

/// A tile: a title and an angle, scripted on tap (`tile-title-angle`).
struct Tile: Decodable, Identifiable, Hashable {
    let id: String
    let title: String
    let subtitle: String
    let angle: String
    let query: String
    let thumb: String
    /// The subject printed on the picture ("TECH"), `thumb_facet`.
    let thumbFacet: String
    /// Written already: plays at once ("ready").
    let cached: Bool
    /// A game's line ("Eagles 21 - 17 Cowboys, Q3") and how live it is.
    let liveLine: String
    let liveStatus: String
    let liveAsOf: Double
    let friendsListened: Int
    /// Indexes into the page's `friend_faces`.
    let friendsFaces: [Int]
    /// The subject tags; the first labels a card with no `thumb_facet`.
    let tags: [String]
    /// The drawing a card with no picture shows (`seedThumbSVG`).
    let icon: String

    /// The card's one line under the title: the angle, else the subtitle
    /// (`seedHook` on the web).
    var hook: String { angle.isEmpty ? subtitle : angle }

    enum CodingKeys: String, CodingKey {
        case id, title, subtitle, angle, query, thumb, thumbFacet, cached
        case liveLine, liveStatus, liveAsOf, friendsListened, friendsFaces, tags, icon
    }

    /// Lenient: a tile from any rail decodes even if a field is missing,
    /// so one shape change never empties a screen.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? ""
        subtitle = try c.decodeIfPresent(String.self, forKey: .subtitle) ?? ""
        angle = try c.decodeIfPresent(String.self, forKey: .angle) ?? ""
        query = try c.decodeIfPresent(String.self, forKey: .query) ?? title
        thumb = try c.decodeIfPresent(String.self, forKey: .thumb) ?? ""
        thumbFacet = try c.decodeIfPresent(String.self, forKey: .thumbFacet) ?? ""
        cached = try c.decodeIfPresent(Bool.self, forKey: .cached) ?? false
        liveLine = try c.decodeIfPresent(String.self, forKey: .liveLine) ?? ""
        liveStatus = try c.decodeIfPresent(String.self, forKey: .liveStatus) ?? ""
        liveAsOf = try c.decodeIfPresent(Double.self, forKey: .liveAsOf) ?? 0
        friendsListened = try c.decodeIfPresent(Int.self, forKey: .friendsListened) ?? 0
        friendsFaces = try c.decodeIfPresent([Int].self, forKey: .friendsFaces) ?? []
        tags = (try? c.decodeIfPresent([String].self, forKey: .tags)) ?? []
        icon = (try? c.decodeIfPresent(String.self, forKey: .icon)) ?? ""
    }

    /// The word on the picture (`seedTagText`): the picture's own facet,
    /// else the first tag.
    var tagText: String { thumbFacet.isEmpty ? (tags.first ?? "") : thumbFacet }
}

/// `/api/searches/trending`: the five most searched questions of the last
/// two hours whose episodes are still current (`trending-searches`).
struct TrendingSearch: Decodable, Hashable {
    let query: String
    let title: String
    let minutes: Double
    /// The question as the speller would have sent it, for a chip with no
    /// title (10.1 #4); `query` is still what is asked.
    let spelled: String?

    /// The episode's own title, else the question made a title
    /// (`deriveTitleFromPrompt(spelled || query)`).
    var label: String {
        if !title.isEmpty { return title }
        let words = (spelled ?? "").isEmpty ? query : (spelled ?? query)
        return SearchTitle.from(words)
    }
}

struct TrendingSearches: Decodable { let searches: [TrendingSearch] }

struct Samples: Decodable { let episodes: [Tile] }

/// `/api/progress`: four marks the pipeline records when each step really
/// finishes, never a timer. `known` false means this worker is not making
/// the episode; the screen then walks the steps when the audio arrives.
struct Progress: Decodable {
    let known: Bool
    let cached: Bool
    let steps: ProgressSteps
    /// How long the voice may take once the script is with it (the
    /// server's REMOTE_VOICE_TIMEOUT), for the give-up time (§497).
    let voiceWait: Double?
}

/// The three things the web reads from `/api/health` for the loading
/// screen's note (`genStatusNote`): demo mode (no key, the sample script),
/// zero spend (staging) and a rejected key.
struct ServerHealth: Decodable {
    let mode: String?
    let environment: ServerHealthEnvironment?
    let credentials: ServerHealthCredentials?

    /// The web's three sentences, in its order; "" for a server that writes.
    var loadingNote: String {
        if mode == "demo" && environment?.zeroSpend == true {
            return "Staging: playing the built-in sample script\u{2026}"
        }
        if mode == "demo" { return "Playing the built-in sample script\u{2026}" }
        if credentials?.state == "rejected" { return "The server's API key was rejected\u{2026}" }
        return ""
    }
}

struct ServerHealthEnvironment: Decodable {
    let name: String?
    let zeroSpend: Bool?
}

struct ServerHealthCredentials: Decodable {
    let state: String?
}

struct ProgressSteps: Decodable {
    let contextualized: Bool
    let retrieved: Bool
    let verified: Bool
    let written: Bool

    var marks: [Bool] { [contextualized, retrieved, verified, written] }
}

struct OK: Decodable { let ok: Bool }

/// `/api/account`: how this account signs in.
struct AccountInfo: Decodable {
    let identities: [Identity]
    let hasPassword: Bool
    /// Settings' rows: "Signed in as", the first run's name, and the
    /// activation answers (§411) - theirs, shown to them only.
    let email: String?
    let phone: String?
    let displayName: String?
    let birthDate: String?
    let gender: String?
    let counted: Bool?
}

struct Identity: Decodable, Hashable {
    let provider: String
    let email: String?
}

// MARK: - The player's side channels

/// What makes an episode an episode: its question, length and the topic it
/// followed up. Every side channel is asked with exactly these.
struct EpisodeKey: Equatable, Hashable {
    var query: String
    var minutes: Double
    var context: String = ""
    /// The reading the listener chose for a name that means two things
    /// (§426), already joined to `query` as "(reading)"; "" when none.
    var reading: String = ""
}

/// The body of a refused `/api/audio` (`http_error`, §426): the server's
/// sentence, who refused (`refused_by`: "quota", "account"), the allowance's
/// verdict, and - with 300 - the readings of a name to choose between.
struct AudioRefusal: Decodable {
    let error: String?
    let detail: String?
    let refusedBy: String?
    let quota: QuotaVerdict?
    let readings: [String]?
    let likeliest: String?
}

/// A replay answered with a link to its kept audio (`_linked_audio`,
/// §252, `X-FAM-Audio-Link: 1`): where the Ogg Opus file is, and its true
/// length in samples at `X-Sample-Rate`.
struct AudioLinkBody: Decodable {
    let link: String
    let frames: Int?
}

/// One bank topic's id and tags (`Topic.as_dict`), for Recommended topics.
struct BankTopic: Decodable {
    let id: String
    let tags: [String]?
}

struct BankPage: Decodable { let topics: [BankTopic] }

/// `sharing.landing_payload`: what a shared link plays.
struct SharePayload: Decodable {
    let id: String
    let title: String
    let question: String
    let minutes: Double
}

/// `quotas.Verdict.as_dict`: the limit screen's words and numbers.
struct QuotaVerdict: Decodable, Equatable {
    let title: String?
    let message: String?
    let limit: Int?
    let used: Int?
    let unlimited: Bool?
    let resetsAt: Double?
}

/// `/api/next`.
struct NextThread: Decodable {
    let thread: String?
    let title: String?
    let titleFinal: Bool?
    let sourcedAt: Double?
    let explicit: Bool?
}

/// `/api/sources`.
struct Sources: Decodable {
    let items: [SourceItem]
    let retrievers: [String]
    let known: Bool
}

struct SourceItem: Decodable, Hashable {
    let label: String
    let kind: String?
    let tier: String?
    let at: String?
    let title: String?
    let url: String?
}

/// `/api/transcript`.
struct Transcript: Decodable {
    let sentences: [String]
    let done: Bool
    let starts: [Double]?
}

/// `/api/episode/card`.
struct EpisodeCard: Decodable {
    let thumb: String?
    /// The picture is borrowed from the episode's category, not its own
    /// (`fallback`): a later answer may replace it (`askPlayerCard`).
    let fallback: Bool?
    let placed: Bool?
    let searcher: String?
}

struct NextUpPage: Decodable { let topics: [Tile] }

struct SavedState: Decodable { let saved: Bool }

/// `POST /api/share`: the link, and per-target wording.
struct SharedLink: Decodable {
    let url: String
    let targets: [String: ShareTarget]?
    /// The story card (`/api/share/card?share=`), absolute where the server
    /// knows its own address, else a path on it.
    let card: String?
    /// False when the server has no public address: the link only works
    /// on its own network.
    let `public`: Bool?
}

/// One destination (`sharing._rendered`): its label, the words, and where
/// to send them - `destination` empty when the platform takes an image or
/// the server has no public address.
struct ShareTarget: Decodable {
    let text: String?
    let label: String?
    let kind: String?
    let needsImage: Bool?
    let subject: String?
    let destination: String?
    /// Instagram's or Snapchat's own hand-off (`sharing.story_handoffs`),
    /// on a story destination when the server has the platform's id; nil
    /// keeps iOS's share sheet.
    let handoff: StoryHandoff?
}

/// A story destination's native hand-off: the card goes on the pasteboard
/// under the platform's keys and `url` opens its app (`StoryHandoff.swift`).
struct StoryHandoff: Decodable {
    let platform: String
    let url: String
    /// Instagram: the Facebook App ID (already in `url`).
    let appId: String?
    /// Snapchat: the Snap Kit client id.
    let clientId: String?
}

/// An answer whose body nothing reads.
struct Ignored: Decodable {}

/// `/api/surveys/offer` (`surveys.definition`): the survey to show, or null.
struct SurveyOffer: Decodable {
    let survey: Survey?
}

/// One survey as the client draws it (`surveys.SURVEYS`).
struct Survey: Decodable, Identifiable, Equatable {
    let id: String
    let title: String
    let intro: String
    let questions: [SurveyQuestion]
}

/// A question: `single`, `multi`, `scale` (1-10) or `text`.
struct SurveyQuestion: Decodable, Identifiable, Equatable {
    let id: String
    let kind: String
    let text: String
    let options: [SurveyOption]?
    /// Shown only when another question's answer is one of these.
    let showIf: SurveyShowIf?
    /// An option whose own box asks a question ("name" -> "Which name?").
    /// Its keys are option ids with no underscore, so the decoder's
    /// snake-case conversion leaves them as they are.
    let detail: [String: String]?
    /// An "Other" chip with a box.
    let other: Bool?
    let min: Int?
    let max: Int?
    let low: String?
    let high: String?
    let placeholder: String?
}

struct SurveyOption: Decodable, Identifiable, Equatable {
    let id: String
    let label: String
}

struct SurveyShowIf: Decodable, Equatable {
    let question: String
    let `in`: [String]
}


enum FAMError: LocalizedError, Equatable {
    case waitlisted(String)
    case consentRequired
    case signedOut(String)
    case refused(status: Int, message: String)
    case unreadable

    var errorDescription: String? {
        switch self {
        case .waitlisted(let text):
            return text.isEmpty ? "FAM is open to members only while the waitlist is running." : text
        case .consentRequired:
            return "FAM needs your yes before your question goes to the AI."
        case .signedOut(let text):
            return text.isEmpty ? "Sign in to keep going." : text
        case let .refused(status, message):
            return message.isEmpty ? "The server answered \(status)." : message
        case .unreadable:
            return "The server's answer could not be read."
        }
    }
}

// MARK: - searchFAM: the speller, attachments (`autocorrect-at-send`, `op-attachments`)

extension FAMAPI {
    /// FAM's own speller behind the keyboard (`/api/spell`, §142): `nil` for
    /// every word it is not sure about. No account needed.
    func spell(_ words: [String], first: [Bool]) async throws -> SpellAnswer {
        try await send("POST", "api/v1/spell", body: ["words": Array(words.prefix(40)),
                                                     "first": Array(first.prefix(40))],
                       as: SpellAnswer.self)
    }

    /// Extract a document, photo or link once, when it is added (never on
    /// the generation path). `data` is the file's base64; a link sends `url`.
    func attach(kind: String, name: String, data: String = "", url: String = "") async throws -> AttachedFile {
        var body: [String: Any] = ["kind": kind, "name": String(name.prefix(300))]
        if !data.isEmpty { body["data"] = data }
        if !url.isEmpty { body["url"] = String(url.prefix(2000)) }
        return try await send("POST", "api/v1/attach", body: body, as: AttachedFile.self)
    }

    /// × on a chip: fire and forget (the server expires it anyway).
    func detach(_ id: String) async {
        _ = try? await sendQuery("DELETE", "api/v1/attach", [URLQueryItem(name: "id", value: id)],
                                 as: OK.self)
    }
}

/// `POST /api/spell`: one correction per word asked, `nil` to leave it.
struct SpellAnswer: Decodable {
    let corrections: [String?]
    let available: Bool?
}

/// `POST /api/attach` (`Attachment.as_dict`): never the payload.
struct AttachedFile: Decodable {
    let id: String
    let kind: String
    let name: String
    let chars: Int?
    let url: String?
    let preview: String?
}

// MARK: - Keychain

/// The session token, kept where iOS keeps passwords. A cookie jar is cleared
/// under conditions the app does not control; the Keychain is not.
enum Keychain {
    private static let service = "net.familiarize.fam"

    static func read(_ key: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var found: AnyObject?
        guard SecItemCopyMatching(query as CFDictionary, &found) == errSecSuccess,
              let data = found as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func write(_ key: String, _ value: String) {
        delete(key)
        let item: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
            kSecValueData as String: Data(value.utf8),
            // Readable while the phone is locked, so a locked phone that is
            // playing can still ask for the next episode.
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]
        SecItemAdd(item as CFDictionary, nil)
    }

    static func delete(_ key: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
        ]
        SecItemDelete(query as CFDictionary)
    }
}

// MARK: - myFAM: mixes (`/api/mixes`, `mixes.py`)
//
// The listener sees this surface as "myFAM"; code and routes keep the old
// name, DailyFAM mixes (`names-swapped`). A mix is topic ids, never audio
// (`op-dailyfam-mixes`); its episodes are written each morning.

extension FAMAPI {
    /// Your mixes, joined ones included (`joint=1`; without it they are left out).
    func mixes() async throws -> [Mix] {
        try await get("api/v1/mixes", [URLQueryItem(name: "joint", value: "1")], as: MixesPage.self).mixes
    }

    /// Made by naming it, empty, public unless said otherwise (`mix-made-by-naming`).
    func createMix(name: String, topics: [Any] = [], cover: String = "") async throws -> Mix {
        var body: [String: Any] = ["name": name, "topic_ids": topics]
        if !cover.isEmpty { body["cover"] = cover }
        return try await send("POST", "api/v1/mixes", body: body, as: Mix.self)
    }

    /// Any of name, public, cover, listen_at/listen_tz, add, remove, topic_ids.
    func updateMix(_ id: String, _ body: [String: Any]) async throws -> Mix {
        try await send("PATCH", "api/v1/mixes/" + id, body: body, as: Mix.self)
    }

    func deleteMix(_ id: String) async throws {
        _ = try await send("DELETE", "api/v1/mixes/" + id, as: Ignored.self)
    }

    /// Which of this listener's mixes this morning's edition wrote and
    /// nobody has played since, and the edition it is (§550, the dot on the
    /// myFAM tab). A guest gets an empty answer, never a 401.
    func freshMixes() async throws -> FreshMixes {
        try await get("api/v1/mixes/fresh", [], as: FreshMixes.self)
    }

    /// Opened or played: keeps the mix out of dormancy (`mix-dormancy`).
    func noteMixOpen(_ id: String, played: Bool) async {
        _ = try? await send("POST", "api/v1/mixes/\(id)/open", body: ["played": played], as: Ignored.self)
    }

    func recommendedTopics() async throws -> RecommendedTopics {
        try await get("api/v1/mixes/recommended", [], as: RecommendedTopics.self)
    }

    /// The subjects a mix can follow (`/api/preferences` → catalogue).
    func catalogue() async throws -> Catalogue {
        try await get("api/v1/preferences", [], as: Catalogue.self)
    }

    func inviteToMix(_ id: String) async throws -> MixInvite {
        try await send("POST", "api/v1/mixes/\(id)/invite", as: MixInvite.self)
    }

    /// "Make it yours alone": the invite link stops working, members go.
    func stopJointMix(_ id: String) async throws -> Mix {
        try await send("DELETE", "api/v1/mixes/\(id)/invite", as: Mix.self)
    }

    func joinMix(token: String) async throws -> Mix {
        try await send("POST", "api/v1/mixes/join", body: ["token": token], as: Mix.self)
    }

    /// The shared bank with each topic's tags (`/api/topics?ranked=1`):
    /// an older bank episode in a mix ranks Recommended topics by its tags
    /// (`itemTags`, `bankById`), as the web's `topicBank` does.
    func topicBank() async throws -> [BankTopic] {
        try await get("api/v1/topics", [URLQueryItem(name: "ranked", value: "1")], as: BankPage.self).topics
    }

    /// One share, for a universal link to `/s/<id>` (decision 5): the same
    /// payload the landing page is drawn from (`/api/share/{id}`).
    func sharedEpisode(_ id: String) async throws -> SharePayload {
        try await get("api/v1/share/" + id, [], as: SharePayload.self)
    }

    /// Somebody looked at a shared episode (the landing page's open count).
    func shareOpened(_ id: String) async {
        _ = try? await send("POST", "api/v1/share/\(id)/open", as: Ignored.self)
    }

    func leaveMix(_ id: String) async throws {
        _ = try await send("POST", "api/v1/mixes/\(id)/leave", as: Ignored.self)
    }

    func publicMixes(_ words: String) async throws -> [Mix] {
        try await get("api/v1/mixes/public", [URLQueryItem(name: "q", value: words)], as: MixesPage.self).mixes
    }

    func publicMix(_ id: String) async throws -> Mix {
        try await get("api/v1/mixes/public/" + id, [], as: Mix.self)
    }

    /// (+) on somebody else's public mix: a private copy, not a link.
    func addPublicMix(_ id: String) async throws -> Mix {
        try await send("POST", "api/v1/mixes/\(id)/add", as: Mix.self)
    }

    func removePublicMix(_ id: String) async throws {
        _ = try await send("DELETE", "api/v1/mixes/\(id)/add", as: Ignored.self)
    }

    func shareMix(_ id: String) async throws -> MixShare {
        try await send("POST", "api/v1/mixes/\(id)/share", as: MixShare.self)
    }

    func pushStatus() async throws -> PushStatus {
        try await get("api/v1/push", [], as: PushStatus.self)
    }

    /// Changes what this listener is told and when (§562, `savePushPrefs`):
    /// any of `daily`, `daily_at` ("HH:MM", or "" for the learned time),
    /// `breaking`, `finals`, and `tz`. Answers with the choices as kept.
    func savePushPrefs(_ changes: [String: Any]) async throws -> PushPrefsAnswer {
        try await send("POST", "api/v1/push/prefs", body: changes, as: PushPrefsAnswer.self)
    }

    /// Your own profile (`/api/profile`): the name, handle and picture a
    /// solo mix's people line shows, and the interests the local
    /// recommendation order starts from (`profileNow`).
    func profile() async throws -> ProfileSummary {
        try await get("api/v1/profile", [], as: ProfileSummary.self)
    }

    /// One interest's episodes (`/api/interest`, the Topic screen behind
    /// every interest chip): `latest` or `friends`, a page at `offset`.
    /// Generates nothing.
    func interest(id: String, label: String, filter: String, offset: Int) async throws -> InterestPage {
        try await get("api/v1/interest", [URLQueryItem(name: "id", value: id),
                                          URLQueryItem(name: "label", value: label),
                                          URLQueryItem(name: "filter", value: filter),
                                          URLQueryItem(name: "offset", value: String(offset))],
                      as: InterestPage.self)
    }

    /// A story card's SVG (`sharing.story_card`), from the address the share
    /// answer gave. The session goes only to this server, never elsewhere.
    func storyCardSVG(_ card: String) async throws -> String {
        guard let url = URL(string: card, relativeTo: base)?.absoluteURL else { throw FAMError.unreadable }
        var req = URLRequest(url: url)
        req.httpMethod = "GET"
        req.setValue(Self.client, forHTTPHeaderField: "X-FAM-Client")
        req.setValue(Self.listenerZone, forHTTPHeaderField: "X-FAM-TZ")
        if url.host == base.host, let token {
            req.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        }
        let (data, response) = try await readPaced(req)
        try Self.check(response, data)
        guard let text = String(data: data, encoding: .utf8), text.contains("<svg") else {
            throw FAMError.unreadable
        }
        return text
    }

    /// What an episode is about, as a mix topic (`/api/episode/topic`).
    func episodeTopic(query: String, title: String) async throws -> EpisodeTopic {
        try await get("api/v1/episode/topic", [URLQueryItem(name: "q", value: query),
                                                URLQueryItem(name: "title", value: title)],
                      as: EpisodeTopic.self)
    }
}

struct MixesPage: Decodable {
    let mixes: [Mix]
}

/// `GET /api/mixes/fresh` (§550): the mixes new this morning and the
/// edition's id. Lenient: anything missing is no dot.
struct FreshMixes: Decodable {
    let fresh: [String]
    let edition: String

    enum CodingKeys: String, CodingKey {
        case fresh, edition
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        fresh = (try? c.decode([String].self, forKey: .fresh)) ?? []
        edition = (try? c.decode(String.self, forKey: .edition)) ?? ""
    }
}

/// The parts of `/api/profile` myFAM reads. Lenient: everything optional.
struct ProfileSummary: Decodable {
    let name: String?
    let handle: String?
    let avatar: String?
    /// The interests pills (`profile_interests`): a subject (`kind`
    /// "topic") or a facet.
    let interestsShown: [ProfileInterest]?
    /// Your own vibes, newest first (a chat's (+) offers them).
    let echoes: [ProfileEcho]?
}

struct ProfileInterest: Decodable {
    let id: String
    let label: String?
    let kind: String?
}

/// A mix as its owner, a member or a stranger sees it (`_mix_out`,
/// `public_dict`, `_public_mix`). Lenient: a field one view leaves out
/// decodes to its empty value.
struct Mix: Decodable, Identifiable, Equatable {
    let id: String
    let name: String
    let items: [MixItem]
    let `public`: Bool
    let cover: String
    let sourceId: String
    /// "HH:MM", or "" - the owner's view only (`mix-listen-time`).
    let listenAt: String
    /// "owner" or "member".
    let role: String
    let joint: Bool
    let people: [MixPerson]
    /// A copy's original owner.
    let from: MixOwner?
    /// Somebody else's public mix: who made it, whether it is yours,
    /// whether you have added it.
    let owner: MixOwner?
    let mine: Bool
    let added: Bool
    /// What changed since this listener last heard the mix (§545, R5 on
    /// the card): nil when nothing did, or from a server before §545.
    let signal: MixSignal?

    var isOwner: Bool { role != "member" }

    enum CodingKeys: String, CodingKey {
        case id, name, items, `public`, cover, sourceId, listenAt, role, joint, people, from, owner, mine, added
        case signal
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        name = (try? c.decode(String.self, forKey: .name)) ?? ""
        items = (try? c.decode([MixItem].self, forKey: .items)) ?? []
        `public` = (try? c.decode(Bool.self, forKey: .public)) ?? false
        cover = (try? c.decode(String.self, forKey: .cover)) ?? ""
        sourceId = (try? c.decode(String.self, forKey: .sourceId)) ?? ""
        listenAt = (try? c.decode(String.self, forKey: .listenAt)) ?? ""
        role = (try? c.decode(String.self, forKey: .role)) ?? "owner"
        joint = (try? c.decode(Bool.self, forKey: .joint)) ?? false
        people = (try? c.decode([MixPerson].self, forKey: .people)) ?? []
        from = try? c.decode(MixOwner.self, forKey: .from)
        owner = try? c.decode(MixOwner.self, forKey: .owner)
        mine = (try? c.decode(Bool.self, forKey: .mine)) ?? false
        added = (try? c.decode(Bool.self, forKey: .added)) ?? false
        signal = try? c.decode(MixSignal.self, forKey: .signal)
    }
}

/// One topic in a mix (`MixItem.as_dict`): a followed subject (`f:nfl`,
/// `f:nfl~Eagles`), a typed one (`q:...`) or an old bank id.
struct MixItem: Decodable, Identifiable, Equatable {
    let id: String
    let title: String
    let query: String
    let custom: Bool
    let subtitle: String
    /// "{date}" is filled with today's date (`mixes.daily_prompt`).
    let dailyPrompt: String
    /// Already dated for today's edition; wins when both are sent (§143).
    let prompt: String
    let minutes: Double
    let follow: Bool
    /// An index into the mix's `people` (joint mixes).
    let addedBy: Int?
    /// The subject's catalogue icon (`catIcon`), drawn when no picture is
    /// approved; "leaf" falls back to the news icon, as on the web.
    let icon: String
    /// The picture the player would draw for this item (§545,
    /// `_mix_pictures`): an `/api/...` path, or "" for none.
    let picture: String
    /// Trending / Major update since this listener last heard it (§545).
    let signal: MixSignal?
    /// Q5's two bubbles: what was last heard, and what is true today.
    let since: MixSince?

    enum CodingKeys: String, CodingKey {
        case id, title, query, custom, subtitle, dailyPrompt, prompt, minutes, follow, addedBy
        case icon, picture, signal, since
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = (try? c.decode(String.self, forKey: .title)) ?? ""
        query = (try? c.decode(String.self, forKey: .query)) ?? ""
        custom = (try? c.decode(Bool.self, forKey: .custom)) ?? false
        subtitle = (try? c.decode(String.self, forKey: .subtitle)) ?? ""
        dailyPrompt = (try? c.decode(String.self, forKey: .dailyPrompt)) ?? ""
        prompt = (try? c.decode(String.self, forKey: .prompt)) ?? ""
        minutes = (try? c.decode(Double.self, forKey: .minutes)) ?? FAMAPI.browseMinutes
        follow = (try? c.decode(Bool.self, forKey: .follow)) ?? false
        addedBy = try? c.decode(Int.self, forKey: .addedBy)
        icon = (try? c.decode(String.self, forKey: .icon)) ?? "leaf"
        picture = (try? c.decode(String.self, forKey: .picture)) ?? ""
        signal = try? c.decode(MixSignal.self, forKey: .signal)
        since = try? c.decode(MixSince.self, forKey: .since)
    }

    /// What the server is asked to keep for this entry in a whole-list
    /// write: the id, or a typed topic's words and title (so it is never
    /// renamed).
    var entry: Any { custom ? ["query": query, "title": title] as [String: Any] : id }
}

/// What changed in a mix since this listener last heard it (§545,
/// `signals.Found.card`): on the mix, the strongest of its items' (R5) with
/// the topic and the story's picture; on an item, its kind, label and
/// headline. Lenient: a field one place leaves out decodes to "".
struct MixSignal: Decodable, Equatable {
    /// "trending" or "update".
    let kind: String
    /// "Trending" or "Major update", as the server words it.
    let label: String
    let itemId: String
    let topic: String
    let headline: String
    /// The story's picture (the player's own picker), or "".
    let picture: String

    /// Anything but "update" is drawn as Trending (`mixSignalCardHTML`).
    var isUpdate: Bool { kind == "update" }

    enum CodingKeys: String, CodingKey {
        case kind, label, itemId, topic, headline, picture
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        kind = (try? c.decode(String.self, forKey: .kind)) ?? ""
        label = (try? c.decode(String.self, forKey: .label)) ?? ""
        itemId = (try? c.decode(String.self, forKey: .itemId)) ?? ""
        topic = (try? c.decode(String.self, forKey: .topic)) ?? ""
        headline = (try? c.decode(String.self, forKey: .headline)) ?? ""
        picture = (try? c.decode(String.self, forKey: .picture)) ?? ""
    }
}

/// Q5's bubbles (`signals.bubbles`): `then` is nil when the listener has
/// never heard this topic - one bubble, never an invented "before".
struct MixSince: Decodable, Equatable {
    let then: MixSinceSide?
    let now: MixSinceSide?

    enum CodingKeys: String, CodingKey {
        case then, now
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        then = try? c.decode(MixSinceSide.self, forKey: .then)
        now = try? c.decode(MixSinceSide.self, forKey: .now)
    }
}

/// One bubble: the day it was heard ("Mon"; "" for today's) and its title.
struct MixSinceSide: Decodable, Equatable {
    let day: String
    let title: String

    enum CodingKeys: String, CodingKey {
        case day, title
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        day = (try? c.decode(String.self, forKey: .day)) ?? ""
        title = (try? c.decode(String.self, forKey: .title)) ?? ""
    }
}

struct MixPerson: Decodable, Equatable, Hashable {
    let name: String?
    let handle: String?
    let avatar: String?
    let owner: Bool?
    let you: Bool?
}

struct MixOwner: Decodable, Equatable {
    let name: String?
    let handle: String?

    /// `@handle`, else the name, else "another listener" (`ownerLabel`).
    var label: String {
        if let handle, !handle.isEmpty { return "@" + handle }
        if let name, !name.isEmpty { return name }
        return "another listener"
    }
}

struct RecommendedTopics: Decodable {
    /// `f:<id>` in the order to offer them.
    let order: [String]
    let personalised: Bool
}

struct Catalogue: Decodable {
    let catalogue: [Subject]
    let tagParent: [String: String]?
    let tagLabels: [String: String]?
}

/// A subject a mix can follow; `focus_picks` are the teams and companies
/// inside it ("eagles" finds NFL · Eagles).
struct Subject: Decodable, Identifiable, Hashable {
    let id: String
    let label: String
    let icon: String?
    let tags: [String]?
    let focusPicks: [String]?
}

struct MixInvite: Decodable {
    let url: String
    let name: String
}

struct MixShare: Decodable {
    let url: String
    let `public`: Bool
    let targets: [String: ShareTarget]?
    /// The mix's story card (`/api/mixes/{id}/card`).
    let card: String?
}

struct PushStatus: Decodable {
    let available: Bool
    let reason: String?
    let subscribed: Bool?
    /// The listener's notification choices (§562), for an account only;
    /// nil from a server before them.
    let prefs: PushPrefs?
}

/// `prefs` on `/api/push` and `/api/push/prefs` (`app._push_choices`): the
/// three switches, the chosen time ("" = learned), and when the daily
/// reminder goes (`daily_time`, "HH:MM") and why (`daily_source`: chosen,
/// mix, learned or default). Every field decoded tolerantly.
struct PushPrefs: Decodable, Equatable {
    let daily: Bool
    let dailyAt: String
    let breaking: Bool
    let finals: Bool
    let dailyTime: String
    let dailySource: String

    private enum CodingKeys: String, CodingKey {
        case daily, dailyAt, breaking, finals, dailyTime, dailySource
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        daily = (try? c.decode(Bool.self, forKey: .daily)) ?? true
        dailyAt = (try? c.decode(String.self, forKey: .dailyAt)) ?? ""
        breaking = (try? c.decode(Bool.self, forKey: .breaking)) ?? true
        finals = (try? c.decode(Bool.self, forKey: .finals)) ?? true
        dailyTime = (try? c.decode(String.self, forKey: .dailyTime)) ?? ""
        dailySource = (try? c.decode(String.self, forKey: .dailySource)) ?? ""
    }
}

/// `POST /api/push/prefs`.
struct PushPrefsAnswer: Decodable {
    let ok: Bool?
    let prefs: PushPrefs?
}

/// `GET /api/interest`: the cards, whether there are more, and - for an
/// empty page - the server's sentence why.
struct InterestPage: Decodable {
    let label: String?
    let episodes: [InterestCard]
    let more: Bool?
    let reason: String?
}

/// One card on the Topic screen: a question and its original length; a
/// friend's vibe says who.
struct InterestCard: Decodable, Hashable {
    let query: String
    let title: String?
    let minutes: Double?
    let source: String?
    let vibedBy: InterestVibedBy?
}

struct InterestVibedBy: Decodable, Hashable {
    let name: String?
    let handle: String?
    let avatar: String?
}

/// A catalogue subject (`id` like `f:nfl~Eagles`), or a typed topic
/// (`id` empty, `query` its words).
struct EpisodeTopic: Decodable {
    let id: String
    let label: String
    let title: String
    let query: String?
}

// MARK: - Categories on the myFAM tab (§229, §500, `your-categories`)

extension FAMAPI {
    /// The tree's facets, every node, and the ones you follow.
    func categories() async throws -> CategoriesPage {
        try await get("api/v1/categories", [], as: CategoriesPage.self)
    }

    /// Follow or unfollow: the whole followed list, in order.
    func followCategories(_ ids: [String]) async throws {
        _ = try await send("POST", "api/v1/categories/mine", body: ["categories": ids], as: Ignored.self)
    }

    /// Today's cached episodes filed under a node, most played first.
    func categoryEpisodes(_ id: String) async throws -> CategoryEpisodes {
        try await get("api/v1/categories/episodes", [URLQueryItem(name: "id", value: id),
                                                     URLQueryItem(name: "sort", value: "popular")],
                      as: CategoryEpisodes.self)
    }
}

struct CategoriesPage: Decodable {
    let facets: [CategoryFacet]
    let all: [CategoryNode]
    let mine: [CategoryNode]
    /// Signed in: following is kept.
    let saved: Bool
}

struct CategoryFacet: Decodable, Identifiable, Hashable {
    let id: String
    let label: String
    /// The tree's first level under it (§550: the Categories tab's row
    /// names a few of them). Nil from a server before it.
    let children: [CategoryChild]?
}

/// A facet's child on `/api/categories` (`facets[].children`).
struct CategoryChild: Decodable, Hashable {
    let id: String
    let label: String
}

struct CategoryNode: Decodable, Identifiable, Hashable {
    let id: String
    let label: String
    let facet: String?
    let path: [String]?
    /// On `mine` only (§550): the names of the level below, and how many
    /// episodes were made in it today. Nil in `all`.
    let children: [String]?
    let today: Int?
}

struct CategoryEpisodes: Decodable {
    let id: String
    let label: String
    let facet: String?
    let subcategories: [CategorySub]
    let episodes: [CategoryEpisode]
}

struct CategorySub: Decodable, Identifiable, Hashable {
    let id: String
    let label: String
    let count: Int
}

struct CategoryEpisode: Decodable, Identifiable, Hashable {
    let query: String
    let title: String
    let minutes: Double?
    let plays: Int?
    let thumb: String?
    let explicit: Bool?
    let nodeLabel: String?

    var id: String { query + "|" + String(minutes ?? 0) }
}

// MARK: - Messages, friends and profiles (`messages.py`, `social.py`)
//
// `op-social-live`: the message cursor is the server's `head`, a row id,
// never a timestamp, and it belongs to the client; `bootstrap` announces
// nothing. `listener-id-server`: nobody's id is ever sent as "me" - only the
// other person's, and only one the server itself handed out (`/api/friends`,
// `/api/people`). `/api/person` carries no id at all (`op-friend-profile`).

extension FAMAPI {
    /// Every conversation, newest first, and the Messages badge (`renderThreadList`).
    func inbox() async throws -> InboxPage {
        try await get("api/v1/messages", [], as: InboxPage.self)
    }

    /// One conversation (`openThreadWith`), or - with `since` - only what
    /// arrived after that row id (the 2 s poll). Reading marks it read.
    func chat(with: String, since: Int = 0) async throws -> ChatPage {
        var items = [URLQueryItem(name: "with", value: with)]
        if since > 0 { items.append(URLQueryItem(name: "since", value: String(since))) }
        return try await get("api/v1/messages/thread", items, as: ChatPage.self)
    }

    /// Delete a chat - for this listener only (§142).
    func deleteChat(with: String) async throws -> UnreadAnswer {
        try await sendQuery("DELETE", "api/v1/messages/thread",
                            [URLQueryItem(name: "with", value: with)], as: UnreadAnswer.self)
    }

    /// Words, or an episode (`query` set) - with `episode`, the id of the
    /// one the sender heard, so the friend replays exactly that (§288).
    /// `reply` marks a reply to, or a like on, a vibe (§282).
    func sendMessage(to: String, text: String = "", query: String = "", minutes: Double = 0,
                     title: String = "", episode: String = "", reply: Bool = false) async throws -> SentMessage {
        var body: [String: Any] = ["to": to]
        if !text.isEmpty { body["text"] = text }
        if !query.isEmpty {
            body["query"] = query
            body["minutes"] = minutes
            body["title"] = title
            if !episode.isEmpty { body["episode"] = episode }
        }
        if reply { body["reply"] = true }
        return try await send("POST", "api/v1/messages", body: body, as: SentMessage.self)
    }

    /// "I am typing to this person" - held five seconds by the server.
    func noteTyping(to: String) async {
        _ = try? await send("POST", "api/v1/messages/typing", body: ["to": to], as: Ignored.self)
    }

    func startGroup(userIDs: [String], name: String) async throws -> GroupAnswer {
        try await send("POST", "api/v1/messages/groups", body: ["user_ids": userIDs, "name": name],
                       as: GroupAnswer.self)
    }

    func renameGroup(_ gid: String, name: String) async throws -> GroupAnswer {
        try await send("PATCH", "api/v1/messages/groups/\(gid)", body: ["name": name], as: GroupAnswer.self)
    }

    /// A `data:` URL, or "" to take the photo off. The server checks it.
    func setGroupPhoto(_ gid: String, avatar: String) async throws -> GroupAnswer {
        try await send("POST", "api/v1/messages/groups/\(gid)/photo", body: ["avatar": avatar],
                       as: GroupAnswer.self)
    }

    func leaveGroup(_ gid: String) async throws -> UnreadAnswer {
        try await send("DELETE", "api/v1/messages/groups/\(gid)", as: UnreadAnswer.self)
    }

    /// The drop-downs' poll: `bootstrap` first (the cursor, nothing to
    /// announce), then `since` the cursor the client holds.
    func notifications(since: Int, bootstrap: Bool) async throws -> NoticePage {
        let items = bootstrap ? [URLQueryItem(name: "bootstrap", value: "1")]
                              : [URLQueryItem(name: "since", value: String(since))]
        return try await get("api/v1/notifications", items, as: NoticePage.self)
    }

    /// The follow graph (`/api/friends`): friends are derived, never stored.
    func friends() async throws -> FriendsPage {
        try await get("api/v1/friends", [], as: FriendsPage.self)
    }

    /// Opening Friends is what clears "new followers" - never the popup.
    func friendsSeen() async {
        _ = try? await send("POST", "api/v1/friends/seen", as: Ignored.self)
    }

    /// The popup or banner for this follower was shown: once, ever (§142).
    func followerAnnounced(_ userID: String) async {
        _ = try? await send("POST", "api/v1/friends/announced", body: ["user_id": userID], as: Ignored.self)
    }

    /// Follow by the id the graph or a search returned, or by handle (a
    /// friend of a friend has no id on this side).
    func follow(userID: String = "", handle: String = "") async throws {
        var body: [String: Any] = [:]
        if !userID.isEmpty { body["user_id"] = userID } else { body["handle"] = handle }
        _ = try await send("POST", "api/v1/friends/follow", body: body, as: FollowAnswer.self)
    }

    func unfollow(userID: String) async throws {
        _ = try await sendQuery("DELETE", "api/v1/friends/follow",
                                [URLQueryItem(name: "user_id", value: userID)], as: FollowAnswer.self)
    }

    /// Find somebody by handle or name (two letters at least).
    func people(_ words: String) async throws -> [FollowPerson] {
        try await get("api/v1/people", [URLQueryItem(name: "q", value: words)], as: PeoplePage.self).people
    }

    /// Somebody's profile: what they chose to publish, no id.
    func person(handle: String, userID: String = "") async throws -> PersonPage {
        let items = handle.isEmpty ? [URLQueryItem(name: "user_id", value: userID)]
                                   : [URLQueryItem(name: "handle", value: handle)]
        return try await get("api/v1/person", items, as: PersonPage.self)
    }

    /// The Saved for Later shelf with its titles, for a chat's (+).
    func savedItems() async throws -> [SavedItem] {
        try await get("api/v1/saved", [], as: SavedPage.self).items
    }

    /// Save or unsave an episode a message carries (`saveSharedEpisode`).
    func saveShared(query: String, minutes: Double, title: String) async throws {
        _ = try await send("POST", "api/v1/saved", body: [
            "query": Self.cut(query, 500), "minutes": minutes, "title": Self.cut(title, 200),
            "source": "message",
        ], as: SavedState.self)
    }

    func unsaveShared(query: String, minutes: Double) async throws {
        _ = try await sendQuery("DELETE", "api/v1/saved", [
            URLQueryItem(name: "q", value: query),
            URLQueryItem(name: "minutes", value: Self.minutes(minutes)),
        ], as: SavedState.self)
    }
}

/// `GET /api/messages`.
struct InboxPage: Decodable {
    let threads: [InboxThread]
    let unread: Int
}

/// One row of the inbox: a person (`with` is their id) or a group (`g:...`).
struct InboxThread: Decodable, Identifiable {
    let with: String
    let group: Bool?
    let name: String?
    let handle: String?
    let avatar: String?
    let explicit: Bool?
    let members: [ChatMember]?
    let unread: Int?
    let last: ChatMessage?

    var id: String { with }

    /// Lenient about `last`: a group whose last line came from somebody
    /// blocked is sent with that line blanked, and one malformed preview
    /// must not cost the listener the whole list.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        with = try c.decode(String.self, forKey: .with)
        group = try c.decodeIfPresent(Bool.self, forKey: .group)
        name = try c.decodeIfPresent(String.self, forKey: .name)
        handle = try c.decodeIfPresent(String.self, forKey: .handle)
        avatar = try c.decodeIfPresent(String.self, forKey: .avatar)
        explicit = try c.decodeIfPresent(Bool.self, forKey: .explicit)
        members = try? c.decodeIfPresent([ChatMember].self, forKey: .members)
        unread = try c.decodeIfPresent(Int.self, forKey: .unread)
        // `try?` flattens (Swift 5): nil whether absent or unreadable.
        last = try? c.decodeIfPresent(ChatMessage.self, forKey: .last)
    }

    private enum CodingKeys: String, CodingKey {
        case with, group, name, handle, avatar, explicit, members, unread, last
    }
}

/// Somebody in a group (`_group_view`): no listener id, by design.
struct ChatMember: Decodable, Hashable {
    let name: String
    let handle: String
    let avatar: String
    let me: Bool
    /// Whether this listener and that member follow each other (§520): a
    /// group may hold people who are not friends with each other.
    let friend: Bool?
}

/// A message (`Message.as_dict`), as a conversation, the inbox's last
/// line and a notification carry it. `kind` is text, episode or system.
struct ChatMessage: Decodable, Hashable {
    let id: Int
    let thread: String?
    let mine: Bool?
    let kind: String?
    let text: String?
    let query: String?
    let minutes: Double?
    let title: String?
    let at: Double?
    let episode: String?
    let topic: String?
    let finished: Bool?
    let from: ChatSender?
    let fromName: String?
    let group: NoticeGroup?
}

extension ChatMessage {
    /// What the composer draws at once, before the server answers.
    init(draftText: String, query: String = "", minutes: Double = 0, title: String = "") {
        self.init(id: 0, thread: nil, mine: true, kind: query.isEmpty ? "text" : "episode",
                  text: draftText, query: query, minutes: minutes, title: title,
                  at: Date().timeIntervalSince1970, episode: nil, topic: nil, finished: nil,
                  from: nil, fromName: nil, group: nil)
    }
}

struct ChatSender: Decodable, Hashable {
    let userId: String?
    let name: String?
    let handle: String?
    let avatar: String?
}

/// A notification's group (`/api/notifications`): the id opens it.
struct NoticeGroup: Decodable, Hashable {
    let userId: String
    let name: String?
}

/// `GET /api/messages/thread`.
struct ChatPage: Decodable {
    let with: ChatWith
    let typing: Bool?
    let messages: [ChatMessage]
    let partial: Bool?
    let head: Int
}

/// Who a conversation is with: a person, or a group and its people.
struct ChatWith: Decodable, Hashable {
    let userId: String?
    let name: String?
    let handle: String?
    let avatar: String?
    let explicit: Bool?
    let group: Bool?
    let named: Bool?
    let members: [ChatMember]?
    /// A group holding people this listener is not friends with: the
    /// server's sentence saying who (§520); "" otherwise.
    let warning: String?
}

struct SentMessage: Decodable {
    let message: ChatMessage?
}

struct GroupAnswer: Decodable {
    let group: ChatWith?
}

struct UnreadAnswer: Decodable {
    let unread: Int?
}

/// `GET /api/notifications`.
struct NoticePage: Decodable {
    let messages: [ChatMessage]
    let follows: [FollowPerson]
    let head: Int
    let unread: Int
}

/// A person in the follow graph, a search result or a new follower.
struct FollowPerson: Decodable, Hashable, Identifiable {
    let userId: String
    let name: String?
    let handle: String?
    let avatar: String?
    let explicit: Bool?
    let waitlisted: Bool?
    let followsBack: Bool?
    let following: Bool?

    var id: String { userId }
}

/// `GET /api/friends`.
struct FriendsPage: Decodable {
    let following: [FollowPerson]
    let followers: [FollowPerson]
    let friends: [FollowPerson]
    let counts: FollowCounts?
    let newFollowers: [FollowPerson]?
    let announce: [FollowPerson]?
}

struct FollowCounts: Decodable, Hashable {
    let friends: Int?
    let following: Int?
    let followers: Int?
}

struct FollowAnswer: Decodable {
    let counts: FollowCounts?
}

struct PeoplePage: Decodable {
    let people: [FollowPerson]
}

/// `GET /api/person`: only what they chose to publish, and no id.
struct PersonPage: Decodable {
    let name: String
    let handle: String
    let avatar: String?
    let explicit: Bool?
    let location: String?
    let mixes: [PersonMix]?
    let vibes: [PersonVibe]?
    let vibeCount: Int?
    let interests: [String]?
    let interestLabels: [String]?
    let friendCount: Int?
    /// Their friends - only for a friend of theirs; nil otherwise.
    let friends: [PersonFriend]?
}

struct PersonMix: Decodable, Hashable, Identifiable {
    let id: String
    let name: String?
    let cover: String?
    let thumb: String?
    let added: Bool?
    let mine: Bool?
}

struct PersonVibe: Decodable, Hashable, Identifiable {
    let id: Int
    let query: String
    let title: String?
    let minutes: Double?
    let at: Double?
    let topic: String?
    let thumb: String?
}

struct PersonFriend: Decodable, Hashable {
    let name: String?
    let handle: String
    let avatar: String?
    let explicit: Bool?
    let following: Bool?
}

/// A vibe of your own (`/api/profile` `echoes`), for a chat's (+).
struct ProfileEcho: Decodable, Hashable {
    let query: String
    let title: String?
    let minutes: Double?
}

// MARK: - YourFAM: your profile, Settings and the shelves (`op-profile-hub`)
//
// The listener's own profile, what Settings edits, Saved for Later
// (`save-pointer`: a question and a length, never an episode id), the
// listening history (`heard-is-kept`: each row replays its episode id) and
// My Vibe. Every write goes to the server; only the time zone pin, speed and
// pitch live on the device (Q11).

extension FAMAPI {
    /// The UserDefaults key of the pinned time zone ("" or absent: automatic).
    static let zoneKey = "fam.prefs.time_zone"

    /// The zone every request names (`X-FAM-TZ`): the pin, else the device's.
    static var listenerZone: String {
        if let pinned = UserDefaults.standard.string(forKey: zoneKey), !pinned.isEmpty,
           TimeZone(identifier: pinned) != nil {
            return pinned
        }
        return TimeZone.current.identifier
    }

    /// Your own profile hub (`/api/profile?notes=1`, `renderProfile`).
    func myProfile() async throws -> MyProfile {
        try await get("api/v1/profile", [URLQueryItem(name: "notes", value: "1")], as: MyProfile.self)
    }

    /// `/api/me` writes the name, handle and picture together: `avatar` nil
    /// leaves the picture alone, "" takes it off (`set_me`).
    func setMe(name: String, handle: String, avatar: String?) async throws {
        var body: [String: Any] = ["name": name, "handle": handle]
        if let avatar { body["avatar"] = avatar }
        _ = try await send("POST", "api/v1/me", body: body, as: Ignored.self)
    }

    /// What is on offer and what this listener chose (`/api/preferences`).
    func preferences() async throws -> MyPreferences {
        try await get("api/v1/preferences", [], as: MyPreferences.self)
    }

    /// Any of: city, region, country, profile_interests, topics, interests,
    /// searches_public, private_search, intro_done.
    @discardableResult
    func savePreferences(_ body: [String: Any]) async throws -> MyPreferences {
        try await send("POST", "api/v1/preferences", body: body, as: MyPreferences.self)
    }

    /// The account's own details (`POST /api/account`): the first run's name.
    func updateAccount(_ body: [String: Any]) async throws {
        _ = try await send("POST", "api/v1/account", body: body, as: Ignored.self)
    }

    /// Change the password. The server ends every session, this one too, so
    /// the caller signs in again with the new one.
    func changePassword(current: String, new: String) async throws {
        _ = try await send("POST", "api/v1/auth/password", body: ["current": current, "new": new], as: OK.self)
    }

    /// A first password, for an account made with Apple or Google.
    func setPassword(_ new: String) async throws {
        _ = try await send("POST", "api/v1/auth/password/set", body: ["new": new], as: OK.self)
    }

    /// Whether this server can email a reset code (`/api/auth/reset`, §244).
    func resetStatus() async throws -> ResetStatus {
        try await get("api/v1/auth/reset", [], as: ResetStatus.self)
    }

    /// Email a code to the account at this address; the same answer whether
    /// or not it has one.
    func resetStart(email: String) async throws -> ResetStarted {
        try await send("POST", "api/v1/auth/reset/start", body: ["email": email], as: ResetStarted.self)
    }

    /// The code and the new password: signs this phone in.
    func resetFinish(email: String, code: String, new: String) async throws -> Listener {
        let who = try await send("POST", "api/v1/auth/reset/finish",
                                 body: ["email": email, "code": code, "new": new, "want_token": true],
                                 as: Listener.self)
        if let fresh = who.sessionToken { token = fresh }
        return who
    }

    /// The activation answers and the words their editor draws (§411).
    func activation() async throws -> Activation {
        try await get("api/v1/activation", [], as: Activation.self)
    }

    /// Save the date of birth, gender and place. A 409 asks to confirm an
    /// under-13 date (`confirm`); `deleted` means the account is gone.
    func saveActivation(_ body: [String: Any]) async throws -> ActivationAnswer {
        var req = request("POST", "api/v1/activation")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, response) = try await URLSession.shared.data(for: req)
        if let http = response as? HTTPURLResponse, http.statusCode == 409,
           let answer = try? Self.decoder.decode(ActivationAnswer.self, from: data) {
            return answer
        }
        try Self.check(response, data)
        return (try? Self.decoder.decode(ActivationAnswer.self, from: data))
            ?? ActivationAnswer(confirm: nil, deleted: nil, message: nil)
    }

    /// "Count me in FAM's totals" (§411 decision 2).
    func setCounted(_ allow: Bool, version: Int?) async throws {
        var body: [String: Any] = ["scope": "demographics", "allow": allow]
        if let version { body["version"] = version }
        _ = try await send("POST", "api/v1/consent", body: body, as: Ignored.self)
    }

    /// Every tier and the one this listener is on (`/api/plans`); no prices.
    func plans() async throws -> PlansPage {
        try await get("api/v1/plans", [], as: PlansPage.self)
    }

    /// The voice bank and the voice searches are spoken in (§147).
    func voices() async throws -> VoicesPage {
        try await get("api/v1/voices", [], as: VoicesPage.self)
    }

    func chooseVoice(_ id: String) async throws {
        _ = try await send("POST", "api/v1/voices/choice", body: ["voice": id], as: Ignored.self)
    }

    /// The whole shelf with its folders (`/api/saved`); filtered here.
    func shelf() async throws -> SavedShelf {
        try await get("api/v1/saved", [], as: SavedShelf.self)
    }

    func removeSaved(_ id: String) async throws {
        _ = try await send("DELETE", "api/v1/saved/\(id)", as: Ignored.self)
    }

    /// Fire and forget: the shelf orders itself by what is come back to.
    func playedSaved(_ id: String) async {
        _ = try? await send("POST", "api/v1/saved/\(id)/played", body: [:], as: Ignored.self)
    }

    /// File a saved episode; "" takes it out of any folder.
    func moveSaved(_ id: String, folderID: String) async throws {
        _ = try await send("POST", "api/v1/saved/\(id)/move", body: ["folder_id": folderID], as: Ignored.self)
    }

    /// A folder of the listener's own: `kind` "saved" or "vibe".
    func createFolder(name: String, kind: String) async throws -> SavedFolder {
        try await send("POST", "api/v1/saved/folders", body: ["name": name, "kind": kind],
                       as: FolderAnswer.self).folder
    }

    func renameFolder(_ id: String, name: String) async throws {
        _ = try await send("POST", "api/v1/saved/folders/\(id)", body: ["name": name], as: FolderAnswer.self)
    }

    /// Its episodes are unfiled, never deleted.
    func deleteFolder(_ id: String) async throws {
        _ = try await send("DELETE", "api/v1/saved/folders/\(id)", as: Ignored.self)
    }

    /// Two weeks of listening, newest first; one surface or all.
    func history(surface: String) async throws -> HistoryPage {
        let items = surface.isEmpty ? [] : [URLQueryItem(name: "surface", value: surface)]
        return try await get("api/v1/history", items, as: HistoryPage.self)
    }

    /// Everything you have vibed, with your vibe folders (`/api/vibes`).
    func myVibes() async throws -> VibesPage {
        // The server's default (40), as the web's `loadMyVibe` asks it.
        try await get("api/v1/vibes", [], as: VibesPage.self)
    }

    func fileVibe(query: String, minutes: Double, folderID: String) async throws {
        _ = try await send("POST", "api/v1/vibes/file",
                           body: ["query": query, "minutes": minutes, "folder_id": folderID],
                           as: Ignored.self)
    }

    /// Delete a vibe whole: the grid, the story, My Vibe and its folder.
    func deleteVibe(query: String, minutes: Double) async throws {
        let answer = try await sendQuery("DELETE", "api/v1/vibe", [
            URLQueryItem(name: "q", value: query),
            URLQueryItem(name: "minutes", value: Self.minutes(minutes)),
        ], as: OK.self)
        if !answer.ok { throw FAMError.refused(status: 400, message: "") }
    }
}

/// `GET /api/profile?notes=1`: the hub. Lenient - every field optional.
struct MyProfile: Decodable {
    let name: String?
    let handle: String?
    let avatar: String?
    /// The place as they wrote it ("Austin, TX, USA").
    let location: String?
    let follows: FollowCounts?
    let echoCount: Int?
    /// The newest twelve vibes, each with its picture.
    let echoes: [MyVibe]?
    /// Public mixes only, each with its picture.
    let mixes: [ProfileMix]?
    let interestsShown: [ProfileInterest]?
    let interestsRanked: [ProfileInterest]?
    let interestsPinned: [String]?
    let interestsMax: Int?
    /// Your own story: your vibes and notes of the last 24 hours.
    let stories: [Story]?
}

/// One vibe on the hub's grid (`Echo.as_dict` plus `thumb`).
struct MyVibe: Decodable, Hashable, Identifiable {
    let id: Int
    let query: String
    let title: String?
    let minutes: Double?
    let at: Double?
    let thumb: String?
}

/// One public mix on the hub (`public_dict` plus `thumb`).
struct ProfileMix: Decodable, Hashable, Identifiable {
    let id: String
    let name: String?
    let cover: String?
    let thumb: String?
}

/// `GET/POST /api/preferences`: the parts YourFAM reads.
struct MyPreferences: Decodable {
    let location: PlaceInfo?
    let interests: [String]?
    let topics: [String]?
    let profileInterests: [String]?
    let interestsAll: [FacetLabel]?
    let topicsChosen: [FacetLabel]?
    let catalogue: [Subject]?
    let searchesPublic: Bool?
    let privateSearch: Bool?
    let introDone: Bool?
}

struct PlaceInfo: Decodable, Equatable {
    let city: String?
    let region: String?
    let country: String?
    let label: String?
}

/// An id and its name: a facet (`interests_all`) or a chosen subject.
struct FacetLabel: Decodable, Hashable {
    let id: String
    let label: String?
}

/// `GET /api/auth/reset`.
struct ResetStatus: Decodable {
    let available: Bool
    let reason: String?
}

/// `POST /api/auth/reset/start`: the server's sentence.
struct ResetStarted: Decodable {
    let ok: Bool?
    let message: String?
}

/// `GET /api/activation` (`activation.state`).
struct Activation: Decodable {
    let needed: Bool?
    let title: String?
    let birthDate: String?
    let gender: String?
    let location: PlaceInfo?
    let genders: [ActivationGender]?
    let help: ActivationHelp?
    let minAge: Int?
    let counting: ActivationCounting?
}

struct ActivationGender: Decodable, Hashable {
    let id: String
    let label: String
}

struct ActivationHelp: Decodable {
    let birthDate: String?
    let location: String?
    let gender: String?
}

/// Being counted (`describe_demographics`).
struct ActivationCounting: Decodable {
    let version: Int?
    let notice: CountingNotice?
    let asked: Bool?
    let counted: Bool?
}

struct CountingNotice: Decodable {
    let body: String?
    let privacyUrl: String?
}

/// `POST /api/activation`: saved, a question to confirm, or deleted.
struct ActivationAnswer: Decodable {
    let confirm: String?
    let deleted: Bool?
    let message: String?
}

/// `GET /api/plans`.
struct PlansPage: Decodable {
    let tiers: [PlanTier]
    let current: String?
    let enforced: Bool?
    let checkout: Bool?
}

struct PlanTier: Decodable, Hashable {
    let name: String
    let label: String?
    let blurb: String?
    let limits: [PlanLimit]?
}

struct PlanLimit: Decodable, Hashable {
    let resource: String
    let window: String?
    let count: Int?
    let unlimited: Bool?
}

/// `GET /api/voices`.
struct VoicesPage: Decodable {
    let selected: String?
    let voices: [VoiceOption]
}

struct VoiceOption: Decodable, Hashable {
    let id: String
    let label: String
}

/// `GET /api/saved`: the folders, then every item.
struct SavedShelf: Decodable {
    let folders: [SavedFolder]
    let items: [SavedItem]
}

struct SavedFolder: Decodable, Hashable, Identifiable {
    let id: String
    let name: String
    let items: Int?
}

struct FolderAnswer: Decodable {
    let folder: SavedFolder
}

/// `GET /api/history`.
struct HistoryPage: Decodable {
    let items: [HistoryItem]
    let days: Int?
}

/// One heard episode: its question, length, surface and kept episode id.
struct HistoryItem: Decodable, Hashable {
    let query: String
    let minutes: Double
    let surface: String?
    let title: String?
    let context: String?
    let at: Double
    let episode: String?
}

/// `GET /api/vibes`.
struct VibesPage: Decodable {
    let vibes: [VibeRow]
    let folders: [SavedFolder]?
    let count: Int?
}

struct VibeRow: Decodable, Hashable, Identifiable {
    let id: Int
    let query: String
    let title: String?
    let minutes: Double?
    let at: Double?
    let folderId: String?
}

// MARK: - A vibe as a post (§547, `vibe-posts`)

extension FAMAPI {
    /// Somebody's vibes as posts, newest first (`GET /api/vibe/posts`); no
    /// handle is your own. `before` pages by the vibe's time.
    func vibePosts(handle: String, before: Double, limit: Int) async throws -> VibePostsPage {
        var items = [URLQueryItem(name: "limit", value: String(min(30, max(1, limit))))]
        if !handle.isEmpty { items.append(URLQueryItem(name: "handle", value: handle)) }
        if before > 0 { items.append(URLQueryItem(name: "before", value: String(before))) }
        return try await get("api/v1/vibe/posts", items, as: VibePostsPage.self)
    }

    /// Like a vibe's post, or take the like back (`POST /api/vibe/{id}/like`).
    func likeVibePost(_ id: Int, on: Bool) async throws -> VibeLikeAnswer {
        try await send("POST", "api/v1/vibe/\(id)/like", body: ["on": on], as: VibeLikeAnswer.self)
    }

    /// A vibe's replies, most liked first, each with its answers.
    func vibeReplies(_ id: Int) async throws -> VibeRepliesPage {
        try await get("api/v1/vibe/\(id)/replies", [], as: VibeRepliesPage.self)
    }

    /// Reply under a vibe, or answer a reply (`parent`); the server cuts
    /// the words to its own limit (`VibeReplyRequest`: 2000 here).
    func postVibeReply(_ id: Int, text: String, parent: Int) async throws -> VibeReply {
        let body: [String: Any] = ["text": Self.cut(text, 2000), "parent_id": max(0, parent)]
        return try await send("POST", "api/v1/vibe/\(id)/replies", body: body, as: VibeReply.self)
    }

    /// Like a reply under a vibe (`POST /api/vibe/replies/{id}/like`).
    func likeVibeReply(_ id: Int, on: Bool) async throws -> VibeLikeAnswer {
        try await send("POST", "api/v1/vibe/replies/\(id)/like", body: ["on": on], as: VibeLikeAnswer.self)
    }

    /// Your own reply, or any reply on your own vibe (`DELETE /api/vibe/replies/{id}`).
    func deleteVibeReply(_ id: Int) async throws -> Bool {
        try await send("DELETE", "api/v1/vibe/replies/\(id)", as: OK.self).ok
    }
}

/// `GET /api/vibe/posts`: whose vibes, whether they are yours, the posts
/// and whether there are more. Lenient: anything missing decodes empty.
struct VibePostsPage: Decodable {
    let person: VibePostPerson?
    let mine: Bool?
    let posts: [VibePost]?
    let more: Bool?
}

/// The poster: name, handle, picture and the E mark (§224). No id.
struct VibePostPerson: Decodable, Hashable {
    let name: String?
    let handle: String?
    let avatar: String?
    let explicit: Bool?
}

/// One vibe as a post (`SocialStore.vibe_posts` plus the picture and who
/// heard it).
struct VibePost: Decodable, Hashable, Identifiable {
    let id: Int
    let query: String
    let title: String?
    let minutes: Double?
    /// The moment it was vibed, which the post shows ("Vibed yesterday").
    let at: Double?
    let caption: String?
    /// The sentence the poster picked, and where it starts (or null).
    let line: VibePostLine?
    let likes: Int?
    let liked: Bool?
    let replies: Int?
    let topReply: VibeReply?
    let heard: VibePostHeard?
    let thumb: String?
    let topic: String?
}

/// `line`: the words, and where they start in seconds (null: no time).
struct VibePostLine: Decodable, Hashable {
    let text: String?
    let at: Double?
}

/// `heard`: how many friends heard the episode, and up to three faces.
struct VibePostHeard: Decodable, Hashable {
    let count: Int?
    let faces: [VibePostPerson]?
}

/// A reply under a vibe: the comments sheet's shape (`_comment_dict`),
/// with its answers under `replies` at the top level.
struct VibeReply: Decodable, Hashable, Identifiable {
    let id: Int
    let parentId: Int?
    let text: String?
    let at: Double?
    let name: String?
    let handle: String?
    let avatar: String?
    let explicit: Bool?
    let likes: Int?
    let liked: Bool?
    let mine: Bool?
    let replies: [VibeReply]?
}

/// `GET /api/vibe/{id}/replies`.
struct VibeRepliesPage: Decodable {
    let replies: [VibeReply]?
    let count: Int?
    let mine: Bool?
}

/// A like on a post or a reply: its count and state.
struct VibeLikeAnswer: Decodable {
    let id: Int?
    let likes: Int?
    let liked: Bool?
}
