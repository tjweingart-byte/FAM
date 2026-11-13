import Foundation

// Shared links open in the app (decision 5), and the player's hooks into
// what only the app model knows (the tab under the player, the search
// page's Length, the AI question).
//
// Universal links for familiarize.net (`applinks:familiarize.net`,
// project.yml): `/s/<id>` (an episode), `/m/<id>` (a mix) and `/j/<token>`
// (an invite to a joint mix), handled as the web handles each - its
// listen page, `openLinkedMix` and `openLinkedJoin` / `joinPendingMix` -
// and held until somebody is signed in and let in, because a mix (and, on
// iPhone, listening) needs an account.

extension AppModel {
    /// The player's view of the app (called once, from `init`).
    func wirePlayerHooks() {
        nowPlaying.ensureConsent = { [weak self] in
            guard let self else { return false }
            return await self.consent.ensure()
        }
        nowPlaying.tabSurface = { [weak self] in
            AppModel.surface(of: self?.shownTab ?? .daily)
        }
        nowPlaying.searchLength = { [weak self] in
            self?.searchLength ?? FAMAPI.browseMinutes
        }
    }

    /// Where a tap under each tab says it came from (`historySurface`,
    /// `stack[0]`): searchFAM is "search", DailyFAM's rails the server's
    /// "myfam", myFAM's mixes "dailyfam" (`names-swapped`), else "other".
    static func surface(of tab: Tab) -> String {
        switch tab {
        case .search: return "search"
        case .daily: return "myfam"
        case .myfam: return "dailyfam"
        case .messages, .you: return "other"
        }
    }

    // MARK: - Links

    /// What a link is, or nil for anything that is not one of FAM's.
    enum AppLink: Equatable {
        case episode(String)
        case mix(String)
        case join(String)
    }

    static func sharedLink(_ url: URL) -> AppLink? {
        let host = (url.host ?? "").lowercased()
        let ours = FAMAPI.configuredBase.host?.lowercased() ?? ""
        let known: Set<String> = ["familiarize.net", "www.familiarize.net", ours]
        guard url.scheme == "https" || url.scheme == "http", known.contains(host) else { return nil }
        let parts = url.path.split(separator: "/").map(String.init)
        if parts.count >= 2 {
            let value = String(parts[1].prefix(64))
            if !value.isEmpty {
                switch parts[0] {
                case "s": return .episode(value)
                case "m": return .mix(value)
                case "j": return .join(value)
                default: break
                }
            }
        }
        // Where the server's redirects land (`/?mix=`, `/?join=`).
        let query = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
        if let id = query.first(where: { $0.name == "mix" })?.value, !id.isEmpty {
            return .mix(String(id.prefix(64)))
        }
        if let token = query.first(where: { $0.name == "join" })?.value, !token.isEmpty {
            return .join(String(token.prefix(64)))
        }
        return nil
    }

    /// A link the app was opened with: followed now if the app is open on
    /// its tabs, else held until it is (`openPendingLink`).
    func openLink(_ url: URL) {
        guard let link = Self.sharedLink(url) else { return }
        // The same link can arrive twice (as a URL and as a web activity).
        if let last = lastLink, last.link == link, Date().timeIntervalSince(last.at) < 2 { return }
        lastLink = (link: link, at: Date())
        pendingLink = url
        if stage == .ready && entry == nil {
            openPendingLink()
            return
        }
        // A shared episode plays for anyone (§575): somebody signed out or
        // still on the waitlist gets the listen page's screen now (§576).
        if stage == .signedOut || stage == .waiting, case .episode = link {
            openSharedListenIfHeld()
            return
        }
        if stage == .signedOut, case .join = link {
            // The web's sheet: the token waits for the sign-up or log-in.
            nowPlaying.say("You\u{2019}ve been invited to a joint mix. Sign up or log in to add topics to it.")
        }
    }

