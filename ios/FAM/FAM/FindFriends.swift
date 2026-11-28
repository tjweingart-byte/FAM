import Contacts
import CoreImage.CIFilterBuiltins
import SwiftUI
import UIKit

// Finding friends (§665-§672, `find-friends`), as the web draws it: the
// Friends page's Suggested for you list (a list of rows under the
// listener's own people, and nowhere else: the sideways cards, sign-up's
// Find your friends step, YourFAM's Discover people and the row under
// Follow on a profile all went at the testers' note), Your contacts on
// FAM, "Followed by …" on every profile (§667),
// "just joined FAM" (§669), where a follow was tapped (`via`, §669), the
// `/@handle` profile link and its QR code (§670-§672).
//
// The web can offer contacts only where the browser has a Contact Picker
// (Chrome on Android); here it is the Contacts permission, asked only when
// the listener taps "Find friends in your contacts". The numbers and
// addresses go to `/api/friends/contacts`, are matched and forgotten there,
// and are kept nowhere on the phone either.

/// The icons the web draws with `yfIcon("addPerson")` and `yfIcon("qr")`.
enum FindIcons {
    /// `<circle cx=9 cy=8 r=3.6/>`, `M2.5 20a6.5 6.5 0 0 1 13 0`, `M19 8v6M16 11h6`.
    static let addPerson: Path = {
        var p = Path()
        p.addEllipse(in: CGRect(x: 5.4, y: 4.4, width: 7.2, height: 7.2))
        p.move(to: CGPoint(x: 2.5, y: 20))
        p.addArc(center: CGPoint(x: 9, y: 20), radius: 6.5,
                 startAngle: .degrees(180), endAngle: .degrees(360), clockwise: false)
        p.move(to: CGPoint(x: 19, y: 8)); p.addLine(to: CGPoint(x: 19, y: 14))
        p.move(to: CGPoint(x: 16, y: 11)); p.addLine(to: CGPoint(x: 22, y: 11))
        return p
    }()

    /// Three finder squares and the scatter (`yfIcon("qr")`).
    static let qr: Path = {
        var p = Path()
        for origin in [CGPoint(x: 3, y: 3), CGPoint(x: 14, y: 3), CGPoint(x: 3, y: 14)] {
            p.addRoundedRect(in: CGRect(origin: origin, size: CGSize(width: 7, height: 7)),
                             cornerSize: CGSize(width: 1, height: 1))
        }
        p.move(to: CGPoint(x: 14, y: 14)); p.addLine(to: CGPoint(x: 17, y: 14))
        p.addLine(to: CGPoint(x: 17, y: 17)); p.addLine(to: CGPoint(x: 14, y: 17)); p.closeSubpath()
        p.move(to: CGPoint(x: 20, y: 14)); p.addLine(to: CGPoint(x: 20, y: 14.01))
        p.move(to: CGPoint(x: 14, y: 20)); p.addLine(to: CGPoint(x: 14.01, y: 20))
        p.move(to: CGPoint(x: 17, y: 20)); p.addLine(to: CGPoint(x: 21, y: 20)); p.addLine(to: CGPoint(x: 21, y: 17))
        return p
    }()
}

// MARK: - The address book

/// Reads the address book once the listener has said yes - only names,
/// numbers and email addresses, at most 2,000 people with something to
/// match on (`CONTACTS_MAX`), eight of each (`ContactEntry`).
enum ContactsReader {
    enum Outcome {
        case read([ContactEntry])
        case denied
    }

    static func read() async -> Outcome {
        let store = CNContactStore()
        let granted: Bool
        do {
            granted = try await store.requestAccess(for: .contacts)
        } catch {
            return .denied
        }
        guard granted else { return .denied }
        let entries: [ContactEntry] = await Task.detached(priority: .userInitiated) {
            let keys = [CNContactGivenNameKey, CNContactFamilyNameKey, CNContactNicknameKey,
                        CNContactPhoneNumbersKey, CNContactEmailAddressesKey] as [CNKeyDescriptor]
            let request = CNContactFetchRequest(keysToFetch: keys)
            var out: [ContactEntry] = []
            try? CNContactStore().enumerateContacts(with: request) { contact, stop in
                let tel = contact.phoneNumbers.map { $0.value.stringValue }.filter { !$0.isEmpty }
                let email = contact.emailAddresses.map { String($0.value) }.filter { !$0.isEmpty }
                if tel.isEmpty && email.isEmpty { return }
                var name = [contact.givenName, contact.familyName].filter { !$0.isEmpty }.joined(separator: " ")
                if name.isEmpty { name = contact.nickname }
                out.append(ContactEntry(name: name, tel: Array(tel.prefix(8)), email: Array(email.prefix(8))))
                if out.count >= 2000 { stop.pointee = true }
            }
            return out
        }.value
        return .read(entries)
    }
}

