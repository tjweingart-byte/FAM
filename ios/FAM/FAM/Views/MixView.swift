import PhotosUI
import SwiftUI
import UIKit

/// One of your mixes, or a joint mix you are in (`screen-mixdetail`): the
/// cover and name, who is in it, Share and Invite, Add topics, today's
/// edition to play, the topics in order (drag to reorder), recommended
/// topics, the listen time, and Public / Private. A member sees only what a
/// member may change - topics (`joint-mixes`).
struct MixView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var mixes: MixesModel
    let id: String
    @Binding var path: [MixRoute]

    @State private var menu = false
    @State private var people = false
    @State private var renaming = false
    @State private var newName = ""
    @State private var confirmDelete = false
    @State private var confirmAlone = false
    @State private var confirmLeave = false
    @State private var confirmPublicShare = false
    @State private var coverChoice = false
    @State private var pickingPhoto = false
    @State private var photo: PhotosPickerItem?
    @State private var pendingCover: String?
    @State private var shareItems: [Any]?
    @State private var sharing = false
    @State private var shown: [String] = []
    @State private var seen: Set<String> = []
    @State private var dragging: Int?
    @State private var dragY: CGFloat = 0
    @State private var rowPitch: CGFloat = 62
    /// A reorder shown at once, until the server's answer replaces it (or
    /// a refusal puts the old order back).
    @State private var localOrder: [String]?
    @State private var reorderRun = 0
    /// The listen-time picker: open, and the time on its wheel. Written
    /// once, on Done - never on each turn of the wheel.
    @State private var pickingTime = false
    @State private var draftTime = Date()

    private var mix: Mix? { mixes.mix(id) }

    var body: some View {
        VStack(spacing: 0) {
            MixHeader(title: mix?.name ?? "", back: { back() }) {
                Button { menu = true } label: { HeadDots() }
                    .buttonStyle(.press)
                    .accessibilityLabel("More options")
            }
            if let mix {
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        hero(mix)
                        actions(mix)
                        addTopicsPill
                        if mix.items.isEmpty {
                            EmptyNote(text: "Nothing in this mix yet. Add the topics you want a fresh briefing on every day.")
                                .padding(.top, 16)
                        } else {
                            MixPlayButton(count: mix.items.count) { model.playMix(mix) }
                                .padding(.top, 2)
                            Text("Today’s edition · \(Self.today)")
                                .monoLabel(8.5, tracking: 0.425)
                                .padding(.top, 16)
                                .padding(.bottom, 9)
                            topicList(mix)
                        }
                        recommended(mix)
                        if mix.isOwner {
                            listenRow(mix)
                            visibilityRow(mix)
                        }
                    }
                    .padding(.horizontal, 18)
                    .padding(.bottom, 30)
                }
            } else {
                Spacer()
            }
        }
        .background(FAMColor.background)
        .toolbar(.hidden, for: .navigationBar)
        .task(id: id) {
            await model.api.noteMixOpen(id, played: false)
            await mixes.loadForPage()
            if shown.isEmpty { shuffle() }
        }
        .confirmationDialog(mix?.name ?? "", isPresented: $menu, titleVisibility: .hidden) { menuButtons }
        .sheet(isPresented: $people) {
            if let mix {
                MixPeopleSheet(mix: mix)
                    .environmentObject(model)
            }
        }
        .alert("Rename mix", isPresented: $renaming) {
            TextField(mix?.name ?? "", text: $newName)
            Button("Cancel", role: .cancel) {}
            Button("Save") { Task { await rename() } }
        }
        .confirmationDialog("Delete “\(mix?.name ?? "")”?", isPresented: $confirmDelete, titleVisibility: .visible) {
            Button("Delete mix", role: .destructive) { Task { await delete() } }
            Button("Cancel", role: .cancel) {}
        } message: { Text("Its topics and listen time go with it. This can’t be undone.") }
        .confirmationDialog("Make “\(mix?.name ?? "")” yours alone? The invite link stops working and everyone else is taken off. The topics they added stay.",
                            isPresented: $confirmAlone, titleVisibility: .visible) {
            Button("Yes, make it mine alone", role: .destructive) { Task { await makeAlone() } }
            Button("Cancel", role: .cancel) {}
        }
        .confirmationDialog("Leave “\(mix?.name ?? "")”? It comes out of your myFAM; the topics you added stay.",
                            isPresented: $confirmLeave, titleVisibility: .visible) {
            Button("Leave", role: .destructive) { Task { await leave() } }
            Button("Cancel", role: .cancel) {}
        }
        .confirmationDialog("Sharing “\(mix?.name ?? "")” makes it public: it goes on your profile, and anyone with the link can see it and add it.",
                            isPresented: $confirmPublicShare, titleVisibility: .visible) {
            Button("Make public and share") {
                Task {
                    if await mixes.update(id, ["public": true]) != nil { later { sharing = true } }
                }
            }
            Button("Cancel", role: .cancel) {}
        }
        .confirmationDialog("Cover", isPresented: $coverChoice, titleVisibility: .hidden) {
            Button("Choose a different photo") { later { pickingPhoto = true } }
            Button("Remove cover", role: .destructive) {
                Task { if await mixes.update(id, ["cover": ""]) != nil { model.nowPlaying.say("Cover removed") } }
            }
            Button("Cancel", role: .cancel) {}
        }
        .photosPicker(isPresented: $pickingPhoto, selection: $photo, matching: .images)
        .onChange(of: photo) { _, item in
            guard let item else { return }
            Task {
                photo = nil
                guard let data = try? await item.loadTransferable(type: Data.self),
                      let url = MixCover.dataURL(from: data) else {
                    model.nowPlaying.say("That file is not an image FAM can read")
                    return
                }
                pendingCover = url
            }
        }
        .alert("Use this photo as the cover?", isPresented: Binding(get: { pendingCover != nil },
                                                                     set: { if !$0 { pendingCover = nil } })) {
            Button("Cancel", role: .cancel) { pendingCover = nil }
            Button("Use photo") {
                let cover = pendingCover ?? ""
                pendingCover = nil
                Task { if await mixes.update(id, ["cover": cover]) != nil { model.nowPlaying.say("Cover updated") } }
            }
        } message: { Text(MixCover.note) }
        .sheet(isPresented: Binding(get: { shareItems != nil }, set: { if !$0 { shareItems = nil } })) {
            ActivityView(items: shareItems ?? []) { shareItems = nil }
        }
        .sheet(isPresented: $sharing) {
            MixShareSheet(id: id)
                .environmentObject(model)
        }
        .sheet(isPresented: $pickingTime) {
            ListenTimeSheet(time: $draftTime) { chosen in
                pickingTime = false
                let hhmm = Self.hhmm(chosen)
                if hhmm != (mix?.listenAt ?? "") { Task { await setListenTime(hhmm) } }
            }
        }
    }

    /// Back to the list, asked for afresh (`loadMixes`).
    private func back() {
        path.removeAll()
        Task { await mixes.load() }
    }

    /// A second presentation waits for a confirmation dialog to finish
    /// closing, or it is dropped (as `PlayerMenu.open` does).
    private func later(_ action: @escaping () -> Void) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35, execute: action)
    }

    // MARK: - Hero

    private func hero(_ mix: Mix) -> some View {
        HStack(alignment: .center, spacing: 14) {
            Group {
                if mix.isOwner {
                    Button { tapCover(mix) } label: {
                        MixArt(cover: mix.cover, size: 84, radius: 16)
                            .shadow(color: .black.opacity(0.35), radius: 13, y: 12)
                            .overlay(alignment: .bottomTrailing) {
                                CoverBadge(size: 24, glyph: 11, ring: 2).offset(x: 5, y: 5)
                            }
                    }
                    .buttonStyle(.press)
                    .accessibilityLabel(mix.cover.isEmpty ? "Add cover photo" : "Change cover photo")
                } else {
                    MixArt(cover: mix.cover, size: 84, radius: 16)
                }
            }
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 8) {
                    Text(mix.name)
                        .font(FAMFont.heading(17, .semibold))
                        .foregroundStyle(FAMColor.text)
                    if mix.isOwner {
                        Button("Change") { startRename(mix) }
                            .font(FAMFont.body(10, .bold))
                            .foregroundStyle(FAMColor.text)
                            .padding(.horizontal, 10)
                            .frame(minHeight: 22)
                            .background(FAMColor.surface2, in: Capsule())
                            .overlay(Capsule().stroke(FAMColor.borderStrong))
                            .buttonStyle(.press)
                            .accessibilityLabel("Rename this mix")
                    }
                }
                peopleRow(mix)
                Text(heroMeta(mix))
                    .monoLabel(8.5, tracking: 0.51)
                    .padding(.top, 5)
            }
            Spacer(minLength: 0)
        }
        .padding(.top, 8)
        .padding(.bottom, 16)
    }

    private func heroMeta(_ mix: Mix) -> String {
        let kind = mix.joint ? "joint" : (mix.public ? "public" : "private")
        return "\(MixesModel.briefings(mix.items.count)) · daily · \(kind)"
    }

    /// Who is in it (`mixPeopleLine`): a joint mix's people, which open
    /// "Who's in this mix"; a mix that is yours alone shows your own face
    /// and name, and opens nothing.
    @ViewBuilder
    private func peopleRow(_ mix: Mix) -> some View {
        if !mix.people.isEmpty {
            Button { people = true } label: { peopleLine(mix.people) }
                .buttonStyle(.press)
                .padding(.top, 7)
                .accessibilityHint("Shows who is in this mix")
        } else if let me = mixes.ownFace(fallbackName: model.listener?.displayName) {
            peopleLine([me])
                .padding(.top, 7)
        }
    }

    private func peopleLine(_ people: [MixPerson]) -> some View {
        HStack(spacing: 7) {
            HStack(spacing: -6) {
                ForEach(Array(people.prefix(3).enumerated()), id: \.offset) { _, p in
                    PersonFace(person: p, size: 20)
                }
            }
            Text(MixesModel.peopleLabel(people))
                .font(FAMFont.body(11, .semibold))
                .foregroundStyle(FAMColor.text)
        }
    }

    @ViewBuilder
    private func actions(_ mix: Mix) -> some View {
        if mix.isOwner {
            HStack(spacing: 4) {
                Button { Task { await shareTapped() } } label: {
                    WebIcon(path: Icons.share, size: 19, line: 2).frame(width: 36, height: 36)
                }
                .buttonStyle(.press)
                .accessibilityLabel("Share this mix")
                Button { Task { await invite() } } label: {
                    WebIcon(path: Icons.invite, size: 20, line: 2).frame(width: 36, height: 36)
                }
                .buttonStyle(.press)
                .accessibilityLabel("Invite collaborators")
                Spacer()
            }
            .foregroundStyle(FAMColor.textMuted)
            .padding(.leading, -6)
            .padding(.bottom, 12)
        }
    }

    private var addTopicsPill: some View {
        Button { path.append(.add(id)) } label: {
            HStack(spacing: 7) {
                WebIcon(path: Icons.plus, size: 13, line: 2.6)
                Text("Add topics to mix")
            }
            .font(FAMFont.body(12, .bold))
            .foregroundStyle(FAMColor.background)
            .padding(.horizontal, 18)
            .frame(minHeight: 34)
            .background(FAMColor.text, in: Capsule())
        }
        .buttonStyle(.press)
        .frame(maxWidth: .infinity)
        .padding(.bottom, 16)
    }

    // MARK: - Topics, in order

    /// The items as drawn: the reorder just made, until the server answers.
    private func shownItems(_ mix: Mix) -> [MixItem] {
        guard let order = localOrder else { return mix.items }
        var byId: [String: MixItem] = [:]
        for item in mix.items { byId[item.id] = item }
        var out: [MixItem] = []
        for itemID in order {
            if let item = byId[itemID] { out.append(item) }
        }
        return out.count == mix.items.count ? out : mix.items
    }

    private func topicList(_ mix: Mix) -> some View {
        let items = shownItems(mix)
        return VStack(spacing: 9) {
            ForEach(Array(items.enumerated()), id: \.element.id) { index, item in
                topicRow(mix, item, index)
                    .offset(y: offset(for: index))
                    .zIndex(dragging == index ? 2 : 0)
                    .animation(dragging == index ? nil : .easeOut(duration: 0.14), value: dragY)
            }
        }
    }

    /// Where a row sits while another is dragged: the dragged one follows
    /// the finger, the ones it passes slide by one row (`transform 140ms`).
    private func offset(for index: Int) -> CGFloat {
        guard let from = dragging else { return 0 }
        if index == from { return dragY }
        let to = target(from)
        if from < to && index > from && index <= to { return -rowPitch }
        if from > to && index < from && index >= to { return rowPitch }
        return 0
    }

    private func target(_ from: Int) -> Int {
        let count = mix?.items.count ?? 0
        let moved = Int((dragY / rowPitch).rounded())
        return max(0, min(count - 1, from + moved))
    }

    /// Who added an item to a joint mix.
    private func adder(_ mix: Mix, _ item: MixItem) -> MixPerson? {
        guard mix.joint, let at = item.addedBy, at >= 0, at < mix.people.count else { return nil }
        return mix.people[at]
    }

    /// "Added by you" / "Added by {name}" / "Added by @handle" - else the
    /// item's own line, never "Added by " and nothing.
    private func subLine(_ adder: MixPerson?, _ item: MixItem) -> String {
        guard let adder else { return item.subtitle }
        if adder.you == true { return "Added by you" }
        if let name = adder.name, !name.isEmpty { return "Added by " + name }
        if let handle = adder.handle, !handle.isEmpty { return "Added by @" + handle }
        return item.subtitle
    }

    private func topicRow(_ mix: Mix, _ item: MixItem, _ index: Int) -> some View {
        let who = adder(mix, item)
        let lifted = dragging == index
        return HStack(spacing: 11) {
            DragDots()
                .foregroundStyle(lifted ? FAMColor.copper : FAMColor.textFaint)
                .frame(width: 22, height: 34)
                .contentShape(Rectangle())
                .gesture(DragGesture(minimumDistance: 2, coordinateSpace: .global)
                    .onChanged { g in
                        if dragging == nil { dragging = index }
                        dragY = g.translation.height
                    }
                    .onEnded { _ in drop(mix) })
                .accessibilityLabel("Drag to reorder")
                .overlay(alignment: .bottomTrailing) {
                    if let who { PersonFace(person: who, size: 16).offset(x: 10, y: 6) }
                }
            VStack(alignment: .leading, spacing: 2) {
                Text(item.title)
                    .font(FAMFont.heading(12.5, .semibold))
                    .foregroundStyle(FAMColor.text)
                Text(subLine(who, item))
                    .font(FAMFont.body(9.5))
                    .foregroundStyle(FAMColor.textMuted)
            }
            Spacer(minLength: 4)
            Button { model.playMix(mix, from: item.id) } label: {
                PlayTriangle().fill(FAMColor.text).frame(width: 11, height: 11)
                    .frame(width: 30, height: 30)
                    .overlay(Circle().stroke(FAMColor.borderStrong))
            }
            .buttonStyle(.press)
            .accessibilityLabel("Play this one")
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 11)
        .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12)
            .stroke(lifted ? FAMColor.copper : FAMColor.border))
        .shadow(color: .black.opacity(lifted ? 0.35 : 0), radius: 11, y: 8)
        .background(GeometryReader { g in
            Color.clear.onAppear { if index == 0 { rowPitch = g.size.height + 9 } }
        })
    }

    /// One whole-list write, as the web does for a reorder. The new order
    /// is drawn at once; the server's answer replaces it, and a refusal
    /// (said in the server's words) puts the old order back.
    private func drop(_ mix: Mix) {
        guard let from = dragging else { return }
        let to = target(from)
        dragging = nil
        dragY = 0
        guard to != from else { return }
        var items = shownItems(mix)
        guard from < items.count, to < items.count else { return }
        let moved = items.remove(at: from)
        items.insert(moved, at: to)
        localOrder = items.map(\.id)
        reorderRun += 1
        let mine = reorderRun
        Task {
            await mixes.update(id, ["topic_ids": items.map(\.entry)])
            if mine == reorderRun { localOrder = nil }
        }
    }

    // MARK: - Recommended topics

    @ViewBuilder
    private func recommended(_ mix: Mix) -> some View {
        let byId = Dictionary(mixes.subjects.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
        let rows = shown.compactMap { byId[$0] }
            .filter { s in !mix.items.contains { MixesModel.base($0.id) == s.id } }
        if !rows.isEmpty {
            VStack(alignment: .leading, spacing: 0) {
                HStack(alignment: .bottom, spacing: 10) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Recommended topics")
                            .font(FAMFont.heading(16, .semibold))
                            .foregroundStyle(FAMColor.text)
                        Text(recommendedSub(mix))
                            .font(FAMFont.body(9.5))
                            .foregroundStyle(FAMColor.textMuted)
                    }
                    Spacer()
                    Button { shuffle() } label: {
                        HStack(spacing: 6) {
                            WebIcon(path: Icons.shuffle, size: 13, line: 2.2)
                            Text("Shuffle")
                        }
                        .font(FAMFont.body(10.5, .semibold))
                        .foregroundStyle(FAMColor.text)
                        .padding(.horizontal, 11)
                        .frame(minHeight: 28)
                        .overlay(Capsule().stroke(FAMColor.borderStrong))
                    }
                    .buttonStyle(.press)
                    .accessibilityLabel("Shuffle recommendations")
                }
                .padding(.bottom, 10)
                ForEach(rows) { s in
                    SubjectRow(subject: s, sub: mixes.subjectSub(s), added: false,
                               label: "Add \(s.label) to this mix") {
                        Task {
                            if await mixes.update(id, ["add": ["f:" + s.id]]) != nil {
                                model.nowPlaying.say("Added \(s.label) to \(mix.name)")
                            }
                        }
                    }
                }
            }
            .padding(.top, 13)
        }
    }

    private func recommendedSub(_ mix: Mix) -> String {
        if mixes.recommended?.personalised == true { return "Based on what you listen to" }
        return mix.items.isEmpty ? "Topics to follow, based on your interests" : "Topics to follow, based on this mix"
    }

    /// The next five not yet shown; round again once the catalogue runs out
    /// (`REC_COUNT`).
    private func shuffle() {
        let pool = mixes.recommendations(for: mix, excludingMix: true).map(\.id)
        guard !pool.isEmpty else { return }
        var fresh = pool.filter { !seen.contains($0) }
        if fresh.isEmpty { seen = []; fresh = pool }
        let next = Array(fresh.prefix(5))
        guard !next.isEmpty else { return }
        shown = next
        seen.formUnion(next)
    }

    // MARK: - Listen time and visibility (the owner's)

    /// Bell, what it does, then the time - a pill that opens the picker,
    /// "--:--" when none is set - and Clear (shot 06c).
    private func listenRow(_ mix: Mix) -> some View {
        HStack(alignment: .center, spacing: 12) {
            WebIcon(path: Icons.bell, size: 17, line: 2).foregroundStyle(FAMColor.copper)
            VStack(alignment: .leading, spacing: 3) {
                Text("Listen time").font(FAMFont.body(12.5)).foregroundStyle(FAMColor.text)
                Text(listenNote(mix))
                    .font(FAMFont.body(10))
                    .foregroundStyle(FAMColor.textMuted)
                    .lineSpacing(4)
            }
            Spacer(minLength: 0)
            Button { openTimePicker(mix) } label: {
                HStack(spacing: 8) {
                    Text(Self.pillLabel(mix.listenAt))
                        .font(FAMFont.body(12, .bold))
                        .monospacedDigit()
                    WebIcon(path: Icons.clock, size: 12, line: 2)
                }
                .foregroundStyle(FAMColor.ink)
                .padding(.horizontal, 8)
                .padding(.vertical, 6)
                .background(FAMColor.paper, in: RoundedRectangle(cornerRadius: 9))
            }
            .buttonStyle(.press)
            .fixedSize()
            .accessibilityLabel("Listen time")
            .accessibilityValue(mix.listenAt.isEmpty ? "Not set" : MixesModel.timeLabel(mix.listenAt))
            if !mix.listenAt.isEmpty {
                Button("Clear") { Task { await setListenTime("") } }
                    .font(.system(size: 11))
                    .foregroundStyle(FAMColor.textMuted)
                    .padding(4)
                    .buttonStyle(.press)
                    .fixedSize()
                    .accessibilityLabel("Remove listen time")
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 13)
        .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(FAMColor.border))
        .padding(.top, 14)
    }

    /// The wheel starts on the set time, else on the next whole hour.
    private func openTimePicker(_ mix: Mix) {
        if let set = Self.date(mix.listenAt) {
            draftTime = set
        } else {
            let hour = (Calendar.current.component(.hour, from: Date()) + 1) % 24
            draftTime = Calendar.current.date(bySettingHour: hour, minute: 0, second: 0, of: Date()) ?? Date()
        }
        pickingTime = true
    }

    /// What the row says (`mixWhenNote`). Notifications reach a phone through
    /// Apple's push service, which the server does not send yet, so a set
    /// time says only when it is.
    private func listenNote(_ mix: Mix) -> String {
        if mix.listenAt.isEmpty {
            return "Pick when you listen and we’ll tell you it’s ready. Episodes are still written each morning."
        }
        let time = MixesModel.timeLabel(mix.listenAt)
        if let push = mixes.push, !push.available, let reason = push.reason, !reason.isEmpty { return reason }
        return "Listening at \(time). Notifications on iPhone come in a later version."
    }

    private func setListenTime(_ hhmm: String) async {
        let zone = TimeZone.current.identifier
        guard await mixes.update(id, ["listen_at": hhmm, "listen_tz": zone]) != nil else { return }
        model.nowPlaying.say(hhmm.isEmpty ? "Listen time removed" : "Listen time set for \(MixesModel.timeLabel(hhmm))")
    }

    private func visibilityRow(_ mix: Mix) -> some View {
        Button {
            Task {
                if await mixes.update(id, ["public": !mix.public]) != nil {
                    model.nowPlaying.say(mix.public ? "Private again" : "On your profile now")
                }
            }
        } label: {
            HStack(spacing: 14) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(mix.public ? "Public" : "Private").font(FAMFont.body(12.5)).foregroundStyle(FAMColor.text)
                    Text(mix.public ? "Public will be displayed on your profile" : "Only you can see this mix")
                        .font(FAMFont.body(10)).foregroundStyle(FAMColor.textMuted)
                }
                Spacer()
                ZStack(alignment: mix.public ? .trailing : .leading) {
                    Capsule().fill(mix.public ? FAMColor.copper : FAMColor.borderStrong).frame(width: 44, height: 24)
                    Circle().fill(mix.public ? FAMColor.ink : FAMColor.textMuted)
                        .frame(width: 18, height: 18)
                        .overlay(Image(systemName: mix.public ? "lock.open" : "lock")
                            .font(.system(size: 8, weight: .bold))
                            .foregroundStyle(mix.public ? FAMColor.copper : FAMColor.surface))
                        .padding(3)
                }
                .animation(.easeOut(duration: 0.16), value: mix.public)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 13)
            .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(FAMColor.border))
        }
        .buttonStyle(.press)
        .padding(.top, 10)
        .accessibilityLabel("Public")
        .accessibilityValue(mix.public ? "On" : "Off")
        .accessibilityAddTraits(.isButton)
    }

    // MARK: - The ⋯ menu

    /// Anything that opens another sheet, alert or picker waits for this
    /// dialog to close first (`later`).
    @ViewBuilder
    private var menuButtons: some View {
        if let mix {
            if mix.isOwner {
                Button(mix.public ? "Make private" : "Make public") {
                    Task {
                        if await mixes.update(id, ["public": !mix.public]) != nil {
                            model.nowPlaying.say(mix.public ? "Private again" : "On your profile now")
                        }
                    }
                }
                Button("Rename") { later { startRename(mix) } }
                Button(mix.cover.isEmpty ? "Add a cover photo" : "Change cover") { later { tapCover(mix) } }
                Button("Add topics to mix") { path.append(.add(id)) }
                Button("Invite collaborators") { later { Task { await invite() } } }
                if mix.joint { Button("Make it yours alone") { later { confirmAlone = true } } }
                Button("Share mix") { later { Task { await shareTapped() } } }
                Button("Delete mix", role: .destructive) { later { confirmDelete = true } }
            } else {
                Button("Add topics to mix") { path.append(.add(id)) }
                if !mix.people.isEmpty { Button("Who’s in this mix") { later { people = true } } }
                Button("Leave this mix", role: .destructive) { later { confirmLeave = true } }
            }
            Button("Cancel", role: .cancel) {}
        }
    }

    private func tapCover(_ mix: Mix) {
        if mix.cover.isEmpty { pickingPhoto = true } else { coverChoice = true }
    }

    private func startRename(_ mix: Mix) {
        newName = mix.name
        renaming = true
    }

    private func rename() async {
        let name = newName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, name != mix?.name else { return }
        await mixes.update(id, ["name": name])
    }

    private func delete() async {
        let name = mix?.name ?? ""
        do {
            try await model.api.deleteMix(id)
            mixes.drop(id)
            path.removeAll()
            model.nowPlaying.say("Deleted \(name)")
        } catch {
            model.nowPlaying.say(MixesModel.sentence(error, else: "Could not delete that mix"))
        }
    }

    private func makeAlone() async {
        do {
            let fresh = try await model.api.stopJointMix(id)
            mixes.keep(fresh)
            model.nowPlaying.say("It’s yours alone now")
        } catch {
            model.nowPlaying.say(MixesModel.sentence(error, else: "Could not change that mix"))
        }
    }

    private func leave() async {
        let name = mix?.name ?? ""
        do {
            try await model.api.leaveMix(id)
            mixes.drop(id)
            path.removeAll()
            model.nowPlaying.say("You left \(name)")
        } catch {
            model.nowPlaying.say(MixesModel.sentence(error, else: "Could not leave that mix"))
        }
    }

    // MARK: - Sharing and inviting

    /// Empty: nothing to share. Private: asks before going public. Public:
    /// FAM's share sheet for a mix (`openMixShareModal`).
    private func shareTapped() async {
        guard let mix else { return }
        if mix.items.isEmpty { model.nowPlaying.say("Add a topic before sharing it"); return }
        if !mix.public { confirmPublicShare = true; return }
        sharing = true
    }

    private func invite() async {
        do {
            let link = try await model.api.inviteToMix(id)
            shareItems = ["Help me build “\(link.name)” on FAM — add the topics you want to hear. \(link.url)"]
        } catch {
            model.nowPlaying.say(MixesModel.sentence(error, else: "Could not make an invite"))
        }
    }

    // MARK: - Dates

    /// "Nov 10" on the device's clock.
    static var today: String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US")
        f.dateFormat = "MMM d"
        return f.string(from: Date())
    }

    static func hhmm(_ date: Date) -> String {
        let c = Calendar.current.dateComponents([.hour, .minute], from: date)
        return String(format: "%02d:%02d", c.hour ?? 0, c.minute ?? 0)
    }

    static func date(_ hhmm: String) -> Date? {
        let parts = hhmm.split(separator: ":").compactMap { Int($0) }
        guard parts.count == 2 else { return nil }
        return Calendar.current.date(bySettingHour: parts[0], minute: parts[1], second: 0, of: Date())
    }

    /// The time field's text: "07:30 AM", or "--:--" with none set.
    static func pillLabel(_ hhmm: String) -> String {
        let parts = hhmm.split(separator: ":").compactMap { Int($0) }
        guard parts.count == 2 else { return "--:--" }
        let hour = parts[0] % 12 == 0 ? 12 : parts[0] % 12
        let half = parts[0] < 12 ? "AM" : "PM"
        return String(format: "%02d:%02d ", hour, parts[1]) + half
    }
}

