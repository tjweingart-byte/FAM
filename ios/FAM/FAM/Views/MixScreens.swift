import PhotosUI
import SwiftUI

/// Name a new mix (`screen-newmix`, `mix-made-by-naming`): a cover, a name,
/// Create. Create makes it at once, empty, and opens it; topics are added
/// from its own page. No picker.
struct NewMixView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var mixes: MixesModel
    @Binding var path: [MixRoute]

    @State private var name = ""
    @State private var cover = ""
    @State private var photo: PhotosPickerItem?
    @State private var picking = false
    @State private var coverChoice = false
    @State private var pendingCover: String?
    @State private var working = false
    @FocusState private var focused: Bool

    private var trimmed: String { name.trimmingCharacters(in: .whitespacesAndNewlines) }

    var body: some View {
        VStack(spacing: 0) {
            // The back row with an X; the step label where a title would be,
            // beside it (`h2.nm-step`).
            HStack(spacing: 10) {
                Button { path.removeAll() } label: {
                    WebIcon(path: Icons.close, size: 20, line: 2.4)
                        .foregroundStyle(FAMColor.text)
                        .frame(width: 36, height: 36)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Cancel")
                Text("New mix")
                    .monoLabel(9.5, tracking: 1.14)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(.horizontal, 10)
            .frame(minHeight: 48)
            ScrollView {
                VStack(spacing: 0) {
                    Button { if cover.isEmpty { picking = true } else { coverChoice = true } } label: { folder }
                        .buttonStyle(.plain)
                        .accessibilityLabel("Choose a cover photo")
                        .padding(.top, 20)
                    Text(cover.isEmpty ? "Tap to add a cover photo" : "Tap to change the cover")
                        .monoLabel(8.5, color: FAMColor.copper, tracking: 0.85)
                        .padding(.top, 14)
                    Text("Name your mix")
                        .font(FAMFont.heading(24, .medium))
                        .foregroundStyle(FAMColor.text)
                        .padding(.top, 16)
                    Text("You’ll add topics once it’s made.")
                        .font(FAMFont.body(11.5))
                        .foregroundStyle(FAMColor.textMuted)
                        .multilineTextAlignment(.center)
                        .frame(maxWidth: 240)
                        .padding(.top, 6)
                    // The underline runs the whole width (`.nm-input`).
                    TextField("", text: $name,
                              prompt: Text("Morning commute").foregroundColor(FAMColor.textFaint.opacity(0.6)))
                        .font(FAMFont.heading(22, .medium))
                        .multilineTextAlignment(.center)
                        .autocorrectionDisabled()
                        .focused($focused)
                        .submitLabel(.done)
                        .onSubmit { Task { await create() } }
                        .onChange(of: name) { _, v in if v.count > 40 { name = String(v.prefix(40)) } }
                        .padding(.horizontal, 4)
                        .padding(.top, 6)
                        .padding(.bottom, 9)
                        .overlay(alignment: .bottom) {
                            Rectangle().fill(focused ? FAMColor.copper : FAMColor.borderStrong).frame(height: 1.5)
                        }
                        .padding(.horizontal, 18)
                        .padding(.top, 20)
                        .accessibilityLabel("Mix name")
                }
            }
            // Create, pinned under the scroller (`.np-save-btn.nm-create`).
            Button { Task { await create() } } label: {
                ZStack {
                    if working { ProgressView().tint(FAMColor.ink) } else { Text("Create") }
                }
                .font(.system(size: 13.5, weight: .bold))
                .foregroundStyle(FAMColor.ink)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 15)
                .background(FAMColor.copper, in: RoundedRectangle(cornerRadius: 14))
            }
            .buttonStyle(.plain)
            .disabled(trimmed.isEmpty || working)
            .opacity(trimmed.isEmpty ? 0.35 : 1)
            .padding(.horizontal, 18)
            .padding(.bottom, 18)
        }
        .background(FAMColor.background)
        .toolbar(.hidden, for: .navigationBar)
        .onAppear { DispatchQueue.main.asyncAfter(deadline: .now() + 0.08) { focused = true } }
        .photosPicker(isPresented: $picking, selection: $photo, matching: .images)
        .onChange(of: photo) { _, item in
            guard let item else { return }
            Task {
                photo = nil
                if let data = try? await item.loadTransferable(type: Data.self), let url = MixCover.dataURL(from: data) {
                    // Said before the photo is used, as on a mix's page:
                    // others see it, so it is checked.
                    pendingCover = url
                } else {
                    model.nowPlaying.say("That file is not an image FAM can read")
                }
            }
        }
        .alert("Use this photo as the cover?", isPresented: Binding(get: { pendingCover != nil },
                                                                     set: { if !$0 { pendingCover = nil } })) {
            Button("Cancel", role: .cancel) { pendingCover = nil }
            Button("Use photo") {
                cover = pendingCover ?? ""
                pendingCover = nil
            }
        } message: { Text(MixCover.note) }
        .confirmationDialog("Cover", isPresented: $coverChoice, titleVisibility: .hidden) {
            // The picker waits for the dialog to close, or it is dropped.
            Button("Choose a different photo") {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { picking = true }
            }
            Button("Remove cover", role: .destructive) { cover = "" }
            Button("Cancel", role: .cancel) {}
        } message: { Text(MixCover.note) }
    }

    private var folder: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 22).fill(FAMColor.paper)
            if let image = MixCover.image(cover) {
                Image(uiImage: image).resizable().scaledToFill()
                LinearGradient(stops: [.init(color: Color(red: 24 / 255, green: 21 / 255, blue: 31 / 255).opacity(0.78), location: 0),
                                       .init(color: Color(red: 24 / 255, green: 21 / 255, blue: 31 / 255).opacity(0), location: 0.58)],
                               startPoint: .bottom, endPoint: .top)
                VStack {
                    Spacer()
                    Text(trimmed.isEmpty ? "Your mix" : trimmed)
                        .font(FAMFont.heading(13, .semibold))
                        .foregroundStyle(Color(hex: 0xF4EFE4))
                        .lineLimit(1)
                        .padding(.horizontal, 10)
                        .padding(.bottom, 11)
                }
            } else {
                VStack(spacing: 10) {
                    WebIcon(path: Icons.folder, size: 34, line: 1.7).foregroundStyle(FAMColor.ink)
                    Text(trimmed.isEmpty ? "Your mix" : trimmed)
                        .font(FAMFont.heading(13, .semibold))
                        .foregroundStyle(FAMColor.ink.opacity(trimmed.isEmpty ? 0.4 : 1))
                        .lineLimit(1)
                        .padding(.horizontal, 10)
                }
            }
        }
        .frame(width: 128, height: 128)
        .clipShape(RoundedRectangle(cornerRadius: 22))
        .shadow(color: .black.opacity(0.18), radius: 12, y: 8)
        .overlay(alignment: .bottomTrailing) {
            CoverBadge(size: 30, glyph: 13, ring: 2.5).offset(x: 7, y: 7)
        }
    }

    private func create() async {
        guard !trimmed.isEmpty else { focused = true; return }
        if mixes.mixes.contains(where: { $0.isOwner && $0.name.lowercased() == trimmed.lowercased() }) {
            model.nowPlaying.say("You already have a mix called \(trimmed)")
            return
        }
        working = true
        defer { working = false }
        do {
            let mix = try await model.api.createMix(name: trimmed, cover: cover)
            mixes.keep(mix)
            path = [.mix(mix.id)]
        } catch {
            model.nowPlaying.say(MixesModel.sentence(error, else: "Could not save that mix"))
        }
    }
}

