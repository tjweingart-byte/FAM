import FAMSafety
import SwiftUI

/// The faces row at the top of DailyFAM (`#myfamCircle`, §297): your story
/// first with its (+) badge, then friends and people you follow - a gradient
/// ring while they have stories you have not seen, a thin grey one once you
/// have - then Invite. Nothing for a listener with nobody and no story of
/// their own.
///
/// A face with no story opens that person's profile (by handle; with no
/// handle, a conversation - `openCircleFace`). Your own face with no story
/// (or its (+)) opens the note composer on the web; that is not built
/// here, so it does nothing (`no-dead-controls`).
struct FacesRow: View {
    @EnvironmentObject private var model: AppModel
    /// Observed so a face turns into a button once the graph says friend.
    @EnvironmentObject private var social: SocialModel
    @State private var viewing: StoryReel?
    @State private var invite = false
    @AppStorage("storiesSeen") private var seenJSON = "{}"

    var body: some View {
        if let circle = model.circle, !(circle.circle.isEmpty && circle.me == nil) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(alignment: .top, spacing: 14) {
                    if let me = circle.me {
                        myFace(me)
                    }
                    ForEach(Array(circle.circle.enumerated()), id: \.element.id) { index, person in
                        let state: Ring = person.stories.isEmpty ? (person.fresh == true ? .fresh : .none)
                                                                 : ring(for: person.userId, person.stories)
                        let label = firstName(person.name, handle: person.handle)
                        let face = faceItem(name: person.name, handle: person.handle, avatar: person.avatar,
                                            label: label, ring: state, plus: false)
                        if person.stories.isEmpty && person.handle.isEmpty && !social.isFriend(person.userId) {
                            face.accessibilityElement(children: .combine)
                        } else if person.stories.isEmpty {
                            Button { openFace(person) } label: { face }
                                .buttonStyle(.plain)
                                .accessibilityLabel(person.handle.isEmpty ? "Message \(label)" : "\(label)'s profile")
                        } else {
                            Button { open(circle: circle.circle, at: index) } label: { face }
                                .buttonStyle(.plain)
                                .accessibilityLabel("\(label)'s story")
                        }
                    }
                    Button { invite = true } label: {
                        VStack(spacing: 6) {
                            WebIcon(path: Icons.plus, size: 17)
                                .foregroundStyle(FAMColor.textMuted)
                                .frame(width: 72, height: 72)
                                .overlay(Circle().inset(by: 0.75)
                                    .stroke(FAMColor.textFaint, style: StrokeStyle(lineWidth: 1.5, dash: [4, 3])))
                            Text("Invite").font(FAMFont.body(11)).foregroundStyle(FAMColor.textMuted)
                        }
                        .frame(width: 72)
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Invite a friend")
                }
                .padding(.horizontal, 2)
                .padding(.top, 6)
                .padding(.bottom, 4)
            }
            .fullScreenCover(item: $viewing) { reel in
                StoryViewer(reel: reel, markSeen: markSeen, seenAt: { seen[$0] ?? 0 })
                    .environmentObject(model)
            }
            .sheet(isPresented: $invite) {
                ActivityView(items: [model.inviteText(handle: model.circle?.me?.handle ?? "")]) { invite = false }
            }
        }
    }

    /// Your face: your story when one is up; the (+) is always drawn, as
    /// the web draws it, but adds nothing until the composer exists.
    @ViewBuilder private func myFace(_ me: CircleMe) -> some View {
        let mine = me.stories ?? []
        let face = faceItem(name: me.name ?? "", handle: me.handle ?? "", avatar: me.avatar ?? "",
                            label: "Your story", ring: mine.isEmpty ? .none : ring(for: "me", mine), plus: true)
        if mine.isEmpty {
            face.accessibilityElement(children: .combine)
        } else {
            Button {
                viewing = StoryReel(people: [StoryPerson(id: "me", name: "Your story", handle: me.handle ?? "",
                                                         avatar: me.avatar ?? "", stories: mine, mine: true)],
                                    start: 0)
            } label: { face }
                .buttonStyle(.plain)
                .accessibilityLabel("Your story")
        }
    }

    private enum Ring { case none, fresh, seen }

    /// 72 pt: a picture of 60 inside the ring - the gold gradient behind a
    /// 3 pt white gap while unseen, a 1.5 pt grey ring and a 4.5 pt gap once
    /// seen, nothing round one with no story; initials 20 pt bold.
    private func faceItem(name: String, handle: String, avatar: String, label: String, ring: Ring,
                          plus: Bool) -> some View {
        VStack(spacing: 6) {
            ZStack {
                switch ring {
                case .fresh:
                    Circle().fill(LinearGradient(stops: [
                        .init(color: Color(hex: 0x7A5A2E), location: 0),
                        .init(color: Color(hex: 0xB98A3E), location: 0.3),
                        .init(color: Color(hex: 0xE0B563), location: 0.62),
                        .init(color: Color(hex: 0xF3D58E), location: 1),
                    ], startPoint: .bottomLeading, endPoint: .topTrailing))
                    Circle().fill(FAMColor.background).frame(width: 66, height: 66)
                case .seen:
                    Circle().fill(FAMColor.textFaint)
                    Circle().fill(FAMColor.background).frame(width: 69, height: 69)
                case .none:
                    Color.clear
                }
                Face(name: name, handle: handle, avatar: avatar, size: 60, initialsSize: 20)
            }
            .frame(width: 72, height: 72)
            .overlay(alignment: .bottomTrailing) {
                if plus {
                    // `.yf-add`: 24 pt, the text colour, a 3 pt white border.
                    ZStack {
                        Circle().fill(FAMColor.background).frame(width: 30, height: 30)
                        Circle().fill(FAMColor.text).frame(width: 24, height: 24)
                        WebIcon(path: Icons.plus, size: 13).foregroundStyle(FAMColor.background)
                    }
                    .accessibilityHidden(true)
                }
            }
            Text(label)
                .font(FAMFont.body(11))
                .foregroundStyle(FAMColor.text)
                .lineLimit(1)
                .frame(maxWidth: 72)
        }
        .frame(width: 72)
    }

    /// `firstName`: the first word of the name, else of the handle.
    private func firstName(_ name: String, handle: String) -> String {
        let source = name.trimmingCharacters(in: .whitespaces).isEmpty ? handle : name
        let first = source.split(separator: " ").first.map(String.init) ?? ""
        return first.isEmpty ? "Someone" : first
    }

    // MARK: Seen state (per device, as `famStoriesSeen`)

    private var seen: [String: Double] {
        (try? JSONDecoder().decode([String: Double].self, from: Data(seenJSON.utf8))) ?? [:]
    }

    private func ring(for id: String, _ stories: [Story]) -> Ring {
        guard let last = stories.map(\.at).max() else { return .none }
        return last > (seen[id] ?? 0) ? .fresh : .seen
    }

    private func markSeen(_ id: String, _ at: Double) {
        var map = seen
        map[id] = max(map[id] ?? 0, at)
        if let data = try? JSONEncoder().encode(map) { seenJSON = String(decoding: data, as: UTF8.self) }
    }

    /// `openCircleFace`: the profile by handle, else a conversation.
    private func openFace(_ p: CirclePerson) {
        if p.handle.isEmpty {
            // A conversation only with a friend (§523).
            guard model.social.isFriend(p.userId) else {
                model.nowPlaying.say("You can message people who follow you back.")
                return
            }
            model.social.open(.thread(ChatOpen(with: p.userId, name: p.name, avatar: p.avatar)))
        } else {
            model.social.open(.person(PersonOpen(handle: p.handle, userID: p.userId, name: p.name,
                                                 avatar: p.avatar)))
        }
    }

    private func open(circle people: [CirclePerson], at index: Int) {
        let withStories = people.filter { !$0.stories.isEmpty }
        guard !people[index].stories.isEmpty,
              let start = withStories.firstIndex(where: { $0.id == people[index].id }) else { return }
        viewing = StoryReel(people: withStories.map {
            StoryPerson(id: $0.userId, name: $0.name, handle: $0.handle, avatar: $0.avatar,
                        stories: $0.stories, mine: false)
        }, start: start)
    }
}