/// The mix page's ⋯ (`.head-dots`): three 3.5 pt dots.
struct HeadDots: View {
    var body: some View {
        HStack(spacing: 3) {
            ForEach(0..<3, id: \.self) { _ in
                Circle().fill(FAMColor.textMuted).frame(width: 3.5, height: 3.5)
            }
        }
        .padding(8)
        .frame(minWidth: 36, minHeight: 36)
        .contentShape(Rectangle())
    }
}

/// The copper camera badge on a cover (`.nm-cam`).
struct CoverBadge: View {
    var size: CGFloat = 24
    var glyph: CGFloat = 11
    var ring: CGFloat = 2

    var body: some View {
        WebIcon(path: Icons.camera, size: glyph, line: 2.2)
            .foregroundStyle(FAMColor.ink)
            .frame(width: size, height: size)
            .background(FAMColor.copper, in: Circle())
            .overlay(Circle().stroke(FAMColor.background, lineWidth: ring))
            .accessibilityHidden(true)
    }
}

/// "Play today's N episodes" (`.mix-play`): the mix page's and a public
/// mix's, the same button.
struct MixPlayButton: View {
    let count: Int
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 8) {
                PlayTriangle().fill(FAMColor.ink).frame(width: 13, height: 13)
                Text(count == 1 ? "Play today’s 1 episode" : "Play today’s \(count) episodes")
            }
            .font(FAMFont.body(12.5, .bold))
            .foregroundStyle(FAMColor.ink)
            .frame(maxWidth: .infinity, minHeight: 43)
            .background(FAMColor.copper, in: Capsule())
        }
        .buttonStyle(.press)
    }
}

