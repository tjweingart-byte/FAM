import SwiftUI
import UIKit

/// Sending inside FAM (`#shareOverlay`, shots 45-48): GROUPS (only when you
/// are in one) and FRIENDS (mutual follows only, §523), each with a
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
                .buttonStyle(.plain)
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
                    let pick = Pick(to: g.with, label: name, button: "Share with " + name)
                    Button { toggle(pick) } label: {
                        PickFace(name: name, handle: "", avatar: g.avatar ?? "", on: picked == pick, label: name)
                    }
                    .buttonStyle(.plain)
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
                        .buttonStyle(.plain)
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

/// ANYWHERE ELSE: the server's destinations (`sharing.TARGETS`), each with
/// how it goes - Copy is the clipboard; a destination opens its app with
/// the words (Facebook and LinkedIn drop them, so they go to the
/// clipboard too); anything else to iOS's share sheet - and the note when
/// the server has no public address.
struct ShareTargetRows: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.openURL) private var openURL
    let url: String
    let targets: [String: ShareTarget]
    let isPublic: Bool
    let done: () -> Void
    @State private var activity: [Any]?

    /// The web's order (`sharing.TARGETS`); story targets need a card.
    static let order = ["copy", "sms", "email", "whatsapp", "x", "facebook", "linkedin"]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Self.order, id: \.self) { key in
                if let t = targets[key], t.needsImage != true {
                    row(key, t)
                }
            }
            if !isPublic {
                Text("This server has no public address set, so the link only works on this network.")
                    .font(FAMFont.mono(9.5))
                    .foregroundStyle(FAMColor.coral)
                    .lineSpacing(4)
                    .padding(.top, 12)
            }
        }
        .sheet(isPresented: Binding(get: { activity != nil }, set: { if !$0 { activity = nil } })) {
            ActivityView(items: activity ?? []) {
                activity = nil
                done()
            }
        }
    }

    /// Which way a tap goes, said on the row (`shareHint`).
    private func hint(_ key: String, _ t: ShareTarget) -> String {
        if key == "copy" { return "clipboard" }
        let destination = t.destination ?? ""
        return destination.isEmpty ? "share sheet" : "opens"
    }

    private func row(_ key: String, _ t: ShareTarget) -> some View {
        Button { send(key, t) } label: {
            HStack(spacing: 12) {
                Text(t.label ?? key)
                    .font(FAMFont.body(13.5, .semibold))
                    .foregroundStyle(FAMColor.text)
                Spacer()
                Text(hint(key, t)).monoLabel(8.5, color: FAMColor.copper, tracking: 0.85)
            }
            .padding(.vertical, 13)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    /// `shareTo`.
    private func send(_ key: String, _ t: ShareTarget) {
        let text = t.text ?? url
        if key == "copy" {
            UIPasteboard.general.string = text
            model.nowPlaying.say("Link copied \u{2014} paste it wherever you like")
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
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.hasPrefix("http"), !trimmed.contains(" "), let link = URL(string: trimmed) {
            activity = [link]
        } else {
            activity = [text]
        }
    }
}
