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

    /// The rails the app shows as "DailyFAM" (`names-swapped`).
    func rails() async throws -> [Rail] {
        try await send("GET", "api/v1/myfam", as: RailsPage.self).sections
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
               topicID: String = "", context: String = "", own: Bool = false) -> URLRequest {
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

    func save(_ ep: EpisodeKey, title: String) async throws {
        _ = try await send("POST", "api/v1/saved", body: [
            "query": ep.query, "minutes": ep.minutes, "title": title, "source": "player",
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

struct RailsPage: Decodable { let sections: [Rail] }

/// One row of DailyFAM: "Made for you", "Trending", …
struct Rail: Decodable, Identifiable, Equatable {
    let key: String
    let title: String
    let topics: [Tile]
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

    /// The card's one line under the title: the angle, else the subtitle
    /// (`seedHook` on the web).
    var hook: String { angle.isEmpty ? subtitle : angle }

    enum CodingKeys: String, CodingKey {
        case id, title, subtitle, angle, query, thumb, thumbFacet
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
    }
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
}

struct ShareTarget: Decodable {
    let text: String?
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