/// Add topics to mix (`screen-addtopics`): recommended subjects with nothing
/// typed; with words, the typed topic first, then teams and companies
/// inside subjects, then subjects. Each (+) or ✓ saves at once as an add or
/// remove operation, so quick taps and other members never undo each other.
struct AddTopicsView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var mixes: MixesModel
    @Environment(\.dismiss) private var dismiss
    let id: String
    @State private var words = ""

    private var mix: Mix? { mixes.mix(id) }
    private var typed: String { words.trimmingCharacters(in: .whitespacesAndNewlines) }

    var body: some View {
        VStack(spacing: 0) {
            MixHeader(title: "Add topics to mix", backLabel: "Back to the mix", back: { dismiss() }) { EmptyView() }
            // A white field with no magnifier, unlike the cream search
            // fields elsewhere (`.at-search .modal-input`).
            TextField("", text: $words,
                      prompt: Text("Search topics, or type anything").foregroundColor(FAMColor.textFaint))
                .font(FAMFont.body(13))
                .foregroundStyle(FAMColor.text)
                .textInputAutocapitalization(.sentences)
                .submitLabel(.done)
                .onSubmit { Task { await addTyped() } }
                .accessibilityLabel("Search topics, or type anything")
                .padding(.horizontal, 12)
                .frame(height: 41)
                .background(FAMColor.background, in: RoundedRectangle(cornerRadius: 10))
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(FAMColor.borderStrong))
                .padding(.horizontal, 18)
                .padding(.bottom, 16)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    if typed.isEmpty { suggestions } else { matches }
                }
                .padding(.horizontal, 18)
                .padding(.bottom, 30)
            }
        }
        .background(FAMColor.background)
        .toolbar(.hidden, for: .navigationBar)
        .task { await mixes.loadForPage() }
    }

    private func inMix(_ topicID: String) -> Bool {
        mix?.items.contains { $0.id.lowercased() == topicID.lowercased() } ?? false
    }

    @ViewBuilder
    private var suggestions: some View {
        if mixes.subjects.isEmpty {
            EmptyNote(text: "Topics could not load. Type one above to add it.")
        } else {
            Text(mixes.recommended?.personalised == true ? "Recommended for you" : "Topics to follow")
                .monoLabel(9, tracking: 1.08).padding(.top, 14).padding(.bottom, 8)
            ForEach(mixes.recommendations(for: nil, excludingMix: false).prefix(15)) { s in
                row(s, id: "f:" + s.id, title: s.label, sub: mixes.subjectSub(s), label: s.label)
            }
        }
    }

    @ViewBuilder
    private var matches: some View {
        let lower = typed.lowercased()
        let wordCount = typed.split(separator: " ").count
        SubjectRow(subject: nil, title: typed,
                   sub: wordCount > 4 ? "Add it · we’ll give it a short name" : "Add as your own topic",
                   added: false, label: "Add it to this mix") { Task { await addTyped() } }
            .contentShape(Rectangle())
            .onTapGesture { Task { await addTyped() } }
        if typed.count >= 2 {
            let picks = mixes.subjects.filter { $0.id != "sports" }.flatMap { s in
                (s.focusPicks ?? []).filter { $0.lowercased().contains(lower) }.map { (s, $0) }
            }.prefix(5)
            if !picks.isEmpty {
                Text("Teams, companies & more").monoLabel(9, tracking: 1.08).padding(.top, 14).padding(.bottom, 8)
                ForEach(Array(picks.enumerated()), id: \.offset) { _, hit in
                    row(hit.0, id: "f:\(hit.0.id)~\(Self.encode(hit.1))", title: "\(hit.0.label) · \(hit.1)",
                        sub: hit.0.label, label: hit.1)
                }
            }
        }
        let subjects = mixes.subjects.filter { $0.label.lowercased().contains(lower) }.prefix(12)
        if !subjects.isEmpty {
            Text("Topics").monoLabel(9, tracking: 1.08).padding(.top, 14).padding(.bottom, 8)
            ForEach(Array(subjects)) { s in
                row(s, id: "f:" + s.id, title: s.label, sub: mixes.facetLabel(s) ?? "Topic", label: s.label)
            }
        }
    }

    private func row(_ s: Subject, id topicID: String, title: String, sub: String, label: String) -> some View {
        let added = inMix(topicID)
        return SubjectRow(subject: s, title: title, sub: sub, added: added,
                          label: added ? "Remove \(title) from this mix" : "Add \(title) to this mix") {
            Task { await toggle(topicID, label: label) }
        }
    }

    private func toggle(_ topicID: String, label: String) async {
        guard let mix else { return }
        let adding = !inMix(topicID)
        if await mixes.update(id, [adding ? "add" : "remove": [topicID]]) != nil {
            model.nowPlaying.say(adding ? "Added \(label) to \(mix.name)" : "Removed \(label) from \(mix.name)")
        }
    }

    /// A typed topic is sent with no title: the server names it short
    /// (`mix-topic-title`).
    private func addTyped() async {
        let text = typed
        guard !text.isEmpty, let mix else { return }
        words = ""
        guard let fresh = await mixes.update(id, ["add": [["query": text]]]) else {
            words = text
            return
        }
        let squash = { (s: String) in s.lowercased().split(whereSeparator: \.isWhitespace).joined(separator: " ") }
        let saved = fresh.items.first { $0.custom && squash($0.query) == squash(text) }
        model.nowPlaying.say("Added “\(saved?.title ?? text)” to \(mix.name)")
    }

    /// JavaScript's `encodeURIComponent`, which the server's ids match:
    /// ASCII letters, digits and `-_.!~*'()` stay; everything else -
    /// accented letters included - is UTF-8 percent-encoded.
    static func encode(_ s: String) -> String {
        let allowed = CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.!~*'()")
        return s.addingPercentEncoding(withAllowedCharacters: allowed) ?? s
    }
}

