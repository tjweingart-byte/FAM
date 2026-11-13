import UIKit

/// Handing a story card straight to Instagram or Snapchat, as each
/// platform documents it - no SDK, no token, nothing posted: the picture
/// goes on the pasteboard under the platform's own keys, its app opens on
/// the story editor with the card in it, and the person presses share
/// there. The server says which platforms are on (`sharing.story_handoffs`,
/// from INSTAGRAM_APP_ID / SNAPCHAT_CLIENT_ID); without a hand-off, or when
/// the app is not installed, the caller keeps iOS's share sheet.
extension StoryHandoff {
    /// Pasteboard content is cleared by the platform once read; five
    /// minutes is the most it is left lying about if the app never opens
    /// (both platforms' samples use the same).
    private static let expiry: TimeInterval = 5 * 60

    /// Whether this phone has the app the hand-off opens. Asked before the
    /// card goes on the pasteboard, so a phone without Instagram never has
    /// its clipboard replaced for nothing (`LSApplicationQueriesSchemes`).
    @MainActor
    var available: Bool {
        // A local name of its own, not `url` again: `let url = URL(string: url)`
        // reads the binding inside its own initialiser in some Swift modes.
        guard let target = URL(string: self.url) else { return false }
        return UIApplication.shared.canOpenURL(target)
    }

    /// Puts the card on the pasteboard and opens the platform's app. True
    /// when iOS opened it; false sends the caller to the share sheet.
    /// `png` is the whole 1080x1920 story (the player card centred, or the
    /// old words-only card from an older server); `sticker` the player card
    /// alone, which Instagram places as a sticker the person can move
    /// (11.12 notes); `caption` the destination's words.
    @MainActor
    func send(png: Data, sticker: Data? = nil, caption: String) async -> Bool {
        guard available else { return false }
        switch platform {
        case "instagram":
            return await instagram(png: png, sticker: sticker)
        case "snapchat":
            return await snapchat(png: png, caption: caption)
        default:
            return false
        }
    }

    /// FAM's dark ground, top and bottom, behind Instagram's sticker.
    private static let groundTop = "#3A3140"
    private static let groundBottom = "#18151F"

    /// Meta's "Sharing to Stories", then
    /// `instagram-stories://share?source_application=<Facebook App ID>`:
    /// the player card under `com.instagram.sharedSticker.stickerImage`
    /// on FAM's ground (`backgroundTopColor` / `backgroundBottomColor`);
    /// with no sticker, the whole picture under `backgroundImage`.
    @MainActor
    private func instagram(png: Data, sticker: Data?) async -> Bool {
        guard let link = URL(string: self.url) else { return false }
        let item: [String: Any]
        if let sticker {
            item = [
                "com.instagram.sharedSticker.stickerImage": sticker,
                "com.instagram.sharedSticker.backgroundTopColor": Self.groundTop,
                "com.instagram.sharedSticker.backgroundBottomColor": Self.groundBottom,
            ]
        } else {
            item = ["com.instagram.sharedSticker.backgroundImage": png]
        }
        UIPasteboard.general.setItems([item], options: [.expirationDate: Date().addingTimeInterval(Self.expiry)])
        return await UIApplication.shared.open(link)
    }

    /// Snap's Creative Kit Lite, share to Preview: the client id, the
    /// picture and the caption under `com.snapchat.creativekit.*`, then
    /// `snapchat://creativekit/preview/1` with the pasteboard's change count
    /// (Snapchat's check that nothing replaced the content in between), the
    /// client id and the app's name.
    @MainActor
    private func snapchat(png: Data, caption: String) async -> Bool {
        guard let clientId, !clientId.isEmpty,
              var parts = URLComponents(string: self.url) else { return false }
        // Snap's sample hands over a JPEG; the card is opaque, so nothing
        // is lost by converting it.
        let picture = UIImage(data: png)?.jpegData(compressionQuality: 0.92) ?? png
        var item: [String: Any] = [
            "com.snapchat.creativekit.clientID": clientId,
            "com.snapchat.creativekit.backgroundImage": picture,
        ]
        let words = caption.trimmingCharacters(in: .whitespacesAndNewlines)
        if !words.isEmpty { item["com.snapchat.creativekit.captionText"] = words }
        UIPasteboard.general.setItems([item], options: [.expirationDate: Date().addingTimeInterval(Self.expiry)])
        let name = (Bundle.main.infoDictionary?["CFBundleDisplayName"] as? String) ?? "FAM"
        parts.queryItems = [
            URLQueryItem(name: "checkcount", value: String(UIPasteboard.general.changeCount)),
            URLQueryItem(name: "clientId", value: clientId),
            URLQueryItem(name: "appDisplayName", value: name),
        ]
        guard let link = parts.url else { return false }
        return await UIApplication.shared.open(link)
    }
}
