import CryptoKit
import Foundation

/// One episode kept on this phone for offline listening: the web's
/// `OfflineShelf` meta row (`static/index.html`, the 27/09 packet, §161).
struct OfflineEpisode: Codable, Equatable {
    let key: String
    let query: String
    let minutes: Double
    let context: String
    let title: String
    /// Bytes of 16-bit samples on disk - what the 250 MB ceiling counts.
    let bytes: Int
    let rate: Double
    /// The samples' file in the shelf's folder.
    let file: String
    /// When it was kept or last played from here: the oldest goes first.
    var at: Double
}

/// The episodes this phone has in hand, so they play with no connection -
/// the web's `OfflineShelf`, rule for rule:
///
/// * **What is kept**: an episode whose whole stream has arrived on this
///   phone and that the server marked keepable (`X-FAM-Keepable: 1`, a
///   production voice, never the placeholder tone, never an attachment's).
///   Automatic, not a download button. A private search's is kept too, as
///   the web keeps it (it stays on this phone and is never sent anywhere).
/// * **How**: exactly what the player already holds - the episode's raw
///   16-bit PCM, no header, no audio file format, nothing converted
///   (`no-audio-files`) - plus a small index, so what is here is known
///   without reading any audio.
/// * **Where**: Application Support, never backed up and never uploaded
///   (`isExcludedFromBackup`); nothing here is ever sent anywhere.
/// * **How much**: 40 episodes, 250 MB, the oldest played out first; a
///   replay from here makes it the last to go.
/// * **Keyed as the web keys it**: the question and the context trimmed,
///   spaces collapsed, lower case, with the length between them.
/// * **Forgotten** on sign-out and account deletion, with the remembered
///   DailyFAM page (`forgetOfflineCopies`): this phone is not the next
///   listener's.
///
/// The index lives on the main actor; file work runs on one serial queue,
/// so a write, a read and a clear never cross.
@MainActor
final class OfflineShelf {
    static let maxEpisodes = 40
    static let maxBytes = 250 * 1024 * 1024

    private(set) var index: [String: OfflineEpisode] = [:]
    private let folder: URL
    private let io = DispatchQueue(label: "net.familiarize.fam.offline", qos: .utility)

    init() {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        folder = base.appendingPathComponent("FAMOffline", isDirectory: true)
        loadIndex()
    }

    /// Every key kept here, for the cards' faded / full brightness.
    var keys: Set<String> { Set(index.keys) }

    // MARK: - Keys

    /// `norm` on the web: trimmed, spaces collapsed, lower case.
    nonisolated static func norm(_ text: String) -> String {
        let words = text.split(whereSeparator: { $0.isWhitespace })
        return words.joined(separator: " ").lowercased()
    }

    /// `keyOf`: question | length | context - 1.5 stays "1.5", 2.0 is "2",
    /// as JavaScript prints a number.
    nonisolated static func keyOf(query: String, minutes: Double, context: String) -> String {
        norm(query) + "|" + FAMAPI.minutes(minutes) + "|" + norm(context)
    }

    nonisolated static func keyOf(_ key: EpisodeKey) -> String {
        keyOf(query: key.query, minutes: key.minutes, context: key.context)
    }

    func has(_ key: EpisodeKey) -> Bool { index[Self.keyOf(key)] != nil }

    func entry(_ key: EpisodeKey) -> OfflineEpisode? { index[Self.keyOf(key)] }

    // MARK: - Keeping, reading, forgetting

    /// Keep a whole episode (`put`): the index at once, the samples written
    /// behind it; a write that fails takes the row back out.
    func put(_ key: EpisodeKey, title: String, rate: Double, samples: [Float]) {
        guard !samples.isEmpty, rate > 0 else { return }
        let id = Self.keyOf(key)
        let file = Self.fileName(for: id)
        let row = OfflineEpisode(key: id, query: key.query, minutes: key.minutes, context: key.context,
                                 title: title.isEmpty ? key.query : title,
                                 bytes: samples.count * 2, rate: rate, file: file,
                                 at: Date().timeIntervalSince1970)
        index[id] = row
        let url = folder.appendingPathComponent(file)
        let dir = folder
        let floats = samples
        io.async {
            let ok = Self.write(floats, to: url, in: dir)
            if !ok {
                Task { @MainActor in self.writeFailed(id: id, file: file) }
            }
        }
        evict()
        saveIndex()
    }

    private func writeFailed(id: String, file: String) {
        if index[id]?.file == file { index[id] = nil }
        saveIndex()
        onChange?()
    }

    /// Told whenever what is kept changes (a write failed, an eviction).
    var onChange: (() -> Void)?

