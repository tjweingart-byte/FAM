import Foundation

// The server half is `consent.py` and `moderation.py`; this is a client of
// it and nothing more (`ios-native-client`). Every path is `/api/v1/...`
// (STAGING.md, `old-clients`), every request carries `X-FAM-Client`, and the
// listener is the bearer token the app keeps in the Keychain
// (`listener-header`) - never an id of its own.

// MARK: - What the server sends

/// The AI notice's words (`consent.AI_NOTICE`), shown exactly as sent.
public struct ConsentNotice: Codable, Equatable, Sendable {
    public let title: String
    public let body: String
    public let unaffected: String
    public let allow: String
    public let decline: String
}

/// `GET /api/v1/consent` -> `ai` (`consent.describe`).
public struct AIConsent: Codable, Equatable, Sendable {
    public let scope: String
    public let provider: String
    public let version: Int
    public let notice: ConsentNotice
    public let asked: Bool
    public let given: Bool
    public let answeredVersion: Int?
    public let at: Double?
}

struct ConsentEnvelope: Codable { let ai: AIConsent }

/// One reason a report can give (`moderation.REASONS`).
public struct ReportReason: Codable, Equatable, Hashable, Sendable, Identifiable {
    public let id: String
    public let label: String
}

/// `GET /api/v1/report`.
public struct ReportOptions: Codable, Equatable, Sendable {
    public let reasons: [ReportReason]
    public let kinds: [String]
    public let reviewHours: Int
    public let contact: String
}

/// `POST /api/v1/report`'s answer. `hidden` means: stop showing it now.
public struct ReportReceipt: Codable, Equatable, Sendable {
    public let ok: Bool
    public let id: String
    public let message: String
    public let hidden: Bool
}

/// Somebody this listener blocked (`GET /api/v1/blocks`).
public struct BlockedPerson: Codable, Equatable, Hashable, Sendable, Identifiable {
    public let name: String
    public let handle: String
    public let avatar: String
    public var id: String { handle }
}

struct BlockedEnvelope: Codable { let people: [BlockedPerson] }

/// What is being reported. The server reads the thing itself from its own
/// stores; the app only names it (`app._report_subject`).
public enum ReportSubject: Equatable, Sendable {
    case comment(id: Int)
    case message(id: Int)
    case vibe(id: Int)
    case person(handle: String)
    case group(id: String)
    case episode(query: String, minutes: Int)

    var kind: String {
        switch self {
        case .comment: return "comment"
        case .message: return "message"
        case .vibe: return "vibe"
        case .person: return "person"
        case .group: return "group"
        case .episode: return "episode"
        }
    }

    func body(reason: String) -> [String: Any] {
        var out: [String: Any] = ["kind": kind, "reason": reason]
        switch self {
        case .comment(let id), .message(let id), .vibe(let id):
            out["target"] = String(id)
        case .person(let handle):
            out["target"] = handle
        case .group(let id):
            out["target"] = id
        case .episode(let query, let minutes):
            out["query"] = query
            out["minutes"] = minutes
        }
        return out
    }
}

// MARK: - Errors

public enum SafetyError: Error, Equatable {
    /// The server refused a generation until the listener says yes
    /// (`403` with `X-FAM-Consent: ai`). Show `ConsentSheet`, then send the
    /// same request again after Allow.
    case consentRequired
    /// `401`: blocking needs an account. Show the sign-up gate.
    case accountRequired(String)
    /// Anything else, with the server's own sentence when it sent one.
    case http(status: Int, detail: String)
    case decoding
}

/// The one test the audio player needs: was this refusal the consent
/// question? Use it on `/api/v1/audio` responses (`onGenerationFailed` in
/// static/index.html is the web's copy of this).
public func isConsentRefusal(_ response: HTTPURLResponse) -> Bool {
    response.statusCode == 403
        && (response.value(forHTTPHeaderField: SafetyAPI.consentHeader) ?? "") == "ai"
}

// MARK: - The client

public final class SafetyAPI: @unchecked Sendable {
    public static let consentHeader = "X-FAM-Consent"
    public static let clientHeader = "X-FAM-Client"

    let base: URL
    let client: String
    let session: URLSession
    let token: @Sendable () -> String?

    /// - Parameters:
    ///   - base: the server, e.g. `https://familiarize.net` (staging for
    ///     TestFlight - IOS_APP.md).
    ///   - client: `ios/<version>+<build>`, as every request sends it.
    ///   - token: the session's bearer token from the Keychain, or nil.
    public init(base: URL, client: String, session: URLSession = .shared,
                token: @escaping @Sendable () -> String?) {
        self.base = base
        self.client = client
        self.session = session
        self.token = token
    }

    // Consent (5.1.2(i)).

    public func consent() async throws -> AIConsent {
        try await get("/api/v1/consent", as: ConsentEnvelope.self).ai
    }

    /// Records Allow (`true`) or Not now / Turn off (`false`) against the
    /// notice version the listener was shown.
    @discardableResult
    public func setConsent(allow: Bool, version: Int) async throws -> AIConsent {
        try await send("POST", "/api/v1/consent",
                       body: ["scope": "ai", "allow": allow, "version": version],
                       as: ConsentEnvelope.self).ai
    }

    // Reporting and blocking (1.2).

    public func reportOptions() async throws -> ReportOptions {
        try await get("/api/v1/report", as: ReportOptions.self)
    }

    public func report(_ subject: ReportSubject, reason: String) async throws -> ReportReceipt {
        try await send("POST", "/api/v1/report", body: subject.body(reason: reason),
                       as: ReportReceipt.self)
    }

    public func block(handle: String) async throws {
        _ = try await send("POST", "/api/v1/block", body: ["handle": handle],
                           as: OK.self)
    }

    public func unblock(handle: String) async throws {
        var parts = URLComponents()
        parts.queryItems = [URLQueryItem(name: "handle", value: handle)]
        _ = try await send("DELETE", "/api/v1/block" + (parts.string ?? ""),
                           body: nil, as: OK.self)
    }

    public func blocked() async throws -> [BlockedPerson] {
        try await get("/api/v1/blocks", as: BlockedEnvelope.self).people
    }

    // MARK: plumbing

    struct OK: Codable { let ok: Bool }

    static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    func request(_ method: String, _ path: String) -> URLRequest {
        var req = URLRequest(url: URL(string: path, relativeTo: base)!)
        req.httpMethod = method
        req.setValue(client, forHTTPHeaderField: Self.clientHeader)
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        if let token = token() {
            req.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        }
        return req
    }

    func get<T: Decodable>(_ path: String, as type: T.Type) async throws -> T {
        try await send("GET", path, body: nil, as: T.self)
    }

    func send<T: Decodable>(_ method: String, _ path: String, body: [String: Any]?,
                            as type: T.Type) async throws -> T {
        var req = request(method, path)
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse else {
            throw SafetyError.http(status: 0, detail: "")
        }
        guard (200..<300).contains(http.statusCode) else {
            if isConsentRefusal(http) { throw SafetyError.consentRequired }
            let detail = Self.detail(from: data)
            if http.statusCode == 401 { throw SafetyError.accountRequired(detail) }
            throw SafetyError.http(status: http.statusCode, detail: detail)
        }
        do {
            return try Self.decoder.decode(T.self, from: data)
        } catch {
            throw SafetyError.decoding
        }
    }

    /// FastAPI puts the sentence under `detail`; a few handlers use `error`.
    static func detail(from data: Data) -> String {
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return "" }
        return (object["detail"] as? String) ?? (object["error"] as? String) ?? ""
    }
}