// MARK: - What the find-friends surfaces share

/// One copy of what the Friends page found, as the web keeps
/// `suggestLists` and `contactMatches` once: a suggestion dismissed stays
/// dismissed, and a follow flips everywhere.
@MainActor
final class FindFriendsModel: ObservableObject {
    static let shared = FindFriendsModel()

    /// nil until the contacts have been read and matched this session.
    @Published private(set) var contactMatches: [FollowPerson]?
    @Published private(set) var contactsAsking = false
    /// The Contacts permission was refused: say where to turn it on.
    @Published private(set) var contactsDenied = false
    /// Discover people (`suggestLists.discover`).
    @Published private(set) var discover: [FollowPerson] = []
    /// Suggestions X'd away, kept on the device (`famSuggestHidden`).
    @Published private(set) var hidden: Set<String>
    /// Follows tapped and not yet answered: "✓ Following" at once.
    @Published private(set) var flipping: Set<String> = []
    @Published private(set) var unflipping: Set<String> = []
    @Published private(set) var followingAll = false

    private var owner = ""
    private var discoverAt: Date?
    static let hiddenKey = "fam.suggestHidden"

    private init() {
        hidden = Set(UserDefaults.standard.stringArray(forKey: Self.hiddenKey) ?? [])
    }

    /// Whatever was found belongs to one account: another one starts over.
    func prepare(for listener: String?) {
        let who = listener ?? ""
        guard who != owner else { return }
        owner = who
        contactMatches = nil
        contactsDenied = false
        discover = []
        discoverAt = nil
    }

    func visible(_ people: [FollowPerson]) -> [FollowPerson] {
        people.filter { !hidden.contains($0.userId) }
    }

    /// Asked at most once a minute (`loadDiscoverPeople`), unless forced.
    func loadDiscover(api: FAMAPI, force: Bool = false) async {
        if !force, let at = discoverAt, Date().timeIntervalSince(at) < 60 { return }
        discoverAt = Date()
        if let found = try? await api.suggested() { discover = found }
    }

    func dismiss(_ id: String) {
        withAnimation(.easeIn(duration: 0.16)) { _ = hidden.insert(id) }
        UserDefaults.standard.set(Array(hidden), forKey: Self.hiddenKey)
    }

    func isFollowing(_ p: FollowPerson, social: SocialModel) -> Bool {
        if flipping.contains(p.userId) { return true }
        if unflipping.contains(p.userId) { return false }
        return social.isFollowing(p.userId)
    }

    /// Follow flips at once and is put back if refused; Following
    /// unfollows in one tap (decision 7). `via` says where it was tapped.
    func toggle(_ p: FollowPerson, via: String, social: SocialModel) {
        let id = p.userId
        guard !id.isEmpty, !flipping.contains(id), !unflipping.contains(id) else { return }
        if social.isFollowing(id) {
            unflipping.insert(id)
            Task {
                await social.unfollow(id)
                unflipping.remove(id)
            }
        } else {
            flipping.insert(id)
            Task {
                await social.follow(userID: id, via: via)
                flipping.remove(id)
            }
        }
    }

    /// "Find friends in your contacts": the permission, then the match.
    func findFromContacts(api: FAMAPI, social: SocialModel) async {
        guard !contactsAsking else { return }
        contactsAsking = true
        defer { contactsAsking = false }
        switch await ContactsReader.read() {
        case .denied:
            contactsDenied = true
        case let .read(entries):
            contactsDenied = false
            if entries.isEmpty {
                contactMatches = []
                return
            }
            do {
                contactMatches = try await api.contacts(entries)
                if social.graph == nil { await social.loadGraph() }
            } catch {
                social.say(SocialModel.sentence(error, else: "Could not check your contacts"))
            }
        }
    }