struct StoryPerson: Hashable {
    let id: String
    let name: String
    let handle: String
    let avatar: String
    var stories: [Story]
    let mine: Bool
}

struct StoryReel: Identifiable {
    let id = UUID()
    let people: [StoryPerson]
    let start: Int
}

/// Which stories have been liked this session, so a second like sends no
/// second heart (`storyLikes`).
@MainActor
enum StoryLikes {
    static var marks: [String: Bool] = [:]

    static func key(_ person: StoryPerson, _ story: Story) -> String {
        let who = person.id.isEmpty ? person.handle : person.id
        return who + "|" + String(story.at) + "|" + (story.query ?? story.kind ?? "")
    }
}

/// The story viewer (`#storyOverlay`): always dark; six seconds a story
/// with a bar each; a tap on the right goes on, the left goes back (past the
/// first, to the previous friend's last); hold to pause, which hides the
/// chrome; a swipe moves to the next or previous friend, each opened on
/// their first unseen story. A vibe's title or Play episode opens it; a
/// friend's has a reply box with its send inside, a heart and the VIBE!
/// arrows (`story-reply`); ⋯ saves, shares, queues, deletes your own or
/// reports somebody else's.
struct StoryViewer: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let reel: StoryReel
    let markSeen: (String, Double) -> Void
    let seenAt: (String) -> Double

    @State private var people: [StoryPerson] = []
    @State private var person = 0
    @State private var index = 0
    @State private var progress: Double = 0
    @State private var held = false
    @State private var moved = false
    @State private var pressing = false
    @State private var pressID = 0
    @State private var reply = ""
    @State private var liked: [String: Bool] = [:]
    @State private var reporting = false
    /// The story's ⋯ (`openStoryMenu`), and what it opens.
    @State private var storyMenu = false
    @State private var addingTopic = false
    @State private var blocking = false
    @State private var resharing = false
    @State private var sharing: ShareSubject?
    @FocusState private var typing: Bool

    private static let seconds = 6.0
    private static let paper = Color(hex: 0xF4EFE4)
    private static let gold = Color(hex: 0xE0B563)

    private var ready: Bool { people.indices.contains(person) && people[person].stories.indices.contains(index) }
    private var who: StoryPerson { people[person] }
    private var story: Story { who.stories[index] }
    private var isNote: Bool { story.kind == "note" }

    var body: some View {
        ZStack {
            LinearGradient(colors: [Color(hex: 0x2A2233), Color(hex: 0x16121C)], startPoint: .top, endPoint: .bottom)
                .ignoresSafeArea()
            if ready {
                GeometryReader { geo in
                    Color.clear
                        .contentShape(Rectangle())
                        .gesture(surfaceGesture(width: geo.size.width))
                }
                .ignoresSafeArea()
                if isNote {
                    // Only its @mentions take a tap (`storyOpenTag`); the
                    // rest lets the story's own taps and holds through.
                    NoteCanvas(story: story, openTag: { tag in openTag(tag) })
                        .padding(.horizontal, 12)
                        .padding(.top, 52)
                        .padding(.bottom, who.mine ? 40 : 76)
                }
                VStack(spacing: 0) {
                    VStack(spacing: 0) {
                        bars.padding(.top, 8)
                        header.padding(.top, 12)
                    }
                    .opacity(held ? 0 : 1)
                    Spacer(minLength: 0)
                    if !isNote { main }
                    Spacer(minLength: 0)
                    card.opacity(held ? 0 : 1)
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 12)
                .animation(.easeOut(duration: 0.2), value: held)
            }
        }
        .foregroundStyle(Self.paper)
        // The app's toast over the story: "Sent to Beth" after a reply or a
        // share, a block's refusal - the tab bar's own toast is under it.
        .overlay(alignment: .bottom) {
            ToastView()
                .environmentObject(model.nowPlaying)
                .padding(.bottom, 96)
        }
        .onAppear {
            let start = min(reel.start, max(0, reel.people.count - 1))
            people = reel.people
            person = start
            index = firstUnseen(start, in: reel.people)
            liked = StoryLikes.marks
        }
        .task(id: "\(person)-\(index)-\(people.count)") { await tick() }
        .confirmationDialog(ready ? (story.title ?? "") : "", isPresented: $storyMenu, titleVisibility: .hidden) {
            if ready { storyMenuButtons }
        }
        .sheet(isPresented: $addingTopic) {
            if ready {
                AddToMixSheet(query: story.query ?? "", title: story.title ?? story.query ?? "")
                    .environmentObject(model)
                    .environmentObject(model.mixes)
            }
        }
        .sheet(isPresented: $resharing) {
            if ready {
                StoryVibeSheet(key: storyKey(), title: story.title ?? story.query ?? "")
                    .environmentObject(model)
            }
        }
        .sheet(item: $sharing) { subject in
            ShareSheet(subject: subject)
                .environmentObject(model)
                .environmentObject(model.nowPlaying)
        }
        .blockConfirmation(isPresented: $blocking, name: ready ? who.name : "", handle: ready ? who.handle : "",
                           api: model.safety, onBlocked: {
                               model.nowPlaying.say("Blocked")
                               Task {
                                   await model.social.loadGraph()
                                   await model.loadCircle()
                               }
                               dismiss()
                           }, onError: { model.nowPlaying.say($0) })
        .sheet(isPresented: $reporting) {
            if ready {
                FAMReportSheet(api: model.safety,
                               subject: isNote ? .note(id: story.id) : .vibe(id: story.id),
                               say: { model.nowPlaying.say($0) }) { receipt in
                    // Taken off this listener's screen at once: the story closes.
                    if receipt?.hidden == true {
                        Task { @MainActor in
                            try? await Task.sleep(nanoseconds: 450_000_000)
                            dismiss()
                        }
                    }
                }
            }
        }
    }

    // MARK: The pieces

    private var bars: some View {
        HStack(spacing: 4) {
            ForEach(0..<who.stories.count, id: \.self) { i in
                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        RoundedRectangle(cornerRadius: 2).fill(Self.paper.opacity(0.25))
                        RoundedRectangle(cornerRadius: 2).fill(Self.paper)
                            .frame(width: geo.size.width * (i < index ? 1 : i == index ? progress : 0))
                    }
                }
                .frame(height: 2.5)
            }
        }
    }

    /// The avatar with its copper ring, the name ("Your story", else the
    /// name or "@handle") and the age. On somebody else's story the
    /// picture and name open their profile, once the story has closed.
    private var header: some View {
        HStack(spacing: 10) {
            if who.mine || who.handle.isEmpty {
                headerWho
            } else {
                Button { openProfile() } label: { headerWho }
                    .buttonStyle(.plain)
                    .accessibilityHint("Opens their profile")
            }
            Spacer()
            Button { storyMenu = true } label: {
                WebFill(path: Icons.dots(1.9), size: 18).frame(width: 32, height: 32)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("More")
            Button { dismiss() } label: {
                WebIcon(path: Icons.close, size: 14, line: 2.4).frame(width: 32, height: 32)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Close")
        }
    }

    private var headerWho: some View {
        HStack(spacing: 10) {
            Face(name: who.mine ? (model.circle?.me?.name ?? "") : who.name, handle: who.handle,
                 avatar: who.avatar, size: 34, initialsSize: 13)
                .overlay(Circle().inset(by: -1).stroke(Self.gold, lineWidth: 2))
            VStack(alignment: .leading, spacing: 1) {
                Text(who.mine ? "Your story" : (who.name.isEmpty ? "@" + who.handle : who.name))
                    .font(FAMFont.body(13, .bold))
                    .lineLimit(1)
                Text(age(story.at) + (story.close == true ? " \u{00B7} Close Friends" : ""))
                    .font(FAMFont.body(11))
                    .foregroundStyle(Self.paper.opacity(0.6))
            }
            .accessibilityElement(children: .combine)
        }
        .contentShape(Rectangle())
    }

    /// A tag on a note opens that person's profile, once the story has
    /// closed (`storyOpenTag`).
    private func openTag(_ tag: StoryTag) {
        guard !tag.handle.isEmpty else { return }
        let route = SocialRoute.person(PersonOpen(handle: tag.handle, name: tag.name))
        dismiss()
        model.social.openAfterClose(route)
    }

    private func openProfile() {
        let route = SocialRoute.person(PersonOpen(handle: who.handle, userID: who.id, name: who.name,
                                                  avatar: who.avatar))
        dismiss()
        model.social.openAfterClose(route)
    }

    /// `.story-main`, centred: VIBE, the title (a tap plays), who vibed it,
    /// and the caption in its bubble.
    private var main: some View {
        VStack(spacing: 10) {
            Text("VIBE")
                .font(FAMFont.mono(10))
                .tracking(1.8)
                .foregroundStyle(Self.gold)
                .allowsHitTesting(false)
            Text(story.title ?? story.query ?? "")
                .font(FAMFont.heading(30, .medium))
                .lineSpacing(0)
                .multilineTextAlignment(.center)
                .onTapGesture { play() }
                .accessibilityAddTraits(.isButton)
                .accessibilityHint("Plays the episode")
            Text("\(who.mine ? "You" : firstName) vibed this \u{00B7} \(FAMAPI.minutes(story.minutes ?? FAMAPI.browseMinutes)) min")
                .font(FAMFont.body(12))
                .foregroundStyle(Self.paper.opacity(0.6))
                .allowsHitTesting(false)
            if let caption = story.caption, !caption.isEmpty {
                Text(caption)
                    .font(FAMFont.body(14))
                    .lineSpacing(6)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 13)
                    .padding(.vertical, 9)
                    .background(Self.paper.opacity(0.12), in: RoundedRectangle(cornerRadius: 14))
                    .padding(.top, 6)
                    .allowsHitTesting(false)
            }
        }
        .padding(.horizontal, 8)
        .frame(maxWidth: .infinity)
    }

    /// `.story-card`: the full-width copper Play episode (not on a note),
    /// then the reply row on somebody else's.
    private var card: some View {
        VStack(spacing: 10) {
            if !isNote {
                Button { play() } label: {
                    HStack(spacing: 8) {
                        WebFill(path: Icons.play, size: 14)
                        Text("Play episode").font(FAMFont.body(13.5, .semibold))
                    }
                    .foregroundStyle(FAMColor.ink)
                    .frame(maxWidth: .infinity)
                    .frame(height: 46)
                    .background(Self.gold, in: Capsule())
                }
                .buttonStyle(.plain)
                .padding(.top, 8)
            }
            if !who.mine && !who.id.isEmpty { replyRow }
        }
    }

    private var replyRow: some View {
        let mark = StoryLikes.key(who, story)
        let on = liked[mark] == true
        return HStack(spacing: 6) {
            HStack(spacing: 0) {
                TextField("", text: $reply, prompt: Text("Send message...").foregroundColor(Self.paper.opacity(0.75)))
                    .font(FAMFont.body(14))
                    .focused($typing)
                    .submitLabel(.send)
                    .onSubmit(sendReply)
                    .padding(.leading, 18)
                    .padding(.trailing, 4)
                Button(action: sendReply) {
                    WebIcon(path: Icons.send, size: 20, line: 1.9).frame(width: 40, height: 40)
                }
                .buttonStyle(.plain)
                .padding(.trailing, 2)
                .accessibilityLabel("Send")
            }
            .frame(height: 44)
            .background(Color.black.opacity(0.18), in: Capsule())
            .overlay(Capsule().stroke(typing ? Self.paper : Self.paper.opacity(0.55)))
            Button { like() } label: {
                ZStack {
                    if on {
                        Glyph(path: Icons.heart, box: CGRect(x: 0, y: 0, width: 24, height: 24))
                            .fill(Color(hex: 0xFF4D5E))
                            .frame(width: 24, height: 24)
                    }
                    WebIcon(path: Icons.heart, size: 24, line: 1.9)
                        .foregroundStyle(on ? Color(hex: 0xFF4D5E) : Self.paper)
                }
                .frame(width: 42, height: 44)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Like")
            .accessibilityAddTraits(on ? .isSelected : [])
            if !isNote {
                Button { resharing = true } label: {
                    WebIcon(path: Icons.reshare, size: 24, line: 1.9).frame(width: 42, height: 44)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("VIBE! to your story")
            }
        }
    }

    // MARK: Gestures: tap, hold, swipe

    /// One gesture for the story's surface, as the web's pointer handlers:
    /// held 220 ms without travelling pauses and hides the chrome until the
    /// finger lifts; a horizontal swipe of 50 pt changes friend; a drag down
    /// closes; otherwise a tap steps back (left half) or on (right half).
    private func surfaceGesture(width: CGFloat) -> some Gesture {
        DragGesture(minimumDistance: 0)
            .onChanged { value in
                if !pressing {
                    pressing = true
                    moved = false
                    pressID += 1
                    let mine = pressID
                    Task { @MainActor in
                        try? await Task.sleep(nanoseconds: 220_000_000)
                        if pressID == mine && pressing && !moved { held = true }
                    }
                }
                if abs(value.translation.width) > 12 || abs(value.translation.height) > 12 { moved = true }
            }
            .onEnded { value in
                let wasHeld = held
                pressing = false
                pressID += 1
                held = false
                if wasHeld { return }
                // A tap away from the reply box only puts the keyboard away.
                if typing {
                    typing = false
                    return
                }
                let dx = value.translation.width
                let dy = value.translation.height
                if abs(dx) >= 50 && abs(dx) >= abs(dy) * 1.2 {
                    friend(dx < 0 ? 1 : -1)
                    return
                }
                if dy > 80 && dy > abs(dx) {
                    dismiss()
                    return
                }
                if abs(dx) < 12 && abs(dy) < 12 {
                    if value.startLocation.x < width / 2 { back() } else { forward() }
                }
            }
    }

    // MARK: The ⋯ menu

    /// The story holds while its menu, or anything the menu opened, is up.
    private var paused: Bool {
        held || typing || storyMenu || addingTopic || reporting || blocking || resharing || sharing != nil
    }

    private var firstName: String {
        let first = who.name.split(separator: " ").first.map(String.init) ?? ""
        return first.isEmpty ? (who.handle.isEmpty ? "Someone" : who.handle) : first
    }

    /// `openStoryMenu` / `openNoteMenu`: a vibe's episode can be saved,
    /// shared, queued or added to a mix; your own vibe deleted and your own
    /// note taken down; somebody else's reported, and its person blocked.
    /// What opens another sheet waits for the dialog to close.
    @ViewBuilder
    private var storyMenuButtons: some View {
        if !isNote {
            Button("Save for later") { saveStory() }
            Button("Share") { shareStory() }
            Button("Add to Queue") { queueStory() }
            Button("Add topic to playlist") { later { addingTopic = true } }
        }
        if who.mine {
            if isNote {
                Button("Remove from your story", role: .destructive) { removeNote() }
            } else {
                Button("Delete vibe", role: .destructive) { deleteVibe() }
            }
        } else {
            Button("Report", role: .destructive) { later { reporting = true } }
            if !who.handle.isEmpty {
                Button("Block \(firstName)", role: .destructive) { later { blocking = true } }
            }
        }
        Button("Cancel", role: .cancel) {}
    }

    private func later(_ action: @escaping () -> Void) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35, execute: action)
    }

    private func storyKey() -> EpisodeKey {
        EpisodeKey(query: story.query ?? "", minutes: story.minutes ?? FAMAPI.browseMinutes)
    }

    private func saveStory() {
        let key = storyKey()
        let title = story.title ?? key.query
        Task {
            do {
                try await model.api.save(key, title: title, source: "story")
                model.nowPlaying.say("Saved for Later")
            } catch {
                model.nowPlaying.say(MixesModel.sentence(error, else: "Could not save that"))
            }
        }
    }

    /// "Share this FAM" for the vibe's episode (`openShareModal(ep)`): to a
    /// friend or a group inside FAM, or anywhere else. The story holds
    /// while the sheet is up.
    private func shareStory() {
        let key = storyKey()
        let title = story.title ?? key.query
        later { sharing = ShareSubject(key: key, title: title) }
    }

    private func queueStory() {
        let key = storyKey()
        model.nowPlaying.addToQueue(Episode(key: key, title: story.title ?? key.query, surface: ""))
    }

    /// "Delete vibe": deleted everywhere, not only off the story
    /// (`deleteVibeFromStory`, `DELETE /api/vibe`).
    private func deleteVibe() {
        let gone = story
        let key = storyKey()
        Task {
            do {
                try await model.api.unvibe(key)
                model.nowPlaying.say("Vibe deleted")
                drop(gone)
                await model.loadCircle()
            } catch {
                model.nowPlaying.say("Could not delete that vibe")
            }
        }
    }

    /// "Remove from your story": a note is only a story, so it is deleted
    /// (`removeFromMyStory`, `DELETE /api/story/note/{id}`).
    private func removeNote() {
        let gone = story
        Task {
            do {
                try await model.api.removeNote(gone.id)
                model.nowPlaying.say("Removed from your story")
                drop(gone)
                await model.loadCircle()
            } catch {
                model.nowPlaying.say("Could not take that down")
            }
        }
    }

    /// Out of the reel; the next one slides into its place, and only one
    /// before it moves the position back. None left closes the viewer.
    private func drop(_ gone: Story) {
        guard people.indices.contains(person) else { return }
        var list = people[person].stories
        guard let at = list.firstIndex(where: { $0.id == gone.id && $0.at == gone.at }) else { return }
        list.remove(at: at)
        var next = index
        if at < next { next -= 1 }
        if next >= list.count { next = list.count - 1 }
        if list.isEmpty {
            dismiss()
            return
        }
        people[person].stories = list
        index = max(0, next)
    }

    // MARK: Stepping

    private func tick() async {
        guard ready else { return }
        markSeen(who.id, story.at)
        progress = 0
        while progress < 1 {
            try? await Task.sleep(nanoseconds: 50_000_000)
            if Task.isCancelled { return }
            if paused { continue }
            progress += 0.05 / Self.seconds
        }
        forward()
    }

    /// Where a friend's stories open: the first one not yet seen.
    private func firstUnseen(_ p: Int, in list: [StoryPerson]) -> Int {
        guard list.indices.contains(p) else { return 0 }
        let after = seenAt(list[p].id)
        return list[p].stories.firstIndex(where: { $0.at > after }) ?? 0
    }

    /// On: the next story, else the next friend's first, else closed.
    private func forward() {
        if index + 1 < who.stories.count {
            index += 1
        } else if person + 1 < people.count {
            person += 1
            index = 0
        } else {
            dismiss()
        }
    }

    /// Back: the story before, else the previous friend's last, else this
    /// one again from the start.
    private func back() {
        if index > 0 {
            index -= 1
        } else if person > 0 {
            person -= 1
            index = max(0, people[person].stories.count - 1)
        } else {
            progress = 0
        }
    }

    /// A swipe: the next or previous friend, on their first unseen story.
    /// Past the last closes; before the first starts this friend again.
    private func friend(_ dir: Int) {
        let next = person + dir
        if next >= 0 && next < people.count {
            person = next
            index = firstUnseen(next, in: people)
        } else if dir > 0 {
            dismiss()
        } else {
            index = 0
            progress = 0
        }
    }

    // MARK: Actions

    /// The episode opens in the full player; one already on the player is
    /// not started again - the player is opened on it (`playStory`).
    private func play() {
        guard let q = story.query, !q.isEmpty else { return }
        if model.player.isLoaded && model.nowPlaying.episode?.key.query == q {
            dismiss()
            model.showPlayer = true
            return
        }
        let ep = Episode(key: EpisodeKey(query: q, minutes: story.minutes ?? FAMAPI.browseMinutes),
                         title: story.title ?? q, surface: "other")
        dismiss()
        model.play(ep)
    }

    private func sendReply() {
        let text = reply.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, ready else { return }
        let s = story
        let to = who.id
        let name = firstName
        reply = ""
        typing = false
        Task {
            do {
                try await model.api.reply(to: to, text: text, story: s)
                model.nowPlaying.say("Sent to \(name)")
            } catch {
                model.nowPlaying.say((error as? FAMError)?.errorDescription ?? "Could not send that")
            }
        }
    }

    /// The heart (`likeStory`): the episode's own like as a taste signal
    /// (not on a note), and the first like on a story sends them a heart.
    private func like() {
        guard ready else { return }
        let s = story
        let mark = StoryLikes.key(who, s)
        let before = liked[mark]
        let on = before != true
        liked[mark] = on
        StoryLikes.marks[mark] = on
        if !isNote {
            let query = s.query ?? ""
            let minutes = s.minutes ?? FAMAPI.browseMinutes
            Task { await model.api.rate(query: query, minutes: minutes, like: on) }
        }
        if before == nil && on {
            let to = who.id
            Task { try? await model.api.reply(to: to, text: FAMAPI.storyLike, story: s) }
        }
    }

    /// "just now", "Nm", "Nh" (`storyAgo`, rounded).
    private func age(_ at: Double) -> String {
        let mins = max(0, Int(((Date().timeIntervalSince1970 - at) / 60).rounded()))
        if mins < 1 { return "just now" }
        if mins < 60 { return "\(mins)m" }
        return "\(Int((Double(mins) / 60).rounded()))h"
    }
}

