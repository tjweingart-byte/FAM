import SwiftUI

/// The Messages tab (`#screen-messages`, shots 01-06): "Messages" and the
/// gold (+), Search messages (in memory, no request), then a row a
/// conversation - 1:1 or group - newest first, unread in the text colour
/// with a copper dot. Re-read every time back lands on it, and when a
/// message arrives while it is open (`openMessages`, `goBack`).
struct MessagesView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var social: SocialModel
    @State private var search = ""
    @State private var newChat = false

    var body: some View {
        NavigationStack(path: $social.messagesPath) {
            VStack(spacing: 0) {
                bar
                searchField
                ScrollView {
                    list
                        .padding(.horizontal, 16)
                        .padding(.top, 2)
                        .padding(.bottom, 18)
                }
                .refreshable { await social.loadInbox() }
            }
            .background(FAMColor.background)
            .toolbar(.hidden, for: .navigationBar)
            .navigationDestination(for: SocialRoute.self) { route in
                SocialScreen(route: route)
            }
            .onAppear { Task { await social.loadInbox() } }
        }
        .fullScreenCover(isPresented: $newChat) {
            NewMessageCard()
                .environmentObject(model)
                .environmentObject(social)
        }
    }

    // MARK: The bar (`.yf-bar`)

    private var bar: some View {
        HStack(spacing: 6) {
            Text("Messages")
                .font(FAMFont.heading(23, .medium))
                .foregroundStyle(FAMColor.text)
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityAddTraits(.isHeader)
            Button { newChat = true } label: {
                RoundIcon(gold: true) { WebIcon(path: Icons.plus, size: 17, line: 2.4) }
            }
            .buttonStyle(.plain)
            .accessibilityLabel("New message")
        }
        .padding(.horizontal, 16)
        .padding(.top, 12)
        .padding(.bottom, 4)
    }

    /// `.yf-search`: 40 tall, radius 13, surface, a hairline.
    private var searchField: some View {
        HStack(spacing: 8) {
            WebIcon(path: Icons.magnifier, size: 15)
                .foregroundStyle(FAMColor.textMuted)
            TextField("", text: $search, prompt: Text("Search messages").foregroundColor(FAMColor.textMuted))
                .font(FAMFont.body(12.5))
                .foregroundStyle(FAMColor.text)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .submitLabel(.search)
        }
        .padding(.horizontal, 12)
        .frame(height: 40)
        .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 13))
        .overlay(RoundedRectangle(cornerRadius: 13).stroke(FAMColor.border))
        .padding(.horizontal, 16)
        .padding(.top, 10)
        .padding(.bottom, 4)
    }

    // MARK: The list and its states

    @ViewBuilder private var list: some View {
        let shown = filtered
        if social.threads.isEmpty {
            switch social.inboxState {
            case .idle, .loading:
                SocialNote(text: "Loading\u{2026}")
            case .failed:
                SocialNote(text: "Could not load your messages.")
            case .loaded:
                emptyInbox
            }
        } else if shown.isEmpty {
            SocialNote(text: "Nothing in your messages matches that.")
        } else {
            LazyVStack(spacing: 0) {
                ForEach(shown) { t in
                    Button { social.open(.thread(SocialModel.chat(from: t))) } label: {
                        InboxRow(thread: t)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    /// The empty inbox, with the inline **Friends** that opens Friends.
    private var emptyInbox: some View {
        SocialNote(text: "No conversations yet. Share an episode with someone from any player, or find them under Friends.") {
            Button { social.open(.friends) } label: {
                Text("Friends")
                    .font(FAMFont.body(11, .bold))
                    .foregroundStyle(FAMColor.text)
                    .underline()
            }
            .buttonStyle(.plain)
            .accessibilityHint("Opens Friends")
        }
    }

    /// Name, handle and the last line's words, without case.
    private var filtered: [InboxThread] {
        let q = search.trimmingCharacters(in: .whitespaces).lowercased()
        guard !q.isEmpty else { return social.threads }
        return social.threads.filter { t in
            let last = t.last
            let parts: [String] = [t.name ?? "", t.handle ?? "", last?.text ?? "", last?.title ?? "",
                                   last?.query ?? "", last?.topic ?? ""]
            return parts.joined(separator: " ").lowercased().contains(q)
        }
    }
}

/// `.thread-row`: 61 tall, a 40 pt face, the name (Geist 13.5 bold) and the
/// time (Geist Mono 9 faint) on one line, the preview (Geist 11.5) under
/// it - led by the share mark for an episode - and the copper dot unread.
struct InboxRow: View {
    let thread: InboxThread

    private var unread: Int { thread.unread ?? 0 }

    var body: some View {
        HStack(spacing: 12) {
            Face(name: thread.name ?? "", handle: thread.handle ?? "", avatar: thread.avatar ?? "",
                 size: 40, initialsSize: 14)
            VStack(alignment: .leading, spacing: 2) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    HStack(spacing: 5) {
                        Text(name)
                            .font(FAMFont.body(13.5, .bold))
                            .foregroundStyle(FAMColor.text)
                            .lineLimit(1)
                        if thread.explicit == true { ExplicitBadge() }
                    }
                    Spacer(minLength: 0)
                    if let at = thread.last?.at {
                        Text(SocialTime.when(at))
                            .font(FAMFont.mono(9))
                            .foregroundStyle(FAMColor.textFaint)
                    }
                }
                HStack(spacing: 5) {
                    if thread.last?.kind == "episode" {
                        WebIcon(path: Icons.menuShare, size: 13)
                            .foregroundStyle(FAMColor.copper)
                    }
                    Text(InboxRow.preview(thread))
                        .font(FAMFont.body(11.5))
                        .foregroundStyle(unread > 0 ? FAMColor.text : FAMColor.textMuted)
                        .lineLimit(1)
                }
            }
            if unread > 0 {
                Circle().fill(FAMColor.copper).frame(width: 8, height: 8)
                    .accessibilityLabel("\(unread) unread")
            }
        }
        .padding(.vertical, 10)
        .frame(minHeight: 61)
        .overlay(alignment: .bottom) { Rectangle().fill(FAMColor.border).frame(height: 1) }
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }

    private var name: String {
        let n = thread.name ?? ""
        if !n.isEmpty { return n }
        let h = thread.handle ?? ""
        return h.isEmpty ? "Someone" : "@" + h
    }

    /// `renderThreadRows`: a group's lead ("You: " / "Rachel: "), an
    /// episode's "Liked a vibe" / "Replied to a vibe: …" / "Shared an
    /// episode" and its subject, a group's system line with who did it.
    static func preview(_ t: InboxThread) -> String {
        guard let last = t.last else { return "" }
        let group = t.group == true
        let kind = last.kind ?? "text"
        let text = last.text ?? ""
        let from = last.fromName ?? ""
        if kind == "system" && group {
            let who = last.mine == true ? "You" : (from.isEmpty ? "Someone" : from)
            return who + " " + text
        }
        var lead = ""
        if group {
            if last.mine == true { lead = "You: " } else if !from.isEmpty { lead = from + ": " }
        }
        if kind == "episode" {
            var body = "Shared an episode"
            if text == FAMAPI.storyLike { body = "Liked a vibe" } else if !text.isEmpty { body = "Replied to a vibe: " + text }
            let topic = last.topic ?? ""
            return lead + body + (topic.isEmpty ? "" : " \u{00B7} " + topic)
        }
        return lead + text
    }
}

/// New message (`#newChatOverlay`, shots 08-10): pick one or more friends
/// (mutual follows only, §523) - one is a chat, two or more a group with an
/// optional name -
/// or find somebody by name or @handle and pick from what comes back (the
/// web says "Name or phone number" and opens the first hit blindly;
/// quirk 6).
struct NewMessageCard: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var social: SocialModel
    @Environment(\.dismiss) private var dismiss
    @State private var picked: [String] = []
    @State private var groupName = ""
    @State private var typed = ""
    @State private var found: [FollowPerson] = []
    @State private var looked = false
    @State private var working = false

    private var people: [FollowPerson] { social.reachable }

    var body: some View {
        ModalCard(close: { dismiss() }) {
            Text("New message")
                .font(FAMFont.heading(16, .medium))
                .foregroundStyle(FAMColor.text)
                .padding(.bottom, 12)
            Text("Pick one or more friends").monoLabel(9.5, tracking: 0.76)
                .padding(.bottom, 8)
            faces
            if !pickedLine.isEmpty {
                Text(pickedLine)
                    .font(FAMFont.body(12))
                    .foregroundStyle(FAMColor.textMuted)
                    .lineLimit(2)
                    .padding(.top, 10)
            }
            if picked.count >= 2 {
                ModalField(placeholder: "Name the group (optional)", text: $groupName)
                    .onChange(of: groupName) { _, v in if v.count > 60 { groupName = String(v.prefix(60)) } }
                    .padding(.top, 10)
            }
            Text("Or start a new conversation").monoLabel(9.5, tracking: 0.76)
                .padding(.top, 16)
                .padding(.bottom, 8)
            ModalField(placeholder: "Name or @handle", text: $typed, plain: true)
                .task(id: typed) { await lookUp() }
            results
            HStack(spacing: 8) {
                Spacer()
                ModalButton(label: "Cancel") { dismiss() }
                ModalButton(label: picked.count >= 2 ? "Start group" : "Start chat", primary: true,
                            enabled: !working) { confirm() }
            }
            .padding(.top, 16)
        }
        .task { if social.graph == nil { await social.loadGraph() } }
    }

    /// `.share-contacts`: 56 wide, a 40 pt face, the first name; picked
    /// with a copper ring and a copper ✓. A tap only picks.
    @ViewBuilder private var faces: some View {
        if people.isEmpty {
            Text(emptyLine)
                .font(FAMFont.body(11))
                .foregroundStyle(FAMColor.textMuted)
                .fixedSize(horizontal: false, vertical: true)
        } else {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(alignment: .top, spacing: 12) {
                    ForEach(people) { p in
                        Button { toggle(p.userId) } label: { PickFace(person: p, on: picked.contains(p.userId)) }
                            .buttonStyle(.plain)
                    }
                }
                .padding(.vertical, 3)
                .padding(.horizontal, 3)
            }
            .padding(.horizontal, -3)
        }
    }

    private var emptyLine: String {
        switch social.graphState {
        case .idle, .loading: return "Loading\u{2026}"
        case .failed: return "Could not load your people."
        case .loaded: return SocialModel.nobodyToMessage
        }
    }

    /// "" / "Chat with Beth" / "Group with Beth, Rachel".
    private var pickedLine: String {
        let names = picked.compactMap { id in people.first { $0.userId == id } }
            .map { socialFirstName($0.name ?? "", handle: $0.handle ?? "") }
        if names.isEmpty { return "" }
        if names.count == 1 { return "Chat with " + names[0] }
        return "Group with " + names.joined(separator: ", ")
    }

    /// Who a typed name or @handle finds, to pick from.
    @ViewBuilder private var results: some View {
        if !found.isEmpty {
            VStack(spacing: 0) {
                ForEach(found.prefix(5)) { p in
                    Button { go(SocialModel.chat(with: p)) } label: {
                        HStack(spacing: 10) {
                            Face(name: p.name ?? "", handle: p.handle ?? "", avatar: p.avatar ?? "",
                                 size: 30, initialsSize: 11)
                            VStack(alignment: .leading, spacing: 2) {
                                Text((p.name ?? "").isEmpty ? "@" + (p.handle ?? "") : (p.name ?? ""))
                                    .font(FAMFont.body(12.5, .semibold))
                                    .foregroundStyle(FAMColor.text)
                                    .lineLimit(1)
                                if let h = p.handle, !h.isEmpty {
                                    Text("@" + h).font(FAMFont.mono(9)).foregroundStyle(FAMColor.textMuted)
                                }
                            }
                            Spacer(minLength: 0)
                        }
                        .padding(.vertical, 7)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.top, 8)
        } else if looked {
            Text("Nobody with that handle")
                .font(FAMFont.body(11))
                .foregroundStyle(FAMColor.textMuted)
                .padding(.top, 8)
        }
    }

    private func toggle(_ id: String) {
        if let at = picked.firstIndex(of: id) { picked.remove(at: at) } else { picked.append(id) }
    }

    /// 260 ms after the last letter, two letters at least (`@` stripped).
    private func lookUp() async {
        let q = Self.clean(typed)
        guard q.count >= 2 else {
            found = []
            looked = false
            return
        }
        try? await Task.sleep(nanoseconds: 260_000_000)
        if Task.isCancelled { return }
        let answer = (try? await model.api.people(q)) ?? []
        if Task.isCancelled { return }
        found = answer
        looked = true
    }

    private static func clean(_ text: String) -> String {
        var q = text.trimmingCharacters(in: .whitespaces)
        if q.hasPrefix("@") { q = String(q.dropFirst()) }
        return q
    }

    /// `confirmNewChat`: two or more picked start a group; one opens that
    /// conversation (nothing is sent until the first message); none looks
    /// the typed words up.
    private func confirm() {
        if picked.count >= 2 {
            working = true
            let ids = picked
            let name = groupName.trimmingCharacters(in: .whitespaces)
            Task {
                do {
                    let made = try await model.api.startGroup(userIDs: ids, name: name)
                    working = false
                    if let g = made.group, let id = g.userId {
                        go(ChatOpen(with: id, name: g.name ?? "", avatar: g.avatar ?? "", group: true,
                                    named: g.named ?? false, members: g.members ?? [], loaded: true))
                    }
                } catch {
                    working = false
                    model.nowPlaying.say(SocialModel.sentence(error, else: "Could not start that group"))
                }
            }
            return
        }
        if let id = picked.first, let p = people.first(where: { $0.userId == id }) {
            go(SocialModel.chat(with: p))
            return
        }
        let q = Self.clean(typed)
        guard q.count >= 2 else {
            model.nowPlaying.say("Type at least two letters of a handle")
            return
        }
        if found.count == 1 { go(SocialModel.chat(with: found[0])); return }
        Task {
            do {
                let answer = try await model.api.people(q)
                found = answer
                looked = true
                if answer.count == 1 { go(SocialModel.chat(with: answer[0])) }
                if answer.isEmpty { model.nowPlaying.say("Nobody with that handle") }
            } catch {
                model.nowPlaying.say("Could not look that up")
            }
        }
    }

    private func go(_ chat: ChatOpen) {
        // One-to-one only with a friend (§523): the server's own sentence,
        // before a conversation that could not send opens.
        if !chat.group && !social.isFriend(chat.with) {
            model.nowPlaying.say("You can message people who follow you back.")
            return
        }
        dismiss()
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 350_000_000)
            social.open(.thread(chat))
        }
    }
}