    /// Follow all of your contacts not yet followed, in one request
    /// (`/api/friends/follow-all`; a follow per person ran into the pace).
    func followAllContacts(api: FAMAPI, social: SocialModel, via: String) async {
        let ids = (contactMatches ?? []).filter { !social.isFollowing($0.userId) }.map(\.userId)
        guard !ids.isEmpty, !followingAll else { return }
        followingAll = true
        flipping.formUnion(ids)
        do {
            let answer = try await api.followAll(userIDs: ids, via: via)
            let n = answer.followed ?? 0
            social.say(n == 1 ? "Following 1 person" : "Following \(n) people")
        } catch {
            social.say(SocialModel.sentence(error, else: "Could not follow them"))
        }
        await social.loadGraph()
        flipping.subtract(ids)
        followingAll = false
    }
}

// MARK: - Pieces

/// The Follow / Following pill of these rows: `FollowPill`, the one look
/// round three gave every Follow (§645), the tick drawing in on the tap.
struct FindFollowButton: View {
    let on: Bool
    let ticking: Bool
    let name: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            FollowPill(following: on, justFollowed: ticking, size: 12, height: 34, padding: 16)
        }
        .buttonStyle(.press)
        .accessibilityLabel(on ? "Following \(name), tap to unfollow" : "Follow \(name)")
    }
}

/// `.pf-rail.ff-rail`: a heading in copper mono, an optional count and a
/// button on the right.
struct FindRail<Trailing: View>: View {
    let title: String
    var count: Int? = nil
    @ViewBuilder var trailing: () -> Trailing

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Text(title).monoLabel(10, color: FAMColor.copper, tracking: 1.3)
            if let count { Text("\(count)").monoLabel(10, tracking: 0.5) }
            Spacer(minLength: 0)
            trailing()
        }
        .padding(.top, 22)
        .padding(.bottom, 6)
    }
}

extension FindRail where Trailing == EmptyView {
    init(title: String, count: Int? = nil) {
        self.init(title: title, count: count) { EmptyView() }
    }
}

/// `personRow`: face, name, @handle and a note, with Follow - and, for a
/// suggestion, a small X after it that takes the row away (`dismiss`).
struct FindPersonRow: View {
    @EnvironmentObject private var social: SocialModel
    @ObservedObject private var find = FindFriendsModel.shared
    let person: FollowPerson
    var note = ""
    let via: String
    var dismiss: (() -> Void)? = nil

    private var name: String {
        let n = person.name ?? ""
        return n.isEmpty ? "@" + (person.handle ?? "") : n
    }

    var body: some View {
        HStack(spacing: 12) {
            Button {
                social.openPerson(handle: person.handle ?? "", userID: person.userId,
                                  name: person.name ?? "", avatar: person.avatar ?? "")
            } label: {
                HStack(spacing: 12) {
                    Face(name: person.name ?? "", handle: person.handle ?? "", avatar: person.avatar ?? "",
                         size: 40, initialsSize: 14)
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: 5) {
                            Text(name)
                                .font(FAMFont.body(13.5, .semibold))
                                .foregroundStyle(FAMColor.text)
                                .lineLimit(1)
                            if person.explicit == true { ExplicitBadge() }
                        }
                        if let h = person.handle, !h.isEmpty {
                            Text("@" + h).font(FAMFont.mono(9.5)).foregroundStyle(FAMColor.textMuted)
                        }
                        if !note.isEmpty {
                            Text(note).font(FAMFont.body(11)).foregroundStyle(FAMColor.textFaint).lineLimit(1)
                        }
                    }
                    Spacer(minLength: 0)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.press)
            FindFollowButton(on: find.isFollowing(person, social: social),
                             ticking: find.flipping.contains(person.userId), name: name) {
                find.toggle(person, via: via, social: social)
            }
            if let dismiss {
                Button(action: dismiss) {
                    WebIcon(path: Icons.close, size: 12, line: 2.6)
                        .foregroundStyle(FAMColor.textMuted)
                        .frame(width: 30, height: 34)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.press)
                .padding(.leading, -6)
                .accessibilityLabel("Remove \(name) from suggestions")
            }
        }
        .padding(.vertical, 10)
        .padding(.horizontal, 2)
    }
}