/// A note (§297), drawn from its layout (`noteCanvasHTML`): its background
/// by name, the words at their place and size in their face - dark on the
/// light backgrounds - and the stickers. Positions are fractions of the
/// canvas and sizes are in 360ths of its width, as on the web, so it reads
/// the same on any phone. An @mention opens that person's profile when the
/// caller says how (`openTag`); nothing else on the canvas takes a tap.
struct NoteCanvas: View {
    let story: Story
    var openTag: ((StoryTag) -> Void)? = nil

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            let h = geo.size.height
            let style = story.style
            let unit = w / 360
            let cx = style?.cx ?? 0.5
            let cy = style?.cy ?? 0.5
            let room = max(10, min(86, (200 * min(cx, 1 - cx)).rounded() - 4)) / 100
            let light = style?.bg == "cream" || style?.bg == "candy"
            ZStack(alignment: .topLeading) {
                Self.background(style?.bg ?? "ink")
                    .allowsHitTesting(false)
                if let text = story.text, !text.isEmpty {
                    Text(text)
                        .font(Self.font(style?.font ?? "strong", size: CGFloat(style?.size ?? 24) * unit))
                        .foregroundStyle(light ? Color(hex: 0x1A1620) : Color(hex: 0xF4EFE4))
                        .shadow(color: light ? .clear : .black.opacity(0.55), radius: 4, y: 1)
                        .multilineTextAlignment(.center)
                        .lineSpacing(2)
                        .frame(maxWidth: w * CGFloat(room))
                        .fixedSize(horizontal: false, vertical: true)
                        .position(x: w * CGFloat(cx), y: h * CGFloat(cy))
                        .allowsHitTesting(false)
                }
                ForEach(Array((style?.stickers ?? []).enumerated()), id: \.offset) { _, sticker in
                    stickerView(sticker, unit: unit)
                        .scaleEffect(CGFloat(sticker.s))
                        .rotationEffect(.degrees(sticker.r))
                        .position(x: w * CGFloat(sticker.x), y: h * CGFloat(sticker.y))
                        .allowsHitTesting(false)
                }
                ForEach(Array((story.tags ?? []).enumerated()), id: \.offset) { _, tag in
                    tagView(tag, unit: unit)
                        .position(x: w * CGFloat(tag.x), y: h * CGFloat(tag.y))
                }
            }
            .frame(width: w, height: h)
        }
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(story.text ?? "A story")
    }

    /// An @mention: a button when the caller can open a profile, else
    /// drawn and passed through.
    @ViewBuilder private func tagView(_ tag: StoryTag, unit: CGFloat) -> some View {
        let label = Text("@" + tag.handle)
            .font(FAMFont.body(14 * unit, .bold))
            .foregroundStyle(Color(hex: 0x1A1620))
            .padding(.horizontal, 10 * unit)
            .padding(.vertical, 5 * unit)
            .background(Color(hex: 0xF4EFE4).opacity(0.94), in: RoundedRectangle(cornerRadius: 7 * unit))
            .fixedSize()
        if let openTag, !tag.handle.isEmpty {
            Button { openTag(tag) } label: { label }
                .buttonStyle(.plain)
        } else {
            label.allowsHitTesting(false)
        }
    }

    @ViewBuilder private func stickerView(_ s: StorySticker, unit: CGFloat) -> some View {
        let word = s.k.contains(where: { $0.isUppercase })
        if word {
            Text(s.k)
                .font(FAMFont.mono(13 * unit, bold: true))
                .tracking(13 * unit * 0.06)
                .foregroundStyle(FAMColor.ink)
                .padding(.horizontal, 13 * unit * 0.8)
                .padding(.vertical, 13 * unit * 0.45)
                .background(FAMColor.copper, in: RoundedRectangle(cornerRadius: 13 * unit * 0.6))
                .fixedSize()
        } else {
            Text(s.k).font(.system(size: 46 * unit)).fixedSize()
        }
    }

    /// The caption's faces (`.sc-cap.f-*`).
    static func font(_ name: String, size: CGFloat) -> Font {
        switch name {
        case "classic": return FAMFont.body(size, .semibold)
        case "type": return FAMFont.mono(size)
        case "serif": return .custom("Georgia-Italic", size: size)
        case "script": return .custom("SnellRoundhand", size: size)
        default: return FAMFont.heading(size, .bold)
        }
    }

    /// `NOTE_BACKGROUNDS`, the gradients at 160°.
    @ViewBuilder static func background(_ name: String) -> some View {
        let stops = Self.stops(name)
        if stops.count == 1 {
            stops[0].color
        } else {
            LinearGradient(stops: stops, startPoint: UnitPoint(x: 0.33, y: 0.03), endPoint: UnitPoint(x: 0.67, y: 0.97))
        }
    }

    private static func stops(_ name: String) -> [Gradient.Stop] {
        func s(_ hex: UInt32, _ at: CGFloat) -> Gradient.Stop { Gradient.Stop(color: Color(hex: hex), location: at) }
        switch name {
        case "copper": return [s(0xC08A2E, 0)]
        case "sunset": return [s(0xFEDA75, 0), s(0xFA7E1E, 0.35), s(0xD62976, 0.75), s(0x962FBF, 1)]
        case "ocean": return [s(0x5EE7DF, 0), s(0x3A7BD5, 0.55), s(0x283C86, 1)]
        case "forest": return [s(0xA8E063, 0), s(0x3E8E5E, 0.55), s(0x134E3A, 1)]
        case "berry": return [s(0xF78CA0, 0), s(0xC43A79, 0.5), s(0x5B1A5E, 1)]
        case "dusk": return [s(0x3A2F44, 0), s(0x6B4C7A, 0.5), s(0xE0B563, 1)]
        case "cream": return [s(0xF4EFE4, 0)]
        case "night": return [s(0x000000, 0)]
        case "candy": return [s(0xFBC2EB, 0), s(0xA6C1EE, 1)]
        default: return [s(0x18151F, 0)]
        }
    }
}

