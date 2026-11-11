import SwiftUI

// MARK: - The folder chips both shelves share (`folderChipsHtml`)

/// "All" (no count), each of the listener's own folders with its count,
/// "+ New folder" (dashed, copper); with a folder chosen, Rename and Delete
/// folder under them. One 18 pt gutter (Q8: the web's Saved indents twice).
struct FolderChips: View {
    let folders: [SavedFolder]
    @Binding var pick: String
    let newFolder: () -> Void
    let rename: () -> Void
    let delete: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 7) {
                    chip("", "All", count: nil)
                    ForEach(folders) { f in chip(f.id, f.name, count: f.items ?? 0) }
                    Button(action: newFolder) {
                        Text("+ New folder")
                            .font(FAMFont.body(11.5, .semibold))
                            .foregroundStyle(FAMColor.copper)
                            .padding(.horizontal, 13)
                            .frame(minHeight: 30)
                            .overlay(Capsule().stroke(FAMColor.borderStrong,
                                                      style: StrokeStyle(lineWidth: 1, dash: [3, 3])))
                            .contentShape(Capsule())
                    }
                    .buttonStyle(.press)
                }
                .padding(.horizontal, 18)
                .padding(.top, 2)
                .padding(.bottom, 10)
            }
            if !pick.isEmpty {
                HStack(spacing: 16) {
                    Button("Rename", action: rename)
                        .font(FAMFont.mono(10))
                        .tracking(0.6)
                        .foregroundStyle(FAMColor.copper)
                        .buttonStyle(.press)
                    Button("Delete folder", action: delete)
                        .font(FAMFont.mono(10))
                        .tracking(0.6)
                        .foregroundStyle(FAMColor.coral)
                        .buttonStyle(.press)
                }
                .padding(.horizontal, 18)
                .padding(.bottom, 8)
            }
        }
    }

    private func chip(_ id: String, _ label: String, count: Int?) -> some View {
        let on = pick == id
        return Button { pick = id } label: {
            HStack(spacing: 3) {
                Text(label).font(FAMFont.body(11.5, .semibold))
                if let count {
                    Text("\(count)")
                        .font(FAMFont.body(11.5, .medium))
                        .foregroundStyle(on ? FAMColor.ink : FAMColor.textMuted)
                        .padding(.leading, 3)
                }
            }
            .foregroundStyle(on ? FAMColor.ink : FAMColor.text)
            .padding(.horizontal, 13)
            .frame(minHeight: 30)
            .background(on ? FAMColor.copper : FAMColor.surface, in: Capsule())
            .overlay(Capsule().stroke(on ? FAMColor.copper : FAMColor.borderStrong))
            .contentShape(Capsule())
        }
        .buttonStyle(.press)
        .accessibilityAddTraits(on ? .isSelected : [])
    }
}

/// `.sv-empty`: padding 30 18, centred, faint, Geist 11 / 1.6.
struct ShelfMessage: View {
    let text: String

    var body: some View {
        Text(text)
            .font(FAMFont.body(11))
            .foregroundStyle(FAMColor.textFaint)
            .lineSpacing(6)
            .multilineTextAlignment(.center)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity)
            .padding(.horizontal, 18)
            .padding(.vertical, 30)
    }
}

/// `.sv-art`: 38 pt, radius 10, surface-2, the copper play.
struct PlayArt: View {
    var body: some View {
        WebFill(path: Icons.play, size: 15)
            .foregroundStyle(FAMColor.copper)
            .frame(width: 38, height: 38)
            .background(FAMColor.surface2, in: RoundedRectangle(cornerRadius: 10))
    }
}

// MARK: - Save for Later (`#screen-saved`, `save-pointer`)

/// The shelf, whole, filtered here by folder. A row plays by its question
/// and length (a pointer, no episode id) and tells the shelf it was played;
/// its folder button files it; its X takes it off at once, before the
/// server answers, and puts it back if the server says no. Folders are the
/// listener's own - none is ever made for them.
struct SavedView: View {
    @EnvironmentObject private var model: AppModel
    @State private var shelf: SavedShelf?
    @State private var problem = ""
    @State private var loaded = false
    @State private var pick = ""
    @State private var gone: Set<String> = []
    @State private var filing: SavedItem?
    @State private var deleting: SavedFolder?
    @State private var textAsk: TextAsk?

