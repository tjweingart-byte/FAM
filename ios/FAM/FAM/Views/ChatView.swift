import FAMSafety
import PhotosUI
import SwiftUI
import UIKit
import UniformTypeIdentifiers

/// Something to report from a conversation: the person or the group from
/// the ⋯, a member from See who's here, or one message held down - which
/// the web has no control for, though the server takes it (quirk 1, 1.2).
struct ReportTarget: Identifiable {
    let id = UUID()
    let subject: ReportSubject
    var messageID: Int? = nil
}

/// An episode a message carries, as Go Deeper builds on it before it has
/// been played (`goDeeperOnShared`).
struct SharedEpisode: Equatable, Identifiable {
    let query: String
    let title: String
    let minutes: Double
    var id: String { query + "|" + FAMAPI.minutes(minutes) }
}

/// A conversation (`#screen-thread`, shots 11-27): the header on the
/// surface colour, the messages - day separators, bubbles, episode cards
/// with Listen / GO DEEPER / save, story replies and likes, the "You
/// finished it" receipt, a group's system lines and sender names, the
/// typing dots - and the composer, where Return is a new line and only
/// the arrow sends (§142). Live every 2 s while on screen.
struct ChatScreen: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var social: SocialModel
    @StateObject private var chat: ChatModel

    @State private var person: PersonPage?
    @State private var sheet: SheetSpec?
    @State private var reporting: ReportTarget?
    @State private var blocking = false
    @State private var blockName = ""
    @State private var blockHandle = ""
    @State private var blockingMember = false
    @State private var confirmDelete = false
    @State private var confirmLeave = false
    @State private var renaming = false
    @State private var newName = ""
    @State private var showMembers = false
    @State private var pickingLibrary = false
    @State private var pickingFile = false
    @State private var takingPhoto = false
    @State private var photoItem: PhotosPickerItem?
    @State private var deeper: SharedEpisode?
    @State private var popReady = false
    @FocusState private var typing: Bool

    init(api: FAMAPI, open: ChatOpen) {
        _chat = StateObject(wrappedValue: ChatModel(api: api, who: open))
    }

    private var who: ChatOpen { chat.who }
    private var first: String { socialFirstName(who.name, handle: who.handle) }

    var body: some View {
        pickers(presentations(screen))
    }

    private var screen: some View {
        VStack(spacing: 0) {
            header
            messages
        }
        .background(FAMColor.background)
        .safeAreaInset(edge: .bottom, spacing: 0) { composer }
        .toolbar(.hidden, for: .navigationBar)
        .task { await start() }
        .onAppear { social.chatOnScreen = who.with }
        .onDisappear { if social.chatOnScreen == who.with { social.chatOnScreen = nil } }
    }

    /// The ⋯ sheets, the report and block, Go Deeper and the confirmations.
    private func presentations<V: View>(_ content: V) -> some View {
        content
            .sheet(item: $sheet) { spec in FAMActionSheet(spec: spec) }
            .sheet(item: $reporting) { target in reportSheet(target) }
            .sheet(isPresented: $showMembers) { membersSheet }
            .sheet(item: $deeper) { base in deeperSheet(base) }
            .modifier(ChatConfirmations(chat: chat, confirmDelete: $confirmDelete, confirmLeave: $confirmLeave,
                                        first: first, deleteChat: deleteChat, leaveGroup: leaveGroup))
            .blockConfirmation(isPresented: $blocking, name: blockName, handle: blockHandle, api: model.safety,
                               onBlocked: { blocked(member: blockingMember) },
                               onError: { model.nowPlaying.say($0) })
            .alert("Name this group", isPresented: $renaming) {
                TextField("Group name", text: $newName)
                Button("Cancel", role: .cancel) {}
                Button("Save") { rename() }
            } message: {
                Text("Up to 60 letters. Leave it empty to take the name off.")
            }
    }

    /// The group photo's three sources.
    private func pickers<V: View>(_ content: V) -> some View {
        content
            .photosPicker(isPresented: $pickingLibrary, selection: $photoItem, matching: .images)
            .onChange(of: photoItem) { _, item in
                guard let item else { return }
                photoItem = nil
                Task { await load(item) }
            }
            .fileImporter(isPresented: $pickingFile, allowedContentTypes: [.image]) { result in
                picked(file: result)
            }
            .fullScreenCover(isPresented: $takingPhoto) {
                CameraPicker { data in
                    takingPhoto = false
                    if let data { upload(data) }
                }
                .ignoresSafeArea()
            }
    }

    private func reportSheet(_ target: ReportTarget) -> some View {
        ReportSheet(api: model.safety, subject: target.subject) { receipt in
            // A reported message leaves this listener's thread at once.
            if let id = target.messageID, receipt?.hidden == true { chat.remove(messageID: id) }
        }
    }

    private func deeperSheet(_ base: SharedEpisode) -> some View {
        GoDeeperSheet(base: base) { words, minutes, typed in
            followUp(base, words: words, minutes: minutes, typed: typed)
        }
        .environmentObject(model)
        .environmentObject(model.nowPlaying)
    }

    private func load(_ item: PhotosPickerItem) async {
        guard let data = try? await item.loadTransferable(type: Data.self) else {
            model.nowPlaying.say("Could not read that file")
            return
        }
        upload(data)
    }

    private func start() async {
        if social.me == nil { Task { await social.loadMe() } }
        if !who.group && !who.handle.isEmpty && person == nil {
            Task { person = try? await model.api.person(handle: who.handle) }
        }
        let app = model
        chat.say = { [weak app] text in app?.nowPlaying.say(text) }
        await chat.run()
    }

    // MARK: - Header (`.yf-thread-head`)

    private var header: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                SocialBackButton { social.back() }
                Button(action: whoTapped) { whoLabel }
                    .buttonStyle(.plain)
                if who.group {
                    Button { photoMenu() } label: {
                        RoundIcon { Image(systemName: "paperclip").font(.system(size: 15, weight: .medium)) }
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(who.avatar.isEmpty ? "Add a group photo" : "Change the group photo")
                }
                Button { openMenu() } label: {
                    RoundIcon { WebFill(path: Icons.dots(1.7), size: 17) }
                }
                .buttonStyle(.plain)
                .accessibilityLabel("More")
            }
            if !who.group && !chipLabels.isEmpty {
                FlowRow(spacing: 7) {
                    ForEach(chipLabels, id: \.self) { label in
                        InterestChip(label: label)
                    }
                    if inCommon > 0 {
                        InterestChip(label: "\(inCommon) in common", common: true)
                    }
                }
                .padding(.top, 11)
            }
        }
        .padding(.horizontal, 16)
        .padding(.top, 10)
        .padding(.bottom, 12)
        .background(FAMColor.surface.ignoresSafeArea(edges: .top))
        .overlay(alignment: .bottom) { Rectangle().fill(FAMColor.border).frame(height: 1) }
    }

    private var whoLabel: some View {
        HStack(spacing: 10) {
            Face(name: who.name, handle: who.handle, avatar: who.avatar, size: 42, initialsSize: 14)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(displayName)
                        .font(FAMFont.heading(18, .medium))
                        .foregroundStyle(FAMColor.text)
                        .lineLimit(1)
                    if who.explicit { ExplicitBadge() }
                }
                Text(subLine)
                    .font(FAMFont.mono(10))
                    .foregroundStyle(FAMColor.textMuted)
                    .lineLimit(1)
            }
            Spacer(minLength: 0)
        }
        .contentShape(Rectangle())
    }

    private var displayName: String {
        if !who.name.isEmpty { return who.name }
        if !who.handle.isEmpty { return "@" + who.handle }
        return who.group ? "Group" : "Someone"
    }

    /// "View profile", or "N people · See who's here" once the group's
    /// people are known (a banner opens a group with none yet, quirk 16).
    private var subLine: String {
        guard who.group else { return "View profile" }
        if !who.loaded || who.members.isEmpty { return "See who\u{2019}s here" }
        let n = who.members.count
        return "\(n) \(n == 1 ? "person" : "people") \u{00B7} See who\u{2019}s here"
    }

    private var chipLabels: [String] { person?.interestLabels ?? [] }
    private var inCommon: Int { social.inCommon(chipLabels) }

    private func whoTapped() {
        if who.group { showMembers = true; return }
        social.openPerson(handle: who.handle, userID: who.with, name: who.name, avatar: who.avatar)
    }

    // MARK: - The messages (`#thread-body`)

    private var messages: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    bodyContent
                    Color.clear.frame(height: 1).id("bottom")
                }
                .padding(.horizontal, 13)
                .padding(.vertical, 14)
                .animation(popReady ? .easeOut(duration: 0.26) : nil, value: chat.rows.map(\.id))
                .animation(.easeOut(duration: 0.2), value: chat.typing)
            }
            .defaultScrollAnchor(.bottom)
            .scrollDismissesKeyboard(.interactively)
            .onChange(of: chat.rows.count) { _, _ in
                withAnimation(.easeOut(duration: 0.2)) { proxy.scrollTo("bottom", anchor: .bottom) }
                if chat.state == .loaded && !popReady {
                    Task { @MainActor in
                        try? await Task.sleep(nanoseconds: 300_000_000)
                        popReady = true
                    }
                }
            }
            .onChange(of: chat.typing) { _, on in
                if on { withAnimation { proxy.scrollTo("bottom", anchor: .bottom) } }
            }
            .onChange(of: typing) { _, on in
                if on {
                    Task { @MainActor in
                        try? await Task.sleep(nanoseconds: 300_000_000)
                        withAnimation { proxy.scrollTo("bottom", anchor: .bottom) }
                    }
                }
            }
        }
    }

    @ViewBuilder private var bodyContent: some View {
        if who.group && !who.warning.isEmpty {
            // The server's sentence: who in this group the listener is not
            // friends with (§523).
            Text(who.warning)
                .font(FAMFont.body(11.5))
                .foregroundStyle(FAMColor.text)
                .lineSpacing(3)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 13)
                .padding(.vertical, 11)
                .background(FAMColor.copper.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
                .overlay(RoundedRectangle(cornerRadius: 12).stroke(FAMColor.copper))
                .padding(.top, 4)
                .padding(.bottom, 12)
                .accessibilityAddTraits(.isStaticText)
        }
        if chat.rows.isEmpty {
            switch chat.state {
            case .idle, .loading: SocialNote(text: "Loading\u{2026}")
            case .failed: SocialNote(text: "Could not open that conversation.")
            case .loaded: SocialNote(text: "Nothing here yet. Say something, or share an episode with the (+).")
            }
        } else {
            let lines = chatLines
            ForEach(lines) { line in
                ChatLineView(line: line, group: who.group, otherFirst: first,
                             saved: savedFor(line.row.message),
                             listen: { listen(line.row.message) },
                             goDeeper: { goDeeper(line.row.message) },
                             save: { save(line.row.message) },
                             report: { reportMessage(line.row.message) })
                    .transition(.scale(scale: 0.6, anchor: line.row.message.mine == true ? .bottomTrailing : .bottomLeading)
                        .combined(with: .opacity))
            }
        }
        if chat.typing {
            HStack {
                TypingBubble()
                Spacer(minLength: 0)
            }
            .transition(.opacity)
        }
    }

    /// Each row with its day separator (every row, system lines too -
    /// quirk 4) and, in a group, the sender's first name over the first
    /// bubble of a run - not over a card, whose label already names them
    /// (quirk 5).
    private var chatLines: [ChatLine] {
        var out: [ChatLine] = []
        var previous: ChatMessage?
        for row in chat.rows {
            let m = row.message
            let at = m.at ?? Date().timeIntervalSince1970
            var day = ""
            if let p = previous, let pat = p.at, SocialTime.sameDay(pat, at) {
                day = ""
            } else {
                day = SocialTime.day(at)
            }
            var from = ""
            if who.group && m.mine != true && m.kind == "text" {
                let sender = m.from?.name ?? ""
                let sameRun = previous.map { p in
                    p.mine != true && p.kind != "system" && (p.from?.name ?? "") == sender && day.isEmpty
                } ?? false
                if !sameRun { from = socialFirstName(sender, handle: m.from?.handle ?? "") }
            }
            out.append(ChatLine(row: row, day: day, from: from))
            previous = m
        }
        return out
    }

    private func savedFor(_ m: ChatMessage) -> Bool {
        guard m.kind == "episode", let q = m.query else { return false }
        return model.isSaved(query: q, minutes: m.minutes ?? FAMAPI.browseMinutes)
    }

    // MARK: - Card actions

    /// Listen: the sender's exact episode (§288), else the question.
    private func listen(_ m: ChatMessage) {
        guard let q = m.query, !q.isEmpty else { return }
        let minutes = m.minutes ?? FAMAPI.browseMinutes
        let title = m.title ?? ""
        let episode = m.episode ?? ""
        let app = model
        social.closeCover { app.playShared(query: q, minutes: minutes, title: title, episode: episode) }
    }

    private func goDeeper(_ m: ChatMessage) {
        guard let q = m.query, !q.isEmpty else { return }
        deeper = SharedEpisode(query: q, title: m.title ?? q, minutes: m.minutes ?? FAMAPI.browseMinutes)
    }

    /// A follow-up of the shared episode: its title is the context.
    private func followUp(_ base: SharedEpisode, words: String, minutes: Double, typed: Bool) {
        let ep = Episode(key: EpisodeKey(query: words, minutes: minutes, context: base.title),
                         title: NowPlayingModel.titleFromQuestion(words), surface: "other",
                         own: typed, picture: nil, titleOverridden: false)
        let app = model
        social.closeCover { app.play(ep) }
    }

    private func save(_ m: ChatMessage) {
        guard let q = m.query, !q.isEmpty else { return }
        model.toggleSaved(query: q, minutes: m.minutes ?? FAMAPI.browseMinutes, title: m.title ?? "")
    }

    private func reportMessage(_ m: ChatMessage) {
        guard m.id > 0 else { return }
        reporting = ReportTarget(subject: .message(id: m.id), messageID: m.id)
    }

    // MARK: - The composer (`.yf-composer`)

    private var composer: some View {
        HStack(alignment: .bottom, spacing: 7) {
            Button { shareInto() } label: {
                RoundIcon { WebIcon(path: Icons.plus, size: 17, line: 2.4) }
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Share an episode")
            TextField("", text: $chat.draft, prompt: Text(placeholder).foregroundColor(FAMColor.textMuted),
                      axis: .vertical)
                .font(FAMFont.body(12.5))
                .foregroundStyle(FAMColor.text)
                .lineLimit(1...5)
                .textInputAutocapitalization(.sentences)
                .focused($typing)
                .padding(.horizontal, 13)
                .padding(.vertical, 9)
                .frame(minHeight: 36)
                .frame(maxHeight: 112)
                .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 18))
                .overlay(RoundedRectangle(cornerRadius: 18).stroke(FAMColor.borderStrong))
                .onChange(of: chat.draft) { _, _ in chat.noteTyping() }
            Button { chat.sendText() } label: {
                RoundIcon(gold: true) { WebIcon(path: Icons.arrow, size: 16, line: 2.4) }
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Send")
        }
        .padding(.horizontal, 12)
        .padding(.top, 10)
        .padding(.bottom, 10)
        .background(FAMColor.background.ignoresSafeArea(edges: .bottom))
        .overlay(alignment: .top) { Rectangle().fill(FAMColor.border).frame(height: 1) }
    }

    private var placeholder: String {
        who.group ? "Message the group" : "Message " + first
    }

    /// `shareIntoThread`: up to seven episodes, nobody twice - the one
    /// playing, Saved for Later, your vibes. Mixes are not sent from here.
    private func shareInto() {
        Task {
            var items: [(ep: SharedEpisode, why: String, episode: String)] = []
            var seen: Set<String> = []
            func offer(_ query: String, _ title: String, _ minutes: Double, _ why: String, _ episode: String) {
                let key = query.lowercased() + "|" + FAMAPI.minutes(minutes)
                guard !query.isEmpty, !seen.contains(key), items.count < 7 else { return }
                seen.insert(key)
                items.append((SharedEpisode(query: query, title: title, minutes: minutes), why, episode))
            }
            if let now = model.nowPlaying.episode, model.player.isLoaded {
                offer(now.key.query, model.nowPlaying.title, now.key.minutes, "playing", model.player.episodeID)
            }
            let saved = (try? await model.api.savedItems()) ?? []
            for s in saved { offer(s.query, s.title ?? "", s.minutes, "saved", "") }
            if social.me == nil { await social.loadMe() }
            for e in social.me?.echoes ?? [] {
                offer(e.query, e.title ?? "", e.minutes ?? FAMAPI.browseMinutes, "vibed", "")
            }
            guard !items.isEmpty else {
                model.nowPlaying.say("Play or save an episode, then share it here")
                return
            }
            let actions = items.map { item in
                SheetAction(label: (item.ep.title.isEmpty ? item.ep.query : item.ep.title) + " \u{00B7} " + item.why) {
                    chat.sendEpisode(query: item.ep.query, minutes: item.ep.minutes,
                                     title: item.ep.title, episode: item.episode)
                }
            }
            sheet = SheetSpec(header: "Share with " + (who.group ? displayName : first), actions: actions)
        }
    }

    // MARK: - The ⋯ (`openThreadMenu`)

    private func openMenu() {
        var actions: [SheetAction] = []
        if who.group {
            actions.append(SheetAction(label: "See who\u{2019}s here") { showMembers = true })
            actions.append(SheetAction(label: "Name the group") {
                newName = who.named ? who.name : ""
                renaming = true
            })
            actions.append(SheetAction(label: who.avatar.isEmpty ? "Add a group photo" : "Change group photo") {
                photoMenu()
            })
            actions.append(SheetAction(label: "Leave group", danger: true) { confirmLeave = true })
            actions.append(SheetAction(label: "Delete chat", danger: true) { confirmDelete = true })
            actions.append(SheetAction(label: "Report this chat", danger: true) {
                reporting = ReportTarget(subject: .group(id: who.with))
            })
        } else {
            actions.append(SheetAction(label: "View profile") {
                social.openPerson(handle: who.handle, userID: who.with, name: who.name, avatar: who.avatar)
            })
            actions.append(SheetAction(label: "Delete chat", danger: true) { confirmDelete = true })
            if !who.handle.isEmpty {
                let name = who.name.isEmpty ? "@" + who.handle : who.name
                let handle = who.handle
                actions.append(SheetAction(label: "Report " + name, danger: true) {
                    reporting = ReportTarget(subject: .person(handle: handle))
                })
                actions.append(SheetAction(label: "Block " + name, danger: true) {
                    askBlock(name: name, handle: handle, member: false)
                })
            }
        }
        sheet = SheetSpec(actions: actions)
    }

    private func askBlock(name: String, handle: String, member: Bool) {
        blockName = name
        blockHandle = handle
        blockingMember = member
        blocking = true
    }

    /// After a block: "Blocked", the graph and inbox re-read; a 1:1 chat
    /// closes, a group stays without their lines.
    private func blocked(member: Bool) {
        model.nowPlaying.say("Blocked")
        Task {
            await social.loadGraph()
            await social.loadInbox()
        }
        if member {
            Task { await chat.load() }
        } else {
            social.back()
        }
    }

    private func deleteChat() {
        let with = who.with
        Task {
            do {
                let answer = try await model.api.deleteChat(with: with)
                social.dropThread(with, unread: answer.unread)
                social.back()
                model.nowPlaying.say("Chat deleted")
            } catch {
                model.nowPlaying.say(SocialModel.sentence(error, else: "Could not delete that chat"))
            }
        }
    }

    private func leaveGroup() {
        let gid = who.with
        Task {
            do {
                let answer = try await model.api.leaveGroup(gid)
                social.dropThread(gid, unread: answer.unread)
                social.back()
                model.nowPlaying.say("You left the group")
            } catch {
                model.nowPlaying.say(SocialModel.sentence(error, else: "Could not leave that group"))
            }
        }
    }

    private func rename() {
        let name = String(newName.trimmingCharacters(in: .whitespacesAndNewlines).prefix(60))
        let gid = who.with
        Task {
            do {
                let answer = try await model.api.renameGroup(gid, name: name)
                if let g = answer.group { chat.apply(g) }
            } catch {
                model.nowPlaying.say(SocialModel.sentence(error, else: "Could not rename it"))
            }
        }
    }

    // MARK: - The group photo (shot 26)

    private func photoMenu() {
        var actions = [SheetAction(label: "Choose from library") { pickingLibrary = true }]
        if UIImagePickerController.isSourceTypeAvailable(.camera) {
            actions.append(SheetAction(label: "Take a photo") { takingPhoto = true })
        }
        actions.append(SheetAction(label: "Choose a file") { pickingFile = true })
        if !who.avatar.isEmpty {
            actions.append(SheetAction(label: "Remove photo", danger: true) { setPhoto("") })
        }
        sheet = SheetSpec(header: who.avatar.isEmpty ? "Add a group photo" : "Change the group photo",
                          actions: actions)
    }

    private func picked(file result: Result<URL, Error>) {
        guard case let .success(url) = result else {
            model.nowPlaying.say("Could not read that file")
            return
        }
        let open = url.startAccessingSecurityScopedResource()
        defer { if open { url.stopAccessingSecurityScopedResource() } }
        guard let data = try? Data(contentsOf: url) else {
            model.nowPlaying.say("Could not read that file")
            return
        }
        upload(data)
    }

    private func upload(_ data: Data) {
        guard let url = GroupPhoto.dataURL(from: data) else {
            model.nowPlaying.say("That file is not an image FAM can read")
            return
        }
        setPhoto(url)
    }

    /// The server checks the photo (Anthropic) and may refuse in a sentence.
    private func setPhoto(_ avatar: String) {
        let gid = who.with
        Task {
            do {
                let answer = try await model.api.setGroupPhoto(gid, avatar: avatar)
                if let g = answer.group { chat.apply(g) }
            } catch {
                model.nowPlaying.say(SocialModel.sentence(error, else: "Could not change the photo"))
            }
        }
    }

    // MARK: - See who's here (shot 25)

    private var membersSheet: some View {
        GroupMembersSheet(name: displayName, members: who.members,
                          open: { m in
                              social.openPerson(handle: m.handle, name: m.name, avatar: m.avatar)
                          },
                          report: { m in reporting = ReportTarget(subject: .person(handle: m.handle)) },
                          block: { m in
                              askBlock(name: m.name.isEmpty ? "@" + m.handle : m.name, handle: m.handle, member: true)
                          })
            .environmentObject(model)
    }
}