    /// The held link, once the app is on its tabs (MainTabs asks on
    /// arrival). Nothing held, nothing done.
    func openPendingLink() {
        guard stage == .ready, entry == nil, let url = pendingLink else { return }
        pendingLink = nil
        guard let link = Self.sharedLink(url) else { return }
        // After the tabs have drawn: a presentation needs a window.
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 350_000_000)
            switch link {
            case let .episode(id): await self.openLinkedEpisode(id)
            case let .mix(id): await self.openLinkedMix(id)
            case let .join(token): await self.joinLinkedMix(token)
            }
        }
    }

    /// `/s/<id>`: the episode the listen page plays - the question at its
    /// length, on the `share` surface - with the open counted as the page
    /// counts it.
    func openLinkedEpisode(_ id: String) async {
        do {
            let share = try await api.sharedEpisode(id)
            guard !share.question.isEmpty else {
                nowPlaying.say("This link has expired")
                return
            }
            Task { await self.api.shareOpened(id) }
            let title = share.title.isEmpty ? "A FAM episode" : share.title
            let ep = Episode(key: EpisodeKey(query: share.question, minutes: max(1, share.minutes)),
                             title: title, surface: "share")
            play(ep)
        } catch {
            nowPlaying.say(MixesModel.sentence(error, else: "This link has expired"))
        }
    }

    /// `/m/<id>` (`openLinkedMix`): somebody else's public mix on its page;
    /// your own opens as yours to play and edit (`openOwnMix`).
    func openLinkedMix(_ id: String) async {
        do {
            let found = try await api.publicMix(id)
            if found.mine {
                tabRequest = .myfam
                await mixes.load()
                if mixes.mix(found.id) != nil { openMix(found.id) }
            } else {
                openPublicMix(found.id)
            }
        } catch {
            nowPlaying.say(MixesModel.sentence(error, else: "That mix is private or no longer exists."))
        }
    }

    /// `/j/<token>` (`joinPendingMix`): joined, the mix kept and opened on
    /// the myFAM tab, and a member told what to do next.
    func joinLinkedMix(_ token: String) async {
        do {
            let mix = try await api.joinMix(token: token)
            mixes.keep(mix)
            openMix(mix.id)
            if mix.role == "member" { nowPlaying.say("You\u{2019}re in \u{2014} add the topics you want to hear") }
        } catch {
            nowPlaying.say(MixesModel.sentence(error, else: "Could not join that mix"))
        }
    }

    // MARK: - A shared episode before the app is open to you (§576)

    /// The stage settled (RootView's `onChange(of: stage)`): a link held
    /// since a cold start opens the shared screen for somebody not let in;
    /// let in, the screen closes and the app takes over as before.
    func stageSettled() {
        switch stage {
        case .signedOut, .waiting: openSharedListenIfHeld()
        case .ready: closeSharedListen()
        case .opening: break
        }
    }

    /// A held `/s/<id>` link, for somebody signed out or waitlisted: the
    /// listen page's screen, its share read, its open counted as the page
    /// counts it. The link is taken - nothing replays it after a sign-up.
    func openSharedListenIfHeld() {
        guard stage == .signedOut || stage == .waiting, let url = pendingLink,
              case let .episode(id)? = Self.sharedLink(url) else { return }
        pendingLink = nil
        stopSample()
        stopSharedAudio()
        sharedListen = SharedListen(id: id)
        Task { @MainActor in
            do {
                let share = try await self.api.sharedEpisode(id)
                guard self.sharedListen?.id == id else { return }
                guard !share.question.isEmpty else {
                    self.sharedListen?.expired = true
                    self.sharedListen?.loaded = true
                    return
                }
                self.sharedListen?.share = share
                self.sharedListen?.loaded = true
                Task { await self.api.shareOpened(id) }
            } catch {
                guard self.sharedListen?.id == id else { return }
                if case let FAMError.refused(status, _) = error, status == 404 {
                    self.sharedListen?.expired = true
                } else {
                    self.sharedListen?.problem = MixesModel.sentence(error, else: "That episode could not be played just now.")
                }
                self.sharedListen?.loaded = true
            }
        }
    }

    /// Play: the listen page's `start()` - the question at exactly the
    /// shared length (1.5 stays 1.5), on the `share` surface, nothing
    /// attached, in the voice it was made in; pause and resume in place.
    func toggleShared() {
        guard let share = sharedListen?.share else { return }
        if !shareStarted || shareEnded || shareQuery.isEmpty {
            shareStarted = true
            shareEnded = false
            shareQuery = share.question
            shareNote = "Starting the episode\u{2026}"
            shareNoteBad = false
            player.play(title: share.title.isEmpty ? "A FAM episode" : share.title,
                        picture: share.image.flatMap { URL(string: $0) },
                        request: api.audio(query: share.question, minutes: share.minutes, surface: "share"),
                        minutes: share.minutes, holdStart: false)
            return
        }
        player.toggle()
    }

    /// The first word (`onFirstAudio`): the note goes.
    func sharedStarted() {
        if !shareQuery.isEmpty && shareNote.hasPrefix("Starting") { shareNote = "" }
    }

    /// `onEnd`.
    func shareFinished() {
        shareEnded = true
        shareNote = "That\u{2019}s the episode."
        shareNoteBad = false
    }

    /// `onError`: a play button again, and why, in the server's words.
    func shareFailed(_ words: String) {
        shareStarted = false
        shareEnded = false
        shareQuery = ""
        shareNote = words.isEmpty ? "That episode could not be played just now." : words
        shareNoteBad = true
    }

    /// The shared episode's audio stops (the page left).
    func stopSharedAudio() {
        if !shareQuery.isEmpty { player.stop() }
        shareQuery = ""
        shareNote = ""
        shareNoteBad = false
        shareStarted = false
        shareEnded = false
    }

    /// Every other control on the page is a door (`data-door`): the page's
    /// go to `/waitlist`. In the app that is Sign up for somebody signed out;
    /// somebody already on the waitlist is back on their status page.
    func shareDoor() {
        let signedOut = stage == .signedOut
        closeSharedListen()
        if signedOut { askSignUp = true }
    }

    /// The screen goes, and the audio with it.
    func closeSharedListen() {
        stopSharedAudio()
        sharedListen = nil
    }
}

/// The shared screen's state: the share once read, or why it is not there.
struct SharedListen: Identifiable {
    let id: String
    var share: SharePayload?
    var loaded = false
    /// Nothing to play: the page's "This link has expired".
    var expired = false
    /// The share could not be read (no connection, the server's sentence).
    var problem = ""
}
