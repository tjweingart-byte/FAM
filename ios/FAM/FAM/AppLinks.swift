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
}
