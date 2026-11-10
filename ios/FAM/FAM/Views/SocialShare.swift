import SwiftUI
import UIKit

/// Sending inside FAM (`#shareOverlay`, shots 45-48): GROUPS (only when you
/// are in one) and FRIENDS (mutual follows only, §520), each with a
/// round search that filters the faces drawn; a tap selects, a second tap
/// or Cancel unselects, and "Share with @handle" sends - one tap, one
/// message. What is sent is the caller's (`send`): an episode with the id
/// of the one heard (§288 - the web's sheet drops it), or a mix's link.
struct SendInsideFAM: View {
    @EnvironmentObject private var social: SocialModel
    /// Sends to an id; the label is what the toast says. True when sent.
    let send: (_ to: String, _ label: String) async -> Bool
    let sent: () -> Void

    private struct Pick: Equatable {
        let to: String
        let label: String
        let button: String
    }

    @State private var picked: Pick?
    @State private var sending = false
    @State private var groupSearch: String?
    @State private var friendSearch: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if !social.groups.isEmpty {
                section("Groups", search: $groupSearch, prompt: "Search your groups")
                groupFaces
            }
            section("Friends", search: $friendSearch, prompt: "Search your friends")
                .padding(.top, social.groups.isEmpty ? 0 : 18)
            friendFaces
            if picked != nil { confirmBar }
        }
        // Both asked afresh on every open (`loadShareContacts`); what was
        // drawn last time stays while they answer.
        .task {
            await social.loadGraph()
            await social.loadInbox()
        }
    }

    /// The section's mono label and its 30 pt round search toggle.
    private func section(_ title: String, search: Binding<String?>, prompt: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                // `.gc-label`: Geist Mono 9.5, tracking .76, muted.
                Text(title).monoLabel(9.5, tracking: 0.76)
                Spacer()
                let on = search.wrappedValue != nil
                Button {
                    search.wrappedValue = on ? nil : ""
                } label: {
                    // `.share-find`: muted on a hairline; `.on` copper (shot 47).
                    WebIcon(path: Icons.magnifier, size: 15)
                        .foregroundStyle(on ? FAMColor.copper : FAMColor.textMuted)
                        .frame(width: 30, height: 30)
                        .overlay(Circle().stroke(on ? FAMColor.copper : FAMColor.borderStrong))
                        .contentShape(Circle())
                }
                .buttonStyle(.press)
                .accessibilityLabel(prompt)
            }
            if search.wrappedValue != nil {
                ModalField(placeholder: prompt,
                           text: Binding(get: { search.wrappedValue ?? "" }, set: { search.wrappedValue = $0 }),
                           plain: true)
            }
        }
        .padding(.bottom, 10)
    }

    /// By name or handle, a leading "@" ignored (`filterShareSection`).
    private func matches(_ words: String?, _ parts: [String]) -> Bool {
        var q = (words ?? "").trimmingCharacters(in: .whitespaces).lowercased()
        if q.hasPrefix("@") { q = String(q.dropFirst()) }
        guard !q.isEmpty else { return true }
        return parts.joined(separator: " ").lowercased().contains(q)
    }

    @ViewBuilder private var groupFaces: some View {
        let shown = social.groups.filter { matches(groupSearch, [$0.name ?? ""]) }
        if shown.isEmpty {
            line("No group matches that")
        } else {
            faces {
                ForEach(shown) { g in
                    let name = (g.name ?? "").isEmpty ? "Group" : (g.name ?? "")
                    // "Sent to <group name|the group>" (`confirmShareContact`).
                    let said = (g.name ?? "").isEmpty ? "the group" : name
                    let pick = Pick(to: g.with, label: said, button: "Share with " + name)
                    Button { toggle(pick) } label: {
                        PickFace(name: name, handle: "", avatar: g.avatar ?? "", on: picked == pick, label: name)
                    }
                    .buttonStyle(.press)
                }
            }
        }
    }

    @ViewBuilder private var friendFaces: some View {
        let people = social.reachable.filter { matches(friendSearch, [$0.name ?? "", $0.handle ?? ""]) }
        if social.reachable.isEmpty {
            switch social.graphState {
            case .idle, .loading: line("Loading\u{2026}")
            case .failed: line("Could not load your people")
            case .loaded: line(SocialModel.nobodyToSend)
            }
        } else if people.isEmpty {
            line("Nobody matches that")
        } else {
            faces {
                ForEach(people) { p in
                    let first = socialFirstName(p.name ?? "", handle: p.handle ?? "")
                    let handle = p.handle ?? ""
                    let pick = Pick(to: p.userId, label: first,
                                    button: "Share with " + (handle.isEmpty ? first : "@" + handle))
                    Button { toggle(pick) } label: { PickFace(person: p, on: picked == pick) }
                        .buttonStyle(.press)
                }
            }
        }
    }

    private func faces<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(alignment: .top, spacing: 12) { content() }
                .padding(3)
        }
        .padding(.horizontal, -3)
    }

    /// `.sh-name.share-none`: Geist Mono 7.5, faint (shot 47).
    private func line(_ text: String) -> some View {
        Text(text)
            .font(FAMFont.mono(7.5))
            .tracking(0.15)
            .foregroundStyle(FAMColor.textFaint)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.vertical, 4)
    }

    private func toggle(_ pick: Pick) {
        guard !sending else { return }
        picked = picked == pick ? nil : pick
    }

    /// Cancel · Share with @handle; "Sending…" while it goes.
    private var confirmBar: some View {
        HStack(spacing: 8) {
            ModalButton(label: "Cancel") { picked = nil }
            ModalButton(label: sending ? "Sending\u{2026}" : (picked?.button ?? ""), primary: true, wide: true,
                        enabled: !sending) { go() }
        }
        .padding(.top, 14)
    }

    private func go() {
        guard let pick = picked, !sending else { return }
        sending = true
        Task {
            let ok = await send(pick.to, pick.label)
            sending = false
            if ok {
                picked = nil
                sent()
            }
        }
    }
}

