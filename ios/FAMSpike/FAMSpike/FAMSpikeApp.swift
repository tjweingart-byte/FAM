import SwiftUI

@main
struct FAMSpikeApp: App {
    @StateObject private var model = SpikeModel()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(model)
                .environmentObject(model.player)
        }
    }
}

/// The samples, which one is playing, and whether to keep going.
final class SpikeModel: ObservableObject {
    @Published var server: String {
        didSet { UserDefaults.standard.set(server, forKey: "server") }
    }
    @Published private(set) var samples: [SpikeAPI.Sample] = []
    @Published private(set) var current: SpikeAPI.Sample?
    @Published private(set) var loading = false
    @Published private(set) var message = ""
    /// Play the next sample when one ends, round and round: the ten-minute
    /// locked test needs more than one 1.5-minute episode.
    @Published var keepGoing = true

    let player = StreamPlayer()

    init() {
        server = UserDefaults.standard.string(forKey: "server") ?? "https://familiarize.net"
        player.onFinished = { [weak self] in self?.next() }
    }

    var api: SpikeAPI? {
        let text = server.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URL(string: text), url.scheme?.hasPrefix("http") == true else { return nil }
        return SpikeAPI(base: url)
    }

    @MainActor
    func load() async {
        guard let api else { message = "That is not a server address."; return }
        loading = true
        defer { loading = false }
        do {
            samples = try await api.samples()
            message = samples.isEmpty
                ? "The server has no samples right now: none of today's most-played episodes has kept audio. Play one in the app, then try again."
                : ""
        } catch {
            message = error.localizedDescription
        }
    }

    func play(_ sample: SpikeAPI.Sample) {
        guard let api else { return }
        current = sample
        player.play(title: sample.title, request: api.audio(for: sample))
    }

    private func next() {
        guard keepGoing, !samples.isEmpty else { return }
        let at = samples.firstIndex { $0 == current } ?? -1
        play(samples[(at + 1) % samples.count])
    }
}
