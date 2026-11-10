import SwiftUI

/// An episode's picture over the thumbnail ground (`.seed-thumb`); the FAM
/// letters, faint, while there is none.
struct Picture: View {
    let url: URL?

    var body: some View {
        ZStack {
            Rectangle().fill(FAMColor.thumb)
            if let url {
                AsyncImage(url: url) { image in
                    image.resizable().scaledToFill()
                } placeholder: {
                    Color.clear
                }
            } else {
                FAMLetters(capHeight: 18, color: FAMColor.copper.opacity(0.35))
            }
        }
        .clipped()
        .accessibilityHidden(true)
    }
}

/// The dashed box a rail shows when it has nothing (`.feed-empty`).
struct EmptyNote: View {
    let text: String

    var body: some View {
        Text(text)
            .font(FAMFont.body(10.5))
            .foregroundStyle(FAMColor.textMuted)
            .lineSpacing(3)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 13)
            .padding(.vertical, 14)
            .overlay(RoundedRectangle(cornerRadius: 12)
                .stroke(FAMColor.borderStrong, style: StrokeStyle(lineWidth: 1, dash: [4, 3])))
    }
}