/// ANYWHERE ELSE: the server's destinations (`sharing.TARGETS`) as the
/// web's grid (`.sh-grid`): four to a row, each a 40 pt circle with its
/// glyph, the name, and how it goes - Copy is the clipboard; a destination
/// opens its app with the words (Facebook and LinkedIn drop them, so they
/// go to the clipboard too); Instagram and Snapchat get the story card
/// (`shareStoryCard`) - the server's SVG drawn to a 1080x1920 PNG here and
/// handed to iOS's share sheet with the words, which is where those apps
/// take a picture for a story; anything else goes to iOS's share sheet.
/// Then the note when the server has no public address.
struct ShareTargetRows: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.openURL) private var openURL
    let url: String
    let targets: [String: ShareTarget]
    let isPublic: Bool
    /// The story card's address from the share answer ("" for none).
    var card: String = ""
    /// What the card's file is called (`fam-episode.png` / `fam-mix.png`).
    var cardName: String = "fam-episode.png"
    let done: () -> Void
    @State private var activity: [Any]?
    @State private var making = false

    /// The server's order (`sharing.TARGETS`).
    static let order = ["copy", "sms", "email", "whatsapp", "x", "facebook", "linkedin",
                        "instagram_story", "snapchat_story"]

    /// `shareGlyph`.
    static func glyph(_ key: String) -> String {
        switch key {
        case "copy": return "\u{29C9}"
        case "sms": return "\u{2709}"
        case "email": return "@"
        case "whatsapp": return "W"
        case "x": return "X"
        case "facebook": return "f"
        case "linkedin": return "in"
        case "instagram_story": return "IG"
        case "snapchat_story": return "SC"
        default: return "\u{2197}"
        }
    }

    /// The server's keys in its order, then any it adds later.
    private var keys: [String] {
        var out = Self.order.filter { targets[$0] != nil }
        for k in targets.keys.sorted() where !out.contains(k) { out.append(k) }
        return out
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            let columns = Array(repeating: GridItem(.flexible(), spacing: 6, alignment: .top), count: 4)
            LazyVGrid(columns: columns, alignment: .center, spacing: 10) {
                ForEach(keys, id: \.self) { key in
                    if let t = targets[key] { tile(key, t) }
                }
            }
            .padding(.top, 4)
            if !isPublic {
                Text("This server has no public address set, so the link only works on this network.")
                    .font(FAMFont.mono(7.5))
                    .foregroundStyle(FAMColor.coral)
                    .lineSpacing(4)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 10)
            }
        }
        .sheet(isPresented: Binding(get: { activity != nil }, set: { if !$0 { activity = nil } })) {
            ActivityView(items: activity ?? []) {
                activity = nil
                done()
            }
        }
    }

    /// Which way a tap goes, said on the tile (`shareHint`).
    private func hint(_ key: String, _ t: ShareTarget) -> String {
        if key == "copy" { return "clipboard" }
        if t.needsImage == true { return "card" }
        let destination = t.destination ?? ""
        return destination.isEmpty ? "share sheet" : "opens"
    }

    private func tile(_ key: String, _ t: ShareTarget) -> some View {
        let name = t.label ?? key
        return Button { send(key, t) } label: {
            VStack(spacing: 5) {
                Text(Self.glyph(key))
                    .font(FAMFont.body(12, .bold))
                    .foregroundStyle(FAMColor.text)
                    .frame(width: 40, height: 40)
                    .background(FAMColor.surface2, in: Circle())
                    .overlay(Circle().stroke(FAMColor.border))
                Text(name)
                    .font(FAMFont.mono(7.5))
                    .tracking(0.15)
                    .foregroundStyle(FAMColor.textFaint)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                Text(hint(key, t))
                    .font(FAMFont.mono(6.5))
                    .tracking(0.39)
                    .textCase(.uppercase)
                    .foregroundStyle(FAMColor.copper)
                    .opacity(0.75)
                    .multilineTextAlignment(.center)
                    .padding(.top, 2)
            }
            .frame(maxWidth: .infinity)
            .contentShape(Rectangle())
        }
        .buttonStyle(.press)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(name + ", " + hint(key, t))
        .accessibilityAddTraits(.isButton)
    }

    /// `shareTo`.
    private func send(_ key: String, _ t: ShareTarget) {
        let text = t.text ?? url
        // A story is a picture with a link on it, not a sentence with a URL.
        if t.needsImage == true {
            shareCard(text: text)
            return
        }
        if key == "copy" {
            UIPasteboard.general.string = text
            model.nowPlaying.snack("Link copied \u{2014} paste it wherever you like")
            done()
            return
        }
        if let destination = t.destination, !destination.isEmpty, let link = URL(string: destination) {
            let dropsText = key == "facebook" || key == "linkedin"
            if dropsText { UIPasteboard.general.string = text }
            openURL(link)
            if dropsText { model.nowPlaying.say("Your words are on the clipboard \u{2014} paste them in") }
            done()
            return
        }
        // A bare link goes as a link (the receiving app draws its card);
        // words go with the server's subject (`title: t.subject`), which
        // Mail takes for the email's subject.
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.hasPrefix("http"), !trimmed.contains(" "), let link = URL(string: trimmed) {
            activity = [link]
        } else {
            activity = [ShareTextItem(text: text, subject: t.subject ?? "")]
        }
    }

    /// `shareStoryCard`: "Making your card…", the card fetched and drawn to
    /// a PNG, then the share sheet with the picture as a file and the words.
    /// A failure is said, never silent.
    private func shareCard(text: String) {
        guard !making else { return }
        guard !card.isEmpty else {
            model.nowPlaying.say("The card could not be made")
            return
        }
        making = true
        model.nowPlaying.say("Making your card\u{2026}")
        let api = model.api
        let address = card
        let name = cardName
        Task {
            defer { making = false }
            do {
                let svg = try await api.storyCardSVG(address)
                guard let png = StoryCardRenderer.png(svg: svg) else {
                    model.nowPlaying.say("The card could not be drawn")
                    return
                }
                let file = FileManager.default.temporaryDirectory.appendingPathComponent(name)
                try png.write(to: file, options: .atomic)
                activity = [file, text]
            } catch {
                model.nowPlaying.say("The card could not be made")
            }
        }
    }
}
