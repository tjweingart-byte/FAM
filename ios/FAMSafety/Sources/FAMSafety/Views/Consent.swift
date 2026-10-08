#if canImport(SwiftUI)
import SwiftUI

/// Asking before a listener's own words go to the AI (5.1.2(i), `consent.py`).
///
/// Use it two ways, as the web does:
///
///     // Before a search, a Go Deeper question or an attachment:
///     guard await consent.ensure() else { return }
///     startSearch()
///
///     // And when the audio request is refused anyway (asked elsewhere,
///     // withdrawn on another device): `isConsentRefusal(response)`.
///     consent.forget()
///     if await consent.ensure() { startSearch() }
///
/// and attach the sheet once, near the root: `.aiConsentSheet(consent)`.
/// Asked once, before a first search - never between a tap and the first word.
@MainActor
public final class ConsentModel: ObservableObject {
    @Published public private(set) var current: AIConsent?
    /// Non-nil while the sheet is up.
    @Published public var asking: AIConsent?

    private let api: SafetyAPI
    private var waiting: CheckedContinuation<Bool, Never>?

    public init(api: SafetyAPI) { self.api = api }

    /// Read at launch, so a first search with a yes on record never waits.
    public func load() async {
        current = try? await api.consent()
    }

    /// The server refused: whatever was cached is no longer the answer.
    public func forget() { current = nil }

    /// True once there is a yes; asks first when there is not.
    public func ensure() async -> Bool {
        if current?.given == true { return true }
        if current == nil { current = try? await api.consent() }
        guard let consent = current else { return false }
        if consent.given { return true }
        return await withCheckedContinuation { continuation in
            waiting?.resume(returning: false)
            waiting = continuation
            asking = consent
        }
    }

    /// Allow (`true`) or Not now (`false`), from the sheet or Settings.
    public func answer(_ allow: Bool) async {
        let version = (asking ?? current)?.version ?? 1
        asking = nil
        let saved = try? await api.setConsent(allow: allow, version: version)
        if let saved { current = saved }
        waiting?.resume(returning: allow && saved?.given == true)
        waiting = nil
    }
}

/// The notice. Every word is the server's (`consent.AI_NOTICE`).
public struct ConsentSheet: View {
    let notice: ConsentNotice
    let answer: (Bool) -> Void

    public init(notice: ConsentNotice, answer: @escaping (Bool) -> Void) {
        self.notice = notice
        self.answer = answer
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Image(systemName: "checkmark.shield")
                .font(.title2)
                .accessibilityHidden(true)
            Text(notice.title)
                .font(.title3.weight(.semibold))
            Text(notice.body)
            Text(notice.unaffected)
                .font(.footnote)
                .foregroundStyle(.secondary)
            HStack {
                Button(notice.decline) { answer(false) }
                    .buttonStyle(.bordered)
                Spacer()
                Button(notice.allow) { answer(true) }
                    .buttonStyle(.borderedProminent)
            }
            .padding(.top, 6)
        }
        .padding(24)
        .presentationDetents([.medium])
        .interactiveDismissDisabled()
    }
}

public extension View {
    /// Presents `ConsentSheet` whenever `model.ensure()` is waiting on an answer.
    func aiConsentSheet(_ model: ConsentModel) -> some View {
        sheet(item: Binding(
            get: { model.asking.map(IdentifiedConsent.init) },
            set: { if $0 == nil, model.asking != nil { Task { await model.answer(false) } } }
        )) { item in
            ConsentSheet(notice: item.consent.notice) { allow in
                Task { await model.answer(allow) }
            }
        }
    }
}

struct IdentifiedConsent: Identifiable {
    let consent: AIConsent
    var id: Int { consent.version }
}
#endif