/// The confirmations, as dialogs with a title and a normal-case message
/// (quirk 3), kept out of `ChatScreen.body` so it type-checks quickly.
private struct ChatConfirmations: ViewModifier {
    @ObservedObject var chat: ChatModel
    @Binding var confirmDelete: Bool
    @Binding var confirmLeave: Bool
    let first: String
    let deleteChat: () -> Void
    let leaveGroup: () -> Void

    func body(content: Content) -> some View {
        content
            .confirmationDialog("Delete this chat?", isPresented: $confirmDelete, titleVisibility: .visible) {
                Button("Delete chat", role: .destructive) { deleteChat() }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("It goes from your messages only \u{2014} \(chat.who.group ? "everybody else" : first) still has it.")
            }
            .confirmationDialog("Leave \(chat.who.name.isEmpty ? "this group" : chat.who.name)?",
                                isPresented: $confirmLeave, titleVisibility: .visible) {
                Button("Leave group", role: .destructive) { leaveGroup() }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("You won\u{2019}t get its messages any more, and the others are told.")
            }
    }
}

extension ChatModel {
    /// A group's new name or photo, from the server's answer.
    func apply(_ w: ChatWith) {
        var next = who
        if let n = w.name, !n.isEmpty { next.name = n }
        next.avatar = w.avatar ?? next.avatar
        next.named = w.named ?? next.named
        if let m = w.members, !m.isEmpty { next.members = m }
        if let warn = w.warning { next.warning = warn }
        who = next
    }
}

