import SwiftUI

/// Search DailyFAM (`screen-myfamsearch`), reached from the bookshelf on
/// searchFAM: the A to Z of episodes other listeners made, a sticky letter
/// over each group, the day each was made; typing searches them.
struct CatalogView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss

    @State private var all: [CatalogEpisode]?
    @State private var failed = false
    @State private var words = ""
    @State private var found: [CatalogEpisode]?
    @State private var searching = false
    @State private var searchFailed = false
    @State private var searchTask: Task<Void, Never>?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 10) {
                Button { dismiss() } label: {
                    WebIcon(path: Icons.back, size: 20, line: 2.6).foregroundStyle(FAMColor.text)
                }
                .accessibilityLabel("Back")
                Text("Search DailyFAM").font(FAMFont.heading(19, .medium)).foregroundStyle(FAMColor.text)
                Spacer()
            }
            .padding(.horizontal, 18)
            .padding(.top, 16)
            .padding(.bottom, 10)
            HStack(spacing: 8) {
                WebIcon(path: Icons.search, size: 15).foregroundStyle(FAMColor.textMuted)
                TextField("", text: $words, prompt: Text("Search episodes people made").foregroundColor(FAMColor.textMuted))
                    .font(FAMFont.body(14))
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                    .onChange(of: words) { _, text in search(text) }
            }
            .padding(.horizontal, 12)
            .frame(minHeight: 40)
            .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 13))
            .overlay(RoundedRectangle(cornerRadius: 13).stroke(FAMColor.border))
            .padding(.horizontal, 18)
            .padding(.bottom, 6)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0, pinnedViews: [.sectionHeaders]) {
                    if !words.trimmingCharacters(in: .whitespaces).isEmpty {
                        searchResults
                    } else {
                        catalogue
                    }
                }
            }
        }
        .background(FAMColor.background)
        .task { await loadAll() }
    }

    @ViewBuilder private var catalogue: some View {
        if failed {
            note("Could not load the episodes right now.", button: "Try again") {
                failed = false
                all = nil
                Task { await loadAll() }
            }
        } else if let all {
            if all.isEmpty {
                note("Nobody else has made an episode yet. The ones they make come up here, A to Z, ready to play.")
            }
            ForEach(groups(all), id: \.0) { group in
                Section {
                    ForEach(group.1, id: \.self) { row($0) }
                } header: {
                    Text(group.0)
                        .font(FAMFont.heading(18, .semibold))
                        .foregroundStyle(FAMColor.copper)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 18)
                        .padding(.top, 14)
                        .padding(.bottom, 6)
                        .background(FAMColor.background)
                        .padding(.horizontal, 18)
                }
            }
        } else {
            note("Loading the episodes people made…")
        }
    }

    @ViewBuilder private var searchResults: some View {
        if searching && found == nil {
            note("Searching…")
        } else if searchFailed {
            note("Could not search right now. Try again in a moment.")
        } else if let found {
            if found.isEmpty {
                // The typed words go to searchFAM's box, which writes one
                // (`searchFromMyFam`): put there, not sent.
                note("Nobody has made an episode like that yet.", button: "Search FAM for it") {
                    model.searchPrefill = words.trimmingCharacters(in: .whitespacesAndNewlines)
                    dismiss()
                }
            }
            ForEach(found, id: \.self) { row($0) }
        }
    }

    private func row(_ ep: CatalogEpisode) -> some View {
        Button { dismiss(); model.playCatalog(ep) } label: {
            HStack(spacing: 12) {
                ZStack {
                    RoundedRectangle(cornerRadius: 10).fill(FAMColor.surface2)
                    WebFill(path: Icons.rowPlay, size: 14).foregroundStyle(FAMColor.copper)
                    if let url = model.api.picture(ep.thumb ?? "") {
                        AsyncImage(url: url) { $0.resizable().scaledToFill() } placeholder: { Color.clear }
                            .clipShape(RoundedRectangle(cornerRadius: 10))
                    }
                }
                .frame(width: 56, height: 42)
                VStack(alignment: .leading, spacing: 3) {
                    HStack(alignment: .firstTextBaseline, spacing: 5) {
                        if ep.explicit == true {
                            ExplicitBadge()
                        }
                        Text(ep.title.isEmpty ? ep.query : ep.title)
                            .font(FAMFont.heading(12.5, .medium))
                            .foregroundStyle(FAMColor.text)
                            .lineSpacing(3)
                            .multilineTextAlignment(.leading)
                    }
                    Text(meta(ep)).monoLabel(8, color: FAMColor.textFaint, tracking: 0.32)
                }
                Spacer(minLength: 0)
            }
            // Inset twice, as the web's row sits in its padded list: the
            // picture at 36 pt, the divider under the row only.
            .padding(.horizontal, 18)
            .padding(.vertical, 12)
            .contentShape(Rectangle())
            .overlay(alignment: .bottom) { Rectangle().fill(FAMColor.border).frame(height: 1) }
            .padding(.horizontal, 18)
        }
        .buttonStyle(.press)
    }

    /// `.sv-empty`: centred, Geist 11, faint, 30 pt above and below; a
    /// copper pill after the words when there is something to do.
    private func note(_ text: String, button: String? = nil, action: @escaping () -> Void = {}) -> some View {
        VStack(spacing: 8) {
            Text(text)
                .font(FAMFont.body(11))
                .foregroundStyle(FAMColor.textFaint)
                .lineSpacing(6)
                .multilineTextAlignment(.center)
            if let button {
                Button(action: action) {
                    Text(button)
                        .font(FAMFont.body(12, .bold))
                        .foregroundStyle(FAMColor.ink)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 6)
                        .background(FAMColor.copper, in: Capsule())
                }
                .buttonStyle(.press)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 36)
        .padding(.vertical, 30)
    }

    /// "1.5 MIN · 3 PLAYS · TODAY", the plays left out at none (`myFamSearchRow`).
    private func meta(_ ep: CatalogEpisode) -> String {
        let plays = ep.plays ?? 0
        var parts = ["\(FAMAPI.minutes(ep.minutes)) min"]
        if plays > 0 { parts.append("\(plays) play\(plays == 1 ? "" : "s")") }
        if let age = ep.sourcedAgeSeconds { parts.append(dayLabel(age)) }
        return parts.joined(separator: " · ")
    }

    /// Today, Yesterday, N days ago to six, then the date (`episodeDayLabel`).
    private func dayLabel(_ age: Double) -> String {
        let made = Date().addingTimeInterval(-age)
        let cal = Calendar.current
        let days = cal.dateComponents([.day], from: cal.startOfDay(for: made), to: cal.startOfDay(for: Date())).day ?? 0
        switch days {
        case ..<1: return "Today"
        case 1: return "Yesterday"
        case 2...6: return "\(days) days ago"
        default:
            // The web's English month names whatever the phone's language
            // (`MONTHS_SHORT`): "Oct 28", then ", 2025" once not this year.
            let gregorian = Calendar(identifier: .gregorian)
            let sameYear = gregorian.component(.year, from: made) == gregorian.component(.year, from: Date())
            let format = DateFormatter()
            format.locale = Locale(identifier: "en_US_POSIX")
            format.calendar = gregorian
            format.timeZone = TimeZone.current
            format.dateFormat = sameYear ? "MMM d" : "MMM d, yyyy"
            return format.string(from: made)
        }
    }

    /// A to Z by the first letter, ignoring leading punctuation; "#" last.
    private func groups(_ rows: [CatalogEpisode]) -> [(String, [CatalogEpisode])] {
        var out: [String: [CatalogEpisode]] = [:]
        for ep in rows {
            let letter = ep.letter ?? String(ep.title.first(where: { $0.isLetter }).map { String($0).uppercased() } ?? "#")
            out[letter, default: []].append(ep)
        }
        return out.keys.sorted { a, b in
            if a == "#" { return false }
            if b == "#" { return true }
            return a < b
        }.map { ($0, out[$0] ?? []) }
    }

    private func loadAll() async {
        do {
            all = try await model.api.catalog()
            failed = false
        } catch {
            failed = true
        }
    }

    private func search(_ text: String) {
        searchTask?.cancel()
        let q = text.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else {
            found = nil
            if failed { Task { await loadAll() } }
            return
        }
        searching = true
        searchTask = Task {
            try? await Task.sleep(nanoseconds: 250_000_000)
            if Task.isCancelled { return }
            do {
                let rows = try await model.api.searchCatalog(q)
                if Task.isCancelled { return }
                found = rows
                searchFailed = false
            } catch {
                searchFailed = true
            }
            searching = false
        }
    }
}
