import FAMSafety
import SwiftUI
import UIKit

/// The faces row at the top of DailyFAM (`#myfamCircle`, §297): your story
/// first with its (+) badge, then friends and people you follow - a gradient
/// ring while they have stories you have not seen, a thin grey one once you
/// have - then Invite. Nothing for a listener with nobody and no story of
/// their own.
///
/// A face with no story opens that person's profile (by handle; with no
/// handle, a conversation - `openCircleFace`). Your own face plays your
/// story when one is up, else opens the note composer; its (+) always
/// opens the composer (`tapMyFace`, §297).
struct FacesRow: View {
    @EnvironmentObject private var model: AppModel
    /// Observed so a face turns into a button once the graph says friend.
    @EnvironmentObject private var social: SocialModel
    @State private var viewing: StoryReel?
    @State private var composing: StoryEditorSubject?
    @State private var invite = false
    /// Where each face is on screen, for the story to grow out of
    /// (`pressedCircle`). A plain box: a scroll does not redraw the row.
    @State private var frames = FaceFrames()
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
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
                            .background(FaceFrameReader(box: frames, id: person.userId))
                        if person.stories.isEmpty && person.handle.isEmpty && !social.isFriend(person.userId) {
                            face.accessibilityElement(children: .combine)
                        } else if person.stories.isEmpty {
                            Button { openFace(person) } label: { face }
                                .buttonStyle(.press)
                                // Every friend's face reads as the web's does
                                // (`circleRowHTML`): "View {first}'s profile".
                                .accessibilityLabel("View \(label)'s profile")
                        } else {
                            Button { open(circle: circle.circle, at: index) } label: { face }
                                .buttonStyle(.press)
                                .accessibilityLabel("View \(label)'s profile")
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
                    .buttonStyle(.press)
                    .accessibilityLabel("Invite a friend")
                }
                .padding(.horizontal, 2)
                .padding(.top, 6)
                .padding(.bottom, 4)
            }
            .fullScreenCover(item: $viewing) { reel in
                StoryViewer(reel: reel, markSeen: markSeen, seenAt: { seen[$0] ?? 0 },
                            openMine: { showMine() }, origin: reel.origin)
                    .environmentObject(model)
            }
            .fullScreenCover(item: $composing) { subject in
                StoryEditor(subject: subject, openMine: { showMine() })
                    .environmentObject(model)
            }
            .sheet(isPresented: $invite) {
                ActivityView(items: model.inviteItems(handle: model.circle?.me?.handle ?? "")) { invite = false }
            }
        }
    }

    /// Your face (`myFaceHTML`): a tap plays your story when one is up,
    /// else starts one; the (+) - a button of its own laid over the face,
    /// never inside it - always starts one, a note (`openNoteComposer`).
    private func myFace(_ me: CircleMe) -> some View {
        let mine = me.stories ?? []
        let face = faceItem(name: me.name ?? "", handle: me.handle ?? "", avatar: me.avatar ?? "",
                            label: "Your story", ring: mine.isEmpty ? .none : ring(for: "me", mine), plus: false)
            .background(FaceFrameReader(box: frames, id: "me"))
        return Button {
            if mine.isEmpty {
                compose()
            } else {
                var reel = StoryReel(people: [StoryPerson(id: "me", name: "Your story", handle: me.handle ?? "",
                                                          avatar: me.avatar ?? "", stories: mine, mine: true)],
                                     start: 0)
                reel.origin = frames.frame("me")
                present(reel)
            }
        } label: { face }
            .buttonStyle(.press)
            .accessibilityLabel(mine.isEmpty ? "Add to your story" : "Your story")
            .overlay(alignment: .topLeading) {
                // `.yf-add`: 24 pt, the text colour, a 3 pt border of the page.
                Button { compose() } label: {
                    ZStack {
                        Circle().fill(FAMColor.background).frame(width: 30, height: 30)
                        Circle().fill(FAMColor.text).frame(width: 24, height: 24)
                        WebIcon(path: Icons.plus, size: 13).foregroundStyle(FAMColor.background)
                    }
                    .contentShape(Circle())
                }
                .buttonStyle(.press)
                .offset(x: 42, y: 42)
                .accessibilityLabel("Add to your story")
            }
    }

    /// The note composer: an account's only (`openNoteComposer`).
    private func compose() {
        guard model.listener?.authenticated == true else { return }
        composing = .note()
    }

    /// The snack's View after posting: your story, read again
    /// (`viewMyNewStory`), once whatever was up has gone.
    private func showMine() {
        Task { @MainActor in
            await model.loadCircle()
            try? await Task.sleep(nanoseconds: 350_000_000)
            if let reel = StoryReel.mine(model) { viewing = reel }
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
            // A conversation only with a friend (§520).
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
        var reel = StoryReel(people: withStories.map {
            StoryPerson(id: $0.userId, name: $0.name, handle: $0.handle, avatar: $0.avatar,
                        stories: $0.stories, mine: false)
        }, start: start)
        reel.origin = frames.frame(people[index].userId)
        present(reel)
    }

    /// Opened out of the tapped face (`growStory`): the cover comes up
    /// without iOS's slide, and the story grows from the face itself.
    /// Under Reduce Motion it is iOS's own cover.
    private func present(_ reel: StoryReel) {
        guard !reduceMotion, reel.origin != .zero else {
            viewing = reel
            return
        }
        var still = Transaction()
        still.disablesAnimations = true
        withTransaction(still) { viewing = reel }
    }
}