/// The listen time's picker: the wheel, then Done. One write, on Done.
struct ListenTimeSheet: View {
    @Binding var time: Date
    let done: (Date) -> Void
    @Environment(\.dismiss) private var dismiss
    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        VStack(spacing: 0) {
            // At the accessibility sizes the heading has a row of its own
            // under Cancel and Done (`large-text`).
            Group {
                if typeSize.isAccessibilitySize {
                    VStack(spacing: 6) {
                        HStack {
                            cancelButton
                            Spacer()
                            doneButton
                        }
                        heading
                    }
                } else {
                    HStack {
                        cancelButton
                        Spacer()
                        heading
                        Spacer()
                        doneButton
                    }
                }
            }
            .padding(.horizontal, 20)
            .padding(.top, 18)
            DatePicker("", selection: $time, displayedComponents: .hourAndMinute)
                .datePickerStyle(.wheel)
                .labelsHidden()
                .accessibilityLabel("Listen time")
            Spacer(minLength: 0)
        }
        .background(FAMColor.surface)
        .presentationDetents([.height(300), .large])
        .presentationBackground(FAMColor.surface)
    }

    private var cancelButton: some View {
        Button("Cancel") { dismiss() }
            .font(FAMFont.body(13, .semibold))
            .foregroundStyle(FAMColor.textMuted)
    }

    private var doneButton: some View {
        Button("Done") { done(time) }
            .font(FAMFont.body(13, .bold))
            .foregroundStyle(FAMColor.copper)
    }

    private var heading: some View {
        Text("Listen time")
            .font(FAMFont.heading(16, .semibold))
            .foregroundStyle(FAMColor.text)
            .multilineTextAlignment(.center)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// "Who's in this mix" (`openMixPeople`): each person with Owner or Can
/// add topics; a row with a handle opens that person's profile (yours
/// does not).
struct MixPeopleSheet: View {
    @EnvironmentObject private var model: AppModel
    let mix: Mix
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SheetGroupLabel(text: "\(mix.name) · a joint mix")
            ScrollView {
                VStack(spacing: 0) {
                    ForEach(Array(mix.people.enumerated()), id: \.offset) { _, p in
                        let handle = p.handle ?? ""
                        if handle.isEmpty || p.you == true {
                            row(p)
                        } else {
                            Button {
                                dismiss()
                                model.social.openAfterClose(.person(PersonOpen(handle: handle, name: p.name ?? "",
                                                                               avatar: p.avatar ?? "")))
                            } label: {
                                row(p).contentShape(Rectangle())
                            }
                            .buttonStyle(.press)
                        }
                    }
                }
            }
            SheetCancelButton { dismiss() }
        }
        .padding(.top, 10)
        .padding(.bottom, 12)
        .background(FAMColor.surface)
        .presentationDetents([.medium, .large])
        .presentationBackground(FAMColor.surface)
    }

    private func name(_ p: MixPerson) -> String {
        var who = ""
        if let n = p.name, !n.isEmpty {
            who = n
        } else if let h = p.handle, !h.isEmpty {
            who = "@" + h
        } else {
            who = "Someone"
        }
        return p.you == true ? who + " (you)" : who
    }

    private func row(_ p: MixPerson) -> some View {
        HStack(spacing: 12) {
            PersonFace(person: p, size: 34)
            VStack(alignment: .leading, spacing: 2) {
                Text(name(p))
                    .font(FAMFont.body(13.5, .semibold))
                    .foregroundStyle(FAMColor.text)
                    .lineLimit(1)
                Text(p.owner == true ? "Owner" : "Can add topics")
                    .font(FAMFont.body(11.5, .medium))
                    .foregroundStyle(FAMColor.textMuted)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 22)
        .padding(.vertical, 12)
        .accessibilityElement(children: .combine)
    }
}

