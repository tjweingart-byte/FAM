import FAMSafety
import SwiftUI

/// The faces row at the top of DailyFAM (`#myfamCircle`, §297): your story
/// first, then friends and people you follow - a gradient ring while they
/// have stories you have not seen - then Invite. Nothing for a listener
/// with nobody and no story of their own.
struct FacesRow: View {
    @EnvironmentObject private var model: AppModel
    @State private var viewing: StoryReel?
    @State private var invite = false
    @AppStorage("storiesSeen") private var seenJSON = "{}"

    var body: some View {
        if let circle = model.circle, !(circle.circle.isEmpty && circle.me == nil) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(alignment: .top, spacing: 14) {
                    if let me = circle.me {
                        let mine = me.stories ?? []
                        faceItem(name: me.name ?? "", handle: me.handle ?? "", avatar: me.avatar ?? "",
                                 label: "Your story", ring: mine.isEmpty ? .none : ring(for: "me", mine)) {
                            if !mine.isEmpty {
                                viewing = StoryReel(people: [StoryPerson(id: "me", name: "Your story",
                                                                         handle: me.handle ?? "",
                                                                         avatar: me.avatar ?? "",
                                                                         stories: mine, mine: true)], start: 0)
                            }
                        }
                    }
                    ForEach(Array(circle.circle.enumerated()), id: \.element.id) { index, person in
                        faceItem(name: person.name, handle: person.handle, avatar: person.avatar,
                                 label: firstName(person.name),
                                 ring: person.stories.isEmpty ? (person.fresh == true ? .fresh : .none)
                                                             : ring(for: person.userId, person.stories)) {
                            open(circle: circle.circle, at: index)
                        }
                    }
                    Button { invite = true } label: {
                        VStack(spacing: 6) {
                            Image(systemName: "plus")
                                .font(.system(size: 20, weight: .semibold))
                                .foregroundStyle(FAMColor.textMuted)
                                .frame(width: 66, height: 66)
                                .overlay(Circle().stroke(FAMColor.borderStrong, style: StrokeStyle(lineWidth: 1.5, dash: [4, 3])))
                                .frame(width: 72, height: 72)
                            Text("Invite").font(FAMFont.body(11)).foregroundStyle(FAMColor.text)
                        }
                        .frame(width: 72)
                    }
                    .buttonStyle(.plain)
                }
                .padding(.top, 4)
            }
            .fullScreenCover(item: $viewing) { reel in
                StoryViewer(reel: reel, markSeen: markSeen)
                    .environmentObject(model)
            }
            .sheet(isPresented: $invite) {
                ActivityView(items: [inviteText]) { invite = false }
            }
        }
    }

    private enum Ring { case none, fresh, seen }

    private func faceItem(name: String, handle: String, avatar: String, label: String, ring: Ring,
                          tap: @escaping () -> Void) -> some View {
        Button(action: tap) {
            VStack(spacing: 6) {
                ZStack {
                    switch ring {
                    case .fresh:
                        Circle().fill(LinearGradient(colors: [Color(hex: 0x7A5A2E), Color(hex: 0xB98A3E),
                                                              Color(hex: 0xE0B563), Color(hex: 0xF3D58E)],
                                                     startPoint: .bottomLeading, endPoint: .topTrailing))
                    case .seen:
                        Circle().fill(FAMColor.textFaint)
                    case .none:
                        Color.clear
                    }
                    Face(name: name, handle: handle, avatar: avatar, size: ring == .none ? 72 : 66 - (ring == .seen ? 3 : 6))
                        .overlay(Circle().stroke(FAMColor.background, lineWidth: ring == .none ? 0 : 3))
                }
                .frame(width: 72, height: 72)
                Text(label)
                    .font(FAMFont.body(11))
                    .foregroundStyle(FAMColor.text)
                    .lineLimit(1)
                    .frame(maxWidth: 72)
            }
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
    }

    private func firstName(_ name: String) -> String {
        String(name.split(separator: " ").first ?? Substring(name))
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

    private func open(circle people: [CirclePerson], at index: Int) {
        let withStories = people.filter { !$0.stories.isEmpty }
        guard !people[index].stories.isEmpty,
              let start = withStories.firstIndex(where: { $0.id == people[index].id }) else { return }
        viewing = StoryReel(people: withStories.map {
            StoryPerson(id: $0.userId, name: $0.name, handle: $0.handle, avatar: $0.avatar,
                        stories: $0.stories, mine: false)
        }, start: start)
    }

    private var inviteText: String {
        let handle = model.circle?.me?.handle ?? ""
        return "Come listen with me on FAM" + (handle.isEmpty ? "." : " — I'm @\(handle).") + " https://familiarize.net/"
    }
}

struct StoryPerson: Hashable {
    let id: String
    let name: String
    let handle: String
    let avatar: String
    let stories: [Story]
    let mine: Bool
}

struct StoryReel: Identifiable {
    let id = UUID()
    let people: [StoryPerson]
    let start: Int
}

/// The story viewer (`#storyOverlay`): always dark; six seconds a story
/// with a bar each; tap the right to go on, the left to go back, hold to
/// pause; a swipe moves to the next friend. A vibe plays its episode; a
/// friend's has a reply box and a heart (`story-reply`); ⋯ reports.
struct StoryViewer: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let reel: StoryReel
    let markSeen: (String, Double) -> Void

    @State private var person = 0
    @State private var index = 0
    @State private var progress: Double = 0
    @State private var holding = false
    @State private var reply = ""
    @State private var liked: Set<Int> = []
    @State private var reporting = false
    @FocusState private var typing: Bool

    private static let seconds = 6.0
    private static let paper = Color(hex: 0xF4EFE4)

    private var who: StoryPerson { reel.people[person] }
    private var story: Story { who.stories[index] }

    var body: some View {
        ZStack {
            Color(hex: 0x16141C).ignoresSafeArea()
            VStack(alignment: .leading, spacing: 0) {
                bars.padding(.top, 8)
                header.padding(.top, 10)
                Spacer()
                content
                Spacer()
                if !who.mine { replyRow }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 12)
        }
        .contentShape(Rectangle())
        .gesture(DragGesture(minimumDistance: 50).onEnded { value in
            if value.translation.width < -50 { nextPerson() }
            if value.translation.width > 50 { previousPerson() }
            if value.translation.height > 80 { dismiss() }
        })
        .onLongPressGesture(minimumDuration: 0.22, perform: {}, onPressingChanged: { holding = $0 })
        .onAppear {
            person = reel.start
            markSeen(who.id, story.at)
        }
        .task(id: "\(person)-\(index)") { await tick() }
        .sheet(isPresented: $reporting) {
            ReportSheet(api: model.safety,
                        subject: story.kind == "note" ? .note(id: story.id) : .vibe(id: story.id)) { _ in }
        }
    }

    private var bars: some View {
        HStack(spacing: 4) {
            ForEach(0..<who.stories.count, id: \.self) { i in
                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        Capsule().fill(Self.paper.opacity(0.3))
                        Capsule().fill(Self.paper)
                            .frame(width: geo.size.width * (i < index ? 1 : i == index ? progress : 0))
                    }
                }
                .frame(height: 2.5)
            }
        }
    }

    private var header: some View {
        HStack(spacing: 10) {
            Face(name: who.name, handle: who.handle, avatar: who.avatar, size: 32)
            VStack(alignment: .leading, spacing: 1) {
                Text(who.name).font(FAMFont.body(13, .semibold)).foregroundStyle(Self.paper)
                Text(age(story.at)).font(FAMFont.mono(10)).foregroundStyle(Self.paper.opacity(0.7))
            }
            Spacer()
            if !who.mine {
                Button { reporting = true } label: {
                    Image(systemName: "ellipsis").foregroundStyle(Self.paper).frame(width: 36, height: 36)
                }
                .accessibilityLabel("Report")
            }
            Button { dismiss() } label: {
                Image(systemName: "xmark").foregroundStyle(Self.paper).frame(width: 36, height: 36)
            }
            .accessibilityLabel("Close")
        }
    }

    @ViewBuilder private var content: some View {
        // Taps: the left third goes back, the rest goes on.
        GeometryReader { geo in
            VStack(alignment: .leading, spacing: 12) {
                if story.kind == "note" {
                    Text(story.text ?? "")
                        .font(FAMFont.heading(26, .medium))
                        .foregroundStyle(Self.paper)
                } else {
                    Text("Vibe").monoLabel(9, color: Color(hex: 0xE0B563), bold: true)
                    Text(story.title ?? "")
                        .font(FAMFont.heading(26, .medium))
                        .foregroundStyle(Self.paper)
                    Text("\(who.mine ? "You" : who.name) vibed this · \(FAMAPI.minutes(story.minutes ?? FAMAPI.browseMinutes)) min")
                        .font(FAMFont.mono(10))
                        .foregroundStyle(Self.paper.opacity(0.7))
                    if let caption = story.caption, !caption.isEmpty {
                        Text(caption).font(FAMFont.body(15)).foregroundStyle(Self.paper)
                    }
                    Button {
                        play()
                    } label: {
                        Label("Play episode", systemImage: "play.fill")
                            .font(FAMFont.body(14, .bold))
                            .foregroundStyle(FAMColor.ink)
                            .padding(.horizontal, 18)
                            .padding(.vertical, 10)
                            .background(Color(hex: 0xE0B563), in: Capsule())
                    }
                    .padding(.top, 6)
                }
            }
            .frame(width: geo.size.width, height: geo.size.height, alignment: .leading)
            .contentShape(Rectangle())
            .onTapGesture { location in
                if location.x < geo.size.width / 3 { back() } else { forward() }
            }
        }
    }

    private var replyRow: some View {
        HStack(spacing: 10) {
            TextField("", text: $reply, prompt: Text("Send message...").foregroundColor(Self.paper.opacity(0.6)))
                .font(FAMFont.body(14))
                .foregroundStyle(Self.paper)
                .focused($typing)
                .submitLabel(.send)
                .onSubmit(sendReply)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .overlay(Capsule().stroke(Self.paper.opacity(0.4)))
            if story.kind != "note" {
                Button {
                    let on = !liked.contains(story.id)
                    if on { liked.insert(story.id) } else { liked.remove(story.id) }
                    let s = story
                    Task { await model.api.rate(query: s.query ?? "", minutes: s.minutes ?? FAMAPI.browseMinutes, like: on) }
                } label: {
                    Image(systemName: liked.contains(story.id) ? "heart.fill" : "heart")
                        .font(.system(size: 22))
                        .foregroundStyle(liked.contains(story.id) ? FAMColor.coral : Self.paper)
                }
                .accessibilityLabel("Like")
            }
        }
    }

    private func tick() async {
        progress = 0
        while progress < 1 {
            try? await Task.sleep(nanoseconds: 50_000_000)
            if Task.isCancelled { return }
            if holding || typing { continue }
            progress += 0.05 / Self.seconds
        }
        forward()
    }

    private func forward() {
        if index + 1 < who.stories.count {
            index += 1
            markSeen(who.id, story.at)
        } else {
            nextPerson()
        }
    }

    private func back() {
        if index > 0 { index -= 1 } else if person > 0 { person -= 1; index = 0 }
    }

    private func nextPerson() {
        guard person + 1 < reel.people.count else { dismiss(); return }
        person += 1
        index = 0
        markSeen(who.id, story.at)
    }

    private func previousPerson() {
        guard person > 0 else { return }
        person -= 1
        index = 0
    }

    private func play() {
        guard let q = story.query, !q.isEmpty else { return }
        let ep = Episode(key: EpisodeKey(query: q, minutes: story.minutes ?? FAMAPI.browseMinutes),
                         title: story.title ?? q, surface: "other")
        dismiss()
        model.play(ep)
    }

    private func sendReply() {
        let text = reply.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        let s = story
        let to = who.id
        let name = who.name
        reply = ""
        typing = false
        Task {
            do {
                try await model.api.reply(to: to, text: text, story: s)
                model.nowPlaying.say("Sent to \(name.split(separator: " ").first.map(String.init) ?? name)")
            } catch {
                model.nowPlaying.say("Could not send that")
            }
        }
    }

    private func age(_ at: Double) -> String {
        let s = Date().timeIntervalSince1970 - at
        if s < 60 { return "just now" }
        if s < 3600 { return "\(Int(s / 60))m" }
        return "\(Int(s / 3600))h"
    }
}