/// Each face's frame on screen, kept out of SwiftUI's state so a scroll
/// of the row redraws nothing.
final class FaceFrames {
    private var frames: [String: CGRect] = [:]

    func set(_ id: String, _ frame: CGRect) { frames[id] = frame }

    func frame(_ id: String) -> CGRect { frames[id] ?? .zero }
}

/// Writes a face's global frame into the box as it moves.
struct FaceFrameReader: View {
    let box: FaceFrames
    let id: String

    var body: some View {
        GeometryReader { geo in
            Color.clear
                .onAppear { box.set(id, geo.frame(in: .global)) }
                .onChange(of: geo.frame(in: .global)) { _, frame in box.set(id, frame) }
        }
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
    /// The face it was opened from, on screen (`.zero`: none).
    var origin: CGRect = .zero
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
/// reports somebody else's. A story laid out in the editor, and every
/// note, is drawn from its layout (`StoryCanvas`); a vibe from before the
/// editor keeps the title-and-caption page.
struct StoryViewer: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let reel: StoryReel
    let markSeen: (String, Double) -> Void
    let seenAt: (String) -> Double
    /// Opens your own story again: the snack's View after the VIBE!
    /// arrows posted one (`viewMyNewStory`). Nil draws no View.
    var openMine: (() -> Void)? = nil
    /// The face it was opened from, on screen: the story grows out of it
    /// and shrinks back into it (`growStory`, `shrinkStory`). `.zero`:
    /// iOS's own cover.
    var origin: CGRect = .zero

    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// The circle the story shows through: the face's, then the screen's.
    @State private var grown = false
    @State private var closing = false
    /// A cube's turn to another friend (`turnStory`): the angle, the edge
    /// it hinges on and the slide that goes with it.
    @State private var turn: Double = 0
    @State private var turnAnchor: UnitPoint = .center
    @State private var turnShift: CGFloat = 0
    @State private var turning = false
    /// The friend being turned away from, as a still copy (`inertCopy`),
    /// turning out while the next one turns in.
    @State private var leavingFace: StillCopy?
    @State private var leavingTurn: Double = 0
    @State private var leavingAnchor: UnitPoint = .center
    @State private var leavingShift: CGFloat = 0
    @State private var stageWidth: CGFloat = 390
    /// Block's sheet (`blockPerson`): the sentence, then "Block".
    @State private var confirm: SheetSpec?

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
    /// The editor opened by the VIBE! arrows (`shareStoryToMine`).
    @State private var resharing: StoryEditorSubject?
    @State private var sharing: ShareSubject?
    /// Where a composed story's episode card is, so a tap there plays.
    @State private var postFrame: CGRect = .zero
    @FocusState private var typing: Bool

    private static let seconds = 6.0
    private static let paper = Color(hex: 0xF4EFE4)
    private static let gold = Color(hex: 0xE0B563)

    private var ready: Bool { people.indices.contains(person) && people[person].stories.indices.contains(index) }
    private var who: StoryPerson { people[person] }
    private var story: Story { who.stories[index] }
    private var isNote: Bool { story.kind == "note" }
    private var composed: Bool { StoryDraft.isComposed(story) }

    var body: some View {
        ZStack {
            LinearGradient(colors: [Color(hex: 0x2A2233), Color(hex: 0x16121C)], startPoint: .top, endPoint: .bottom)
                .ignoresSafeArea()
            if ready {
                GeometryReader { geo in
                    Color.clear
                        .contentShape(Rectangle())
                        .gesture(surfaceGesture(width: geo.size.width))
                        .onAppear { stageWidth = geo.size.width }
                }
                .ignoresSafeArea()
                if composed {
                    // Only its @mentions take a tap (`storyOpenTag`); the
                    // rest lets the story's own taps and holds through, and
                    // a tap on the episode's card plays (`playStoryTap`).
                    StoryCanvas(draft: StoryDraft(story: story), openTag: { tag in openTag(tag) })
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
                    if !composed { main }
                    Spacer(minLength: 0)
                    card.opacity(held ? 0 : 1)
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 12)
                .animation(.easeOut(duration: 0.2), value: held)
            }
        }
        // The cube's turn between friends, over the dark behind it.
        .rotation3DEffect(.degrees(turn), axis: (x: 0, y: 1, z: 0), anchor: turnAnchor, perspective: 0.55)
        .offset(x: turnShift)
        // The friend turned away from, a copy that takes no touches, turning
        // out about its far edge as the next one turns in about its near one.
        .overlay {
            if let leavingFace {
                StillCopyView(copy: leavingFace)
                    .ignoresSafeArea()
                    .rotation3DEffect(.degrees(leavingTurn), axis: (x: 0, y: 1, z: 0),
                                      anchor: leavingAnchor, perspective: 0.55)
                    .offset(x: leavingShift)
                    .allowsHitTesting(false)
                    .accessibilityHidden(true)
            }
        }
        .background(Color.black.opacity(turning ? 1 : 0).ignoresSafeArea())
        // Out of the face and back into it.
        .mask {
            GeometryReader { geo in storyMask(geo) }
                .ignoresSafeArea()
        }
        .presentationBackground(grows ? Color.clear : Color(hex: 0x16121C))
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
            growIn()
        }
        .onPreferenceChange(StoryPostFrameKey.self) { frame in postFrame = frame }
        .task(id: "\(person)-\(index)-\(people.count)") { await tick() }
        // `openStoryMenu` / `openNoteMenu`: the web's sheet, its head and
        // an icon on every row.
        .famSheet(isPresented: $storyMenu) {
            if ready {
                storyMenuSheet
                    .environmentObject(model)
            }
        }
        .famSheet(item: $confirm) { spec in FAMActionSheet(spec: spec) }
        .famSheet(isPresented: $addingTopic) {
            if ready {
                AddToMixSheet(query: story.query ?? "", title: story.title ?? story.query ?? "")
                    .environmentObject(model)
                    .environmentObject(model.mixes)
            }
        }
        // The web closes the story and opens the editor: here the editor
        // covers the story, and the story closes behind it when it goes.
        .fullScreenCover(item: $resharing, onDismiss: { close(animated: false) }) { subject in
            StoryEditor(subject: subject, openMine: openMine)
                .environmentObject(model)
        }
        .famSheet(item: $sharing) { subject in
            ShareSheet(subject: subject)
                .environmentObject(model)
                .environmentObject(model.nowPlaying)
        }
        .famSheet(isPresented: $reporting) {
            if ready {
                FAMReportSheet(api: model.safety,
                               subject: isNote ? .note(id: story.id) : .vibe(id: story.id),
                               say: { model.nowPlaying.say($0) }) { receipt in
                    // Any receipt closes the story (`reportContent`'s
                    // `onHidden` runs after every one the server sends).
                    if receipt != nil {
                        Task { @MainActor in
                            try? await Task.sleep(nanoseconds: 450_000_000)
                            close()
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
            if who.mine {
                // Your own: the header closes the story (`storyProfile`).
                Button { close() } label: { headerWho }
                    .buttonStyle(.press)
                    .accessibilityHint("Closes your story")
            } else if !who.handle.isEmpty {
                Button { openProfile() } label: { headerWho }
                    .buttonStyle(.press)
                    .accessibilityLabel("View profile")
            } else if !who.id.isEmpty {
                // No handle: a conversation (`openCircleFace`).
                Button { openChat() } label: { headerWho }
                    .buttonStyle(.press)
                    .accessibilityHint("Opens your conversation")
            } else {
                headerWho
            }
            Spacer()
            Button { storyMenu = true } label: {
                WebFill(path: Icons.dots(1.9), size: 18).frame(width: 32, height: 32)
            }
            .buttonStyle(.press)
            .accessibilityLabel("More")
            Button { close() } label: {
                WebIcon(path: Icons.close, size: 14, line: 2.4).frame(width: 32, height: 32)
            }
            .buttonStyle(.press)
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

    /// A tag on a story opens that person's profile, once the story has
    /// closed (`storyOpenTag`).
    private func openTag(_ tag: StoryMention) {
        guard !tag.handle.isEmpty else { return }
        let route = SocialRoute.person(PersonOpen(handle: tag.handle, name: tag.name))
        close()
        model.social.openAfterClose(route)
    }

    private func openProfile() {
        let route = SocialRoute.person(PersonOpen(handle: who.handle, userID: who.id, name: who.name,
                                                  avatar: who.avatar))
        close()
        model.social.openAfterClose(route)
    }

    /// Somebody with no handle: your conversation with them, once the
    /// story has closed - a friend's only (§520), as the faces row does.
    private func openChat() {
        guard model.social.isFriend(who.id) else {
            model.nowPlaying.say("You can message people who follow you back.")
            return
        }
        let route = SocialRoute.thread(ChatOpen(with: who.id, name: who.name, avatar: who.avatar))
        close()
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
                    .frame(minHeight: 46)
                    .background(Self.gold, in: Capsule())
                }
                .buttonStyle(.press)
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
                    .accessibilityLabel("Reply to this vibe")
                    // `maxlength="500"`.
                    .onChange(of: reply) { _, words in
                        if words.count > 500 { reply = String(words.prefix(500)) }
                    }
                    .submitLabel(.send)
                    .onSubmit(sendReply)
                    .padding(.leading, 18)
                    .padding(.trailing, 4)
                Button(action: sendReply) {
                    WebIcon(path: Icons.send, size: 20, line: 1.9).frame(width: 40, height: 40)
                }
                .buttonStyle(.press)
                .padding(.trailing, 2)
                .accessibilityLabel("Send")
            }
            .frame(minHeight: 44)
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
            .buttonStyle(.press)
            .accessibilityLabel("Like")
            .accessibilityAddTraits(on ? .isSelected : [])
            if !isNote {
                Button { reshare() } label: {
                    WebIcon(path: Icons.reshare, size: 24, line: 1.9).frame(width: 42, height: 44)
                }
                .buttonStyle(.press)
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
        DragGesture(minimumDistance: 0, coordinateSpace: .global)
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
                if abs(dx) < 12 && abs(dy) < 12 {
                    if composed && !isNote && postFrame.contains(value.startLocation) {
                        play()
                    } else if value.startLocation.x < width / 2 {
                        back()
                    } else {
                        forward()
                    }
                }
            }
    }

    // MARK: The ⋯ menu

    /// The story holds while its menu, or anything the menu opened, is up.
    /// And while FAM is not in front (the web's `visibilitychange`), and
    /// while it turns to another friend or closes.
    private var paused: Bool {
        if held || typing || storyMenu || addingTopic || reporting { return true }
        if confirm != nil || resharing != nil || sharing != nil { return true }
        return scenePhase != .active || turning || closing
    }

    private var firstName: String {
        let first = who.name.split(separator: " ").first.map(String.init) ?? ""
        return first.isEmpty ? (who.handle.isEmpty ? "Someone" : who.handle) : first
    }

    /// `openStoryMenu` / `openNoteMenu`: the head - the vibe's title (a
    /// note's first 60 letters), "Your story" / "Vibed by <First>" (a
    /// note's "<First>’s story") and the picture - then a vibe's episode
    /// saved, shared, queued or added to a mix; your own vibe deleted and
    /// your own note taken down; somebody else's reported (only when it has
    /// an id) and its person blocked (only with a handle). Each row waits
    /// for the sheet to close.
    private var storyMenuSheet: some View {
        YouSheet(headTitle: menuTitle, headSub: menuSub, headThumb: isNote ? "" : (story.style?.thumb ?? ""),
                 items: menuItems)
    }

    private var menuTitle: String {
        if isNote {
            let words = story.text ?? ""
            return words.isEmpty ? "A story" : String(words.prefix(60))
        }
        let title = story.title ?? ""
        return title.isEmpty ? (story.query ?? "") : title
    }

    private var menuSub: String {
        if who.mine { return "Your story" }
        return isNote ? firstName + "\u{2019}s story" : "Vibed by " + firstName
    }

    private var menuItems: [YouSheetItem] {
        var items: [YouSheetItem] = []
        let hasID = story.id > 0
        if !isNote {
            items.append(YouSheetItem(label: "Save for later", icon: Icons.bookmark) { saveStory() })
            items.append(YouSheetItem(label: "Share", icon: Icons.menuShare) { shareStory() })
            items.append(YouSheetItem(label: "Add to Queue", icon: Icons.addQueue) { queueStory() })
            items.append(YouSheetItem(label: "Add topic to playlist", icon: Icons.circlePlus) { addingTopic = true })
        }
        if who.mine {
            if hasID && isNote {
                items.append(YouSheetItem(label: "Remove from your story", danger: true,
                                          icon: PlayerIcons.circleMinus) { removeNote() })
            } else if hasID {
                items.append(YouSheetItem(label: "Delete vibe", danger: true,
                                          icon: PlayerIcons.circleMinus) { deleteVibe() })
            }
        } else {
            if hasID {
                items.append(YouSheetItem(label: "Report", danger: true, icon: PlayerIcons.flag) { reporting = true })
            }
            if !who.handle.isEmpty {
                items.append(YouSheetItem(label: "Block " + firstName, danger: true, icon: Icons.blockMark) {
                    askBlock()
                })
            }
        }
        return items
    }

    /// `blockPerson(handle, name, closeStory)`.
    private func askBlock() {
        let app = model
        confirm = FAMBlock.sheet(name: who.name, handle: who.handle, model: model) {
            app.nowPlaying.say("Blocked")
            Task {
                await app.social.loadGraph()
                await app.loadCircle()
            }
            close()
        }
    }

    private func later(_ action: @escaping () -> Void) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35, execute: action)
    }

    /// The VIBE! arrows (`shareStoryToMine`): your own story editor on this
    /// episode, with the story's own picture.
    private func reshare() {
        guard ready, !isNote else { return }
        let key = storyKey()
        let title = story.title ?? key.query
        let thumb = story.style?.thumb ?? ""
        resharing = .episode(key, title: title, thumb: thumb, thread: "")
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
                model.nowPlaying.snack("Saved for Later")
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
        sharing = ShareSubject(key: key, title: title)
    }

    /// With the story's picture and its own title (`thumb`,
    /// `titleOverridden`), as the web's queue entry carries them.
    private func queueStory() {
        let key = storyKey()
        let thumb = story.style?.thumb ?? ""
        let picture: URL? = thumb.isEmpty ? nil : model.api.picture(thumb)
        let ep = Episode(key: key, title: story.title ?? key.query, surface: "", picture: picture)
        model.nowPlaying.addToQueue(ep)
    }

    /// "Delete vibe": deleted everywhere, not only off the story
    /// (`deleteVibeFromStory`, `DELETE /api/vibe`).
    private func deleteVibe() {
        let gone = story
        let key = storyKey()
        Task {
            do {
                try await model.api.unvibe(key)
                model.nowPlaying.snack("Vibe deleted")
                drop(gone)
                await model.loadCircle()
                await model.you.loadProfile()
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
                model.nowPlaying.snack("Removed from your story")
                drop(gone)
                await model.loadCircle()
                // `loadProfileQuietly`: the hub's story ring and grid.
                await model.you.loadProfile()
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
            close()
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
            turnTo(person + 1, index: 0, dir: 1)
        } else {
            close()
        }
    }

    /// Back: the story before, else the previous friend's last, else this
    /// one again from the start.
    private func back() {
        if index > 0 {
            index -= 1
        } else if person > 0 {
            let previous = person - 1
            turnTo(previous, index: max(0, people[previous].stories.count - 1), dir: -1)
        } else {
            progress = 0
        }
    }

    /// A swipe: the next or previous friend, on their first unseen story.
    /// Past the last closes; before the first starts this friend again.
    private func friend(_ dir: Int) {
        let next = person + dir
        if next >= 0 && next < people.count {
            turnTo(next, index: firstUnseen(next, in: people), dir: dir)
        } else if dir > 0 {
            close()
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
            close(animated: false)
            model.showPlayer = true
            return
        }
        let ep = Episode(key: EpisodeKey(query: q, minutes: story.minutes ?? FAMAPI.browseMinutes),
                         title: story.title ?? q, surface: "other")
        // At once: the loading screen and the player come up as it goes.
        close(animated: false)
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

    // MARK: Motion: out of the face, back into it, a cube's turn

    /// Whether it grows from a face: one was given, and motion is on.
    private var grows: Bool { origin != .zero && !reduceMotion }

    /// The circle the story shows through: the face's 72 pt circle at
    /// first, then one past every corner of the screen (`circleIn`).
    @ViewBuilder
    private func storyMask(_ geo: GeometryProxy) -> some View {
        if grows {
            let frame = geo.frame(in: .global)
            let side = min(origin.width, 72)
            let cx = origin.midX - frame.minX
            let cy = origin.minY + side / 2 - frame.minY
            let far = (frame.width * frame.width + frame.height * frame.height).squareRoot()
            let d: CGFloat = grown ? far * 2 + 8 : max(16, side)
            Circle()
                .frame(width: d, height: d)
                .position(x: cx, y: cy)
        } else {
            Rectangle()
        }
    }

    /// `growStory`: 300 ms from the face to the whole screen.
    private func growIn() {
        guard grows, !grown else {
            grown = true
            return
        }
        DispatchQueue.main.async {
            withAnimation(.timingCurve(0.2, 0.8, 0.2, 1, duration: 0.3)) { grown = true }
        }
    }

    /// Every way out of the story: `shrinkStory` back into the face over
    /// 240 ms, then gone without iOS's slide. `animated: false` - a play,
    /// whose loading screen comes up at once - goes straight away.
    private func close(animated: Bool = true) {
        guard !closing else { return }
        closing = true
        typing = false
        guard grows && animated else {
            dismiss()
            return
        }
        withAnimation(.easeInOut(duration: 0.24)) { grown = false }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.24) {
            var still = Transaction()
            still.disablesAnimations = true
            withTransaction(still) { dismiss() }
        }
    }

    /// `turnToFriend`: the next friend's stories turn in like a cube's next
    /// face - this one turns away about its far edge, theirs turns in about
    /// the near one (340 ms in all, perspective as the web's 900 px). One
    /// friend's own stories change in place. Still under Reduce Motion.
    private func turnTo(_ next: Int, index start: Int, dir: Int) {
        guard people.indices.contains(next) else { return }
        guard !reduceMotion, !turning, let copy = StillCopy.ofWindow() else {
            person = next
            index = start
            return
        }
        turning = true
        let width = stageWidth
        let d = CGFloat(dir)
        // Both faces at once, as the web's: the copy of this friend from
        // in place to a quarter-turn away and a width over; the next
        // friend from a quarter-turn and a width the other side to in place.
        var still = Transaction()
        still.disablesAnimations = true
        withTransaction(still) {
            leavingFace = copy
            leavingAnchor = dir > 0 ? .trailing : .leading
            leavingTurn = 0
            leavingShift = 0
            person = next
            index = start
            turnAnchor = dir > 0 ? .leading : .trailing
            turn = Double(90 * dir)
            turnShift = width * d
        }
        DispatchQueue.main.async {
            withAnimation(.easeInOut(duration: 0.34)) {
                leavingTurn = Double(-90 * dir)
                leavingShift = -width * d
                turn = 0
                turnShift = 0
            }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.36) {
            leavingFace = nil
            turning = false
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

/// A still copy of what is on screen now (the web's `inertCopy`): drawn,
/// never pressed or read aloud.
struct StillCopy: Identifiable {
    let id = UUID()
    let view: UIView

    static func ofWindow() -> StillCopy? {
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        guard let window = scenes.flatMap(\.windows).first(where: { $0.isKeyWindow }),
              let view = window.snapshotView(afterScreenUpdates: false) else { return nil }
        return StillCopy(view: view)
    }
}

struct StillCopyView: UIViewRepresentable {
    let copy: StillCopy

    func makeUIView(context: Context) -> UIView {
        let box = UIView()
        box.isUserInteractionEnabled = false
        copy.view.frame = box.bounds
        copy.view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        box.addSubview(copy.view)
        return box
    }

    func updateUIView(_ uiView: UIView, context: Context) {}
}