    /// The samples of a kept episode, read off the main thread (`get`);
    /// played again, so it is the last to go. Nil when the file is gone.
    func samples(for row: OfflineEpisode) async -> [Float]? {
        let url = folder.appendingPathComponent(row.file)
        let floats: [Float]? = await withCheckedContinuation { (done: CheckedContinuation<[Float]?, Never>) in
            io.async {
                done.resume(returning: Self.read(url))
            }
        }
        if floats == nil {
            index[row.key] = nil
            saveIndex()
            onChange?()
            return nil
        }
        if var again = index[row.key] {
            again.at = Date().timeIntervalSince1970
            index[row.key] = again
            saveIndex()
        }
        return floats
    }

    /// Everything kept here for the listener who is leaving.
    func clear() {
        index = [:]
        let dir = folder
        io.async {
            try? FileManager.default.removeItem(at: dir)
        }
        onChange?()
    }

    // MARK: - The remembered DailyFAM page (`rememberFeed`)

    /// The last `/api/myfam` answer that drew, so an offline open still
    /// shows the cards. Friends' pictures are left out of the copy (each is
    /// up to 96 KB and a page can carry dozens); offline, a face falls back
    /// to its initials.
    func rememberFeed(_ data: Data) {
        guard var body = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return }
        if let faces = body["friend_faces"] as? [[String: Any]] {
            var bare: [[String: Any]] = []
            for face in faces {
                var copy = face
                copy["avatar"] = ""
                bare.append(copy)
            }
            body["friend_faces"] = bare
        }
        guard let copy = try? JSONSerialization.data(withJSONObject: body) else { return }
        let url = folder.appendingPathComponent("last-feed.json")
        let dir = folder
        io.async {
            Self.ensure(dir)
            try? copy.write(to: url, options: .atomic)
        }
    }

    func rememberedFeed() -> RailsPage? {
        let url = folder.appendingPathComponent("last-feed.json")
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? FAMAPI.decoder.decode(RailsPage.self, from: data)
    }

    // MARK: - Inside

    /// Oldest out first while over 40 episodes or 250 MB (`evict`).
    private func evict() {
        var rows = Array(index.values)
        rows.sort { $0.at < $1.at }
        var bytes = 0
        for row in rows { bytes += row.bytes }
        var gone: [OfflineEpisode] = []
        while !rows.isEmpty && (rows.count > Self.maxEpisodes || bytes > Self.maxBytes) {
            let row = rows.removeFirst()
            bytes -= row.bytes
            gone.append(row)
            index[row.key] = nil
        }
        guard !gone.isEmpty else { return }
        let dir = folder
        let files = gone.map { $0.file }
        io.async {
            for file in files {
                try? FileManager.default.removeItem(at: dir.appendingPathComponent(file))
            }
        }
        onChange?()
    }

    private func loadIndex() {
        let url = folder.appendingPathComponent("index.json")
        guard let data = try? Data(contentsOf: url),
              let rows = try? JSONDecoder().decode([OfflineEpisode].self, from: data) else { return }
        var found: [String: OfflineEpisode] = [:]
        for row in rows { found[row.key] = row }
        index = found
    }

    private func saveIndex() {
        let rows = Array(index.values)
        guard let data = try? JSONEncoder().encode(rows) else { return }
        let url = folder.appendingPathComponent("index.json")
        let dir = folder
        io.async {
            Self.ensure(dir)
            try? data.write(to: url, options: .atomic)
        }
    }

    private static func fileName(for key: String) -> String {
        let digest = SHA256.hash(data: Data(key.utf8))
        var hex = ""
        for byte in digest { hex += String(format: "%02x", byte) }
        return String(hex.prefix(40)) + ".pcm"
    }

    /// The folder, made once and kept out of iCloud and device backups.
    nonisolated private static func ensure(_ dir: URL) {
        let files = FileManager.default
        if files.fileExists(atPath: dir.path) { return }
        try? files.createDirectory(at: dir, withIntermediateDirectories: true)
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        var marked = dir
        try? marked.setResourceValues(values)
    }

    /// Floats back to the 16-bit samples they were made from (value / 32768).
    nonisolated private static func write(_ floats: [Float], to url: URL, in dir: URL) -> Bool {
        ensure(dir)
        var data = Data(count: floats.count * 2)
        data.withUnsafeMutableBytes { (raw: UnsafeMutableRawBufferPointer) in
            for i in 0..<floats.count {
                let scaled = (floats[i] * 32768).rounded()
                let value = Int16(clamping: Int(max(-32768, min(32767, scaled))))
                raw.storeBytes(of: value.littleEndian, toByteOffset: i * 2, as: Int16.self)
            }
        }
        do {
            try data.write(to: url, options: .atomic)
            return true
        } catch {
            return false
        }
    }

    nonisolated private static func read(_ url: URL) -> [Float]? {
        guard let data = try? Data(contentsOf: url), data.count >= 2 else { return nil }
        let count = data.count / 2
        var out = [Float](repeating: 0, count: count)
        data.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            for i in 0..<count {
                let value = Int16(littleEndian: raw.loadUnaligned(fromByteOffset: i * 2, as: Int16.self))
                out[i] = Float(value) / 32768
            }
        }
        return out
    }
}