/// VIBE! a friend's story to your own (`shareStoryToMine`): the episode
/// with a caption, posted with `/api/vibe`. The web opens its full story
/// editor here; that editor is not ported (PARITY.md), so this is the
/// caption box the player's VIBE! uses.
struct StoryVibeSheet: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let key: EpisodeKey
    let title: String
    @State private var caption = ""
    @State private var posting = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("VIBE! to your story").font(FAMFont.heading(16, .medium)).foregroundStyle(FAMColor.text)
            Text(title)
                .font(FAMFont.heading(14))
                .foregroundStyle(FAMColor.text)
                .lineSpacing(3)
            TextField("", text: $caption, prompt: Text("Say something about it").foregroundColor(FAMColor.textFaint),
                      axis: .vertical)
                .font(FAMFont.body(14))
                .foregroundStyle(FAMColor.text)
                .lineLimit(1...4)
                .onChange(of: caption) { _, text in
                    if text.count > 150 { caption = String(text.prefix(150)) }
                }
                .padding(12)
                .background(FAMColor.background, in: RoundedRectangle(cornerRadius: 10))
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(FAMColor.borderStrong))
            Button {
                post()
            } label: {
                if posting { ProgressView().tint(FAMColor.ink) } else { Text("VIBE!") }
            }
            .buttonStyle(CopperButtonStyle())
            .disabled(posting)
            Spacer(minLength: 0)
        }
        .padding(20)
        .background(FAMColor.surface)
        .presentationDetents([.medium])
        .presentationBackground(FAMColor.surface)
    }

    private func post() {
        posting = true
        let words = caption
        Task {
            do {
                try await model.api.vibe(key, title: title, thread: "", caption: words)
                model.nowPlaying.say("Vibed to your story")
                posting = false
                dismiss()
                await model.loadCircle()
            } catch {
                posting = false
                model.nowPlaying.say((error as? FAMError)?.errorDescription ?? "Could not vibe that")
            }
        }
    }
}
