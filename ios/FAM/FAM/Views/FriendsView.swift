import SwiftUI

/// Friends (`#screen-friends`, shots 28-32): find someone by handle,
/// Invite new users +, the counts, then three groups - nobody twice:
/// FRIENDS (mutual follows, `op-follow-graph`), FOLLOWING (one-way) and
/// FOLLOWS YOU. Opening it is what clears "new followers"
/// (`POST /api/friends/seen`) - never the popup. Rows open the profile;
/// Follow flips to "✓ Following" at once; Following unfollows (asked
/// first here - the web does it in one tap, quirk 7).
struct FriendsView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var social: SocialModel
    @State private var search = ""
    @State private var found: [FollowPerson]?
    @State private var flipping: Set<String> = []
    @State private var unfollowing: FollowPerson?
    @State private var invite: [Any]?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 10) {
                SocialBackButton { social.back() }
                Text("Friends")
                    .font(FAMFont.heading(19, .medium))
                    .foregroundStyle(FAMColor.text)
                    .accessibilityAddTraits(.isHeader)
                Spacer()
            }
            .padding(.horizontal, 18)
            .padding(.top, 16)
            .padding(.bottom, 10)
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
                .frame(height: 30)
                .background(FAMColor.copper, in: Capsule())
            }
            .buttonStyle(.plain)
            .padding(.horizontal, 18)
            .padding(.top, 10)
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
        .confirmationDialog(unfollowing.map { "Unfollow \(displayName($0))?" } ?? "",
                            isPresented: Binding(get: { unfollowing != nil }, set: { if !$0 { unfollowing = nil } }),
                            titleVisibility: .visible) {
            if let p = unfollowing {
                Button("Unfollow", role: .destructive) { unfollow(p) }
                Button("Cancel", role: .cancel) {}
            }
        }
        .sheet(isPresented: Binding(get: { invite != nil }, set: { if !$0 { invite = nil } })) {
            ActivityView(items: invite ?? []) { invite = nil }
        }
    }

    // MARK: The graph

    @ViewBuilder private var graphView: some View {
        if let g = social.graph {
            counts(g)
            let friendIDs = Set(g.friends.map(\.userId))
            let following = g.following.filter { !friendIDs.contains($0.userId) }
            let followers = g.followers.filter { !friendIDs.contains($0.userId) }
            if g.friends.isEmpty && following.isEmpty && followers.isEmpty {
                SocialNote(text: "Nobody yet. Search a @handle above. When someone follows you back, you\u{2019}re friends.")
            }
            if !g.friends.isEmpty {
                group("Friends", g.friends,
                      note: "Mutual follows. Nothing to accept \u{2014} you are friends when you both follow.")
            }
            if !following.isEmpty {
                group("Following", following,
                      note: "One-way, for now. They are friends the moment they follow back.")
            }
            if !followers.isEmpty {
                group("Follows you", followers, note: "Following back makes you friends.")
            }
        } else if social.graphState == .failed {
            SocialNote(text: "Could not load your friends list.")
        } else {
            SocialNote(text: "Loading\u{2026}")
        }
    }

    /// "2 friends · 3 following · 3 followers", the numbers bold.
    private func counts(_ g: FriendsPage) -> some View {
        let friends = g.counts?.friends ?? g.friends.count
        let following = g.counts?.following ?? g.following.count
        let followers = g.counts?.followers ?? g.followers.count
        let line = Text("\(friends)").font(FAMFont.body(12.5, .bold)).foregroundColor(FAMColor.text)
            + Text(friends == 1 ? " friend \u{00B7} " : " friends \u{00B7} ")
            + Text("\(following)").font(FAMFont.body(12.5, .bold)).foregroundColor(FAMColor.text)
            + Text(" following \u{00B7} ")
            + Text("\(followers)").font(FAMFont.body(12.5, .bold)).foregroundColor(FAMColor.text)
            + Text(followers == 1 ? " follower" : " followers")
        return line
            .font(FAMFont.body(12.5))
            .foregroundStyle(FAMColor.textMuted)
            .padding(.top, 2)
            .padding(.bottom, 6)
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
                            Text(displayName(p))
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
            .buttonStyle(.plain)
            followButton(p)
        }
        .padding(.vertical, 10)
        .padding(.horizontal, 2)
    }

    private func isFollowing(_ p: FollowPerson) -> Bool {
        if flipping.contains(p.userId) { return true }
        if let f = p.following, found != nil { return f || social.isFollowing(p.userId) }
        return social.isFollowing(p.userId)
    }

    /// `.pf-btn`: 34 tall, radius 20, outlined, Geist (the web falls back
    /// to Arial here, quirk 8). "✓ Following" draws at once on a tap.
    private func followButton(_ p: FollowPerson) -> some View {
        let on = isFollowing(p)
        return Button {
            if on {
                if !flipping.contains(p.userId) { unfollowing = p }
            } else {
                follow(p)
            }
        } label: {
            HStack(spacing: 5) {
                if flipping.contains(p.userId) {
                    WebIcon(path: Icons.check, size: 12, line: 2.6)
                }
                Text(on ? "Following" : "Follow").font(FAMFont.body(12.5, .medium))
            }
            .foregroundStyle(FAMColor.text)
            .padding(.horizontal, 16)
            .frame(height: 34)
            .overlay(Capsule().stroke(FAMColor.borderStrong))
            .animation(.easeOut(duration: 0.26), value: on)
        }
        .buttonStyle(.plain)
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

    /// `unfollowPerson`: then the search clears and the graph is re-read,
    /// as after a follow - a result still saying "Following" would be wrong.
    private func unfollow(_ p: FollowPerson) {
        Task {
            let ok = await social.unfollow(p.userId)
            if ok {
                search = ""
                found = nil
            }
        }
    }

    private func displayName(_ p: FollowPerson) -> String {
        let n = p.name ?? ""
        return n.isEmpty ? "@" + (p.handle ?? "") : n
    }

    /// `inviteFriends`: "Come listen with me on FAM — I'm @handle." and the
    /// referral link (while the waitlist runs) or the app's address.
    private func inviteNewUsers() {
        let handle = social.myHandle.isEmpty ? (model.circle?.me?.handle ?? "") : social.myHandle
        invite = [model.inviteText(handle: handle)]
    }
}