// MARK: - One line of a conversation

struct ChatLine: Identifiable {
    let row: ChatModel.Row
    /// The day separator above it, or "".
    let day: String
    /// A group's sender name over it, or "".
    let from: String
    var id: String { row.id }
}

/// A row as the web draws it (`renderThreadBody`).
struct ChatLineView: View {
    let line: ChatLine
    let group: Bool
    let otherFirst: String
    let saved: Bool
    let listen: () -> Void
    let goDeeper: () -> Void
    let save: () -> Void
    let report: () -> Void

    private var m: ChatMessage { line.row.message }
    private var mine: Bool { m.mine == true }
    private var text: String { m.text ?? "" }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if !line.day.isEmpty {
                Text(line.day)
                    .font(FAMFont.mono(9))
                    .tracking(0.36)
                    .foregroundStyle(FAMColor.textFaint)
                    .frame(maxWidth: .infinity)
                    .padding(.bottom, 10)
            }
            if !line.from.isEmpty {
                Text(line.from)
                    .font(FAMFont.mono(9.5))
                    .tracking(0.57)
                    .foregroundStyle(FAMColor.textMuted)
                    .padding(.top, 6)
                    .padding(.bottom, 2)
                    .padding(.leading, 4)
            }
            content
        }
        .opacity(line.row.pending ? 0.55 : 1)
    }

    @ViewBuilder private var content: some View {
        if m.kind == "system" {
            Text(systemText)
                .font(FAMFont.body(11))
                .foregroundStyle(FAMColor.textMuted)
                .multilineTextAlignment(.center)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 8)
        } else if m.kind == "episode" {
            episode
        } else {
            side { reportable(TextBubble(text: text, mine: mine)) }
                .padding(.bottom, 10)
        }
    }

    /// "You named the group Book club", "Theo changed the group photo".
    private var systemText: String {
        let who = mine ? "You" : socialFirstName(m.from?.name ?? "", handle: m.from?.handle ?? "")
        return who + " " + text
    }

    /// The card, then the words or the big heart on the same side, then
    /// the receipt for an episode sent to you that you finished.
    @ViewBuilder private var episode: some View {
        side { reportable(SharedEpisodeCard(message: m, mine: mine, label: cardLabel, saved: saved,
                                      listen: listen, goDeeper: goDeeper, save: save)) }
            .padding(.bottom, 10)
        if text == FAMAPI.storyLike {
            side {
                Text(FAMAPI.storyLike)
                    .font(.system(size: 30))
                    .padding(.horizontal, 4)
                    .accessibilityLabel("Liked")
            }
            .padding(.bottom, 10)
        } else if !text.isEmpty {
            side { TextBubble(text: text, mine: mine) }
                .padding(.bottom, 10)
        }
        if m.finished == true {
            HStack {
                Spacer(minLength: 0)
                HStack(spacing: 6) {
                    WebIcon(path: Icons.check, size: 13)
                        .foregroundStyle(FAMColor.sage)
                    Text("You finished it \u{00B7} \(FAMAPI.minutes(m.minutes ?? FAMAPI.browseMinutes)) min")
                        .font(FAMFont.body(10))
                        .foregroundStyle(FAMColor.textMuted)
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(FAMColor.surface, in: Capsule())
                .overlay(Capsule().stroke(FAMColor.borderStrong, style: StrokeStyle(lineWidth: 1, dash: [4, 3])))
            }
            .padding(.top, -2)
            .padding(.bottom, 10)
        }
    }

    /// "YOU SHARED AN EPISODE", "BETH REPLIED TO YOUR VIBE", "RACHEL LIKED
    /// YOUR VIBE" - in a group, the sender's first name.
    private var cardLabel: String {
        let like = text == FAMAPI.storyLike
        if mine {
            if like { return "You liked their vibe" }
            return text.isEmpty ? "You shared an episode" : "You replied to their vibe"
        }
        let name = group ? socialFirstName(m.from?.name ?? "", handle: m.from?.handle ?? "") : otherFirst
        if like { return name + " liked your vibe" }
        return name + (text.isEmpty ? " shared an episode" : " replied to your vibe")
    }

    /// Theirs on the left, yours on the right, never wider than 75%.
    private func side<Inner: View>(@ViewBuilder _ inner: () -> Inner) -> some View {
        HStack(spacing: 0) {
            if mine { Spacer(minLength: 88) }
            inner()
            if !mine { Spacer(minLength: 88) }
        }
    }

    /// Held down, somebody else's message can be reported (quirk 1).
    @ViewBuilder private func reportable<Inner: View>(_ inner: Inner) -> some View {
        if !mine && m.id > 0 {
            inner.contextMenu {
                Button(role: .destructive, action: report) {
                    Label("Report", systemImage: "flag")
                }
            }
        } else {
            inner
        }
    }
}