    var body: some View {
        VStack(spacing: 0) {
            YouBackRow(title: "Save for Later") {
                if !model.youPath.isEmpty { model.youPath.removeLast() }
            }
            ScrollView {
                VStack(spacing: 0) { content }
                    .padding(.bottom, 18)
            }
        }
        .background(FAMColor.background)
        .sheet(item: $filing) { item in folderSheet(item) }
        .sheet(item: $deleting) { f in deleteSheet(f) }
        .textModal($textAsk)
        .task { await load() }
    }

    private var items: [SavedItem] {
        (shelf?.items ?? []).filter { !gone.contains($0.id ?? "") }
    }

    private var folders: [SavedFolder] { shelf?.folders ?? [] }

    @ViewBuilder private var content: some View {
        if !problem.isEmpty && shelf == nil {
            ShelfMessage(text: problem)
        } else if !loaded {
            // The web keeps the old shelf or nothing (Q16): here it says so.
            ShelfMessage(text: "Loading\u{2026}")
        } else if items.isEmpty && pick.isEmpty {
            ShelfMessage(text: "Nothing here yet.\nPress save on the player and the episode lands here.")
        } else {
            FolderChips(folders: folders, pick: $pick, newFolder: { newFolder(then: nil) },
                        rename: rename, delete: { deleting = folders.first { $0.id == pick } })
            let shown = pick.isEmpty ? items : items.filter { $0.folderId == pick }
            if shown.isEmpty {
                ShelfMessage(text: "Nothing in this folder yet.\nTap the folder on any episode to put it here.")
            } else {
                ForEach(shown, id: \.rowID) { item in row(item) }
            }
        }
    }

    private func folderName(_ id: String?) -> String {
        guard let id, !id.isEmpty else { return "" }
        return folders.first { $0.id == id }?.name ?? ""
    }

