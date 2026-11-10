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

    let base: URL

    init(base: URL = FAMAPI.production) {
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
                authorizationCode: String = "", acceptTerms: Bool) async throws -> Listener {
        var body: [String: Any] = ["provider": provider, "id_token": idToken, "nonce": nonce,
                                   "accept_terms": acceptTerms, "want_token": true]
        if !authorizationCode.isEmpty { body["authorization_code"] = authorizationCode }
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
    }

    /// Delete the account and everything FAM holds about it (5.1.1(v)).
    func deleteAccount() async throws {
        _ = try await send("DELETE", "api/v1/account", as: OK.self)
        token = nil
    }

    // MARK: - Listening

    /// The rails the app shows as "DailyFAM" (`names-swapped`), with the
    /// face book their cards' friends pills point into.
    func rails() async throws -> RailsPage {
        try await get("api/v1/myfam", [URLQueryItem(name: "minutes", value: Self.minutes(Self.browseMinutes))],
                      as: RailsPage.self)
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

    /// Pick up where you left off (`/api/godeeper`).
    func pickUp() async throws -> PickUpPage {
        try await get("api/v1/godeeper", [], as: PickUpPage.self)
    }

    /// Never offer this one again, on any device.
    func dismissPickUp(query: String) async {
        _ = try? await send("POST", "api/v1/godeeper/dismiss", body: ["query": query], as: Ignored.self)
    }

    /// Which episodes are saved, so the cards' bookmarks are lit.
    func savedKeys() async throws -> Set<String> {
        let page = try await get("api/v1/saved", [], as: SavedPage.self)
        return Set(page.items.map { "\($0.query)|\(Self.minutes($0.minutes))" })
    }

    func saveTile(query: String, title: String, source: String) async throws {
        _ = try await send("POST", "api/v1/saved", body: [
            "query": query, "minutes": Self.browseMinutes, "title": title, "source": source,
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
    /// link (`loadInviteLink`).
    func waitlistMe() async throws -> WaitlistMe {
        try await get("api/v1/waitlist/me", [], as: WaitlistMe.self)
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

    /// The audio of one episode as bare PCM (`fmt=pcm`). A DailyFAM tile
    /// names its topic; a search names its length and says it is a search.
    func audio(query: String, minutes: Double, surface: String,
               topicID: String = "", context: String = "", own: Bool = false,
               cachedOnly: Bool = false, episode: String = "") -> URLRequest {
        var items = [
            URLQueryItem(name: "q", value: query),
            URLQueryItem(name: "minutes", value: Self.minutes(minutes)),
            URLQueryItem(name: "fmt", value: "pcm"),
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
        return request("GET", "api/v1/audio", query: items)
    }

    /// Which of the loading screen's steps the episode being made has
    /// finished (`/api/progress`), asked with exactly the audio request's
    /// key-shaped fields - a different field asks about a different episode.
    func progress(query: String, minutes: Double, surface: String,
                  topicID: String = "", context: String = "") async throws -> Progress {
        var items = [
            URLQueryItem(name: "q", value: query),
            URLQueryItem(name: "minutes", value: Self.minutes(minutes)),
            URLQueryItem(name: "surface", value: surface),
        ]
        if !topicID.isEmpty { items.append(URLQueryItem(name: "topic_id", value: topicID)) }
        if !context.isEmpty { items.append(URLQueryItem(name: "context", value: context)) }
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
            "query": ep.query, "minutes": ep.minutes, "title": title, "source": source,
        ], as: SavedState.self)
    }

    func unsave(_ ep: EpisodeKey) async throws {
        _ = try await sendQuery("DELETE", "api/v1/saved",
                                episodeItems(ep).filter { $0.name != "context" }, as: SavedState.self)
    }

    /// VIBE! the episode to your story (`/api/vibe`, `echo` in code).
    func vibe(_ ep: EpisodeKey, title: String, thread: String, caption: String) async throws {
        _ = try await send("POST", "api/v1/vibe", body: [
            "query": ep.query, "title": title, "minutes": ep.minutes,
            "thread": thread, "caption": caption,
        ], as: OK.self)
    }

    func unvibe(_ ep: EpisodeKey) async throws {
        _ = try await sendQuery("DELETE", "api/v1/vibe",
                                episodeItems(ep).filter { $0.name != "context" }, as: OK.self)
    }

    /// A share link (`/s/<id>`) and its wording; no account needed.
    func share(_ ep: EpisodeKey, title: String) async throws -> SharedLink {
        try await send("POST", "api/v1/share", body: [
            "query": ep.query, "minutes": ep.minutes, "title": title,
        ], as: SharedLink.self)
    }

    /// What the taste model learns from (`/api/event`). Fire and forget.
    func event(_ kind: String, topicID: String = "", text: String, thread: String = "",
               minutes: Double = 0, episode: String = "") async {
        var body: [String: Any] = ["kind": kind, "topic_id": topicID,
                                   "text": String(text.prefix(300)),
                                   "thread": String(thread.prefix(200))]
        if minutes > 0 { body["minutes"] = minutes }
        if !episode.isEmpty { body["episode"] = episode }
        _ = try? await send("POST", "api/v1/event", body: body, as: Ignored.self)
    }

    /// How far through it is, kept on the account (`POST /api/progress`).
    func noteResume(_ ep: EpisodeKey, seconds: Double, title: String, duration: Double,
                    episode: String) async {
        _ = try? await send("POST", "api/v1/progress", body: [
            "query": ep.query, "minutes": ep.minutes, "seconds": min(max(0, seconds), 3600),
            "title": title, "context": ep.context, "duration": min(max(0, duration), 3600),
            "episode": episode,
        ], as: Ignored.self)
    }

    /// The listening history row (`POST /api/history`).
    func noteListen(_ ep: EpisodeKey, surface: String, title: String, episode: String,
                    retitle: Bool = false) async {
        var body: [String: Any] = [
            "query": ep.query, "minutes": ep.minutes, "surface": surface,
            "title": title, "context": ep.context, "episode": episode,
        ]
        if retitle { body["retitle"] = true }
        _ = try? await send("POST", "api/v1/history", body: body, as: Ignored.self)
    }

    private func get<T: Decodable>(_ path: String, _ items: [URLQueryItem], as type: T.Type) async throws -> T {
        try await sendQuery("GET", path, items, as: type)
    }

    private func sendQuery<T: Decodable>(_ method: String, _ path: String, _ items: [URLQueryItem],
                                         as type: T.Type) async throws -> T {
        let (data, response) = try await URLSession.shared.data(for: request(method, path, query: items))
        try Self.check(response, data)
        do { return try Self.decoder.decode(T.self, from: data) } catch { throw FAMError.unreadable }
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

    // MARK: - Plumbing

    func request(_ method: String, _ path: String, query: [URLQueryItem] = []) -> URLRequest {
        var parts = URLComponents(url: base.appendingPathComponent(path),
                                  resolvingAgainstBaseURL: false)!
        if !query.isEmpty { parts.queryItems = query }
        var req = URLRequest(url: parts.url!)
        req.httpMethod = method
        req.setValue(Self.client, forHTTPHeaderField: "X-FAM-Client")
        req.setValue(TimeZone.current.identifier, forHTTPHeaderField: "X-FAM-TZ")
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
        let (data, response) = try await URLSession.shared.data(for: req)
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
        guard let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) else { return }
        let detail = message(in: data)
        if http.value(forHTTPHeaderField: "X-FAM-Waitlist") != nil { throw FAMError.waitlisted(detail) }
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
/// the plain page rather than dropping the faces row.
struct StoryStyle: Decodable, Hashable {
    var bg: String?
    var font: String?
    var size: Double?
    var cx: Double?
    var cy: Double?
    var thumb: String?
    var stickers: [StorySticker]

    enum CodingKeys: String, CodingKey { case bg, font, size, cx, cy, thumb, stickers }

    init(from decoder: Decoder) throws {
        stickers = []
        guard let c = try? decoder.container(keyedBy: CodingKeys.self) else { return }
        bg = try? c.decodeIfPresent(String.self, forKey: .bg)
        font = try? c.decodeIfPresent(String.self, forKey: .font)
        size = try? c.decodeIfPresent(Double.self, forKey: .size)
        cx = try? c.decodeIfPresent(Double.self, forKey: .cx)
        cy = try? c.decodeIfPresent(Double.self, forKey: .cy)
        thumb = try? c.decodeIfPresent(String.self, forKey: .thumb)
        stickers = (try? c.decodeIfPresent([StorySticker].self, forKey: .stickers)) ?? []
    }

    /// Whether the editor laid this out at all (`storyHasLayout`).
    var isEmpty: Bool {
        bg == nil && font == nil && size == nil && cx == nil && cy == nil && thumb == nil && stickers.isEmpty
    }
}

/// A sticker on a story: an emoji or a word, where it sits, its scale and turn.
struct StorySticker: Decodable, Hashable {
    var k: String
    var x: Double
    var y: Double
    var s: Double
    var r: Double

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
    var handle: String
    var name: String
    var x: Double
    var y: Double

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
struct WaitlistMe: Decodable {
    let waitlist: Bool?
    let referralLink: String?
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

    var label: String { title.isEmpty ? query : title }
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
    let placed: Bool?
    let searcher: String?
}

struct NextUpPage: Decodable { let topics: [Tile] }

struct SavedState: Decodable { let saved: Bool }

/// `POST /api/share`: the link, and per-target wording.
struct SharedLink: Decodable {
    let url: String
    let targets: [String: ShareTarget]?
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
}

/// An answer whose body nothing reads.
struct Ignored: Decodable {}


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

    /// Your own profile (`/api/profile`): the name, handle and picture a
    /// solo mix's people line shows, and the interests the local
    /// recommendation order starts from (`profileNow`).
    func profile() async throws -> ProfileSummary {
        try await get("api/v1/profile", [], as: ProfileSummary.self)
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

    var isOwner: Bool { role != "member" }

    enum CodingKeys: String, CodingKey {
        case id, name, items, `public`, cover, sourceId, listenAt, role, joint, people, from, owner, mine, added
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

    enum CodingKeys: String, CodingKey {
        case id, title, query, custom, subtitle, dailyPrompt, prompt, minutes, follow, addedBy
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
    }

    /// What the server is asked to keep for this entry in a whole-list
    /// write: the id, or a typed topic's words and title (so it is never
    /// renamed).
    var entry: Any { custom ? ["query": query, "title": title] as [String: Any] : id }
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
}

struct PushStatus: Decodable {
    let available: Bool
    let reason: String?
    let subscribed: Bool?
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
}

struct CategoryNode: Decodable, Identifiable, Hashable {
    let id: String
    let label: String
    let facet: String?
    let path: [String]?
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
            "query": query, "minutes": minutes, "title": title, "source": "message",
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
    /// Whether this listener and that member follow each other (§523): a
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
    /// server's sentence saying who (§523); "" otherwise.
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