/// Suggested for you (`suggestRowsHTML`), as Instagram lists it: a row a
/// person - face, name, @handle, why (the server's words), Follow and an X
/// that hides them on this phone. Drawn on the Friends page under the
/// listener's own people, and nowhere else.
struct SuggestedList: View {
    @ObservedObject private var find = FindFriendsModel.shared
    let people: [FollowPerson]
    let via: String

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(find.visible(people)) { p in
                FindPersonRow(person: p, note: p.reason ?? "Suggested for you", via: via,
                              dismiss: { find.dismiss(p.userId) })
                    .transition(.opacity)
            }
        }
    }
}

/// Your contacts on FAM (`contactsBlockHTML`): the button and its promise
/// first; then the matches with "In your contacts as …" and Follow all.
struct ContactsBlock: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var social: SocialModel
    @ObservedObject private var find = FindFriendsModel.shared
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let matches = find.contactMatches {
                let unfollowed = matches.filter { !social.isFollowing($0.userId) }
                FindRail(title: "Your contacts on FAM", count: matches.count) {
                    if unfollowed.count > 1 {
                        Button {
                            Task { await find.followAllContacts(api: model.api, social: social,
                                                                via: "contacts_all") }
                        } label: {
                            Text("Follow all")
                                .font(FAMFont.body(12, .bold))
                                .foregroundStyle(FAMColor.ink)
                                .padding(.horizontal, 12)
                                .frame(minHeight: 28)
                                .background(FAMColor.copper, in: Capsule())
                        }
                        .buttonStyle(.press)
                        .disabled(find.followingAll)
                    }
                }
                if matches.isEmpty {
                    SocialNote(text: "None of your contacts are on FAM yet. Invite them below.")
                } else {
                    ForEach(matches) { p in
                        FindPersonRow(person: p,
                                      note: (p.contactName ?? "").isEmpty ? "" : "In your contacts as " + (p.contactName ?? ""),
                                      via: "contacts")
                    }
                }
            } else {
                Button {
                    Task { await find.findFromContacts(api: model.api, social: social) }
                } label: {
                    HStack(spacing: 8) {
                        WebIcon(path: FindIcons.addPerson, size: 15)
                        Text(find.contactsAsking ? "Looking\u{2026}" : "Find friends in your contacts")
                            .font(FAMFont.body(12.5, .bold))
                    }
                    .foregroundStyle(FAMColor.ink)
                    .frame(maxWidth: .infinity, minHeight: 42)
                    .background(FAMColor.paper, in: Capsule())
                }
                .buttonStyle(.press)
                .disabled(find.contactsAsking)
                .padding(.top, 14)
                Text(find.contactsDenied
                     ? "FAM can\u{2019}t see your contacts. Allow Contacts for FAM in Settings to find them."
                     : "Only names, numbers and email addresses are checked, and FAM keeps none of them.")
                    .font(FAMFont.body(11))
                    .foregroundStyle(FAMColor.textFaint)
                    .lineSpacing(4)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 8)
                if find.contactsDenied, let url = URL(string: UIApplication.openSettingsURLString) {
                    Button { UIApplication.shared.open(url) } label: {
                        Text("Open Settings").font(FAMFont.body(12, .semibold)).foregroundStyle(FAMColor.copper)
                    }
                    .buttonStyle(.press)
                    .padding(.top, 6)
                }
            }
        }
        .task(id: model.listener?.userId) { find.prepare(for: model.listener?.userId) }
    }
}

// MARK: - "Followed by …" (§667)

/// Up to four faces and "Followed by **Brady**, **Ian**, and 45 others",
/// left-aligned under Follow on anybody's profile; it opens the list.
struct MutualsLine: View {
    let mutuals: PersonMutuals
    let open: () -> Void
    static let faces = 4

    private var people: [PersonFriend] { mutuals.people ?? [] }