    /// `.sv-row`: the play art, the title (Bricolage 12.5, wraps), "2 MIN ·
    /// Commute", the folder button and the X; a line under it.
    private func row(_ item: SavedItem) -> some View {
        let title = (item.title ?? "").isEmpty ? item.query : (item.title ?? "")
        let place = folderName(item.folderId)
        let meta = FAMAPI.minutes(item.minutes) + " MIN" + (place.isEmpty ? "" : " \u{00B7} " + place)
        return HStack(spacing: 11) {
            Button { model.playSaved(item) } label: {
                HStack(spacing: 11) {
                    PlayArt()
                    VStack(alignment: .leading, spacing: 3) {
                        Text(title)
                            .font(FAMFont.heading(12.5, .medium))
                            .foregroundStyle(FAMColor.text)
                            .lineSpacing(2)
                            .multilineTextAlignment(.leading)
                            .fixedSize(horizontal: false, vertical: true)
                        Text(meta)
                            .font(FAMFont.mono(8))
                            .tracking(0.32)
                            .foregroundStyle(FAMColor.textFaint)
                            .lineLimit(1)
                    }
                    Spacer(minLength: 0)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.press)
            Button { filing = item } label: {
                WebIcon(path: Icons.folder, size: 13)
                    .foregroundStyle(FAMColor.textFaint)
                    .padding(6)
                    .frame(minWidth: 32, minHeight: 32)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.press)
            .accessibilityLabel("Choose a folder for this episode")
            Button { remove(item) } label: {
                WebIcon(path: Icons.close, size: 13, line: 2.2)
                    .foregroundStyle(FAMColor.textFaint)
                    .padding(6)
                    .frame(minWidth: 32, minHeight: 32)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.press)
            .accessibilityLabel("Remove from Saved for Later")
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 12)
        .overlay(alignment: .bottom) { Rectangle().fill(FAMColor.border).frame(height: 1) }
        .transition(.move(edge: .leading).combined(with: .opacity))
    }

    private func load() async {
        do {
            shelf = try await model.api.shelf()
            problem = ""
            if !pick.isEmpty && !folders.contains(where: { $0.id == pick }) { pick = "" }
        } catch {
            problem = SocialModel.sentence(error, else: "Could not open your shelf")
        }
        gone = []
        loaded = true
    }

    /// Off the shelf on the tap; read again either way, so a refusal puts
    /// it back. The player's save goes off if it is that episode.
    private func remove(_ item: SavedItem) {
        guard let id = item.id else { return }
        withAnimation(.easeOut(duration: 0.25)) { _ = gone.insert(id) }
        Task {
            do {
                try await model.api.removeSaved(id)
                model.nowPlaying.savedRemoved(query: item.query, minutes: item.minutes)
                await load()
                model.nowPlaying.say("Removed")
            } catch {
                await load()
                model.nowPlaying.say("Could not remove that")
            }
        }
    }

    private func folderSheet(_ item: SavedItem) -> some View {
        var rows = [YouSheetItem(label: "Put this episode in a folder", group: true)]
        for f in folders {
            let mark = item.folderId == f.id ? "\u{2713} " : ""
            let fid = f.id
            rows.append(YouSheetItem(label: mark + f.name) { file(item, into: fid) })
        }
        if !(item.folderId ?? "").isEmpty {
            rows.append(YouSheetItem(label: "No folder") { file(item, into: "") })
        }
        rows.append(YouSheetItem(label: "+ New folder") {
            newFolder { folder in file(item, into: folder.id, name: folder.name) }
        })
        return YouSheet(items: rows).environmentObject(model)
    }

    private func file(_ item: SavedItem, into folderID: String, name: String = "") {
        guard let id = item.id else { return }
        Task {
            do {
                try await model.api.moveSaved(id, folderID: folderID)
                let called = name.isEmpty ? folderName(folderID) : name
                model.nowPlaying.say(folderID.isEmpty ? "Taken out of the folder"
                                                      : "Moved to " + (called.isEmpty ? "the folder" : called))
                await load()
            } catch {
                model.nowPlaying.say(SocialModel.sentence(error, else: "That did not work"))
            }
        }
    }

    /// "New folder" (`newShelfFolder`): `then` takes the folder made from a
    /// row's sheet, which files that episode in it at once.
    private func newFolder(then: ((SavedFolder) -> Void)?) {
        textAsk = TextAsk(title: "New folder", placeholder: "e.g. Commute", button: "Create") { name in
            Task {
                do {
                    let folder = try await model.api.createFolder(name: name, kind: "saved")
                    if let then {
                        then(folder)
                    } else {
                        pick = folder.id
                        model.nowPlaying.say("Folder created")
                        await load()
                    }
                } catch {
                    model.nowPlaying.say(SocialModel.sentence(error, else: "That did not work"))
                }
            }
        }
    }

    private func rename() {
        let id = pick
        guard !id.isEmpty else { return }
        textAsk = TextAsk(title: "Rename folder", placeholder: "", button: "Save", initial: folderName(id)) { name in
            Task {
                do {
                    try await model.api.renameFolder(id, name: name)
                    model.nowPlaying.say("Renamed")
                    await load()
                } catch {
                    model.nowPlaying.say(SocialModel.sentence(error, else: "That did not work"))
                }
            }
        }
    }

    private func deleteSheet(_ f: SavedFolder) -> some View {
        let rows = [
            YouSheetItem(label: "Delete \u{201C}" + f.name + "\u{201D}? Its episodes stay, outside any folder.", group: true),
            YouSheetItem(label: "Delete folder", danger: true) { deleteFolder(f.id) },
        ]
        return YouSheet(items: rows).environmentObject(model)
    }

    private func deleteFolder(_ id: String) {
        Task {
            do {
                try await model.api.deleteFolder(id)
                pick = ""
                model.nowPlaying.say("Folder deleted")
                await load()
            } catch {
                model.nowPlaying.say("Could not delete that folder")
            }
        }
    }
}

extension SavedItem: Identifiable {
    /// The shelf's row id, else the episode it points at.
    var rowID: String { id ?? (query + "|" + FAMAPI.minutes(minutes)) }
}

// MARK: - Recent listening history (`#screen-history`, `heard-is-kept`)

/// Two weeks of what was heard, newest first, under day headings; tabs
/// filter it on the server - their ids are the code's and their labels the
/// listener's, swapped (`names-swapped`): `myfam` reads "dailyFAM",
/// `dailyfam` reads "myFAM". A row replays the episode that was heard, by
/// its id. There is no clearing it, on the web or the server.
struct HistoryView: View {
    @EnvironmentObject private var model: AppModel
    @State private var tab = ""
    @State private var items: [HistoryItem] = []
    @State private var state: LoadState = .idle

    struct HistTab: Hashable {
        let id: String
        let label: String
    }

    static let tabs: [HistTab] = [HistTab(id: "", label: "All"), HistTab(id: "myfam", label: "dailyFAM"),
                                  HistTab(id: "dailyfam", label: "myFAM"), HistTab(id: "search", label: "searchFAM")]
    static let labels: [String: String] = ["myfam": "dailyFAM", "dailyfam": "myFAM",
                                           "search": "searchFAM", "other": "Shared & more"]