/// Share a mix (`openMixShareModal`): FAM's share sheet titled "Share this
/// mix" - to a group or a person inside FAM (the link's words as a
/// message), then the server's destinations from `/api/mixes/{id}/share`.
/// Copy is the clipboard; a destination opens its app with the words; the
/// rest go to iOS's share sheet. The story cards are not ported.
struct MixShareSheet: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let id: String

    @State private var link: MixShare?
    @State private var problem = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Share this mix")
                .font(FAMFont.heading(16, .semibold))
                .foregroundStyle(FAMColor.text)
                .padding(.horizontal, 22)
                .padding(.top, 20)
                .padding(.bottom, 14)
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    // To a person or a group inside FAM: the link's words,
                    // as a message (`shareSubject.kind == "mix"`).
                    SendInsideFAM(send: { to, label in await sendInside(to: to, label: label) },
                                  sent: { dismiss() })
                    Text("Anywhere else").monoLabel(9.5, tracking: 0.76)
                        .padding(.top, 20)
                        .padding(.bottom, 4)
                    if let link {
                        ShareTargetRows(url: link.url, targets: link.targets ?? [:],
                                        isPublic: link.public) { dismiss() }
                    } else {
                        Text(problem.isEmpty ? "Making a link\u{2026}" : problem)
                            .font(FAMFont.body(12))
                            .foregroundStyle(FAMColor.textMuted)
                            .padding(.vertical, 14)
                    }
                }
                .padding(.horizontal, 22)
            }
            SheetCancelButton { dismiss() }
        }
        .padding(.bottom, 12)
        .background(FAMColor.surface)
        .presentationDetents([.large])
        .presentationBackground(FAMColor.surface)
        .environmentObject(model.social)
        // The toast over the sheet: a send's refusal in the server's words
        // ("You can message people who follow you back.") would be under it.
        .overlay(alignment: .bottom) {
            ToastView()
                .environmentObject(model.nowPlaying)
                .padding(.bottom, 70)
        }
        .task { await load() }
    }

    private func load() async {
        do {
            link = try await model.api.shareMix(id)
        } catch {
            problem = MixesModel.sentence(error, else: "Could not make a share link")
        }
    }

    /// The SMS words with the link, as a message; before the link is made,
    /// "Still making the link — try again in a moment".
    private func sendInside(to: String, label: String) async -> Bool {
        guard let link else {
            model.nowPlaying.say("Still making the link \u{2014} try again in a moment")
            return false
        }
        let words = link.targets?["sms"]?.text ?? link.targets?["copy"]?.text ?? link.url
        return await model.social.sendShare(to: to, label: label, text: words)
    }
}

