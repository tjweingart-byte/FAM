import Foundation
import SwiftUI
import UIKit
import UniformTypeIdentifiers

/// One chip in the search bar (`ATTACHED` on the web): shown at once as
/// pending ("reading…"), then the server's answer - or left on screen as a
/// failed chip carrying the server's sentence, because a file that silently
/// fails to attach makes an episode about a document nobody read.
struct AttachSlot: Identifiable, Equatable {
    let id = UUID()
    var name: String
    var kind: String
    var serverID = ""
    var chars = 0
    var pending = true
    var error = ""

    /// The chip's small uppercase note (`.sz`).
    var note: String {
        if !error.isEmpty { return error }
        if pending { return "reading\u{2026}" }
        if kind == "image" { return "photo" }
        if kind == "link" { return "link" }
        if chars > 0 { return "\(Int((Double(chars) / 1000).rounded()))k chars" }
        return "document"
    }
}

/// What the listener attached to the next search (`op-attachments`):
/// extracted at attach time, never on the generation path. An episode built
/// on these is never cached, never in history and never queued.
@MainActor
final class AttachModel: ObservableObject {
    /// `attachments.MAX_BYTES`: the server refuses more, in these words.
    static let maxBytes = 8 * 1024 * 1024
    static let tooBig = "That file is over 8 MB."
    /// `attach=` takes 400 characters of ids (16 hex each, comma-joined), so
    /// this many fit: the attach button is not offered past it.
    static let most = 23
    /// The question an attachment on its own asks (`sendSearch`).
    static let summarise = "Summarise what I have attached, and tell me what matters in it."

    @Published private(set) var items: [AttachSlot] = []

    private let api: FAMAPI
    var say: (String) -> Void = { _ in }

    init(api: FAMAPI) { self.api = api }

    var isEmpty: Bool { items.isEmpty }
    var pending: Bool { items.contains { $0.pending } }
    var full: Bool { items.count >= Self.most }

    /// `attachedIds`: the ones the server holds, failed ones left out.
    var ids: String {
        items.filter { !$0.serverID.isEmpty && $0.error.isEmpty }.map { $0.serverID }.joined(separator: ",")
    }

    /// The first chip's name: the title of a search that is only an attachment.
    var firstName: String { items.first?.name ?? "" }

    func clear() { items = [] }

    /// × on a chip: gone at once, the server told (fire and forget).
    func remove(_ slot: AttachSlot) {
        items.removeAll { $0.id == slot.id }
        let gone = slot.serverID
        if !gone.isEmpty { Task { await api.detach(gone) } }
    }

    /// A link from the "Add a link" modal.
    func addLink(_ url: String) {
        let text = url.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        send(kind: "link", name: text, data: "", url: text)
    }

    /// A photo from the library: always a JPEG named `.jpg`, since the server
    /// types photos by their suffix and refuses HEIC (quirk 6).
    func addPhoto(_ data: Data) {
        let n = items.filter { $0.kind == "image" }.count
        let name = n == 0 ? "Photo.jpg" : "Photo \(n + 1).jpg"
        guard let jpeg = Self.jpeg(data) else {
            failNow(name: name, kind: "image", "That file could not be read")
            return
        }
        guard jpeg.count <= Self.maxBytes else {
            failNow(name: name, kind: "image", Self.tooBig)
            return
        }
        send(kind: "image", name: name, data: jpeg.base64EncodedString(), url: "")
    }

    /// A file from Files: a photo is made a JPEG as above; anything else is
    /// sent as it is and the server reads it (or says why not).
    func addFile(_ url: URL) {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        guard let data = try? Data(contentsOf: url) else {
            nowSay("That file could not be read")
            return
        }
        let type = UTType(filenameExtension: url.pathExtension.lowercased())
        let isImage = type?.conforms(to: .image) ?? false
        if isImage {
            let base = url.deletingPathExtension().lastPathComponent
            let name = (base.isEmpty ? "Photo" : base) + ".jpg"
            guard let jpeg = Self.jpeg(data) else {
                failNow(name: name, kind: "image", "That file could not be read")
                return
            }
            guard jpeg.count <= Self.maxBytes else {
                failNow(name: name, kind: "image", Self.tooBig)
                return
            }
            send(kind: "image", name: name, data: jpeg.base64EncodedString(), url: "")
            return
        }
        let name = url.lastPathComponent
        guard data.count <= Self.maxBytes else {
            failNow(name: name, kind: "document", Self.tooBig)
            return
        }
        send(kind: "document", name: name, data: data.base64EncodedString(), url: "")
    }

    private func nowSay(_ text: String) { say(text) }

    /// A chip that failed before anything was sent (too big, unreadable):
    /// on screen with its sentence, and the sentence as a toast.
    private func failNow(name: String, kind: String, _ sentence: String) {
        var slot = AttachSlot(name: name, kind: kind)
        slot.pending = false
        slot.error = sentence
        items.append(slot)
        say(sentence)
    }

    /// `sendAttachment`: the chip first, then `POST /api/attach`.
    private func send(kind: String, name: String, data: String, url: String) {
        let slot = AttachSlot(name: name, kind: kind)
        let local = slot.id
        items.append(slot)
        let api = self.api
        Task {
            do {
                let got = try await api.attach(kind: kind, name: name, data: data, url: url)
                update(local) { s in
                    s.pending = false
                    s.serverID = got.id
                    s.kind = got.kind
                    s.name = got.name.isEmpty ? name : got.name
                    s.chars = got.chars ?? 0
                }
            } catch {
                var sentence = "That could not be attached"
                if case let FAMError.refused(_, message) = error, !message.isEmpty { sentence = message }
                update(local) { s in
                    s.pending = false
                    s.error = sentence
                }
                // Only if the chip is still there: a removed one says nothing.
                if items.contains(where: { $0.id == local }) { say(sentence) }
            }
        }
    }

    private func update(_ id: UUID, _ change: (inout AttachSlot) -> Void) {
        guard let i = items.firstIndex(where: { $0.id == id }) else { return }
        var slot = items[i]
        change(&slot)
        items[i] = slot
    }

    /// Any photo as a JPEG under the cap: as it is at 0.85, then the long
    /// edge held to 2048 points if that is still too big.
    static func jpeg(_ data: Data) -> Data? {
        guard let image = UIImage(data: data) else { return nil }
        if let first = image.jpegData(compressionQuality: 0.85), first.count <= maxBytes { return first }
        let longest = max(image.size.width, image.size.height)
        guard longest > 0 else { return nil }
        let scale = min(1, 2048 / longest)
        let size = CGSize(width: image.size.width * scale, height: image.size.height * scale)
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        let renderer = UIGraphicsImageRenderer(size: size, format: format)
        let smaller = renderer.image { _ in image.draw(in: CGRect(origin: .zero, size: size)) }
        return smaller.jpegData(compressionQuality: 0.8)
    }

    /// What Files offers (`accept=".pdf,.docx,.txt,.md,.csv,.json,image/*"`).
    static var fileTypes: [UTType] {
        var types: [UTType] = [.pdf, .plainText, .commaSeparatedText, .json, .image]
        if let docx = UTType("org.openxmlformats.wordprocessingml.document") { types.append(docx) }
        if let md = UTType(filenameExtension: "md") { types.append(md) }
        return types
    }
}