    var body: some View {
        Button(action: open) {
            HStack(spacing: 8) {
                HStack(spacing: -7) {
                    ForEach(Array(people.prefix(Self.faces).enumerated()), id: \.offset) { _, p in
                        Face(name: p.name ?? "", handle: p.handle, avatar: p.avatar ?? "", size: 22)
                            .overlay(Circle().stroke(FAMColor.background, lineWidth: 1.5))
                    }
                }
                Self.words(mutuals)
                    .font(FAMFont.body(12))
                    .foregroundStyle(FAMColor.textMuted)
                    .multilineTextAlignment(.leading)
                    .lineLimit(2)
                Spacer(minLength: 0)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.press)
        .accessibilityHint("Shows who you follow that follows them")
    }

    static func firstName(_ p: PersonFriend) -> String {
        let n = (p.name ?? "").isEmpty ? p.handle : (p.name ?? "")
        return n.split(separator: " ").first.map(String.init) ?? n
    }

    /// `mutualsWords`: one name "A", two "A and B", more "A, B, and N others".
    static func words(_ m: PersonMutuals) -> Text {
        let names = (m.people ?? []).prefix(2).map { Text(firstName($0)).bold().foregroundColor(FAMColor.text) }
        let n = max(names.count, m.count ?? 0)
        let rest = n - names.count
        var out = Text("Followed by ")
        if names.count == 1 {
            out = out + names[0]
        } else if names.count == 2 {
            out = rest == 0 ? out + names[0] + Text(" and ") + names[1]
                            : out + names[0] + Text(", ") + names[1]
        }
        if rest > 0 {
            out = out + Text(names.count == 2 ? ", and " : " and ") + Text("\(rest) " + (rest == 1 ? "other" : "others"))
        }
        return out
    }
}

/// The whole list ("Followed by"), each row a profile.
struct MutualsSheet: View {
    @EnvironmentObject private var social: SocialModel
    @Environment(\.dismiss) private var dismiss
    let people: [PersonFriend]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Followed by")
                .font(FAMFont.heading(17, .semibold))
                .foregroundStyle(FAMColor.text)
                .padding(.bottom, 10)
            ScrollView {
                VStack(spacing: 0) {
                    ForEach(people, id: \.handle) { p in
                        Button {
                            dismiss()
                            social.openAfterClose(.person(PersonOpen(handle: p.handle, userID: "",
                                                                     name: p.name ?? "", avatar: p.avatar ?? "")))
                        } label: {
                            HStack(spacing: 12) {
                                Face(name: p.name ?? "", handle: p.handle, avatar: p.avatar ?? "", size: 36)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text((p.name ?? "").isEmpty ? p.handle : (p.name ?? ""))
                                        .font(FAMFont.body(13.5, .semibold))
                                        .foregroundStyle(FAMColor.text)
                                    Text("@" + p.handle).font(FAMFont.mono(9.5)).foregroundStyle(FAMColor.textMuted)
                                }
                                Spacer()
                                WebIcon(path: Icons.chevron, size: 13).foregroundStyle(FAMColor.textFaint)
                            }
                            .padding(.vertical, 9)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.press)
                    }
                }
            }
        }
        .padding(20)
        .background(FAMColor.surface)
    }
}

// MARK: - The profile link and its QR code (§669-§672)

/// Whose code the card shows.
struct QRSubject: Identifiable, Hashable {
    let handle: String
    var name = ""
    var mine = true
    var id: String { handle }
}

extension AppModel {
    /// `familiarize.net/@handle` - the server's home (`AUTH.home`), else
    /// the address this build talks to, as the web's `myProfileLink`.
    func profileLink(_ handle: String) -> URL? {
        guard !handle.isEmpty else { return nil }
        let base = home.isEmpty ? api.base.absoluteString : home
        return URL(string: (base.hasSuffix("/") ? String(base.dropLast()) : base) + "/@" + handle)
    }

    /// The share sheet's three things for a profile link.
    func profileLinkItems(_ q: QRSubject) -> [Any] {
        guard let url = profileLink(q.handle) else { return [] }
        let text = q.mine ? "Follow me on FAM" : "Follow @\(q.handle) on FAM"
        return [ShareTextItem(text: text, subject: "FAM"), url]
    }
}

/// The code drawn on the phone (Core Image, error correction M as the
/// server's segno) from the same `/@handle` link, so it shows with no
/// network and needs no SVG renderer.
enum ProfileQR {
    static func image(_ text: String) -> UIImage? {
        let filter = CIFilter.qrCodeGenerator()
        filter.message = Data(text.utf8)
        filter.correctionLevel = "M"
        guard let out = filter.outputImage?.transformed(by: CGAffineTransform(scaleX: 12, y: 12)),
              let cg = CIContext().createCGImage(out, from: out.extent) else { return nil }
        return UIImage(cgImage: cg)
    }
}