    var body: some View {
        VStack(spacing: 0) {
            YouBackRow(title: "Recent listening history") {
                if !model.youPath.isEmpty { model.youPath.removeLast() }
            }
            tabBar
            ScrollView {
                VStack(alignment: .leading, spacing: 0) { content }
                    .padding(.bottom, 18)
            }
        }
        .background(FAMColor.background)
        .task(id: tab) { await load(tab) }
    }

    /// `#histTabs`: padding 4 18 10, pills 30 tall, copper when on.
    private var tabBar: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 7) {
                ForEach(Self.tabs, id: \.id) { t in
                    let on = tab == t.id
                    Button { tab = t.id } label: {
                        Text(t.label)
                            .font(FAMFont.body(12, .semibold))
                            .foregroundStyle(on ? FAMColor.ink : FAMColor.textMuted)
                            .padding(.horizontal, 13)
                            .frame(minHeight: 30)
                            .background(on ? FAMColor.copper : FAMColor.surface, in: Capsule())
                            .overlay(Capsule().stroke(on ? FAMColor.copper : FAMColor.borderStrong))
                            .contentShape(Capsule())
                    }
                    .buttonStyle(.press)
                    .accessibilityAddTraits(on ? .isSelected : [])
                }
            }
            .padding(.horizontal, 18)
            .padding(.top, 4)
            .padding(.bottom, 10)
        }
    }

    @ViewBuilder private var content: some View {
        switch state {
        case .idle, .loading:
            ShelfMessage(text: "Loading\u{2026}")
        case .failed:
            ShelfMessage(text: "Could not load your history.")
        case .loaded:
            if items.isEmpty {
                ShelfMessage(text: tab.isEmpty
                             ? "Nothing heard in the last two weeks.\nEpisodes you play land here, and stay for two weeks."
                             : "Nothing heard on " + (Self.labels[tab] ?? tab) + " in the last two weeks.")
            } else {
                ForEach(Array(items.enumerated()), id: \.offset) { i, item in
                    if i == 0 || !Self.sameDay(items[i - 1].at, item.at) {
                        Text(Self.dayHeading(item.at))
                            .monoLabel(8.5, color: FAMColor.textFaint, tracking: 0.68)
                            .padding(.horizontal, 18)
                            .padding(.top, 14)
                            .padding(.bottom, 4)
                    }
                    row(item)
                }
            }
        }
    }

    private func row(_ item: HistoryItem) -> some View {
        let title = (item.title ?? "").isEmpty ? item.query : (item.title ?? "")
        var meta = FAMAPI.minutes(item.minutes) + " MIN \u{00B7} " + Self.when(item.at)
        if tab.isEmpty { meta += " \u{00B7} " + (Self.labels[item.surface ?? ""] ?? "") }
        return Button { model.playHistory(item) } label: {
            HStack(spacing: 11) {
                PlayArt()
                VStack(alignment: .leading, spacing: 3) {
                    Text(title)
                        .font(FAMFont.heading(12.5, .medium))
                        .foregroundStyle(FAMColor.text)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(meta)
                            .font(FAMFont.mono(8))
                            .tracking(0.32)
                            .foregroundStyle(FAMColor.textFaint)
                            .lineLimit(1)
                }
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 18)
            .padding(.vertical, 12)
            .overlay(alignment: .bottom) { Rectangle().fill(FAMColor.border).frame(height: 1) }
            .contentShape(Rectangle())
        }
        .buttonStyle(.press)
    }

    /// A reply for a tab left mid-flight is dropped.
    private func load(_ asked: String) async {
        state = .loading
        do {
            let page = try await model.api.history(surface: asked)
            guard asked == tab else { return }
            items = page.items
            state = .loaded
        } catch {
            guard asked == tab else { return }
            state = .failed
        }
    }

    static func sameDay(_ a: Double, _ b: Double) -> Bool {
        Calendar.current.isDate(Date(timeIntervalSince1970: a), inSameDayAs: Date(timeIntervalSince1970: b))
    }

    /// "TODAY", "YESTERDAY", else "FRI, NOV 6".
    static func dayHeading(_ at: Double) -> String {
        let d = Date(timeIntervalSince1970: at)
        let cal = Calendar.current
        if cal.isDateInToday(d) { return "TODAY" }
        if cal.isDateInYesterday(d) { return "YESTERDAY" }
        let f = DateFormatter()
        f.setLocalizedDateFormatFromTemplate("EEEMMMd")
        return f.string(from: d).uppercased()
    }

    /// `yfWhen`: "6:58" today, "Mon" this week, "Nov 3" before that.
    static func when(_ at: Double) -> String {
        let d = Date(timeIntervalSince1970: at)
        let cal = Calendar.current
        if cal.isDateInToday(d) {
            let c = cal.dateComponents([.hour, .minute], from: d)
            return String(format: "%d:%02d", c.hour ?? 0, c.minute ?? 0)
        }
        let f = DateFormatter()
        if Date().timeIntervalSince(d) < 6.5 * 86400 {
            f.setLocalizedDateFormatFromTemplate("EEE")
        } else {
            f.setLocalizedDateFormatFromTemplate("MMMd")
        }
        return f.string(from: d)
    }
}