/// Somebody else's public mix (`screen-publicmix`): who made it, Add to
/// myFAM, and today's edition to play - hearing it needs no account.
struct PublicMixView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let id: String
    @Binding var path: [MixRoute]
    @State private var mix: Mix?
    @State private var problem = ""

    var body: some View {
        VStack(spacing: 0) {
            MixHeader(title: mix?.name ?? "", back: { dismiss() }) { EmptyView() }
            ScrollView {
                if let mix {
                    VStack(alignment: .leading, spacing: 0) {
                        HStack(spacing: 14) {
                            MixArt(cover: mix.cover, size: 84, radius: 16)
                            VStack(alignment: .leading, spacing: 0) {
                                Text(mix.name).font(FAMFont.heading(17, .semibold)).foregroundStyle(FAMColor.text)
                                Text("\(MixesModel.briefings(mix.items.count)) · daily")
                                    .monoLabel(8.5, tracking: 0.51)
                                    .padding(.top, 5)
                                if let owner = mix.owner {
                                    Text("by " + ownerLine(owner))
                                        .font(FAMFont.body(11, .bold))
                                        .foregroundStyle(FAMColor.copper)
                                        .padding(.top, 7)
                                }
                            }
                            Spacer(minLength: 0)
                        }
                        .padding(.top, 8)
                        if mix.mine {
                            Text("This is one of your mixes").monoLabel(8.5, tracking: 0.425).padding(.top, 16)
                        } else {
                            AddMixButton(mix: mix, wide: true).padding(.top, 16)
                        }
                        if mix.items.isEmpty {
                            EmptyNote(text: "This mix is empty.").padding(.top, 16)
                        } else {
                            MixPlayButton(count: mix.items.count) { model.playMix(mix) }
                                .padding(.top, 18)
                            Text("Today’s edition · \(MixView.today)")
                                .monoLabel(8.5, tracking: 0.425)
                                .padding(.top, 16)
                                .padding(.bottom, 9)
                            ForEach(mix.items) { item in
                                publicRow(mix, item)
                            }
                        }
                    }
                    .padding(.horizontal, 18)
                    .padding(.bottom, 30)
                } else if !problem.isEmpty {
                    EmptyNote(text: problem).padding(18)
                }
            }
        }
        .background(FAMColor.background)
        .toolbar(.hidden, for: .navigationBar)
        .task {
            do {
                let found = try await model.api.publicMix(id)
                if found.mine { path = [.mix(found.id)] } else { mix = found }
            } catch {
                problem = MixesModel.sentence(error, else: "That mix is private or no longer exists.")
            }
        }
    }

    /// A topic of somebody else's mix (`.mix-topic`, no handle, no icon).
    private func publicRow(_ mix: Mix, _ item: MixItem) -> some View {
        HStack(spacing: 11) {
            VStack(alignment: .leading, spacing: 2) {
                Text(item.title).font(FAMFont.heading(12.5, .semibold)).foregroundStyle(FAMColor.text)
                Text(item.subtitle).font(FAMFont.body(9.5)).foregroundStyle(FAMColor.textMuted)
            }
            Spacer(minLength: 4)
            Button { model.playMix(mix, from: item.id) } label: {
                PlayTriangle().fill(FAMColor.text).frame(width: 11, height: 11)
                    .frame(width: 30, height: 30)
                    .overlay(Circle().stroke(FAMColor.borderStrong))
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Play this one")
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 11)
        .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(FAMColor.border))
        .padding(.bottom, 9)
    }

    private func ownerLine(_ owner: MixOwner) -> String {
        if let name = owner.name, !name.isEmpty {
            if let handle = owner.handle, !handle.isEmpty { return "\(name) · @\(handle)" }
            return name
        }
        return owner.label
    }
}