/// A face (`mixFaceHTML`): the picture where one is set, over two-letter
/// initials on the person's colour - the web's `SRC_HUES`, picked by
/// `personHue` (a hash of "@" + handle, else name), so one person is the
/// same colour on every surface. Ringed in the card colour.
struct PersonFace: View {
    @EnvironmentObject private var model: AppModel
    let person: MixPerson
    var size: CGFloat = 20

    /// `SRC_HUES`.
    static let hues: [UInt32] = [0xC9A876, 0x8FAE9A, 0xC3A8E0, 0xE2694F, 0x8FB4D9, 0xD9B38F]

    /// `personHue(text)` = `srcHue("@" + text)`: sum = (sum * 31 + UTF-16
    /// unit) % 9973 over the string, then one of six.
    static func hue(_ text: String) -> Color {
        var sum = 0
        for unit in ("@" + text).utf16 {
            sum = (sum * 31 + Int(unit)) % 9973
        }
        return Color(hex: hues[sum % hues.count])
    }

    /// `deriveInitials`: the first letters of the first two words, else the
    /// first two characters, without a leading "@".
    static func initials(_ text: String) -> String {
        var clean = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if clean.hasPrefix("@") { clean = String(clean.dropFirst()) }
        let words = clean.split(whereSeparator: { $0.isWhitespace })
        if words.count >= 2, let a = words[0].first, let b = words[1].first {
            return (String(a) + String(b)).uppercased()
        }
        return String(clean.prefix(2)).uppercased()
    }