/// `#qrOverlay`: the code on white in either theme (a camera cannot read
/// a dark code on a dark card), the handle, the link, a line and Share.
struct ProfileQRCard: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let subject: QRSubject
    @State private var sharing: [Any]?

    private var link: URL? { model.profileLink(subject.handle) }

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Spacer()
                Button { dismiss() } label: {
                    WebIcon(path: Icons.close, size: 14, line: 2.2)
                        .foregroundStyle(FAMColor.textMuted)
                        .frame(width: 30, height: 30)
                }
                .buttonStyle(.press)
                .accessibilityLabel("Close")
            }
            if let link, let image = ProfileQR.image(link.absoluteString) {
                Image(uiImage: image)
                    .interpolation(.none)
                    .resizable()
                    .scaledToFit()
                    .frame(width: 210, height: 210)
                    .padding(14)
                    .background(Color.white, in: RoundedRectangle(cornerRadius: 16))
                    .accessibilityLabel("QR code for @\(subject.handle)")
            }
            Text("@" + subject.handle)
                .font(FAMFont.heading(17, .semibold))
                .foregroundStyle(FAMColor.text)
                .padding(.top, 16)
            if let link {
                Text(link.absoluteString.replacingOccurrences(of: "https://", with: "")
                        .replacingOccurrences(of: "http://", with: ""))
                    .font(FAMFont.mono(10))
                    .foregroundStyle(FAMColor.textMuted)
                    .padding(.top, 4)
            }
            Text(subject.mine
                 ? "Point a phone camera at this to open your profile and follow you."
                 : "Point a phone camera at this to open \(firstName)\u{2019}s profile.")
                .font(FAMFont.body(12))
                .foregroundStyle(FAMColor.textMuted)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 12)
            ModalButton(label: subject.mine ? "Share your link" : "Share their link", primary: true, wide: true) {
                sharing = model.profileLinkItems(subject)
            }
            .padding(.top, 16)
        }
        .padding(20)
        .background(FAMColor.surface)
        .sheet(isPresented: Binding(get: { sharing != nil }, set: { if !$0 { sharing = nil } })) {
            ActivityView(items: sharing ?? []) { sharing = nil }
        }
    }

    private var firstName: String {
        let n = subject.name.isEmpty ? subject.handle : subject.name
        return n.split(separator: " ").first.map(String.init) ?? "their"
    }
}

/// "Your profile link" with QR and Share, at the foot of the Friends page.
struct ProfileLinkRow: View {
    @EnvironmentObject private var model: AppModel
    let handle: String
    let showQR: () -> Void
    let share: () -> Void

    var body: some View {
        if let url = model.profileLink(handle) {
            HStack(spacing: 8) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Your profile link").font(FAMFont.body(12.5, .semibold)).foregroundStyle(FAMColor.text)
                    Text(url.absoluteString.replacingOccurrences(of: "https://", with: "")
                            .replacingOccurrences(of: "http://", with: ""))
                        .font(FAMFont.mono(9.5))
                        .foregroundStyle(FAMColor.textMuted)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                Spacer(minLength: 4)
                Button(action: showQR) {
                    HStack(spacing: 5) {
                        WebIcon(path: FindIcons.qr, size: 15)
                        Text("QR").font(FAMFont.body(12, .medium))
                    }
                    .foregroundStyle(FAMColor.text)
                    .padding(.horizontal, 12)
                    .frame(minHeight: 32)
                    .overlay(Capsule().stroke(FAMColor.borderStrong))
                }
                .buttonStyle(.press)
                .accessibilityLabel("Show your QR code")
                Button(action: share) {
                    Text("Share").font(FAMFont.body(12, .medium))
                        .foregroundStyle(FAMColor.text)
                        .padding(.horizontal, 12)
                        .frame(minHeight: 32)
                        .overlay(Capsule().stroke(FAMColor.borderStrong))
                }
                .buttonStyle(.press)
            }
            .padding(12)
            .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(FAMColor.border))
        }
    }
}
