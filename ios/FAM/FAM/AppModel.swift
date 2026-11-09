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

    func loadRails() async {
        loadingRails = true
        defer { loadingRails = false }
        do {
            rails = try await api.rails()
            railsProblem = rails.isEmpty ? "Nothing is ready yet." : ""
        } catch FAMError.waitlisted {
            stage = .waiting
        } catch {
            railsProblem = error.localizedDescription
        }
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
    func play(_ ep: Episode) {
        if ep.own && consent.current?.given != true {
            Task {
                guard await consent.ensure() else { return }
                startPlaying(ep)
            }
            return
        }
        startPlaying(ep)
    }

    private func startPlaying(_ ep: Episode) {
        let request = api.audio(query: ep.key.query, minutes: ep.key.minutes, surface: ep.surface,
                                topicID: ep.topicID, context: ep.key.context, own: ep.own)
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