/// "Add to myFAM Playlist" from the player's ⋯, a DailyFAM card's ⋯ and a
/// story's ⋯ (`openAddToMix`): what the episode is about, then which mix -
/// or a new one named for it. Drawn as the web's action sheet (shot 09).
/// Callers check there is something to add before opening it; with no
/// query they say "Play something first" instead.
struct AddToMixSheet: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var mixes: MixesModel
    @Environment(\.dismiss) private var dismiss
    let query: String
    let title: String

    @State private var topic: EpisodeTopic?
    @State private var problem = ""
    @State private var naming = false
    @State private var newName = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let topic {
                SheetGroupLabel(text: "Add \(topic.label) to a myFAM mix")
                ScrollView {
                    VStack(spacing: 0) {
                        ForEach(mixes.mixes) { mix in
                            Button { Task { await add(to: mix, topic) } } label: { mixRow(mix) }
                                .buttonStyle(.plain)
                        }
                        Button { newName = topic.label; naming = true } label: { createRow }
                            .buttonStyle(.plain)
                    }
                }
            } else if !problem.isEmpty {
                Text(problem).font(FAMFont.body(13)).foregroundStyle(FAMColor.textMuted).padding(22)
            } else {
                ProgressView().frame(maxWidth: .infinity).padding(30)
            }
            SheetCancelButton { dismiss() }
        }
        .padding(.top, 10)
        .padding(.bottom, 12)
        .background(FAMColor.surface)
        .presentationDetents([.medium, .large])
        .presentationBackground(FAMColor.surface)
        .task { await load() }
        .alert("Name your new playlist", isPresented: $naming) {
            TextField("e.g. Morning commute", text: $newName)
            Button("Cancel", role: .cancel) {}
            Button("Create") { Task { await create() } }
        }
    }

    /// `.mix-pick`: a 42 pt tile on surface-2, the name, "{n} briefings".
    private func mixRow(_ mix: Mix) -> some View {
        HStack(spacing: 12) {
            MixArt(cover: mix.cover, size: 42, radius: 8, tile: FAMColor.surface2,
                   glyph: FAMColor.text, glyphSize: 20)
            VStack(alignment: .leading, spacing: 2) {
                Text(mix.name)
                    .font(FAMFont.body(13.5, .semibold))
                    .foregroundStyle(FAMColor.text)
                    .lineLimit(1)
                Text(MixesModel.briefings(mix.items.count))
                    .font(FAMFont.body(11.5, .medium))
                    .foregroundStyle(FAMColor.textMuted)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 22)
        .padding(.vertical, 14)
        .contentShape(Rectangle())
    }

    private var createRow: some View {
        HStack(spacing: 12) {
            WebIcon(path: Icons.plus, size: 20, line: 2)
                .foregroundStyle(FAMColor.text)
                .frame(width: 42, height: 42)
                .background(FAMColor.surface2, in: RoundedRectangle(cornerRadius: 8))
            Text("Create new playlist")
                .font(FAMFont.body(13.5, .semibold))
                .foregroundStyle(FAMColor.text)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 22)
        .padding(.vertical, 14)
        .contentShape(Rectangle())
    }

    /// One after the other, on the main actor: the topic, then the list.
    private func load() async {
        guard !query.isEmpty else { problem = "Play something first"; return }
        let answer: EpisodeTopic
        do {
            answer = try await model.api.episodeTopic(query: query, title: title)
        } catch {
            problem = "Could not load your myFAM mixes"
            return
        }
        await mixes.load()
        if answer.id.isEmpty && (answer.query ?? "").isEmpty {
            problem = "Could not tell what this episode is about"
        } else {
            topic = answer
        }
    }

    private func entry(_ t: EpisodeTopic) -> Any {
        t.id.isEmpty ? ["query": t.query ?? "", "title": t.title] as [String: Any] : t.id
    }

    private func add(to mix: Mix, _ t: EpisodeTopic) async {
        let present = mix.items.contains { item in
            (!t.id.isEmpty && item.id == t.id)
                || (t.id.isEmpty && item.custom && item.query.lowercased() == (t.query ?? "").lowercased())
        }
        dismiss()
        if present {
            model.nowPlaying.say("\(t.label) is already in \(mix.name)", action: "Open") { model.openMix(mix.id) }
            return
        }
        if await mixes.update(mix.id, ["add": [entry(t)]]) != nil {
            model.nowPlaying.say("Added to \(mix.name)", action: "Open") { model.openMix(mix.id) }
        }
    }

    private func create() async {
        guard let topic else { return }
        let name = newName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return }
        dismiss()
        do {
            let mix = try await model.api.createMix(name: name, topics: [entry(topic)])
            mixes.keep(mix)
            model.nowPlaying.say("Added to \(mix.name)", action: "Open") { model.openMix(mix.id) }
        } catch {
            model.nowPlaying.say(MixesModel.sentence(error, else: "Could not save that mix"))
        }
    }
}

/// An action sheet's group header (`.sheet-group`): Geist Mono 9.5,
/// uppercase, faint.
struct SheetGroupLabel: View {
    let text: String

    var body: some View {
        Text(text)
            .monoLabel(9.5, color: FAMColor.textFaint, tracking: 1.14)
            .padding(.horizontal, 22)
            .padding(.top, 14)
            .padding(.bottom, 6)
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityAddTraits(.isHeader)
    }
}

/// An action sheet's Cancel (`.sheet-cancel`): a white rounded bar.
struct SheetCancelButton: View {
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text("Cancel")
                .font(FAMFont.body(13, .bold))
                .foregroundStyle(FAMColor.textMuted)
                .frame(maxWidth: .infinity)
                .padding(12)
                .background(FAMColor.background, in: RoundedRectangle(cornerRadius: 14))
        }
        .buttonStyle(.plain)
        .padding(.horizontal, 16)
        .padding(.top, 8)
    }
}
