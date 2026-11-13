import Foundation

/// The two requests the spike makes, and nothing else.
///
/// * `GET /api/v1/welcome` - the sign-up screen's samples: today's
///   most-played episodes whose audio is kept. Open to a guest, open past the
///   waitlist, no model call and no voice woken.
/// * `GET /api/v1/audio?...&cached_only=true` - one of those samples, as a
///   replay. The server lets a guest through only for exactly this shape
///   (`app._welcome_sample_request`): `cached_only=true`, `minutes=1.5`, and no
///   `voice`, `topic_id`, `context`, `episode` or `attach`. Change the shape
///   and a guest gets a 403. `tests/test_ios_spike_contract.py` holds this
///   file to that.
struct SpikeAPI {
    var base: URL

    /// `ios/<version>+<build>`: an unregistered iOS build is served normally
    /// (`client_versions.py`), and `/api/health` counts it.
    static var client: String {
        let info = Bundle.main.infoDictionary ?? [:]
        let version = info["CFBundleShortVersionString"] as? String ?? "0"
        let build = info["CFBundleVersion"] as? String ?? "0"
        return "ios/\(version)+\(build)"
    }

    /// The length every sample is kept at (`config.BROWSE_MINUTES`).
    static let sampleMinutes = "1.5"

    struct Sample: Decodable, Identifiable, Equatable {
        let id: String
        let title: String
        let query: String
    }

    private struct Welcome: Decodable { let episodes: [Sample] }

    func request(_ path: String, query: [URLQueryItem] = []) -> URLRequest {
        var parts = URLComponents(url: base.appendingPathComponent(path),
                                  resolvingAgainstBaseURL: false)!
        if !query.isEmpty { parts.queryItems = query }
        var req = URLRequest(url: parts.url!)
        req.setValue(Self.client, forHTTPHeaderField: "X-FAM-Client")
        req.setValue(TimeZone.current.identifier, forHTTPHeaderField: "X-FAM-TZ")
        return req
    }

    func samples() async throws -> [Sample] {
        let (data, response) = try await URLSession.shared.data(for: request("api/v1/welcome"))
        try Self.check(response, data)
        return try JSONDecoder().decode(Welcome.self, from: data).episodes
    }

    /// The replay of one sample, as bare 16-bit mono PCM (`fmt=pcm`).
    func audio(for sample: Sample) -> URLRequest {
        request("api/v1/audio", query: [
            URLQueryItem(name: "q", value: sample.query),
            URLQueryItem(name: "minutes", value: Self.sampleMinutes),
            URLQueryItem(name: "fmt", value: "pcm"),
            URLQueryItem(name: "cached_only", value: "true"),
        ])
    }

    /// The server's own sentence for a refusal, else the status.
    static func check(_ response: URLResponse, _ data: Data) throws {
        guard let http = response as? HTTPURLResponse else { return }
        guard !(200..<300).contains(http.statusCode) else { return }
        throw SpikeError.refused(status: http.statusCode, message: message(in: data))
    }

    static func message(in data: Data) -> String {
        if let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            for key in ["detail", "error"] {
                if let text = body[key] as? String { return text }
            }
        }
        return ""
    }
}

enum SpikeError: LocalizedError {
    case refused(status: Int, message: String)

    var errorDescription: String? {
        switch self {
        case let .refused(status, message):
            return message.isEmpty ? "The server answered \(status)." : "\(status): \(message)"
        }
    }
}