    private var hueKey: String {
        if let h = person.handle, !h.isEmpty { return h }
        return person.name ?? ""
    }

    private var initialsText: String {
        if let n = person.name, !n.isEmpty { return Self.initials(n) }
        if let h = person.handle, !h.isEmpty { return Self.initials(h) }
        return "?"
    }

    var body: some View {
        ZStack {
            Circle().fill(Self.hue(hueKey))
            Text(initialsText)
                .font(FAMFont.body(max(7, size * 0.4), .bold))
                .foregroundStyle(FAMColor.ink)
                .lineLimit(1)
                .minimumScaleFactor(0.6)
            if let avatar = person.avatar, !avatar.isEmpty, let url = model.api.picture(avatar) {
                AsyncImage(url: url) { $0.resizable().scaledToFill() } placeholder: { Color.clear }
            }
        }
        .frame(width: size, height: size)
        .clipShape(Circle())
        .overlay(Circle().stroke(FAMColor.surface, lineWidth: 1.5))
        .accessibilityHidden(true)
    }
}

/// A subject offered to a mix (`addTopicsRow`, also Recommended topics):
/// the subject's own icon in a cream tile, name, "{Facet} · new briefing
/// every day", and a round button with "+" - or, once in the mix, a copper
/// border and "✓".
struct SubjectRow: View {
    let subject: Subject?
    var title: String? = nil
    var icon: String? = nil
    let sub: String
    let added: Bool
    let label: String
    let action: () -> Void