/// `.msg-bubble`: Geist 12.5 / 17.5, padding 9/13, radius 15 with the tail
/// corner 5; theirs on surface-2, yours copper with ink at 500.
struct TextBubble: View {
    let text: String
    let mine: Bool

    var body: some View {
        Text(text)
            .font(FAMFont.body(12.5, mine ? .medium : .regular))
            .foregroundStyle(mine ? FAMColor.ink : FAMColor.text)
            .lineSpacing(5)
            .padding(.horizontal, 13)
            .padding(.vertical, 9)
            .background(mine ? FAMColor.copper : FAMColor.surface2, in: ChatShape.bubble(mine: mine))
            .textSelection(.enabled)
    }
}

enum ChatShape {
    /// Radius 15, the tail corner 5 on the sender's side.
    static func bubble(mine: Bool) -> UnevenRoundedRectangle {
        UnevenRoundedRectangle(topLeadingRadius: 15, bottomLeadingRadius: mine ? 15 : 5,
                               bottomTrailingRadius: mine ? 5 : 15, topTrailingRadius: 15,
                               style: .continuous)
    }
}

/// `.yf-ep`: the copper label with the share mark, then the card - meta,
/// the title, Listen / GO DEEPER / save - at most 236 wide.
struct SharedEpisodeCard: View {
    let message: ChatMessage
    let mine: Bool
    let label: String
    let saved: Bool
    let listen: () -> Void
    let goDeeper: () -> Void
    let save: () -> Void

