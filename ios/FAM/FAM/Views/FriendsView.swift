import SwiftUI

/// Friends (`#screen-friends`, shots 28-32): find someone by handle,
/// Invite new users +, then two tabs, Friends | Following (11.16 notes, the
/// owner's sketch; `renderFriends`). Friends are mutual follows
/// (`op-follow-graph`); Following is everyone followed, friends included,
/// as Instagram's is. Somebody who follows this listener and is not
/// followed back sits under Friends in a FOLLOWS YOU group, where following
/// back would put them. Opening the page is what clears "new followers"
/// (`POST /api/friends/seen`) - never the popup. Rows open the profile;
/// Follow flips to "✓ Following" at once, the tick drawing in; Following
/// unfollows in one tap, flipping back at once (the owner's decision 7).
struct FriendsView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var social: SocialModel
    @State private var search = ""
    @State private var found: [FollowPerson]?
    @State private var flipping: Set<String> = []
    /// Unfollowed on the tap, before the server has answered (`Motion.follow`).
    @State private var unflipping: Set<String> = []
    @State private var invite: [Any]?
    /// The tab asked for (`friendsTab`); `shownTab` is the one drawn.
    @State private var tab: FriendsTab = .friends

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // `.back-row`: the double chevron (stroke 2.6, .85) and "Friends".
            YouBackRow(title: "Friends") { social.back() }
            ModalField(placeholder: "Find someone by handle", text: $search, plain: true)
                .padding(.horizontal, 18)
                .task(id: search) { await lookUp() }
            Button { inviteNewUsers() } label: {
                HStack(spacing: 5) {
                    Text("Invite new users").font(FAMFont.body(12, .bold))
                    WebIcon(path: Icons.plus, size: 12, line: 2.6)
                }
                .foregroundStyle(FAMColor.ink)
                .padding(.leading, 13)
                .padding(.trailing, 12)
                .frame(minHeight: 30)
                .background(FAMColor.copper, in: Capsule())
            }
            .buttonStyle(.press)
            .padding(.horizontal, 18)
            .padding(.top, 10)
            // Above the list, so the tabs stay put while it scrolls
            // (`.fr-tabs{ position:sticky }`); not over a handle search.
            if found == nil, let g = social.graph {
                FriendsTabs(tab: shownTab(g), friends: g.friends.count, following: g.following.count) { tab = $0 }
                    .padding(.top, 12)
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    if let found {
                        results(found)
                    } else {
                        graphView
                    }
                }
                .padding(.horizontal, 18)
                .padding(.top, 12)
                .padding(.bottom, 18)
            }
            // A tab opens at its top (`showFriendsTab`: `scrollTop = 0`).
            .id(tab)
            .refreshable { await social.loadGraph() }
        }
        .background(FAMColor.background)
        .toolbar(.hidden, for: .navigationBar)
        .task {
            if model.inviteLink == nil { await model.loadInviteLink() }
        }
        // Your handle, for the invite's "I'm @handle".
        .task { if social.me == nil { await social.loadMe() } }
        .task { await social.friendsOpened() }
        .sheet(isPresented: Binding(get: { invite != nil }, set: { if !$0 { invite = nil } })) {
            ActivityView(items: invite ?? []) { invite = nil }
                .tourCover()
        }
    }

    // MARK: The graph

    /// Followers this listener does not follow back (`followsYou`).
    private func followsYou(_ g: FriendsPage) -> [FollowPerson] {
        let friendIDs = Set(g.friends.map(\.userId))
        let followedIDs = Set(g.following.map(\.userId))
        return g.followers.filter { !friendIDs.contains($0.userId) && !followedIDs.contains($0.userId) }
    }

    /// The tab drawn: the one asked for - but with nobody to show under
    /// Friends and somebody followed, Following (`renderFriends`).
    private func shownTab(_ g: FriendsPage) -> FriendsTab {
        if tab == .following { return .following }
        if g.friends.isEmpty && followsYou(g).isEmpty && !g.following.isEmpty { return .following }
        return .friends
    }

    @ViewBuilder private var graphView: some View {
        if let g = social.graph {
            if shownTab(g) == .following {
                if g.following.isEmpty {
                    SocialNote(text: "You are not following anybody yet. Search a @handle above.")
                } else {
                    ForEach(g.following) { p in row(p) }
                }
            } else {
                let waiting = followsYou(g)
                ForEach(g.friends) { p in row(p) }
                if !waiting.isEmpty {
                    group("Follows you", waiting, note: "Following back makes you friends.")
                }
                if g.friends.isEmpty && waiting.isEmpty {
                    SocialNote(text: "Nobody yet. Search a @handle above. When someone follows you back, you\u{2019}re friends.")
                }
            }
        } else if social.graphState == .failed {
            SocialNote(text: "Could not load your friends list.")
        } else {
            SocialNote(text: "Loading\u{2026}")
        }
    }

    /// `.pf-rail` heading with its count, the muted sentence, the rows.
    private func group(_ title: String, _ people: [FollowPerson], note: String) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline) {
                Text(title).monoLabel(10, color: FAMColor.copper, tracking: 1.3)
                Spacer()
                Text("\(people.count)").monoLabel(10, tracking: 0.5)
            }
            .padding(.top, 26)
            .padding(.bottom, 2)
            Text(note)
                .font(FAMFont.body(11))
                .foregroundStyle(FAMColor.textFaint)
                .lineSpacing(5.5)
                .padding(.horizontal, 2)
                .padding(.bottom, 6)
            ForEach(people) { p in row(p) }
        }
    }

    // MARK: Search

    @ViewBuilder private func results(_ people: [FollowPerson]) -> some View {
        if people.isEmpty {
            SocialNote(text: "No one with that handle.")
        } else {
            ForEach(people) { p in row(p) }
        }
    }

    /// Under two letters the graph; else 260 ms after the last letter.
    /// Failures are silent.
    private func lookUp() async {
        var q = search.trimmingCharacters(in: .whitespaces)
        if q.hasPrefix("@") { q = String(q.dropFirst()) }
        guard q.count >= 2 else {
            found = nil
            return
        }
        try? await Task.sleep(nanoseconds: 260_000_000)
        if Task.isCancelled { return }
        guard let answer = try? await model.api.people(q), !Task.isCancelled else { return }
        found = answer
    }

    // MARK: A row (`.fr-row`)

    private func row(_ p: FollowPerson) -> some View {
        HStack(spacing: 12) {
            Button { social.openPerson(handle: p.handle ?? "", userID: p.userId, name: p.name ?? "",
                                       avatar: p.avatar ?? "") } label: {
                HStack(spacing: 12) {
                    Face(name: p.name ?? "", handle: p.handle ?? "", avatar: p.avatar ?? "", size: 40, initialsSize: 14)
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: 5) {
                            // `personRow`: the name, else "Someone".
                            Text(rowName(p))
                                .font(FAMFont.body(13.5, .semibold))
                                .foregroundStyle(FAMColor.text)
                                .lineLimit(1)
                            if p.explicit == true { ExplicitBadge() }
                        }
                        if let h = p.handle, !h.isEmpty {
                            Text("@" + h).font(FAMFont.mono(9.5)).foregroundStyle(FAMColor.textMuted)
                        }
                        if p.waitlisted == true {
                            Text("Still on the waitlist").monoLabel(10, color: FAMColor.copper)
                        }
                    }
                    Spacer(minLength: 0)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.press)
            followButton(p)
        }
        .padding(.vertical, 10)
        .padding(.horizontal, 2)
    }

    private func isFollowing(_ p: FollowPerson) -> Bool {
        if flipping.contains(p.userId) { return true }
        if unflipping.contains(p.userId) { return false }
        if let f = p.following, found != nil { return f || social.isFollowing(p.userId) }
        return social.isFollowing(p.userId)
    }

    /// `.pf-btn`: 34 tall, radius 20, outlined, Geist (the web falls back
    /// to Arial here, quirk 8). "✓ Following" draws at once on a tap.
    private func followButton(_ p: FollowPerson) -> some View {
        let on = isFollowing(p)
        return Button {
            if on {
                if !flipping.contains(p.userId) { unfollow(p) }
            } else if !unflipping.contains(p.userId) {
                follow(p)
            }
        } label: {
            HStack(spacing: 5) {
                if flipping.contains(p.userId) {
                    FollowTick()
                }
                Text(on ? "Following" : "Follow").font(FAMFont.body(12.5, .medium))
            }
            .foregroundStyle(FAMColor.text)
            .padding(.horizontal, 16)
            .frame(minHeight: 34)
            .overlay(Capsule().stroke(FAMColor.borderStrong))
            .animation(.easeOut(duration: 0.26), value: on)
        }
        .buttonStyle(.press)
        .accessibilityLabel(on ? "Following \(displayName(p)), tap to unfollow" : "Follow \(displayName(p))")
    }

    /// `followPerson`: flipped at once; then the search clears and the
    /// graph is re-read; a refusal flips it back with the server's words.
    private func follow(_ p: FollowPerson) {
        flipping.insert(p.userId)
        Task {
            let ok = await social.follow(userID: p.userId)
            flipping.remove(p.userId)
            if ok {
                search = ""
                found = nil
            }
        }
    }

    /// `unfollowPerson`: one tap, "Follow" at once and put back if refused;
    /// then the search clears and the graph is re-read, as after a follow -
    /// a result still saying "Following" would be wrong.
    private func unfollow(_ p: FollowPerson) {
        unflipping.insert(p.userId)
        Task {
            let ok = await social.unfollow(p.userId)
            unflipping.remove(p.userId)
            if ok {
                search = ""
                found = nil
            }
        }
    }

    private func rowName(_ p: FollowPerson) -> String {
        let n = p.name ?? ""
        return n.isEmpty ? "Someone" : n
    }

    private func displayName(_ p: FollowPerson) -> String {
        let n = p.name ?? ""
        return n.isEmpty ? "@" + (p.handle ?? "") : n
    }

    /// `inviteFriends`: "Come listen with me on FAM — I'm @handle.", the
    /// title "FAM" and the referral link (while the waitlist runs) or the
    /// app's address, as three things - not one string.
    private func inviteNewUsers() {
        let handle = social.myHandle.isEmpty ? (model.circle?.me?.handle ?? "") : social.myHandle
        invite = model.inviteItems(handle: handle)
    }
}