    var body: some View {
        HStack(spacing: 11) {
            SubjectIcon(name: icon ?? subject?.icon ?? "news", size: 17)
                .foregroundStyle(FAMColor.copper)
                .frame(width: 32, height: 32)
                .background(added ? Color(red: 224 / 255, green: 181 / 255, blue: 99 / 255).opacity(0.16)
                            : FAMColor.surface2, in: RoundedRectangle(cornerRadius: 9))
            VStack(alignment: .leading, spacing: 2) {
                Text(title ?? subject?.label ?? "")
                    .font(FAMFont.heading(12.5, .semibold))
                    .foregroundStyle(FAMColor.text)
                    .lineSpacing(2)
                Text(sub).font(FAMFont.body(9.5)).foregroundStyle(FAMColor.textMuted)
            }
            Spacer(minLength: 4)
            Button(action: action) {
                Text(added ? "✓" : "+")
                    .font(.system(size: 15))
                    .foregroundStyle(added ? FAMColor.ink : FAMColor.text)
                    .frame(width: 30, height: 30)
                    .background(added ? FAMColor.copper : Color.clear, in: Circle())
                    .overlay(Circle().stroke(added ? FAMColor.copper : FAMColor.borderStrong))
            }
            .buttonStyle(.press)
            .accessibilityLabel(label)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 11)
        .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(added ? FAMColor.copper : FAMColor.border))
        .padding(.bottom, 9)
    }
}

/// Six dots: the drag handle (`.mix-drag`).
struct DragDots: View {
    var body: some View {
        Canvas { ctx, size in
            let sx = size.width / 12, sy = size.height / 16
            for (x, y) in [(3.0, 3.0), (9, 3), (3, 8), (9, 8), (3, 13), (9, 13)] {
                ctx.fill(Path(ellipseIn: CGRect(x: (x - 1.4) * sx, y: (y - 1.4) * sy, width: 2.8 * sx, height: 2.8 * sy)),
                         with: .foreground)
            }
        }
        .frame(width: 12, height: 16)
    }
}

/// The filled play triangle the web draws (`M8 5v14l11-7z`).
struct PlayTriangle: Shape {
    func path(in r: CGRect) -> Path {
        var p = Path()
        p.move(to: CGPoint(x: r.minX + r.width * 8 / 24, y: r.minY + r.height * 5 / 24))
        p.addLine(to: CGPoint(x: r.minX + r.width * 8 / 24, y: r.minY + r.height * 19 / 24))
        p.addLine(to: CGPoint(x: r.minX + r.width * 19 / 24, y: r.minY + r.height * 12 / 24))
        p.closeSubpath()
        return p
    }
}