    private var meta: String {
        let topic = message.topic ?? ""
        let minutes = FAMAPI.minutes(message.minutes ?? FAMAPI.browseMinutes) + " min"
        return topic.isEmpty ? minutes : topic + " \u{00B7} " + minutes
    }

    private var title: String {
        let t = message.title ?? ""
        return t.isEmpty ? (message.query ?? "") : t
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(spacing: 5) {
                WebIcon(path: Icons.menuShare, size: 12)
                Text(label).monoLabel(9, color: FAMColor.copper, tracking: 0.36)
            }
            .foregroundStyle(FAMColor.copper)
            card
        }
        .frame(maxWidth: 236, alignment: .leading)
    }

    private var card: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(meta).monoLabel(8.5, tracking: 0.68)
            Text(title)
                .font(FAMFont.heading(15.5, .medium))
                .foregroundStyle(FAMColor.text)
                .lineSpacing(1.5)
                .fixedSize(horizontal: false, vertical: true)
            actions
        }
        .padding(11)
        .frame(maxWidth: 236, alignment: .leading)
        .background(FAMColor.surface2, in: ChatShape.bubble(mine: mine))
        .overlay(ChatShape.bubble(mine: mine).stroke(Color(red: 30 / 255, green: 27 / 255, blue: 39 / 255).opacity(0.14)))
    }

    private var actions: some View {
        HStack(spacing: 6) {
            Button(action: listen) {
                HStack(spacing: 7) {
                    WebFill(path: Icons.rowPlay, size: 11)
                    Text("Listen").font(FAMFont.body(11, .bold))
                }
                .foregroundStyle(FAMColor.ink)
                .padding(.horizontal, 11)
                .frame(height: 30)
                .background(FAMColor.copper, in: Capsule())
            }
            .buttonStyle(.plain)
            Button(action: goDeeper) {
                Text("GO DEEPER")
                    .font(FAMFont.body(11, .medium))
                    .foregroundStyle(FAMColor.text)
                    .padding(.horizontal, 11)
                    .frame(height: 30)
                    .overlay(Capsule().stroke(FAMColor.borderStrong))
            }
            .buttonStyle(.plain)
            Button(action: save) {
                ZStack {
                    if saved {
                        Glyph(path: Icons.bookmark, box: CGRect(x: 0, y: 0, width: 24, height: 24))
                            .fill(FAMColor.copper)
                            .frame(width: 14, height: 14)
                    }
                    WebIcon(path: Icons.bookmark, size: 14)
                        .foregroundStyle(saved ? FAMColor.copper : FAMColor.text)
                }
                .frame(width: 30, height: 30)
                .overlay(Circle().stroke(saved ? FAMColor.copper : FAMColor.borderStrong))
            }
            .buttonStyle(.plain)
            .accessibilityLabel(saved ? "Remove from Saved for Later" : "Save for later")
        }
    }
}