/// A face to pick (`.share-contact`): 56 wide, a 40 pt face, the first name
/// in Geist 9; picked: a 2 pt copper ring outside a 2 pt gap and a copper ✓.
struct PickFace: View {
    let name: String
    let handle: String
    let avatar: String
    let on: Bool
    var label: String? = nil

    init(person: FollowPerson, on: Bool) {
        name = person.name ?? ""
        handle = person.handle ?? ""
        avatar = person.avatar ?? ""
        self.on = on
    }

    init(name: String, handle: String, avatar: String, on: Bool, label: String) {
        self.name = name
        self.handle = handle
        self.avatar = avatar
        self.on = on
        self.label = label
    }

    var body: some View {
        VStack(spacing: 5) {
            Face(name: name, handle: handle, avatar: avatar, size: 40, initialsSize: 12)
                .padding(2)
                .overlay(Circle().stroke(on ? FAMColor.copper : Color.clear, lineWidth: 2))
                .overlay(alignment: .bottomTrailing) {
                    if on {
                        WebIcon(path: Icons.check, size: 10, line: 3)
                            .foregroundStyle(FAMColor.ink)
                            .frame(width: 18, height: 18)
                            .background(FAMColor.copper, in: Circle())
                            .overlay(Circle().stroke(FAMColor.surface, lineWidth: 2))
                            .offset(x: 4, y: 2)
                    }
                }
            Text(label ?? socialFirstName(name, handle: handle))
                .font(FAMFont.body(9, on ? .bold : .regular))
                .foregroundStyle(on ? FAMColor.copper : FAMColor.textMuted)
                .lineLimit(2)
                .multilineTextAlignment(.center)
                .frame(width: 56)
        }
        .frame(width: 56)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(on ? .isSelected : [])
    }
}
