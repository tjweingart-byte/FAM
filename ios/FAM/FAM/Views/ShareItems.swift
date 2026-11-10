import SwiftUI
import UIKit

/// Words for iOS's share sheet with a subject, as the web hands
/// `navigator.share` a `title`: Mail takes it as the email's subject
/// (`shareTo`'s `t.subject`, the invite's "FAM"). Everything else gets the
/// words alone.
final class ShareTextItem: NSObject, UIActivityItemSource {
    let text: String
    let subject: String

    init(text: String, subject: String) {
        self.text = text
        self.subject = subject
    }

    func activityViewControllerPlaceholderItem(_ activityViewController: UIActivityViewController) -> Any {
        text
    }

    func activityViewController(_ activityViewController: UIActivityViewController,
                                itemForActivityType activityType: UIActivity.ActivityType?) -> Any? {
        text
    }

    func activityViewController(_ activityViewController: UIActivityViewController,
                                subjectForActivityType activityType: UIActivity.ActivityType?) -> String {
        subject
    }
}

extension AppModel {
    /// `inviteFriends`: `navigator.share({title: "FAM", text, url})` - the
    /// words "Come listen with me on FAM — I'm @handle.", the title "FAM"
    /// (an email's subject) and the link as a link of its own: the referral
    /// link while the waitlist runs, else the server's home (`AUTH.home`),
    /// else the address this build talks to.
    func inviteItems(handle: String) -> [Any] {
        let who = handle.isEmpty ? "" : " \u{2014} I'm @" + handle
        let text = "Come listen with me on FAM" + who + "."
        let address = inviteLink ?? ((home.isEmpty ? api.base.absoluteString : home) + "/")
        var items: [Any] = [ShareTextItem(text: text, subject: "FAM")]
        if let url = URL(string: address) {
            items.append(url)
        } else {
            items = [ShareTextItem(text: text + " " + address, subject: "FAM")]
        }
        return items
    }
}