/// `.msg-typing`: three 6 pt dots in an incoming bubble, rising 3 pt and
/// brightening in turn every 1.2 s (static at .7 with reduced motion).
struct TypingBubble: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 30, paused: reduceMotion)) { context in
            let t = context.date.timeIntervalSinceReferenceDate
            HStack(spacing: 4) {
                ForEach(0..<3, id: \.self) { i in
                    let k = reduceMotion ? 0.55 : TypingBubble.pulse(t - Double(i) * 0.15)
                    Circle()
                        .fill(FAMColor.textMuted)
                        .frame(width: 6, height: 6)
                        .offset(y: -3 * k)
                        .opacity(0.35 + 0.65 * k)
                }
            }
        }
        .padding(.horizontal, 14)
        .frame(width: 54, height: 30)
        .background(FAMColor.surface2, in: ChatShape.bubble(mine: false))
        .padding(.bottom, 10)
        .accessibilityLabel("Typing")
    }

    /// 0 → 1 → 0 over the first half of each 1.2 s beat.
    static func pulse(_ t: Double) -> Double {
        let beat = 1.2
        var p = t.truncatingRemainder(dividingBy: beat) / beat
        if p < 0 { p += 1 }
        guard p < 0.6 else { return 0 }
        let x = p / 0.6
        return x < 0.5 ? x * 2 : (1 - x) * 2
    }
}