// MARK: - My Vibe (`#screen-myvibe`)

/// Everything you have vibed, with your vibe folders. A row plays it as it
/// was vibed; its folder button files it; its X takes the vibe back whole -
/// the same act as holding it on your grid, in the web's words for this
/// list ("Vibe taken back", `unvibe`).
struct MyVibeView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var you: YouModel
    @State private var page: VibesPage?
    @State private var state: LoadState = .idle
    @State private var pick = ""
    @State private var filing: VibeRow?
    @State private var deleting: SavedFolder?
    @State private var textAsk: TextAsk?

    var body: some View {
        VStack(spacing: 0) {
            YouBackRow(title: "My Vibe") {
                if !model.youPath.isEmpty { model.youPath.removeLast() }
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 0) { content }
                    .padding(.bottom, 18)
            }
        }
        .background(FAMColor.background)
        .sheet(item: $filing) { v in folderSheet(v) }
        .sheet(item: $deleting) { f in deleteSheet(f) }
        .textModal($textAsk)
        .task { await load() }
    }

    private var vibes: [VibeRow] { page?.vibes ?? [] }
    private var folders: [SavedFolder] { page?.folders ?? [] }

    @ViewBuilder private var content: some View {
        switch state {
        case .idle, .loading:
            if page == nil {
                SocialNote(text: "Loading\u{2026}").padding(.horizontal, 18)
            } else {
                list
            }
        case .failed:
            if page == nil {
                SocialNote(text: "Your vibes could not load. Try again in a moment.").padding(.horizontal, 18)
            } else {
                list
            }
        case .loaded:
            list
        }
    }

    @ViewBuilder private var list: some View {
        if vibes.isEmpty {
            SocialNote(text: "Nothing vibed yet. Tap VIBE! on any episode to share it with your followers. It stays here, and shows on your friends\u{2019} screens as a story for 24 hours.")
                .padding(.horizontal, 18)
        } else {
            FolderChips(folders: folders, pick: $pick, newFolder: { newFolder(then: nil) },
                        rename: rename, delete: { deleting = folders.first { $0.id == pick } })
            let shown = pick.isEmpty ? vibes : vibes.filter { $0.folderId == pick }
            if shown.isEmpty {
                SocialNote(text: "Nothing in this folder yet. Tap the folder on any vibe to put it here.")
                    .padding(.horizontal, 18)
            } else {
                VStack(spacing: 9) {
                    ForEach(shown) { v in row(v) }
                }
                .padding(.horizontal, 18)
            }
        }
    }

    private func folderName(_ id: String?) -> String {
        guard let id, !id.isEmpty else { return "" }
        return folders.first { $0.id == id }?.name ?? ""
    }

    /// `.vibe-row`: a card - the title Bricolage 13, "2 MIN · VIBED · BEST
    /// OF THE WEEK" in copper, then two 26 pt round buttons.
    private func row(_ v: VibeRow) -> some View {
        let title = (v.title ?? "").isEmpty ? v.query : (v.title ?? "")
        let place = folderName(v.folderId)
        let meta = FAMAPI.minutes(v.minutes ?? FAMAPI.browseMinutes) + " min \u{00B7} vibed"
            + (place.isEmpty ? "" : " \u{00B7} " + place)
        return HStack(spacing: 6) {
            Button { model.playOwnVibe(query: v.query, minutes: v.minutes, title: v.title) } label: {
                VStack(alignment: .leading, spacing: 4) {
                    Text(title)
                        .font(FAMFont.heading(13, .medium))
                        .foregroundStyle(FAMColor.text)
                        .lineSpacing(3)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(meta).monoLabel(8, color: FAMColor.copper, tracking: 0.64)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.press)
            roundButton(Icons.folder, line: 2, label: "Choose a folder for this vibe") { filing = v }
            roundButton(Icons.close, line: 2.4, label: "Take this vibe back") { remove(v) }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 13)
        .background(FAMColor.surface, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(FAMColor.border))
    }

    private func roundButton(_ icon: Path, line: CGFloat, label: String,
                             action: @escaping () -> Void) -> some View {
        Button(action: action) {
            WebIcon(path: icon, size: 12, line: line)
                .foregroundStyle(FAMColor.textMuted)
                .frame(width: 26, height: 26)
                .overlay(Circle().stroke(FAMColor.borderStrong))
                .frame(minWidth: 32, minHeight: 32)
                .contentShape(Rectangle())
        }
        .buttonStyle(.press)
        .accessibilityLabel(label)
    }

    private func load() async {
        if page == nil { state = .loading }
        do {
            page = try await model.api.myVibes()
            state = .loaded
            if !pick.isEmpty && !folders.contains(where: { $0.id == pick }) { pick = "" }
        } catch {
            state = .failed
        }
    }

    private func remove(_ v: VibeRow) {
        Task {
            let ok = await you.deleteVibe(query: v.query, minutes: v.minutes ?? FAMAPI.browseMinutes)
            await load()
            if ok { model.nowPlaying.snack("Vibe taken back") } else { model.nowPlaying.say("Could not take that back") }
        }
    }

    private func folderSheet(_ v: VibeRow) -> some View {
        var rows = [YouSheetItem(label: "Put this episode in a folder", group: true)]
        for f in folders {
            let mark = v.folderId == f.id ? "\u{2713} " : ""
            let fid = f.id
            rows.append(YouSheetItem(label: mark + f.name) { file(v, into: fid) })
        }
        if !(v.folderId ?? "").isEmpty {
            rows.append(YouSheetItem(label: "No folder") { file(v, into: "") })
        }
        rows.append(YouSheetItem(label: "+ New folder") {
            newFolder { folder in file(v, into: folder.id, name: folder.name) }
        })
        return YouSheet(items: rows).environmentObject(model)
    }

    private func file(_ v: VibeRow, into folderID: String, name: String = "") {
        Task {
            do {
                try await model.api.fileVibe(query: v.query, minutes: v.minutes ?? FAMAPI.browseMinutes,
                                             folderID: folderID)
                let called = name.isEmpty ? folderName(folderID) : name
                model.nowPlaying.say(folderID.isEmpty ? "Taken out of the folder"
                                                      : "Moved to " + (called.isEmpty ? "the folder" : called))
                await load()
            } catch {
                model.nowPlaying.say(SocialModel.sentence(error, else: "That did not work"))
            }
        }
    }

    private func newFolder(then: ((SavedFolder) -> Void)?) {
        textAsk = TextAsk(title: "New folder", placeholder: "e.g. Best of the week", button: "Create") { name in
            Task {
                do {
                    let folder = try await model.api.createFolder(name: name, kind: "vibe")
                    if let then {
                        then(folder)
                    } else {
                        pick = folder.id
                        model.nowPlaying.say("Folder created")
                        await load()
                    }
                } catch {
                    model.nowPlaying.say(SocialModel.sentence(error, else: "That did not work"))
                }
            }
        }
    }

    private func rename() {
        let id = pick
        guard !id.isEmpty else { return }
        textAsk = TextAsk(title: "Rename folder", placeholder: "", button: "Save", initial: folderName(id)) { name in
            Task {
                do {
                    try await model.api.renameFolder(id, name: name)
                    model.nowPlaying.say("Renamed")
                    await load()
                } catch {
                    model.nowPlaying.say(SocialModel.sentence(error, else: "That did not work"))
                }
            }
        }
    }

    private func deleteSheet(_ f: SavedFolder) -> some View {
        let rows = [
            YouSheetItem(label: "Delete \u{201C}" + f.name + "\u{201D}? Its episodes stay, outside any folder.", group: true),
            YouSheetItem(label: "Delete folder", danger: true) { deleteFolder(f.id) },
        ]
        return YouSheet(items: rows).environmentObject(model)
    }

    private func deleteFolder(_ id: String) {
        Task {
            do {
                try await model.api.deleteFolder(id)
                pick = ""
                model.nowPlaying.say("Folder deleted")
                await load()
            } catch {
                model.nowPlaying.say("Could not delete that folder")
            }
        }
    }
}
