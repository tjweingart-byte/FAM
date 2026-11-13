import LinkPresentation
import UIKit

/// A share link handed to iOS's share sheet as a rich link (§601): the URL
/// with its title and picture already filled in (`LPLinkMetadata`), so
/// Messages and Mail send the tile - the episode's picture with its title
/// under it - the way they do when the link is pasted. Opening Messages
/// with `sms:&body=<url>` puts the link in as typed text, which Messages
/// sends as a bare blue link; this is Apple's way to hand it a tile.
///
/// The picture is the server's link preview (`/api/share/{id}/preview.jpg`,
/// `link_preview`), the same one the page names for every other app. With
/// none (it could not be fetched), the metadata carries the title alone
/// and Messages fetches the page's own picture as it would for a paste.
final class RichLinkItem: NSObject, UIActivityItemSource {
    let url: URL
    let title: String
    let image: UIImage?

    init(url: URL, title: String, image: UIImage?) {
        self.url = url
        self.title = title
        self.image = image
    }

    func activityViewControllerPlaceholderItem(_ activityViewController: UIActivityViewController) -> Any {
        url
    }

    func activityViewController(_ activityViewController: UIActivityViewController,
                                itemForActivityType activityType: UIActivity.ActivityType?) -> Any? {
        url
    }

    func activityViewController(_ activityViewController: UIActivityViewController,
                                subjectForActivityType activityType: UIActivity.ActivityType?) -> String {
        title
    }

    func activityViewControllerLinkMetadata(_ activityViewController: UIActivityViewController) -> LPLinkMetadata? {
        let metadata = LPLinkMetadata()
        metadata.originalURL = url
        metadata.url = url
        if !title.isEmpty { metadata.title = title }
        if let image {
            metadata.imageProvider = NSItemProvider(object: image)
        } else {
            // Without our picture, let Messages read the page as it would
            // for a pasted link, rather than send a tile with no picture.
            return nil
        }
        return metadata
    }
}

extension FAMAPI {
    /// A shared link's preview picture (the server's `preview` on
    /// `/api/share`), or nil: a share never waits on it for long and never
    /// fails because of it.
    func linkPreviewImage(_ address: String) async -> UIImage? {
        guard !address.isEmpty,
              let url = URL(string: address, relativeTo: base)?.absoluteURL else { return nil }
        var req = URLRequest(url: url, timeoutInterval: 6)
        req.httpMethod = "GET"
        // As every request this app makes (`listener-header`): no cookie.
        req.httpShouldHandleCookies = false
        req.setValue(Self.client, forHTTPHeaderField: "X-FAM-Client")
        guard let answer = try? await URLSession.shared.data(for: req),
              (answer.1 as? HTTPURLResponse)?.statusCode == 200 else { return nil }
        return UIImage(data: answer.0)
    }
}