/// See who's here (shot 25), without the web's inert "You" row (quirk 2):
/// each other member opens their profile, and each has Report and Block -
/// the web has neither for a group's people (1.2).
struct GroupMembersSheet: View {
    @Environment(\.dismiss) private var dismiss
    let name: String
    let members: [ChatMember]
    let open: (ChatMember) -> Void
    let report: (ChatMember) -> Void
    let block: (ChatMember) -> Void
    @State private var acting: ChatMember?

    private var others: [ChatMember] { members.filter { !$0.me } }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SheetGroupLabel(text: "\(name) \u{00B7} \(members.count) \(members.count == 1 ? "person" : "people")")
            ScrollView {
                VStack(spacing: 0) {
                    ForEach(others, id: \.self) { m in row(m) }
                }
            }
            SheetCancelButton { dismiss() }
        }
        .padding(.top, 10)
        .padding(.bottom, 12)
        .background(FAMColor.surface)
        .presentationDetents([.medium, .large])
        .presentationBackground(FAMColor.surface)
        .confirmationDialog(acting.map { $0.name.isEmpty ? "@" + $0.handle : $0.name } ?? "",
                            isPresented: Binding(get: { acting != nil }, set: { if !$0 { acting = nil } }),
                            titleVisibility: .visible) {
            if let m = acting {
                Button("View profile") { after { open(m) } }
                Button("Report", role: .destructive) { after { report(m) } }
                Button("Block", role: .destructive) { after { block(m) } }
                Button("Cancel", role: .cancel) {}
            }
        }
    }

    private func row(_ m: ChatMember) -> some View {
        HStack(spacing: 12) {
            Button { after { open(m) } } label: {
                HStack(spacing: 12) {
                    Face(name: m.name, handle: m.handle, avatar: m.avatar, size: 34, initialsSize: 12)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(m.name.isEmpty ? "@" + m.handle : m.name)
                            .font(FAMFont.body(13.5, .semibold))
                            .foregroundStyle(FAMColor.text)
                            .lineLimit(1)
                        if !m.handle.isEmpty {
                            Text("@" + m.handle)
                                .font(FAMFont.body(11))
                                .foregroundStyle(FAMColor.textMuted)
                        }
                    }
                    Spacer(minLength: 0)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(m.handle.isEmpty)
            if !m.handle.isEmpty {
                Button { acting = m } label: {
                    RoundIcon(size: 30) { WebFill(path: Icons.dots(1.6), size: 15) }
                }
                .buttonStyle(.plain)
                .accessibilityLabel("More about \(m.name.isEmpty ? m.handle : m.name)")
            }
        }
        .padding(.horizontal, 22)
        .padding(.vertical, 9)
    }

    /// Close the sheet, then act once it has gone.
    private func after(_ action: @escaping () -> Void) {
        dismiss()
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 450_000_000)
            action()
        }
    }
}