/// The Friends page's two tabs (`friendsTab`).
enum FriendsTab {
    case friends, following
}

/// Friends | Following (`.pf-tabs.fr-tabs`): two equal tabs, a copper rule
/// under the one that is on and each with its count, drawn as myFAM's
/// Mixes | Categories are (`PlayfamTabs`).
struct FriendsTabs: View {
    let tab: FriendsTab
    let friends: Int
    let following: Int
    let pick: (FriendsTab) -> Void

    var body: some View {
        HStack(spacing: 0) {
            button(.friends, "Friends", friends)
            button(.following, "Following", following)
        }
        // The hairline under both; the copper rule of the one on is drawn
        // over it (`margin-bottom:-1px`).
        .background(alignment: .bottom) {
            Rectangle().fill(FAMColor.border).frame(height: 1)
        }
        .padding(.horizontal, 18)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Friends")
    }

    private func button(_ which: FriendsTab, _ label: String, _ count: Int) -> some View {
        let on = tab == which
        return Button { pick(which) } label: {
            HStack(spacing: 6) {
                Text(label)
                    .font(FAMFont.body(15, .semibold))
                    .foregroundStyle(on ? FAMColor.text : FAMColor.textMuted)
                // `.fr-tabs em`: the count, lighter and muted.
                Text("\(count)")
                    .font(FAMFont.body(15, .medium))
                    .foregroundStyle(FAMColor.textMuted)
            }
            .lineLimit(1)
            .padding(.top, 8)
            .padding(.bottom, 9 + 2.5)
            .frame(maxWidth: .infinity, minHeight: 42)
            .overlay(alignment: .bottom) {
                Rectangle()
                    .fill(on ? FAMColor.copper : Color.clear)
                    .frame(height: 2.5)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.press)
        .accessibilityLabel("\(label), \(count)")
        .accessibilityAddTraits(on ? .isSelected : [])
    }
}
