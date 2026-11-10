import PhotosUI
import SwiftUI

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
    @State private var shown: [String] = []
    @State private var seen: Set<String> = []
    @State private var dragging: Int?
    @State private var dragY: CGFloat = 0
    @State private var rowPitch: CGFloat = 62
    @State private var listenTime = Date()

    private var mix: Mix? { mixes.mix(id) }

    var body: some View {
        VStack(spacing: 0) {
            MixHeader(title: mix?.name ?? "", back: { path.removeAll() }) {
                Button { menu = true } label: {
                    Image(systemName: "ellipsis")
                        .font(.system(size: 16, weight: .bold))
                        .foregroundStyle(FAMColor.text)
                        .frame(width: 36, height: 36)
                }
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
                            playButton(mix)
                            Text("Today’s edition · \(Self.today)")
                                .monoLabel(8.5)
                                .padding(.top, 10)
                                .padding(.bottom, 10)
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
            if let at = mix?.listenAt, let date = Self.date(at) { listenTime = date }
        }
        .confirmationDialog(mix?.name ?? "", isPresented: $menu, titleVisibility: .hidden) { menuButtons }
        .confirmationDialog(mix.map { "\($0.name) · a joint mix" } ?? "", isPresented: $people,
                            titleVisibility: .visible) {
            ForEach(Array((mix?.people ?? []).enumerated()), id: \.offset) { _, p in
                Button(personLine(p)) {}
            }
            Button("Cancel", role: .cancel) {}
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
                    if await mixes.update(id, ["public": true]) != nil { await share() }
                }
            }
            Button("Cancel", role: .cancel) {}
        }
        .confirmationDialog("Cover", isPresented: $coverChoice, titleVisibility: .hidden) {
            Button("Choose a different photo") { pickingPhoto = true }
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
    }

    // MARK: - Hero

    private func hero(_ mix: Mix) -> some View {
        HStack(alignment: .center, spacing: 14) {
            Group {
                if mix.isOwner {
                    Button { tapCover(mix) } label: {
                        MixArt(cover: mix.cover, size: 84, radius: 16)
                            .overlay(alignment: .bottomTrailing) {
                                Image(systemName: "camera.fill")
                                    .font(.system(size: 10, weight: .bold))
                                    .foregroundStyle(FAMColor.ink)
                                    .frame(width: 24, height: 24)
                                    .background(FAMColor.copper, in: Circle())
                                    .overlay(Circle().stroke(FAMColor.background, lineWidth: 2))
                                    .offset(x: 4, y: 4)
                            }
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(mix.cover.isEmpty ? "Add cover photo" : "Change cover photo")
                } else {
                    MixArt(cover: mix.cover, size: 84, radius: 16)
                }
            }
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 8) {
                    Text(mix.name)
                        .font(FAMFont.heading(17, .semibold))
                        .foregroundStyle(FAMColor.text)
                    if mix.isOwner {
                        Button("Change") { startRename(mix) }
                            .font(FAMFont.body(10, .bold))
                            .foregroundStyle(FAMColor.text)
                            .padding(.horizontal, 9)
                            .frame(height: 22)
                            .background(FAMColor.surface2, in: Capsule())
                            .buttonStyle(.plain)
                            .accessibilityLabel("Rename this mix")
                    }
                }
                if !mix.people.isEmpty {
                    Button { people = true } label: { peopleLine(mix.people) }
                        .buttonStyle(.plain)
                }
                Text("\(MixesModel.briefings(mix.items.count)) · daily · \(mix.joint ? "joint" : (mix.public ? "public" : "private"))")
                    .monoLabel(8.5)
            }
            Spacer(minLength: 0)
        }
        .padding(.top, 8)
    }

    private func peopleLine(_ people: [MixPerson]) -> some View {
        HStack(spacing: 8) {
            HStack(spacing: -6) {
                ForEach(Array(people.prefix(3).enumerated()), id: \.offset) { _, p in
                    PersonFace(person: p, size: 20)
                }
            }
            Text(MixesModel.peopleLabel(people))
                .font(FAMFont.body(11.5, .semibold))
                .foregroundStyle(FAMColor.text)
        }
    }

    private func personLine(_ p: MixPerson) -> String {
        let who = p.name.flatMap { $0.isEmpty ? nil : $0 } ?? p.handle.map { "@" + $0 } ?? "Someone"
        return who + (p.you == true ? " (you)" : "") + " — " + (p.owner == true ? "Owner" : "Can add topics")
    }

    @ViewBuilder
    private func actions(_ mix: Mix) -> some View {
        if mix.isOwner {
            HStack(spacing: 4) {
                Button { Task { await shareTapped() } } label: {
                    Image(systemName: "square.and.arrow.up").font(.system(size: 17)).frame(width: 36, height: 36)
                }
                .accessibilityLabel("Share this mix")
                Button { Task { await invite() } } label: {
                    Image(systemName: "person.badge.plus").font(.system(size: 17)).frame(width: 36, height: 36)
                }
                .accessibilityLabel("Invite collaborators")
                Spacer()
            }
            .foregroundStyle(FAMColor.textMuted)
            .padding(.top, 8)
        }
    }

    private var addTopicsPill: some View {
        Button { path.append(.add(id)) } label: {
            HStack(spacing: 6) {
                Image(systemName: "plus").font(.system(size: 11, weight: .bold))
                Text("Add topics to mix")
            }
            .font(FAMFont.body(12, .bold))
            .foregroundStyle(FAMColor.background)
            .padding(.horizontal, 18)
            .frame(height: 34)
            .background(FAMColor.text, in: Capsule())
        }
        .buttonStyle(.plain)
        .frame(maxWidth: .infinity)
        .padding(.top, 12)
    }

    private func playButton(_ mix: Mix) -> some View {
        Button { model.playMix(mix) } label: {
            HStack(spacing: 8) {
                Image(systemName: "play.fill").font(.system(size: 12))
                Text(mix.items.count == 1 ? "Play today’s 1 episode" : "Play today’s \(mix.items.count) episodes")
            }
        }
        .buttonStyle(CopperButtonStyle())
        .padding(.top, 18)
    }

    // MARK: - Topics, in order

    private func topicList(_ mix: Mix) -> some View {
        VStack(spacing: 8) {
            ForEach(Array(mix.items.enumerated()), id: \.element.id) { index, item in
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
        return max(0, min(count - 1, from + Int((dragY / rowPitch).rounded())))
    }

    private func topicRow(_ mix: Mix, _ item: MixItem, _ index: Int) -> some View {
        let adder = item.addedBy.flatMap { mix.joint && $0 < mix.people.count ? mix.people[$0] : nil }
        return HStack(spacing: 11) {
            Image(systemName: "line.3.horizontal")
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(FAMColor.textFaint)
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
                    if let adder { PersonFace(person: adder, size: 16).offset(x: 10, y: 6) }
                }
            VStack(alignment: .leading, spacing: 3) {
                Text(item.title)
                    .font(FAMFont.heading(12.5, .semibold))
                    .foregroundStyle(FAMColor.text)
                Text(adder.map { $0.you == true ? "Added by you" : "Added by \($0.name ?? ("@" + ($0.handle ?? "")))" }
                     ?? item.subtitle)
                    .font(FAMFont.body(9.5))
                    .foregroundStyle(FAMColor.textMuted)
            }
            Spacer(minLength: 4)
            Button { model.playMix(mix, from: item.id) } label: {
                Image(systemName: "play.fill")
                    .font(.system(size: 10))
                    .foregroundStyle(FAMColor.text)
                    .frame(width: 30, height: 30)
                    .overlay(Circle().stroke(FAMColor.borderStrong))
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Play this one")
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 11)
        .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12)
            .stroke(dragging == index ? FAMColor.copper : FAMColor.border))
        .shadow(color: .black.opacity(dragging == index ? 0.25 : 0), radius: 11, y: 8)
        .background(GeometryReader { g in
            Color.clear.onAppear { if index == 0 { rowPitch = g.size.height + 8 } }
        })
    }

    /// One whole-list write, as the web does for a reorder; the server's
    /// answer replaces the local copy.
    private func drop(_ mix: Mix) {
        guard let from = dragging else { return }
        let to = target(from)
        dragging = nil
        dragY = 0
        guard to != from else { return }
        var items = mix.items
        let moved = items.remove(at: from)
        items.insert(moved, at: to)
        Task { await mixes.update(id, ["topic_ids": items.map(\.entry)]) }
    }

    // MARK: - Recommended topics

    @ViewBuilder
    private func recommended(_ mix: Mix) -> some View {
        let byId = Dictionary(mixes.subjects.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
        let rows = shown.compactMap { byId[$0] }
            .filter { s in !mix.items.contains { MixesModel.base($0.id) == s.id } }
        if !rows.isEmpty {
            VStack(alignment: .leading, spacing: 0) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 3) {
                        Text("Recommended topics")
                            .font(FAMFont.heading(16, .semibold))
                            .foregroundStyle(FAMColor.text)
                        Text(mixes.recommended?.personalised == true ? "Based on what you listen to"
                             : (mix.items.isEmpty ? "Topics to follow, based on your interests"
                                : "Topics to follow, based on this mix"))
                            .font(FAMFont.body(9.5))
                            .foregroundStyle(FAMColor.textMuted)
                    }
                    Spacer()
                    Button { shuffle() } label: {
                        HStack(spacing: 5) {
                            Image(systemName: "shuffle").font(.system(size: 10, weight: .bold))
                            Text("Shuffle")
                        }
                        .font(FAMFont.body(11, .bold))
                        .foregroundStyle(FAMColor.text)
                        .padding(.horizontal, 11)
                        .frame(height: 28)
                        .overlay(Capsule().stroke(FAMColor.borderStrong))
                    }
                    .buttonStyle(.plain)
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
            .padding(.top, 26)
        }
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

    private func listenRow(_ mix: Mix) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: "bell").font(.system(size: 15)).foregroundStyle(FAMColor.copper)
            VStack(alignment: .leading, spacing: 4) {
                Text("Listen time").font(FAMFont.body(13, .semibold)).foregroundStyle(FAMColor.text)
                Text(listenNote(mix)).font(FAMFont.body(10.5)).foregroundStyle(FAMColor.textMuted)
                HStack {
                    DatePicker("", selection: $listenTime, displayedComponents: .hourAndMinute)
                        .labelsHidden()
                        .accessibilityLabel("Listen time")
                        .onChange(of: listenTime) { _, value in
                            let hhmm = Self.hhmm(value)
                            if hhmm != mix.listenAt { Task { await setListenTime(hhmm) } }
                        }
                    if !mix.listenAt.isEmpty {
                        Button("Clear") { Task { await setListenTime("") } }
                            .font(FAMFont.body(12, .bold))
                            .foregroundStyle(FAMColor.copper)
                            .accessibilityLabel("Remove listen time")
                    }
                }
            }
            Spacer(minLength: 0)
        }
        .padding(14)
        .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 12))
        .padding(.top, 26)
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
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(mix.public ? "Public" : "Private").font(FAMFont.body(13, .semibold)).foregroundStyle(FAMColor.text)
                    Text(mix.public ? "Public will be displayed on your profile" : "Only you can see this mix")
                        .font(FAMFont.body(10.5)).foregroundStyle(FAMColor.textMuted)
                }
                Spacer()
                ZStack(alignment: mix.public ? .trailing : .leading) {
                    Capsule().fill(mix.public ? FAMColor.copper : FAMColor.borderStrong).frame(width: 44, height: 24)
                    Circle().fill(mix.public ? FAMColor.ink : FAMColor.textMuted)
                        .frame(width: 18, height: 18)
                        .overlay(Image(systemName: mix.public ? "lock.open.fill" : "lock.fill")
                            .font(.system(size: 8)).foregroundStyle(FAMColor.background))
                        .padding(3)
                }
                .animation(.easeOut(duration: 0.16), value: mix.public)
            }
            .padding(14)
            .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 12))
        }
        .buttonStyle(.plain)
        .padding(.top, 10)
        .accessibilityLabel("Public")
        .accessibilityValue(mix.public ? "On" : "Off")
        .accessibilityAddTraits(.isButton)
    }

    // MARK: - The ⋯ menu

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
                Button("Rename") { startRename(mix) }
                Button(mix.cover.isEmpty ? "Add a cover photo" : "Change cover") { tapCover(mix) }
                Button("Add topics to mix") { path.append(.add(id)) }
                Button("Invite collaborators") { Task { await invite() } }
                if mix.joint { Button("Make it yours alone") { confirmAlone = true } }
                Button("Share mix") { Task { await shareTapped() } }
                Button("Delete mix", role: .destructive) { confirmDelete = true }
            } else {
                Button("Add topics to mix") { path.append(.add(id)) }
                if !mix.people.isEmpty { Button("Who’s in this mix") { people = true } }
                Button("Leave this mix", role: .destructive) { confirmLeave = true }
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

    private func shareTapped() async {
        guard let mix else { return }
        if mix.items.isEmpty { model.nowPlaying.say("Add a topic before sharing it"); return }
        if !mix.public { confirmPublicShare = true; return }
        await share()
    }

    private func share() async {
        do {
            let link = try await model.api.shareMix(id)
            shareItems = [link.targets?["sms"]?.text ?? link.targets?["copy"]?.text ?? link.url]
        } catch {
            model.nowPlaying.say(MixesModel.sentence(error, else: "Could not make a share link"))
        }
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
}

/// A face: the picture, else initials on a hue from the name.
struct PersonFace: View {
    @EnvironmentObject private var model: AppModel
    let person: MixPerson
    var size: CGFloat = 20

    var body: some View {
        let name = person.name ?? person.handle ?? "?"
        ZStack {
            Circle().fill(Color(hue: Double(name.unicodeScalars.reduce(0) { ($0 * 31 + Int($1.value)) % 360 }) / 360,
                                saturation: 0.35, brightness: 0.75))
            Text(String(name.prefix(1)).uppercased())
                .font(.system(size: size * 0.45, weight: .bold))
                .foregroundStyle(.white)
            if let avatar = person.avatar, let url = model.api.picture(avatar) {
                AsyncImage(url: url) { $0.resizable().scaledToFill() } placeholder: { Color.clear }
            }
        }
        .frame(width: size, height: size)
        .clipShape(Circle())
        .overlay(Circle().stroke(FAMColor.background, lineWidth: 1.5))
        .accessibilityHidden(true)
    }
}

/// A subject offered to a mix: icon tile, name, "{Facet} · new briefing
/// every day", and (+) or ✓.
struct SubjectRow: View {
    let subject: Subject?
    var title: String? = nil
    let sub: String
    let added: Bool
    let label: String
    let action: () -> Void

    var body: some View {
        HStack(spacing: 11) {
            Image(systemName: "dot.radiowaves.left.and.right")
                .font(.system(size: 13))
                .foregroundStyle(FAMColor.copper)
                .frame(width: 32, height: 32)
                .background(FAMColor.surface2, in: RoundedRectangle(cornerRadius: 9))
            VStack(alignment: .leading, spacing: 2) {
                Text(title ?? subject?.label ?? "")
                    .font(FAMFont.heading(13, .semibold))
                    .foregroundStyle(FAMColor.text)
                Text(sub).font(FAMFont.body(10)).foregroundStyle(FAMColor.textMuted)
            }
            Spacer(minLength: 4)
            Button(action: action) {
                Image(systemName: added ? "checkmark" : "plus")
                    .font(.system(size: 12, weight: .bold))
                    .foregroundStyle(added ? FAMColor.ink : FAMColor.text)
                    .frame(width: 30, height: 30)
                    .background(added ? FAMColor.copper : Color.clear, in: Circle())
                    .overlay(Circle().stroke(added ? FAMColor.copper : FAMColor.borderStrong))
            }
            .buttonStyle(.plain)
            .accessibilityLabel(label)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(added ? FAMColor.copper : FAMColor.border))
        .padding(.bottom, 8)
    }
}