/// A group's photo as the web sends it: cut square from the middle, 256
/// pixels (`AVATAR_PX`), JPEG at .85, as a `data:` URL.
enum GroupPhoto {
    static let side: CGFloat = 256

    static func dataURL(from data: Data) -> String? {
        guard let source = UIImage(data: data), source.size.width > 0, source.size.height > 0 else { return nil }
        let edge = min(source.size.width, source.size.height)
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: side, height: side), format: format)
        let scale = side / edge
        let x = (source.size.width - edge) / 2
        let y = (source.size.height - edge) / 2
        let square = renderer.image { _ in
            source.draw(in: CGRect(x: -x * scale, y: -y * scale,
                                   width: source.size.width * scale, height: source.size.height * scale))
        }
        guard let jpeg = square.jpegData(compressionQuality: 0.85) else { return nil }
        return "data:image/jpeg;base64," + jpeg.base64EncodedString()
    }
}

/// "Take a photo": the camera, handing back the picture's data.
struct CameraPicker: UIViewControllerRepresentable {
    let done: (Data?) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(done: done) }

    func makeUIViewController(context: Context) -> UIImagePickerController {
        let picker = UIImagePickerController()
        picker.sourceType = .camera
        picker.delegate = context.coordinator
        return picker
    }

    func updateUIViewController(_ vc: UIImagePickerController, context: Context) {}

    final class Coordinator: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
        let done: (Data?) -> Void

        init(done: @escaping (Data?) -> Void) { self.done = done }

        func imagePickerController(_ picker: UIImagePickerController,
                                   didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]) {
            let image = info[.originalImage] as? UIImage
            done(image?.jpegData(compressionQuality: 0.9))
        }

        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) {
            done(nil)
        }
    }
}
